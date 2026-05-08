import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json

import httpx
import pytest

from app.models import SecurityHolding
from app.services.cache import RedisBackedTTLCache, TTLCache
from app.services.common_service import _coerce_utc_datetime
from app.services.market_data import (
	BitgetQuoteProvider,
	EastMoneyQuoteProvider,
	EastMoneySecuritySearchProvider,
	FrankfurterRateProvider,
	MarketDataClient,
	Quote,
	QuoteLookupError,
	SecuritySearchResult,
	YahooQuoteProvider,
	YahooSecuritySearchProvider,
	build_eastmoney_secid,
	build_local_search_results,
	build_fx_symbol,
	infer_security_market,
	normalize_symbol_for_market,
	normalize_symbol,
	parse_eastmoney_search_item,
)
from app.services import service_context
from app.services.portfolio_read_service import _value_holdings


def _make_quote(
	symbol: str = "AAPL",
	price: Decimal | int | str = Decimal("100"),
	currency: str = "USD",
) -> Quote:
	return Quote(
		symbol=symbol,
		name=symbol,
		price=Decimal(str(price)),
		currency=currency,
		market_time=datetime(2026, 2, 28, tzinfo=timezone.utc),
	)


class SequenceQuoteProvider:
	def __init__(self, outcomes: list[object]) -> None:
		self._outcomes = outcomes
		self.calls = 0
		self.symbols: list[str] = []

	async def fetch_quote(self, symbol: str) -> Quote:
		self.calls += 1
		self.symbols.append(symbol)
		outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
		if isinstance(outcome, Exception):
			raise outcome
		return outcome


class SequenceRateProvider:
	def __init__(self, outcomes: list[object]) -> None:
		self._outcomes = outcomes
		self.calls = 0
		self.pairs: list[tuple[str, str]] = []

	async def fetch_rate(self, from_currency: str, to_currency: str) -> Decimal:
		self.calls += 1
		self.pairs.append((from_currency, to_currency))
		outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
		if isinstance(outcome, Exception):
			raise outcome
		return Decimal(str(outcome))


class DeferredQuoteProvider:
	def __init__(self, initial_quote: Quote, refreshed_quote: Quote) -> None:
		self._initial_quote = initial_quote
		self._refreshed_quote = refreshed_quote
		self._refresh_gate = asyncio.Event()
		self.calls = 0

	async def fetch_quote(self, symbol: str) -> Quote:
		self.calls += 1
		if self.calls == 1:
			return self._initial_quote
		await self._refresh_gate.wait()
		return self._refreshed_quote

	def release_refresh(self) -> None:
		self._refresh_gate.set()


class DeferredRateProvider:
	def __init__(self, initial_rate: Decimal | int | str, refreshed_rate: Decimal | int | str) -> None:
		self._initial_rate = initial_rate
		self._refreshed_rate = refreshed_rate
		self._refresh_gate = asyncio.Event()
		self.calls = 0

	async def fetch_rate(self, from_currency: str, to_currency: str) -> Decimal:
		self.calls += 1
		if self.calls == 1:
			return Decimal(str(self._initial_rate))
		await self._refresh_gate.wait()
		return Decimal(str(self._refreshed_rate))

	def release_refresh(self) -> None:
		self._refresh_gate.set()


class SequenceSearchProvider:
	def __init__(self, outcomes: list[object]) -> None:
		self._outcomes = outcomes
		self.calls = 0
		self.queries: list[str] = []

	async def search(self, query: str) -> list[SecuritySearchResult]:
		self.calls += 1
		self.queries.append(query)
		outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
		if isinstance(outcome, Exception):
			raise outcome
		return outcome


class FakeRedis:
	def __init__(self) -> None:
		self._values: dict[str, bytes] = {}
		self._ttls: dict[str, int] = {}

	def _normalize_key(self, key: str | bytes) -> str:
		if isinstance(key, bytes):
			return key.decode("utf-8")
		return key

	def get(self, key: str | bytes) -> bytes | None:
		return self._values.get(self._normalize_key(key))

	def set(self, key: str | bytes, value: bytes, ex: int | None = None) -> bool:
		normalized_key = self._normalize_key(key)
		self._values[normalized_key] = value
		if ex is None:
			self._ttls.pop(normalized_key, None)
		else:
			self._ttls[normalized_key] = ex
		return True

	def delete(self, *keys: str | bytes) -> int:
		deleted = 0
		for key in keys:
			normalized_key = self._normalize_key(key)
			if normalized_key in self._values:
				del self._values[normalized_key]
				self._ttls.pop(normalized_key, None)
				deleted += 1
		return deleted

	def scan_iter(self, pattern: str) -> list[str]:
		prefix = pattern[:-1] if pattern.endswith("*") else pattern
		return [key for key in sorted(self._values) if key.startswith(prefix)]

	def ttl(self, key: str | bytes) -> int:
		return self._ttls.get(self._normalize_key(key), -1)


