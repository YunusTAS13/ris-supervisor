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

"""risctl: command line client for the ris-supervisor control socket."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

DEFAULT_SOCKET = Path("/var/run/ris-svc.sock")


def send_command(socket_path: Path, command: str) -> str:
    """Open the control socket, send one command, and return everything back."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(10)
        conn.connect(str(socket_path))
        conn.sendall(command.encode() + b"\n")
        chunks: list[bytes] = []
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode(errors="replace")


def main(argv: list[str]) -> int:
    """Risctl entry point: parse args, send command, print result."""
    parser = argparse.ArgumentParser(
        prog="risctl", description="RIS service control client",
    )
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument(
        "command", choices=["status", "list", "stop", "start", "restart", "reload"],
    )
    parser.add_argument("service", nargs="?")
    args = parser.parse_args(argv)

    if args.command in {"start", "stop", "restart"} and not args.service:
        parser.error(f"'{args.command}' needs a service name")

    command = args.command if args.service is None else f"{args.command} {args.service}"
    try:
        print(send_command(args.socket, command), end="")  # noqa: T201
    except OSError as e:
        print(  # noqa: T201
            f"risctl: cannot reach supervisor via {args.socket}: {e}", file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
