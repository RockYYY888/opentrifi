from __future__ import annotations

import logging
from typing import Annotated, cast

from fastapi import Depends
from sqlmodel import Session

from app.database import get_session
from app import runtime_state
from app.services.cache import RedisBackedTTLCache, RedisCacheClient, TTLCache
from app.services.market_data import MarketDataClient
from app.settings import get_settings

SessionDependency = Annotated[Session, Depends(get_session)]
settings = get_settings()
redis_cache_client = cast(RedisCacheClient, runtime_state.redis_client)
market_data_client = MarketDataClient(
	quote_cache=RedisBackedTTLCache(
		redis_cache_client,
		"asset-tracker:v2:runtime:market-quotes",
		stale_ttl_seconds=6 * 60 * 60,
	),
	search_cache=TTLCache(),
	fx_cache=RedisBackedTTLCache(
		redis_cache_client,
		"asset-tracker:v2:runtime:market-fx",
		stale_ttl_seconds=6 * 60 * 60,
	),
	quote_ttl_seconds=1,
)
logger = logging.getLogger(__name__)

__all__ = [
	"SessionDependency",
	"logger",
	"market_data_client",
	"settings",
]
