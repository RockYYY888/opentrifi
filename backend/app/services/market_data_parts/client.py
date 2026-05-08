from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
from typing import Protocol, TypeVar

import httpx

from app.fixed_precision import quantize_decimal
from app.services.cache import TTLCache
from app.services.market_data_parts.common import Quote, QuoteLookupError, SecuritySearchResult
from app.services.market_data_parts.providers import (
	BitgetQuoteProvider,
	EastMoneyQuoteProvider,
	EastMoneySecuritySearchProvider,
	FrankfurterRateProvider,
	OpenExchangeRateProvider,
	TencentQuoteProvider,
	YahooQuoteProvider,
	YahooSecuritySearchProvider,
)
from app.services.market_data_parts.search_catalog import (
	_contains_cjk_characters,
	_merge_search_results,
	build_local_search_results,
)
from app.services.market_data_parts.symbols import infer_security_market, normalize_symbol_for_market

logger = logging.getLogger(__name__)
CacheValue = TypeVar("CacheValue")


class QuoteProvider(Protocol):
	async def fetch_quote(self, symbol: str) -> Quote: ...


class RateProvider(Protocol):
	async def fetch_rate(self, from_currency: str, to_currency: str) -> Decimal: ...


class SecuritySearchProvider(Protocol):
	async def search(self, query: str) -> list[SecuritySearchResult]: ...


class CacheStore(Protocol[CacheValue]):
	def get(self, key: str) -> CacheValue | None: ...
	def get_stale(self, key: str) -> CacheValue | None: ...
	def set(self, key: str, value: CacheValue, ttl_seconds: Decimal | int) -> CacheValue: ...
	def clear(self) -> None: ...
	def expire_all(self) -> None: ...