def test_build_fx_symbol_uses_yahoo_pair_format() -> None:
	assert build_fx_symbol("hkd", "cny") == "HKDCNY=X"


def test_coerce_utc_datetime_treats_naive_values_as_utc() -> None:
	naive_timestamp = datetime(2026, 3, 1, 8, 0, 0)
	normalized_timestamp = _coerce_utc_datetime(naive_timestamp)

	assert normalized_timestamp.tzinfo == timezone.utc
	assert normalized_timestamp.hour == 8


@pytest.mark.parametrize(
	("raw_symbol", "expected"),
	[
		("sh600519", "600519.SS"),
		("700", "0700.HK"),
		("02015.HK", "2015.HK"),
		("09988", "9988.HK"),
		("brk-b", "BRK-B"),
	],
)
def test_normalize_symbol_supports_common_market_formats(
	raw_symbol: str,
	expected: str,
) -> None:
	assert normalize_symbol(raw_symbol) == expected


def test_normalize_symbol_rejects_obviously_invalid_values() -> None:
	with pytest.raises(ValueError, match="Invalid symbol format"):
		normalize_symbol("bad symbol!")


def test_infer_security_market_uses_symbol_and_exchange_hints() -> None:
	assert infer_security_market("0700.HK") == "HK"
	assert infer_security_market("600519.SS") == "CN"
	assert infer_security_market("AAPL", "NMS") == "US"
	assert infer_security_market("BTC-USD") == "CRYPTO"


def test_normalize_symbol_for_market_maps_crypto_aliases_to_usd_pairs() -> None:
	assert normalize_symbol_for_market("btc", "CRYPTO") == "BTC-USD"
	assert normalize_symbol_for_market("eth/usdt", "CRYPTO") == "ETH-USD"


def test_build_eastmoney_secid_maps_cn_and_hk_symbols() -> None:
	assert build_eastmoney_secid("0700.HK") == "116.00700"
	assert build_eastmoney_secid("600519.SS") == "1.600519"
	assert build_eastmoney_secid("300750.SZ") == "0.300750"


def test_fetch_quote_uses_fresh_cache_before_calling_provider() -> None:
	provider = SequenceQuoteProvider([_make_quote()])
	client = MarketDataClient(
		quote_provider=provider,
		quote_ttl_seconds=60,
	)

	first_quote, first_warnings = asyncio.run(client.fetch_quote("aapl"))
	second_quote, second_warnings = asyncio.run(client.fetch_quote("AAPL"))

	assert first_quote.price == Decimal("100")
	assert second_quote.price == Decimal("100")
	assert first_warnings == []
	assert second_warnings == []
	assert provider.calls == 1
	assert provider.symbols == ["AAPL"]


def test_redis_backed_ttl_cache_keeps_stale_entries_after_expiry() -> None:
	clock = [Decimal("0")]
	cache = RedisBackedTTLCache[Quote](
		FakeRedis(),
		"asset-tracker:test:quotes",
		now=lambda: clock[0],
	)
	cache.set("AAPL", _make_quote(price=123.4), ttl_seconds=30)

	assert cache.get("AAPL") is not None

	clock[0] = Decimal("31")

	assert cache.get("AAPL") is None
	assert cache.get_stale("AAPL") is not None


def test_redis_backed_ttl_cache_writes_versioned_json_payloads() -> None:
	redis_client = FakeRedis()
	cache = RedisBackedTTLCache[Quote](
		redis_client,
		"asset-tracker:test:quotes",
		now=lambda: Decimal("0"),
	)
	cache.set("AAPL", _make_quote(price=123.4), ttl_seconds=30)

	redis_keys = list(redis_client.scan_iter("asset-tracker:test:quotes:*"))
	assert len(redis_keys) == 1
	raw_payload = redis_client.get(redis_keys[0])
	assert raw_payload is not None
	assert not raw_payload.startswith(b"\x80")
	payload = json.loads(raw_payload.decode("utf-8"))
	assert payload["version"] == 2
	assert payload["value"]["__type__"] == "Quote"
	assert payload["value"]["price"]["value"] == "123.4"


def test_redis_backed_ttl_cache_ignores_legacy_pickle_payloads() -> None:
	redis_client = FakeRedis()
	cache = RedisBackedTTLCache[Quote](
		redis_client,
		"asset-tracker:test:quotes",
		now=lambda: Decimal("0"),
	)
	redis_client.set(cache._entry_key("AAPL"), b"\x80\x05legacy-pickle")

	assert cache.get("AAPL") is None
	assert cache.get_stale("AAPL") is None


