"""Build device protos into Python: protobuf and gRPC modules, then device contracts.

Every proto names the others by path from an include root, e.g.
``import "labnet/devices/v1/common.proto";``. The include roots are the
target's own proto folder, labnet's protos (shipped inside the labnet package)
and protobuf's well-known types. protoc names a module after its proto's path;
a build writes the target's modules flat into one Python package and rewrites
the imports between them: the target's own as relative imports, labnet's to
``labnet.grpc.generated``. Generated output is never edited by hand, and a
``check`` build fails if the files on disk differ from a fresh build.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType

from google.protobuf import descriptor_pb2

import labnet

from .render import render_contracts_module, render_facades_module, render_facades_stub
from .validate import CORE_PROTO_DIR, RPC_OPTIONS_FILE, ContractValidationError, RpcContractSpec, validate_descriptor_set

CORE_PYTHON_PACKAGE = "labnet.grpc.generated"
GRPC_TOOLS_HINT = "Building protos needs grpcio-tools: pip install 'labnet[driver-dev]'."

# protoc's imports between generated modules: `from a.b import x_pb2 as …`, or
# `import x_pb2 as …` for a proto at the top of an include root.
_DOTTED_IMPORT = re.compile(r"^from ([A-Za-z_][\w.]*) import (\w+_pb2)\b", re.MULTILINE)
_BARE_IMPORT = re.compile(r"^import (\w+_pb2) as ", re.MULTILINE)


def core_proto_root() -> Path:
    """labnet's own protos (``labnet/devices/v1/*.proto``), as installed."""

    return Path(labnet.__file__).resolve().parent / "protos"


@dataclass(frozen=True)
class BuildTarget:
    """What one build compiles, and where its Python output goes.

    ``proto_files`` are paths relative to ``proto_root`` (every ``.proto``
    under it when empty). ``output_root`` is the folder of the Python package
    ``python_package``. ``contracts_module`` names the contracts module to
    render, or is None for protobuf code only (protos without services).

    A driver package also names itself: ``distribution`` (its pip name) and
    ``package`` (its top-level Python package). Its protos must then use that
    package (``<package>.v1``), and the build renders ``facades_module``.
    """

    proto_root: Path
    output_root: Path
    python_package: str
    proto_files: tuple[str, ...] = ()
    contracts_module: str | None = "contracts"
    distribution: str = ""
    package: str = ""
    facades_module: str | None = None


def core_target() -> BuildTarget:
    """labnet's own protos (shared messages, no services), built into ``labnet.grpc.generated``."""

    package = Path(labnet.__file__).resolve().parent
    return BuildTarget(
        proto_root=package / "protos",
        output_root=package / "grpc" / "generated",
        python_package=CORE_PYTHON_PACKAGE,
        contracts_module=None,
    )


def target_from_project(project: Path) -> BuildTarget:
    """A driver package's build, from ``[tool.labnet.driver]`` in its ``pyproject.toml``.

    Keys (all optional): ``package`` (default: the project name with ``_``),
    ``protos`` (default ``protos``) and ``output`` (default ``src/<package>/_generated``).
    """

    project = Path(project).resolve()
    pyproject = project / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContractValidationError(f"No pyproject.toml in {project}.") from None
    distribution = data.get("project", {}).get("name", "")
    config = data.get("tool", {}).get("labnet", {}).get("driver")
    if config is None or not distribution:
        raise ContractValidationError(
            f"{pyproject} has no [project] name or no [tool.labnet.driver] table; is it a LabNet driver package?"
        )
    package = config.get("package", distribution.replace("-", "_"))
    output = project / config.get("output", f"src/{package}/_generated")
    relative = output.relative_to(project).parts
    module_parts = relative[relative.index("src") + 1:] if "src" in relative else relative
    return BuildTarget(
        proto_root=project / config.get("protos", "protos"),
        output_root=output,
        python_package=".".join(module_parts),
        contracts_module="contracts",
        distribution=distribution,
        package=package,
        facades_module="facades",
    )


def proto_files_of(target: BuildTarget) -> tuple[str, ...]:
    root = Path(target.proto_root)
    names = target.proto_files or tuple(path.relative_to(root).as_posix() for path in root.rglob("*.proto"))
    if not names:
        raise ContractValidationError(f"No .proto files found in {root}.")
    missing = sorted(name for name in names if not (root / name).is_file())
    if missing:
        raise ContractValidationError("Proto file(s) not found: " + ", ".join(missing))
    stems: dict[str, str] = {}
    for name in sorted(dict.fromkeys(names)):
        stem = PurePosixPath(name).stem
        if stems.setdefault(stem, name) != name:
            raise ContractValidationError(
                f"{stems[stem]} and {name} would both generate {stem}_pb2; rename one."
            )
    return tuple(sorted(dict.fromkeys(names)))


