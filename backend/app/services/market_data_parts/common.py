from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

INVALID_SYMBOL_MESSAGE = (
	"Invalid symbol format. Use A-share (600519, 600519.SS, SH600519), "
	"HK (00700, 00700.HK, HK00700), or US (AAPL, BRK-B)."
)
SEARCHABLE_QUOTE_TYPES = {"EQUITY", "ETF", "MUTUALFUND", "CRYPTOCURRENCY"}
US_EXCHANGES = {"NMS", "NGM", "NYQ", "ASE", "PCX", "BTS", "NCM", "NSQ", "OOTC", "PNK"}
CRYPTO_EXCHANGES = {"CCC", "CCY", "CRY", "COIN"}
EASTMONEY_SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
BITGET_EXCHANGE = "BITGET"
BITGET_SOURCE_LABEL = "Bitget"
BITGET_STABLE_QUOTES = {"USDT", "USDC"}

class QuoteLookupError(RuntimeError):
	"""Raised when the market data providers cannot return a usable value."""

def _describe_http_error(exc: httpx.HTTPError) -> str:
	"""Format transport failures with status code when available."""
	if isinstance(exc, httpx.HTTPStatusError):
		status_code = exc.response.status_code
		reason_phrase = (exc.response.reason_phrase or "").strip()
		if reason_phrase:
			return f"HTTP {status_code} {reason_phrase}"
		return f"HTTP {status_code}"

	if isinstance(exc, httpx.TimeoutException):
		return exc.__class__.__name__

	if isinstance(exc, httpx.RequestError):
		error_message = str(exc).strip()
		if error_message:
			return f"{exc.__class__.__name__}: {error_message}"
		return exc.__class__.__name__

	return exc.__class__.__name__

def _parse_epoch_millis(value: object | None) -> datetime | None:
	if value in (None, ""):
		return None

	try:
		numeric_value = int(Decimal(str(value)))
	except (InvalidOperation, TypeError, ValueError):
		return None

	if numeric_value <= 0:
		return None

	if numeric_value > 10_000_000_000:
		return datetime.fromtimestamp(numeric_value / 1000, tz=timezone.utc)

	return datetime.fromtimestamp(numeric_value, tz=timezone.utc)

def _parse_tencent_market_time(value: str | None) -> datetime | None:
	"""Parse Tencent quote timestamps that vary by market."""
	candidate = str(value or "").strip()
	if not candidate:
		return None

	for datetime_format in ("%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
		try:
			return datetime.strptime(candidate, datetime_format).replace(tzinfo=timezone.utc)
		except ValueError:
			continue

	return None


@dataclass(slots=True)

class Quote:
	symbol: str
	name: str
	price: Decimal
	currency: str
	market_time: datetime | None


@dataclass(slots=True)

class SecuritySearchResult:
	symbol: str
	name: str
	market: str
	currency: str
	exchange: str | None
	source: str | None = None


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
