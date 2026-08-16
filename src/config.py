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

"""Parse service definitions from /etc/ris/services/*.service files."""

from __future__ import annotations

import logging
import shlex
from typing import TYPE_CHECKING

from service import RESTART_POLICIES, Service

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_KNOWN_KEYS = frozenset(
    {
        "exec",
        "restart",
        "restart_delay",
        "backoff_limit",
        "stop_timeout",
        "run_at_boot",
        "chdir",
        "umask",
        "user",
        "group",
        "environment",
    },
)


def _parse_bool(value: str) -> bool:
    """Parse a boolean value from a config string."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_env(value: str) -> dict[str, str]:
    """Parse whitespace separated KEY=VAL pairs into a dict."""
    env: dict[str, str] = {}
    for chunk in value.split():
        key, _, val = chunk.partition("=")
        if key:
            env[key] = val
    return env


def _parse_service(path: Path) -> Service:
    """Parse a single .service file into a Service object."""
    values: dict[str, str] = {}
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(("#", "[")):
                continue
            key, _, value = line.partition("=")
            values[key.strip().lower().replace("-", "_")] = value.strip()

    for key in sorted(set(values) - _KNOWN_KEYS):
        logger.warning("%s: unknown key '%s'", path.name, key)

    cmd = shlex.split(values.get("exec", ""))
    if not cmd:
        raise ValueError(  # noqa: TRY003
            f"missing 'exec' in {path.name}",  # noqa: EM102
        )
    restart = values.get("restart", "always")
    if restart not in RESTART_POLICIES:
        raise ValueError(  # noqa: TRY003
            f"invalid restart policy: {restart}",  # noqa: EM102
        )

    return Service(
        name=path.stem,
        file_path=path,
        cmd=cmd,
        restart=restart,
        restart_delay=float(values.get("restart_delay", "1")),
        backoff_limit=int(values.get("backoff_limit", "10")),
        stop_timeout=float(values.get("stop_timeout", "10")),
        run_at_boot=_parse_bool(values.get("run_at_boot", "true")),
        chdir=values.get("chdir", "/"),
        umask=int(values.get("umask", "022"), 8),
        user=values.get("user") or None,
        group=values.get("group") or None,
        environment=_parse_env(values.get("environment", "")),
    )


def load_all(config_dir: Path) -> dict[str, Service]:
    """Load every *.service file in config_dir into a name->Service map.

    Files that fail to parse are logged and skipped.
    """
    services: dict[str, Service] = {}
    for path in sorted(config_dir.glob("*.service")):
        try:
            svc = _parse_service(path)
        except (OSError, ValueError) as e:
            logger.error("failed to load %s: %s", path, e)
            continue
        services[svc.name] = svc
    return services
