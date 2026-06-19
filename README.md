# pysolated

A Python toolkit that orchestrates AI coding agents inside sandboxes to enable automated Ralph like loops in sequence or in parralel for example to automate the completion of tickets on a backlog marked as not needing humans in the loop, or to spin up and down isolated agents to check code in continuous integration loops. 

A Python
reimagining of [Sandcastle](https://github.com/ai-hero-dev/sandcastle); see
[`CONTEXT.md`](CONTEXT.md) for the vocabulary and [`docs/`](docs) for the PRD and
ADRs.

## Why pysolated

There are alternatives that may be better in specific circumstances. To understand when pysolated is appropriate consider when not to use platform specific tools like bubblewrap and seatbelt or AI agent specific tools like Claude's /sandbox.

If you are using multiple different coding agents such as Claude and Codex and need to run on multiple different platforms such as Linux, MacOS or cloud hosted sandbox providers, and you want a consistent process in all cases, the pysolated will do the trick.

Why not use Sandcastle? If you are comfortable with Node or need to programatically instantiate agent loops from Typescript/Javascript code then Sancastle is the better choice. If you are more comfortable with Python or need to orchestrate agent loops from Python code, pysolate is functionally equivalent at the time of publication but built in Python

## Status

v1 iteration lifecycle: one agent provider (`claude_code`), driven through
`run()` and the `pysolated run` CLI. The run loops up to `max_iterations`,
stops early on a **completion signal**, enforces an **idle timeout** and a
post-signal **completion grace window**, reports the **commits** the agent
made, supports **abort** via an `asyncio.Event` (or Ctrl-C from the CLI), and
optionally extracts a schema-validated **structured output** payload from the
agent's prose. Three sandbox providers ship: `no_sandbox` (no isolation —
host subprocess), `podman` (rootless container with a same-path repo bind
mount — real isolation, see below), and `docker` (Podman's sibling for
Docker-only hosts; mirrors Podman everywhere the two engines agree).

## Library

```python
import asyncio
from pysolated import run, claude_code, no_sandbox

async def main():
    result = await run(
        agent=claude_code("claude-opus-4-7"),
        sandbox=no_sandbox(),
        prompt="say hi",
    )
    print(result.stdout)
    print(result.branch, result.usage)

asyncio.run(main())
```

`run()` returns a frozen `RunResult` (`iterations`, `stdout`, `branch`, `usage`,
`completion_signal`, `commits`, `output`, `log_file_path`). The inline prompt
is sent to the agent verbatim — no substitution or expansion. Loop and timer
behavior is configurable:

```python
result = await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=no_sandbox(),
    prompt="Refactor X. Emit DONE when finished.",
    max_iterations=5,                  # default 1
    completion_signal="DONE",          # str or list[str]; default <promise>COMPLETE</promise>
    idle_timeout_seconds=600,          # fail if no output for this long
    completion_timeout_seconds=60,     # grace window after the signal is seen
)
```

### Sandbox providers

Three sandbox providers ship today; they all implement the same factory +
live-handle seam ([ADR 0003](docs/adr/0003-sandbox-providers-are-factories.md)),
so swapping between them is one constructor change in your `run()` call.

**`no_sandbox()`** — no isolation. The agent is a host subprocess that touches
your real working directory. Right for trusted runs in throwaway worktrees;
wrong for anything you wouldn't paste into your shell.

**`podman(image=…)`** — a long-lived rootless container as the isolation
boundary. `run()` starts the container once, runs every command in it via
`podman exec`, and removes it (`podman rm -f`) when the run exits — success,
failure, idle timeout, or Ctrl-C.

```python
from pysolated import run, claude_code, podman

await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=podman(
        image="pysolated-agent:latest",
        env={"ANTHROPIC_API_KEY": "sk-..."},  # MUST pass credentials explicitly
    ),
    prompt="say hi",
)
```

Key behaviours:

- **Same-path repo bind mount** ([ADR 0004](docs/adr/0004-same-path-bind-mount.md)).
  The host repo is mounted at the *identical* path inside the container with
  the default `:z` SELinux label. Combined with `--userns=keep-id:uid=N,gid=N`
  + `--user N:N`, files appear owned by the in-container user and the
  orchestrator's host `cwd` passes through `podman exec -w` unchanged — no
  chown step, no git `safe.directory` configuration.
- **Credentials are explicit.** Unlike `no_sandbox`, the container does **not**
  inherit your host shell's environment. Anything the agent needs
  (`ANTHROPIC_API_KEY`, `GH_TOKEN`, …) goes through `env=` or a mounted file.
  This is the deliberate isolation surface, not a configuration miss.
