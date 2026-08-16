# ris-supervisor

The service starter/manager companion for **RIS** — the [modular init system for
GNU/Linux](https://codeberg.org/javav12/ris).

RIS deliberately does not ship a service manager; it delegates that job to an
external binary. `rissup` is that binary: it supervises services, applies
restart policies, logs their output, and shuts everything down cleanly when RIS
asks for it. `risctl` is its control client.

```
 KERNEL ──▶ /init (prepare.sh) ──▶ RIS (PID 1)
                                       │
                          spawns child│
                                       ▼
                                 rissup (this project)
                              ┌──────────┬──────────┬──────────┐
                              │  ticker  │ dropbear │  getty   │ ...
                              └──────────┴──────────┴──────────┘
                              ▲
                          risctl (unix socket, /var/run/ris-svc.sock)
```

## The RIS contract

RIS treats the service starter as a plain long-lived child process:

| RIS behaviour                          | What `rissup` must do                                  |
| -------------------------------------- | ------------------------------------------------------ |
| `fork+exec` at boot (`src/main.py`)    | run forever as a direct child of PID 1                 |
| reaps zombies, detects starter death   | never die on transient failures                        |
| respawns starter with backoff          | restart cleanly, or don't die in the first place       |
| `SIGTERM`, waits 30s, then `SIGKILL`   | stop all services and exit **within 30 seconds**       |

`rissup` satisfies the last point by sending `SIGTERM` to every service on
shutdown, giving each its `stop_timeout` (default 10s) before escalating to
`SIGKILL`, and exiting immediately once everything is stopped.

RIS's own FIFO only understands `rl0`/`rl6`. Runlevel 1–5 are reserved "for the
service starter" — that is where the `risctl` control socket lives.

## Install & integrate

Build a musl binary the same way RIS does:

```bash
sudo ./build.sh          # produces ./ris-musl (static, via alpine container)
sudo cp ./ris-musl /sbin/rissup
sudo cp examples/*.service /etc/ris/services/
```

Make RIS spawn this instead of a shell. In RIS's `src/main.py`, inside
`service_starter_spawn`:

```python
service_starter_pid = spawn("/sbin/rissup", ["rissup"])
```

That is the whole integration — no pipes, no IPC with RIS, just a child process
that behaves.

## Service definitions

Every `*.service` file in `/etc/ris/services/` describes one service. Lines are
`key = value`, `#` starts a comment, and `[Service]` section headers are
ignored.

```ini
[Service]
# the command to run (split like a shell)
exec = /usr/sbin/dropbear -E -F
# restart policy: always | on_failure | never
restart = always
# wait before respawning a crashed service (seconds)
restart_delay = 2
# give up after this many consecutive fast failures (0 = unlimited)
backoff_limit = 10
# how long a service may take to die before SIGKILL (seconds)
stop_timeout = 5
# start automatically when rissup boots
run_at_boot = true
# working directory and umask for the service
chdir = /
umask = 022
# drop privileges (needs root)
# user = nobody
# group = nogroup
# extra environment variables
environment = FOO=bar BAZ=qux
```

A service is considered stable after staying up for 5 seconds; its fast-failure
counter then resets. Output goes to `/var/log/ris/<name>.log`.

## Control

```bash
risctl status                # name pid state restarts last-exit
risctl start  getty          # start one service
risctl stop   getty          # stop one service
risctl restart getty         # restart one service
risctl reload                # re-read /etc/ris/services, start new boot services
risctl list                  # same as status
```

Commands travel over a line-based unix socket at `/var/run/ris-svc.sock`, one
command per connection — the same "do one thing" philosophy as RIS's FIFO.

## Test

`test/sandbox-test.sh` runs the whole thing in a scratch directory without
touching the system: boot services, crash-restart, backoff limiter, graceful
stop, and a clean SIGTERM shutdown of the daemon itself.

```bash
bash test/sandbox-test.sh
```

## Lint

```bash
ruff check
```

## Limitations

- `rissup` is for systems where RIS is the real PID 1. Running it as a regular
  process still works for supervision testing (see the sandbox test), but
  privilege dropping (`user`/`group`) needs root.
- If `rissup` is killed and RIS respawns it, previously running services are
  reparented to PID 1 and are no longer supervised; they keep running until
  shutdown. The new instance starts the `run_at_boot` set fresh.
- Early development stage. Test in a VM/QEMU, not on your main machine.

## License

GPL-3.0-or-later, matching RIS.

## Credits

This project exists because of the **RIS** init system by
[javav12](https://codeberg.org/javav12) — a modular PID 1 for GNU/Linux that
deliberately leaves the *service starter* slot open and delegates service
management to an external binary.

Big thank you to **javav12** for building RIS, for that clean "do one thing and
do it well" philosophy, and for the space this project plugs into. `ris-supervisor`
is an independent companion, not a fork: the only change RIS needs is the
one-line `spawn("/sbin/rissup", ["rissup"])` shown in
[Install & integrate](#install--integrate).

- RIS project: <https://codeberg.org/javav12/ris>
- Built in the style of RIS: same layout, build pipeline (PyInstaller `--onedir`,
  musl/alpine & debian container builds) and `ruff select ALL` lint setup.