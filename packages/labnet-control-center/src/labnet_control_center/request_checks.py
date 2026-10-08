"""Checks shared by the APIs people use: who is asking, and is it really our page.

* :func:`request_user` is the person Caddy signed in (see ``ingress.py``), or
  None in development, where there are no logins.
* :func:`author` is the name recorded as "created by" / "updated by": the
  signed-in person when there is one, otherwise the name typed on the page.
* :func:`same_origin` and :func:`read_json` keep other web sites from making
  changes: a browser says where a request came from (``Sec-Fetch-Site``), and
  a cross-site form can't send ``application/json``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from .experiment_schema import clean_author
from .experiment_store import ExperimentError


# Where HumanIngress puts the signed-in person's name in the ASGI scope.
USER_SCOPE_KEY = "labnet.user"
MAX_JSON_BYTES = 256 * 1024


def request_user(request: Request) -> str | None:
    """The signed-in person's name (from Caddy), or None without logins."""

    return request.scope.get(USER_SCOPE_KEY)


def author(request: Request, data: dict[str, Any], key: str) -> str:
    """The signed-in person, or else the (optional) name typed on the page."""

    user = request_user(request)
    if user is not None:
        return user
    try:
        return clean_author(data.get(key, ""))
    except ValueError as exc:
        raise ExperimentError(422, f"{key}: {exc}") from None


def cross_site(site: str | None) -> bool:
    """True when a browser says the request came from another site."""

    return site is not None and site not in ("same-origin", "none")


def same_origin(request: Request) -> None:
    """Refuse a change a browser says came from another site."""

    if cross_site(request.headers.get("sec-fetch-site")):
        raise ExperimentError(403, "Changes must come from the Control Center's own pages.")


async def read_json(
    request: Request,
    *,
    limit: int = MAX_JSON_BYTES,
    not_object: str = "Send a JSON object.",
) -> dict[str, Any]:
    """The request's JSON object, read with a size cap. Anything but application/json is refused."""

    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise ExperimentError(415, "Send JSON (Content-Type: application/json).")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise ExperimentError(413, "The request is too large.")
    try:
        data = json.loads(body or b"null")
    except (ValueError, RecursionError):  # RecursionError: nested thousands deep
        raise ExperimentError(422, "The request is not valid JSON.") from None
    if not isinstance(data, dict):
        raise ExperimentError(422, not_object)
    return data


__all__ = ["MAX_JSON_BYTES", "USER_SCOPE_KEY", "author", "cross_site", "read_json", "request_user", "same_origin"]
