from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.fixed_precision import quantize_decimal, to_decimal
from app.services.market_data_parts.common import (
	BITGET_STABLE_QUOTES,
	EASTMONEY_SEARCH_TOKEN,
	SEARCHABLE_QUOTE_TYPES,
	Quote,
	QuoteLookupError,
	SecuritySearchResult,
	_describe_http_error,
	_parse_epoch_millis,
	_parse_tencent_market_time,
)
from app.services.market_data_parts.search_catalog import parse_eastmoney_search_item
from app.services.market_data_parts.symbols import (
	_default_currency_for_market,
	build_bitget_symbol,
	build_eastmoney_secid,
	infer_security_market,
	normalize_symbol,
	normalize_symbol_for_market,
)

class YahooQuoteProvider:
	YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
	YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

	def __init__(self, timeout: int = 10) -> None:
		self.timeout = timeout

	async def _request_json(
		self,
		url: str,
		*,
		symbol: str,
		params: dict[str, object],
		source_label: str,
	) -> dict[str, object]:
		try:
			async with httpx.AsyncClient(timeout=self.timeout) as client:
				response = await client.get(
					url,
					params=params,
					headers={"User-Agent": "Mozilla/5.0"},
				)
				response.raise_for_status()
				payload = response.json()
		except httpx.HTTPError as exc:
			error_details = _describe_http_error(exc)
			raise QuoteLookupError(
				f"{source_label} request failed for {symbol} ({error_details}).",
			) from exc
		except ValueError as exc:
			raise QuoteLookupError(
				f"{source_label} returned invalid data for {symbol}.",
			) from exc

		if not isinstance(payload, dict):
			raise QuoteLookupError(f"{source_label} returned invalid data for {symbol}.")

		return payload

	@staticmethod
	def _parse_quote_payload(symbol: str, payload: dict[str, object]) -> Quote:
		results = payload.get("quoteResponse", {}).get("result", [])
		if not results:
			raise QuoteLookupError(f"No quote data returned for {symbol}.")

		result = results[0]
		price = result.get("regularMarketPrice")
		currency = result.get("currency") or result.get("financialCurrency")
		if price in (None, 0) or not currency:
			raise QuoteLookupError(f"Incomplete quote data returned for {symbol}.")

		timestamp = result.get("regularMarketTime")
		market_time = _parse_epoch_millis(timestamp)

		return Quote(
			symbol=result.get("symbol", symbol),
			name=result.get("shortName") or result.get("longName") or symbol,
			price=quantize_decimal(price),
			currency=str(currency).upper(),
			market_time=market_time,
		)

	@staticmethod
	def _parse_chart_payload(symbol: str, payload: dict[str, object]) -> Quote:
		result_list = payload.get("chart", {}).get("result") or []
		if not result_list:
			raise QuoteLookupError(f"No chart quote data returned for {symbol}.")

		result = result_list[0]
		meta = result.get("meta") or {}
		price = meta.get("regularMarketPrice")
		if price in (None, 0):
			quotes = (result.get("indicators") or {}).get("quote") or []
			closes = (quotes[0] if quotes else {}).get("close") or []
			price = next(
				(
					quantize_decimal(close_value)
					for close_value in reversed(closes)
					if close_value not in (None, 0)
				),
				None,
			)

		currency = meta.get("currency")
		if price in (None, 0) or not currency:
			raise QuoteLookupError(f"Incomplete chart quote data returned for {symbol}.")

		return Quote(
			symbol=str(meta.get("symbol") or symbol),
			name=str(meta.get("shortName") or meta.get("longName") or symbol),
			price=quantize_decimal(price),
			currency=str(currency).upper(),
			market_time=_parse_epoch_millis(meta.get("regularMarketTime")),
		)

	async def fetch_quote(self, symbol: str) -> Quote:
		"""Fetch the latest quote from Yahoo and fall back to the chart API when needed."""
		quote_error: QuoteLookupError | None = None

		try:
			payload = await self._request_json(
				self.YAHOO_QUOTE_URL,
				symbol=symbol,
				params={"symbols": symbol},
				source_label="Yahoo quote",
			)
			return self._parse_quote_payload(symbol, payload)
		except QuoteLookupError as exc:
			quote_error = exc

		try:
			payload = await self._request_json(
				f"{self.YAHOO_CHART_URL}/{symbol}",
				symbol=symbol,
				params={
					"interval": "1d",
					"range": "1d",
					"includePrePost": "false",
					"events": "history",
				},
				source_label="Yahoo chart quote fallback",
			)
			return self._parse_chart_payload(symbol, payload)
		except QuoteLookupError as exc:
			if quote_error is None:
				raise
			raise QuoteLookupError(f"{quote_error}; {exc}") from exc