def test_redis_backed_ttl_cache_sets_physical_expiry_for_stale_entries() -> None:
	redis_client = FakeRedis()
	cache = RedisBackedTTLCache[Quote](
		redis_client,
		"asset-tracker:test:quotes",
		stale_ttl_seconds=180,
	)
	cache.set("AAPL", _make_quote(price=123.4), ttl_seconds=30)

	redis_keys = list(redis_client.scan_iter("asset-tracker:test:quotes:*"))
	assert len(redis_keys) == 1
	assert redis_client.ttl(redis_keys[0]) == 180


def test_fetch_quote_uses_redis_backed_cache_after_client_recreation() -> None:
	clock = [Decimal("0")]
	redis_client = FakeRedis()
	provider = SequenceQuoteProvider([_make_quote(price=101.5)])
	client = MarketDataClient(
		quote_provider=provider,
		quote_cache=RedisBackedTTLCache[Quote](
			redis_client,
			"asset-tracker:test:quotes",
			now=lambda: clock[0],
		),
		quote_ttl_seconds=60,
	)

	first_quote, first_warnings = asyncio.run(client.fetch_quote("AAPL"))

	recreated_provider = SequenceQuoteProvider([QuoteLookupError("should stay cached")])
	recreated_client = MarketDataClient(
		quote_provider=recreated_provider,
		quote_cache=RedisBackedTTLCache[Quote](
			redis_client,
			"asset-tracker:test:quotes",
			now=lambda: clock[0],
		),
		quote_ttl_seconds=60,
	)
	second_quote, second_warnings = asyncio.run(recreated_client.fetch_quote("AAPL"))

	assert first_quote.price == Decimal("101.5")
	assert second_quote.price == Decimal("101.5")
	assert first_warnings == []
	assert second_warnings == []
	assert provider.calls == 1
	assert recreated_provider.calls == 0


def test_fetch_fx_rate_uses_redis_backed_cache_after_client_recreation() -> None:
	clock = [Decimal("0")]
	redis_client = FakeRedis()
	provider = SequenceRateProvider([Decimal("7.1234")])
	client = MarketDataClient(
		fx_provider=provider,
		fallback_fx_provider=SequenceRateProvider([QuoteLookupError("unused")]),
		fx_cache=RedisBackedTTLCache[Decimal](
			redis_client,
			"asset-tracker:test:fx",
			now=lambda: clock[0],
		),
		fx_ttl_seconds=60,
	)

	first_rate, first_warnings = asyncio.run(client.fetch_fx_rate("USD", "CNY"))

	recreated_provider = SequenceRateProvider([QuoteLookupError("should stay cached")])
	recreated_client = MarketDataClient(
		fx_provider=recreated_provider,
		fallback_fx_provider=SequenceRateProvider([QuoteLookupError("unused")]),
		fx_cache=RedisBackedTTLCache[Decimal](
			redis_client,
			"asset-tracker:test:fx",
			now=lambda: clock[0],
		),
		fx_ttl_seconds=60,
	)
	second_rate, second_warnings = asyncio.run(recreated_client.fetch_fx_rate("USD", "CNY"))

	assert first_rate == Decimal("7.1234")
	assert second_rate == Decimal("7.1234")
	assert first_warnings == []
	assert second_warnings == []
	assert provider.calls == 1
	assert recreated_provider.calls == 0


def test_fetch_quote_refreshes_after_cache_expiry() -> None:
	clock = [Decimal("0")]
	provider = SequenceQuoteProvider([
		_make_quote(price=100.0),
		_make_quote(price=101.5),
	])
	client = MarketDataClient(
		quote_provider=provider,
		quote_cache=TTLCache[Quote](now=lambda: clock[0]),
		quote_ttl_seconds=30,
	)

	first_quote, _ = asyncio.run(client.fetch_quote("AAPL"))
	clock[0] = Decimal("31")
	second_quote, _ = asyncio.run(client.fetch_quote("AAPL"))

	assert first_quote.price == Decimal("100")
	assert second_quote.price == Decimal("101.5")
	assert provider.calls == 2


def test_fetch_quote_returns_stale_cache_when_provider_fails() -> None:
	clock = [Decimal("0")]
	provider = SequenceQuoteProvider([
		_make_quote(price=88.8),
		QuoteLookupError("provider down"),
	])
	client = MarketDataClient(
		quote_provider=provider,
		quote_cache=TTLCache[Quote](now=lambda: clock[0]),
		quote_ttl_seconds=30,
	)

	cached_quote, _ = asyncio.run(client.fetch_quote("AAPL"))
	clock[0] = Decimal("31")
	fallback_quote, warnings = asyncio.run(client.fetch_quote("AAPL"))

	assert cached_quote.price == Decimal("88.8")
	assert fallback_quote.price == Decimal("88.8")
	assert warnings == ["AAPL 行情源不可用，已回退到最近缓存值: provider down"]
	assert provider.calls == 2


