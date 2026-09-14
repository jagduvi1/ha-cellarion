# Core submission — step by step

A walkthrough for opening the three pull requests yourself.

**Order:** documentation, then core, then brands. All three templates ask for
links to the others, so one cross-link always has to be added afterwards;
this order keeps that to a single edit (the core link added back into the
documentation pull request). Brands goes last because its template asks for
both other links, and because it only accepts icons for integrations that
are going into core.

Budget about an hour for the three, plus whatever you spend writing.

Facts to write from are in `FACTS.md`. The texts you type into GitHub are
yours; the Home Assistant AI policy expects you to explain the change in
your own words and to answer reviewers yourself.

Before you start:

- Sign in to GitHub as `jagduvi1` with two-factor authentication enabled.
- Have `C:\tmp\core` (the core checkout with the `cellarion` branch) and this
  repository open; the assets and the docs draft live under
  `docs/core-submission/`.
- Read the pull request templates once; each repository has its own and
  reviewers close pull requests that delete template sections.

## 1. Documentation page

Repository: https://github.com/home-assistant/home-assistant.io

1. Fork it. Documentation for unreleased integrations goes on the `next`
   branch, so create your branch from `next`:

   ```
   git clone https://github.com/jagduvi1/home-assistant.io
   cd home-assistant.io
   git checkout next
   git checkout -b cellarion
   ```

2. Review `docs/core-submission/cellarion.markdown` in this repository. It is
   a draft in their page format; read every sentence and change anything you
   would say differently. Two fields need your decision:
   - `ha_release`: the Home Assistant version the integration will ship in.
     Leave `'2026.11'`; reviewers correct it to the actual release.
   - `ha_quality_scale`: `bronze`, matching the core manifest of the first
     pull request.
3. Copy it to `source/_integrations/cellarion.markdown`, commit, push:

   ```
   copy C:\VSCODE\ha-cellarion\docs\core-submission\cellarion.markdown source\_integrations\cellarion.markdown
   git add source/_integrations/cellarion.markdown
   git commit -m "Add Cellarion integration documentation"
   git push -u origin cellarion
   ```

4. Open a pull request against `home-assistant/home-assistant.io` **`next`**
   (not `current`). Their template asks what the pull request is about and
   for a link to the core pull request; that one does not exist yet, so add
   it by editing the description once step 2 is done.
5. Note the pull request URL.

The docs repository has a preview build (Netlify) on each pull request; open
it to see the rendered page.

## 2. Core

Repository: https://github.com/home-assistant/core

Your fork `jagduvi1/core` already has the branch `cellarion` with one commit.
Nothing needs to be built or pushed for the first version.

1. Open https://github.com/jagduvi1/core/tree/cellarion and click
   **Contribute** → **Open pull request**. Base repository
   `home-assistant/core`, base branch **`dev`**, head `jagduvi1:cellarion`.
2. The template loads automatically. Fill it in completely and keep every
   section and checkbox, ticking only what is true:
   - *Breaking change*: remove that section only, as its comment instructs.
   - *Proposed change*: what Cellarion is, what this first pull request adds
     (config flow with token or email+password, one service device, the
     sensors), and what is deliberately left for follow-ups (the consume
     action, push updates, diagnostics). `FACTS.md` has the list.
   - *Type of change*: New integration.
   - *Additional information*: the documentation pull request link. The
     brands one does not exist yet; add it to the description after step 3.
   - *Checklist*: the facts for each item are in `FACTS.md`; only tick what
     you have verified.
3. Create the pull request. Their bots add labels and run the full CI within
   about half an hour: hassfest, ruff, mypy, pytest for the component, and
   translations checks. Everything listed in `FACTS.md` was already run
   locally, so expect green.
4. After the brands pull request exists, edit this description to add its
   link, and edit the documentation pull request to add the core link.

## 3. Brands (icon and logo)

Repository: https://github.com/home-assistant/brands