class EastMoneyQuoteProvider:
	EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"

	def __init__(self, timeout: int = 10) -> None:
		self.timeout = timeout

	async def fetch_quote(self, symbol: str) -> Quote:
		"""Fetch CN/HK quotes from Eastmoney when the primary source is unavailable."""
		try:
			secid = build_eastmoney_secid(symbol)
		except ValueError as exc:
			raise QuoteLookupError(str(exc)) from exc

		normalized_symbol = normalize_symbol(symbol)

		try:
			async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
				response = await client.get(
					self.EASTMONEY_QUOTE_URL,
					params={"secid": secid, "fields": "f43,f57,f58"},
					headers={
						"User-Agent": "Mozilla/5.0",
						"Referer": "https://quote.eastmoney.com/",
					},
				)
				response.raise_for_status()
				payload = response.json()
		except httpx.HTTPError as exc:
			error_details = _describe_http_error(exc)
			raise QuoteLookupError(
				f"Eastmoney quote request failed for {normalized_symbol} ({error_details}).",
			) from exc

		data = payload.get("data") or {}
		raw_price = data.get("f43")
		raw_name = data.get("f58")
		if raw_price in (None, 0):
			raise QuoteLookupError(f"No Eastmoney quote data returned for {normalized_symbol}.")

		scale = 1000 if normalized_symbol.endswith(".HK") else 100
		price = quantize_decimal(to_decimal(raw_price) / Decimal(scale))
		if price <= 0:
			raise QuoteLookupError(f"Incomplete Eastmoney quote data returned for {normalized_symbol}.")

		return Quote(
			symbol=normalized_symbol,
			name=str(raw_name or normalized_symbol).strip() or normalized_symbol,
			price=price,
			currency="HKD" if normalized_symbol.endswith(".HK") else "CNY",
			market_time=datetime.now(timezone.utc),
		)

class TencentQuoteProvider:
	TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="

	def __init__(self, timeout: int = 10) -> None:
		self.timeout = timeout

	@staticmethod
	def _build_tencent_symbol(symbol: str) -> tuple[str, str]:
		normalized_symbol = normalize_symbol(symbol)

		if normalized_symbol.endswith(".HK"):
			code = normalized_symbol.removesuffix(".HK").zfill(5)
			return f"hk{code}", "HKD"
		if normalized_symbol.endswith(".SS"):
			code = normalized_symbol.removesuffix(".SS")
			return f"sh{code}", "CNY"
		if normalized_symbol.endswith(".SZ"):
			code = normalized_symbol.removesuffix(".SZ")
			return f"sz{code}", "CNY"

		raise QuoteLookupError(f"Tencent quote does not support symbol {normalized_symbol}.")

	async def fetch_quote(self, symbol: str) -> Quote:
		"""Fetch HK/CN quotes from Tencent's free quote endpoint."""
		normalized_symbol = normalize_symbol(symbol)
		tencent_symbol, currency = self._build_tencent_symbol(normalized_symbol)

		try:
			async with httpx.AsyncClient(timeout=self.timeout) as client:
				response = await client.get(
					f"{self.TENCENT_QUOTE_URL}{tencent_symbol}",
					headers={"User-Agent": "Mozilla/5.0"},
				)
				response.raise_for_status()
				# The endpoint defaults to GBK and includes non-ASCII market names.
				payload_text = response.content.decode("gbk", errors="ignore")
		except httpx.HTTPError as exc:
			error_details = _describe_http_error(exc)
			raise QuoteLookupError(
				f"Tencent quote request failed for {normalized_symbol} ({error_details}).",
			) from exc

		if "=" not in payload_text:
			raise QuoteLookupError(f"No Tencent quote data returned for {normalized_symbol}.")

		payload = payload_text.split("=", maxsplit=1)[1].strip()
		payload = payload.strip(";").strip().strip('"')
		fields = payload.split("~")
		if len(fields) < 4:
			raise QuoteLookupError(f"No Tencent quote data returned for {normalized_symbol}.")

		raw_price = fields[3]
		try:
			price = quantize_decimal(raw_price)
		except (ArithmeticError, TypeError, ValueError) as exc:
			raise QuoteLookupError(
				f"Incomplete Tencent quote data returned for {normalized_symbol}.",
			) from exc

		if price <= 0:
			raise QuoteLookupError(f"Incomplete Tencent quote data returned for {normalized_symbol}.")

		name = str(fields[1] if len(fields) > 1 else normalized_symbol).strip() or normalized_symbol
		market_time = _parse_tencent_market_time(fields[30] if len(fields) > 30 else None)
		return Quote(
			symbol=normalized_symbol,
			name=name,
			price=price,
			currency=currency,
			market_time=market_time,
		)

