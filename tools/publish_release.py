from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload an immutable LabNet release bundle to Central."
    )
    parser.add_argument("--central-url", required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--promote", action="store_true")
    return parser


def _request(
    url: str,
    api_key: str,
    method: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    context: ssl.SSLContext,
) -> dict:
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": content_type,
        },
    )
    try:
        with urlopen(request, context=context, timeout=600) as response:
            payload = response.read()
    except HTTPError as exc:
        raise SystemExit(f"Central rejected the request: {exc.read().decode()}") from exc
    parsed = json.loads(payload or b"{}")
    if not isinstance(parsed, dict):
        raise SystemExit("Central returned an invalid response.")
    return parsed


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    base = args.central_url.rstrip("/") + "/"
    parsed_base = urlparse(base)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.hostname:
        raise SystemExit("--central-url must be an absolute HTTP(S) URL.")
    if parsed_base.scheme == "http" and not _loopback(parsed_base.hostname):
        raise SystemExit("A non-loopback Central URL must use HTTPS.")
    api_key = args.api_key_file.read_text(encoding="utf-8").strip()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.artifact.name != manifest.get("artifact_filename"):
        raise SystemExit("Artifact filename does not match the manifest.")
    context = ssl.create_default_context(
        cafile=str(args.ca_file) if args.ca_file else None
    )
    created = _request(
        urljoin(base, "api/v1/releases"),
        api_key,
        "POST",
        body=json.dumps({"manifest": manifest}).encode("utf-8"),
        context=context,
    )
    release_id = created["release_id"]
    uploaded = _upload_file(
        urljoin(base, created["upload_url"].lstrip("/")),
        api_key,
        args.artifact,
        context=context,
    )
    result = uploaded
    if args.promote:
        result = _request(
            urljoin(base, f"api/v1/releases/{release_id}/promote"),
            api_key,
            "POST",
            context=context,
        )
    print(json.dumps(result, indent=2))


def _upload_file(
    url: str,
    api_key: str,
    path: Path,
    *,
    context: ssl.SSLContext,
) -> dict:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            context=context,
            timeout=600,
        )
    elif parsed.scheme == "http":
        connection = http.client.HTTPConnection(
            parsed.hostname, parsed.port or 80, timeout=600
        )
    else:
        raise SystemExit("Central URL must use HTTP or HTTPS.")
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    connection.putrequest("PUT", target)
    connection.putheader("Authorization", f"Bearer {api_key}")
    connection.putheader("Accept", "application/json")
    connection.putheader("Content-Type", "application/zip")
    connection.putheader("Content-Length", str(path.stat().st_size))
    connection.endheaders()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                connection.send(chunk)
        response = connection.getresponse()
        payload = response.read()
    finally:
        connection.close()
    if response.status >= 400:
        raise SystemExit(f"Central rejected the upload: {payload.decode()}")
    parsed_payload = json.loads(payload or b"{}")
    if not isinstance(parsed_payload, dict):
        raise SystemExit("Central returned an invalid upload response.")
    return parsed_payload


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if __name__ == "__main__":
    main()
