# Runtime Modes

`AGENT_RUNTIME_MODE` controls execution strictness for `core/executor.py`.

- `dev`: no `-I`, full environment inheritance for rapid local iteration.
- `safe` (default): runs with `-I`, environment allowlist, fixed working directory.
- `strict`: currently same baseline as `safe`, reserved for tighter restrictions.

If an invalid mode is provided, it falls back to `safe`.

