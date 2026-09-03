from __future__ import annotations

from examples.client_test.async_client import run as async_client_run
from examples.central_server.main import app as central_app
from examples.client_test.client import main as client_main
from examples.lab_pc.main import create_app as create_lab_pc_app
from examples.sacher_laser.calibrate_and_scan import scan_points
from examples.sacher_laser.remote_scan import main as sacher_remote_main
from labnet import MaxonEpos2, PhysicalSacherLaser, SacherLaser


def test_examples_compose_the_installed_labnet_package() -> None:
    assert central_app.title == "LabNet Central Server"
    assert callable(async_client_run)
    assert callable(client_main)
    assert callable(sacher_remote_main)
    assert MaxonEpos2.__name__ == "MaxonEpos2"
    assert PhysicalSacherLaser.device_type == SacherLaser.device_type
    assert scan_points(770.0, 774.0, 3) == [770.0, 772.0, 774.0]
    assert create_lab_pc_app().title == "Labnet Lab-PC Management Plane"
