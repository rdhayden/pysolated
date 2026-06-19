"""Orchestrator seam tests for the branch strategy.

Verifies that ``run()`` routes through ``branch_strategy.prepare`` (before
``sandbox.create()``) and ``branch_strategy.finalize`` (after ``sandbox.close()``),
and that the ``source_branch`` plumbing — ``RunResult.source_branch`` plus the
``{{source_branch}}`` prompt-template arg — is populated.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from pysolated import (
    AgentCommandOptions,
    BranchAlreadyCheckedOutError,
    Command,
    ExecResult,
    HeadStrategy,
    MergeConflictError,
    MergeToHeadStrategy,
    NamedBranchStrategy,
    RunResult,
    Severity,
    no_sandbox,
    parse_session_usage,
    parse_stream_line,
    podman,
    run,
)
from pysolated.worktrees import FinalizedRun, PreparedRun


class _RecordingStrategy:
    """A strategy that records when prepare/finalize fired and what it saw.

    Wraps an inner strategy so the seam wiring is exercised, but with
    visibility for the test to assert on. The `events` list is a shared
    timeline — the test passes the same list to the sandbox so the relative
    order of prepare/create/close/finalize is observable.
    """

    def __init__(self, inner: HeadStrategy, events: list[str] | None = None) -> None:
        self._inner = inner
        self.events: list[str] = events if events is not None else []
        self.prepared_with: str | None = None
        self.finalize_success: bool | None = None

    async def prepare(self, cwd: str) -> PreparedRun:
        self.events.append("prepare")
        self.prepared_with = cwd
        return await self._inner.prepare(cwd)

    async def finalize(self, prepared: PreparedRun, *, success: bool) -> FinalizedRun:
        self.events.append("finalize")
        self.finalize_success = success
        return await self._inner.finalize(prepared, success=success)


class _EventOrderSandbox:
    """A no_sandbox-ish fake that records create/close around the prepare/finalize bracket."""

    name = "fake-sandbox"
    env: dict[str, str] = {}

    def __init__(
        self,
        events: list[str],
        *,
        lines: list[str],
        branch: str = "main",
    ) -> None:
        self._events = events
        self._lines = lines
        self._branch = branch

    async def create(self, work_dir: str) -> "_EventOrderSandbox":
        self._events.append("create")
        return self

    async def close(self) -> None:
        self._events.append("close")

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        if argv[:2] == ["git", "rev-parse"] and "--abbrev-ref" in argv:
            return ExecResult(exit_code=0, stdout=f"{self._branch}\n", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return ExecResult(exit_code=0, stdout="deadbeef\n", stderr="")
        if argv[:2] == ["git", "rev-list"]:
            return ExecResult(exit_code=0, stdout="", stderr="")
        for line in self._lines:
            if on_line is not None:
                on_line(line)
        return ExecResult(exit_code=0, stdout="\n".join(self._lines), stderr="")


class _FakeAgent:
    name = "fake-agent"
    env: dict[str, str] = {}

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.built_options: AgentCommandOptions | None = None

    def build_command(self, options: AgentCommandOptions) -> Command:
        self.built_options = options
        return Command(argv=["fake-agent"], stdin=options.prompt)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class _SilentDisplay:
    def intro(self, title: str) -> None: ...
    def status(self, message: str, severity: Severity) -> None: ...
    def text(self, message: str) -> None: ...
    def tool_call(self, name: str, formatted_args: str) -> None: ...
    def summary(self, title: str, rows: dict[str, str]) -> None: ...


def _hello_line() -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    )


async def test_prepare_runs_before_create_and_finalize_after_close() -> None:
    """The strategy brackets the sandbox lifetime: prepare→create→close→finalize."""
    events: list[str] = []
    sandbox = _EventOrderSandbox(events, lines=[_hello_line()])
    strategy = _RecordingStrategy(HeadStrategy(), events=events)

    await run(
        agent=_FakeAgent([_hello_line()]),
        sandbox=sandbox,
        prompt="go",
        cwd="/repo",
        display=_SilentDisplay(),
        branch_strategy=strategy,
    )

    # The bracket: prepare must precede sandbox.create, and finalize must
    # follow sandbox.close — exactly once each.
    assert events.index("prepare") < events.index("create")
    assert events.index("close") < events.index("finalize")
    assert events.count("prepare") == 1
    assert events.count("finalize") == 1


async def test_default_strategy_is_head_when_unspecified() -> None:
    """Calling run() with no branch_strategy uses HeadStrategy()."""
    sandbox = _EventOrderSandbox([], lines=[_hello_line()])
    result = await run(
        agent=_FakeAgent([_hello_line()]),
        sandbox=sandbox,
        prompt="go",
        cwd="/repo",
        display=_SilentDisplay(),
    )
    # source_branch == branch (target) for head — the observable signature
    # of the default having engaged.
    assert result.source_branch == result.branch == "main"


async def test_finalize_called_with_success_on_clean_run() -> None:
    strategy = _RecordingStrategy(HeadStrategy())
    sandbox = _EventOrderSandbox([], lines=[_hello_line()])
    await run(
        agent=_FakeAgent([_hello_line()]),
        sandbox=sandbox,
        prompt="go",
        cwd="/repo",
        display=_SilentDisplay(),
        branch_strategy=strategy,
    )
    assert strategy.finalize_success is True


async def test_finalize_called_with_failure_on_raise() -> None:
    """A failing run still finalize()s — the strategy must run its cleanup."""

    class _FailingSandbox(_EventOrderSandbox):
        async def exec(
            self,
            argv: list[str],
            *,
            stdin: str | None = None,
            cwd: str | None = None,
            on_line: Callable[[str], None] | None = None,
        ) -> ExecResult:
            if argv[:2] == ["git", "rev-parse"] and "--abbrev-ref" in argv:
                return ExecResult(exit_code=0, stdout="main\n", stderr="")
            if argv[:2] == ["git", "rev-parse"]:
                return ExecResult(exit_code=0, stdout="deadbeef\n", stderr="")
            if argv[:2] == ["git", "rev-list"]:
                return ExecResult(exit_code=0, stdout="", stderr="")
            return ExecResult(exit_code=2, stdout="", stderr="boom")

    strategy = _RecordingStrategy(HeadStrategy())
    sandbox = _FailingSandbox([], lines=[])
    with pytest.raises(Exception):
        await run(
            agent=_FakeAgent([]),
            sandbox=sandbox,
            prompt="go",
            cwd="/repo",
            display=_SilentDisplay(),
            branch_strategy=strategy,
        )
    # finalize must have been called, with success=False.
    assert strategy.events == ["prepare", "finalize"]
    assert strategy.finalize_success is False


async def test_iterations_run_in_work_dir_returned_by_prepare() -> None:
    """The iteration loop runs in the work_dir prepare returns, not the original cwd.

    For head, work_dir == cwd, so this is a degenerate check — but it pins the
    contract that the merge-to-head impl will rely on.
    """
    sandbox = _EventOrderSandbox([], lines=[_hello_line()])
    captured_cwds: list[str | None] = []

    original_exec = sandbox.exec

    async def recording_exec(
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        captured_cwds.append(cwd)
        return await original_exec(argv, stdin=stdin, cwd=cwd, on_line=on_line)

    sandbox.exec = recording_exec  # type: ignore[method-assign]

    await run(
        agent=_FakeAgent([_hello_line()]),
        sandbox=sandbox,
        prompt="go",
        cwd="/repo",
        display=_SilentDisplay(),
        branch_strategy=HeadStrategy(),
    )
    # Every exec call must have used /repo (the work_dir prepare returned).
    assert captured_cwds and all(c == "/repo" for c in captured_cwds)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


class _NoopRealAgent:
    """Emits the completion signal so the iteration ends without an agent process."""

    name = "noop"
    env: dict[str, str] = {}

    def build_command(self, options: AgentCommandOptions) -> Command:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": f"prompt={options.prompt} STOP"}
                    ]
                },
            }
        )
        return Command(argv=["printf", "%s\n", line], stdin=None)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


async def test_source_branch_populated_on_run_result_for_head(git_repo: Path) -> None:
    """For head, source_branch == branch (target) == the current branch."""
    result = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=HeadStrategy(),
    )
    assert isinstance(result, RunResult)
    assert result.branch == "main"
    assert result.source_branch == "main"


async def test_head_run_creates_no_worktree_directory(git_repo: Path) -> None:
    """The byte-for-byte head regression: no worktree dir should appear in cwd."""
    await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
    )
    # The bullet auto-writes .pysolated/worktrees/ only for merge-to-head.
    # The head pass-through must not touch the user's tree.
    assert not (git_repo / ".pysolated" / "worktrees").exists()


async def test_default_run_matches_explicit_head_byte_for_byte(git_repo: Path) -> None:
    """An explicit ``HeadStrategy()`` is observable-equivalent to omitting the arg.

    Same branch, same source_branch, same commits, same completion signal.
    """
    default_result = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
    )
    explicit_result = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=HeadStrategy(),
    )
    assert default_result.branch == explicit_result.branch
    assert default_result.source_branch == explicit_result.source_branch
    assert default_result.completion_signal == explicit_result.completion_signal
    assert default_result.commits == explicit_result.commits


async def test_source_branch_available_in_prompt_template(
    git_repo: Path, tmp_path: Path
) -> None:
    """`{{source_branch}}` resolves in a prompt template (head: == current branch)."""
    template = tmp_path / "prompt.txt"
    template.write_text("branch={{branch}} source={{source_branch}} STOP\n")
    agent = _NoopRealAgent()

    result = await run(
        agent=agent,
        sandbox=no_sandbox(),
        prompt_file=str(template),
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=HeadStrategy(),
    )
    # The agent echoes the resolved prompt back, so the stdout carries the values.
    assert "branch=main" in result.stdout
    assert "source=main" in result.stdout


# ---------------------------------------------------------------------------
# merge-to-head integration tests against a real git repo + no_sandbox.
# ---------------------------------------------------------------------------


class _CommittingAgent:
    """Writes a file, commits it inside the work_dir, then prints the signal.

    Runs through `sh -c` so the work_dir set by the orchestrator is the cwd
    git operates in — the worktree path for merge-to-head, cwd for head.
    """

    name = "committing"
    env: dict[str, str] = {}

    def __init__(self, content: str = "agent\n", filename: str = "agent.txt") -> None:
        self._content = content
        self._filename = filename

    def build_command(self, options: AgentCommandOptions) -> Command:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "done STOP"}]},
            }
        )
        script = (
            f"printf '%s' '{self._content}' > {self._filename} && "
            f"git -c user.email=agent@test -c user.name=agent add {self._filename} && "
            f"git -c user.email=agent@test -c user.name=agent commit -qm 'agent commit' && "
            f"printf '%s\\n' '{line}'"
        )
        return Command(argv=["sh", "-c", script], stdin=None)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


async def test_merge_to_head_run_merges_back_to_target(git_repo: Path) -> None:
    """End-to-end: merge-to-head runs in a worktree and merges back to main."""
    result = await run(
        agent=_CommittingAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=MergeToHeadStrategy(),
    )
    # Target is the host branch; source is the temp scratch branch.
    assert result.branch == "main"
    assert result.source_branch.startswith("pysolated/")
    assert result.preserved_worktree_path is None
    # The agent's commit must be reachable from main.
    log = subprocess.run(
        ["git", "log", "--oneline", "main"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "agent commit" in log
    # The scratch branch is deleted; the worktree is gone.
    branches = subprocess.run(
        ["git", "branch", "--list", result.source_branch],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branches == ""
    assert not (
        git_repo / ".pysolated" / "worktrees" / result.source_branch.split("/")[-1]
    ).exists()


async def test_merge_to_head_writes_gitignore_into_repo(git_repo: Path) -> None:
    """A merge-to-head run writes `.pysolated/worktrees/.gitignore`."""
    await run(
        agent=_CommittingAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=MergeToHeadStrategy(),
    )
    gitignore = git_repo / ".pysolated" / "worktrees" / ".gitignore"
    assert gitignore.exists()
    assert gitignore.read_text().strip() == "*"


async def test_merge_to_head_conflict_propagates_as_run_error(git_repo: Path) -> None:
    """A merge conflict from finalize surfaces as ``MergeConflictError`` out of run()."""

    class _ConflictAgent:
        name = "conflict"
        env: dict[str, str] = {}

        def build_command(self, options: AgentCommandOptions) -> Command:
            line = json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "STOP"}]},
                }
            )
            # The agent commits "agent version" of conflict.txt inside the worktree.
            script = (
                "printf '%s' 'agent version\n' > conflict.txt && "
                "git -c user.email=a@a -c user.name=a add conflict.txt && "
                "git -c user.email=a@a -c user.name=a commit -qm 'agent change' && "
                f"printf '%s\\n' '{line}'"
            )
            return Command(argv=["sh", "-c", script], stdin=None)

        def parse_stream_line(self, line: str):
            return parse_stream_line(line)

        def parse_session_usage(self, content: str):
            return parse_session_usage(content)

    # Wrap MergeToHeadStrategy with a tiny adapter that diverges the host
    # branch between prepare and the agent's run. The strategy itself is
    # frozen, so we compose rather than mutate; the adapter forwards finalize
    # straight through.
    class _DivergingStrategy:
        def __init__(self) -> None:
            self._inner = MergeToHeadStrategy()

        async def prepare(self, cwd: str) -> PreparedRun:
            prepared = await self._inner.prepare(cwd)
            repo = Path(cwd)
            (repo / "conflict.txt").write_text("host version\n")
            subprocess.run(
                ["git", "add", "conflict.txt"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "host change"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            return prepared

        async def finalize(
            self, prepared: PreparedRun, *, success: bool
        ) -> FinalizedRun:
            return await self._inner.finalize(prepared, success=success)

    strategy = _DivergingStrategy()

    with pytest.raises(MergeConflictError) as excinfo:
        await run(
            agent=_ConflictAgent(),
            sandbox=no_sandbox(),
            prompt="go",
            cwd=str(git_repo),
            display=_SilentDisplay(),
            completion_signal="STOP",
            branch_strategy=strategy,
        )

    # The worktree is preserved on disk; the user's tree has no markers.
    assert Path(excinfo.value.worktree_path).exists()
    assert "<<<<<<<" not in (git_repo / "conflict.txt").read_text()


async def test_merge_to_head_preserved_worktree_path_on_run_result(
    git_repo: Path,
) -> None:
    """A dirty (uncommitted) worktree on success is preserved on `RunResult`."""

    class _DirtyAgent:
        name = "dirty"
        env: dict[str, str] = {}

        def build_command(self, options: AgentCommandOptions) -> Command:
            line = json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "STOP"}]},
                }
            )
            # Write a file but do NOT commit it.
            script = f"printf '%s' 'dirty\n' > dirty.txt && printf '%s\\n' '{line}'"
            return Command(argv=["sh", "-c", script], stdin=None)

        def parse_stream_line(self, line: str):
            return parse_stream_line(line)

        def parse_session_usage(self, content: str):
            return parse_session_usage(content)

    result = await run(
        agent=_DirtyAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=MergeToHeadStrategy(),
    )
    assert result.preserved_worktree_path is not None
    assert Path(result.preserved_worktree_path).exists()
    assert (Path(result.preserved_worktree_path) / "dirty.txt").exists()


async def test_merge_to_head_rejects_non_no_sandbox_provider(git_repo: Path) -> None:
    """A non-no_sandbox provider hard-errors up front."""
    with pytest.raises(ValueError, match="MergeToHeadStrategy"):
        await run(
            agent=_NoopRealAgent(),
            sandbox=podman(image="dummy"),
            prompt="go",
            cwd=str(git_repo),
            display=_SilentDisplay(),
            completion_signal="STOP",
            branch_strategy=MergeToHeadStrategy(),
        )


# ---------------------------------------------------------------------------
# NamedBranchStrategy integration tests against a real git repo + no_sandbox.
# ---------------------------------------------------------------------------


async def test_named_branch_run_commits_land_on_named_branch_no_merge_back(
    git_repo: Path,
) -> None:
    """Agent commits land on the named branch; the host's current branch is untouched."""
    pre_run_main = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    result = await run(
        agent=_CommittingAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=NamedBranchStrategy(branch="feature/x"),
    )

    # For `branch`, source == target == the named branch.
    assert result.branch == "feature/x"
    assert result.source_branch == "feature/x"
    # The agent's commit must be on the named branch.
    feature_log = subprocess.run(
        ["git", "log", "--oneline", "feature/x"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "agent commit" in feature_log
    # No merge-back: main's tip is unchanged.
    post_run_main = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert post_run_main == pre_run_main
    # The named branch must NOT be reachable from main (no merge happened).
    main_log = subprocess.run(
        ["git", "log", "--oneline", "main"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "agent commit" not in main_log


async def test_named_branch_run_result_surfaces_durable_worktree_path(
    git_repo: Path,
) -> None:
    """`RunResult.worktree_path` points at the durable worktree; preserved is None."""
    result = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=NamedBranchStrategy(branch="feature/x"),
    )

    assert result.worktree_path is not None
    assert Path(result.worktree_path).is_dir()
    # The durable channel is `worktree_path`, NOT `preserved_worktree_path`.
    assert result.preserved_worktree_path is None
    # The durable worktree dir is at the deterministic path.
    assert (
        Path(result.worktree_path)
        == git_repo / ".pysolated" / "worktrees" / "feature-x"
    )


async def test_named_branch_run_keeps_worktree_after_run(git_repo: Path) -> None:
    """The durable worktree persists by design — not removed at run end."""
    result = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=NamedBranchStrategy(branch="feature/x"),
    )
    assert result.worktree_path is not None
    assert Path(result.worktree_path).exists()


async def test_named_branch_reuse_does_not_wipe_dirty_worktree(
    git_repo: Path,
) -> None:
    """A second run targeting the same branch reuses the worktree without wiping."""
    await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=NamedBranchStrategy(branch="feature/x"),
    )
    worktree = git_repo / ".pysolated" / "worktrees" / "feature-x"
    (worktree / "scratch.txt").write_text("uncommitted host work\n")

    result = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=NamedBranchStrategy(branch="feature/x"),
    )

    assert result.worktree_path == str(worktree)
    # Uncommitted work survives the second run.
    assert (worktree / "scratch.txt").read_text() == "uncommitted host work\n"


async def test_named_branch_already_in_main_tree_raises_clear_error(
    git_repo: Path,
) -> None:
    """Naming the branch already checked out in the main tree raises a clear pysolated error."""
    with pytest.raises(BranchAlreadyCheckedOutError):
        await run(
            agent=_NoopRealAgent(),
            sandbox=no_sandbox(),
            prompt="go",
            cwd=str(git_repo),
            display=_SilentDisplay(),
            completion_signal="STOP",
            branch_strategy=NamedBranchStrategy(branch="main"),
        )


async def test_named_branch_rejects_non_no_sandbox_provider(git_repo: Path) -> None:
    """`branch` with a non-no_sandbox provider hard-errors up front."""
    with pytest.raises(ValueError, match="NamedBranchStrategy"):
        await run(
            agent=_NoopRealAgent(),
            sandbox=podman(image="dummy"),
            prompt="go",
            cwd=str(git_repo),
            display=_SilentDisplay(),
            completion_signal="STOP",
            branch_strategy=NamedBranchStrategy(branch="feature/x"),
        )
