"""A LabNet network on this computer for development: Central, Lab PCs, Control Center, watchdog.

Everything binds to 127.0.0.1 and uses plaintext, which LabNet permits only on
loopback. State lives in ``<root>/.devnet`` (database, keys, settings, logs);
experiments, Automation projects and papers in ``<root>/experiments``,
``automation`` and ``literature``, so re-seeding keeps them.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .processes import DevError, clean_environment, command, http_ok, port_in_use, start, stop, wait_for_http


@dataclass(frozen=True)
class LabPC:
    id: str
    grpc_port: int
    http_port: int
    inventory: str  # package.module:function or path/file.py:function


@dataclass(frozen=True)
class DevNetwork:
    """The topology; the defaults are what ``labnet dev`` starts."""

    root: Path
    central_port: int = 8008
    control_center_port: int = 8080
    watchdog_port: int = 8081
    admin_user: str = "dev-admin"
    researcher_user: str = "researcher"
    control_center_user: str = "control-center"
    watchdog_user: str = "automation-watchdog"
    lab_pcs: tuple[LabPC, ...] = field(default_factory=lambda: (
        LabPC("lab-pc-dev-01", 50051, 9001, "labnet_sim.inventories.dev_lab_pc_01:create_devices"),
        LabPC("lab-pc-dev-02", 50052, 9002, "labnet_sim.inventories.dev_lab_pc_02:create_devices"),
    ))

    @property
    def devnet(self) -> Path:
        return Path(self.root) / ".devnet"

    @property
    def logs(self) -> Path:
        return self.devnet / "logs"

    @property
    def run(self) -> Path:
        return self.devnet / "run"

    @property
    def central_url(self) -> str:
        return f"http://127.0.0.1:{self.central_port}"

    def env_file(self, user: str) -> Path:
        return self.devnet / f"{user}.env"

    def components(self) -> list[str]:
        return ["central", *(pc.id for pc in self.lab_pcs), "control-center", "watchdog"]


def say(kind: str, message: str) -> None:
    print(f"{kind:>4} {message}", flush=True)


def _require(name: str, package: str) -> Path:
    found = command(name)
    if found is None:
        raise DevError(f"{name} isn't installed here: pip install {package}")
    return found


def _admin(net: DevNetwork, *arguments: str) -> None:
    result = subprocess.run(
        [str(_require("labnet-central-admin", "'labnet[central]'")), "--env-file", str(net.env_file("central")),
         *arguments],
        env=clean_environment(), capture_output=True, text=True,
    )
    for line in (result.stdout + result.stderr).splitlines():
        if line.strip() and not line.startswith("INFO  [alembic"):
            print(f"     {line}")
    if result.returncode != 0:
        raise DevError("labnet-central-admin failed (see above).")


def _append(path: Path, lines: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(f"{key}={value}\n" for key, value in lines.items()))


def _watchdog_settings(net: DevNetwork) -> None:
    env_file = net.env_file(net.watchdog_user)
    if "LABNET_API_KEY=" not in env_file.read_text(encoding="utf-8"):
        raise DevError(f"{env_file} has no API key; run labnet dev seed again.")
    _append(env_file, {
        "LABNET_AUTOMATION_DIR": (Path(net.root) / "automation").resolve().as_posix(),
        "LABNET_WATCHDOG_CONTROL_CENTER_URL": f"http://127.0.0.1:{net.control_center_port}",
        "LABNET_WATCHDOG_PORT": str(net.watchdog_port),
    })


def seed(net: DevNetwork, *, watchdog_only: bool = False) -> None:
    """A fresh network: delete .devnet, create Central's database, users and every settings file.

    ``watchdog_only`` adds just the watchdog's user and settings to a network seeded
    before it existed; it deletes nothing.
    """

    root = Path(net.root).resolve()
    if watchdog_only:
        if not net.env_file("central").is_file():
            raise DevError("No seeded network here; run labnet dev seed first.")
        if net.env_file(net.watchdog_user).is_file():
            say("ok", f"{net.env_file(net.watchdog_user)} already exists")
            return
        (root / "automation").mkdir(exist_ok=True)
        _admin(net, "setup", "--user", net.watchdog_user, "--write-env", str(net.devnet),
               "--server-url", net.central_url)
        _watchdog_settings(net)
        return

    busy = [name for name, port in (("central", net.central_port), *((pc.id, pc.http_port) for pc in net.lab_pcs))
            if port_in_use(port)]
    if busy:
        raise DevError(f"{', '.join(busy)} still running; run labnet dev down first.")
    say("==>", f"Resetting {net.devnet}")
    for _ in range(10):  # a just-stopped process may hold its log open for a moment (Windows)
        shutil.rmtree(net.devnet, ignore_errors=True)
        if not net.devnet.exists():
            break
        time.sleep(1)
    if net.devnet.exists():
        raise DevError(f"Could not delete {net.devnet}; is a process still using it?")
    net.logs.mkdir(parents=True)
    net.env_file("central").write_text(
        "# Written by labnet dev seed; used as labnet-central --env-file.\n"
        f"LABNET_DATABASE_URL=sqlite:///{(net.devnet / 'central.db').as_posix()}\n"
        "LABNET_CENTRAL_BIND_HOST=127.0.0.1\n"
        f"LABNET_CENTRAL_BIND_PORT={net.central_port}\n",
        encoding="utf-8", newline="\n",
    )
    say("==>", "Creating Central's database, users and their settings files")
    _admin(net, "setup", "--admin", net.admin_user, "--user", net.researcher_user,
           "--user", net.control_center_user, "--user", net.watchdog_user,
           *(argument for pc in net.lab_pcs for argument in ("--lab-pc", pc.id)),
           "--write-env", str(net.devnet), "--server-url", net.central_url)
    for pc in net.lab_pcs:
        _append(net.env_file(pc.id), {
            "LABNET_GRPC_PORT": str(pc.grpc_port),
            "LABNET_HTTP_PORT": str(pc.http_port),
            "LABNET_DEVICE_INVENTORY": pc.inventory,
        })
        say("ok", f"{pc.id} -> gRPC :{pc.grpc_port}, health :{pc.http_port}, {pc.inventory}")
    for folder in ("experiments", "automation", "literature"):
        (root / folder).mkdir(exist_ok=True)
    control_center = {
        "LABNET_CONTROL_CENTER_PORT": str(net.control_center_port),
        "LABNET_AUTOMATION_ENABLED": "true",
        "LABNET_PHOTO_AGENT_ENABLED": "true",
        "LABNET_CONTROL_CENTER_EXPERIMENTS_DIR": (root / "experiments").as_posix(),
        "LABNET_AUTOMATION_DIR": (root / "automation").as_posix(),
        "LABNET_AGENT_LITERATURE_DIR": (root / "literature").as_posix(),
    }
    sources = [root / name for name in ("packages", "examples") if (root / name).is_dir()]
    if sources:  # a LabNet checkout: Photo-Agent reads its code and examples
        control_center["LABNET_AGENT_SOURCES"] = os.pathsep.join(path.as_posix() for path in sources)
    _append(net.env_file(net.control_center_user), control_center)
    say("ok", f"{net.control_center_user} -> http://127.0.0.1:{net.control_center_port}/status")
    _watchdog_settings(net)
    say("ok", f"Seeded. Keys and settings are in {net.devnet}.")


def _start_and_wait(net: DevNetwork, name: str, port: int, argv: Sequence[object], timeout: float) -> None:
    log_file = net.logs / f"{name}.log"
    if port_in_use(port):
        say("ok", f"{name} already running")
    else:
        say("==>", f"Starting {name}")
        start(name, [str(part) for part in argv], log_file=log_file, run_dir=net.run, cwd=Path(net.root))
    wait_for_http(f"http://127.0.0.1:{port}/health/ready", timeout, log_file)
    say("ok", f"{name} ready")


def up(net: DevNetwork) -> None:
    """Start everything in dependency order (seeding first if needed) and wait until each is ready."""

    central = _require("labnet-central", "'labnet[central]'")
    lab_pc = _require("labnet-lab-pc", "'labnet[lab-pc]'")
    for pc in net.lab_pcs:
        module = pc.inventory.split(":")[0]
        if not module.endswith(".py") and "/" not in module and importlib.util.find_spec(module.split(".")[0]) is None:
            raise DevError(f"{pc.id}'s inventory {pc.inventory} needs {module.split('.')[0]}: "
                           "pip install labnet-sim (or the package with your drivers)")
    if not net.env_file("central").is_file():
        seed(net)
    _start_and_wait(net, "central", net.central_port, [central, "--env-file", net.env_file("central")], 30)
    for pc in net.lab_pcs:
        if not port_in_use(pc.http_port) and port_in_use(pc.grpc_port):
            raise DevError(f"gRPC port {pc.grpc_port} (for {pc.id}) is already in use.")
        # Ready means: devices connected, registered with Central, gRPC serving.
        _start_and_wait(net, pc.id, pc.http_port, [lab_pc, "--env-file", net.env_file(pc.id)], 45)
    control_center = command("labnet-control-center")
    if control_center is None:
        say("--", "No Control Center here (pip install labnet-control-center to add it).")
    else:
        _start_and_wait(net, "control-center", net.control_center_port,
                        [control_center, "--env-file", net.env_file(net.control_center_user)], 30)
        watchdog = command("labnet-watchdog")
        if not net.env_file(net.watchdog_user).is_file():
            say("warn", "No watchdog settings (seeded before it existed): labnet dev seed --watchdog")
        elif watchdog is not None:
            # Applies an Automation grant's safe state if the Control Center dies mid-grant.
            _start_and_wait(net, "watchdog", net.watchdog_port,
                            [watchdog, "--env-file", net.env_file(net.watchdog_user)], 30)
    where = f"http://127.0.0.1:{net.control_center_port}/status" if control_center else net.central_url
    say("ok", f"Network is up: {where}")


def down(net: DevNetwork, names: Sequence[str] | None = None) -> None:
    """Stop the named components (default: all), the watchdog first and Central last.

    The watchdog goes first: it would otherwise take over a stopped Control Center's grants.
    """

    order = [("watchdog", [net.watchdog_port]), ("control-center", [net.control_center_port]),
             *((pc.id, [pc.http_port, pc.grpc_port]) for pc in net.lab_pcs), ("central", [net.central_port])]
    wanted = set(names) if names else None
    if wanted:
        unknown = wanted - {name for name, _ in order}
        if unknown:
            raise DevError(f"Unknown component {', '.join(sorted(unknown))}; choose from {', '.join(net.components())}.")
    for name, ports in order:
        if wanted is None or name in wanted:
            if stop(name, ports, net.run):
                say("ok", f"stopped {name}")
    if wanted is None:
        say("ok", "Network is down.")


def restart(net: DevNetwork, names: Sequence[str]) -> None:
    """Stop the named components and start what is stopped, e.g. after editing a Lab PC's inventory."""

    down(net, names)
    up(net)


