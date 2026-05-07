from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.database import init_db
from app.runtime_state import (
	current_agent_name_context,
	current_agent_task_id_context,
	current_actor_source_context,
	current_api_key_name_context,
	dashboard_cache,
	live_holdings_return_states,
	live_portfolio_states,
	login_attempt_states,
	validate_runtime_redis_connection,
)
from app.services import service_context

logger = logging.getLogger(__name__)
settings = service_context.settings
market_data_client = service_context.market_data_client


@asynccontextmanager
async def lifespan(_: FastAPI):
	settings.validate_runtime()
	validate_runtime_redis_connection()
	init_db()
	try:
		yield
	finally:
		pass


def create_app() -> FastAPI:
	app = FastAPI(
		title="OpenTraFi API",
		version="0.1.0",
		lifespan=lifespan,
	)
	app.add_middleware(
		TrustedHostMiddleware,
		allowed_hosts=settings.trusted_hosts() or ["localhost", "127.0.0.1"],
	)
	app.add_middleware(
		CORSMiddleware,
		allow_origins=settings.cors_origins(),
		allow_credentials=True,
		allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
		allow_headers=[
			"Authorization",
			"Agent-Name",
			"Content-Type",
			"Idempotency-Key",
			"X-Client-Device-Id",
		],
	)
	app.add_middleware(
		SessionMiddleware,
		secret_key=settings.session_secret_value() or "asset-tracker-session-fallback",
		session_cookie="asset_tracker_session",
		max_age=60 * 60 * 24 * 30,
		same_site="lax",
		https_only=settings.session_cookie_https_only(),
	)

	@app.middleware("http")
	async def add_security_headers(request: Request, call_next):
		actor_source_token = current_actor_source_context.set("USER")
		api_key_name_token = current_api_key_name_context.set(None)
		agent_name_token = current_agent_name_context.set(None)
		agent_task_token = current_agent_task_id_context.set(None)
		try:
			response: Response = await call_next(request)
		finally:
			current_agent_task_id_context.reset(agent_task_token)
			current_agent_name_context.reset(agent_name_token)
			current_api_key_name_context.reset(api_key_name_token)
			current_actor_source_context.reset(actor_source_token)
		response.headers["Cache-Control"] = "no-store"
		response.headers["Pragma"] = "no-cache"
		response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
		response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
		response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
		response.headers["Referrer-Policy"] = "same-origin"
		response.headers["X-Content-Type-Options"] = "nosniff"
		response.headers["X-Frame-Options"] = "DENY"
		if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
			response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
		return response

	app.include_router(api_router)
	return app


app = create_app()
