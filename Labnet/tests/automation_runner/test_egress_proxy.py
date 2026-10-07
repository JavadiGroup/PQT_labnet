"""The egress proxy: CONNECT to allow-listed names on 443 only, public addresses only, bounded."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from labnet.automation_runner import egress_proxy
from labnet.automation_runner.egress_proxy import AllowList, AllowListError, EgressProxy, is_public_address


REPO = Path(__file__).resolve().parents[3]
PUBLIC = "160.79.104.10"  # stands in for api.anthropic.com's address


@pytest.fixture
async def echo():
    """A local TCP echo server: what an allowed tunnel reaches instead of the real host."""

    async def serve(reader, writer):
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


def make_proxy(echo_port: int, addresses: dict[str, list[str]] | None = None, **kwargs) -> EgressProxy:
    table = {"api.anthropic.com": [PUBLIC], "platform.claude.com": [PUBLIC], "x.files.example.org": [PUBLIC]}
    table.update(addresses or {})
    dialled: list[tuple[str, int]] = []

    async def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError("no such host")
        return table[host]

    async def dial(address: str, port: int):
        dialled.append((address, port))
        return await asyncio.open_connection("127.0.0.1", echo_port)

    allow = AllowList.parse("api.anthropic.com\nplatform.claude.com  # token refresh\n*.files.example.org\n")
    proxy = EgressProxy(allow, resolve=resolve, dial=dial, **kwargs)
    proxy.dialled = dialled  # type: ignore[attr-defined]
    return proxy


@pytest.fixture
async def started(echo):
    servers = []

    async def start(**kwargs):
        proxy = make_proxy(echo, **kwargs)
        server = await proxy.start("127.0.0.1", 0)
        servers.append(server)
        return proxy, server.sockets[0].getsockname()[1]

    yield start
    for server in servers:
        server.close()
        await server.wait_closed()


async def exchange(port: int, request: bytes) -> tuple[bytes, asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    return head, reader, writer


async def status_of(port: int, request: bytes) -> int:
    head, _reader, writer = await exchange(port, request)
    writer.close()
    return int(head.split(b" ")[1])


async def test_allowed_connect_tunnels_bytes_both_ways(started, caplog):
    caplog.set_level(logging.INFO, logger="labnet.egress")
    proxy, port = await started()
    head, reader, writer = await exchange(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com:443\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    writer.write(b"\x16\x03\x01 pretend TLS hello")
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(len(b"\x16\x03\x01 pretend TLS hello")), 5) == b"\x16\x03\x01 pretend TLS hello"
    writer.close()
    assert proxy.dialled == [(PUBLIC, 443)]  # the checked address, not the name again
    assert any("ALLOW" in r.getMessage() and "api.anthropic.com:443" in r.getMessage() for r in caplog.records)


async def test_bytes_sent_with_the_head_are_forwarded(started):
    _proxy, port = await started()
    head, reader, writer = await exchange(port, b"CONNECT API.Anthropic.com.:443 HTTP/1.1\r\n\r\nearly")
    assert head.startswith(b"HTTP/1.1 200")
    assert await asyncio.wait_for(reader.readexactly(5), 5) == b"early"
    writer.close()


async def test_wildcard_matches_names_under_the_suffix_only(started):
    _proxy, port = await started(addresses={"files.example.org": [PUBLIC], "evilfiles.example.org": [PUBLIC]})
    assert await status_of(port, b"CONNECT x.files.example.org:443 HTTP/1.1\r\n\r\n") == 200
    assert await status_of(port, b"CONNECT files.example.org:443 HTTP/1.1\r\n\r\n") == 403
    assert await status_of(port, b"CONNECT evilfiles.example.org:443 HTTP/1.1\r\n\r\n") == 403


@pytest.mark.parametrize("request_bytes", [
    b"GET http://api.anthropic.com/ HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n",  # plain HTTP proxying
    b"POST https://api.anthropic.com/v1/messages HTTP/1.1\r\n\r\n",
    b"CONNECT api.anthropic.com:80 HTTP/1.1\r\n\r\n",  # another port
    b"CONNECT api.anthropic.com:8443 HTTP/1.1\r\n\r\n",
    b"CONNECT api.anthropic.com HTTP/1.1\r\n\r\n",  # no port
    b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",  # not listed
    b"CONNECT api.anthropic.com.evil.com:443 HTTP/1.1\r\n\r\n",
    b"CONNECT 160.79.104.10:443 HTTP/1.1\r\n\r\n",  # IP literals, even public ones
    b"CONNECT [2607:6bc0::10]:443 HTTP/1.1\r\n\r\n",
    b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n",
    b"CONNECT api_anthropic.com:443 HTTP/1.1\r\n\r\n",
])
async def test_everything_else_is_refused_with_403(started, request_bytes, caplog):
    caplog.set_level(logging.INFO, logger="labnet.egress")
    proxy, port = await started()
    assert await status_of(port, request_bytes) == 403
    assert proxy.dialled == []
    assert any(r.getMessage().startswith("DENY") for r in caplog.records)


@pytest.mark.parametrize("addresses", [
    ["10.0.0.5"], ["192.168.1.20"], ["172.17.0.1"], ["127.0.0.1"], ["169.254.169.254"], ["100.64.0.1"],
    ["224.0.0.1"], ["0.0.0.0"], ["::1"], ["fe80::1"], ["fd00::1"], ["::ffff:10.0.0.1"],
    [PUBLIC, "10.0.0.5"],  # one private answer among public ones is enough to refuse
])
async def test_a_name_resolving_to_a_private_address_is_refused(started, addresses):
    proxy, port = await started(addresses={"api.anthropic.com": addresses})
    assert await status_of(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n") == 403
    assert proxy.dialled == []


async def test_unresolvable_name_is_a_bad_gateway(started):
    _proxy, port = await started(addresses={"api.anthropic.com": []})
    assert await status_of(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n") == 502


async def test_oversized_head_is_refused(started):
    proxy, port = await started()
    huge = b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nX-Pad: " + b"a" * 20000 + b"\r\n\r\n"
    assert await status_of(port, huge) == 431
    assert proxy.dialled == []


async def test_slow_head_times_out(started):
    _proxy, port = await started(header_timeout=0.2)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n")  # never finished
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    assert head.split(b" ")[1] == b"408"
    writer.close()


async def test_idle_tunnel_is_closed(started):
    _proxy, port = await started(idle_timeout=0.2)
    head, reader, writer = await exchange(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    assert await asyncio.wait_for(reader.read(), 5) == b""  # closed by the proxy
    writer.close()


async def test_connection_cap(started):
    proxy, port = await started(max_connections=1)
    _head, _reader, held = await exchange(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
    assert proxy.active == 1
    assert await status_of(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n") == 503
    held.close()


def test_public_address_classification():
    for address in ("160.79.104.10", "1.1.1.1", "2607:6bc0::10"):
        assert is_public_address(address), address
    for address in ("10.1.2.3", "192.168.0.1", "127.0.0.53", "169.254.1.1", "100.100.1.1", "239.1.1.1",
                    "255.255.255.255", "198.18.0.1", "::", "::ffff:192.168.0.1", "2002:c0a8:0101::1", "fc00::1",
                    "ff02::1", "not an ip"):
        assert not is_public_address(address), address


def test_allow_list_parsing():
    allow = AllowList.parse("# comment\n\nAPI.Anthropic.com   # the API\n*.Example.org\n")
    assert allow.allows("api.anthropic.com")
    assert allow.allows("a.b.example.org") and not allow.allows("example.org")
    assert len(allow) == 2
    for bad in ("http://api.anthropic.com", "api.anthropic.com:443", "*", "*.com.", "10.0.0.1", "a b", "*.*.x.com"):
        with pytest.raises(AllowListError):
            AllowList.parse(bad)


def test_shipped_allow_list_is_valid_and_minimal():
    allow = AllowList.load(REPO / "docker" / "automation" / "egress-allow.txt")
    assert allow.allows("api.anthropic.com") and allow.allows("platform.claude.com")
    assert not allow.suffixes  # no wildcards
    for unwanted in ("statsig.anthropic.com", "registry.npmjs.org", "sentry.io", "github.com", "example.com"):
        assert not allow.allows(unwanted)


def test_main_refuses_an_empty_or_bad_allow_list(tmp_path):
    empty = tmp_path / "allow.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    assert egress_proxy.main(["--allow", str(empty)]) == 2
    empty.write_text("http://x\n", encoding="utf-8")
    assert egress_proxy.main(["--allow", str(empty)]) == 2
    assert egress_proxy.main(["--allow", str(tmp_path / "missing.txt")]) == 2
