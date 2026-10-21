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
    assert manifest["requirements"] == ["pycellarion==0.2.0"]
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
    icons = json.loads((comp / "icons.json").read_text(encoding="utf-8"))
    # The inventory (get_bottles, its cache, the image proxy) is HACS-only for now
    assert "inventory.py" not in names
    assert not {"inventory_not_supported", "inventory_failed"} & set(strings["exceptions"])
    assert "get_bottles" not in icons.get("services", {})
    if stage == "minimal":
        assert "services" not in strings and "issues" not in strings
        assert "consume_failed" not in strings["exceptions"]
        assert not ({"services.py", "push.py", "diagnostics.py", "services.yaml"} & names)
        assert "async_setup_services" not in init and "async_push_listener" not in init
        assert "services" not in icons
    else:
        assert "services" in strings and "issues" in strings
        assert {"services.py", "push.py", "diagnostics.py", "services.yaml"} <= names
        assert set(strings["services"]) == {"consume_bottle"}
        assert set(icons["services"]) == {"consume_bottle"}
        services_yaml = (comp / "services.yaml").read_text(encoding="utf-8")
        assert "consume_bottle:" in services_yaml and "get_bottles" not in services_yaml
        services_py = (comp / "services.py").read_text(encoding="utf-8")
        for name in ("get_bottles", "inventory", "SupportsResponse", "ServiceResponse"):
            assert name not in services_py, name
        assert "_resolve_entry" in services_py

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
    assert "def snapshot" not in conftest and "SnapshotAssertion" not in conftest
    for test_file in tests.glob("*.py"):
        text = test_file.read_text(encoding="utf-8")
        assert "@feature" not in text, test_file.name
        assert "from __future__" not in text, test_file.name
    if stage == "minimal":
        assert "test_services.py" not in test_names and "test_push.py" not in test_names
        init_tests = (tests / "test_init.py").read_text(encoding="utf-8")
        assert "push_issue_id" not in init_tests and "_FakeResources" not in init_tests


def test_feature_blocks_are_balanced() -> None:
    """A stray marker would silently drop code; make that a test failure."""
    for module in [
        *(ROOT / "custom_components" / "cellarion").glob("*.py"),
        *(ROOT / "tests").glob("*.py"),
    ]:
        text = module.read_text(encoding="utf-8")
        assert text.count("# @feature ") == text.count("# @endfeature"), module.name


def test_prune_handles_parenthesised_imports() -> None:
    """Unused names leave a parenthesised import; an all-unused one disappears."""
    text = (
        "from a import (\n    Used,\n    Unused,\n)\n"
        "from b import (\n    Gone,\n)\n"
        "from c import x, y\n"
        "Used(); x()\n"
    )

    pruned = export_core.prune_unused_imports(text)

    assert pruned == "from a import (\n    Used,\n)\n\nfrom c import x\nUsed(); x()\n"


def test_drop_services_removes_one_action() -> None:
    """A top-level action block goes; its neighbours stay intact."""
    text = "# c\na:\n  fields:\n    x:\n\nb:\n  fields:\n    y:\n"

    assert export_core.drop_services(text, {"b"}) == "# c\na:\n  fields:\n    x:\n"
    assert export_core.drop_services(text, {"a"}) == "# c\nb:\n  fields:\n    y:\n"
