# The scaffolded driver uses `{{KEY}}` substitution and forwards `.env` generically

Two coupled choices about the **driver** (`main.py`) `init` scaffolds, both
diverging from the Sandcastle reference.

**`{{KEY}}` substitution, not regex-rewrite of a runnable template.** Sandcastle
ships `blank/main.mts` as a valid, runnable file with real `claudeCode(...)` +
`docker()` and regex-rewrites those identifiers at scaffold time, keeping the
template lint/run-testable as-is. pysolated instead carries
`{{AGENT_IMPORT}}`/`{{AGENT_FACTORY}}`/`{{MODEL}}`/`{{SANDBOX}}` placeholders filled
by the same `substitute_arguments` engine that composes the Containerfile — one
uniform mechanism across every scaffolded file. The cost, accepted: the driver
template is **not** standalone-runnable, so tests validate the *scaffolded output*
(substitute → assert it imports/parses), not the template file.

**Credentials forwarded generically from `.env`.** pysolated's sandbox providers
deliberately do not forward host environment ("no `os.environ` forward"), so —
unlike Sandcastle, whose providers forward credentials internally — the scaffolded
driver must pass secrets explicitly. It does so generically: load `.pysolated/.env`
and pass `env=dotenv_values(".pysolated/.env")`, rather than hard-coding per-agent
key names. This keeps the driver **agent-agnostic** (no env-key rewriting when the
agent changes); the per-agent `.env.example` documents *which* keys to set.

## Consequences

- One substitution mechanism for the whole **config directory** (driver,
  Containerfile, prompt), at the price of a non-runnable driver template.
- Switching agents in the driver is an import + factory + model change only — the
  `env=` line is unchanged because it forwards whatever `.env` holds.
- Generic forwarding drops the dogfood driver's per-key fail-fast (`_require_env`);
  a missing credential surfaces as an agent-side auth error instead. Acceptable for
  a scaffold the user then edits; fail-fast can be reintroduced by hand.
