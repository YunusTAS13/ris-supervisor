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

"""The supervision state and runtime metadata of one service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

RESTART_POLICIES = frozenset({"always", "on_failure", "never"})
REDIRECT_MODES = frozenset({"log", "none", "devnull"})

# A service that stays up longer than this is considered "stable",
# and its consecutive-failure counter is reset on exit.
MIN_UPTIME = 5.0


@dataclass
class Service:
    """A supervised service: static config plus live run state."""

    name: str
    file_path: Path

    cmd: list[str] = field(default_factory=list)
    restart: str = "always"
    restart_delay: float = 1.0
    backoff_limit: int = 10  # 0 means unlimited retries
    stop_timeout: float = 10.0
    run_at_boot: bool = True
    chdir: str = "/"
    umask: int = 0o022
    user: str | None = None
    group: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    redirect: str = "log"  # log | none | devnull

    pid: int = 0
    state: str = "stopped"  # stopped | starting | running | stopping
    restarts: int = 0
    consecutive_failures: int = 0
    last_exit: str = ""
    spawn_time: float = 0.0
    next_restart_at: float = 0.0
    stop_deadline: float = 0.0
    pending_restart: bool = False

    @property
    def active(self) -> bool:
        """True while the process exists and we have not started stopping it."""
        return self.state in {"starting", "running", "stopping"}
