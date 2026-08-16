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

"""A small line-based unix socket control server for risctl.

Commands arrive as newline terminated strings, one per connection,
and the response is echoed back before the connection is closed.
"""

from __future__ import annotations

import logging
import selectors
import socket
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)
MAX_LINE = 8192


class ControlServer:
    """Transport for the supervisor control socket."""

    def __init__(
        self,
        path: Path,
        on_command: Callable[[str], str],
        sel: selectors.BaseSelector,
    ) -> None:
        """Bind the socket and register it with the supervisor selector."""
        self.path = path
        self.on_command = on_command
        self.sel = sel
        self._buffers: dict[socket.socket, bytes] = {}

        with suppress(FileNotFoundError):
            path.unlink()

        self.sock = socket.socket(
            socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK,
        )
        self.sock.bind(str(path))
        self.sock.listen(8)
        self.sel.register(self.sock, selectors.EVENT_READ, "server")
        logger.info("control socket listening on %s", path)

    def accept_pending(self) -> None:
        """Accept every pending connection and register it."""
        while True:
            try:
                conn, _addr = self.sock.accept()
            except BlockingIOError:
                return
            conn.setblocking(False)  # noqa: FBT003
            self._buffers[conn] = b""
            self.sel.register(conn, selectors.EVENT_READ, "conn")

    def _respond(self, conn: socket.socket, response: str) -> None:
        """Send the response line and close the connection."""
        try:
            conn.setblocking(True)  # noqa: FBT003
            conn.sendall(response.encode())
        except OSError as e:
            logger.warning("failed to send response: %s", e)
        finally:
            self._close(conn)

    def _close(self, conn: socket.socket) -> None:
        """Unregister and close a client connection."""
        self.sel.unregister(conn)
        self._buffers.pop(conn, None)
        conn.close()

    def handle(self, conn: socket.socket) -> None:
        """Consume available bytes and act when a full command line is seen."""
        try:
            chunk = conn.recv(MAX_LINE)
        except BlockingIOError:
            return
        if not chunk:
            self._close(conn)
            return

        self._buffers[conn] = self._buffers.get(conn, b"") + chunk
        buff = self._buffers[conn]
        newline = buff.find(b"\n")
        if newline == -1:
            if len(buff) > MAX_LINE:
                logger.warning("oversized command dropped")
                self._close(conn)
            return

        command = buff[:newline].decode(errors="replace")
        self._buffers.pop(conn, None)
        logger.info("control command: %s", command)
        try:
            response = self.on_command(command.strip())
        except Exception as e:
            logger.exception("command handler failed")
            response = f"error: {e}\n"
        self._respond(conn, response)

    def close(self) -> None:
        """Close the server and all client sockets."""
        for conn in list(self._buffers):
            self._close(conn)
        if self.sock is not None:
            self.sel.unregister(self.sock)
            self.sock.close()
        with suppress(FileNotFoundError):
            self.path.unlink()