@contextmanager
def _as_researcher(net: DevNetwork) -> Iterator[Path]:
    env_file = net.env_file(net.researcher_user)
    if not env_file.is_file():
        raise DevError(f"No {env_file}; run labnet dev seed.")
    # Only LABNET_* change: clearing all of os.environ breaks gRPC's own settings on Windows.
    saved = {key: value for key, value in os.environ.items() if key.startswith("LABNET_")}
    try:
        for key in saved:
            del os.environ[key]
        os.environ["LABNET_ENV_FILE"] = str(env_file)
        yield env_file
    finally:
        for key in [key for key in os.environ if key.startswith("LABNET_")]:
            del os.environ[key]
        os.environ.update(saved)


def status(net: DevNetwork) -> int:
    """Each component's health, then the devices Central hands to clients."""

    from . import check

    ports = [("central", net.central_port), *((pc.id, pc.http_port) for pc in net.lab_pcs),
             ("control-center", net.control_center_port), ("watchdog", net.watchdog_port)]
    for name, port in ports:
        if http_ok(f"http://127.0.0.1:{port}/health/ready"):
            say("ok", f"{name} ready (port {port})")
        elif port_in_use(port):
            say("warn", f"{name} running but NOT ready; see {net.logs / (name + '.log')}")
        else:
            say("--", f"{name} stopped")
    if not http_ok(f"{net.central_url}/health/ready"):
        return 1
    print()
    with _as_researcher(net):
        return check.status()


def smoke(net: DevNetwork) -> int:
    """As a researcher: lease every available device, call one read RPC, release it."""

    import asyncio

    from . import check

    with _as_researcher(net):
        return asyncio.run(check.smoke())


__all__ = ["DevNetwork", "LabPC", "down", "restart", "seed", "smoke", "status", "up"]
