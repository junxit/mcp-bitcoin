"""Electrum protocol framing.

Covers the desync bug: without id-matching, an unsolicited subscription
notification was consumed as a reply, leaving every subsequent response
permanently off-by-one — get_balance(A) returning B's balance.
"""

from __future__ import annotations

import json

import pytest

from mcp_bitcoin.config import Network
from mcp_bitcoin.providers.electrum import ElectrumProvider, script_hash_for
from tests.vectors import KNOWN_ADDRESSES, TESTNET_ADDRESSES


class FakeStream:
    """Scripted reader/writer pair standing in for an Electrum server."""

    def __init__(self, frames: list[dict]):
        self._lines = [json.dumps(f).encode() + b"\n" for f in frames]
        self.written: list[dict] = []

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    def write(self, data: bytes) -> None:
        self.written.append(json.loads(data))

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def make_provider(frames: list[dict]) -> tuple[ElectrumProvider, FakeStream]:
    provider = ElectrumProvider("tcp://electrum.invalid:50001", Network.MAINNET)
    stream = FakeStream(frames)
    provider._reader = stream  # type: ignore[assignment]
    provider._writer = stream  # type: ignore[assignment]
    return provider, stream


class TestUrlParsing:
    @pytest.mark.parametrize(
        "url,host,port,ssl",
        [
            ("ssl://node.local:50002", "node.local", 50002, True),
            ("tcp://node.local:50001", "node.local", 50001, False),
            ("ssl://node.local", "node.local", 50002, True),
            ("tcp://node.local", "node.local", 50001, False),
        ],
    )
    def test_parses_valid_urls(self, url, host, port, ssl) -> None:
        p = ElectrumProvider(url, Network.MAINNET)
        assert (p._host, p._port, p._use_ssl) == (host, port, ssl)

    @pytest.mark.parametrize("bad", ["node.local:50002", "http://node.local", "node.local", ""])
    def test_rejects_schemeless_url(self, bad) -> None:
        """A scheme-less URL silently resolved to localhost:50001."""
        with pytest.raises(ValueError, match="ssl:// or tcp://"):
            ElectrumProvider(bad, Network.MAINNET)


class TestTlsPolicy:
    def test_verifies_by_default(self) -> None:
        import ssl as ssl_mod

        ctx = ElectrumProvider("ssl://node.local:50002", Network.MAINNET)._ssl_context()
        assert ctx is not None
        assert ctx.verify_mode == ssl_mod.CERT_REQUIRED

    def test_opt_in_disables_verification(self) -> None:
        import ssl as ssl_mod

        ctx = ElectrumProvider(
            "ssl://node.local:50002", Network.MAINNET, allow_self_signed=True
        )._ssl_context()
        assert ctx.verify_mode == ssl_mod.CERT_NONE

    def test_onion_skips_verification(self) -> None:
        """A v3 onion address authenticates the endpoint on its own."""
        import ssl as ssl_mod

        ctx = ElectrumProvider("ssl://abc123def456.onion:50002", Network.MAINNET)._ssl_context()
        assert ctx.verify_mode == ssl_mod.CERT_NONE

    def test_plaintext_has_no_context(self) -> None:
        assert ElectrumProvider("tcp://n.local:50001", Network.MAINNET)._ssl_context() is None


