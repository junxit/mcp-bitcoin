"""BIP-85 conformance against the published spec vectors.

The previous revision of these tests asserted only that derivation was
deterministic and produced a valid mnemonic. A spec-violating implementation
passes both checks, which is exactly what happened: the HEX application omitted
``num_bytes`` from the derivation path and went unnoticed.
"""

from __future__ import annotations

import pytest
from embit import bip32

from mcp_bitcoin.derivation.bip85 import (
    APP_HEX,
    derive_bip39_mnemonic,
    derive_entropy,
)
from mcp_bitcoin.derivation.mnemonic import validate_mnemonic
from tests.vectors import BIP85_MASTER_XPRV, BIP85_VECTORS


@pytest.fixture
def spec_master():
    return bip32.HDKey.from_base58(BIP85_MASTER_XPRV)


class TestSpecVectors:
    @pytest.mark.parametrize(
        "key,word_count",
        [("bip39_12_words", 12), ("bip39_18_words", 18), ("bip39_24_words", 24)],
    )
    def test_bip39_application(self, spec_master, key, word_count) -> None:
        expected = BIP85_VECTORS[key]["mnemonic"]
        assert derive_bip39_mnemonic(spec_master, word_count, 0) == expected

    def test_hex_application(self, spec_master) -> None:
        expected = BIP85_VECTORS["hex_64_bytes"]["entropy"]
        assert derive_entropy(spec_master, APP_HEX, 0, 64).hex() == expected


class TestPathStructure:
    def test_length_is_part_of_the_path(self, spec_master) -> None:
        """Different lengths must derive independently, not truncate.

        This is the precise regression: when ``num_bytes`` is missing from the
        path, a 32-byte request returns a prefix of the 64-byte result.
        """
        short = derive_entropy(spec_master, APP_HEX, 0, 32).hex()
        long = derive_entropy(spec_master, APP_HEX, 0, 64).hex()
        assert not long.startswith(short)

    def test_returns_requested_length(self, spec_master) -> None:
        for n in (16, 32, 48, 64):
            assert len(derive_entropy(spec_master, APP_HEX, 0, n)) == n

    def test_index_changes_output(self, spec_master) -> None:
        assert derive_entropy(spec_master, APP_HEX, 0, 32) != derive_entropy(
            spec_master, APP_HEX, 1, 32
        )


class TestValidation:
    @pytest.mark.parametrize("num_bytes", [0, 15, 65, 1000, -1])
    def test_rejects_out_of_range_length(self, spec_master, num_bytes) -> None:
        with pytest.raises(ValueError, match="num_bytes"):
            derive_entropy(spec_master, APP_HEX, 0, num_bytes)

    def test_rejects_negative_index(self, spec_master) -> None:
        with pytest.raises(ValueError, match="index"):
            derive_entropy(spec_master, APP_HEX, -1, 32)

    def test_rejects_bad_word_count(self, spec_master) -> None:
        with pytest.raises(ValueError, match="word count"):
            derive_bip39_mnemonic(spec_master, 13, 0)

    def test_rejects_non_english(self, spec_master) -> None:
        with pytest.raises(ValueError, match="English"):
            derive_bip39_mnemonic(spec_master, 12, 0, language=1)

    def test_rejects_public_key(self, spec_master) -> None:
        with pytest.raises(ValueError, match="private"):
            derive_entropy(spec_master.to_public(), APP_HEX, 0, 32)


class TestDerivedOutput:
    @pytest.mark.parametrize("word_count", [12, 15, 18, 21, 24])
    def test_child_mnemonics_are_valid(self, spec_master, word_count) -> None:
        child = derive_bip39_mnemonic(spec_master, word_count, 0)
        result = validate_mnemonic(child)
        assert result["valid"]
        assert result["word_count"] == word_count
