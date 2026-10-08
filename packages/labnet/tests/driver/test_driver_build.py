"""labnet.driver.build: path-qualified protos, import rewriting, check mode, and the CLI."""

from __future__ import annotations

import importlib
from pathlib import Path
from textwrap import dedent

import pytest

from labnet import cli
from labnet.contracts import DeviceContract
from labnet.driver import BuildTarget, ContractValidationError, build, core_target

THING = dedent(
    """\
    syntax = "proto3";
    package acme_dev.v1;

    import "labnet/devices/v1/common.proto";
    import "labnet/devices/v1/rpc_options.proto";

    message ThingRequest {
      string device_id = 1;
      string operation_id = 2;
    }
    message ThingResponse {
      string operation_id = 1;
      labnet.devices.v1.ScalarMeasurement measurement = 2;
    }
    service AcmeThingService {
      rpc ReadLevel(ThingRequest) returns (ThingResponse) {
        option (labnet.devices.v1.rpc) = {
          pattern: RPC_PATTERN_SCALAR_READ
          physical_method: "read_level"
          default_unit: "V"
        };
      }
    }
    """
)


def _target(tmp_path: Path) -> BuildTarget:
    proto_root = tmp_path / "protos"
    (proto_root / "acme_dev" / "v1").mkdir(parents=True)
    (proto_root / "acme_dev" / "v1" / "thing.proto").write_text(THING, encoding="utf-8")
    output_root = tmp_path / "src" / "acme_dev" / "_generated"
    output_root.mkdir(parents=True)
    (output_root.parent / "__init__.py").write_text("", encoding="utf-8")
    (output_root / "__init__.py").write_text("", encoding="utf-8")
    return BuildTarget(proto_root=proto_root, output_root=output_root, python_package="acme_dev._generated")


def test_labnets_own_generated_files_are_current() -> None:
    contracts = build(core_target(), check=True)
    assert contracts  # the simulated families still live in core


def test_a_driver_package_builds_against_labnets_protos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _target(tmp_path)
    (contract,) = {spec.service_full_name for spec in build(target)}
    assert contract == "acme_dev.v1.AcmeThingService"

    pb2 = (target.output_root / "thing_pb2.py").read_text(encoding="utf-8")
    assert "from labnet.grpc.generated import common_pb2" in pb2  # labnet's messages, not a copy
    assert "from labnet.grpc.generated import rpc_options_pb2" in pb2
    assert "from . import thing_pb2" in (target.output_root / "thing_pb2_grpc.py").read_text(encoding="utf-8")
    assert not (target.output_root / "common_pb2.py").exists()

    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    importlib.invalidate_caches()
    contracts = importlib.import_module("acme_dev._generated.contracts")
    (thing,) = contracts.DEVICE_CONTRACTS
    assert isinstance(thing, DeviceContract) and thing.device_type == "acme_thing"
    assert [method.python_name for method in thing.methods] == ["read_level"]

    build(target, check=True)  # nothing changed
    proto = target.proto_root / "acme_dev" / "v1" / "thing.proto"
    proto.write_text(THING.replace('default_unit: "V"', 'default_unit: "mV"'), encoding="utf-8")
    with pytest.raises(ContractValidationError, match="stale: contracts.py, thing_pb2.py"):
        build(target, check=True)


def test_two_protos_with_one_module_name_are_refused(tmp_path: Path) -> None:
    target = _target(tmp_path)
    (target.proto_root / "acme_dev" / "v2").mkdir()
    (target.proto_root / "acme_dev" / "v2" / "thing.proto").write_text(
        THING.replace("acme_dev.v1", "acme_dev.v2"), encoding="utf-8"
    )
    with pytest.raises(ContractValidationError, match="would both generate thing_pb2"):
        build(target)


def test_the_labnet_command_has_the_driver_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert "driver" in cli.parser().format_help()
    assert cli.main(["driver", "build"]) == 2  # driver packages arrive with labnet-sim
    assert "only --core builds" in capsys.readouterr().out
