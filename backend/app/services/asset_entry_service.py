from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import Response
from sqlmodel import select

from app.fixed_precision import (
	display_money,
	quantize_decimal,
	quantize_optional_decimal,
)
from app.models import FixedAsset, LiabilityEntry, OtherAsset
from app.schemas import (
    FixedAssetCreate,
    FixedAssetRead,
    FixedAssetUpdate,
    LiabilityEntryCreate,
    LiabilityEntryRead,
    LiabilityEntryUpdate,
    OtherAssetCreate,
    OtherAssetRead,
    OtherAssetUpdate,
)
from app.services import job_service
from app.services.auth_service import CurrentUserDependency
from app.services.common_service import (
	_calculate_return_pct,
	_capture_model_state,
	_invalidate_dashboard_cache,
	_normalize_currency,
	_normalize_optional_text,
	_record_asset_mutation,
	_touch_model,
)
from app.services.portfolio_read_service import _to_liability_read
from app.services.service_context import SessionDependency

async def list_fixed_assets(
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> list[FixedAssetRead]:
	from app.services.dashboard_query_service import _get_cached_dashboard

	dashboard = await _get_cached_dashboard(session, current_user)
	assets = list(
		session.exec(
			select(FixedAsset)
			.where(FixedAsset.user_id == current_user.username)
			.order_by(FixedAsset.category, FixedAsset.name),
		),
	)
	valued_asset_map = {asset.id: asset for asset in dashboard.fixed_assets}
	items: list[FixedAssetRead] = []

	for asset in assets:
		valued_asset = valued_asset_map.get(asset.id or 0)
		items.append(
			FixedAssetRead(
				id=asset.id or 0,
				name=asset.name,
				category=asset.category,
				current_value_cny=display_money(asset.current_value_cny),
				purchase_value_cny=display_money(asset.purchase_value_cny)
				if asset.purchase_value_cny is not None
				else None,
				started_on=asset.started_on,
				note=asset.note,
				value_cny=valued_asset.value_cny
				if valued_asset
				else display_money(asset.current_value_cny),
				return_pct=valued_asset.return_pct if valued_asset else None,
			),
		)

	return items

def create_fixed_asset(
	payload: FixedAssetCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> FixedAssetRead:
	asset = FixedAsset(
		user_id=current_user.username,
		name=payload.name.strip(),
		category=payload.category,
		current_value_cny=quantize_decimal(payload.current_value_cny),
		purchase_value_cny=quantize_optional_decimal(payload.purchase_value_cny),
		started_on=payload.started_on,
		note=payload.note,
	)
	session.add(asset)
	session.flush()
	_record_asset_mutation(
		session,
		current_user,
		entity_type="FIXED_ASSET",
		entity_id=asset.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(asset),
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(asset)
	_invalidate_dashboard_cache(current_user.username)
	value_cny = display_money(asset.current_value_cny)
	return FixedAssetRead(
		id=asset.id or 0,
		name=asset.name,
		category=asset.category,
		current_value_cny=value_cny,
		purchase_value_cny=display_money(asset.purchase_value_cny)
		if asset.purchase_value_cny is not None
		else None,
		started_on=asset.started_on,
		note=asset.note,
		value_cny=value_cny,
		return_pct=_calculate_return_pct(value_cny, asset.purchase_value_cny),
	)

def update_fixed_asset(
	asset_id: int,
	payload: FixedAssetUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> FixedAssetRead:
	asset = session.get(FixedAsset, asset_id)
	if asset is None or asset.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Fixed asset not found.")

	before_state = _capture_model_state(asset)
	asset.name = payload.name.strip()
	asset.category = payload.category
	asset.current_value_cny = quantize_decimal(payload.current_value_cny)
	asset.purchase_value_cny = quantize_optional_decimal(payload.purchase_value_cny)
	if "started_on" in payload.model_fields_set:
		asset.started_on = payload.started_on
	if "note" in payload.model_fields_set:
		asset.note = _normalize_optional_text(payload.note)
	_touch_model(asset)
	session.add(asset)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="FIXED_ASSET",
		entity_id=asset.id,
		operation="UPDATE",
		before_state=before_state,
		after_state=_capture_model_state(asset),
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(asset)
	_invalidate_dashboard_cache(current_user.username)
	value_cny = display_money(asset.current_value_cny)
	return FixedAssetRead(
		id=asset.id or 0,
		name=asset.name,
		category=asset.category,
		current_value_cny=value_cny,
		purchase_value_cny=display_money(asset.purchase_value_cny)
		if asset.purchase_value_cny is not None
		else None,
		started_on=asset.started_on,
		note=asset.note,
		value_cny=value_cny,
		return_pct=_calculate_return_pct(value_cny, asset.purchase_value_cny),
	)

def delete_fixed_asset(
	asset_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	asset = session.get(FixedAsset, asset_id)
	if asset is None or asset.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Fixed asset not found.")

	before_state = _capture_model_state(asset)
	session.delete(asset)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="FIXED_ASSET",
		entity_id=asset_id,
		operation="DELETE",
		before_state=before_state,
		after_state=None,
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)

async def list_liabilities(
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> list[LiabilityEntryRead]:
	from app.services.dashboard_query_service import _get_cached_dashboard

	dashboard = await _get_cached_dashboard(session, current_user)
	entries = list(
		session.exec(
			select(LiabilityEntry)
			.where(LiabilityEntry.user_id == current_user.username)
			.order_by(LiabilityEntry.category, LiabilityEntry.name),
		),
	)
	valued_entry_map = {entry.id: entry for entry in dashboard.liabilities}
	items: list[LiabilityEntryRead] = []

	for entry in entries:
		valued_entry = valued_entry_map.get(entry.id or 0)
		items.append(
			LiabilityEntryRead(
				id=entry.id or 0,
				name=entry.name,
				category=entry.category,
				currency=entry.currency,
				balance=display_money(entry.balance),
				started_on=entry.started_on,
				note=entry.note,
				fx_to_cny=valued_entry.fx_to_cny if valued_entry else None,
				value_cny=valued_entry.value_cny if valued_entry else None,
			),
		)

	return items

def create_liability(
	payload: LiabilityEntryCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> LiabilityEntryRead:
	entry = LiabilityEntry(
		user_id=current_user.username,
		name=payload.name.strip(),
		category=payload.category,
		currency=_normalize_currency(payload.currency),
		balance=quantize_decimal(payload.balance),
		started_on=payload.started_on,
		note=payload.note,
	)
	session.add(entry)
	session.flush()
	_record_asset_mutation(
		session,
		current_user,
		entity_type="LIABILITY",
		entity_id=entry.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(entry),
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(entry)
	_invalidate_dashboard_cache(current_user.username)
	return _to_liability_read(entry)

def update_liability(
	entry_id: int,
	payload: LiabilityEntryUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> LiabilityEntryRead:
	entry = session.get(LiabilityEntry, entry_id)
	if entry is None or entry.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Liability not found.")

	before_state = _capture_model_state(entry)
	entry.name = payload.name.strip()
	entry.currency = _normalize_currency(payload.currency)
	entry.balance = quantize_decimal(payload.balance)
	if payload.category is not None:
		entry.category = payload.category
	if "started_on" in payload.model_fields_set:
		entry.started_on = payload.started_on
	if "note" in payload.model_fields_set:
		entry.note = _normalize_optional_text(payload.note)
	_touch_model(entry)
	session.add(entry)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="LIABILITY",
		entity_id=entry.id,
		operation="UPDATE",
		before_state=before_state,
		after_state=_capture_model_state(entry),
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(entry)
	_invalidate_dashboard_cache(current_user.username)
	return _to_liability_read(entry)

def delete_liability(
	entry_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	entry = session.get(LiabilityEntry, entry_id)
	if entry is None or entry.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Liability not found.")

	before_state = _capture_model_state(entry)
	session.delete(entry)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="LIABILITY",
		entity_id=entry_id,
		operation="DELETE",
		before_state=before_state,
		after_state=None,
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)

async def list_other_assets(
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> list[OtherAssetRead]:
	from app.services.dashboard_query_service import _get_cached_dashboard

	dashboard = await _get_cached_dashboard(session, current_user)
	assets = list(
		session.exec(
			select(OtherAsset)
			.where(OtherAsset.user_id == current_user.username)
			.order_by(OtherAsset.category, OtherAsset.name),
		),
	)
	valued_asset_map = {asset.id: asset for asset in dashboard.other_assets}
	items: list[OtherAssetRead] = []

	for asset in assets:
		valued_asset = valued_asset_map.get(asset.id or 0)
		items.append(
			OtherAssetRead(
				id=asset.id or 0,
				name=asset.name,
				category=asset.category,
				current_value_cny=display_money(asset.current_value_cny),
				original_value_cny=display_money(asset.original_value_cny)
				if asset.original_value_cny is not None
				else None,
				started_on=asset.started_on,
				note=asset.note,
				value_cny=valued_asset.value_cny
				if valued_asset
				else display_money(asset.current_value_cny),
				return_pct=valued_asset.return_pct if valued_asset else None,
			),
		)

	return items

def create_other_asset(
	payload: OtherAssetCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> OtherAssetRead:
	asset = OtherAsset(
		user_id=current_user.username,
		name=payload.name.strip(),
		category=payload.category,
		current_value_cny=quantize_decimal(payload.current_value_cny),
		original_value_cny=quantize_optional_decimal(payload.original_value_cny),
		started_on=payload.started_on,
		note=payload.note,
	)
	session.add(asset)
	session.flush()
	_record_asset_mutation(
		session,
		current_user,
		entity_type="OTHER_ASSET",
		entity_id=asset.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(asset),
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(asset)
	_invalidate_dashboard_cache(current_user.username)
	value_cny = display_money(asset.current_value_cny)
	return OtherAssetRead(
		id=asset.id or 0,
		name=asset.name,
		category=asset.category,
		current_value_cny=value_cny,
		original_value_cny=display_money(asset.original_value_cny)
		if asset.original_value_cny is not None
		else None,
		started_on=asset.started_on,
		note=asset.note,
		value_cny=value_cny,
		return_pct=_calculate_return_pct(value_cny, asset.original_value_cny),
	)

def update_other_asset(
	asset_id: int,
	payload: OtherAssetUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> OtherAssetRead:
	asset = session.get(OtherAsset, asset_id)
	if asset is None or asset.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Other asset not found.")

	before_state = _capture_model_state(asset)
	asset.name = payload.name.strip()
	asset.category = payload.category
	asset.current_value_cny = quantize_decimal(payload.current_value_cny)
	asset.original_value_cny = quantize_optional_decimal(payload.original_value_cny)
	if "started_on" in payload.model_fields_set:
		asset.started_on = payload.started_on
	if "note" in payload.model_fields_set:
		asset.note = _normalize_optional_text(payload.note)
	_touch_model(asset)
	session.add(asset)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="OTHER_ASSET",
		entity_id=asset.id,
		operation="UPDATE",
		before_state=before_state,
		after_state=_capture_model_state(asset),
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(asset)
	_invalidate_dashboard_cache(current_user.username)
	value_cny = display_money(asset.current_value_cny)
	return OtherAssetRead(
		id=asset.id or 0,
		name=asset.name,
		category=asset.category,
		current_value_cny=value_cny,
		original_value_cny=display_money(asset.original_value_cny)
		if asset.original_value_cny is not None
		else None,
		started_on=asset.started_on,
		note=asset.note,
		value_cny=value_cny,
		return_pct=_calculate_return_pct(value_cny, asset.original_value_cny),
	)

def delete_other_asset(
	asset_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	asset = session.get(OtherAsset, asset_id)
	if asset is None or asset.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Other asset not found.")

	before_state = _capture_model_state(asset)
	session.delete(asset)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="OTHER_ASSET",
		entity_id=asset_id,
		operation="DELETE",
		before_state=before_state,
		after_state=None,
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)

__all__ = ['list_fixed_assets', 'create_fixed_asset', 'update_fixed_asset', 'delete_fixed_asset', 'list_liabilities', 'create_liability', 'update_liability', 'delete_liability', 'list_other_assets', 'create_other_asset', 'update_other_asset', 'delete_other_asset']
