from __future__ import annotations

from app.services.market_data_parts.common import (
	BITGET_EXCHANGE,
	BITGET_SOURCE_LABEL,
	SecuritySearchResult,
	US_EXCHANGES,
)
from app.services.market_data_parts.symbols import (
	_default_currency_for_market,
	infer_security_market,
	normalize_symbol,
)

LOCAL_SEARCH_CATALOG = (
	(
		("腾讯", "腾讯控股", "tencent"),
		SecuritySearchResult(
			symbol="0700.HK",
			name="腾讯控股",
			market="HK",
			currency="HKD",
			exchange="HKG",
			source="本地映射",
		),
	),
	(
		("阿里", "阿里巴巴", "alibaba"),
		SecuritySearchResult(
			symbol="9988.HK",
			name="阿里巴巴-SW",
			market="HK",
			currency="HKD",
			exchange="HKG",
			source="本地映射",
		),
	),
	(
		("苹果", "apple", "aapl"),
		SecuritySearchResult(
			symbol="AAPL",
			name="Apple Inc.",
			market="US",
			currency="USD",
			exchange="NMS",
			source="本地映射",
		),
	),
	(
		("英伟达", "nvidia", "nvda"),
		SecuritySearchResult(
			symbol="NVDA",
			name="NVIDIA Corporation",
			market="US",
			currency="USD",
			exchange="NMS",
			source="本地映射",
		),
	),
	(
		("特斯拉", "tesla", "tsla"),
		SecuritySearchResult(
			symbol="TSLA",
			name="Tesla, Inc.",
			market="US",
			currency="USD",
			exchange="NMS",
			source="本地映射",
		),
	),
	(
		("小米", "xiaomi"),
		SecuritySearchResult(
			symbol="1810.HK",
			name="小米集团-W",
			market="HK",
			currency="HKD",
			exchange="HKG",
			source="本地映射",
		),
	),
	(
		("茅台", "贵州茅台", "kweichow moutai"),
		SecuritySearchResult(
			symbol="600519.SS",
			name="贵州茅台",
			market="CN",
			currency="CNY",
			exchange="SHH",
			source="本地映射",
		),
	),
	(
		("理想", "理想汽车", "li auto", "li"),
		SecuritySearchResult(
			symbol="2015.HK",
			name="理想汽车-W",
			market="HK",
			currency="HKD",
			exchange="HKG",
			source="本地映射",
		),
	),
	(
		("寒武纪", "cambricon"),
		SecuritySearchResult(
			symbol="688256.SS",
			name="寒武纪-U",
			market="CN",
			currency="CNY",
			exchange="SHH",
			source="本地映射",
		),
	),
	(
		("比特币", "btc", "bitcoin"),
		SecuritySearchResult(
			symbol="BTC-USD",
			name="Bitcoin",
			market="CRYPTO",
			currency="USD",
			exchange=BITGET_EXCHANGE,
			source=BITGET_SOURCE_LABEL,
		),
	),
	(
		("以太坊", "eth", "ethereum"),
		SecuritySearchResult(
			symbol="ETH-USD",
			name="Ethereum",
			market="CRYPTO",
			currency="USD",
			exchange=BITGET_EXCHANGE,
			source=BITGET_SOURCE_LABEL,
		),
	),
	(
		("usdt", "泰达币", "tether"),
		SecuritySearchResult(
			symbol="USDT-USD",
			name="Tether USDt",
			market="CRYPTO",
			currency="USD",
			exchange=BITGET_EXCHANGE,
			source=BITGET_SOURCE_LABEL,
		),
	),
	(
		("usdc", "usd coin"),
		SecuritySearchResult(
			symbol="USDC-USD",
			name="USD Coin",
			market="CRYPTO",
			currency="USD",
			exchange=BITGET_EXCHANGE,
			source=BITGET_SOURCE_LABEL,
		),
	),
)

def parse_eastmoney_search_item(item: dict[str, str]) -> SecuritySearchResult | None:
	"""Convert Eastmoney's search payload into the app's normalized search result."""
	code = str(item.get("Code") or "").strip().upper()
	name = str(item.get("Name") or "").strip()
	if not code or not name:
		return None

	quote_id = str(item.get("QuoteID") or "").strip()
	classify = str(item.get("Classify") or "").strip().upper()
	jys = str(item.get("JYS") or "").strip().upper()
	exchange_name = jys or None

	if classify == "NEEQ":
		return None

	if quote_id.startswith("1."):
		return SecuritySearchResult(
			symbol=f"{code}.SS",
			name=name,
			market="CN",
			currency="CNY",
			exchange=exchange_name or "SHH",
			source="东方财富",
		)

	if quote_id.startswith("0."):
		return SecuritySearchResult(
			symbol=f"{code}.SZ",
			name=name,
			market="CN",
			currency="CNY",
			exchange=exchange_name or "SHE",
			source="东方财富",
		)

	if classify == "HK" or jys == "HK" or quote_id.startswith("116."):
		return SecuritySearchResult(
			symbol=normalize_symbol(f"{code}.HK"),
			name=name,
			market="HK",
			currency="HKD",
			exchange=exchange_name or "HKG",
			source="东方财富",
		)

	if classify == "USSTOCK" or jys in US_EXCHANGES or quote_id.startswith("105."):
		return SecuritySearchResult(
			symbol=normalize_symbol(code),
			name=name,
			market="US",
			currency="USD",
			exchange=exchange_name,
			source="东方财富",
		)

	return None

def _merge_search_results(
	primary_results: list[SecuritySearchResult],
	secondary_results: list[SecuritySearchResult],
) -> list[SecuritySearchResult]:
	merged_results: list[SecuritySearchResult] = []
	seen_symbols: set[str] = set()

	for result in [*primary_results, *secondary_results]:
		dedupe_key = (
			f"{result.symbol}::{result.source or ''}"
			if result.market == "CRYPTO"
			else result.symbol
		)
		if dedupe_key in seen_symbols:
			continue
		merged_results.append(result)
		seen_symbols.add(dedupe_key)

	return merged_results

def _contains_cjk_characters(value: str) -> bool:
	return any("\u4e00" <= character <= "\u9fff" for character in value)

def build_local_search_results(query: str) -> list[SecuritySearchResult]:
	"""Fallback suggestions for symbol-like input and common names."""
	normalized_query = query.strip().casefold()
	if not normalized_query:
		return []

	results: list[SecuritySearchResult] = []
	for keywords, result in LOCAL_SEARCH_CATALOG:
		if any(normalized_query in keyword or keyword in normalized_query for keyword in keywords):
			results.append(result)

	if not any(result.market == "CRYPTO" for result in results):
		try:
			symbol = normalize_symbol(query)
		except ValueError:
			pass
		else:
			market = infer_security_market(symbol)
			if market != "OTHER":
				results.append(
					SecuritySearchResult(
						symbol=symbol,
						name=symbol,
						market=market,
						currency=_default_currency_for_market(market),
						exchange=BITGET_EXCHANGE if market == "CRYPTO" else None,
						source=BITGET_SOURCE_LABEL if market == "CRYPTO" else None,
					),
				)

	return _merge_search_results(results, [])
