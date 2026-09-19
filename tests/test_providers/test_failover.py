"""Provider failover, health tracking and error reporting."""

from __future__ import annotations

import pytest

from mcp_bitcoin.config import ServerConfig
from mcp_bitcoin.providers.base import (
    BackendUnavailableError,
    ProviderManager,
    _ProviderState,
    redact_url,
)
from tests.conftest import FakeProvider


class TestRouting:
    async def test_uses_highest_priority(self, make_manager) -> None:
        first, second = FakeProvider("first"), FakeProvider("second")
        pm = make_manager(first, second)
        await pm.call("get_balance", "addr")
        assert first.calls and not second.calls

    async def test_fails_over(self, make_manager) -> None:
        broken, working = FakeProvider("broken", fail=True), FakeProvider("working")
        pm = make_manager(broken, working)
        result = await pm.call("get_balance", "addr")
        assert result.source == "working"

    async def test_skips_unsupported(self, make_manager) -> None:
        core = FakeProvider("core", unsupported={"get_balance"})
        rest = FakeProvider("rest")
        pm = make_manager(core, rest)
        result = await pm.call("get_balance", "addr")
        assert result.source == "rest"


class TestErrorReporting:
    async def test_all_failed_names_each_backend(self, make_manager) -> None:
        pm = make_manager(FakeProvider("a", fail=True), FakeProvider("b", fail=True))
        with pytest.raises(BackendUnavailableError) as exc:
            await pm.call("get_balance", "addr")
        message = str(exc.value)
        assert "a" in message and "b" in message
        assert "backend is down" in message
        assert len(exc.value.failures) == 2

    async def test_unsupported_message_is_not_empty(self, make_manager) -> None:
        """The regression: all-NotImplementedError produced an empty message.

        This is the documented Electrum-only and Core-only configuration, so
        the most common single-backend setups hit it.
        """
        pm = make_manager(
            FakeProvider("a", unsupported={"get_balance"}),
            FakeProvider("b", unsupported={"get_balance"}),
        )
        with pytest.raises(BackendUnavailableError) as exc:
            await pm.call("get_balance", "addr")

        message = str(exc.value)
        assert message.strip()
        assert not message.rstrip().endswith(":")
        assert "does not support" in message
        assert exc.value.unsupported == ["a", "b"]

    async def test_no_backends_configured(self) -> None:
        pm = ProviderManager(ServerConfig())
        with pytest.raises(BackendUnavailableError) as exc:
            await pm.call("get_balance", "addr")
        assert "no backend is configured" in str(exc.value).lower()
        assert "MCP_BITCOIN_MEMPOOL_URL" in str(exc.value)

    async def test_missing_method_reported_as_unsupported(self, make_manager) -> None:
        pm = make_manager(FakeProvider("partial"))
        with pytest.raises(BackendUnavailableError) as exc:
            await pm.call("get_block", "1")
        assert exc.value.unsupported == ["partial"]

    async def test_as_dict_is_structured(self, make_manager) -> None:
        pm = make_manager(FakeProvider("a", fail=True))
        with pytest.raises(BackendUnavailableError) as exc:
            await pm.call("get_balance", "addr")
        payload = exc.value.as_dict()
        assert payload["error"] == "backend_unavailable"
        assert payload["provider_errors"][0]["provider"] == "a"


class TestHealthState:
    def test_starts_available(self) -> None:
        state = _ProviderState(FakeProvider(), cooldown=60)
        assert state.available

    def test_failure_starts_backoff(self) -> None:
        state = _ProviderState(FakeProvider(), cooldown=60)
        state.mark_failed()
        assert state.degraded and not state.available

    def test_success_clears_backoff(self) -> None:
        state = _ProviderState(FakeProvider(), cooldown=60)
        state.mark_failed()
        state.mark_healthy()
        assert state.available and state.failure_count == 0

    def test_backoff_expires(self) -> None:
        state = _ProviderState(FakeProvider(), cooldown=60)
        state.mark_failed()
        state.last_failure -= 61  # pretend the window elapsed
        assert state.available

    def test_backoff_doubles_per_outage(self) -> None:
        state = _ProviderState(FakeProvider(), cooldown=60)
        seen = []
        for _ in range(4):
            state.last_failure -= state.backoff + 1  # allow a retry through
            state.mark_failed()
            seen.append(state.backoff)
        assert seen == [60, 120, 240, 480]

    def test_repeated_failures_in_one_outage_do_not_compound(self) -> None:
        """Five calls during a brief restart must not jump to the 15-minute cap.

        Counting per request rather than per outage turned a documented
        1-minute first backoff into 15 minutes.
        """
        state = _ProviderState(FakeProvider(), cooldown=60)
        for _ in range(5):
            state.mark_failed()
        assert state.failure_count == 1
        assert state.backoff == 60

    def test_backoff_is_capped(self) -> None:
        state = _ProviderState(FakeProvider(), cooldown=60)
        for _ in range(20):
            state.last_failure -= state.backoff + 1
            state.mark_failed()
        assert state.backoff <= 900


class TestPingAll:
    async def test_reports_each_backend(self, make_manager) -> None:
        pm = make_manager(FakeProvider("up"), FakeProvider("down", fail=True))
        statuses = await pm.ping_all()
        assert [s.reachable for s in statuses] == [True, False]
        assert statuses[1].error

    async def test_probe_only_leaves_health_alone(self, make_manager) -> None:
        """Listing backends must not push a flaky one deeper into backoff."""
        pm = make_manager(FakeProvider("flaky", fail=True))
        await pm.ping_all(probe_only=True)
        assert pm._states[0].failure_count == 0
        assert pm._states[0].available

    async def test_non_probe_records_failure(self, make_manager) -> None:
        pm = make_manager(FakeProvider("flaky", fail=True))
        await pm.ping_all(probe_only=False)
        assert pm._states[0].degraded

    async def test_empty_manager(self) -> None:
        assert await ProviderManager(ServerConfig()).ping_all() == []


class TestRedactUrl:
    def test_no_credentials_unchanged(self) -> None:
        assert redact_url("https://mempool.space/api") == "https://mempool.space/api"

    def test_strips_credentials(self) -> None:
        assert redact_url("http://user:pass@host:8332") == "http://***:***@host:8332"

    def test_password_containing_at_sign(self) -> None:
        """Using the first '@' leaked most of the password."""
        got = redact_url("http://user:p@ssw0rd@node.local:8332")
        assert got == "http://***:***@node.local:8332"
        assert "ssw0rd" not in got
