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

---

## Committed slice scope (tracer bullet: `init` wizard scaffolds a 2×2 container project)

Settled in a grilling session (2026-06-23), after folding items 5 and 6 in (see
[features.md](./features.md)). The bullet proves *`init` can scaffold a runnable
**config directory** from the project's agent + sandbox choices* — the scaffold
seam plus the two registries that compose the Containerfile. Issue trackers,
extra templates, package-manager detection, and image building are fast-follows.

- **Interactive wizard, tri-state with flags.** `pysolated init` prompts for the
  choices it isn't given. Each choice has an optional flag: **flag present** →
  use it (validated up front, exit 2 on unknown value); **flag absent + TTY** →
  prompt; **flag absent + no TTY** → fail fast naming the missing flag. This keeps
  the wizard *and* a fully headless/scriptable path (tests drive it with all
  flags). A departure from pysolated's headless-only CLI precedent — see ADR.
  Prompts use **rich** (already a dependency); numbered/typed choice, no new
  arrow-select dependency.
- **Prompted axes: agent × sandbox (the 2×2).** `--agent` over `claude-code` +
  `codex`; `--sandbox` over `podman` + `docker`. `--model` defaults to the chosen
  agent's default. `--template` is fixed to `blank` (the only one that exists;
  `simple-loop` needs lifecycle hooks + an issue tracker). Issue-tracker,
  build-image, install-deps, and create-label prompts are **not** built this slice.
- **Pure `scaffold(repo_dir, options)` core + thin wizard shell**, mirroring
  Sandcastle's `scaffold()` vs `cli.ts` split. The wizard gathers options then
  calls `scaffold()`; tests exercise `scaffold()` directly and the wizard via the
  all-flags path. Fails if `.pysolated/` already exists.
- **Scaffolded files:** `main.py` (the **driver**), `prompt.md` (a skeleton
  **prompt template**, no issue-tracker placeholder this slice), `Containerfile`
  (podman) / `Dockerfile` (docker), `.gitignore` (ignores `.env`), and
  `.env.example` (the chosen agent's key block). No `config.json` — config lives
  in the **driver**'s `run()` call, matching Sandcastle.
- **Containerfile composed from agent-install × sandbox-UID parts** (not Sandcastle's
  full per-agent template). pysolated's podman path uses `keep-id` (no UID build-arg)
  while docker requires the `AGENT_UID`/`AGENT_GID` build-arg align (ADR 0005), so the
  Containerfile genuinely differs on *both* axes. A per-sandbox base template carries
  `{{ROOT_INSTALL}}` (before `USER`) and `{{USER_INSTALL}}` (after `USER`) slots;
  per-agent snippets fill them (codex → root `npm i -g`; claude-code → user `curl`).
  Assembled via the existing `{{KEY}}` substitution engine. See ADR.
- **`main.py` via `{{KEY}}` substitution**, uniform with the Containerfile:
  `{{AGENT_IMPORT}}`/`{{AGENT_FACTORY}}`/`{{MODEL}}`/`{{SANDBOX}}` placeholders, filled
  by `substitute_arguments`. The template is therefore **not** standalone-runnable;
  tests validate the *scaffolded output* (substitute → assert it imports/parses), not
  the template file. See ADR.
- **Credentials forwarded generically.** pysolated providers don't forward host env
  ("no `os.environ` forward"), so the scaffolded `main.py` loads `.pysolated/.env`
  and passes `env=dotenv_values(...)` — agent-agnostic, so no per-agent env-key
  rewriting. The per-agent `.env.example` documents *which* keys to set.
- **Image building stays a next step, not an init action.** init prints next-steps
  pointing at the already-shipped `pysolated <provider> build-image`; it does not
  build (or offer to build) the image this slice.

## Deferred out of the slice

- **Issue-tracker registry** (`{{LIST_TASKS_COMMAND}}` + tool-install snippet +
  `.env.example` lines + the `custom` setup doc) — the folded-in item 5. The `blank`
  prompt skeleton ships with no tracker placeholder; the Containerfile has no
  tracker-tools slot yet. Lands as the fast-follow that also brings `simple-loop`.
- **Richer scaffold templates** (`simple-loop`, `sequential-reviewer`, the planner
  templates) — depend on lifecycle hooks ([lifecycle-hooks.md](./lifecycle-hooks.md)),
  `copy_to_worktree` wiring in the **driver**, and the issue-tracker registry.
- **Other agents / providers in the matrix** (Copilot, Cursor, OpenCode, pi;
  a third sandbox) — the compose registries grow additively as those land.
- **Package-manager detection + `--install-template-deps`** — only the planner
  templates need host deps; nothing to install for `blank`.
- **`--build-image` / `--create-label` / triage labels** — image building and the
  GitHub-label vocabulary are their own follow-ups.
- **Interactive arrow-select UX** — rich numbered choice is the slice's prompt
  surface; a `questionary`-style dependency is deferred unless richer prompts are
  wanted.
