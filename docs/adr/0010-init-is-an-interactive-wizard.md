# `init` is an interactive wizard, made headless-safe by a tri-state pattern

`pysolated init` is an **interactive wizard** — it prompts for the choices it
isn't given (agent, sandbox, …). Every other pysolated CLI surface to date is
pure `typer` flags with no prompting (`run_command` validates and exits 2; there
is no `input()` anywhere), so a reader will reasonably expect `init` to be
flags-only too. It isn't, deliberately: scaffolding is a first-run, get-started
moment where a guided wizard is the better UX and matches the Sandcastle
reference.

The departure is made safe — scriptable and testable — by a **tri-state**
resolution applied per choice:

- **flag present** → use it (validated up front, exit 2 on an unknown value);
- **flag absent + TTY** → prompt interactively;
- **flag absent + no TTY** → fail fast, naming the missing flag
  (`--agent is required in non-interactive mode`).

So passing every flag is a fully headless path (what CI and the test suite use),
and the pure `scaffold(repo_dir, options)` core is unit-tested directly with the
wizard as a thin gap-filler on top — mirroring Sandcastle's `scaffold()` vs
`cli.ts` split. Prompts are rendered with **rich** (already a dependency) as
numbered/typed choices; no arrow-select dependency is added.

## Consequences

- pysolated's "CLI == `run()`, no interactivity" discipline gains one documented
  exception, scoped to `init`. The headless path is preserved, not abandoned.
- Tests target `scaffold()` and the all-flags path; the interactive prompts are a
  thin shell and are not the unit-test surface.
- A future `questionary`-style arrow-select UX can replace the rich prompts
  without touching `scaffold()` or the flag contract.
