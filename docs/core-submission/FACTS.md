# Core submission — fact sheet

Facts for writing the pull requests yourself. Nothing here is PR text.
Generated from this repository at the commit noted below; regenerate with
`python tools/export_core.py --stage minimal <core checkout>` after changes.

## What is ready

| Item | Where | State |
| --- | --- | --- |
| Integration branch | `jagduvi1/core`, branch `cellarion` (one commit, author Johan Eklund, based on core `dev` 2026.10.0.dev0) | pushed, not opened as a PR |
| Documentation page draft | `docs/core-submission/cellarion.markdown` (goes to `source/_integrations/cellarion.markdown` in `home-assistant/home-assistant.io`, branch `next`) | draft for your review and edits |
| Brand assets | `docs/core-submission/brands/cellarion/` (goes to `core_integrations/cellarion/` in `home-assistant/brands`) | trimmed, sizes verified |
| Library | `pycellarion==0.1.0` on PyPI, MIT, typed, async | released |

## What the core branch contains (stage "minimal")

- `homeassistant/components/cellarion/`: `__init__.py`, `config_flow.py`, `const.py`, `coordinator.py`, `entity.py`, `sensor.py`, `manifest.json`, `strings.json`, `icons.json`, `quality_scale.yaml`.
- `tests/components/cellarion/`: `conftest.py`, `test_config_flow.py`, `test_init.py`, `test_sensor.py`, `test_snapshots.py`, `snapshots/test_snapshots.ambr` (generated inside core).
- Generated files core requires, produced by core's own scripts: `CODEOWNERS`, `homeassistant/generated/config_flows.py`, `homeassistant/generated/integrations.json`, `mypy.ini`, `.strict-typing`, `requirements_all.txt`.
- Not in this stage, by design (core wants a minimal first PR; each comes as a follow-up): the `consume_bottle` action, the push (SSE) listener and its repair issue, diagnostics. The bundled Lovelace card never goes to core.
- Manifest: `integration_type: service`, `iot_class: cloud_polling`, `quality_scale: bronze`, `requirements: ["pycellarion==0.1.0"]`, codeowner `@jagduvi1`, documentation URL `https://www.home-assistant.io/integrations/cellarion`.
- `quality_scale.yaml`: Bronze rules all `done` or `exempt` with a reason (no actions, no triggers/conditions, no discovery, no physical devices); rules of the excluded features are `todo`.

## Verification, run inside a core dev checkout (2026-09-12)

| Command | Result |
| --- | --- |
| `python -m script.hassfest --integration-path homeassistant/components/cellarion` | Integrations: 1, Invalid: 0 |
| `python -m script.hassfest --action generate -p codeowners,config_flow,mypy_config` | derived files updated, 0 invalid |
| `python -m script.gen_requirements_all` | `requirements_all.txt` updated |
| `python -m script.translations develop --integration cellarion` | en.json generated (not committed; core ignores it) |
| `ruff check` / `ruff format --check` on the integration and its tests, core config | clean |
| `python -m pytest tests/components/cellarion` | 49 passed, 42 snapshots |
| `mypy homeassistant/components/cellarion` (core config, strict) | no errors in the integration |

Also, in this repository: 83 tests, 99 % coverage, mypy strict, ruff; live against Home Assistant 2026.7.1 and Cellarion 1.220.

## Checklist facts (core PR template)

- Type of change: new integration.
- Breaking change: none.
- Tests added: config flow (every branch), setup and reauth paths, sensors, snapshots of all 21 entities.
- Formatted with ruff; `python3 -m script.hassfest` and `python3 -m script.gen_requirements_all` were run and their output is committed.
- Dependency: `pycellarion==0.1.0`, first release, changelog is the release notes at https://github.com/jagduvi1/pycellarion/releases/tag/v0.1.0.
- The library's own contract tests run nightly against a live server (once the token secret is set).
- Links to fill in: documentation PR, brands PR (both opened by you).

## Steps for Monday, in order

1. **brands**: fork `home-assistant/brands`, add `core_integrations/cellarion/icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png` from `docs/core-submission/brands/cellarion/`, open the PR (their template asks only for the integration domain and whether it is core).
2. **docs**: fork `home-assistant/home-assistant.io`, add `source/_integrations/cellarion.markdown` on a branch off `next`, using the draft after your review. Set `ha_release` to the release the reviewers target (they will tell you; leave the draft value otherwise).
3. **core**: open the PR from `jagduvi1/core:cellarion` against `home-assistant/core:dev`. Use the template in full; link the docs and brands PRs in *Additional information*. Do not amend or squash the branch after opening; push new commits.
4. After opening: reply to review comments yourself. When a change is requested, tell me the request; I make it in this repository, re-run the export, and you push the new commit.

## Regenerating the core branch after changes here

```
python tools/export_core.py --stage minimal C:\tmp\core
cd C:\tmp\core
python -m script.translations develop --integration cellarion
python -m pytest tests/components/cellarion --snapshot-update
python -m script.hassfest --integration-path homeassistant/components/cellarion
python -m script.hassfest --action generate -p codeowners,config_flow,mypy_config
python -m script.gen_requirements_all
python -m pytest tests/components/cellarion
```

The dev container `ha-core-dev` (image python:3.14, core mounted at `/core`) has these dependencies installed; `docker exec ha-core-dev sh -c "cd /core && …"` runs them.