class MarketDataClient:
	def __init__(
		self,
		quote_provider: QuoteProvider | None = None,
		fallback_quote_provider: QuoteProvider | None = None,
		backup_quote_provider: QuoteProvider | None = None,
		crypto_quote_provider: QuoteProvider | None = None,
		china_search_provider: SecuritySearchProvider | None = None,
		search_provider: SecuritySearchProvider | None = None,
		fx_provider: RateProvider | None = None,
		fallback_fx_provider: RateProvider | None = None,
		quote_cache: CacheStore[Quote] | None = None,
		search_cache: CacheStore[list[SecuritySearchResult]] | None = None,
		fx_cache: CacheStore[Decimal] | None = None,
		quote_ttl_seconds: int = 60,
		search_ttl_seconds: int = 300,
		fx_ttl_seconds: int = 600,
	) -> None:
		self.quote_provider = quote_provider or YahooQuoteProvider()
		self.fallback_quote_provider = fallback_quote_provider or EastMoneyQuoteProvider()
		self.backup_quote_provider = backup_quote_provider or TencentQuoteProvider()
		self.crypto_quote_provider = crypto_quote_provider or BitgetQuoteProvider()
		self.china_search_provider = china_search_provider or EastMoneySecuritySearchProvider()
		self.search_provider = search_provider or YahooSecuritySearchProvider()
		self.fx_provider = fx_provider or FrankfurterRateProvider()
		self.fallback_fx_provider = fallback_fx_provider or OpenExchangeRateProvider()
		self.quote_cache = quote_cache or TTLCache[Quote]()
		self.search_cache = search_cache or TTLCache[list[SecuritySearchResult]]()
		self.fx_cache = fx_cache or TTLCache[Decimal]()
		self.quote_ttl_seconds = quote_ttl_seconds
		self.search_ttl_seconds = search_ttl_seconds
		self.fx_ttl_seconds = fx_ttl_seconds
		self._quote_refresh_tasks: dict[str, asyncio.Task[None]] = {}
		self._fx_refresh_tasks: dict[str, asyncio.Task[None]] = {}

	async def _fetch_quote_with_retry(
		self,
		provider: QuoteProvider,
		symbol: str,
		retry_attempts: int = 0,
	) -> Quote:
		"""Retry transient quote lookups a limited number of times before failing."""
		last_error: QuoteLookupError | None = None

		for attempt in range(retry_attempts + 1):
			try:
				return await provider.fetch_quote(symbol)
			except QuoteLookupError as exc:
				last_error = exc
				if attempt >= retry_attempts:
					break
				await asyncio.sleep(0.25)

		raise last_error or QuoteLookupError(f"Quote provider request failed for {symbol}.")

	async def _fetch_fx_rate_with_retry(
		self,
		provider: RateProvider,
		from_currency: str,
		to_currency: str,
		retry_attempts: int = 0,
	) -> Decimal:
		"""Retry transient FX lookups a limited number of times before failing."""
		last_error: QuoteLookupError | ValueError | None = None

		for attempt in range(retry_attempts + 1):
			try:
				return quantize_decimal(await provider.fetch_rate(from_currency, to_currency))
			except (QuoteLookupError, ValueError) as exc:
				last_error = exc
				if attempt >= retry_attempts:
					break
				await asyncio.sleep(0.25)

		raise last_error or QuoteLookupError(
			f"FX provider request failed for {from_currency}/{to_currency}.",
		)

	def clear_runtime_caches(self, *, clear_search: bool = False) -> None:
		"""Expire runtime caches so refreshes refetch while stale values remain available."""
		self.quote_cache.expire_all()
		self.fx_cache.expire_all()
		if clear_search:
			self.search_cache.expire_all()

	def _resolve_quote_provider_chain(
		self,
		resolved_market: str | None,
	) -> list[tuple[QuoteProvider, int]]:
		if resolved_market in {"HK", "CN"}:
			return [
				(self.fallback_quote_provider, 1),
				(self.backup_quote_provider, 1),
				(self.quote_provider, 0),
			]
		if resolved_market == "CRYPTO":
			return [(self.crypto_quote_provider, 0)]
		return [(self.quote_provider, 0)]

	async def _fetch_quote_from_providers(
		self,
		normalized_symbol: str,
		resolved_market: str | None,
	) -> Quote:
		errors: list[str] = []
		for provider, retry_attempts in self._resolve_quote_provider_chain(resolved_market):
			try:
				return await self._fetch_quote_with_retry(
					provider,
					normalized_symbol,
					retry_attempts=retry_attempts,
				)
			except QuoteLookupError as exc:
				errors.append(str(exc))
				continue

		error_message = "; ".join(dict.fromkeys(errors))
		if not error_message:
			error_message = f"Quote provider request failed for {normalized_symbol}."
		raise QuoteLookupError(error_message)

	async def _refresh_quote_cache(
		self,
		normalized_symbol: str,
		resolved_market: str | None,
	) -> None:
		quote = await self._fetch_quote_from_providers(normalized_symbol, resolved_market)
		self.quote_cache.set(normalized_symbol, quote, ttl_seconds=self.quote_ttl_seconds)

	def _ensure_quote_refresh(
		self,
		normalized_symbol: str,
		resolved_market: str | None,
	) -> None:
		existing_task = self._quote_refresh_tasks.get(normalized_symbol)
		if existing_task is not None and not existing_task.done():
			return

		refresh_task = asyncio.create_task(
			self._refresh_quote_cache(normalized_symbol, resolved_market),
			name=f"quote-refresh:{normalized_symbol}",
		)

		def cleanup(task: asyncio.Task[None]) -> None:
			self._quote_refresh_tasks.pop(normalized_symbol, None)
			try:
				task.result()
			except asyncio.CancelledError:
				return
			except QuoteLookupError as exc:
				logger.debug("Background quote refresh failed for %s: %s", normalized_symbol, exc)
			except Exception:
				logger.exception("Background quote refresh crashed for %s", normalized_symbol)

		refresh_task.add_done_callback(cleanup)
		self._quote_refresh_tasks[normalized_symbol] = refresh_task

	async def _fetch_fx_rate_from_providers(
		self,
		from_code: str,
		to_code: str,
	) -> Decimal:
		errors: list[str] = []
		rate_providers = (
			(self.fx_provider, 1),
			(self.fallback_fx_provider, 1),
		)
		for provider, retry_attempts in rate_providers:
			try:
				return await self._fetch_fx_rate_with_retry(
					provider,
					from_code,
					to_code,
					retry_attempts=retry_attempts,
				)
			except (QuoteLookupError, ValueError) as exc:
				errors.append(str(exc))
				continue

		error_message = "; ".join(dict.fromkeys(errors))
		if not error_message:
			error_message = f"FX provider request failed for {from_code}/{to_code}."
		raise QuoteLookupError(error_message)

	async def _refresh_fx_cache(
		self,
		from_code: str,
		to_code: str,
	) -> None:
		cache_key = f"{from_code}:{to_code}"
		rate = await self._fetch_fx_rate_from_providers(from_code, to_code)
		self.fx_cache.set(cache_key, rate, ttl_seconds=self.fx_ttl_seconds)

	def _ensure_fx_refresh(
		self,
		from_code: str,
		to_code: str,
	) -> None:
		cache_key = f"{from_code}:{to_code}"
		existing_task = self._fx_refresh_tasks.get(cache_key)
		if existing_task is not None and not existing_task.done():
			return

		refresh_task = asyncio.create_task(
			self._refresh_fx_cache(from_code, to_code),
			name=f"fx-refresh:{cache_key}",
		)

		def cleanup(task: asyncio.Task[None]) -> None:
			self._fx_refresh_tasks.pop(cache_key, None)
			try:
				task.result()
			except asyncio.CancelledError:
				return
			except QuoteLookupError as exc:
				logger.debug("Background FX refresh failed for %s: %s", cache_key, exc)
			except Exception:
				logger.exception("Background FX refresh crashed for %s", cache_key)

		refresh_task.add_done_callback(cleanup)
		self._fx_refresh_tasks[cache_key] = refresh_task

	async def fetch_quote(
		self,
		symbol: str,
		market: str | None = None,
		*,
		prefer_stale: bool = False,
		schedule_stale_refresh: bool = True,
	) -> tuple[Quote, list[str]]:
		"""Fetch a quote, preferring a fresh cache hit and falling back to stale data."""
		normalized_market = (market or "").strip().upper() or None
		normalized_symbol = normalize_symbol_for_market(symbol, normalized_market)
		resolved_market = normalized_market or infer_security_market(normalized_symbol)
		cached_quote = self.quote_cache.get(normalized_symbol)
		if cached_quote is not None:
			return cached_quote, []
		stale_quote = self.quote_cache.get_stale(normalized_symbol)
		if prefer_stale and stale_quote is not None:
			if schedule_stale_refresh:
				self._ensure_quote_refresh(normalized_symbol, resolved_market)
			return stale_quote, []
		if prefer_stale and not schedule_stale_refresh:
			raise QuoteLookupError(f"{normalized_symbol} quote cache is still warming.")

		try:
			quote = await self._fetch_quote_from_providers(normalized_symbol, resolved_market)
		except QuoteLookupError as exc:
			error_message = str(exc)
			stale_quote = self.quote_cache.get_stale(normalized_symbol)
			if stale_quote is not None:
				return stale_quote, [
					f"{normalized_symbol} 行情源不可用，已回退到最近缓存值: {error_message}",
				]
			raise

		self.quote_cache.set(normalized_symbol, quote, ttl_seconds=self.quote_ttl_seconds)
		return quote, []

	async def fetch_fx_rate(
		self,
		from_currency: str,
		to_currency: str,
		*,
		prefer_stale: bool = False,
		schedule_stale_refresh: bool = True,
	) -> tuple[Decimal, list[str]]:
		"""Fetch an FX rate from the dedicated FX provider and fall back to stale cache."""
		from_code = from_currency.strip().upper()
		to_code = to_currency.strip().upper()
		if from_code == to_code:
			return Decimal("1"), []

		cache_key = f"{from_code}:{to_code}"
		cached_rate = self.fx_cache.get(cache_key)
		if cached_rate is not None:
			return cached_rate, []
		stale_rate = self.fx_cache.get_stale(cache_key)
		if prefer_stale and stale_rate is not None:
			if schedule_stale_refresh:
				self._ensure_fx_refresh(from_code, to_code)
			return stale_rate, []
		if prefer_stale and not schedule_stale_refresh:
			raise QuoteLookupError(f"{from_code}/{to_code} FX cache is still warming.")

		try:
			rate = await self._fetch_fx_rate_from_providers(from_code, to_code)
		except QuoteLookupError as exc:
			error_message = str(exc)
			stale_rate = self.fx_cache.get_stale(cache_key)
			if stale_rate is not None:
				return stale_rate, [
					f"{from_code}/{to_code} 汇率源不可用，已回退到最近缓存值: {error_message}",
				]
			raise

		self.fx_cache.set(cache_key, rate, ttl_seconds=self.fx_ttl_seconds)
		return rate, []

	async def fetch_hourly_price_series(
		self,
		symbol: str,
		*,
		market: str | None = None,
		start_at: datetime,
		end_at: datetime,
	) -> tuple[list[tuple[datetime, Decimal]], str | None, list[str]]:
		"""Fetch price history from a time point to now with minimal requests and bucket-ready output."""
		normalized_market = (market or "").strip().upper() or None
		normalized_symbol = normalize_symbol_for_market(symbol, normalized_market)
		start_utc = (
			start_at.replace(tzinfo=timezone.utc)
			if start_at.tzinfo is None
			else start_at.astimezone(timezone.utc)
		)
		end_utc = (
			end_at.replace(tzinfo=timezone.utc)
			if end_at.tzinfo is None
			else end_at.astimezone(timezone.utc)
		)
		if end_utc <= start_utc:
			return [], None, []

		async def fetch_chart_points(
			interval: str,
			segment_start: datetime,
			segment_end: datetime,
		) -> tuple[list[tuple[datetime, Decimal]], str | None, str | None]:
			url = f"https://query1.finance.yahoo.com/v8/finance/chart/{normalized_symbol}"
			params = {
				"period1": int(segment_start.timestamp()),
				"period2": int(segment_end.timestamp()),
				"interval": interval,
				"events": "history",
				"includePrePost": "false",
			}
			try:
				async with httpx.AsyncClient(timeout=15) as client:
					response = await client.get(
						url,
						params=params,
						headers={"User-Agent": "Mozilla/5.0"},
					)
					response.raise_for_status()
					payload = response.json()
			except httpx.HTTPError as exc:
				return [], None, f"{normalized_symbol} {interval} 历史行情拉取失败: {exc}"

			result_list = payload.get("chart", {}).get("result") or []
			if not result_list:
				return [], None, f"{normalized_symbol} {interval} 历史行情为空。"

			result = result_list[0]
			timestamps = result.get("timestamp") or []
			quotes = (result.get("indicators") or {}).get("quote") or []
			closes = (quotes[0] if quotes else {}).get("close") or []
			currency = (result.get("meta") or {}).get("currency")
			points: list[tuple[datetime, Decimal]] = []
			for index, raw_timestamp in enumerate(timestamps):
				try:
					timestamp = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
				except (TypeError, ValueError):
					continue
				if timestamp < segment_start or timestamp > segment_end:
					continue

				close_value = closes[index] if index < len(closes) else None
				if close_value in (None, 0):
					continue

				try:
					price = quantize_decimal(close_value)
				except (ArithmeticError, TypeError, ValueError):
					continue
				if price <= 0:
					continue

				if interval == "1h":
					bucket = timestamp.replace(minute=0, second=0, microsecond=0)
				else:
					bucket = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
				points.append((bucket, price))

			return points, str(currency).upper() if currency else None, None

		warnings: list[str] = []
		all_points: list[tuple[datetime, Decimal]] = []
		currency: str | None = None

		# Yahoo's 1h chart API has long-range constraints. Chunk requests by window to keep
		# full hourly granularity from start_at to end_at without falling back to daily bars.
		hourly_window_span = timedelta(days=729)
		segment_start = start_utc
		while segment_start < end_utc:
			segment_end = min(segment_start + hourly_window_span, end_utc)
			hourly_points, hourly_currency, hourly_warning = await fetch_chart_points(
				"1h",
				segment_start,
				segment_end,
			)
			if hourly_warning:
				warnings.append(hourly_warning)
			else:
				all_points.extend(hourly_points)
				currency = hourly_currency or currency
			segment_start = segment_end

		deduped_points: dict[datetime, Decimal] = {}
		for bucket, price in all_points:
			deduped_points[bucket] = price

		return sorted(deduped_points.items(), key=lambda item: item[0]), currency, warnings

	async def search_securities(self, query: str) -> list[SecuritySearchResult]:
		"""Search securities by name or code with a short-lived cache."""
		normalized_query = query.strip()
		if not normalized_query:
			return []

		cache_key = normalized_query.casefold()
		cached_results = self.search_cache.get(cache_key)
		if cached_results is not None:
			return cached_results

		local_results = build_local_search_results(normalized_query)
		china_results: list[SecuritySearchResult] = []
		global_results: list[SecuritySearchResult] = []
		should_query_global_provider = not local_results and not _contains_cjk_characters(
			normalized_query,
		)

		try:
			china_results = await self.china_search_provider.search(normalized_query)
		except QuoteLookupError:
			china_results = []

		if should_query_global_provider:
			try:
				global_results = await self.search_provider.search(normalized_query)
			except QuoteLookupError:
				global_results = []

		results = _merge_search_results(local_results, _merge_search_results(china_results, global_results))
		self.search_cache.set(cache_key, results, ttl_seconds=self.search_ttl_seconds)
		return results
