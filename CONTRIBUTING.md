# Contributing

We welcome contributions! Please make sure your code passes the linting
process before opening a Pull Request:

```bash
ruff check
```

(If you need to bypass a specific linting rule for one line, use
`# noqa: <error-code>`.)

Ideas that fit the project's philosophy:

- `oneshot` service type (run to completion, like fsck or a first-boot setup).
- Privilege dropping for `user`/`group` end-to-end tests.
- Restarting `run_at_boot` services when respawned by RIS after a crash.
- Pre/post exec hooks such as `exec-start` / `exec-stop`.

Kept deliberately minimal, in the "do one thing" spirit of RIS.