"""Provider protocol and the failover manager."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol, runtime_checkable

from mcp_bitcoin.config import Network, ProviderType, ServerConfig
from mcp_bitcoin.types import (
    UTXO,
    Balance,
    BlockHeader,
    DecodedTransaction,
    FeeEstimate,
    FeeTiers,
    MempoolInfo,
    ProviderStatus,
    TxHistoryPage,
)

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 900


class ProviderError(Exception):
    """A single provider failed to service a request."""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        self.message = message
        super().__init__(f"[{provider}] {message}")


class NetworkMismatchError(ProviderError):
    """A backend is serving a different chain than the server is configured for."""


class BackendUnavailableError(Exception):
    """No provider could service a request.

    Carries enough structure for the caller to explain *why* — which provider
    failed with what, whether the method is supported at all, and what to try.
    """

    def __init__(
        self,
        method: str,
        *,
        failures: list[ProviderError] | None = None,
        unsupported: list[str] | None = None,
        cooling_down: list[str] | None = None,
        configured: int = 0,
    ):
        self.method = method
        self.failures = failures or []
        self.unsupported = unsupported or []
        self.cooling_down = cooling_down or []
        self.configured = configured
        super().__init__(self._message())

    def _message(self) -> str:
        if self.configured == 0:
            return (
                f"Cannot run '{self.method}': no backend is configured. Set at least "
                "one of MCP_BITCOIN_CORE_URL, MCP_BITCOIN_ELECTRUM_URL, "
                "MCP_BITCOIN_MEMPOOL_URL or MCP_BITCOIN_ESPLORA_URL."
            )

        parts: list[str] = []
        if self.failures:
            detail = "; ".join(f"{e.provider}: {e.message}" for e in self.failures)
            parts.append(f"failed ({detail})")
        if self.unsupported:
            parts.append(f"does not support this operation: {', '.join(self.unsupported)}")
        if self.cooling_down:
            parts.append(f"still in failure backoff: {', '.join(self.cooling_down)}")

        if not parts:
            return f"Cannot run '{self.method}': no backend was available."

        msg = f"Cannot run '{self.method}'. Backend status — " + "; ".join(parts) + "."
        if self.unsupported and not self.failures:
            msg += (
                " Configure a backend that supports it — address queries need "
                "Electrum, mempool.space or Esplora; mempool details need "
                "Bitcoin Core or mempool.space."
            )
        elif self.failures:
            msg += " Run the 'ping' tool to check backend connectivity."
        return msg

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": "backend_unavailable",
            "method": self.method,
            "message": str(self),
            "provider_errors": [
                {"provider": e.provider, "error": e.message} for e in self.failures
            ],
            "unsupported_by": self.unsupported,
            "cooling_down": self.cooling_down,
        }


@runtime_checkable
class BitcoinProvider(Protocol):
    """Interface every backend adapter implements.

    Methods a backend genuinely cannot serve raise :class:`NotImplementedError`;
    the manager reports those separately from real failures.
    """

    name: str
    provider_type: ProviderType
    network: Network
    url: str

    async def ping(self) -> bool: ...
    async def get_balance(self, address: str) -> Balance: ...
    async def get_utxos(self, address: str) -> list[UTXO]: ...
    async def get_tx_history(
        self, address: str, limit: int, after_txid: str | None
    ) -> TxHistoryPage: ...
    async def get_transaction(self, txid: str) -> DecodedTransaction: ...
    async def get_raw_transaction(self, txid: str) -> str: ...
    async def get_block(self, block_ref: str) -> BlockHeader: ...
    async def get_block_height(self) -> BlockHeader: ...
    async def get_tip_height(self) -> int: ...
    async def estimate_fee(self, target_blocks: int) -> FeeEstimate: ...
    async def get_recommended_fees(self) -> FeeTiers: ...
    async def get_mempool_info(self) -> MempoolInfo: ...
    async def get_fee_histogram(self) -> list[dict[str, Any]]: ...
    async def get_mempool_entry(self, txid: str) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class _ProviderState:
    """Health tracking for one provider.

    ``failure_count`` advances once per *outage*, not once per failed request.
    Counting requests meant a handful of calls during a brief restart jumped
    straight to the 15-minute cap.
    """

    def __init__(self, provider: BitcoinProvider, cooldown: int):
        self.provider = provider
        self.cooldown = cooldown
        self.degraded = False
        self.last_failure: float = 0.0
        self.failure_count = 0

    @property
    def backoff(self) -> float:
        if self.failure_count <= 0:
            return 0.0
        return float(min(self.cooldown * (2 ** (self.failure_count - 1)), MAX_BACKOFF_SECONDS))

    @property
    def available(self) -> bool:
        if not self.degraded:
            return True
        return (time.monotonic() - self.last_failure) >= self.backoff

    def mark_failed(self) -> None:
        now = time.monotonic()
        # Only deepen the backoff when this is a fresh outage, or a retry that
        # was allowed through after the previous window expired and failed again.
        if not self.degraded or (now - self.last_failure) >= self.backoff:
            self.failure_count = min(self.failure_count + 1, 8)
        self.degraded = True
        self.last_failure = now

    def mark_healthy(self) -> None:
        self.degraded = False
        self.failure_count = 0


class ProviderManager:
    """Routes calls across providers with priority ordering and failover."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self._states: list[_ProviderState] = []

    def register(self, provider: BitcoinProvider) -> None:
        self._states.append(_ProviderState(provider, self.config.failover_cooldown))
        logger.info("Registered provider: %s (%s)", provider.name, redact_url(provider.url))

    @property
    def provider_count(self) -> int:
        return len(self._states)

    async def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call ``method`` on the first available provider that supports it.

        Raises:
            BackendUnavailableError: If no provider could service the call. The
                exception distinguishes "nothing supports this", "everything
                failed" and "everything is cooling down".
        """
        failures: list[ProviderError] = []
        unsupported: list[str] = []
        cooling_down: list[str] = []

        for state in self._states:
            provider = state.provider
            if not state.available:
                cooling_down.append(provider.name)
                continue

            fn = getattr(provider, method, None)
            if fn is None:
                unsupported.append(provider.name)
                continue

            try:
                result = await asyncio.wait_for(fn(*args, **kwargs), timeout=self.config.timeout)
            except NotImplementedError as exc:
                # A deliberate "this backend can't do that", not a failure.
                unsupported.append(provider.name)
                logger.debug("%s does not support %s: %s", provider.name, method, exc)
                continue
            except NetworkMismatchError:
                # Never fail over past this: it means the operator's config is
                # wrong, and a silent fallback would return the wrong chain.
                state.mark_failed()
                raise
            except TimeoutError:
                failures.append(
                    ProviderError(provider.name, f"timed out after {self.config.timeout}s")
                )
                state.mark_failed()
                logger.warning("Provider %s timed out for %s", provider.name, method)
            except ProviderError as exc:
                failures.append(exc)
                state.mark_failed()
                logger.warning("Provider %s failed for %s: %s", provider.name, method, exc)
            except Exception as exc:
                failures.append(ProviderError(provider.name, f"{type(exc).__name__}: {exc}"))
                state.mark_failed()
                logger.warning("Provider %s failed for %s: %s", provider.name, method, exc)
            else:
                state.mark_healthy()
                return result

        raise BackendUnavailableError(
            method,
            failures=failures,
            unsupported=unsupported,
            cooling_down=cooling_down,
            configured=len(self._states),
        )

    async def ping_all(self, *, probe_only: bool = True) -> list[ProviderStatus]:
        """Ping every provider concurrently and report status.

        Args:
            probe_only: When true (the default) health state is left untouched,
                so asking for status never pushes a flaky backend deeper into
                backoff. Startup checks pass false to record real results.
        """

        async def probe(state: _ProviderState) -> ProviderStatus:
            provider = state.provider
            status = ProviderStatus(
                name=provider.name,
                type=provider.provider_type.value,
                url=redact_url(provider.url),
                reachable=False,
                degraded=state.degraded,
            )
            start = time.monotonic()
            try:
                await asyncio.wait_for(provider.ping(), timeout=self.config.timeout)
            except TimeoutError:
                status.error = f"timed out after {self.config.timeout}s"
                if not probe_only:
                    state.mark_failed()
            except Exception as exc:
                status.error = str(exc)
                if not probe_only:
                    state.mark_failed()
            else:
                status.reachable = True
                status.latency_ms = round((time.monotonic() - start) * 1000, 1)
                if not probe_only:
                    state.mark_healthy()
            return status

        if not self._states:
            return []
        return list(await asyncio.gather(*(probe(s) for s in self._states)))

    async def close_all(self) -> None:
        """Close every provider's network resources."""
        for state in self._states:
            try:
                await state.provider.close()
            except Exception as exc:
                logger.debug("Error closing %s: %s", state.provider.name, exc)


def redact_url(url: str) -> str:
    """Strip credentials from a URL for display.

    Uses the *last* ``@`` so a password containing ``@`` cannot leak its tail.
    """
    if "@" not in url:
        return url
    scheme_end = url.find("://")
    if scheme_end == -1:
        return url
    at_pos = url.rfind("@")
    return f"{url[: scheme_end + 3]}***:***@{url[at_pos + 1 :]}"
