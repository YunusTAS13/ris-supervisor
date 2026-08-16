#
#    ris-supervisor - GNU/Linux service manager companion for the RIS init system
#    Copyright (C) 2026
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    SPDX-License-Identifier: GPL-3.0-or-later
#

"""ris-supervisor: the service starter/manager companion for the RIS init system.

RIS (a minimal PID 1) spawns this daemon as its "service starter". This daemon
loads service definitions from /etc/ris/services/, supervises them (restart
policies, output logging, backoff), serves control commands over a unix socket,
and stops everything gracefully on SIGTERM so RIS can finish the shutdown.
"""

from __future__ import annotations

import argparse
import logging
import os
import selectors
import signal
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import config as ris_config
from control import ControlServer
from runner import spawn
from service import MIN_UPTIME

if TYPE_CHECKING:
    from service import Service

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path("/etc/ris/services")
DEFAULT_LOG_DIR = Path("/var/log/ris")
DEFAULT_SOCKET = Path("/var/run/ris-svc.sock")
DEFAULT_LOG = Path("/var/run/rissup.log")

POLL_TIMEOUT = 0.05


def _describe_status(status: int) -> str:
    """Human readable description of a waitpid status."""
    if os.WIFEXITED(status):
        return f"exited({os.WEXITSTATUS(status)})"
    if os.WIFSIGNALED(status):
        return f"signaled({os.WTERMSIG(status)})"
    return f"status({status})"


