# Observability & logging extras

See [features.md](./features.md) for the index.

- **`onAgentStreamEvent` callback** — forward each agent stream event (text /
  tool call, with iteration number + timestamp) to an external observability system
  (log-to-file mode only).
- **Run logs** under `.sandcastle/logs/` with branch/name-derived filenames.
