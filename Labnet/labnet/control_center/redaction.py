"""Remove credentials from text that leaves the Control Center.

A lease id is enough to drive a device until the lease ends, and errors from
a Lab PC or Central can quote one (or a key). Anything shown in a browser,
sent to a script or returned to a runner goes through :func:`redact` first.
"""

from __future__ import annotations

import re


_SECRETS = re.compile(
    r"lease_[0-9a-fA-F]{32}"  # Central's lease ids
    r"|labnet_[A-Za-z0-9_-]{20,}"  # Central's API keys
    r"|lnr_[A-Za-z0-9_-]{20,}"  # Automation runner tokens
)


def _hide(match: re.Match[str]) -> str:
    return match.group(0).split("_", 1)[0] + "_…"


def redact(text: str) -> str:
    """``text`` with every lease id, API key and runner token replaced by ``<prefix>_…``."""

    return _SECRETS.sub(_hide, text)


__all__ = ["redact"]
