"""Export the integration in the layout a Home Assistant core submission uses.

This repository stays the single source of truth. A core pull request is a
generated copy: run this script, copy the output into a fork of
home-assistant/core, and open the pull request from there. When a reviewer
asks for a change, make it here and re-run the export.

Stages mirror how core accepts new integrations: a minimal first pull
request, then one feature per follow-up.

    python tools/export_core.py --stage minimal /path/to/core
    python tools/export_core.py --stage full /path/to/core

Given a core checkout, the integration lands in
homeassistant/components/cellarion/ and its tests in
tests/components/cellarion/. Given any other directory, the same two
subtrees are created inside it.

What the export does:
- copies the integration package, minus HACS-only files (the bundled card,
  the brand icons, frontend.py, hacs.json)
- drops feature blocks marked `# @feature <name>` … `# @endfeature` that the
  stage does not include, and the modules that belong to those features
- rewrites the manifest for core (no version, sorted keys)
- drops translations/en.json (core generates it from strings.json) and
  trims strings.json sections that belong to excluded features
- rewrites the tests for core's tree and fixtures
- runs `ruff check --fix` (and, in a core checkout, `ruff format`) when ruff
  is installed, and regenerates translations/en.json in a core checkout
- adds the integration to core's .strict-typing
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "custom_components" / "cellarion"
TESTS = ROOT / "tests"
DOMAIN = "cellarion"

# Files that never go to core.
HACS_ONLY = {"www", "brand", "frontend.py", "__pycache__"}
# Tests about this repository itself, not about the integration.
REPO_ONLY_TESTS = {"test_export_core.py"}

# Feature name → (integration modules, test modules, strings.json keys).
FEATURES: dict[str, tuple[set[str], set[str], set[str]]] = {
    "frontend": ({"frontend.py"}, set(), set()),
    "services": ({"services.py", "services.yaml"}, {"test_services.py"}, {"services"}),
    "push": ({"push.py"}, {"test_push.py"}, {"issues"}),
    "diagnostics": ({"diagnostics.py"}, {"test_diagnostics.py"}, set()),
    # The inventory card's backend (get_bottles action, inventory cache). HACS
    # only for now: no stage includes it.
    "inventory": ({"inventory.py"}, {"test_inventory.py"}, set()),
}

# Actions that belong to a feature other than "services": dropped from
# services.yaml, strings.json and icons.json when that feature is excluded.
FEATURE_SERVICES: dict[str, set[str]] = {"inventory": {"get_bottles"}}

STAGES: dict[str, set[str]] = {
    # Core's first pull request: config flow, coordinator, sensors. Nothing else.
    "minimal": set(),
    # Everything core can take.
    "full": {"services", "push", "diagnostics"},
}

# The quality-scale tier the manifest claims per stage. A first pull request
# claims Bronze; the tier is raised in follow-ups as features land.
STAGE_TIER = {"minimal": "bronze", "full": "platinum"}

# Quality-scale rules that only hold once a feature is present. In a stage
# without the feature they become "todo" (rules above the claimed tier) or an
# exemption with a reason (rules inside it).
FEATURE_RULES: dict[str, dict[str, str]] = {
    "services": {
        "action-setup": "exempt: The integration has no actions yet.",
        "docs-actions": "exempt: The integration has no actions yet.",
        "action-exceptions": "todo",
    },
    "push": {"repair-issues": "todo"},
    "diagnostics": {"diagnostics": "todo"},
}

# Exception translation keys that only the services feature raises.
SERVICE_EXCEPTIONS = {
    "no_accounts",
    "unknown_entry",
    "multiple_accounts",
    "consume_failed",
    "consume_auth_failed",
    "consume_scope_missing",
}
# Exception translation keys per feature, dropped when the feature is.
FEATURE_EXCEPTIONS: dict[str, set[str]] = {
    "services": SERVICE_EXCEPTIONS,
    "inventory": {"inventory_not_supported", "inventory_auth_failed", "inventory_failed"},
}

FEATURE_BLOCK = re.compile(
    r"^(?P<indent>[ \t]*)# @feature (?P<name>\w+)\n(?P<body>.*?)^[ \t]*# @endfeature\n", re.M | re.S
)


def strip_features(text: str, keep: set[str]) -> str:
    """Remove marked blocks for features not in `keep`; unwrap the others."""

    def repl(m: re.Match[str]) -> str:
        return m.group("body") if m.group("name") in keep else ""

    return FEATURE_BLOCK.sub(repl, text)


def drop_future_annotations(text: str) -> str:
    """Core forbids `from __future__ import annotations` (Python 3.14 and up)."""
    return re.sub(r"^from __future__ import annotations\n\n?", "", text, flags=re.M)


def add_strict_typing(dest: Path) -> None:
    """List the integration in core's .strict-typing (sorted), for the mypy config."""
    path = dest / ".strict-typing"
    if not path.is_file():
        return
    entry = f"homeassistant.components.{DOMAIN}.*"
    lines = path.read_text(encoding="utf-8").splitlines()
    if entry in lines:
        return
    components = [line for line in lines if line.startswith("homeassistant.components.")]
    others = [line for line in lines if not line.startswith("homeassistant.components.")]
    components = sorted([*components, entry])
    path.write_text("\n".join([*others, *components]) + "\n", encoding="utf-8")


