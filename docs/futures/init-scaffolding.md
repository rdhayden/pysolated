# Init, scaffolding & image lifecycle

Roadmap items 5 (issue-tracker subsystem) and 7 (`init` scaffolding — composes items
2–6, lands last). Image lifecycle for Podman already shipped (see
[completed-features.md](./completed-features.md)). See [features.md](./features.md)
for the index.

**Item 5 folded in here (decided 2026-06-19).** The issue-tracker subsystem has no
honest standalone tracer bullet before init: pysolated already has `{{KEY}}`
substitution, caller `prompt_args`, and `` !`command` `` expansion, so the
`{{LIST_TASKS_COMMAND}}` string alone needs no new subsystem. The registry only
earns its keep when it bundles each tracker's **command**, its **tool-install
Dockerfile snippet**, and its **`.env.example` lines** and writes all three into the
scaffold consistently — that bundling *is* the init slice. So the issue-tracker
registry is built as part of init, not before it.

- **`init` command** — scaffold the `.sandcastle/` **config directory** in a repo
  (Dockerfile, `prompt.md`, `config.json`, `.env`/`.env.example`).
- **Templates** — Dockerfile/prompt scaffolds with **template arguments** and
  substitution; templates carry no shared code (ADR 0009).
- **`config.json`** — file-based config (`agent`, `maxIterations`, …).
- **Issue tracker selection** — choose a task source during init (GitHub Issues,
  Beads) so the agent can select **tasks** to work on.
- **Triage labels** — canonical label vocabulary and `--create-label`.
- **Image lifecycle commands** — provider-namespaced `build-image` and
  `remove-image` (e.g. `pysolated docker build-image`), with `--build-image` and
  `--install-template-deps` options at init.
- **Package-manager detection** during scaffolding.
