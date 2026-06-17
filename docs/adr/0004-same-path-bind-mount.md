# Bind-mount the repo at its host path, not a canonical `/workspace`

The Podman provider bind-mounts the host repo to the **identical** path inside the
container (`-v /home/you/repo:/home/you/repo`), rather than a canonical mount point
like Sandcastle's `/home/agent/workspace`. The reason is pysolated's orchestrator is
path-agnostic: it computes `work_dir` as a host absolute path and forwards it as
`cwd=` to *every* `exec` (the agent command, the git calls, prompt `sh -c`). A
same-path mount makes the host and container agree by construction, so `cwd` passes
straight through `podman exec -w <cwd>` with no rewriting, and `no_sandbox` and
`podman` honour an identical seam contract. Paired with `--userns=keep-id:uid=N,gid=N`
+ `--user N:N`, the bind-mounted files appear owned by the in-container user and the
process runs as that user — so there is **no chown step and no git `safe.directory`
configuration**, and git running inside the container (how the orchestrator does
`rev-parse`/`rev-list`) sees no "dubious ownership".

## Considered Options

- **Fixed mount point + path translation (Sandcastle's `/home/agent/workspace`).**
  Rejected: it requires the handle to know the host→container mapping and string-rewrite
  every incoming `cwd` — fragile around symlinks, sub-directories, and `git -C`.
  Sandcastle needs it only because *its* worktree machinery hands the agent a
  container-relative path; pysolated has no such machinery.

## Consequences

- The repo sits at its host path inside the container, not tidily under
  `$HOME=/home/agent`. Harmless under keep-id — it's just a mount target Podman creates.
- A reader cross-referencing Sandcastle will find the mount point deliberately
  different; this is intentional, not an oversight.
- The image contract: a user/group at `container_uid:container_gid` (default 1000:1000)
  must exist in the image (so keep-id maps correctly), with `git` and the agent CLI on
  `PATH`. This mirrors Sandcastle ADR 0005 (keep-id instead of build-arg/chown UID
  alignment), which is the Podman-specific path.