IMPORT_LINE = re.compile(
    r"^from (?P<module>[\w.]+) import (?:\((?P<group>[^)]*)\)|(?P<names>[^(\n]+))$", re.M
)


def prune_unused_imports(text: str) -> str:
    """Drop names from `from x import a, b as c` (one line or parenthesised) nothing uses.

    Dropping a feature block can orphan an import that shares a statement
    with one still in use; ruff treats that fix as unsafe in __init__.py, so
    it is done here, deterministically, for the exported copy only.
    """

    def repl(m: re.Match[str]) -> str:
        if m.group("module") == "__future__":
            return m.group(0)
        rest = text.replace(m.group(0), "")
        grouped = m.group("group") is not None
        kept = []
        for item in (n.strip() for n in (m.group("group") or m.group("names")).split(",")):
            if not item:
                continue
            alias = item.split(" as ")[-1].strip()
            if re.search(r"\b" + re.escape(alias) + r"\b", rest):
                kept.append(item)
        if not kept:
            return ""
        if grouped:
            body = "".join(f"    {item},\n" for item in kept)
            return f"from {m.group('module')} import (\n{body})"
        return f"from {m.group('module')} import {', '.join(kept)}"

    return IMPORT_LINE.sub(repl, text)


def excluded_services(keep: set[str]) -> set[str]:
    """Actions whose feature the stage leaves out."""
    return set().union(*(names for f, names in FEATURE_SERVICES.items() if f not in keep))


def drop_services(text: str, names: set[str]) -> str:
    """Remove top-level action definitions from services.yaml text."""
    for name in names:
        text = re.sub(rf"^{re.escape(name)}:\n(?:[ \t]+.*\n|\n)*", "", text, flags=re.M)
    return text.rstrip("\n") + "\n"


def export_icons(text: str, keep: set[str]) -> str:
    icons = json.loads(text)
    services = icons.get("services", {})
    for name in excluded_services(keep):
        services.pop(name, None)
    if "services" not in keep or not services:
        icons.pop("services", None)
    return json.dumps(icons, indent=2, ensure_ascii=False) + "\n"


def export_manifest(text: str, stage: str) -> str:
    manifest = json.loads(text)
    manifest.pop("version", None)  # core integrations are versioned with core
    manifest.pop("issue_tracker", None)  # core issues live in home-assistant/core
    # The card needed http and lovelace; core ships no card
    manifest.pop("after_dependencies", None)
    manifest.pop("dependencies", None)
    manifest["documentation"] = f"https://www.home-assistant.io/integrations/{DOMAIN}"
    manifest["loggers"] = ["pycellarion"]
    manifest["quality_scale"] = STAGE_TIER[stage]
    ordered = {k: manifest[k] for k in ("domain", "name") if k in manifest}
    ordered.update(sorted((k, v) for k, v in manifest.items() if k not in ordered))
    return json.dumps(ordered, indent=2) + "\n"


def export_quality_scale(text: str, keep: set[str]) -> str:
    """Mark rules of excluded features as todo/exempt; leave the rest as is."""
    for feature, rules in FEATURE_RULES.items():
        if feature in keep:
            continue
        for rule, value in rules.items():
            if value.startswith("exempt: "):
                repl = f"  {rule}:\n    status: exempt\n    comment: {value[8:]}"
            else:
                repl = f"  {rule}: {value}"
            text = re.sub(
                rf"^  {re.escape(rule)}:(?: done| todo|\n(?:    .*\n)+?)(?=\n|$)",
                repl,
                text,
                flags=re.M,
            )
            text = re.sub(rf"^  {re.escape(rule)}: done$", repl, text, flags=re.M)
    return text


def export_strings(text: str, keep: set[str]) -> str:
    strings = json.loads(text)
    dropped_keys = set().union(*(FEATURES[f][2] for f in FEATURES if f not in keep))
    for key in dropped_keys:
        strings.pop(key, None)
    for feature, keys in FEATURE_EXCEPTIONS.items():
        if feature not in keep:
            for key in keys:
                strings.get("exceptions", {}).pop(key, None)
    for name in excluded_services(keep):
        strings.get("services", {}).pop(name, None)
    return json.dumps(strings, indent=2, ensure_ascii=False) + "\n"


