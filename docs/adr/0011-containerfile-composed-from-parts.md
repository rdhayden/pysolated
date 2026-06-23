# `init`'s Containerfile is composed from agent-install × sandbox-UID parts

`init` builds the scaffolded Containerfile by **composing** a per-sandbox base
template with per-agent install snippets, rather than shipping one full
Containerfile per agent the way Sandcastle does. Sandcastle bakes the docker-style
`AGENT_UID`/`AGENT_GID` build-arg alignment into each per-agent Dockerfile and
reuses the same file for podman (only the filename differs). pysolated cannot:
its podman path uses rootless `keep-id`, chosen specifically to **avoid** build-arg
UID alignment, while its docker path **requires** that alignment (ADR 0005). So the
Containerfile genuinely differs on **both** axes — agent determines the CLI-install
lines, sandbox determines the UID-handling block — and the install also interleaves
with the `USER` directive (codex installs as root *before* `USER`; claude-code
`curl`s *after*).

The chosen shape: a per-sandbox base (`podman` → `keep-id`, `USER 1000`, no
build-arg; `docker` → `ARG AGENT_UID/GID` + numeric `USER`) carrying two slots,
`{{ROOT_INSTALL}}` (before `USER`) and `{{USER_INSTALL}}` (after `USER`); per-agent
snippets fill the appropriate slot. Assembly uses the existing `{{KEY}}`
substitution engine.

This composition does not violate the "templates carry no shared code" principle
(Sandcastle ADR 0009): that rule governs the verbatim-copied **scaffold template**
directories (`blank/main.py`, `prompt.md`), not the Containerfile — which in
Sandcastle is itself already a composed/substituted artifact (per-agent string +
`{{ISSUE_TRACKER_TOOLS}}`).

## Considered Options

1. **Full standalone Containerfile per (agent, sandbox) combo** — rejected. Matches
   Sandcastle's "duplication welcome" ethos and is verifiable whole, but grows
   multiplicatively (agents × sandboxes → 18+ near-duplicates as deferred agents and
   a third provider land), duplicating the keep-id-vs-build-arg block across every
   agent.
2. **Compose base × install parts via `{{KEY}}`** (chosen) — scales additively
   (agents + sandboxes), keeps the keep-id/build-arg divergence written once per
   sandbox, and reuses machinery pysolated already has.

## Consequences

- Adding an agent is one install snippet; adding a sandbox is one base template.
- The install/`USER` interleave is encoded by *which* slot a snippet fills, not by
  hand-placing it in a full file.
- The two UID contracts (podman keep-id, docker build-arg) each live in exactly one
  base template, where they can be read against ADR 0005.
