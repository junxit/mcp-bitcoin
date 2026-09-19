"""Log sanitization.

This is a backstop for accidental logging, not a security boundary. Tool
*results* travel to the model and the client transcript without passing through
here — which is why tools refuse secrets by default instead of relying on
redaction.
"""

from __future__ import annotations

import logging
import re

REDACTION = "[REDACTED]"

SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # BIP-39 mnemonics: 12 or more lowercase words in sequence.
    re.compile(r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b"),
    # WIF private keys. Mainnet starts 5/K/L; test networks start 9/c, which an
    # earlier revision of this file missed entirely.
    re.compile(r"\b[5KL9c][1-9A-HJ-NP-Za-km-z]{50,51}\b"),
    # Extended private keys across all SLIP-132 prefixes and networks.
    re.compile(r"\b(?:xprv|yprv|zprv|tprv|uprv|vprv|Yprv|Zprv)[1-9A-HJ-NP-Za-km-z]{100,115}\b"),
    # Raw private keys / seeds, only where labelled as such. A bare 64-hex rule
    # would redact every txid and block hash and make logs useless.
    re.compile(
        r"((?:priv(?:ate)?[_ ]?key|secret|seed|entropy)\S{0,12}[=:\s]+)[0-9a-fA-F]{32,128}",
        re.IGNORECASE,
    ),
    # RPC credentials embedded in a URL.
    re.compile(r"(://)[^:/@\s]+:[^@/\s]+(@)"),
)


def sanitize(text: str) -> str:
    """Redact anything that looks like key material."""
    text = SENSITIVE_PATTERNS[0].sub(REDACTION, text)
    text = SENSITIVE_PATTERNS[1].sub(REDACTION, text)
    text = SENSITIVE_PATTERNS[2].sub(REDACTION, text)
    text = SENSITIVE_PATTERNS[3].sub(rf"\1{REDACTION}", text)
    text = SENSITIVE_PATTERNS[4].sub(r"\1***:***\2", text)
    return text


class SanitizingFormatter(logging.Formatter):
    """Formatter that redacts key material from log records."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize(super().format(record))


def configure_logging(level: int = logging.INFO) -> None:
    """Send sanitized logs to stderr.

    stdout carries the MCP protocol stream, so logs must never go there.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(SanitizingFormatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    root = logging.getLogger("mcp_bitcoin")
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False
