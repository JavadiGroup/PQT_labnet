"""The agent container's only way out: an allow-listing HTTPS (CONNECT) proxy.

    python -m labnet.automation_runner.egress_proxy --listen 0.0.0.0:3128 --allow /etc/labnet/egress-allow.txt

The agent container sits on an ``internal`` Docker network: no route out at
all. This proxy is attached to that network and to a normal one, and Claude
Code reaches Anthropic through it (``HTTPS_PROXY``). It forwards a connection
only when every one of these holds:

* the request is ``CONNECT host:443`` (plain-HTTP proxying, other methods and
  other ports are refused: everything that leaves is TLS to a named host);
* ``host`` is a DNS name, not an IP literal, and it is on the allow-list:
  exact names, or ``*.suffix`` lines that match names *under* the suffix;
* the proxy resolves the name itself and *every* address it gets is a public
  unicast address. A name that resolves to a private, loopback, link-local,
  multicast or reserved address is refused, so a DNS trick can't turn an
  allowed name into a way into the lab LAN. It then connects to an address it
  checked (not to the name again), so the answer can't change in between.

The request head is capped in size and time, idle tunnels are closed, and the
number of connections is capped. Every decision is logged on one line. The
proxy never sees inside the TLS: it only decides where a connection may go.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import re
import socket
import sys
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


log = logging.getLogger("labnet.egress")

MAX_HEADER_BYTES = 8 * 1024
HEADER_TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 10.0
RESOLVE_TIMEOUT_SECONDS = 10.0
# Claude Code's own watchdogs give up on a silent stream after 3 to 5 minutes.
IDLE_TIMEOUT_SECONDS = 600.0
MAX_CONNECTIONS = 64
ALLOWED_PORT = 443

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"(?:{_LABEL}\.)+{_LABEL}")

Resolver = Callable[[str, int], Awaitable[list[str]]]
Dialer = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class AllowListError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AllowList:
    exact: frozenset[str]
    suffixes: tuple[str, ...]  # ".example.com" for a "*.example.com" line

    @classmethod
    def parse(cls, text: str) -> "AllowList":
        exact: set[str] = set()
        suffixes: list[str] = []
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip().lower()
            if not line:
                continue
            wildcard = line.startswith("*.")
            name = line[2:] if wildcard else line
            if _HOST.fullmatch(name) is None or len(name) > 253 or _is_ip(name):
                raise AllowListError(f"line {number}: {raw.strip()!r} is not a host name or *.suffix")
            if wildcard:
                suffixes.append("." + name)
            else:
                exact.add(name)
        return cls(frozenset(exact), tuple(suffixes))

    @classmethod
    def load(cls, path: str | Path) -> "AllowList":
        return cls.parse(Path(path).read_text(encoding="utf-8"))

    def allows(self, host: str) -> bool:
        return host in self.exact or any(host.endswith(suffix) for suffix in self.suffixes)

    def __len__(self) -> int:
        return len(self.exact) + len(self.suffixes)


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip("[]"))
    except ValueError:
        return False
    return True


def is_public_address(address: str) -> bool:
    """A global unicast address: not private, loopback, link-local, multicast, reserved or unspecified."""

    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # The IPv4 address hidden in an IPv4-mapped, 6to4 or Teredo address decides too.
        for inner in (ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None):
            if inner is not None and not is_public_address(str(inner)):
                return False
    return not (
        not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


async def system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    # IPv4 first: the egress network usually has no IPv6 route.
    ordered = sorted(infos, key=lambda info: info[0] != socket.AF_INET)
    seen: list[str] = []
    for info in ordered:
        address = str(info[4][0])
        if address not in seen:
            seen.append(address)
    return seen


async def system_dial(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(address, port)


def _shown_target(head: bytes) -> str:
    """The request's method and target, printable and short, for the log line."""

    words = head.split(b"\r\n", 1)[0].split(b" ")[:2]
    text = " ".join(word.decode("ascii", "replace") for word in words)
    return "".join(c if c == " " or (c.isprintable() and not c.isspace()) else "?" for c in text)[:120] or "-"


