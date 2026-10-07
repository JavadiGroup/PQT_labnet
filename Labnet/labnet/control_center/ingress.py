"""Only people who signed in through Caddy reach the Control Center.

In the lab, Caddy asks each person for their own login (``basic_auth``) and
adds two headers to every request it forwards::

    X-LabNet-Proxy: <LABNET_CONTROL_CENTER_PROXY_SECRET>
    X-LabNet-User:  <the login name>

With the secret configured, :class:`HumanIngress` refuses any request without
it, so a program on the Control Center PC that dials ``127.0.0.1:8080``
directly gets nowhere, and the user name can be trusted: it becomes the
author of experiments and projects (and, later, the approver of protocols).
Without the secret (development on one computer) there are no logins and the
header is ignored, since anyone could send it.

Exempt: the health checks, and the Automation runner's API, whose requests
prove themselves with a runner token instead (see ``automation_runner_api.py``).

Whatever the mode, a change (POST, PUT, PATCH, DELETE) that a browser says
came from another web site is refused, so a page elsewhere can't pause a
device or start a script through someone's open session.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from .experiment_schema import clean_author
from .request_checks import USER_SCOPE_KEY, cross_site


PROXY_HEADER = "x-labnet-proxy"
USER_HEADER = "x-labnet-user"
RUNNER_PREFIX = "/api/automation/runner/"
EXEMPT_PREFIXES = ("/health/", RUNNER_PREFIX)
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _refuse(message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=403, headers={"Cache-Control": "no-store"})


class HumanIngress:
    """Pure ASGI: check the proxy secret, record the user, refuse cross-site changes.

    Websockets are closed at once: the app has none.
    """

    def __init__(self, app: Any, *, secret: str | None) -> None:
        self.app = app
        self._secret = secret.encode("utf-8") if secret else None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "websocket":  # the app has none; never a way around the checks
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        path: str = scope["path"]
        exempt = path.startswith(EXEMPT_PREFIXES)
        scope[USER_SCOPE_KEY] = None
        if self._secret is not None and not exempt:
            supplied = headers.get(PROXY_HEADER, "").encode("utf-8", "replace")
            if not hmac.compare_digest(supplied, self._secret):
                await _refuse("Open the Control Center through its sign-in address (Caddy).")(scope, receive, send)
                return
            try:
                user = clean_author(headers.get(USER_HEADER, ""))
            except ValueError:
                user = ""
            if not user:
                await _refuse("The sign-in proxy sent no usable user name.")(scope, receive, send)
                return
            scope[USER_SCOPE_KEY] = user
        if (
            scope["method"] in UNSAFE_METHODS
            and not path.startswith(RUNNER_PREFIX)
            and cross_site(headers.get("sec-fetch-site"))
        ):
            await _refuse("Changes must come from the Control Center's own pages.")(scope, receive, send)
            return
        await self.app(scope, receive, send)


__all__ = ["EXEMPT_PREFIXES", "HumanIngress", "PROXY_HEADER", "RUNNER_PREFIX", "USER_HEADER"]