def test_fetch_quote_prefers_stale_cache_and_refreshes_in_background() -> None:
	async def scenario() -> None:
		clock = [Decimal("0")]
		provider = DeferredQuoteProvider(
			_make_quote(price=88.8),
			_make_quote(price=99.9),
		)
		client = MarketDataClient(
			quote_provider=provider,
			quote_cache=TTLCache[Quote](now=lambda: clock[0]),
			quote_ttl_seconds=30,
		)

		initial_quote, initial_warnings = await client.fetch_quote("AAPL")
		assert initial_quote.price == Decimal("88.8")
		assert initial_warnings == []

		clock[0] = Decimal("31")
		stale_quote, stale_warnings = await client.fetch_quote("AAPL", prefer_stale=True)
		assert stale_quote.price == Decimal("88.8")
		assert stale_warnings == []

		await asyncio.sleep(0)
		assert provider.calls == 2

		provider.release_refresh()
		await asyncio.sleep(0)

		refreshed_quote, refreshed_warnings = await client.fetch_quote("AAPL")
		assert refreshed_quote.price == Decimal("99.9")
		assert refreshed_warnings == []

	asyncio.run(scenario())


def test_fetch_quote_prefers_stale_cache_without_background_refresh_when_disabled() -> None:
	async def scenario() -> None:
		clock = [Decimal("0")]
		provider = DeferredQuoteProvider(
			_make_quote(price=88.8),
			_make_quote(price=99.9),
		)
		client = MarketDataClient(
			quote_provider=provider,
			quote_cache=TTLCache[Quote](now=lambda: clock[0]),
			quote_ttl_seconds=30,
		)

		await client.fetch_quote("AAPL")
		clock[0] = Decimal("31")
		stale_quote, stale_warnings = await client.fetch_quote(
			"AAPL",
			prefer_stale=True,
			schedule_stale_refresh=False,
		)

		assert stale_quote.price == Decimal("88.8")
		assert stale_warnings == []
		await asyncio.sleep(0)
		assert provider.calls == 1

	asyncio.run(scenario())


def test_fetch_quote_does_not_hit_provider_when_cache_only_path_has_no_value() -> None:
	async def scenario() -> None:
		provider = SequenceQuoteProvider([_make_quote(price=88.8)])
		client = MarketDataClient(
			quote_provider=provider,
			quote_cache=TTLCache[Quote](),
			quote_ttl_seconds=30,
		)

		with pytest.raises(QuoteLookupError, match="cache is still warming"):
			await client.fetch_quote(
				"AAPL",
				prefer_stale=True,
				schedule_stale_refresh=False,
			)

		assert provider.calls == 0

	asyncio.run(scenario())


def test_fetch_quote_prefers_china_provider_for_hk_symbols() -> None:
	primary_provider = SequenceQuoteProvider([QuoteLookupError("rate limited")])
	fallback_provider = SequenceQuoteProvider([
		_make_quote(symbol="0700.HK", price=518.0, currency="HKD"),
	])
	client = MarketDataClient(
		quote_provider=primary_provider,
		fallback_quote_provider=fallback_provider,
	)

	quote, warnings = asyncio.run(client.fetch_quote("0700.HK"))

	assert quote.symbol == "0700.HK"
	assert quote.price == Decimal("518")
	assert quote.currency == "HKD"
	assert warnings == []
	assert primary_provider.calls == 0
	assert fallback_provider.calls == 1


def test_fetch_quote_retries_china_provider_once_before_failing() -> None:
	fallback_provider = SequenceQuoteProvider([
		QuoteLookupError("temporary timeout"),
		_make_quote(symbol="2015.HK", price=68.75, currency="HKD"),
	])
	client = MarketDataClient(
		fallback_quote_provider=fallback_provider,
	)

	quote, warnings = asyncio.run(client.fetch_quote("2015.HK"))

	assert quote.symbol == "2015.HK"
	assert quote.price == Decimal("68.75")
	assert quote.currency == "HKD"
	assert warnings == []
	assert fallback_provider.calls == 2


def test_fetch_quote_uses_backup_provider_when_china_sources_fail() -> None:
	fallback_provider = SequenceQuoteProvider([QuoteLookupError("eastmoney timeout")])
	backup_provider = SequenceQuoteProvider([
		_make_quote(symbol="1810.HK", price=42.5, currency="HKD"),
	])
	client = MarketDataClient(
		fallback_quote_provider=fallback_provider,
		backup_quote_provider=backup_provider,
	)

	quote, warnings = asyncio.run(client.fetch_quote("1810.HK"))

	assert quote.symbol == "1810.HK"
	assert quote.price == Decimal("42.5")
	assert quote.currency == "HKD"
	assert warnings == []
	assert fallback_provider.calls == 2
	assert backup_provider.calls == 1