class Refused(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


_STATUS_TEXT = {
    400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed", 408: "Request Timeout",
    431: "Request Header Fields Too Large", 502: "Bad Gateway", 503: "Service Unavailable",
}


class EgressProxy:
    """One proxy. ``serve(host, port)`` runs it; ``handle`` serves one client (tests drive it too)."""

    def __init__(
        self,
        allow: AllowList,
        *,
        resolve: Resolver = system_resolve,
        dial: Dialer = system_dial,
        max_connections: int = MAX_CONNECTIONS,
        header_timeout: float = HEADER_TIMEOUT_SECONDS,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        max_header_bytes: int = MAX_HEADER_BYTES,
    ) -> None:
        self.allow = allow
        self._resolve = resolve
        self._dial = dial
        self.max_connections = max_connections
        self.header_timeout = header_timeout
        self.idle_timeout = idle_timeout
        self.connect_timeout = connect_timeout
        self.max_header_bytes = max_header_bytes
        self.active = 0

    async def start(self, host: str, port: int) -> asyncio.base_events.Server:
        # The stream limit makes readuntil() give up on an oversized head.
        return await asyncio.start_server(self.handle, host, port, limit=self.max_header_bytes)

    async def serve(self, host: str, port: int) -> None:
        server = await self.start(host, port)
        names = ", ".join(str(s.getsockname()[:2]) for s in server.sockets)
        log.info("egress proxy listening on %s with %d allow-list entries", names, len(self.allow))
        async with server:
            await server.serve_forever()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        client = peer[0] if isinstance(peer, tuple) and peer else "?"
        try:
            if self.active >= self.max_connections:
                log.warning("DENY client=%s reason=too many connections (%d)", client, self.active)
                await self._refuse(writer, 503, "too many connections")
                return
            self.active += 1
            try:
                await self._handle(reader, writer, client)
            finally:
                self.active -= 1
        finally:
            # Every path closes the client socket, the refusals included; otherwise it stays half-open.
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client: str) -> None:
        target = "-"
        try:
            head = await self._read_head(reader)
            target = _shown_target(head)
            target, host = self._check_request(head)
            address = await self._check_resolution(host)
            upstream = await self._connect(address)
        except Refused as refusal:
            log.warning("DENY client=%s target=%s status=%d reason=%s", client, target, refusal.status, refusal.reason)
            await self._refuse(writer, refusal.status, refusal.reason)
            return
        log.info("ALLOW client=%s target=%s address=%s", client, target, address)
        up_reader, up_writer = upstream
        try:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await self._pipe(reader, writer, up_reader, up_writer)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            up_writer.close()
            try:
                await up_writer.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass

    async def _read_head(self, reader: asyncio.StreamReader) -> bytes:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.header_timeout)
        except asyncio.TimeoutError:
            raise Refused(408, "no complete request head in time") from None
        except asyncio.LimitOverrunError:
            raise Refused(431, f"request head larger than {self.max_header_bytes} bytes") from None
        except asyncio.IncompleteReadError:
            raise Refused(400, "connection closed before the request head ended") from None
        if len(head) > self.max_header_bytes:
            raise Refused(431, f"request head larger than {self.max_header_bytes} bytes")
        return head

    def _check_request(self, head: bytes) -> tuple[str, str]:
        try:
            line = head.split(b"\r\n", 1)[0].decode("ascii")
        except UnicodeDecodeError:
            raise Refused(400, "request line is not ASCII") from None
        parts = line.split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            raise Refused(400, "malformed request line")
        method, target, _version = parts
        shown = target[:100]
        if method != "CONNECT":
            raise Refused(403, f"method {method[:16]} (only CONNECT to port {ALLOWED_PORT})")
        host, sep, port = target.rpartition(":")
        if not sep or not host:
            raise Refused(403, f"target {shown} has no port")
        if port != str(ALLOWED_PORT):
            raise Refused(403, f"port {port[:8]} (only {ALLOWED_PORT})")
        host = host.lower().removesuffix(".")
        if _is_ip(host):
            raise Refused(403, "IP address targets are not allowed; use a host name")
        if _HOST.fullmatch(host) is None or len(host) > 253:
            raise Refused(403, "not a valid host name")
        if not self.allow.allows(host):
            raise Refused(403, f"{host} is not on the allow-list")
        return f"{host}:{port}", host

    async def _check_resolution(self, host: str) -> str:
        try:
            addresses = await asyncio.wait_for(self._resolve(host, ALLOWED_PORT), RESOLVE_TIMEOUT_SECONDS)
        except (OSError, asyncio.TimeoutError) as exc:
            raise Refused(502, f"cannot resolve {host}: {exc or type(exc).__name__}") from None
        if not addresses:
            raise Refused(502, f"{host} has no addresses")
        private = [address for address in addresses if not is_public_address(address)]
        if private:
            raise Refused(403, f"{host} resolves to a non-public address ({', '.join(private[:3])})")
        return addresses[0]

    async def _connect(self, address: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(self._dial(address, ALLOWED_PORT), self.connect_timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise Refused(502, f"cannot connect to {address}: {exc or type(exc).__name__}") from None

    async def _pipe(self, a_reader, a_writer, b_reader, b_writer) -> None:
        async def copy(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await asyncio.wait_for(source.read(64 * 1024), self.idle_timeout)
                    if not data:
                        break
                    sink.write(data)
                    await sink.drain()
            finally:
                if sink.can_write_eof():
                    try:
                        sink.write_eof()
                    except OSError:
                        pass

        tasks = [asyncio.ensure_future(copy(a_reader, b_writer)), asyncio.ensure_future(copy(b_reader, a_writer))]
        try:
            # Either side going idle (TimeoutError) or failing ends the tunnel; a clean
            # end of one direction waits for the other.
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _refuse(self, writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        body = f"LabNet egress proxy: {reason}\n".encode("utf-8", "replace")
        head = (
            f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'Error')}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        try:
            writer.write(head + body)
            await writer.drain()
        except OSError:
            pass


def parse_listen(text: str) -> tuple[str, int]:
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit() or not 0 < int(port) < 65536:
        raise argparse.ArgumentTypeError("--listen takes host:port, e.g. 0.0.0.0:3128")
    return host.strip("[]") or "0.0.0.0", int(port)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="labnet-egress-proxy", description=__doc__.split("\n\n")[0])
    parser.add_argument("--listen", type=parse_listen, default=("0.0.0.0", 3128))
    parser.add_argument("--allow", required=True, help="the allow-list file (one host or *.suffix per line)")
    parser.add_argument("--max-connections", type=int, default=MAX_CONNECTIONS)
    parser.add_argument("--idle-timeout", type=float, default=IDLE_TIMEOUT_SECONDS)
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    try:
        allow = AllowList.load(args.allow)
    except (OSError, AllowListError) as exc:
        log.error("cannot use the allow-list %s: %s", args.allow, exc)
        return 2
    if not len(allow):
        log.error("the allow-list %s is empty; refusing to start a proxy that allows nothing", args.allow)
        return 2
    proxy = EgressProxy(allow, max_connections=args.max_connections, idle_timeout=args.idle_timeout)
    try:
        asyncio.run(proxy.serve(*args.listen))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["AllowList", "AllowListError", "EgressProxy", "is_public_address", "main"]
