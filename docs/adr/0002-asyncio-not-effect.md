# Plain asyncio instead of porting Effect

Sandcastle is built end-to-end on [Effect](https://effect.website) — `Effect.gen`,
`Layer`/`Context.Tag` for dependency injection, typed error channels, and
fiber-based cancellation. pysolated deliberately does **not** reproduce an
effect-system layer. Instead it uses plain `asyncio` for concurrency, `Protocol`
classes for the injected seams (agent provider, sandbox provider, display), and
ordinary exceptions for errors. Python has no Effect equivalent, and the
"reimagining" framing means fidelity to the orchestration *behavior* matters, not
to the effect-system machinery.

## Consequences

- **Concurrency:** the timer race (idle / completion / abort) and fan-out use `asyncio` primitives (`asyncio.timeout`, `TaskGroup`, `wait`) rather than `Effect.race`/fibers.
- **Dependency injection:** seams are passed as constructor/argument values typed against `Protocol`s, not resolved from a `Layer` graph. Tests substitute fakes by passing them in.
- **Errors:** failures raise exceptions (a small hierarchy) rather than flowing through a typed error channel. The compiler will not enumerate failure modes for us; docstrings and tests carry that weight.
- A reader cross-referencing the TS source will find no structural correspondence for the Effect plumbing — that absence is intentional, not an omission.
