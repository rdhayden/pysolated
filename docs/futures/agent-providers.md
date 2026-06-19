# Additional agent providers

Roadmap item 3 (multi-agent registry — init picks an agent + writes its
Containerfile). v1 ships `claude_code` only. See [features.md](./features.md) for the
index.

Sandcastle also supports:

- **Codex** (`codex exec`, `effort` low/medium/high/xhigh, session storage).
- **Copilot** (`copilot -p --output-format json`, `effort`).
- **Cursor** (`agent --print`; prompt passed via argv with a size guard;
  non-resumable).
- **OpenCode** (`--format json` event stream; session storage).
- **pi** (session storage).
- Per-provider **env manifests + env checks** (declared required vars validated
  before the agent starts) and per-provider Dockerfile templates.