def test_clear_runtime_caches_clears_quote_and_fx_but_keeps_search_by_default() -> None:
	client = MarketDataClient()
	client.quote_cache.set("AAPL", _make_quote(), ttl_seconds=60)
	client.fx_cache.set("USD:CNY", Decimal("7.2"), ttl_seconds=60)
	client.search_cache.set("aapl", [], ttl_seconds=60)

	client.clear_runtime_caches()

	assert client.quote_cache.get("AAPL") is None
	assert client.quote_cache.get_stale("AAPL") is not None
	assert client.fx_cache.get("USD:CNY") is None
	assert client.fx_cache.get_stale("USD:CNY") == Decimal("7.2")
	assert client.search_cache.get("aapl") == []


def test_clear_runtime_caches_keeps_search_stale_when_requested() -> None:
	client = MarketDataClient()
	client.search_cache.set("aapl", [], ttl_seconds=60)

	client.clear_runtime_caches(clear_search=True)

	assert client.search_cache.get("aapl") is None
	assert client.search_cache.get_stale("aapl") == []


def test_fetch_quote_prefers_crypto_provider_for_crypto_symbols() -> None:
	primary_provider = SequenceQuoteProvider([QuoteLookupError("rate limited")])
	crypto_provider = SequenceQuoteProvider([
		_make_quote(symbol="BTC-USD", price=84500.0, currency="USD"),
	])
	client = MarketDataClient(
		quote_provider=primary_provider,
		crypto_quote_provider=crypto_provider,
	)

	quote, warnings = asyncio.run(client.fetch_quote("BTC-USD"))

	assert quote.symbol == "BTC-USD"
	assert quote.price == Decimal("84500")
	assert quote.currency == "USD"
	assert warnings == []
	assert primary_provider.calls == 0
	assert crypto_provider.calls == 1


def test_search_securities_uses_cache_before_calling_provider() -> None:
	results = [
		SecuritySearchResult(
			symbol="0700.HK",
			name="Tencent Holdings",
			market="HK",
			currency="HKD",
			exchange="HKG",
		),
	]
	provider = SequenceSearchProvider([results])
	client = MarketDataClient(
		china_search_provider=SequenceSearchProvider([[]]),
		search_provider=provider,
	)

	first_results = asyncio.run(client.search_securities("bad symbol!"))
	second_results = asyncio.run(client.search_securities("Bad Symbol!"))

	assert first_results == results
	assert second_results == results
	assert provider.calls == 1
	assert provider.queries == ["bad symbol!"]


def test_search_securities_returns_local_alias_when_provider_fails() -> None:
	client = MarketDataClient(
		china_search_provider=SequenceSearchProvider([[]]),
		search_provider=SequenceSearchProvider([QuoteLookupError("rate limited")]),
	)

	results = asyncio.run(client.search_securities("腾讯"))

	assert results[0].symbol == "0700.HK"
	assert results[0].name == "腾讯控股"


def test_search_securities_returns_empty_list_when_provider_fails_without_local_match() -> None:
	client = MarketDataClient(
		china_search_provider=SequenceSearchProvider([QuoteLookupError("rate limited")]),
		search_provider=SequenceSearchProvider([QuoteLookupError("rate limited")]),
	)

	results = asyncio.run(client.search_securities("unmatched query"))

	assert results == []


def test_build_local_search_results_supports_symbol_fallback() -> None:
	results = build_local_search_results("700")

	assert results[0].symbol == "0700.HK"


def test_build_local_search_results_supports_crypto_aliases() -> None:
	results = build_local_search_results("比特币")

	assert results[0].symbol == "BTC-USD"
	assert results[0].source == "Bitget"


def test_search_securities_skips_global_provider_when_local_matches_exist() -> None:
	local_query = "btc"
	global_provider = SequenceSearchProvider([[
		SecuritySearchResult(
			symbol="BTC-USD",
			name="Bitcoin",
			market="CRYPTO",
			currency="USD",
			exchange="CCC",
			source="Yahoo Finance",
		),
	]])
	client = MarketDataClient(
		china_search_provider=SequenceSearchProvider([[]]),
		search_provider=global_provider,
	)

	results = asyncio.run(client.search_securities(local_query))

	assert len(results) == 1
	assert results[0].source == "Bitget"
	assert global_provider.calls == 0


def test_build_local_search_results_supports_usdt_alias() -> None:
	results = build_local_search_results("usdt")

	assert results[0].symbol == "USDT-USD"
	assert results[0].market == "CRYPTO"
	assert results[0].source == "Bitget"


def test_fetch_quote_uses_crypto_market_hint_for_stablecoin_symbol() -> None:
	client = MarketDataClient(
		quote_provider=SequenceQuoteProvider([QuoteLookupError("rate limited")]),
		crypto_quote_provider=BitgetQuoteProvider(),
	)

	quote, warnings = asyncio.run(client.fetch_quote("USDT", "CRYPTO"))

	assert quote.symbol == "USDT-USD"
	assert quote.price == Decimal("1")
	assert quote.currency == "USD"
	assert warnings == []


