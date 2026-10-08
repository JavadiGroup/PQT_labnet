"""Export script: the generated code's shape, order, names, and safety."""

from __future__ import annotations

import ast
import copy
import re

import pytest

from labnet_control_center.experiment_script import ScriptError, clean_text, generate_script


def experiment(**changes) -> dict:
    doc = {
        "id": "aom-scan-abcdef",
        "version": 4,
        "updated_at": "2026-10-06T12:00:00Z",
        "created_by": "Rob",
        "updated_by": "Anna",
        "name": "AOM frequency scan",
        "description": "Efficiency against RF frequency.",
        "run": {"settle_seconds": 0.2, "samples": 10, "sample_rate_hz": 20.0, "output_name": "aom_scan"},
        "nodes": [
            {"id": "laser", "kind": "instrument", "label": "Laser", "x": 0, "y": 0,
             "device_id": "dev01-laser", "device_type": "dummy_laser",
             "settings": [{"id": "s1", "method": "set_current", "args": {"value": 120}},
                          {"id": "s2", "method": "set_emission_enabled", "args": {"enabled": True}}]},
            {"id": "aom", "kind": "instrument", "label": "AOM 1", "x": 256, "y": 0,
             "device_id": "dev01-aom", "device_type": "dummy_aom",
             "settings": [{"id": "s1", "method": "set_frequency",
                           "sweep": {"start": 70, "stop": 90, "points": 21}}],
             "records": [{"id": "r1", "method": "read_diffraction_efficiency"}]},
            {"id": "awg", "kind": "instrument", "label": "AWG", "x": 256, "y": -160,
             "device_id": "dev02-awg", "device_type": "dummy_awg",
             "settings": [{"id": "s1", "method": "set_waveform", "args": {"waveform": "sine", "channel": "CH2"}}]},
            {"id": "m1", "kind": "mirror", "x": 512, "y": 0},
        ],
        "links": [
            {"id": "l1", "source": "laser", "target": "aom"},
            {"id": "l2", "source": "awg", "target": "aom", "type": "signal", "label": "CH2 → RF"},
            {"id": "l3", "source": "aom", "target": "m1"},
        ],
    }
    doc.update(changes)
    return doc


def lines_with(script: str, text: str) -> list[str]:
    return [line.strip() for line in script.splitlines() if text in line]


def test_a_scan_becomes_a_script_like_example_04() -> None:
    script = generate_script(experiment())

    compile(script, "aom_scan.py", "exec")
    assert script == generate_script(experiment())  # the same version, the same script
    assert "from labnet import DummyAOM, DummyAWG, DummyLaser, LabClient, LabClientError" in script
    assert "AOM_1_FREQUENCY = linspace(70.0, 90.0, 21)" in script
    leases = lines_with(script, "client=central)")
    assert leases == [
        'laser = DummyLaser(device_id="dev01-laser", client=central)',
        'awg = DummyAWG(device_id="dev02-awg", client=central)',  # upstream of the AOM by its cable
        'aom_1 = DummyAOM(device_id="dev01-aom", client=central)',
    ]
    assert "laser.set_current(120.0)  # mA" in script
    assert "laser.set_emission_enabled(enabled=True)" in script
    assert 'awg.set_waveform(waveform="sine", channel="CH2")' in script
    assert "for aom_1_frequency in AOM_1_FREQUENCY:" in script
    assert "aom_1.set_frequency(aom_1_frequency)" in script
    assert "aom_1_diffraction_efficiency = [aom_1.read_diffraction_efficiency() for _ in range(10)]" in script
    assert '"aom_1_frequency_MHz", "aom_1_diffraction_efficiency_mean", "aom_1_diffraction_efficiency_std"' in script
    closes = lines_with(script, ".close()")
    assert closes == ["output.close()", "aom_1.close()", "awg.close()", "laser.close()", "central.close()"]
    assert "#   Laser -> AOM 1 -> Mirror" in script
    assert "#   (AWG -> AOM 1: CH2 → RF)" in script


def test_streams_single_samples_and_no_settle_time() -> None:
    doc = experiment(run={"settle_seconds": 0, "samples": 1, "sample_rate_hz": 50.0, "output_name": "x"})
    doc["nodes"][1]["records"].append({"id": "r2", "method": "stream_diffraction_efficiency", "samples": 5})
    script = generate_script(doc)
    assert "import time" not in script and "SETTLE_SECONDS" not in script
    assert "aom_1_diffraction_efficiency = [aom_1.read_diffraction_efficiency()]" in script
    assert ("aom_1_diffraction_efficiency_2 = [sample.value for sample in "
            "aom_1.stream_diffraction_efficiency(sample_rate_hz=50.0, sample_count=5)]") in script
    assert "aom_1_diffraction_efficiency[0]," in script and "*summarize(aom_1_diffraction_efficiency_2)," in script


