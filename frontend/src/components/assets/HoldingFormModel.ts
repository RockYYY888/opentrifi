import type { HoldingMergePreview } from "./HoldingMergePreviewDialog";
import { normalizeSupportedCurrency } from "../../lib/assetCurrency";
import type {
	HoldingFormDraft,
	HoldingInput,
	HoldingRecord,
} from "../../types/assets";
import { DEFAULT_HOLDING_FORM_DRAFT } from "../../types/assets";

export function toHoldingDraft(value?: Partial<HoldingFormDraft> | null): HoldingFormDraft {
	return {
		...DEFAULT_HOLDING_FORM_DRAFT,
		...value,
	};
}

export function toHoldingInput(draft: HoldingFormDraft): HoldingInput {
	const normalizedBroker = draft.broker.trim();
	const normalizedNote = draft.note.trim();
	const shouldMergeIntoExistingCash =
		draft.side === "SELL" && draft.sell_proceeds_handling === "ADD_TO_EXISTING_CASH";
	const shouldDeductFromExistingCash =
		draft.side === "BUY" && draft.buy_funding_account_id.trim().length > 0;

	return {
		side: draft.side,
		symbol: draft.symbol.trim().toUpperCase(),
		name: draft.name.trim(),
		quantity: Number(draft.quantity),
		fallback_currency: normalizeSupportedCurrency(draft.fallback_currency, "CNY"),
		cost_basis_price: draft.cost_basis_price.trim()
			? Number(draft.cost_basis_price)
			: undefined,
		market: draft.market as HoldingInput["market"],
		broker: normalizedBroker || undefined,
		started_on: draft.started_on.trim() || undefined,
		note: normalizedNote || undefined,
		sell_proceeds_handling:
			draft.side === "SELL" ? draft.sell_proceeds_handling : undefined,
		sell_proceeds_account_id:
			shouldMergeIntoExistingCash && draft.sell_proceeds_account_id
				? Number(draft.sell_proceeds_account_id)
				: undefined,
		buy_funding_handling:
			shouldDeductFromExistingCash ? "DEDUCT_FROM_EXISTING_CASH" : undefined,
		buy_funding_account_id:
			shouldDeductFromExistingCash ? Number(draft.buy_funding_account_id) : undefined,
	};
}

export function normalizeSearchToken(value: string): string {
	return value.trim().replace(/\s+/g, " ").toLowerCase();
}

export function getSearchLabel(selection: { name: string; symbol: string }): string {
	return `${selection.name} (${selection.symbol})`;
}

export function allowsFractionalQuantity(market: HoldingFormDraft["market"]): boolean {
	return market === "FUND" || market === "CRYPTO";
}

export function isImplicitSearchSourceLabel(source?: string | null): boolean {
	return source === "代码推断" || source === "本地映射";
}

export function shouldPrefillBroker(source?: string | null): boolean {
	return Boolean(source && !isImplicitSearchSourceLabel(source));
}

function roundHoldingMetric(value: number, digits = 4): number {
	return Number(value.toFixed(digits));
}

function resolveMergedCostBasis(
	targetRecord: HoldingRecord,
	nextPayload: HoldingInput,
	mergedQuantity: number,
): {
	mergedCostBasis: number | null;
	knownCostTotal: number | null;
} {
	const existingCostTotal = targetRecord.cost_basis_price != null
		? targetRecord.quantity * targetRecord.cost_basis_price
		: null;
	const incomingCostTotal = nextPayload.cost_basis_price != null
		? nextPayload.quantity * nextPayload.cost_basis_price
		: null;

	if (existingCostTotal != null && incomingCostTotal != null && mergedQuantity > 0) {
		const totalCost = existingCostTotal + incomingCostTotal;
		return {
			mergedCostBasis: roundHoldingMetric(totalCost / mergedQuantity),
			knownCostTotal: roundHoldingMetric(totalCost, 2),
		};
	}

	if (nextPayload.cost_basis_price != null) {
		return {
			mergedCostBasis: roundHoldingMetric(nextPayload.cost_basis_price),
			knownCostTotal: roundHoldingMetric(incomingCostTotal ?? 0, 2),
		};
	}

	if (targetRecord.cost_basis_price != null) {
		return {
			mergedCostBasis: roundHoldingMetric(targetRecord.cost_basis_price),
			knownCostTotal: roundHoldingMetric(existingCostTotal ?? 0, 2),
		};
	}

	return {
		mergedCostBasis: null,
		knownCostTotal: null,
	};
}

export function buildHoldingMergePreview(
	targetRecord: HoldingRecord,
	nextPayload: HoldingInput,
	sourceRecordId: number | null,
): HoldingMergePreview {
	const mergedQuantity = roundHoldingMetric(targetRecord.quantity + nextPayload.quantity);
	const { mergedCostBasis, knownCostTotal } = resolveMergedCostBasis(
		targetRecord,
		nextPayload,
		mergedQuantity,
	);
	const estimatedReturnPct =
		targetRecord.price != null && targetRecord.price > 0 && mergedCostBasis != null && mergedCostBasis > 0
			? roundHoldingMetric(
				((targetRecord.price - mergedCostBasis) / mergedCostBasis) * 100,
				2,
			)
			: null;

	return {
		targetRecord,
		sourceRecordId,
		mergedPayload: {
			...nextPayload,
			quantity: mergedQuantity,
			cost_basis_price: mergedCostBasis ?? undefined,
			broker: nextPayload.broker ?? targetRecord.broker ?? undefined,
			started_on: nextPayload.started_on ?? targetRecord.started_on ?? undefined,
			note: nextPayload.note ?? targetRecord.note ?? undefined,
		},
		existingQuantity: targetRecord.quantity,
		incomingQuantity: nextPayload.quantity,
		mergedQuantity,
		existingCostBasis: targetRecord.cost_basis_price ?? null,
		incomingCostBasis: nextPayload.cost_basis_price ?? null,
		mergedCostBasis,
		knownCostTotal,
		estimatedReturnPct,
	};
}

export function findDuplicateHolding(
	existingHoldings: HoldingRecord[],
	symbol: string,
	currentRecordId: number | null,
): HoldingRecord | null {
	const normalizedSymbol = symbol.trim().toUpperCase();
	return existingHoldings.find((holding) => {
		if (holding.id === currentRecordId) {
			return false;
		}
		return holding.symbol.trim().toUpperCase() === normalizedSymbol;
	}) ?? null;
}
