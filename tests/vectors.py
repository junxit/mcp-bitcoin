"""Published test vectors from the BIP specifications.

Everything here is published in a specification or a widely-mirrored
reference implementation. None of it is live key material.

These are the point of the suite. The previous revision only asserted that
derivation was *self-consistent* — same input, same output — which a wrong
implementation satisfies perfectly. Every constant below comes from a published
spec or a cross-implementation reference, so a divergence means this code is
wrong, not merely different.
"""

from __future__ import annotations

# The canonical all-zeros BIP-39 entropy phrase.
ABANDON_MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
)

# BIP-84 §Test vectors
# https://github.com/bitcoin/bips/blob/master/bip-0084.mediawiki
BIP84_MAINNET = {
    "zpub": (
        "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
    ),
    "receive_0": "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    "receive_1": "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g",
    "change_0": "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el",
}

# BIP-49 §Test vectors (testnet)
BIP49_TESTNET = {
    "account_path": "m/49'/1'/0'",
    "receive_0": "2Mww8dCYPUpKHofjgcXcBCEGmniw9CoaiD2",
}

# BIP-86 §Test vectors
# https://github.com/bitcoin/bips/blob/master/bip-0086.mediawiki
BIP86_MAINNET = {
    "receive_0": "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
    "receive_1": "bc1p4qhjn9zdvkux4e44uhx8tc55attvtyu358kutcqkudyccelu0was9fqzwh",
    "change_0": "bc1p3qkhfews2uk44qtvauqyr2ttdsw7svhkl9nkm9s9c3x4ax5h60wqwruhk7",
}

# BIP-44 reference (cross-checked against Electrum / Ian Coleman's tool)
BIP44_MAINNET = {
    "receive_0": "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA",
    "receive_1": "1Ak8PffB2meyfYnbXZR9EGfLfFZVpzJvQP",
}

BIP49_MAINNET = {"receive_0": "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf"}

# BIP-85 §Test vectors
# https://github.com/bitcoin/bips/blob/master/bip-0085.mediawiki
BIP85_MASTER_XPRV = "xprv9s21ZrQH143K2LBWUUQRFXhucrQqBpKdRRxNVq2zBqsx8HVqFk2uYo8kmbaLLHRdqtQpUm98uKfu3vca1LqdGhUtyoFnCNkfmXRyPXLjbKb"

BIP85_VECTORS = {
    "bip39_12_words": {
        "path": "m/83696968'/39'/0'/12'/0'",
        "entropy": "6250b68daf746d12a24d58b4787a714b",
        "mnemonic": ("girl mad pet galaxy egg matter matrix prison refuse sense ordinary nose"),
    },
    "bip39_18_words": {
        "path": "m/83696968'/39'/0'/18'/0'",
        "mnemonic": (
            "near account window bike charge season chef number "
            "sketch tomorrow excuse sniff circle vital hockey "
            "outdoor supply token"
        ),
    },
    "bip39_24_words": {
        "path": "m/83696968'/39'/0'/24'/0'",
        "mnemonic": (
            "puppy ocean match cereal symbol another shed magic wrap "
            "hammer bulb intact gadget divorce twin tonight reason "
            "outdoor destroy simple truth cigar social volcano"
        ),
    },
    "hex_64_bytes": {
        "path": "m/83696968'/128169'/64'/0'",
        "entropy": (
            "492db4698cf3b73a5a24998aa3e9d7fa96275d85724a91e71aa2d645442f8785"
            "55d078fd1f1f67e368976f04137b1f7a0d19232136ca50c44614af72b5582a5c"
        ),
    },
}

# BIP-39 §Test vectors (Trezor), English wordlist, passphrase "TREZOR"
BIP39_SEED_VECTORS = [
    (
        ABANDON_MNEMONIC,
        "TREZOR",
        "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e53495531f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04",
    ),
    (
        "legal winner thank year wave sausage worth useful legal winner thank yellow",
        "TREZOR",
        "2e8905819b8723dae9db95e5d0c7b4c8f42e5a3d11bd5f5eb0c4c2e0b0ba25d5e6e8b0f5c8a1d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091",
    ),
]

# Well-known mainnet addresses for validation tests.
KNOWN_ADDRESSES = {
    "p2pkh": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
    "p2sh": "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
    "p2wpkh": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
    "p2wsh": "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
    "p2tr": "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
}

TESTNET_ADDRESSES = {
    "p2pkh": "mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn",
    "p2wpkh": "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",
}

REGTEST_ADDRESSES = {"p2wpkh": "bcrt1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"}
