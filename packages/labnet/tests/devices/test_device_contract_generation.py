from __future__ import annotations

import importlib
import inspect
from pathlib import Path
import re
from textwrap import dedent

import pytest

from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.devices.SimulatedTunableLaser.physical import PhysicalSimulatedTunableLaser
from labnet.grpc.generated import (
    dummy_device_pb2,
    rpc_options_pb2,
)
from labnet.contracts import PhysicalContractError, registry, validate_implementation
from labnet.driver import BuildTarget, ContractValidationError, build

CORE_PROTOS = {"common.proto", "rpc_options.proto"}


def generate_protos(*, proto_root: Path, output_root: Path, proto_files, check: bool = False):
    """Build test protos the way a driver package is built: against labnet's installed protos."""

    return build(
        BuildTarget(
            proto_root=proto_root,
            output_root=output_root,
            python_package=output_root.name,
            proto_files=tuple(name for name in proto_files if name not in CORE_PROTOS),
            contracts_module="device_contracts",
        ),
        check=check,
    )


def _write_test_contract(
    tmp_path: Path,
    option_block: str,
) -> tuple[Path, Path]:
    proto_root = tmp_path / "protos"
    output_root = tmp_path / "generated"
    proto_root.mkdir()
    output_root.mkdir()
    (proto_root / "test_device.proto").write_text(
        dedent(
            f'''\
            syntax = "proto3";

            package labnet.tests.v1;

            import "labnet/devices/v1/common.proto";
            import "labnet/devices/v1/rpc_options.proto";

            message ResetRequest {{
              string device_id = 1;
              string operation_id = 2;
            }}

            message ResetResponse {{
              string operation_id = 1;
              labnet.devices.v1.ScalarMeasurement measurement = 2;
            }}

            service TestDeviceService {{
              rpc Reset(ResetRequest) returns (ResetResponse) {{
                {option_block}
              }}
            }}
            '''
        ),
        encoding="utf-8",
    )
    return proto_root, output_root


def _generate_test_contract(tmp_path: Path, option_block: str) -> None:
    proto_root, output_root = _write_test_contract(tmp_path, option_block)
    generate_protos(
        proto_root=proto_root,
        output_root=output_root,
        proto_files=("common.proto", "rpc_options.proto", "test_device.proto"),
    )


def _dummy_contract():
    return registry.by_service("labnet.devices.v1.DummyDeviceService")