def test_loops_advance_together_or_nest() -> None:
    doc = experiment()
    doc["nodes"][0]["settings"][0] = {"id": "s1", "method": "set_current",
                                      "sweep": {"start": 50, "stop": 150, "points": 21, "axis": 0}}
    doc["nodes"][2]["settings"].append({"id": "s2", "method": "set_offset", "args": {"channel": "CH1"},
                                        "sweep": {"start": -1, "stop": 1, "points": 3, "axis": 2}})
    script = generate_script(doc)
    assert "for laser_current, aom_1_frequency in zip(LASER_CURRENT, AOM_1_FREQUENCY):" in script
    assert "    for awg_offset_ch1 in AWG_OFFSET_CH1:" in script
    assert 'awg.set_offset(awg_offset_ch1, channel="CH1")' in script
    assert "TOTAL_POINTS = 63" in script


def test_a_command_sweep_uses_its_argument_name() -> None:
    doc = experiment(nodes=[{
        "id": "laser", "kind": "instrument", "device_id": "lab-pc-01-laser", "device_type": "simulated_tunable_laser",
        "settings": [{"id": "s1", "method": "go_to_wavelength_nm",
                      "sweep": {"field": "wavelength_nm", "start": 770, "stop": 771, "points": 3}}],
    }], links=[])
    script = generate_script(doc)
    # Names are capped at 32 characters.
    assert "lab_pc_01_laser.go_to_wavelength_nm(wavelength_nm=lab_pc_01_laser_go_to_wavelength)" in script


def test_names_are_plain_unique_identifiers() -> None:
    doc = experiment()
    doc["nodes"][0]["label"] = "class"
    doc["nodes"][1]["label"] = "1st AOM"
    doc["nodes"][2]["label"] = "print"
    script = generate_script(doc)
    leases = lines_with(script, "client=central)")
    assert [line.split(" = ")[0] for line in leases] == ["class_device", "print_device", "dev_1st_aom"]


# Text the experiment schema allows (it already refuses controls, bidirectional
# and zero-width characters) but that would break naive code generation.
HOSTILE = '"""\\" + __import__("os").system("calc") + """ # coding: utf-7 ; x = 1 \\'
HOSTILE_LINES = 'first line\n"""\nimport os\nos.system("calc")\n# -*- coding: utf-7 -*-\n\\'


def _shape(script: str) -> str:
    """The code's structure, ignoring the docstring's text."""

    tree = ast.parse(script)
    tree.body[0].value.value = ""
    return ast.dump(tree)


def test_hostile_text_cannot_change_the_code() -> None:
    benign = experiment()
    for node in benign["nodes"]:
        node["label"] = ""  # labels name variables; keep both versions' names equal
    hostile = copy.deepcopy(benign)
    hostile["name"] = HOSTILE[:120]
    hostile["description"] = HOSTILE_LINES + HOSTILE
    hostile["created_by"] = HOSTILE[:64]
    hostile["updated_by"] = HOSTILE[:64]
    hostile["updated_at"] = HOSTILE[:40]
    hostile["nodes"][3]["label"] = HOSTILE[:60]  # the mirror: shown, never a name
    hostile["links"][1]["label"] = HOSTILE[:40]
    script = generate_script(hostile)

    assert _shape(script) == _shape(generate_script(benign))
    docstring = ast.get_docstring(ast.parse(script))
    assert docstring.splitlines()[0].startswith("'''/' + __import__('os')")  # quotes and backslashes defused


def test_clean_text_drops_invisible_characters() -> None:
    assert clean_text("a‮b​c d\re") == "abcd e"  # dropped; a carriage return becomes a space
    assert clean_text('say "hi" \\n') == "say 'hi' /n"


def test_labels_become_names_but_never_code() -> None:
    doc = experiment()
    doc["nodes"][1]["label"] = 'aom"); import os; os.system("calc'
    script = generate_script(doc)
    tree = ast.parse(script)
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert "os" not in imported
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "system" not in called
    assert 'aom_import_os_os_system_calc = DummyAOM(device_id="dev01-aom", client=central)' in script


def test_problems_stop_the_export() -> None:
    doc = experiment()
    doc["nodes"][1]["records"] = [{"id": "r1", "method": "set_frequency"}]
    with pytest.raises(ScriptError) as stopped:
        generate_script(doc)
    assert "can't be recorded" in stopped.value.errors[0]["message"]


def test_unused_instruments_are_not_leased() -> None:
    doc = experiment()
    doc["nodes"].append({"id": "eom", "kind": "instrument", "device_id": "dev01-eom", "device_type": "dummy_eom"})
    script = generate_script(doc)
    assert "DummyEOM" not in script.split("from labnet import")[1].splitlines()[0]
    assert "# On the diagram but not used (nothing set or recorded): dev01-eom" in script
    assert re.search(r"^\s*eom\b", script, re.MULTILINE) is None