def test_parse_eastmoney_search_item_maps_a_share_codes() -> None:
	result = parse_eastmoney_search_item({
		"Code": "688256",
		"Name": "寒武纪-U",
		"QuoteID": "1.688256",
		"JYS": "23",
	})

	assert result is not None
	assert result.symbol == "688256.SS"
	assert result.market == "CN"


def test_parse_eastmoney_search_item_maps_hk_codes() -> None:
	result = parse_eastmoney_search_item({
		"Code": "02015",
		"Name": "理想汽车-W",
		"QuoteID": "116.02015",
		"JYS": "HK",
		"Classify": "HK",
	})

	assert result is not None
	assert result.symbol == "2015.HK"
	assert result.market == "HK"


def test_eastmoney_search_provider_returns_empty_results_for_null_data(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	class StaticAsyncClient:
		def __init__(self, *args, **kwargs) -> None:
			pass

		async def __aenter__(self) -> "StaticAsyncClient":
			return self

		async def __aexit__(self, exc_type, exc, tb) -> None:
			return None

		async def get(self, *args, **kwargs) -> httpx.Response:
			request = httpx.Request("GET", EastMoneySecuritySearchProvider.EASTMONEY_SEARCH_URL)
			return httpx.Response(
				200,
				request=request,
				json={"QuotationCodeTable": {"Data": None, "Status": 0, "TotalCount": 0}},
			)

	monkeypatch.setattr("app.services.market_data_parts.providers.httpx.AsyncClient", StaticAsyncClient)
	provider = EastMoneySecuritySearchProvider()

	results = asyncio.run(provider.search("不存在的标的xyz123"))

	assert results == []


def test_yahoo_search_provider_returns_empty_results_for_null_quotes(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	class StaticAsyncClient:
		def __init__(self, *args, **kwargs) -> None:
			pass

		async def __aenter__(self) -> "StaticAsyncClient":
			return self

		async def __aexit__(self, exc_type, exc, tb) -> None:
			return None

		async def get(self, *args, **kwargs) -> httpx.Response:
			request = httpx.Request("GET", YahooSecuritySearchProvider.YAHOO_SEARCH_URL)
			return httpx.Response(
				200,
				request=request,
				json={"count": 0, "quotes": None},
			)

	monkeypatch.setattr("app.services.market_data_parts.providers.httpx.AsyncClient", StaticAsyncClient)
	provider = YahooSecuritySearchProvider()

	results = asyncio.run(provider.search("zzznotfound"))

	assert results == []


def test_yahoo_quote_provider_falls_back_to_chart_endpoint_when_quote_endpoint_unauthorized(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	requested_urls: list[str] = []

	class StaticAsyncClient:
		def __init__(self, *args, **kwargs) -> None:
			pass

		async def __aenter__(self) -> "StaticAsyncClient":
			return self

		async def __aexit__(self, exc_type, exc, tb) -> None:
			return None

		async def get(self, url: str, *args, **kwargs) -> httpx.Response:
			requested_urls.append(url)
			if url == YahooQuoteProvider.YAHOO_QUOTE_URL:
				request = httpx.Request("GET", url)
				response = httpx.Response(
					401,
					request=request,
					json={
						"finance": {
							"result": None,
							"error": {"code": "Unauthorized"},
						},
					},
				)
				raise httpx.HTTPStatusError(
					"401 Unauthorized",
					request=request,
					response=response,
				)

			request = httpx.Request("GET", url)
			return httpx.Response(
				200,
				request=request,
				json={
					"chart": {
						"result": [
							{
								"meta": {
									"currency": "USD",
									"symbol": "AAPL",
									"shortName": "Apple Inc.",
									"regularMarketPrice": 252.82,
									"regularMarketTime": 1773691203,
								},
							},
						],
					},
				},
			)

	monkeypatch.setattr("app.services.market_data_parts.providers.httpx.AsyncClient", StaticAsyncClient)
	provider = YahooQuoteProvider()

	quote = asyncio.run(provider.fetch_quote("AAPL"))

	assert quote.symbol == "AAPL"
	assert quote.name == "Apple Inc."
	assert quote.price == Decimal("252.82")
	assert quote.currency == "USD"
	assert requested_urls == [
		YahooQuoteProvider.YAHOO_QUOTE_URL,
		f"{YahooQuoteProvider.YAHOO_CHART_URL}/AAPL",
	]


def test_eastmoney_quote_provider_exposes_http_status_details(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	class FailingAsyncClient:
		def __init__(self, *args, **kwargs) -> None:
			pass

		async def __aenter__(self) -> "FailingAsyncClient":
			return self

		async def __aexit__(self, exc_type, exc, tb) -> None:
			return None

		async def get(self, *args, **kwargs):
			request = httpx.Request("GET", "https://push2.eastmoney.com/api/qt/stock/get")
			response = httpx.Response(429, request=request)
			raise httpx.HTTPStatusError(
				"429 Too Many Requests",
				request=request,
				response=response,
			)

	monkeypatch.setattr("app.services.market_data_parts.providers.httpx.AsyncClient", FailingAsyncClient)
	provider = EastMoneyQuoteProvider()

	with pytest.raises(
		QuoteLookupError,
		match=r"Eastmoney quote request failed for 1810\.HK \(HTTP 429",
	):
		asyncio.run(provider.fetch_quote("1810.HK"))


def test_frankfurter_rate_provider_uses_latest_official_endpoint(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	class StaticAsyncClient:
		def __init__(self, *args, **kwargs) -> None:
			pass

		async def __aenter__(self) -> "StaticAsyncClient":
			return self

		async def __aexit__(self, exc_type, exc, tb) -> None:
			return None

		async def get(self, url: str, *args, **kwargs) -> httpx.Response:
			assert url == FrankfurterRateProvider.FRANKFURTER_URL
			assert kwargs["params"] == {"base": "USD", "symbols": "CNY"}
			request = httpx.Request("GET", url, params=kwargs["params"])
			return httpx.Response(
				200,
				request=request,
				json={"base": "USD", "rates": {"CNY": 6.8961}},
			)

	monkeypatch.setattr("app.services.market_data_parts.providers.httpx.AsyncClient", StaticAsyncClient)
	provider = FrankfurterRateProvider()

	rate = asyncio.run(provider.fetch_rate("USD", "CNY"))

	assert rate == Decimal("6.8961")


def test_fetch_fx_rate_returns_stale_cache_when_providers_fail() -> None:
	clock = [Decimal("0")]
	client = MarketDataClient(
		quote_provider=SequenceQuoteProvider([QuoteLookupError("quote down")]),
		fx_provider=SequenceRateProvider([QuoteLookupError("fx down")]),
		fallback_fx_provider=SequenceRateProvider([QuoteLookupError("fx backup down")]),
		quote_cache=TTLCache[Quote](now=lambda: clock[0]),
		fx_cache=TTLCache[Decimal](now=lambda: clock[0]),
		fx_ttl_seconds=300,
	)
	client.fx_cache.set("USD:CNY", Decimal("7.2"), ttl_seconds=300)

	clock[0] = Decimal("301")
	rate, warnings = asyncio.run(client.fetch_fx_rate("usd", "cny"))

	assert rate == Decimal("7.2")
	assert warnings == [
		"USD/CNY 汇率源不可用，已回退到最近缓存值: fx down; fx backup down",
	]


def test_fetch_fx_rate_raises_when_no_cache_and_providers_fail() -> None:
	client = MarketDataClient(
		quote_provider=SequenceQuoteProvider([QuoteLookupError("quote down")]),
		fx_provider=SequenceRateProvider([QuoteLookupError("fx down")]),
		fallback_fx_provider=SequenceRateProvider([QuoteLookupError("fx backup down")]),
	)

	with pytest.raises(QuoteLookupError, match="fx down; fx backup down"):
		asyncio.run(client.fetch_fx_rate("USD", "CNY"))


def test_fetch_fx_rate_uses_fallback_provider_when_primary_fails() -> None:
	primary_fx_provider = SequenceRateProvider([QuoteLookupError("frankfurter timeout")])
	fallback_fx_provider = SequenceRateProvider([Decimal("6.95")])
	client = MarketDataClient(
		fx_provider=primary_fx_provider,
		fallback_fx_provider=fallback_fx_provider,
	)

	rate, warnings = asyncio.run(client.fetch_fx_rate("USD", "CNY"))

	assert rate == Decimal("6.95")
	assert warnings == []
	assert primary_fx_provider.calls == 2
	assert fallback_fx_provider.calls == 1


def test_fetch_fx_rate_prefers_stale_cache_and_refreshes_in_background() -> None:
	async def scenario() -> None:
		clock = [Decimal("0")]
		provider = DeferredRateProvider(Decimal("7.2"), Decimal("7.3"))
		client = MarketDataClient(
			fx_provider=provider,
			fallback_fx_provider=SequenceRateProvider([QuoteLookupError("unused")]),
			fx_cache=TTLCache[Decimal](now=lambda: clock[0]),
			fx_ttl_seconds=30,
		)

		initial_rate, initial_warnings = await client.fetch_fx_rate("USD", "CNY")
		assert initial_rate == Decimal("7.2")
		assert initial_warnings == []

		clock[0] = Decimal("31")
		stale_rate, stale_warnings = await client.fetch_fx_rate(
			"USD",
			"CNY",
			prefer_stale=True,
		)
		assert stale_rate == Decimal("7.2")
		assert stale_warnings == []

		await asyncio.sleep(0)
		assert provider.calls == 2

		provider.release_refresh()
		await asyncio.sleep(0)

		refreshed_rate, refreshed_warnings = await client.fetch_fx_rate("USD", "CNY")
		assert refreshed_rate == Decimal("7.3")
		assert refreshed_warnings == []

	asyncio.run(scenario())


def test_fetch_fx_rate_prefers_stale_cache_without_background_refresh_when_disabled() -> None:
	async def scenario() -> None:
		clock = [Decimal("0")]
		provider = DeferredRateProvider(Decimal("7.2"), Decimal("7.3"))
		client = MarketDataClient(
			fx_provider=provider,
			fallback_fx_provider=SequenceRateProvider([QuoteLookupError("unused")]),
			fx_cache=TTLCache[Decimal](now=lambda: clock[0]),
			fx_ttl_seconds=30,
		)

		await client.fetch_fx_rate("USD", "CNY")
		clock[0] = Decimal("31")
		stale_rate, stale_warnings = await client.fetch_fx_rate(
			"USD",
			"CNY",
			prefer_stale=True,
			schedule_stale_refresh=False,
		)

		assert stale_rate == Decimal("7.2")
		assert stale_warnings == []
		await asyncio.sleep(0)
		assert provider.calls == 1

	asyncio.run(scenario())


def test_fetch_fx_rate_does_not_hit_provider_when_cache_only_path_has_no_value() -> None:
	async def scenario() -> None:
		provider = SequenceRateProvider([Decimal("7.2")])
		client = MarketDataClient(
			fx_provider=provider,
			fallback_fx_provider=SequenceRateProvider([QuoteLookupError("unused")]),
			fx_cache=TTLCache[Decimal](),
			fx_ttl_seconds=30,
		)

		with pytest.raises(QuoteLookupError, match="cache is still warming"):
			await client.fetch_fx_rate(
				"USD",
				"CNY",
				prefer_stale=True,
				schedule_stale_refresh=False,
			)

		assert provider.calls == 0

	asyncio.run(scenario())


class FailingMarketDataClient:
	async def fetch_quote(
		self,
		symbol: str,
		market: str | None = None,
	) -> tuple[Quote, list[str]]:
		raise QuoteLookupError("provider down")

	async def fetch_fx_rate(self, from_currency: str, to_currency: str) -> tuple[Decimal, list[str]]:
		raise AssertionError("FX lookup should not run when quote lookup fails.")


class ConcurrentMarketDataClient:
	def __init__(self) -> None:
		self.active_quote_requests = 0
		self.max_active_quote_requests = 0

	async def fetch_quote(
		self,
		symbol: str,
		market: str | None = None,
	) -> tuple[Quote, list[str]]:
		self.active_quote_requests += 1
		self.max_active_quote_requests = max(
			self.max_active_quote_requests,
			self.active_quote_requests,
		)
		try:
			await asyncio.sleep(0.01)
			return _make_quote(symbol=symbol), []
		finally:
			self.active_quote_requests -= 1

	async def fetch_fx_rate(self, from_currency: str, to_currency: str) -> tuple[Decimal, list[str]]:
		if from_currency.upper() == to_currency.upper():
			return Decimal("1"), []
		return Decimal("7"), []


def test_value_holdings_turns_provider_failure_into_warning(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	holding = SecurityHolding(
		user_id="tester",
		symbol="AAPL",
		name="Apple",
		quantity=2,
		fallback_currency="USD",
	)
	monkeypatch.setattr(service_context, "market_data_client", FailingMarketDataClient())

	items, total, warnings = asyncio.run(_value_holdings([holding]))

	assert total == Decimal("0")
	assert items[0].price == Decimal("0")
	assert items[0].fx_to_cny == Decimal("0")
	assert items[0].price_currency == "USD"
	assert warnings == ["持仓 AAPL 行情更新中"]


def test_value_holdings_fetches_quotes_concurrently(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	client = ConcurrentMarketDataClient()
	holdings = [
		SecurityHolding(
			user_id="tester",
			symbol="AAPL",
			name="Apple",
			quantity=1,
			fallback_currency="USD",
			market="US",
		),
		SecurityHolding(
			user_id="tester",
			symbol="MSFT",
			name="Microsoft",
			quantity=1,
			fallback_currency="USD",
			market="US",
		),
		SecurityHolding(
			user_id="tester",
			symbol="GOOG",
			name="Alphabet",
			quantity=1,
			fallback_currency="USD",
			market="US",
		),
	]
	monkeypatch.setattr(service_context, "market_data_client", client)

	items, total, warnings = asyncio.run(_value_holdings(holdings))

	assert [item.symbol for item in items] == ["AAPL", "MSFT", "GOOG"]
	assert total == Decimal("2100")
	assert warnings == []
	assert client.max_active_quote_requests >= 2
