"""A real local network for scripts that use ``LabClient.from_env()``.

``labnet.testing.local_network`` runs Central and one Lab PC in this process;
the examples and generated experiment scripts connect through
``LABNET_ENV_FILE``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from labnet import (
    PhysicalDummyAOM,
    PhysicalDummyAWG,
    PhysicalDummyDevice,
    PhysicalDummyEOM,
    PhysicalDummyLaser,
    PhysicalSimulatedWavemeter,
)
from labnet.testing import local_network


LAB_PC_ID = "lab-pc-test"


def _devices() -> list:
    # Every simulated family; noise off where the tests compare exact values.
    return [
        PhysicalDummyDevice("test-dummy"),
        PhysicalSimulatedWavemeter("test-wavemeter"),
        PhysicalDummyAOM("test-aom", noise=0.0),
        PhysicalDummyEOM("test-eom", noise=0.0),
        PhysicalDummyAWG("test-awg", noise=0.0),
        PhysicalDummyLaser("test-laser", noise_mw=0.0, noise_nm=0.0),
    ]


@pytest.fixture(scope="module")
def researcher_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("network")
    with local_network(_devices, root, users=("researcher", "other-client"), lab_pc_id=LAB_PC_ID) as network:
        yield network.env_file("researcher")


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch, researcher_env: Path) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in [key for key in os.environ if key.startswith("LABNET_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("LABNET_ENV_FILE", str(researcher_env))