class Supervisor:
    """Owns services, the waitpid loop, and the control socket."""

    def __init__(
        self,
        config_dir: Path,
        log_dir: Path,
        socket_path: Path,
        log_file: Path,
    ) -> None:
        """Initialize the supervisor with overridable paths."""
        self.config_dir = config_dir
        self.log_dir = log_dir
        self.socket_path = socket_path
        self.log_file = log_file
        self.sel = selectors.DefaultSelector()
        self.services: dict[str, Service] = {}
        self._by_pid: dict[int, Service] = {}
        self.wanted: dict[str, bool] = {}
        self.stopping = False
        self.control: ControlServer | None = None

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._init_logging()

    def _init_logging(self) -> None:
        """Configure file + stderr logging before anything is reported."""
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        logging.basicConfig(level=logging.INFO, format=fmt)
        root = logging.getLogger()
        root.addHandler(logging.FileHandler(self.log_file))
        root.info("ris-supervisor starting (pid %s)", os.getpid())

    # ------------------------------------------------------------------ #
    # service lifecycle primitives
    # ------------------------------------------------------------------ #

    def _spawn_process(self, svc: Service) -> None:
        """Start one service process (no restart scheduling)."""
        log_path = self.log_dir / f"{svc.name}.log"
        result = spawn(svc, log_path)
        if result[1] is not None:
            svc.state = "stopped"
            svc.last_exit = f"exec failed: {result[1]}"
            svc.consecutive_failures += 1
            logger.error("%s: %s", svc.name, svc.last_exit)
            self._schedule_retry(svc)
            return
        svc.pid, _ = result
        svc.state = "running"
        svc.spawn_time = time.monotonic()
        svc.next_restart_at = 0.0
        self._by_pid[svc.pid] = svc
        logger.info("%s: started (pid %s)", svc.name, svc.pid)

    def _schedule_retry(self, svc: Service) -> None:
        """Apply restart/backoff accounting after a failure."""
        if not self.wanted.get(svc.name, False):
            return
        limit = svc.backoff_limit
        if limit and svc.consecutive_failures >= limit:
            self.wanted[svc.name] = False
            logger.critical("%s: gave up after %s failures", svc.name, limit)
            return
        svc.restarts += 1
        svc.next_restart_at = time.monotonic() + svc.restart_delay
        logger.info(
            "%s: restarting in %.2fs (%s)",
            svc.name,
            svc.restart_delay,
            svc.consecutive_failures,
        )

    def _handle_exit(self, svc: Service, status: int) -> None:
        """React to a reaped child: restart it or mark it stopped."""
        uptime = time.monotonic() - svc.spawn_time
        if uptime >= MIN_UPTIME:
            svc.consecutive_failures = 0
        success = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        reason = f"{_describe_status(status)} after {uptime:.1f}s"
        svc.last_exit = reason
        svc.pid = 0
        svc.state = "stopped"
        svc.stop_deadline = 0.0

        if svc.pending_restart or self._should_restart(svc, success=success):
            svc.pending_restart = False
            svc.consecutive_failures += 1
            self._schedule_retry(svc)
            return
        logger.info("%s: stopped (%s)", svc.name, reason)

    def _should_restart(self, svc: Service, *, success: bool) -> bool:
        """Decide under the restart policy whether to bring the service back."""
        return self.wanted.get(svc.name, False) and (
            svc.restart == "always" or (svc.restart == "on_failure" and not success)
        )

    def _scan_children(self) -> None:
        """Reap every dead child through a nonblocking waitpid loop."""
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            svc = self._by_pid.pop(pid, None)
            if svc is None:
                logger.warning("reaped untracked child %s", pid)
                continue
            self._handle_exit(svc, status)

    def _kill_overdue(self) -> None:
        """SIGKILL services that ignored their stop timeout."""
        now = time.monotonic()
        for svc in self.services.values():
            if (
                svc.state == "stopping"
                and svc.stop_deadline
                and now >= svc.stop_deadline
            ):
                svc.stop_deadline = 0.0
                logger.warning("%s: stop timeout, SIGKILL", svc.name)
                with suppress(ProcessLookupError):
                    os.killpg(svc.pid, signal.SIGKILL)

    def _start_due(self) -> None:
        """Start services whose restart delay has elapsed."""
        now = time.monotonic()
        for svc in self.services.values():
            if (
                self.wanted.get(svc.name, False)
                and svc.pid == 0
                and svc.state == "stopped"
                and 0.0 < svc.next_restart_at <= now
            ):
                self._spawn_process(svc)

    def _all_stopped(self) -> bool:
        """Report whether every service has finished stopping."""
        return all(not svc.active for svc in self.services.values())

    # ------------------------------------------------------------------ #
    # public control actions
    # ------------------------------------------------------------------ #

    def stop_all(self) -> None:
        """Send SIGTERM to every running service (used on shutdown / stop all)."""
        now = time.monotonic()
        for svc in self.services.values():
            self.wanted[svc.name] = False
            svc.pending_restart = False
            if svc.active:
                svc.state = "stopping"
                svc.stop_deadline = now + svc.stop_timeout
                logger.info("%s: SIGTERM", svc.name)
                with suppress(ProcessLookupError):
                    os.killpg(svc.pid, signal.SIGTERM)

    def reload_config(self) -> str:
        """Re-read service definitions; add new services and start them."""
        loaded = ris_config.load_all(self.config_dir)
        added: list[str] = []
        for name in sorted(loaded):
            if name in self.services:
                continue
            self.services[name] = loaded[name]
            self.wanted[name] = loaded[name].run_at_boot
            added.append(name)
        if not added:
            return "ok (no changes)\n"
        for name in added:
            if self.wanted[name]:
                self._spawn_process(self.services[name])
            else:
                logger.info("%s: added, not started (run_at_boot=false)", name)
        return f"ok (added {', '.join(added)})\n"

    def start_service(self, name: str) -> str:
        """Start a single service by name."""
        svc = self.services.get(name)
        if svc is None:
            return f"error: unknown service '{name}'\n"
        self.wanted[name] = True
        if svc.active:
            return f"already running (pid {svc.pid})\n"
        svc.next_restart_at = 0.0
        self._spawn_process(svc)
        return f"started (pid {svc.pid or 0})\n"

    def stop_service(self, name: str) -> str:
        """Stop a single service by name."""
        svc = self.services.get(name)
        if svc is None:
            return f"error: unknown service '{name}'\n"
        self.wanted[name] = False
        svc.pending_restart = False
        if not svc.active:
            return "already stopped\n"
        svc.state = "stopping"
        svc.stop_deadline = time.monotonic() + svc.stop_timeout
        with suppress(ProcessLookupError):
            os.killpg(svc.pid, signal.SIGTERM)
        return f"stopping (pid {svc.pid})\n"

    def restart_service(self, name: str) -> str:
        """Restart a single service (stop, then bring it back up)."""
        svc = self.services.get(name)
        if svc is None:
            return f"error: unknown service '{name}'\n"
        self.wanted[name] = True
        if svc.active:
            svc.pending_restart = True
            svc.state = "stopping"
            svc.stop_deadline = time.monotonic() + svc.stop_timeout
            with suppress(ProcessLookupError):
                os.killpg(svc.pid, signal.SIGTERM)
            return f"restarting (pid {svc.pid})\n"
        svc.next_restart_at = 0.0
        self._spawn_process(svc)
        return f"started (pid {svc.pid or 0})\n"

    def status_text(self) -> str:
        """Render one line per service for the status command."""
        lines = []
        for name in sorted(self.services):
            svc = self.services[name]
            pid = svc.pid or "-"
            lines.append(
                f"{name} {pid} {svc.state} {svc.restarts} {svc.last_exit or '-'}",
            )
        return "\n".join(lines) + "\n"

    def handle_command(self, line: str) -> str:
        """Dispatch a single control command line to a response string."""
        parts = line.split()
        if not parts:
            return "error: empty command\n"
        action, *args = parts
        if action in {"status", "list"}:
            return self.status_text()
        if action in {"start", "stop", "restart"} and args:
            command_map = {
                "start": self.start_service,
                "stop": self.stop_service,
                "restart": self.restart_service,
            }
            return command_map[action](args[0])
        if action == "reload":
            return self.reload_config()
        return "error: unknown command\n"

    def _request_stop(self, _signum: int, _frame: object) -> None:
        """SIGTERM/SIGINT handler: begin the graceful shutdown."""
        logger.info("shutdown requested")
        self.stopping = True
        self.stop_all()

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #

    def _boot(self) -> None:
        """Load config and start every run_at_boot service."""
        self.services = ris_config.load_all(self.config_dir)
        if not self.services:
            logger.warning("no services found in %s", self.config_dir)
        for name, svc in self.services.items():
            self.wanted[name] = svc.run_at_boot
            if svc.run_at_boot:
                self._spawn_process(svc)
                self._start_due()  # in case exec failed and a retry is due now

    def run(self) -> None:
        """Drive the supervisor until every service is stopped after SIGTERM."""
        self._boot()
        self.control = ControlServer(self.socket_path, self.handle_command, self.sel)
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        while True:
            for key, _mask in self.sel.select(POLL_TIMEOUT):
                if key.data == "server":
                    self.control.accept_pending()
                else:
                    self.control.handle(key.fileobj)
            self._scan_children()
            self._kill_overdue()
            self._start_due()
            if self.stopping and self._all_stopped():
                break

        self.control.close()
        logger.info("all services stopped; exiting")
        sys.exit(0)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Build the command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="rissup", description="RIS service supervisor",
    )
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    Supervisor(args.config_dir, args.log_dir, args.socket, args.log).run()
