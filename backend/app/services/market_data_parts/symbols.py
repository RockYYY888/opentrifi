from __future__ import annotations

import re

from app.services.market_data_parts.common import (
	BITGET_STABLE_QUOTES,
	CRYPTO_EXCHANGES,
	INVALID_SYMBOL_MESSAGE,
	US_EXCHANGES,
)

def build_fx_symbol(from_currency: str, to_currency: str) -> str:
	"""Translate a currency pair into a Yahoo Finance symbol."""
	return f"{from_currency.upper()}{to_currency.upper()}=X"

def _normalize_hk_code(raw_code: str) -> str:
	"""Normalize HK numeric codes to a canonical 4+ digit form without extra leading zeroes."""
	return str(int(raw_code)).zfill(4)

def normalize_symbol(symbol: str) -> str:
	"""Normalize common CN/HK/US ticker formats into Yahoo-compatible symbols."""
	candidate = symbol.strip().upper()
	if not candidate:
		raise ValueError("Symbol cannot be empty.")

	if re.fullmatch(r"^[A-Z]{6}=X$", candidate):
		return candidate

	if match := re.fullmatch(r"^(SH|SZ)(\d{6})$", candidate):
		suffix = "SS" if match.group(1) == "SH" else "SZ"
		return f"{match.group(2)}.{suffix}"

	if match := re.fullmatch(r"^HK(\d{1,5})$", candidate):
		return f"{_normalize_hk_code(match.group(1))}.HK"

	if re.fullmatch(r"^\d{6}\.(SS|SZ)$", candidate):
		return candidate

	if re.fullmatch(r"^\d{1,5}\.HK$", candidate):
		code, _, _ = candidate.partition(".")
		return f"{_normalize_hk_code(code)}.HK"

	if re.fullmatch(r"^\d{6}$", candidate):
		suffix = "SS" if candidate[0] in {"5", "6", "9"} else "SZ"
		return f"{candidate}.{suffix}"

	if re.fullmatch(r"^\d{1,5}$", candidate):
		return f"{_normalize_hk_code(candidate)}.HK"

	if re.fullmatch(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?$", candidate):
		return candidate

	raise ValueError(INVALID_SYMBOL_MESSAGE)

def build_eastmoney_secid(symbol: str) -> str:
	"""Map normalized CN/HK symbols into Eastmoney's secid format."""
	normalized_symbol = normalize_symbol(symbol)

	if normalized_symbol.endswith(".SS"):
		return f"1.{normalized_symbol.removesuffix('.SS')}"
	if normalized_symbol.endswith(".SZ"):
		return f"0.{normalized_symbol.removesuffix('.SZ')}"
	if normalized_symbol.endswith(".HK"):
		code = normalized_symbol.removesuffix(".HK")
		return f"116.{code.zfill(5)}"

	raise ValueError(f"Eastmoney quote does not support symbol {normalized_symbol}.")

def build_bitget_symbol(symbol: str) -> str:
	"""Map app-level crypto symbols into Bitget's spot symbol format."""
	normalized_symbol = normalize_symbol_for_market(symbol, "CRYPTO")
	base, _, _quote = normalized_symbol.partition("-")
	quote_currency = "USDT" if "USDT" in BITGET_STABLE_QUOTES else sorted(BITGET_STABLE_QUOTES)[0]
	return f"{base}{quote_currency}"

def _default_currency_for_market(market: str) -> str:
	if market == "CRYPTO":
		return "USD"
	if market == "HK":
		return "HKD"
	if market == "US":
		return "USD"
	return "CNY"

def normalize_symbol_for_market(symbol: str, market: str | None = None) -> str:
	"""Normalize symbols with market-specific handling for crypto pairs."""
	normalized_market = (market or "").strip().upper()
	candidate = symbol.strip().upper()

	if normalized_market == "CRYPTO":
		if re.fullmatch(r"^[A-Z0-9]{2,15}$", candidate):
			return f"{candidate}-USD"

		if re.fullmatch(r"^[A-Z0-9]{2,15}[-/](USD|USDT|USDC)$", candidate):
			base = re.split(r"[-/]", candidate, maxsplit=1)[0]
			return f"{base}-USD"

	return normalize_symbol(candidate)

def infer_security_market(
	symbol: str,
	exchange: str | None = None,
	quote_type: str | None = None,
) -> str:
	"""Infer a frontend-friendly market code from quote metadata."""
	normalized_symbol = symbol.strip().upper()
	normalized_exchange = (exchange or "").strip().upper()
	normalized_quote_type = (quote_type or "").strip().upper()

	if normalized_quote_type == "CRYPTOCURRENCY" or normalized_exchange in CRYPTO_EXCHANGES:
		return "CRYPTO"
	if re.fullmatch(r"^[A-Z0-9]{2,15}-(USD|USDT|USDC)$", normalized_symbol):
		return "CRYPTO"

	if normalized_symbol.endswith(".HK") or normalized_exchange.startswith("HKG"):
		return "HK"
	if normalized_symbol.endswith(".SS") or normalized_symbol.endswith(".SZ"):
		return "CN"
	if normalized_exchange in {
		"SHH",
		"SHZ",
		"SHANGHAI",
		"SHENZHEN",
		"SHA",
		"SHE",
	}:
		return "CN"
	if normalized_exchange in US_EXCHANGES:
		return "US"
	if not normalized_exchange and re.fullmatch(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?$", normalized_symbol):
		return "US"
	return "OTHER"
