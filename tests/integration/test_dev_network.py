"""`labnet dev`: seed, start, check, smoke-test and stop a real network of separate processes."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from labnet.dev import network
from labnet.dev.processes import DevError, command, port_in_use


def _free_ports(count: int) -> list[int]:
    sockets = [socket.socket() for _ in range(count)]
    try:
        for probe in sockets:
            probe.bind(("127.0.0.1", 0))
        return [probe.getsockname()[1] for probe in sockets]
    finally:
        for probe in sockets:
            probe.close()


@pytest.mark.skipif(command("labnet-control-center") is None, reason="needs the whole workspace installed")
def test_the_dev_network_goes_up_answers_and_goes_down(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    central, control_center, watchdog, grpc, health = _free_ports(5)
    net = network.DevNetwork(
        root=tmp_path, central_port=central, control_center_port=control_center, watchdog_port=watchdog,
        lab_pcs=(network.LabPC("lab-pc-test", grpc, health, "labnet_sim.inventories.dev_lab_pc_02:create_devices"),),
    )
    try:
        network.up(net)  # seeds first: there is no .devnet yet
        assert (tmp_path / ".devnet" / "researcher.env").is_file()
        assert all(port_in_use(port) for port in (central, control_center, watchdog, grpc, health))
        assert network.status(net) == 0
        assert "dev02-wavemeter" in capsys.readouterr().out
        assert network.smoke(net) == 0
        assert "OK   dev02-awg" in capsys.readouterr().out
        with pytest.raises(DevError, match="still running"):
            network.seed(net)
        network.restart(net, ["lab-pc-test"])
        assert port_in_use(health)
    finally:
        network.down(net)
    assert not any(port_in_use(port) for port in (central, control_center, watchdog, grpc, health))
    assert not any((tmp_path / ".devnet" / "run").glob("*.pid"))


def test_unknown_components_are_named(tmp_path: Path) -> None:
    with pytest.raises(DevError, match="Unknown component nonsense; choose from central, lab-pc-dev-01"):
        network.down(network.DevNetwork(root=tmp_path), ["nonsense"])