def _generate_importable_contract_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    package_name: str,
    proto_source: str,
):
    proto_root = tmp_path / f"{package_name}_protos"
    output_root = tmp_path / package_name
    proto_root.mkdir()
    output_root.mkdir()
    proto_name = f"{package_name}.proto"
    (proto_root / proto_name).write_text(dedent(proto_source), encoding="utf-8")
    build_contracts = generate_protos(
        proto_root=proto_root,
        output_root=output_root,
        proto_files=("common.proto", "rpc_options.proto", proto_name),
    )
    (output_root / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    runtime_contracts = importlib.import_module(f"{package_name}.device_contracts")
    return build_contracts, runtime_contracts


def _generate_shape_contract(
    tmp_path: Path,
    *,
    pattern: str,
    request_fields: str,
    response_fields: str,
    server_streaming: bool = False,
    rpc_name: str = "Execute",
    physical_method: str = "execute",
    default_unit: str = "arb",
):
    proto_root = tmp_path / "shape-protos"
    output_root = tmp_path / "shape-generated"
    proto_root.mkdir()
    output_root.mkdir()
    duration_import = (
        'import "google/protobuf/duration.proto";'
        if "google.protobuf.Duration" in request_fields
        else ""
    )
    stream_keyword = "stream " if server_streaming else ""
    (proto_root / "shape_device.proto").write_text(
        dedent(
            f'''\
            syntax = "proto3";

            package labnet.tests.shape.v1;

            import "labnet/devices/v1/common.proto";
            import "labnet/devices/v1/rpc_options.proto";
            {duration_import}

            message ShapeRequest {{
              {request_fields}
            }}

            message ShapeResponse {{
              {response_fields}
            }}

            service ShapeDeviceService {{
              rpc {rpc_name}(ShapeRequest) returns ({stream_keyword}ShapeResponse) {{
                option (labnet.devices.v1.rpc) = {{
                  pattern: {pattern}
                  physical_method: "{physical_method}"
                  default_unit: "{default_unit}"
                }};
              }}
            }}
            '''
        ),
        encoding="utf-8",
    )
    return generate_protos(
        proto_root=proto_root,
        output_root=output_root,
        proto_files=("common.proto", "rpc_options.proto", "shape_device.proto"),
    )


def test_reference_device_explicitly_declares_every_required_rpc_option() -> None:
    service = dummy_device_pb2.DESCRIPTOR.services_by_name["DummyDeviceService"]

    assert service.methods
    for method in service.methods:
        options = method.GetOptions()
        assert options.HasExtension(rpc_options_pb2.rpc), method.full_name
        rpc_options = options.Extensions[rpc_options_pb2.rpc]
        assert rpc_options.HasField("pattern"), method.full_name
        assert rpc_options.pattern != rpc_options_pb2.RPC_PATTERN_UNSPECIFIED
        assert rpc_options.HasField("physical_method"), method.full_name
        assert rpc_options.physical_method.strip(), method.full_name
        assert rpc_options.HasField("default_unit"), method.full_name
        assert rpc_options.default_unit.strip(), method.full_name


def test_unitless_supported_operation_uses_explicit_dimensionless_unit(
    tmp_path: Path,
) -> None:
    _generate_test_contract(
        tmp_path,
        dedent(
            """\
            option (labnet.devices.v1.rpc) = {
              pattern: RPC_PATTERN_SCALAR_READ
              physical_method: "read_state"
              default_unit: "1"
            };
            """
        ),
    )


def test_physical_source_change_does_not_make_generated_contract_stale(
    tmp_path: Path,
) -> None:
    option_block = dedent(
        """\
        option (labnet.devices.v1.rpc) = {
          pattern: RPC_PATTERN_SCALAR_READ
          physical_method: "read_state"
          default_unit: "1"
        };
        """
    )
    proto_root, output_root = _write_test_contract(tmp_path, option_block)
    proto_files = ("common.proto", "rpc_options.proto", "test_device.proto")
    physical_source = tmp_path / "physical.py"
    physical_source.write_text(
        "def read_state():\n    return 1.0\n",
        encoding="utf-8",
    )
    generate_protos(
        proto_root=proto_root,
        output_root=output_root,
        proto_files=proto_files,
    )

    physical_source.write_text(
        "def read_state():\n    return 999.0  # vendor SDK replacement\n",
        encoding="utf-8",
    )

    generate_protos(
        proto_root=proto_root,
        output_root=output_root,
        proto_files=proto_files,
        check=True,
    )


def test_generation_rejects_declared_but_not_yet_implemented_pattern(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ContractValidationError,
        match=r"RPC_PATTERN_QUERY_METADATA.*not (?:yet )?implemented|"
        r"not (?:yet )?implemented.*RPC_PATTERN_QUERY_METADATA",
    ):
        _generate_test_contract(
            tmp_path,
            dedent(
                """\
                option (labnet.devices.v1.rpc) = {
                  pattern: RPC_PATTERN_QUERY_METADATA
                  physical_method: "reset"
                  default_unit: "1"
                };
                """
            ),
        )


def test_generation_rejects_rpc_without_labnet_options(tmp_path: Path) -> None:
    with pytest.raises(
        ContractValidationError,
        match=re.escape("missing required option '(labnet.devices.v1.rpc)'"),
    ):
        _generate_test_contract(tmp_path, "")


@pytest.mark.parametrize(
    ("option_block", "missing_field"),
    [
        (
            '''option (labnet.devices.v1.rpc) = {
                 physical_method: "reset"
                 default_unit: "1"
               };''',
            "pattern",
        ),
        (
            '''option (labnet.devices.v1.rpc) = {
                 pattern: RPC_PATTERN_UNARY_COMMAND
                 default_unit: "1"
               };''',
            "physical_method",
        ),
        (
            '''option (labnet.devices.v1.rpc) = {
                 pattern: RPC_PATTERN_UNARY_COMMAND
                 physical_method: "reset"
               };''',
            "default_unit",
        ),
    ],
)
def test_generation_rejects_omitted_required_rpc_option_field(
    tmp_path: Path,
    option_block: str,
    missing_field: str,
) -> None:
    with pytest.raises(
        ContractValidationError,
        match=rf"must explicitly set field\(s\):.*{missing_field}",
    ):
        _generate_test_contract(tmp_path, option_block)


@pytest.mark.parametrize(
    ("option_block", "invalid_field"),
    [
        (
            '''option (labnet.devices.v1.rpc) = {
                 pattern: RPC_PATTERN_UNSPECIFIED
                 physical_method: "reset"
                 default_unit: "1"
               };''',
            "pattern",
        ),
        (
            '''option (labnet.devices.v1.rpc) = {
                 pattern: RPC_PATTERN_UNARY_COMMAND
                 physical_method: ""
                 default_unit: "1"
               };''',
            "physical_method",
        ),
        (
            '''option (labnet.devices.v1.rpc) = {
                 pattern: RPC_PATTERN_UNARY_COMMAND
                 physical_method: "reset"
                 default_unit: ""
               };''',
            "default_unit",
        ),
    ],
)
def test_generation_rejects_empty_or_unspecified_rpc_option_value(
    tmp_path: Path,
    option_block: str,
    invalid_field: str,
) -> None:
    with pytest.raises(
        ContractValidationError,
        match=rf"{invalid_field}.*(?:non-empty|UNSPECIFIED)",
    ):
        _generate_test_contract(tmp_path, option_block)


@pytest.mark.parametrize(
    ("rpc_name", "derived_name"),
    [
        ("Close", "close"),
        ("Class", "class"),
    ],
)
def test_generation_rejects_reserved_or_keyword_rpc_derived_name(
    tmp_path: Path,
    rpc_name: str,
    derived_name: str,
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_SCALAR_READ",
            request_fields="string device_id = 1; string operation_id = 2;",
            response_fields=(
                "string operation_id = 1; "
                "labnet.devices.v1.ScalarMeasurement measurement = 2;"
            ),
            rpc_name=rpc_name,
            physical_method="read_value",
        )
    message = str(caught.value)
    assert derived_name in message
    assert "conflict" in message.lower() or "rename" in message.lower()


def test_generation_rejects_python_keyword_physical_method(tmp_path: Path) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_SCALAR_READ",
            request_fields="string device_id = 1; string operation_id = 2;",
            response_fields=(
                "string operation_id = 1; "
                "labnet.devices.v1.ScalarMeasurement measurement = 2;"
            ),
            physical_method="class",
        )
    assert "physical_method" in str(caught.value)
    assert "keyword" in str(caught.value).lower()