- **`exec` is argv passthrough** ([ADR 0001](docs/adr/0001-agent-providers-return-argv.md)).
  No `sh -c` wrapper inside the container; the argv built by the agent
  provider runs as-is.
- **Cancellation kills the `podman exec` client.** `podman rm -f` from `close()`
  is the true kill switch for any in-container process the client left behind;
  an `atexit` backstop catches the rare abnormal-exit case.

The **image contract** the provider relies on:

- A user/group exists at `container_uid:container_gid` (default `1000:1000`),
  so keep-id maps host ↔ container ownership without a chown step.
- `git` and the agent CLI (`claude` for `claude_code`) are on `PATH`.
- The user has a writable `HOME`. The provider injects `HOME=/home/agent` by
  default; override via `env={"HOME": "..."}`.

A missing image is caught up front: `create()` runs `podman image inspect
<image>` as a preflight and raises `PodmanImageNotFoundError` with a clear
message naming `pysolated podman build-image` so the fix is one command away.

Knobs on `podman(...)`:

| Option | Default | Description |
| --- | --- | --- |
| `image` | `pysolated:<sanitized-host-dirname>` | The image to run. Same name `pysolated podman build-image` produces, so `podman()` and the CLI line up out of the box. |
| `env` | `{}` | `-e` pairs at `podman run`. Provider env wins over the `HOME=/home/agent` default. |
| `userns` | `"keep-id"` | `--userns=keep-id:uid=N,gid=N` + paired `--user N:N`. Pass `None` for raw Podman defaults. |
| `container_uid` / `container_gid` | `1000` / `1000` | uid:gid for `--user` and keep-id. |
| `selinux_label` | `"z"` | Mount label (`z` shared, `Z` private). Pass `None` for no label. |
| `mounts` | `[]` | Extra `Mount`s appended after the repo bind mount. See below. |
| `cpus` | `None` | `--cpus N` (fractional ok, e.g. `1.5`). Omitted when `None`. |

**Custom mounts.** Each `Mount(host_path, sandbox_path, readonly=False)` is
appended as a `-v host:sandbox[:opts]` entry on `podman run`. `host_path` is
tilde-expanded against the host `$HOME` and, if relative, resolved against
the host cwd; the resolved path **must exist** at `create()` time or the
provider fails fast with `Mount host_path does not exist`. `sandbox_path`
must be **absolute** — there is no sandbox-side `~` expansion. The `ro` and
SELinux-label options are composed through the same formatter as the repo
mount, so `Mount(..., readonly=True)` with `selinux_label="z"` renders as
`…:ro,z`.

> Caveat: the sandbox-side **parent directory must already exist in the
> image**. Mounting a single file whose parent dir is absent will fail —
> auto-creating parent dirs for file mounts is tracked in
> [`docs/futures/features.md`](docs/futures/features.md).

```python
from pysolated import podman, Mount

sandbox = podman(
    image="pysolated-agent:latest",
    env={"ANTHROPIC_API_KEY": "sk-..."},
    mounts=[
        Mount(host_path="~/.config/gh", sandbox_path="/home/agent/.config/gh"),
        Mount(host_path="./data", sandbox_path="/data", readonly=True),
    ],
    cpus=1.5,
)
```

**Image lifecycle.** `pysolated podman build-image` runs `podman build -f
Containerfile -t pysolated:<sanitized-host-dirname> .` against the host cwd;
`--file <path>` overrides the Containerfile and `--image <tag>` overrides the
derived tag. `pysolated podman remove-image` is the matching `podman rmi`.

Memory limits and the other `podman run` knobs (`--network`, `--group-add`,
`--device`) are tracked in
[`docs/futures/features.md`](docs/futures/features.md) and the committed
roadmap there.

**`docker(image=…)`** — a long-lived Docker container, sibling to `podman`.
Mirrors the Podman provider everywhere the two engines agree: same-path repo
bind mount + `:z` label, argv-passthrough `exec` (no `sh -c`), `docker rm -f`
on close with the same idempotent + `atexit`-backed teardown, `HOME=/home/agent`
plus provider `env` (provider wins, **no host `os.environ` forward**), and the
same `mounts` / `cpus` knobs through the shared volume-spec builder.

The defining divergence is **UID handling**, because Docker has no
`--userns=keep-id` ([ADR 0005](docs/adr/0005-docker-uid-alignment-via-build-arg.md)):