class TestFraming:
    async def test_matches_response_by_id(self) -> None:
        provider, _ = make_provider([{"jsonrpc": "2.0", "id": 1, "result": "ok"}])
        assert await provider._send("server.version", []) == "ok"

    async def test_skips_notification_before_response(self) -> None:
        """The exact desync: a header notification arriving before the reply."""
        provider, _ = make_provider(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "blockchain.headers.subscribe",
                    "params": [{"height": 850_000, "hex": "00"}],
                },
                {"jsonrpc": "2.0", "id": 1, "result": "the-real-answer"},
            ]
        )
        assert await provider._send("blockchain.scripthash.get_balance", []) == ("the-real-answer")

    async def test_notification_updates_tip(self) -> None:
        provider, _ = make_provider(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "blockchain.headers.subscribe",
                    "params": [{"height": 912_345}],
                },
                {"jsonrpc": "2.0", "id": 1, "result": "ok"},
            ]
        )
        await provider._send("anything", [])
        assert provider._tip_height == 912_345

    async def test_discards_stale_response_id(self) -> None:
        provider, _ = make_provider(
            [
                {"jsonrpc": "2.0", "id": 99, "result": "stale"},
                {"jsonrpc": "2.0", "id": 1, "result": "fresh"},
            ]
        )
        assert await provider._send("method", []) == "fresh"

    async def test_multiple_notifications_in_a_row(self) -> None:
        frames = [
            {"jsonrpc": "2.0", "method": "blockchain.headers.subscribe", "params": [{"height": h}]}
            for h in (1, 2, 3)
        ]
        frames.append({"jsonrpc": "2.0", "id": 1, "result": "done"})
        provider, _ = make_provider(frames)
        assert await provider._send("method", []) == "done"
        assert provider._tip_height == 3

    async def test_server_error_raises(self) -> None:
        from mcp_bitcoin.providers.base import ProviderError

        provider, _ = make_provider(
            [{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "nope"}}]
        )
        with pytest.raises(ProviderError, match="nope"):
            await provider._send("method", [])

    async def test_closed_connection_raises(self) -> None:
        from mcp_bitcoin.providers.base import ProviderError

        provider, _ = make_provider([])
        with pytest.raises(ProviderError, match="closed"):
            await provider._send("method", [])

    async def test_malformed_json_raises(self) -> None:
        from mcp_bitcoin.providers.base import ProviderError

        provider = ElectrumProvider("tcp://n.invalid:50001", Network.MAINNET)

        class BadStream(FakeStream):
            async def readline(self) -> bytes:
                return b"{not json\n"

        stream = BadStream([])
        provider._reader = stream  # type: ignore[assignment]
        provider._writer = stream  # type: ignore[assignment]
        with pytest.raises(ProviderError, match="malformed"):
            await provider._send("method", [])

    async def test_ids_increment(self) -> None:
        provider, stream = make_provider(
            [
                {"jsonrpc": "2.0", "id": 1, "result": "a"},
                {"jsonrpc": "2.0", "id": 2, "result": "b"},
            ]
        )
        await provider._send("one", [])
        await provider._send("two", [])
        assert [m["id"] for m in stream.written] == [1, 2]

    async def test_reset_clears_connection(self) -> None:
        provider, _ = make_provider([])
        await provider._reset()
        assert provider._reader is None and provider._writer is None


class TestScriptHash:
    def test_known_address(self) -> None:
        """sha256(scriptPubKey), byte-reversed.

        Derived independently of this codebase, so the assertion is a real
        check rather than a restatement of whatever the implementation does::

            SPK=0014751e76e8199196d454941c45d1b3a323f1433bd6
            printf "$SPK" | xxd -r -p | openssl dgst -sha256 -binary \\
                | xxd -p -c 64 | fold -w2 | tac | tr -d '\\n'

        The scriptPubKey is that of the BIP-173 vector address
        ``bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4``.
        """
        got = script_hash_for(KNOWN_ADDRESSES["p2wpkh"], Network.MAINNET)
        assert len(got) == 64
        assert got == ("9623df75239b5daa7f5f03042d325b51498c4bb7059c7748b17049bf96f73888")

    def test_rejects_wrong_network(self) -> None:
        """Without this, a mainnet address returns the testnet balance.

        The scriptPubKey is identical across networks for the same pubkey hash,
        so the script hash is too — nothing downstream can catch the mistake.
        """
        with pytest.raises(ValueError, match="mainnet address"):
            script_hash_for(KNOWN_ADDRESSES["p2wpkh"], Network.TESTNET)

    def test_accepts_matching_network(self) -> None:
        assert script_hash_for(TESTNET_ADDRESSES["p2wpkh"], Network.TESTNET)