@pytest.mark.parametrize("field_name", ["class", "self", "timeout_seconds"])
def test_generation_rejects_forwarded_python_or_transport_name_collision(
    tmp_path: Path,
    field_name: str,
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_SCALAR_READ",
            request_fields=(
                "string device_id = 1; string operation_id = 2; "
                f"optional string {field_name} = 3;"
            ),
            response_fields=(
                "string operation_id = 1; "
                "labnet.devices.v1.ScalarMeasurement measurement = 2;"
            ),
        )
    message = str(caught.value)
    assert field_name in message
    assert "rename" in message.lower() or "conflict" in message.lower()


def test_generation_rejects_forwarded_oneof_request_field(tmp_path: Path) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_SCALAR_READ",
            request_fields=(
                "string device_id = 1; string operation_id = 2; "
                "oneof selector { string channel_name = 3; uint32 channel_index = 4; }"
            ),
            response_fields=(
                "string operation_id = 1; "
                "labnet.devices.v1.ScalarMeasurement measurement = 2;"
            ),
        )
    message = str(caught.value).lower()
    assert "oneof" in message
    assert "not supported" in message


@pytest.mark.parametrize(
    "default_unit",
    [
        "x" * 33,
        r"H\nz",
    ],
)
def test_generation_rejects_long_or_control_character_default_unit(
    tmp_path: Path,
    default_unit: str,
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_SCALAR_READ",
            request_fields="string device_id = 1; string operation_id = 2;",
            response_fields=(
                "string operation_id = 1; "
                "labnet.devices.v1.ScalarMeasurement measurement = 2;"
            ),
            physical_method="read_value",
            default_unit=default_unit,
        )
    message = str(caught.value)
    assert "default_unit" in message
    assert "32 printable" in message


