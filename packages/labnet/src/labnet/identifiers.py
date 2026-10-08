"""Shared wire-identifier contract used by every LabNet process."""

from __future__ import annotations

import re


LABNET_IDENTIFIER_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"
LABNET_IDENTIFIER_REGEX = re.compile(rf"^(?:{LABNET_IDENTIFIER_PATTERN})$")


def validate_labnet_identifier(value: object, *, name: str = "identifier") -> str:
    """Return a valid identifier without silently normalizing wire identity."""

    if not isinstance(value, str) or LABNET_IDENTIFIER_REGEX.fullmatch(value) is None:
        raise ValueError(
            f"{name} must be 1-128 characters using letters, numbers, '.', "
            "'_', ':', or '-', and must start with a letter or number."
        )
    return value


__all__ = [
    "LABNET_IDENTIFIER_PATTERN",
    "LABNET_IDENTIFIER_REGEX",
    "validate_labnet_identifier",
]