def generated_files(output_root: Path, contracts_module: str | None, facades_module: str | None = None) -> set[Path]:
    output_root = Path(output_root)
    files = {
        *output_root.glob("*_pb2.py"),
        *output_root.glob("*_pb2.pyi"),
        *output_root.glob("*_pb2_grpc.py"),
    }
    if contracts_module:
        files.add(output_root / f"{contracts_module}.py")
    if facades_module:
        files |= {output_root / f"{facades_module}.py", output_root / f"{facades_module}.pyi"}
    return files


def _target_files(root: Path, target: BuildTarget) -> set[Path]:
    return generated_files(root, target.contracts_module, target.facades_module)


def _descriptors(descriptor_path: Path, names: tuple[str, ...]) -> list[descriptor_pb2.FileDescriptorProto]:
    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.ParseFromString(descriptor_path.read_bytes())
    return [file for file in descriptor_set.file if file.name in names]


def _check_packages(target: BuildTarget, files: list[descriptor_pb2.FileDescriptorProto]) -> None:
    """A driver's protos live in its own package: ``labnet.`` is labnet's alone."""

    if not target.package:
        return
    for file in files:
        if file.package != target.package and not file.package.startswith(f"{target.package}."):
            raise ContractValidationError(
                f"{file.name}: package {file.package or '(none)'} must be {target.package}.v1 "
                f"(or another {target.package}.* package), so its names can't collide with another driver's."
            )


def fingerprints(files: list[descriptor_pb2.FileDescriptorProto]) -> dict[str, str]:
    """Each service's fingerprint: sha256 of its proto file's descriptor (deterministic bytes).

    Comments and formatting don't change it; any change to a message, field or RPC does.
    """

    result = {}
    for file in files:
        digest = hashlib.sha256(file.SerializeToString(deterministic=True)).hexdigest()
        for service in file.service:
            result[f"{file.package}.{service.name}" if file.package else service.name] = digest
    return result


def _protoc():
    try:
        import grpc_tools
        from grpc_tools import protoc
    except ImportError as exc:
        raise ContractValidationError(GRPC_TOOLS_HINT) from exc
    return Path(grpc_tools.__file__).resolve().parent / "_proto", protoc


def _run_protoc(target: BuildTarget, names: tuple[str, ...], raw_root: Path, descriptor_path: Path) -> None:
    well_known_types, protoc = _protoc()
    include_roots = [Path(target.proto_root).resolve()]
    if core_proto_root() not in include_roots:
        include_roots.append(core_proto_root())
    raw_root.mkdir(parents=True, exist_ok=True)
    arguments = [
        "grpc_tools.protoc",
        *(f"--proto_path={root}" for root in include_roots),
        f"--proto_path={well_known_types}",
        f"--python_out={raw_root}",
        f"--pyi_out={raw_root}",
        f"--grpc_python_out={raw_root}",
        f"--descriptor_set_out={descriptor_path}",
        "--include_imports",
        *(str(Path(target.proto_root) / name) for name in names),
    ]
    result = protoc.main(arguments)
    if result != 0:
        raise ContractValidationError(f"grpc_tools.protoc failed with exit code {result}.")


def _flatten(names: tuple[str, ...], raw_root: Path, staging_root: Path) -> None:
    """Move each target proto's modules from protoc's folder tree into one flat package."""

    staging_root.mkdir(parents=True, exist_ok=True)
    own_packages = {str(PurePosixPath(name).parent).replace("/", ".") for name in names}

    def rewrite(source: str) -> str:
        def dotted(match: re.Match[str]) -> str:
            package, module = match.group(1), match.group(2)
            if package in own_packages:
                return f"from . import {module}"
            if package == CORE_PROTO_DIR.replace("/", "."):
                return f"from {CORE_PYTHON_PACKAGE} import {module}"
            return match.group(0)  # google.protobuf and the like

        source = _DOTTED_IMPORT.sub(dotted, source)
        if "." in own_packages:
            source = _BARE_IMPORT.sub(r"from . import \1 as ", source)
        return source

    for name in names:
        proto = PurePosixPath(name)
        for suffix in ("_pb2.py", "_pb2.pyi", "_pb2_grpc.py"):
            generated = raw_root / proto.parent / f"{proto.stem}{suffix}"
            if generated.is_file():
                text = rewrite(generated.read_text(encoding="utf-8"))
                (staging_root / generated.name).write_text(text, encoding="utf-8", newline="\n")