class BitgetQuoteProvider:
	BITGET_TICKER_URL = "https://api.bitget.com/api/v2/spot/market/tickers"

	def __init__(self, timeout: int = 10) -> None:
		self.timeout = timeout

	async def fetch_quote(self, symbol: str) -> Quote:
		"""Fetch spot crypto quotes from Bitget's public market endpoint."""
		normalized_symbol = normalize_symbol_for_market(symbol, "CRYPTO")
		base, _, _quote = normalized_symbol.partition("-")
		if base in BITGET_STABLE_QUOTES:
			return Quote(
				symbol=normalized_symbol,
				name="Tether USDt" if base == "USDT" else "USD Coin",
				price=Decimal("1"),
				currency="USD",
				market_time=datetime.now(timezone.utc),
			)

		bitget_symbol = build_bitget_symbol(normalized_symbol)

		try:
			async with httpx.AsyncClient(timeout=self.timeout) as client:
				response = await client.get(
					self.BITGET_TICKER_URL,
					params={"symbol": bitget_symbol},
					headers={"User-Agent": "Mozilla/5.0"},
				)
				response.raise_for_status()
				payload = response.json()
		except httpx.HTTPError as exc:
			raise QuoteLookupError(f"Bitget quote request failed for {normalized_symbol}.") from exc

		if str(payload.get("code") or "").strip() not in {"", "00000"}:
			raise QuoteLookupError(f"Bitget quote request failed for {normalized_symbol}.")

		data = payload.get("data") or {}
		if isinstance(data, list):
			data = data[0] if data else {}

		raw_price = data.get("close") or data.get("lastPr") or data.get("last")
		if raw_price in (None, "", 0, "0"):
			raise QuoteLookupError(f"No Bitget quote data returned for {normalized_symbol}.")

		try:
			price = quantize_decimal(raw_price)
		except (ArithmeticError, TypeError, ValueError) as exc:
			raise QuoteLookupError(
				f"Incomplete Bitget quote data returned for {normalized_symbol}.",
			) from exc

		if price <= 0:
			raise QuoteLookupError(f"Incomplete Bitget quote data returned for {normalized_symbol}.")

		return Quote(
			symbol=normalized_symbol,
			name=base,
			price=price,
			currency="USD",
			market_time=_parse_epoch_millis(
				data.get("ts") or data.get("timestamp") or payload.get("requestTime"),
			),
		)

class YahooSecuritySearchProvider:
	YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"

	def __init__(self, timeout: int = 10) -> None:
		self.timeout = timeout

	async def search(self, query: str) -> list[SecuritySearchResult]:
		"""Search Yahoo's public security lookup feed."""
		if not query.strip():
			return []

		try:
			async with httpx.AsyncClient(timeout=self.timeout) as client:
				response = await client.get(
					self.YAHOO_SEARCH_URL,
					params={
						"q": query,
						"quotesCount": 8,
						"newsCount": 0,
						"enableFuzzyQuery": False,
					},
					headers={"User-Agent": "Mozilla/5.0"},
				)
				response.raise_for_status()
				payload = response.json()
		except httpx.HTTPError as exc:
			raise QuoteLookupError(f"Security search request failed for {query}.") from exc
		except ValueError as exc:
			raise QuoteLookupError(f"Security search returned invalid data for {query}.") from exc

		results: list[SecuritySearchResult] = []
		seen_symbols: set[str] = set()
		items = payload.get("quotes") if isinstance(payload, dict) else []
		if items is None:
			return []
		if not isinstance(items, list):
			raise QuoteLookupError(f"Security search returned invalid data for {query}.")

		for item in items:
			raw_symbol = str(item.get("symbol") or "").strip()
			quote_type = str(item.get("quoteType") or "").strip().upper()
			if not raw_symbol or (quote_type and quote_type not in SEARCHABLE_QUOTE_TYPES):
				continue

			try:
				symbol = normalize_symbol_for_market(raw_symbol, "CRYPTO" if quote_type == "CRYPTOCURRENCY" else None)
			except ValueError:
				continue

			if symbol in seen_symbols:
				continue

			name = str(item.get("shortname") or item.get("longname") or symbol).strip()
			exchange = str(item.get("exchange") or "").strip() or None
			market = infer_security_market(symbol, exchange, quote_type)
			if market == "OTHER":
				continue
			currency = str(item.get("currency") or "").strip().upper() or _default_currency_for_market(
				market,
			)
			results.append(
				SecuritySearchResult(
					symbol=symbol,
					name=name,
					market=market,
					currency=currency,
					exchange=exchange,
					source="Yahoo Finance",
				),
			)
			seen_symbols.add(symbol)

		return results

