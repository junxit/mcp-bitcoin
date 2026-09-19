"""BIP-39 mnemonic generation, validation and seed derivation."""

from __future__ import annotations

import pytest

from mcp_bitcoin.derivation.mnemonic import (
    VALID_WORD_COUNTS,
    generate_mnemonic,
    mnemonic_to_seed,
    require_valid_mnemonic,
    validate_mnemonic,
)
from tests.vectors import ABANDON_MNEMONIC


class TestGenerate:
    @pytest.mark.parametrize("word_count", VALID_WORD_COUNTS)
    def test_word_count_and_validity(self, word_count) -> None:
        phrase = generate_mnemonic(word_count)
        assert len(phrase.split()) == word_count
        assert validate_mnemonic(phrase)["valid"]

    @pytest.mark.parametrize("bad", [0, 11, 13, 25, -12, 100])
    def test_rejects_bad_word_count(self, bad) -> None:
        with pytest.raises(ValueError, match="Invalid word count"):
            generate_mnemonic(bad)

    def test_entropy_is_fresh(self) -> None:
        assert len({generate_mnemonic(12) for _ in range(20)}) == 20


class TestValidate:
    def test_canonical_phrase(self) -> None:
        result = validate_mnemonic(ABANDON_MNEMONIC)
        assert result["valid"]
        assert result["word_count"] == 12

    def test_bad_checksum(self) -> None:
        # Valid words, wrong checksum: the last word should be "about".
        result = validate_mnemonic("abandon " * 11 + "abandon")
        assert not result["valid"]
        assert "checksum" in result["error"].lower()

    def test_unknown_word(self) -> None:
        phrase = ABANDON_MNEMONIC.replace("about", "notaword")
        assert not validate_mnemonic(phrase)["valid"]

    @pytest.mark.parametrize("bad", ["", "   ", "abandon", "abandon " * 13])
    def test_bad_word_count(self, bad) -> None:
        result = validate_mnemonic(bad)
        assert not result["valid"]
        assert "word count" in result["error"].lower()

    def test_normalizes_whitespace(self) -> None:
        messy = f"  {ABANDON_MNEMONIC.replace(' ', '   ')}  "
        assert validate_mnemonic(messy)["valid"]


class TestRequireValid:
    def test_returns_normalized(self) -> None:
        messy = f"  {ABANDON_MNEMONIC.replace(' ', '  ')} "
        assert require_valid_mnemonic(messy) == ABANDON_MNEMONIC

    def test_raises_on_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid mnemonic"):
            require_valid_mnemonic("abandon abandon abandon")


class TestSeed:
    def test_length(self) -> None:
        assert len(mnemonic_to_seed(ABANDON_MNEMONIC)) == 64

    def test_deterministic(self) -> None:
        assert mnemonic_to_seed(ABANDON_MNEMONIC, "x") == mnemonic_to_seed(ABANDON_MNEMONIC, "x")

    def test_passphrase_changes_seed(self) -> None:
        """The passphrase is the '13th/25th word' — it must change everything."""
        assert mnemonic_to_seed(ABANDON_MNEMONIC) != mnemonic_to_seed(ABANDON_MNEMONIC, "TREZOR")

    def test_empty_passphrase_is_the_default(self) -> None:
        assert mnemonic_to_seed(ABANDON_MNEMONIC) == mnemonic_to_seed(ABANDON_MNEMONIC, "")

    def test_validates_before_deriving(self) -> None:
        """An invalid phrase must fail loudly rather than yield a usable seed."""
        with pytest.raises(ValueError, match="Invalid mnemonic"):
            mnemonic_to_seed("abandon " * 11 + "abandon")
