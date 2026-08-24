from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import grpc_tools
from grpc_tools import protoc


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROTO_ROOT = REPOSITORY_ROOT / "Labnet" / "protos" / "labnet" / "devices" / "v1"
OUTPUT_ROOT = REPOSITORY_ROOT / "Labnet" / "labnet" / "grpc" / "generated"
PROTO_FILES = ("common.proto", "transfer.proto", "dummy_device.proto")
LOCAL_IMPORT = re.compile(
    r"^import (common_pb2|transfer_pb2|dummy_device_pb2) as ",
    flags=re.MULTILINE,
)


def main() -> None:
    well_known_types = Path(grpc_tools.__file__).resolve().parent / "_proto"
    arguments = [
        "grpc_tools.protoc",
        f"--proto_path={PROTO_ROOT}",
        f"--proto_path={well_known_types}",
        f"--python_out={OUTPUT_ROOT}",
        f"--pyi_out={OUTPUT_ROOT}",
        f"--grpc_python_out={OUTPUT_ROOT}",
        *(str(PROTO_ROOT / name) for name in PROTO_FILES),
    ]
    result = protoc.main(arguments)
    if result != 0:
        raise SystemExit(f"grpc_tools.protoc failed with exit code {result}.")

    generated_paths = {
        *OUTPUT_ROOT.glob("*_pb2.py"),
        *OUTPUT_ROOT.glob("*_pb2.pyi"),
        *OUTPUT_ROOT.glob("*_pb2_grpc.py"),
    }
    for path in sorted(generated_paths):
        generated = path.read_text(encoding="utf-8")
        package_relative = LOCAL_IMPORT.sub(r"from . import \1 as ", generated)
        path.write_text(package_relative, encoding="utf-8", newline="\n")

    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from labnet.grpc.generated import "
                "common_pb2, transfer_pb2, dummy_device_pb2, "
                "dummy_device_pb2_grpc"
            ),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    print(f"Generated and import-verified {len(PROTO_FILES)} protobuf schemas.")


if __name__ == "__main__":
    main()
