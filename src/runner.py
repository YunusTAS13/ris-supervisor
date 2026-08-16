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

"""spawn a service: fork, setsid, redirect stdio, exec.

The child reports exec failures back to the parent through a pipe so
the supervisor can log a useful reason instead of a silent child death.
"""

from __future__ import annotations

import grp
import os
import pwd
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from service import Service

EXEC_FAILED = 127


def _setup_child(svc: Service, log_path: Path) -> None:
    """Configure the child process before exec: session, user, stdio.

    Stdio handling follows svc.redirect:
      - "log":    stdin -> /dev/null, stdout/stderr -> log file
      - "none":   keep inherited console fds (for interactive services)
      - "devnull": all of stdin/stdout/stderr -> /dev/null
    """
    os.setsid()
    os.umask(svc.umask)
    if svc.chdir:
        os.chdir(svc.chdir)
    for key, value in svc.environment.items():
        os.environ[key] = value
    if svc.group is not None:
        os.setgid(grp.getgrnam(svc.group).gr_gid)
    if svc.user is not None:
        os.setuid(pwd.getpwnam(svc.user).pw_uid)

    if svc.redirect == "none":
        return

    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    if svc.redirect == "log":
        target = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(target, 1)
        os.dup2(target, 2)
        os.close(target)
    else:  # devnull
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    os.close(devnull)


def spawn(svc: Service, log_path: Path) -> tuple[int, None] | tuple[None, str]:
    """Fork and exec svc.cmd, returning (pid, None) or (None, error_str)."""
    read_fd, write_fd = os.pipe()  # pipe fds are CLOEXEC by default (PEP 446)

    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            _setup_child(svc, log_path)
            os.execvp(svc.cmd[0], svc.cmd)
        except Exception as e:  # noqa: BLE001
            with suppress(OSError):
                os.write(write_fd, str(e).encode())
            os._exit(EXEC_FAILED)

    os.close(write_fd)
    error = os.read(read_fd, 4096)
    os.close(read_fd)
    if error:
        return None, error.decode().strip()
    return pid, None