- `container_uid` / `container_gid` default to the **host** UID/GID (resolved
  in the `docker()` factory; falls back to `1000` where `os.getuid` is
  unavailable). Host-UID alignment is the whole point — a `Docker(...)`
  provider isn't reproducible across hosts the way `Podman(container_uid=1000)`
  is.
- `--user N:N` is **always** emitted on `docker run` — there is no `userns`
  field and no opt-out, because alignment is a single coupled mechanism and
  disabling it only reintroduces the silent `EACCES` it prevents.

```python
from pysolated import run, claude_code, docker

await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=docker(
        image="pysolated-agent:latest",
        env={"ANTHROPIC_API_KEY": "sk-..."},  # MUST pass credentials explicitly
    ),
    prompt="say hi",
)
```

The **image contract is heavier than Podman's** because `--user`, the
forthcoming `pysolated docker build-image` build-args, and the (forthcoming)
pre-flight all depend on it. The user-provided Containerfile must:

```dockerfile
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN groupmod -o -g $AGENT_GID <user> && \
    usermod -o -u $AGENT_UID -g $AGENT_GID -d /home/agent -m -l agent <user>
USER ${AGENT_UID}:${AGENT_GID}
```

The `-o` flag lets alignment succeed when the host UID/GID collides with one
already in the base image; the numeric `USER` is what makes the (forthcoming)
pre-flight `{{.Config.User}}` check parseable. `git` + the agent CLI on
`PATH`, writable `HOME=/home/agent`.

A missing image is caught up front: `create()` runs `docker image inspect
<image>` as a preflight and raises `DockerImageNotFoundError` naming
`pysolated docker build-image`. A failed `docker run` raises
`DockerLaunchError`.

### Prompt templates

A **prompt template** lives in a file and is resolved before the agent runs.
Pass it via `prompt_file=` (and `prompt_args=` for the values). `prompt` and
`prompt_file` are mutually exclusive; exactly one is required.

```python
# prompts/refactor.txt
#   Refactor the {{area}} module on branch {{branch}}.
#   Most recent commit: !`git log -1 --oneline`
#   Emit DONE when finished.

result = await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=no_sandbox(),
    prompt_file="prompts/refactor.txt",
    prompt_args={"area": "auth"},      # `branch` is built-in — don't pass it
    completion_signal="DONE",
)
```

Two stages run in order, both via the **sandbox seam** (so a Docker sandbox
later will resolve prompts inside the container too):

1. **Argument substitution** — every `{{KEY}}` is replaced. `prompt_args`
   overlay built-in arguments; for v1 the only built-in is `branch` (the
   current git branch, injected automatically). Validation is strict:
   - Passing a `prompt_args` key that collides with a built-in raises
     `PromptArgumentError` so you can't silently shadow framework values.
   - A `{{KEY}}` with no matching argument raises `PromptArgumentError` so
     a typo never reaches the agent as the literal `{{KEY}}` token.
2. **Prompt expansion** — every `` !`command` `` is replaced by the
   command's stdout, evaluated via the sandbox seam. A non-zero exit raises
   `PromptExpansionError` immediately, so a broken command never produces a
   silently-truncated prompt.

**Inline prompts skip both stages.** A literal `{{KEY}}` or `` !`cmd` `` in
an inline string is sent through verbatim — nothing is substituted or
executed. Passing `prompt_args` alongside an inline `prompt` raises
`PromptArgumentError` up front so you find out immediately that the args
would be ignored.

### File logging and run naming

By default `run()` narrates to the terminal via `TerminalDisplay`. For an
unattended run, redirect progress and agent output to a log file by passing
`log_file=` — `run()` constructs a `FileDisplay` that writes line-buffered, so
`tail -f` shows live progress while the run is in flight:

```python
result = await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=no_sandbox(),
    prompt="Refactor X. Emit DONE when finished.",
    name="nightly-refactor",            # appears in status lines + log header
    log_file="/tmp/pysolated-nightly.log",
)
print(result.log_file_path)             # /tmp/pysolated-nightly.log
```

- `log_file=` and `display=` are mutually exclusive — pass one or the other.
- `RunResult.log_file_path` is the resolved path of the log file when
  `log_file=` was used; `None` otherwise.
- `name=` is optional. When set it prefixes every status line in both
  `TerminalDisplay` and `FileDisplay` (`[nightly-refactor] Iteration 1/3`) and
  appears in the log file's first line so concurrent runs are distinguishable
  at a glance.
- Both displays satisfy the same `Display` protocol — the orchestrator is
  unchanged; it just receives a different impl.