def export_test(text: str, keep: set[str]) -> str:
    text = prune_unused_imports(strip_features(text, keep))
    text = text.replace("custom_components.cellarion", "homeassistant.components.cellarion")
    text = text.replace("pytest_homeassistant_custom_component.common", "tests.common")
    text = text.replace(
        "pytest_homeassistant_custom_component.test_util.aiohttp", "tests.test_util.aiohttp"
    )
    # Core's aioclient_mock fixture lives in tests.conftest; the custom-component
    # shim's autouse fixture is not needed there, and neither is the snapshot
    # override (core's own tests/conftest.py applies its extension).
    text = re.sub(
        r"@pytest\.fixture\(autouse=True\)\ndef auto_enable_custom_integrations\(enable_custom_integrations\):\n(?:    .*\n)+?\n",
        "",
        text,
    )
    text = re.sub(
        r"\n@pytest\.fixture\ndef snapshot\(snapshot: SnapshotAssertion\) -> SnapshotAssertion:\n(?:    .*\n|\n)+?    return .*\n\n",
        "",
        text,
    )
    return prune_unused_imports(text)


def export(stage: str, dest: Path) -> tuple[Path, Path]:
    keep = STAGES[stage]
    is_core = (dest / "homeassistant" / "components").is_dir()
    comp = dest / "homeassistant" / "components" / DOMAIN
    tests = dest / "tests" / "components" / DOMAIN
    for path in (comp, tests):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)

    excluded_modules = set().union(*(FEATURES[f][0] for f in FEATURES if f not in keep)) | HACS_ONLY
    excluded_tests = (
        set().union(*(FEATURES[f][1] for f in FEATURES if f not in keep)) | REPO_ONLY_TESTS
    )

    for src in sorted(SRC.iterdir()):
        if src.name in excluded_modules or src.name == "translations":
            continue
        text = (
            src.read_text(encoding="utf-8").replace("\r\n", "\n")
            if src.suffix in {".py", ".json", ".yaml"}
            else None
        )
        if src.name == "manifest.json":
            (comp / src.name).write_text(export_manifest(text or "", stage), encoding="utf-8")
        elif src.name == "quality_scale.yaml":
            (comp / src.name).write_text(export_quality_scale(text or "", keep), encoding="utf-8")
        elif src.name == "strings.json":
            (comp / src.name).write_text(export_strings(text or "", keep), encoding="utf-8")
        elif src.name == "icons.json":
            (comp / src.name).write_text(export_icons(text or "", keep), encoding="utf-8")
        elif src.name == "services.yaml":
            services = drop_services(text or "", excluded_services(keep))
            (comp / src.name).write_text(services, encoding="utf-8")
        elif src.suffix == ".py":
            stripped = prune_unused_imports(strip_features(text or "", keep))
            (comp / src.name).write_text(drop_future_annotations(stripped), encoding="utf-8")
        elif src.is_file():
            shutil.copy2(src, comp / src.name)

    if (TESTS / "snapshots").is_dir() and not is_core:
        shutil.copytree(TESTS / "snapshots", tests / "snapshots")
    for src in sorted(TESTS.glob("*.py")):
        if src.name in excluded_tests:
            continue
        (tests / src.name).write_text(
            drop_future_annotations(
                export_test(src.read_text(encoding="utf-8").replace("\r\n", "\n"), keep)
            ),
            encoding="utf-8",
        )

    if is_core:
        add_strict_typing(dest)
        # Core's tests read translations/en.json, which its script generates
        # from strings.json; the export just removed the previous one.
        subprocess.run(
            [sys.executable, "-m", "script.translations", "develop", "--integration", DOMAIN],
            cwd=dest,
            check=False,
            capture_output=True,
        )
    if shutil.which("ruff"):
        # Removing an import left unused by a dropped block is an "unsafe"
        # fix in __init__.py as far as ruff is concerned (it could have been
        # a re-export); here it never is.
        cmd = ["ruff", "check", "--fix", "--unsafe-fixes", "--quiet"]
        if not is_core:
            cmd += ["--select", "F401,I"]
        result = subprocess.run(
            [*cmd, str(comp), str(tests)], check=False, capture_output=True, text=True
        )
        if result.returncode not in (0, 1):
            print(result.stderr, file=sys.stderr)
        if is_core:
            # Core's own formatter settings apply inside the checkout
            subprocess.run(["ruff", "format", "--quiet", str(comp), str(tests)], check=False)
    return comp, tests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--stage", choices=sorted(STAGES), default="minimal")
    parser.add_argument("dest", type=Path, help="core checkout, or any directory")
    args = parser.parse_args(argv)
    comp, tests = export(args.stage, args.dest)
    print(f"exported stage '{args.stage}' to {comp} and {tests}")
    if (args.dest / "homeassistant" / "components").is_dir():
        # Core's snapshot serializer differs from the one the HACS tests use,
        # so the .ambr files are generated inside core rather than copied.
        print(
            "next: python -m pytest tests/components/cellarion --snapshot-update",
            "      python -m script.hassfest --integration-path homeassistant/components/cellarion",
            "      python -m script.gen_requirements_all",
            sep="\n",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
