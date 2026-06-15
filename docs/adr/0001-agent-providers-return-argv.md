# Agent providers return an argv list, not a shell command string

An **agent provider** builds the **agent** invocation as an argv list (e.g.
`["claude", "--print", "--output-format", "stream-json", ...]`) plus optional
stdin, *not* a shell command string. The TypeScript original (Sandcastle) returns
a shell string because every backend runs through `sandbox.exec(commandString)`,
including Docker (`sh -c "..."`). pysolated's first sandbox is no-sandbox (a host
subprocess), where an argv list is safe by construction — no shell, no
`shlex.quote` escaping footguns. Sandbox providers that genuinely need a shell
(e.g. `docker exec sh -c`) wrap the argv themselves rather than pushing escaping
onto every agent provider.

## Consequences

- The seam between **agent providers** and **sandbox providers** is `(argv: list[str], stdin: str | None)`. A sandbox provider receives argv and decides how to run it.
- Diverges deliberately from Sandcastle; cross-referencing the TS source, the command-building code will not match line-for-line.
- If a future sandbox backend can *only* accept a shell string, it owns the argv→string conversion (e.g. via `shlex.join`), keeping escaping in one place per backend instead of in every agent provider.