### Abort / cancellation

A run can be cancelled mid-flight with an `asyncio.Event` passed as `signal=`.
Setting the event aborts the in-flight iteration: the `sandbox.exec` is
cancelled (on `no_sandbox` that kills the host subprocess) and `run()` raises
`asyncio.CancelledError` promptly instead of waiting for the agent to finish.

```python
import asyncio
from pysolated import run, claude_code, no_sandbox

async def main():
    abort = asyncio.Event()

    async def fire_abort_after(seconds: float) -> None:
        await asyncio.sleep(seconds)
        abort.set()

    asyncio.create_task(fire_abort_after(5.0))
    try:
        await run(
            agent=claude_code("claude-opus-4-7"),
            sandbox=no_sandbox(),
            prompt="long task",
            signal=abort,
        )
    except asyncio.CancelledError:
        print("aborted")

asyncio.run(main())
```

- Setting the event between iterations stops the outer loop before the next
  iteration starts.
- Pre-setting the event before `run()` is called aborts immediately, without
  invoking the agent.
- From the CLI, **Ctrl-C** maps onto the same abort signal: the SIGINT handler
  sets the event so the orchestrator cancels cleanly and the CLI exits with
  status `130` instead of tearing through asyncio with a `KeyboardInterrupt`.

### Agent failure surfacing

When the agent subprocess exits non-zero, `run()` raises `AgentExecutionError`
— a structured exception from the library's hierarchy, carrying the exit code
and the *tail* of stderr/stdout most likely to explain the crash:

```python
from pysolated import AgentExecutionError, run, claude_code, no_sandbox

try:
    await run(
        agent=claude_code("claude-opus-4-7"),
        sandbox=no_sandbox(),
        prompt="...",
    )
except AgentExecutionError as exc:
    print(exc.exit_code)     # e.g. 127
    print(exc.stderr_tail)   # last lines of stderr
    print(exc.stdout_tail)   # last lines of stream-json output
```

- `exit_code` is the subprocess exit status.
- `stderr_tail` / `stdout_tail` are truncated to the last ~50 lines so the
  exception stays readable even when the agent emitted megabytes of
  stream-json before crashing.
- `str(exc)` includes the exit code and the relevant tail — what the CLI
  echoes to the user (exit `1`).
- Distinct from `IdleTimeoutError` (a *stuck* agent) and
  `StructuredOutputError` (a malformed payload); each names its own failure
  mode so callers can branch on the cause.

### Structured output

A **structured output** is a schema-validated payload the agent emits inside
a named XML tag in its own prose. Ask for one via `output=` on `run()`:

```python
from pydantic import BaseModel
from pysolated import Output, run, claude_code, no_sandbox

class Answer(BaseModel):
    answer: int
    rationale: str

result = await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=no_sandbox(),
    prompt=(
        "Compute the answer. "
        "Reply with <result>{\"answer\": int, \"rationale\": str}</result>."
    ),
    output=Output.object("result", Answer),
)
print(result.output.answer)       # typed: int
print(result.output.rationale)    # typed: str
```

Two flavours, both consumed via `output=`:

- `Output.object(tag, model)` — JSON-parses the inner text and validates it
  against the Pydantic `model`. A `` ```json … ``` `` (or bare ` ``` … ``` `)
  fence inside the tag is unwrapped automatically.
- `Output.string(tag)` — returns the raw inner text, whitespace-trimmed; no
  JSON parsing, no schema.

When the agent emits the tag multiple times — common during self-correction —
the **last occurrence wins**.

Structured output is **orthogonal to the completion signal**; a run may use
either, both, or neither. Two up-front guards run before any agent work, so a
misconfigured call fails fast:

- `max_iterations != 1` is rejected (the payload must unambiguously belong to
  the iteration that produced it).
- The resolved prompt must contain the configured opening tag — a missing
  instruction is caught before paying for the run.

On a tag that does parse and validate, `RunResult.output` holds the model
instance (object mode) or the trimmed string (string mode). On failure (tag
missing, JSON parse error, schema mismatch), `run()` raises
`StructuredOutputError` carrying `tag`, `raw_matched` (the inner text when
any was found), and the underlying `cause` (the `JSONDecodeError` or
`pydantic.ValidationError`) so you can fix the prompt or schema without
re-running the agent.

## CLI

```bash
uv run pysolated run --prompt "say hi"
```

The CLI is a thin Typer layer over the same `run()` engine. Flags:

| Flag | Default | Description |
| --- | --- | --- |
| `--prompt` | _(see below)_ | Inline prompt, sent to the agent verbatim. Mutually exclusive with `--prompt-file`; one is required. |
| `--prompt-file` | _(see below)_ | Path to a prompt template. `{{KEY}}` placeholders are substituted from `--prompt-arg` overlaid on built-ins (`branch`); `` !`cmd` `` expressions are run through the sandbox and replaced by stdout. |
| `--prompt-arg` | _(none)_ | `KEY=VALUE` argument for `--prompt-file`. Repeatable. Rejected when combined with `--prompt`. |
| `--model` | `claude-opus-4-7` | Claude model to run. |
| `--cwd` | current dir | Repo directory to anchor the run to. |
| `--permission-mode` | _(none)_ | Claude `--permission-mode`; mutually exclusive with skip-permissions. |
| `--name` | _(none)_ | Optional name for the run. Appears as a `[name]` prefix on every status line and in the log file header. |
| `--log-file` | _(none)_ | Write progress and agent output to this path instead of the terminal. `tail -f` shows live progress; `RunResult.log_file_path` reports the path. |
| `--max-iterations` | `1` | Maximum agent invocations in the loop. |
| `--completion-signal` | `<promise>COMPLETE</promise>` | Substring in the agent's own output that ends the loop early (tool inputs/outputs it reads are never matched). Repeat the flag to match any of several. |
| `--idle-timeout` | `600` | Seconds without output before failing with an idle error. |
| `--completion-timeout` | `60` | Grace seconds after the completion signal before forcing success. |

Examples:

```bash
# multi-iteration run that stops as soon as the agent emits a custom signal
uv run pysolated run \
  --prompt "Refactor X. Emit DONE when finished." \
  --max-iterations 5 \
  --completion-signal DONE

# match either of two completion signals, against another repo
uv run pysolated run --prompt "..." --cwd /path/to/repo \
  --completion-signal DONE --completion-signal FINISHED

# shorten the idle timeout and the post-signal grace window
uv run pysolated run --prompt "..." --idle-timeout 120 --completion-timeout 30

# unattended run: write progress + agent output to a log file, name the run
uv run pysolated run --prompt "..." \
  --name nightly-refactor --log-file /tmp/pysolated-nightly.log

# resolve a prompt template, supplying one user argument; `branch` is built-in
uv run pysolated run \
  --prompt-file prompts/refactor.txt \
  --prompt-arg area=auth \
  --completion-signal DONE
```

On completion the CLI prints the iteration count, the matched completion signal,
the commits the agent made, and token usage.

### Podman image lifecycle

The `pysolated podman` subgroup manages the image the `podman(...)` sandbox
runs. Both subcommands default to the same derived tag the provider uses —
`pysolated:<sanitized-host-dirname>` — so a fresh repo goes from zero to a
running agent without leaving pysolated.

```bash
# build ./Containerfile to the derived tag
uv run pysolated podman build-image

# build a different Containerfile to an explicit tag
uv run pysolated podman build-image --file docker/Containerfile --image my-agent:latest

# remove the derived (or a named) image
uv run pysolated podman remove-image
uv run pysolated podman remove-image --image my-agent:latest
```

**`pysolated podman build-image`** — runs `podman build -f <file> -t <tag> <cwd>`:

| Flag | Default | Description |
| --- | --- | --- |
| `--file` / `-f` | `Containerfile` | Containerfile path passed to `podman build -f`. |
| `--image` | `pysolated:<sanitized-host-dirname>` | Image tag to build. |

**`pysolated podman remove-image`** — the matching `podman rmi`:

| Flag | Default | Description |
| --- | --- | --- |
| `--image` | `pysolated:<sanitized-host-dirname>` | Image tag to remove. |

Both exit non-zero (propagating Podman's exit code) and echo Podman's stderr
when the underlying command fails.

## Architecture

Three injectable `Protocol` seams (per
[ADR 0002](docs/adr/0002-asyncio-not-effect.md)), built up front so Docker and
other agents are additive:

- **`AgentProvider`** — builds the agent command (an argv list + optional stdin,
  per [ADR 0001](docs/adr/0001-agent-providers-return-argv.md)) and parses its
  stream output.
- **`SandboxProvider`** — runs a command, streaming stdout line-by-line.
- **`Display`** — narrates the run (the orchestrator's test-substitution point).
  Two impls ship: `TerminalDisplay` (default) and `FileDisplay` (selected by
  `log_file=` / `--log-file`).

## Development

```bash
uv sync
uv run pytest
```
