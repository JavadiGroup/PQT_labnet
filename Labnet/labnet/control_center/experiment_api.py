"""The experiments API: list, create, read, save, delete, check, export.

Every reply that carries an experiment also carries its report (see
``experiment_checks.py``), so the canvas always shows what would stop an
export. Saving needs only a well-formed experiment; export needs a clean one.

Changes (create, check, save, delete) must come from the Control Center's own
pages: a browser's ``Sec-Fetch-Site`` must not say cross-site, and bodies must
be ``application/json``, which a form on another site cannot send (see
``request_checks.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from labnet.grpc.generated.device_contracts import DEVICE_CONTRACTS

from .contracts import describe_device_type, facade_class_name, type_label
from .experiment_checks import check_experiment
from .experiment_schema import (
    LINK_TYPES,
    MAX_INSTRUMENTS,
    MAX_LINKS,
    MAX_NODES,
    MAX_POINTS,
    OPTIC_KINDS,
    ExperimentContent,
    json_schema,
    parse_content,
)
from .experiment_script import ScriptError, generate_script
from .experiment_store import ExperimentError, ExperimentStore, VersionConflict
from .request_checks import author, read_json, same_origin


NetworkSource = Callable[[], Awaitable[dict[str, Any]]]
# Keys the server manages; a client may send them back, and they are ignored.
_SERVER_FIELDS = {"schema_version", "id", "version", "created_at", "updated_at", "created_by", "updated_by"}
MAX_BODY_BYTES = 512 * 1024
_CHANGES = [Depends(same_origin)]


def _devices(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        device["id"]: {**device, "lab_pc": pc["id"], "lab_pc_online": pc["online"]}
        for pc in snapshot.get("lab_pcs", [])
        for device in pc["devices"]
    }


def _fill_device_types(data: dict[str, Any], devices: dict[str, dict[str, Any]]) -> None:
    """An instrument sent without device_type (e.g. by Photo-Agent) takes the live one."""

    for node in data.get("nodes") or []:
        if (
            isinstance(node, dict) and node.get("kind") == "instrument" and not node.get("device_type")
            and isinstance(node.get("device_id"), str) and node["device_id"] in devices
        ):
            node["device_type"] = devices[node["device_id"]]["type"]


async def _body(request: Request) -> dict[str, Any]:
    return await read_json(request, limit=MAX_BODY_BYTES, not_object="Send an experiment as a JSON object.")


def create_experiment_router(network: NetworkSource) -> APIRouter:
    router = APIRouter(prefix="/api/experiments")

    def store(request: Request) -> ExperimentStore:
        return request.app.state.experiments

    async def devices() -> dict[str, dict[str, Any]] | None:
        try:
            snapshot = await network()
        except Exception:  # the report just can't mention the network
            return None
        return _devices(snapshot) if snapshot.get("lab_pcs") or snapshot["central"]["ok"] else None

    async def content_or_422(data: dict[str, Any], live: dict[str, dict[str, Any]] | None) -> ExperimentContent:
        fields = {key: value for key, value in data.items() if key not in _SERVER_FIELDS}
        _fill_device_types(fields, live or {})
        content, problems = parse_content(fields)
        if content is None:
            raise ExperimentError(422, problems[0]["message"] if len(problems) == 1 else
                                  f"The experiment has {len(problems)} problems.", errors=problems)
        return content

    def reply(experiment: dict[str, Any], live: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
        content = ExperimentContent.model_validate(
            {key: experiment[key] for key in ("name", "description", "run", "nodes", "links")}
        )
        return {"experiment": experiment, "report": check_experiment(content, live)}

    @router.get("")
    async def list_experiments(request: Request) -> dict[str, Any]:
        summaries, unreadable = await asyncio.to_thread(store(request).list)
        return {"experiments": summaries, "unreadable": unreadable}

    @router.get("/meta")
    async def meta() -> dict[str, Any]:
        """What the canvas needs before any device is online: components, types, limits."""

        return {
            "optics": [{"kind": kind, "label": label} for kind, label in OPTIC_KINDS.items()],
            "link_types": list(LINK_TYPES),
            "device_types": {
                contract.device_type: {
                    "label": type_label(contract.device_type),
                    "python_class": facade_class_name(contract),
                    "methods": describe_device_type(contract.device_type),
                }
                for contract in DEVICE_CONTRACTS
            },
            "limits": {
                "nodes": MAX_NODES,
                "instruments": MAX_INSTRUMENTS,
                "links": MAX_LINKS,
                "points": MAX_POINTS,
            },
            "json_schema": json_schema(),
        }

    @router.post("/validate", dependencies=_CHANGES)
    async def validate(request: Request) -> dict[str, Any]:
        """Check an experiment without saving it (Photo-Agent proposals). Always 200."""

        data = await _body(request)
        live = await devices()
        fields = {key: value for key, value in data.items() if key not in _SERVER_FIELDS}
        _fill_device_types(fields, live or {})
        content, problems = parse_content(fields)
        if content is None:
            return {
                "experiment": None,
                "report": {"exportable": False, "errors": problems, "warnings": [], "points": 0,
                           "estimated_seconds": 0},
            }
        return {"experiment": content.model_dump(mode="json"), "report": check_experiment(content, live)}

    @router.post("", status_code=201, dependencies=_CHANGES)
    async def create(request: Request) -> JSONResponse:
        data = await _body(request)
        live = await devices()
        content = await content_or_422(data, live)
        experiment = await asyncio.to_thread(store(request).create, content, author=author(request, data, "created_by"))
        return JSONResponse(
            reply(experiment, live), status_code=201, headers={"Location": f"/experiments/{experiment['id']}"}
        )

    @router.get("/{experiment_id}")
    async def get(request: Request, experiment_id: str) -> dict[str, Any]:
        experiment = await asyncio.to_thread(store(request).get, experiment_id)
        return reply(experiment, await devices())

    @router.put("/{experiment_id}", dependencies=_CHANGES)
    async def save(request: Request, experiment_id: str) -> dict[str, Any]:
        data = await _body(request)
        version = data.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ExperimentError(422, "Send the version this edit is based on.")
        if data.get("id", experiment_id) != experiment_id:
            raise ExperimentError(422, "The experiment's id doesn't match the address.")
        live = await devices()
        content = await content_or_422(data, live)
        experiment = await asyncio.to_thread(
            store(request).update, experiment_id, content, base_version=version, author=author(request, data, "updated_by")
        )
        return reply(experiment, live)

    @router.delete("/{experiment_id}", status_code=204, dependencies=_CHANGES)
    async def delete(request: Request, experiment_id: str, version: int | None = None) -> Response:
        await asyncio.to_thread(store(request).delete, experiment_id, version=version)
        return Response(status_code=204)

    @router.get("/{experiment_id}/script")
    async def script(request: Request, experiment_id: str, version: int | None = None) -> Response:
        """The Python script that runs this experiment, as a download.

        ``version`` (what the page shows) guards against exporting something
        else: a newer version saved elsewhere answers 409.
        """

        experiment = await asyncio.to_thread(store(request).get, experiment_id)
        if version is not None and experiment["version"] != version:
            raise VersionConflict(experiment)
        try:
            source = generate_script(experiment)
        except ScriptError as exc:
            raise ExperimentError(422, "Fix these problems to export a script.", errors=exc.errors) from None
        filename = f"{experiment['run']['output_name']}.py"
        return Response(
            source,
            media_type="text/x-python; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
        )

    return router


__all__ = ["create_experiment_router"]