@pytest.mark.parametrize(
    ("request_fields", "response_fields", "missing_field"),
    [
        (
            "string operation_id = 2;",
            "string operation_id = 1; "
            "labnet.devices.v1.ScalarMeasurement measurement = 2;",
            "device_id",
        ),
        (
            "string device_id = 1;",
            "string operation_id = 1; "
            "labnet.devices.v1.ScalarMeasurement measurement = 2;",
            "operation_id",
        ),
        (
            "string device_id = 1; string operation_id = 2;",
            "labnet.devices.v1.ScalarMeasurement measurement = 2;",
            "operation_id",
        ),
    ],
)
def test_pattern_shape_requires_framework_identity_fields(
    tmp_path: Path,
    request_fields: str,
    response_fields: str,
    missing_field: str,
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_SCALAR_READ",
            request_fields=request_fields,
            response_fields=response_fields,
        )
    assert missing_field in str(caught.value)


@pytest.mark.parametrize(
    "measurement_field",
    [
        "",
        "double measurement = 2;",
    ],
)
def test_scalar_read_requires_canonical_measurement_response(
    tmp_path: Path,
    measurement_field: str,
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_SCALAR_READ",
            request_fields="string device_id = 1; string operation_id = 2;",
            response_fields=f"string operation_id = 1; {measurement_field}",
        )
    assert "measurement" in str(caught.value)


@pytest.mark.parametrize(
    ("device_fields", "invalid_field"),
    [
        ("optional string channel = 3; optional string unit = 5;", "value"),
        (
            "optional string channel = 3; string value = 4; "
            "optional string unit = 5;",
            "value",
        ),
        ("optional string channel = 3; double value = 4;", "unit"),
        (
            "optional string channel = 3; double value = 4; "
            "optional double unit = 5;",
            "unit",
        ),
    ],
)
def test_scalar_write_requires_canonical_value_and_unit_fields(
    tmp_path: Path,
    device_fields: str,
    invalid_field: str,
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_SCALAR_WRITE",
            request_fields=(
                "string device_id = 1; string operation_id = 2; " + device_fields
            ),
            response_fields=(
                "string operation_id = 1; "
                "labnet.devices.v1.ScalarMeasurement measurement = 2;"
            ),
        )
    assert invalid_field in str(caught.value)


