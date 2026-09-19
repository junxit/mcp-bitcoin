"""Log sanitization.

This is an advertised security control that previously had zero tests.

Every key in this file is a long-published example from the Bitcoin wiki or a
BIP specification, reproduced here so the redaction patterns can be exercised.
None of them are live, and none should ever be used to hold value.
"""

from __future__ import annotations

import logging

import pytest

from mcp_bitcoin.utils.sanitize import REDACTION, SanitizingFormatter, sanitize
from tests.vectors import ABANDON_MNEMONIC


class TestMnemonicRedaction:
    def test_redacts_12_words(self) -> None:
        assert ABANDON_MNEMONIC not in sanitize(f"deriving from {ABANDON_MNEMONIC}")

    def test_redacts_24_words(self) -> None:
        phrase = " ".join(["abandon"] * 23 + ["art"])
        assert phrase not in sanitize(f"seed={phrase}")

    def test_leaves_ordinary_prose_alone(self) -> None:
        message = "Registered provider: mempool (https://mempool.space/api)"
        assert sanitize(message) == message


class TestKeyRedaction:
    @pytest.mark.parametrize(
        "wif",
        [
            "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ",  # mainnet
            "L1aW4aubDFB7yfras2S1mN3bqg9nwySY8nkoLmJebSLD5BWv3ENZ",  # mainnet
            "cVt4o7BGAig1UXywgGSmARhxMdzP5qvQsxKkSsc1XEkw3tDTQFpy",  # testnet
            "92Pg46rUhgTT7romnV7iGW6W1gbGdeezqdbJCzShkCsYNzyyNcc",  # testnet
        ],
    )
    def test_redacts_wif(self, wif) -> None:
        """Testnet WIFs (9../c..) were missed by the original pattern."""
        assert wif not in sanitize(f"signing with {wif}")

    @pytest.mark.parametrize("prefix", ["xprv", "yprv", "zprv", "tprv", "vprv"])
    def test_redacts_extended_private_keys(self, prefix) -> None:
        key = (
            prefix
            + "9s21ZrQH143K2LBWUUQRFXhucrQqBpKdRRxNVq2zBqsx8HVqFk2uYo8kmbaLLHRdqtQpUm98uKfu3vca1LqdGhUtyoFnCNkfmXRyPXLjbKb"
        )
        assert key not in sanitize(f"master={key}")

    def test_redacts_labelled_hex_secret(self) -> None:
        secret = "a" * 64
        assert REDACTION in sanitize(f"private_key={secret}")

    def test_redacts_url_credentials(self) -> None:
        got = sanitize("connecting to http://rpcuser:hunter2@127.0.0.1:8332")
        assert "hunter2" not in got
        assert "127.0.0.1:8332" in got


class TestFalsePositives:
    def test_keeps_txids_readable(self) -> None:
        """A blanket 64-hex rule would redact every txid and ruin debugging."""
        txid = "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"
        assert txid in sanitize(f"fetched transaction {txid}")

    def test_keeps_block_hashes_readable(self) -> None:
        block = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
        assert block in sanitize(f"block hash {block}")

    def test_keeps_addresses_readable(self) -> None:
        address = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
        assert address in sanitize(f"balance for {address}")


class TestFormatter:
    def test_redacts_through_formatter(self) -> None:
        record = logging.LogRecord(
            name="mcp_bitcoin",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="deriving from %s",
            args=(ABANDON_MNEMONIC,),
            exc_info=None,
        )
        assert ABANDON_MNEMONIC not in SanitizingFormatter("%(message)s").format(record)