1. Click **Fork** (top right) and create the fork under your account.
2. In your fork, open the folder `core_integrations`, then **Add file** →
   **Upload files**. Drag in the four files from
   `docs/core-submission/brands/cellarion/`: `icon.png`, `icon@2x.png`,
   `logo.png`, `logo@2x.png`. GitHub's uploader cannot create a folder, so
   type the target path as `core_integrations/cellarion/` in the file name
   field of the first upload, or do it from a clone:

   ```
   git clone https://github.com/jagduvi1/brands
   cd brands
   git checkout -b cellarion
   mkdir core_integrations/cellarion
   copy C:\VSCODE\ha-cellarion\docs\core-submission\brands\cellarion\*.png core_integrations\cellarion\
   git add core_integrations/cellarion
   git commit -m "Add Cellarion"
   git push -u origin cellarion
   ```

3. Open a pull request from your `cellarion` branch to `home-assistant/brands`
   `master`. In *Type of change*, tick exactly one box:
   **"Add a new logo or icon for a new core integration"**. That is the right
   box because the integration is being submitted to core; the repository no
   longer accepts icons for custom (HACS-only) integrations at all, which is
   why this step comes after the core pull request exists.
   In *Additional information*, fill in "Link to code base pull request" (the
   core one) and "Link to documentation pull request".
   Image facts for the description: icon 256×256 and 512×512, logo 322×256
   and 488×388, all PNG, transparent, trimmed.
4. Note the pull request URL.

Their checks run automatically. A failure is almost always an image-size or
transparency issue; tell me the message and I regenerate the file.

## 4. While it is under review

- Reviews arrive as comments on the diff. Reply yourself; short and factual.
  If you need the reasoning behind something, ask me and put the answer in
  your words.
- For a requested change: tell me what they asked. I change it in this
  repository, regenerate the branch, run core's checks, and hand you a new
  commit on `jagduvi1/core:cellarion`. You push it (never amend or squash a
  pushed commit; reviewers follow the history).
- Their review bot may leave automated comments; treat them like any other
  and say briefly if one is wrong.
- Expect weeks between reviews. Do not ping; do not merge `dev` into the
  branch unless a maintainer asks you to resolve a conflict.

## 5. After the merge

- The integration ships in the next monthly Home Assistant release. The HACS
  version keeps working; users switch by uninstalling the HACS one, and their
  settings carry over.
- Follow-up pull requests, one feature each: the consume action, push
  updates with the repair issue, diagnostics, then raising the quality-scale
  tier. Each is an export at a later stage from this repository.
- The card gets its own repository and a HACS "dashboard" submission, since
  core users cannot get it from the integration.

## If something goes wrong

- A red check on the core pull request: open it, copy the failing lines to
  me. Most are one-line fixes here plus a re-export.
- Conflict with `dev` (another integration touched a generated file): I
  rebase the export onto the current `dev`; you force-push only if a
  maintainer explicitly asks, otherwise push the rebased branch as a new
  branch and re-open.
- A reviewer asks for a structural change (naming, entity layout): that is
  applied here first so the HACS version stays identical in shape.

## Background reading

Worth skimming before Monday; the first two are what reviewers hold a new
integration to.

| Page | Why |
| --- | --- |
| [Checklist for creating a component](https://developers.home-assistant.io/docs/creating_component_code_review) | The requirements list for a new integration: API code in a PyPI library, pinned requirement in the manifest, schema validation, minimal first pull request, tests. |
| [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/) | Bronze is the minimum for a new integration, and the tier is granted by the core team on review of `quality_scale.yaml`, not self-declared. |
| [Review process](https://developers.home-assistant.io/docs/review-process) | How reviews run, what a "perfect pull request" is (one subject, small, tested), and why a reviewed pull request turns into a draft until you mark it ready. |
| [Development checklist](https://developers.home-assistant.io/docs/development_checklist) | The general rules every pull request is held to. |
| [Development guidelines](https://developers.home-assistant.io/docs/development_guidelines) | Style conventions their linters enforce. |
| [AI policy](https://developers.home-assistant.io/docs/ai_policy) | The rules for AI-assisted contributions: you explain the change and answer reviewers yourself. |
| [Architecture decision records](https://github.com/home-assistant/architecture/tree/master/adr) | Decisions a new integration must not contradict. |
| [Brands repository README](https://github.com/home-assistant/brands) | Image rules; also states that icons for custom integrations are no longer accepted. |

Note on wording: in Home Assistant an **add-on** is a separate thing (a
container managed by the Supervisor, submitted to a different repository).
What we are submitting is an **integration**, so search the developer docs
for that word.