@pytest.mark.parametrize(
    ("control_fields", "invalid_field"),
    [
        (
            "double sample_rate_hz = 4; optional uint64 sample_count = 5; "
            "google.protobuf.Duration duration = 6;",
            "channel",
        ),
        (
            "optional string channel = 3; string sample_rate_hz = 4; "
            "optional uint64 sample_count = 5; "
            "google.protobuf.Duration duration = 6;",
            "sample_rate_hz",
        ),
        (
            "optional string channel = 3; double sample_rate_hz = 4; "
            "google.protobuf.Duration duration = 6;",
            "sample_count",
        ),
        (
            "optional string channel = 3; double sample_rate_hz = 4; "
            "optional string sample_count = 5; "
            "google.protobuf.Duration duration = 6;",
            "sample_count",
        ),
        (
            "optional string channel = 3; double sample_rate_hz = 4; "
            "optional uint64 sample_count = 5;",
            "duration",
        ),
        (
            "optional string channel = 3; double sample_rate_hz = 4; "
            "optional uint64 sample_count = 5; bytes duration = 6;",
            "duration",
        ),
    ],
)
def test_polled_scalar_stream_requires_canonical_control_fields(
    tmp_path: Path,
    control_fields: str,
    invalid_field: str,
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        _generate_shape_contract(
            tmp_path,
            pattern="RPC_PATTERN_POLLED_SCALAR_STREAM",
            request_fields=(
                "string device_id = 1; string operation_id = 2; "
                + control_fields
            ),
            response_fields=(
                "string operation_id = 1; "
                "labnet.devices.v1.ScalarMeasurement measurement = 2;"
            ),
            server_streaming=True,
        )
    assert invalid_field in str(caught.value)


def test_map_request_uses_mapping_protocol_and_serializes_as_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_contracts, generated = _generate_importable_contract_package(
        tmp_path,
        monkeypatch,
        package_name="map_generated",
        proto_source='''\
            syntax = "proto3";
            package labnet.tests.map.v1;
            import "labnet/devices/v1/common.proto";
            import "labnet/devices/v1/rpc_options.proto";

            message MapRequest {
              string device_id = 1;
              string operation_id = 2;
              map<string, double> calibration = 3;
            }
            message MapResponse {
              string operation_id = 1;
              labnet.devices.v1.ScalarMeasurement measurement = 2;
            }
            service MapDeviceService {
              rpc ReadMap(MapRequest) returns (MapResponse) {
                option (labnet.devices.v1.rpc) = {
                  pattern: RPC_PATTERN_SCALAR_READ
                  physical_method: "read_map"
                  default_unit: "Hz"
                };
              }
            }
        ''',
    )
    field_spec = build_contracts[0].forwarded_field_specs[0]
    assert field_spec.name == "calibration"
    assert field_spec.annotation == "Mapping[str, float]"

    contract = generated.DEVICE_CONTRACTS[0]
    protocol_signature = inspect.signature(contract.physical_protocol.read_map)
    assert (
        protocol_signature.parameters["calibration"].annotation
        == "Mapping[str, float]"
    )

    from labnet.client.core.grpc_client import _build_contract_request

    calibration = {"offset": 1.25, "scale": 2.0}
    request, _ = _build_contract_request(
        contract.methods[0],
        device_id="map-device-1",
        args=(),
        kwargs={"calibration": calibration},
    )
    assert dict(request.calibration) == calibration


def test_shared_physical_method_merges_multi_rpc_field_as_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, generated = _generate_importable_contract_package(
        tmp_path,
        monkeypatch,
        package_name="shared_method_generated",
        proto_source='''\
            syntax = "proto3";
            package labnet.tests.shared.v1;
            import "labnet/devices/v1/common.proto";
            import "labnet/devices/v1/rpc_options.proto";

            message ReadWithGainRequest {
              string device_id = 1;
              string operation_id = 2;
              double gain = 3;
            }
            message ReadDefaultRequest {
              string device_id = 1;
              string operation_id = 2;
            }
            message ReadResponse {
              string operation_id = 1;
              labnet.devices.v1.ScalarMeasurement measurement = 2;
            }
            service SharedMethodDeviceService {
              rpc ReadWithGain(ReadWithGainRequest) returns (ReadResponse) {
                option (labnet.devices.v1.rpc) = {
                  pattern: RPC_PATTERN_SCALAR_READ
                  physical_method: "read_shared"
                  default_unit: "Hz"
                };
              }
              rpc ReadDefault(ReadDefaultRequest) returns (ReadResponse) {
                option (labnet.devices.v1.rpc) = {
                  pattern: RPC_PATTERN_SCALAR_READ
                  physical_method: "read_shared"
                  default_unit: "Hz"
                };
              }
            }
        ''',
    )
    contract = generated.DEVICE_CONTRACTS[0]
    protocol_signature = inspect.signature(contract.physical_protocol.read_shared)
    gain = protocol_signature.parameters["gain"]
    assert gain.default is None
    assert "None" in gain.annotation

    class CompatiblePhysical:
        def read_shared(self, gain: float | None = None) -> float:
            return 0.0 if gain is None else gain

    class RequiredGainPhysical:
        def read_shared(self, gain: float) -> float:
            return gain

    generated.validate_implementation(CompatiblePhysical, contract)
    with pytest.raises(generated.PhysicalContractError) as caught:
        generated.validate_implementation(RequiredGainPhysical, contract)
    message = str(caught.value)
    assert "gain" in message
    assert "not supplied by every mapped RPC" in message


def test_current_physical_device_satisfies_generated_contract() -> None:
    validate_implementation(PhysicalDummyDevice, _dummy_contract())
    validate_implementation(
        PhysicalSimulatedTunableLaser,
        registry.by_service(
            "labnet.devices.v1.SimulatedTunableLaserService"
        ),
    )


def test_physical_implementation_body_can_change_without_contract_regeneration() -> None:
    class FirstImplementation:
        def read_value(self, channel: str | None = None) -> float:
            return 1.0

        def write_value(
            self,
            value: float,
            channel: str | None = None,
        ) -> None:
            pass

    class ReplacementImplementation:
        def read_value(self, channel: str | None = None) -> float:
            return 999.0

        def write_value(
            self,
            value: float,
            channel: str | None = None,
        ) -> None:
            if value < 0:
                raise ValueError("The replacement driver rejects negative values.")

    contract = _dummy_contract()
    validate_implementation(FirstImplementation, contract)
    validate_implementation(ReplacementImplementation, contract)


def test_missing_physical_method_fails_with_actionable_error() -> None:
    class MissingReadValue:
        def write_value(
            self,
            value: float,
            channel: str | None = None,
        ) -> None:
            pass

    with pytest.raises(PhysicalContractError) as caught:
        validate_implementation(MissingReadValue, _dummy_contract())
    message = str(caught.value).lower()
    assert "read_value" in message
    assert "missing" in message or "implement" in message


def test_mismatched_physical_method_signature_fails_with_actionable_error() -> None:
    class MismatchedReadValue:
        def read_value(
            self,
            required_driver_argument: str,
            channel: str | None = None,
        ) -> float:
            return 0.0

        def write_value(
            self,
            value: float,
            channel: str | None = None,
        ) -> None:
            pass

    with pytest.raises(PhysicalContractError) as caught:
        validate_implementation(MismatchedReadValue, _dummy_contract())
    message = str(caught.value)
    assert "read_value" in message
    assert "required_driver_argument" in message


def test_optional_proto_argument_cannot_be_required_by_physical_method() -> None:
    class RequiredChannel:
        def read_value(self, channel: str) -> float:
            return 0.0

        def write_value(self, value: float, channel: str) -> None:
            pass

    with pytest.raises(PhysicalContractError) as caught:
        validate_implementation(RequiredChannel, _dummy_contract())
    message = str(caught.value)
    assert "channel" in message
    assert "optional" in message.lower() or "default" in message.lower()


def test_generation_rejects_duplicate_derived_device_type(tmp_path: Path) -> None:
    proto_root = tmp_path / "collision-protos"
    output_root = tmp_path / "collision-generated"
    proto_root.mkdir()
    output_root.mkdir()
    for filename, package in (("first.proto", "one.v1"), ("second.proto", "two.v1")):
        (proto_root / filename).write_text(
            dedent(
                f'''\
                syntax = "proto3";
                package {package};
                import "labnet/devices/v1/common.proto";
                import "labnet/devices/v1/rpc_options.proto";

                message ReadRequest {{
                  string device_id = 1;
                  string operation_id = 2;
                }}
                message ReadResponse {{
                  string operation_id = 1;
                  labnet.devices.v1.ScalarMeasurement measurement = 2;
                }}
                service CollisionDeviceService {{
                  rpc Read(ReadRequest) returns (ReadResponse) {{
                    option (labnet.devices.v1.rpc) = {{
                      pattern: RPC_PATTERN_SCALAR_READ
                      physical_method: "read"
                      default_unit: "arb"
                    }};
                  }}
                }}
                '''
            ),
            encoding="utf-8",
        )

    with pytest.raises(
        ContractValidationError,
        match=r"derive (?:a )?unique device_type|device_type.*unique",
    ):
        generate_protos(
            proto_root=proto_root,
            output_root=output_root,
            proto_files=(
                "common.proto",
                "rpc_options.proto",
                "first.proto",
                "second.proto",
            ),
        )
