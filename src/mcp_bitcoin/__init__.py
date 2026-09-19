"""MCP server for Bitcoin: HD wallet derivation, chain queries, fee analysis."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-bitcoin")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"


def main() -> None:
    """Console-script entry point."""
    from mcp_bitcoin.server import main as _main

    _main()


__all__ = ["__version__", "main"]