class EastMoneySecuritySearchProvider:
	EASTMONEY_SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"

	def __init__(self, timeout: int = 10) -> None:
		self.timeout = timeout

	async def search(self, query: str) -> list[SecuritySearchResult]:
		"""Search A-share/HK/US symbols via Eastmoney's public suggestion endpoint."""
		if not query.strip():
			return []

		try:
			async with httpx.AsyncClient(timeout=self.timeout) as client:
				response = await client.get(
					self.EASTMONEY_SEARCH_URL,
					params={
						"input": query,
						"type": "14",
						"count": "10",
						"token": EASTMONEY_SEARCH_TOKEN,
					},
					headers={
						"User-Agent": "Mozilla/5.0",
						"Referer": "https://quote.eastmoney.com/",
					},
				)
				response.raise_for_status()
				payload = response.json()
		except httpx.HTTPError as exc:
			raise QuoteLookupError(f"Eastmoney search request failed for {query}.") from exc
		except ValueError as exc:
			raise QuoteLookupError(f"Eastmoney search returned invalid data for {query}.") from exc

		results: list[SecuritySearchResult] = []
		seen_symbols: set[str] = set()
		quotation_table = payload.get("QuotationCodeTable") if isinstance(payload, dict) else {}
		items = quotation_table.get("Data") if isinstance(quotation_table, dict) else []
		if items is None:
			return []
		if not isinstance(items, list):
			raise QuoteLookupError(f"Eastmoney search returned invalid data for {query}.")

		for item in items:
			parsed_result = parse_eastmoney_search_item(item)
			if parsed_result is None or parsed_result.symbol in seen_symbols:
				continue
			results.append(parsed_result)
			seen_symbols.add(parsed_result.symbol)

		return results

class FrankfurterRateProvider:
	FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"

	def __init__(self, timeout: int = 10) -> None:
		self.timeout = timeout

	async def fetch_rate(self, from_currency: str, to_currency: str) -> Decimal:
		"""Fetch a conversion rate using Frankfurter's ECB-backed feed."""
		try:
			async with httpx.AsyncClient(timeout=self.timeout) as client:
				response = await client.get(
					self.FRANKFURTER_URL,
					params={"base": from_currency, "symbols": to_currency},
				)
				response.raise_for_status()
				payload = response.json()
		except httpx.HTTPError as exc:
			raise QuoteLookupError(
				f"FX provider request failed for {from_currency}/{to_currency}.",
			) from exc

		rate = payload.get("rates", {}).get(to_currency)
		if rate in (None, 0):
			raise QuoteLookupError(f"No FX rate returned for {from_currency}/{to_currency}.")

		return quantize_decimal(rate)

class OpenExchangeRateProvider:
	OPEN_EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest"

	def __init__(self, timeout: int = 10) -> None:
		self.timeout = timeout

	async def fetch_rate(self, from_currency: str, to_currency: str) -> Decimal:
		"""Fetch a conversion rate from Open ExchangeRate-API as a fallback source."""
		try:
			async with httpx.AsyncClient(timeout=self.timeout) as client:
				response = await client.get(f"{self.OPEN_EXCHANGE_RATE_URL}/{from_currency}")
				response.raise_for_status()
				payload = response.json()
		except httpx.HTTPError as exc:
			raise QuoteLookupError(
				f"FX provider request failed for {from_currency}/{to_currency}.",
			) from exc

		result = str(payload.get("result") or "").strip().lower()
		if result and result != "success":
			raise QuoteLookupError(f"No FX rate returned for {from_currency}/{to_currency}.")

		rate = payload.get("rates", {}).get(to_currency)
		if rate in (None, 0):
			raise QuoteLookupError(f"No FX rate returned for {from_currency}/{to_currency}.")

		return quantize_decimal(rate)
