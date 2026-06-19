# Init, scaffolding & image lifecycle

Roadmap items 5 (issue-tracker subsystem) and 7 (`init` scaffolding — composes items
2–6, lands last). Image lifecycle for Podman already shipped (see
[completed-features.md](./completed-features.md)). See [features.md](./features.md)
for the index.

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
