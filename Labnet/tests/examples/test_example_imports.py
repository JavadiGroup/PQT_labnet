from __future__ import annotations

from examples.client_test.async_client import run as async_client_run
from examples.central_server.main import app as central_app
from examples.client_test.client import main as client_main
from examples.lab_pc.main import create_app as create_lab_pc_app


def test_examples_compose_the_installed_labnet_package() -> None:
    assert central_app.title == "LabNet Central Server"
    assert callable(async_client_run)
    assert callable(client_main)
    assert create_lab_pc_app().title == "Labnet Lab-PC Management Plane"
