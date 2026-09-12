"""Tests for tools/export_core.py — the generated core layout must be sound."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import py_compile
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("export_core", ROOT / "tools" / "export_core.py")
assert SPEC and SPEC.loader
export_core = importlib.util.module_from_spec(SPEC)
sys.modules["export_core"] = export_core
SPEC.loader.exec_module(export_core)


@pytest.mark.parametrize("stage", ["minimal", "full"])
def test_export_produces_a_core_layout(tmp_path: Path, stage: str) -> None:
    comp, tests = export_core.export(stage, tmp_path)

    names = {p.name for p in comp.iterdir()}
    # Never in core: the card, the icons, the module that serves them
    assert not ({"www", "brand", "frontend.py", "hacs.json", "translations"} & names)
    for required in (
        "__init__.py",
        "config_flow.py",
        "coordinator.py",
        "sensor.py",
        "manifest.json",
        "strings.json",
        "quality_scale.yaml",
    ):
        assert required in names

    init = (comp / "__init__.py").read_text(encoding="utf-8")
    assert "@feature" not in init and "@endfeature" not in init
    assert "async_register_card" not in init

    manifest = json.loads((comp / "manifest.json").read_text(encoding="utf-8"))
    assert "version" not in manifest and "issue_tracker" not in manifest
    assert "dependencies" not in manifest and "after_dependencies" not in manifest
    assert manifest["requirements"] == ["pycellarion==0.1.0"]
    assert manifest["loggers"] == ["pycellarion"]
    assert manifest["documentation"] == "https://www.home-assistant.io/integrations/cellarion"
    assert manifest["quality_scale"] == ("bronze" if stage == "minimal" else "platinum")
    assert list(manifest)[:2] == ["domain", "name"]

    scale = (comp / "quality_scale.yaml").read_text(encoding="utf-8")
    if stage == "minimal":
        assert "  diagnostics: todo" in scale and "  repair-issues: todo" in scale
        assert "  action-setup:\n    status: exempt" in scale
        assert "  action-setup: done" not in scale
    else:
        assert "  diagnostics: done" in scale and "  action-setup: done" in scale

    strings = json.loads((comp / "strings.json").read_text(encoding="utf-8"))
    if stage == "minimal":
        assert "services" not in strings and "issues" not in strings
        assert "consume_failed" not in strings["exceptions"]
        assert not ({"services.py", "push.py", "diagnostics.py", "services.yaml"} & names)
        assert "async_setup_services" not in init and "async_push_listener" not in init
    else:
        assert "services" in strings and "issues" in strings
        assert {"services.py", "push.py", "diagnostics.py", "services.yaml"} <= names

    # Every exported module still parses
    for module in comp.glob("*.py"):
        py_compile.compile(str(module), doraise=True)

    test_names = {p.name for p in tests.iterdir()}
    assert "conftest.py" in test_names and "test_config_flow.py" in test_names
    assert "test_export_core.py" not in test_names  # about this repo, not the integration
    conftest = (tests / "conftest.py").read_text(encoding="utf-8")
    assert "custom_components" not in conftest
    assert "from tests.common import MockConfigEntry" in conftest
    assert "enable_custom_integrations" not in conftest
    if stage == "minimal":
        assert "test_services.py" not in test_names and "test_push.py" not in test_names


def test_feature_blocks_are_balanced() -> None:
    """A stray marker would silently drop code; make that a test failure."""
    for module in (ROOT / "custom_components" / "cellarion").glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert text.count("# @feature ") == text.count("# @endfeature"), module.name