def _rpc_options_module(names: tuple[str, ...], staging_root: Path) -> ModuleType:
    if RPC_OPTIONS_FILE not in names:
        return importlib.import_module(f"{CORE_PYTHON_PACKAGE}.rpc_options_pb2")
    # Building labnet itself: use the freshly generated options, not the installed ones.
    module_path = staging_root / "rpc_options_pb2.py"
    spec = importlib.util.spec_from_file_location("_labnet_driver_build_rpc_options_pb2", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load generated options module at {module_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy(staging_root: Path, target: BuildTarget) -> None:
    output_root = Path(target.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    staged = {path.name: path for path in _target_files(staging_root, target) if path.is_file()}
    for name, source in sorted(staged.items()):
        destination = output_root / name
        temporary = destination.with_name(f".{destination.name}.tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    for existing in sorted(_target_files(output_root, target)):
        if existing.is_file() and existing.name not in staged:
            existing.unlink()


def _assert_current(staging_root: Path, target: BuildTarget, command: str) -> None:
    files = lambda root: {  # noqa: E731
        path.name: path.read_bytes() for path in _target_files(root, target) if path.is_file()
    }
    staged, current = files(staging_root), files(Path(target.output_root))
    stale = sorted(name for name in set(staged) | set(current) if staged.get(name) != current.get(name))
    if stale:
        raise ContractValidationError(f"Generated files are stale: {', '.join(stale)}. Run `{command}`.")


def build(
    target: BuildTarget,
    *,
    check: bool = False,
    command: str = "labnet driver build",
) -> tuple[RpcContractSpec, ...]:
    """Compile and validate ``target``; write its output, or with ``check`` only compare it.

    Returns the contracts of the target's services (empty without a contracts module).
    """

    names = proto_files_of(target)
    with tempfile.TemporaryDirectory(prefix="labnet-driver-build-") as temporary:
        temporary_root = Path(temporary)
        staging_root = temporary_root / "generated"
        descriptor_path = temporary_root / "descriptors.pb"
        _run_protoc(target, names, temporary_root / "raw", descriptor_path)
        _flatten(names, temporary_root / "raw", staging_root)
        own = _descriptors(descriptor_path, names)
        _check_packages(target, own)
        contracts: tuple[RpcContractSpec, ...] = ()
        if target.contracts_module:
            contracts = validate_descriptor_set(
                descriptor_path,
                _rpc_options_module(names, staging_root),
                local_proto_files=names,
            )
            source = render_contracts_module(
                contracts,
                proto_files=names,
                distribution=target.distribution,
                package=target.package,
                fingerprints=fingerprints(own),
            )
            try:
                compile(source, f"{target.contracts_module}.py", "exec")
            except SyntaxError as exc:
                raise ContractValidationError(
                    f"Generated contract source is not valid Python: {exc.msg} (line {exc.lineno})."
                ) from exc
            (staging_root / f"{target.contracts_module}.py").write_text(source, encoding="utf-8", newline="\n")
            if target.facades_module:
                facades = render_facades_module(
                    contracts, package=target.package, contracts_module=target.contracts_module
                )
                for suffix, text in ((".py", facades), (".pyi", render_facades_stub(contracts))):
                    (staging_root / f"{target.facades_module}{suffix}").write_text(text, encoding="utf-8", newline="\n")
        if check:
            _assert_current(staging_root, target, command)
        else:
            _copy(staging_root, target)
    return contracts


def import_check(target: BuildTarget) -> None:
    """Import every generated module in a fresh interpreter, from the files on disk."""

    package_root = Path(target.output_root).resolve()
    for _ in target.python_package.split("."):
        package_root = package_root.parent
    modules = sorted(
        path.stem for path in _target_files(Path(target.output_root), target) if path.suffix == ".py"
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib, sys\n"
            f"for name in {modules!r}:\n"
            f"    importlib.import_module({target.python_package!r} + '.' + name)",
        ],
        cwd=package_root,
        check=True,
    )


__all__ = [
    "BuildTarget",
    "CORE_PYTHON_PACKAGE",
    "build",
    "core_proto_root",
    "core_target",
    "fingerprints",
    "generated_files",
    "target_from_project",
    "import_check",
    "proto_files_of",
]
