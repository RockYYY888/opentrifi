import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import "./asset-components.css";
import { HoldingCashSettlementFields } from "./HoldingCashSettlementFields";
import {
	HoldingMergePreviewDialog,
	type HoldingMergePreview,
} from "./HoldingMergePreviewDialog";
import {
	allowsFractionalQuantity,
	buildHoldingMergePreview,
	findDuplicateHolding,
	getSearchLabel,
	isImplicitSearchSourceLabel,
	normalizeSearchToken,
	shouldPrefillBroker,
	toHoldingDraft,
	toHoldingInput,
} from "./HoldingFormModel";
import { HoldingSearchSection } from "./HoldingSearchSection";
import { HoldingTransactionFields } from "./HoldingTransactionFields";
import {
	calculateTargetCnyAmount,
	normalizeSupportedCurrency,
	type SupportedCurrencyFxRates,
} from "../../lib/assetCurrency";
import {
	formatMoneyAmount,
	formatQuantity,
} from "../../lib/assetFormatting";
import { useAutoRefreshGuard } from "../../lib/autoRefreshGuards";
import { toErrorMessage } from "../../lib/apiClient";
import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";
import type {
	AssetEditorMode,
	CashAccountRecord,
	HoldingEditorIntent,
	HoldingFormDraft,
	HoldingInput,
	HoldingMergeRequest,
	HoldingRecord,
	MaybePromise,
	SecuritySearchResult,
} from "../../types/assets";
import {
	DEFAULT_HOLDING_FORM_DRAFT,
	SELL_PROCEEDS_HANDLING_OPTIONS,
} from "../../types/assets";

export interface HoldingFormProps {
	mode?: AssetEditorMode;
	intent?: HoldingEditorIntent;
	resetKey?: number;
	value?: Partial<HoldingFormDraft> | null;
	existingHoldings?: HoldingRecord[];
	cashAccounts?: CashAccountRecord[];
	recordId?: number | null;
	title?: string;
	subtitle?: string;
	submitLabel?: string;
	busy?: boolean;
	errorMessage?: string | null;
	maxStartedOnDate?: string;
	fxRates?: SupportedCurrencyFxRates;
	onCreate?: (payload: HoldingInput) => MaybePromise<unknown>;
	onEdit?: (recordId: number, payload: HoldingInput) => MaybePromise<unknown>;
	onDelete?: (recordId: number) => MaybePromise<unknown>;
	onSearch?: (query: string) => MaybePromise<SecuritySearchResult[]>;
	onMergeDuplicate?: (request: HoldingMergeRequest) => MaybePromise<unknown>;
	onCancel?: () => void;
}

const EMPTY_HOLDINGS: HoldingRecord[] = [];
const EMPTY_CASH_ACCOUNTS: CashAccountRecord[] = [];

function formatCashAccountOptionLabel(account: CashAccountRecord): string {
	return `${account.name} · ${formatMoneyAmount(account.balance, account.currency)}`;
}

function getTodayDateValue(): string {
	const now = new Date();
	const year = now.getFullYear();
	const month = String(now.getMonth() + 1).padStart(2, "0");
	const day = String(now.getDate()).padStart(2, "0");
	return `${year}-${month}-${day}`;
}

function resolveDefaultTradeDate(maxStartedOnDate?: string): string {
	return maxStartedOnDate ?? getTodayDateValue();
}

function getHoldingSelectionKey(holding: Pick<HoldingRecord, "symbol" | "market">): string {
	return `${holding.symbol.trim().toUpperCase()}::${holding.market}`;
}

function findHoldingBySelectionKey(
	holdings: HoldingRecord[],
	selectionKey: string,
): HoldingRecord | null {
	return holdings.find((holding) => getHoldingSelectionKey(holding) === selectionKey) ?? null;
}

function resolveSellPriceDraftValue(holding: HoldingRecord): string {
	if (holding.price != null && Number.isFinite(holding.price) && holding.price > 0) {
		return String(holding.price);
	}

	if (
		holding.cost_basis_price != null &&
		Number.isFinite(holding.cost_basis_price) &&
		holding.cost_basis_price > 0
	) {
		return String(holding.cost_basis_price);
	}

	return "";
}

function clampSellQuantityDraftValue(nextValue: string, maxQuantity?: number): string {
	if (!nextValue.trim() || maxQuantity == null || !Number.isFinite(maxQuantity) || maxQuantity <= 0) {
		return nextValue;
	}

	const parsedQuantity = Number(nextValue);
	if (!Number.isFinite(parsedQuantity) || parsedQuantity <= maxQuantity) {
		return nextValue;
	}

	return String(maxQuantity);
}

function applyHoldingSelectionToDraft(
	currentDraft: HoldingFormDraft,
	holding: HoldingRecord,
	options?: {
		resetQuantity?: boolean;
		prefillSellPrice?: boolean;
		defaultTradeDate?: string;
	},
): HoldingFormDraft {
	return {
		...currentDraft,
		symbol: holding.symbol,
		name: holding.name,
		market: holding.market,
		fallback_currency: holding.fallback_currency,
		broker: holding.broker ?? "",
		quantity: options?.resetQuantity ? "" : currentDraft.quantity,
		cost_basis_price: options?.prefillSellPrice
			? resolveSellPriceDraftValue(holding)
			: currentDraft.cost_basis_price,
		started_on: currentDraft.started_on || options?.defaultTradeDate || "",
	};
}

function createHoldingResetDraft(
	intent: HoldingEditorIntent,
	maxStartedOnDate?: string,
): HoldingFormDraft {
	return {
		...DEFAULT_HOLDING_FORM_DRAFT,
		side: intent === "sell" ? "SELL" : "BUY",
		started_on: intent === "edit" ? "" : resolveDefaultTradeDate(maxStartedOnDate),
	};
}

export function HoldingForm({
	mode = "create",
	intent,
	resetKey = 0,
	value,
	existingHoldings = EMPTY_HOLDINGS,
	cashAccounts = EMPTY_CASH_ACCOUNTS,
	recordId = null,
	title,
	subtitle,
	submitLabel,
	busy = false,
	errorMessage = null,
	maxStartedOnDate,
	fxRates,
	onCreate,
	onEdit,
	onDelete,
	onSearch,
	onMergeDuplicate,
	onCancel,
}: HoldingFormProps) {
	useAutoRefreshGuard(true, "holding-form");
	const resolvedIntent = intent ?? (
		mode === "edit"
			? "edit"
			: value?.side === "SELL"
				? "sell"
				: "buy"
	);
	const sellableHoldings = useMemo(
		() => existingHoldings.filter((holding) => holding.quantity > 0),
		[existingHoldings],
	);
	const [draft, setDraft] = useState<HoldingFormDraft>(() =>
		toHoldingDraft({
			...createHoldingResetDraft(resolvedIntent, maxStartedOnDate),
			...value,
			side: resolvedIntent === "sell" ? "SELL" : "BUY",
		}),
	);
	const [localError, setLocalError] = useState<string | null>(null);
	const [searchError, setSearchError] = useState<string | null>(null);
	const [pendingMergePreview, setPendingMergePreview] = useState<HoldingMergePreview | null>(null);
	const [isWorking, setIsWorking] = useState(false);
	const [searchQuery, setSearchQuery] = useState("");
	const [searchResults, setSearchResults] = useState<SecuritySearchResult[]>([]);
	const [isSearching, setIsSearching] = useState(false);
	const [isSearchOpen, setIsSearchOpen] = useState(false);
	const searchRequestIdRef = useRef(0);
	const searchEnabled = Boolean(onSearch);
	useBodyScrollLock(pendingMergePreview !== null);

	useEffect(() => {
		let nextDraft = toHoldingDraft({
			...createHoldingResetDraft(resolvedIntent, maxStartedOnDate),
			...value,
			side: resolvedIntent === "sell" ? "SELL" : "BUY",
		});
		if (resolvedIntent === "sell") {
			const initialHolding = nextDraft.symbol && nextDraft.market
				? findHoldingBySelectionKey(
					sellableHoldings,
					getHoldingSelectionKey({
						symbol: nextDraft.symbol,
						market: nextDraft.market,
					}),
				)
				: null;
			if (initialHolding) {
				nextDraft = applyHoldingSelectionToDraft(nextDraft, initialHolding, {
					prefillSellPrice: !nextDraft.cost_basis_price,
					defaultTradeDate: resolveDefaultTradeDate(maxStartedOnDate),
				});
			}
		}
		setDraft(nextDraft);
		setSearchQuery(resolvedIntent === "sell" ? "" : nextDraft.name || nextDraft.symbol);
		setSearchResults([]);
		setIsSearchOpen(false);
		setSearchError(null);
		setLocalError(null);
		setPendingMergePreview(null);
	}, [resetKey, resolvedIntent]);

	useEffect(() => {
		if (!onSearch) {
			return;
		}

		const normalizedQuery = normalizeSearchToken(searchQuery);
		const selectionTokens = [
			normalizeSearchToken(draft.name),
			normalizeSearchToken(draft.symbol),
			normalizeSearchToken(`${draft.name} ${draft.symbol}`),
		].filter(Boolean);

		if (!normalizedQuery) {
			setSearchResults([]);
			setIsSearchOpen(false);
			setSearchError(null);
			setIsSearching(false);
			return;
		}

		if (selectionTokens.includes(normalizedQuery)) {
			setSearchResults([]);
			setIsSearchOpen(false);
			setSearchError(null);
			setIsSearching(false);
			return;
		}

		const requestId = ++searchRequestIdRef.current;
		setSearchResults([]);
		setIsSearchOpen(false);
		setIsSearching(true);

		const timer = window.setTimeout(() => {
			void (async () => {
				try {
					const results = await onSearch(searchQuery.trim());
					if (requestId !== searchRequestIdRef.current) {
						return;
					}

					setSearchResults(results);
					setIsSearchOpen(results.length > 0);
					setSearchError(null);
				} catch (error) {
					if (requestId !== searchRequestIdRef.current) {
						return;
					}

					setSearchResults([]);
					setIsSearchOpen(false);
					setSearchError(toErrorMessage(error, "标的搜索失败，请稍后重试。"));
				} finally {
					if (requestId === searchRequestIdRef.current) {
						setIsSearching(false);
					}
				}
			})();
		}, 240);

		return () => window.clearTimeout(timer);
	}, [draft.name, draft.symbol, onSearch, searchQuery]);

	const effectiveError = localError ?? errorMessage;
	const isSubmitting = busy || isWorking;
	const isEditIntent = resolvedIntent === "edit";
	const isSellTransaction = resolvedIntent === "sell";
	const resolvedTitle = title ?? (
		isEditIntent
			? "编辑投资持仓"
			: isSellTransaction
				? "新增卖出"
				: "新增买入"
	);
	const resolvedSubmitLabel = submitLabel ?? (
		isEditIntent
			? "保存编辑"
			: isSellTransaction
				? "确认卖出"
				: "确认买入"
	);
	const cancelLabel = isEditIntent ? "取消编辑" : "取消";
	const quantityLabelBase = draft.market === "FUND"
		? "份额"
		: draft.market === "CRYPTO"
			? "数量"
			: draft.market === ""
				? "数量"
				: "数量（股/支）";
	const quantityLabel = isEditIntent ? `持仓${quantityLabelBase}` : quantityLabelBase;
	const quantityStep = allowsFractionalQuantity(draft.market) ? "0.0001" : "1";
	const quantityMin = allowsFractionalQuantity(draft.market) ? "0.0001" : "1";
	const shouldMergeIntoExistingCash =
		isSellTransaction && draft.sell_proceeds_handling === "ADD_TO_EXISTING_CASH";
	const priceLabel = isEditIntent
		? "当前币种持仓价"
		: isSellTransaction
			? "当前币种卖出价"
			: "当前币种买入价";
	const pricePlaceholder = isEditIntent
		? "请输入修正后的持仓价"
		: isSellTransaction
			? "可选，不填则优先使用当前行情"
			: "可选，建议填写实际买入成交价";
	const dateFieldLabel = isEditIntent ? "买入日期" : "交易日";
	const dateLabel = dateFieldLabel;
	const datePlaceholder = isEditIntent ? "请选择买入日期" : "请选择交易日期";
	const defaultSaveErrorMessage = isEditIntent
		? "保存持仓资料失败，请稍后重试。"
		: isSellTransaction
			? "新增卖出失败，请稍后重试。"
			: "新增买入失败，请稍后重试。";
	const selectedSellHolding = isSellTransaction && draft.symbol && draft.market
		? findHoldingBySelectionKey(
			sellableHoldings,
			getHoldingSelectionKey({
				symbol: draft.symbol,
				market: draft.market,
			}),
		)
		: null;
	const canSelectExistingCashAccount = cashAccounts.length > 0;
	const canSelectBuyFundingCashAccount = cashAccounts.length > 0;
	const availableSellProceedsOptions = SELL_PROCEEDS_HANDLING_OPTIONS.filter((option) =>
		option.value !== "ADD_TO_EXISTING_CASH" || canSelectExistingCashAccount
	);
	const sellProceedsSelectionValue =
		draft.sell_proceeds_handling === "ADD_TO_EXISTING_CASH" && !canSelectExistingCashAccount
			? "CREATE_NEW_CASH"
			: draft.sell_proceeds_handling;
	const shouldShowSearchInput = !isSellTransaction && !isEditIntent;
	const shouldShowIdentityFields = !isSellTransaction && !isEditIntent;
	const parsedQuantity = draft.quantity.trim() ? Number(draft.quantity) : null;
	const parsedPrice = draft.cost_basis_price.trim() ? Number(draft.cost_basis_price) : null;
	const effectivePriceForCnyPreview = parsedPrice ??
		(isSellTransaction && selectedSellHolding?.price != null ? selectedSellHolding.price : null);
	const targetAmountCny = draft.fallback_currency
		? calculateTargetCnyAmount(
			(parsedQuantity ?? NaN) * (effectivePriceForCnyPreview ?? NaN),
			normalizeSupportedCurrency(draft.fallback_currency, "CNY"),
			{ fxRates },
		)
		: null;

	useEffect(() => {
		if (!isSellTransaction) {
			return;
		}

		if (canSelectExistingCashAccount) {
			return;
		}

		if (draft.sell_proceeds_handling !== "ADD_TO_EXISTING_CASH") {
			return;
		}

		setDraft((currentDraft) => ({
			...currentDraft,
			sell_proceeds_handling: "CREATE_NEW_CASH",
			sell_proceeds_account_id: "",
		}));
	}, [
		canSelectExistingCashAccount,
		draft.sell_proceeds_handling,
		isSellTransaction,
	]);

	useEffect(() => {
		if (!isSellTransaction) {
			return;
		}

		if (shouldMergeIntoExistingCash) {
			return;
		}

		if (!draft.sell_proceeds_account_id) {
			return;
		}

		setDraft((currentDraft) => ({
			...currentDraft,
			sell_proceeds_account_id: "",
		}));
	}, [
		draft.sell_proceeds_account_id,
		isSellTransaction,
		shouldMergeIntoExistingCash,
	]);

	useEffect(() => {
		if (isSellTransaction || canSelectBuyFundingCashAccount) {
			return;
		}

		if (!draft.buy_funding_account_id) {
			return;
		}

		setDraft((currentDraft) => ({
			...currentDraft,
			buy_funding_account_id: "",
		}));
	}, [
		canSelectBuyFundingCashAccount,
		draft.buy_funding_account_id,
		isSellTransaction,
	]);

	function updateDraft<K extends keyof HoldingFormDraft>(
		field: K,
		nextValue: HoldingFormDraft[K],
	): void {
		setLocalError(null);
		setDraft((currentDraft) => ({
			...currentDraft,
			[field]: nextValue,
		}));
	}

	function handleQuantityChange(nextValue: string): void {
		updateDraft(
			"quantity",
			clampSellQuantityDraftValue(nextValue, selectedSellHolding?.quantity),
		);
	}

	function handleSellHoldingChange(selectionKey: string): void {
		setLocalError(null);
		setSearchError(null);

		if (!selectionKey) {
			setDraft((currentDraft) => ({
				...createHoldingResetDraft("sell", maxStartedOnDate),
				note: currentDraft.note,
				sell_proceeds_handling: currentDraft.sell_proceeds_handling,
				sell_proceeds_account_id: currentDraft.sell_proceeds_account_id,
			}));
			return;
		}

		const nextHolding = findHoldingBySelectionKey(sellableHoldings, selectionKey);
		if (!nextHolding) {
			return;
		}

		setDraft((currentDraft) =>
			applyHoldingSelectionToDraft(currentDraft, nextHolding, {
				resetQuantity: true,
				prefillSellPrice: true,
				defaultTradeDate: resolveDefaultTradeDate(maxStartedOnDate),
			}),
		);
	}

	function handleSearchInput(nextValue: string): void {
		setLocalError(null);
		setSearchError(null);
		setSearchQuery(nextValue);
		if (!searchEnabled) {
			return;
		}

		setDraft((currentDraft) => ({
			...currentDraft,
			symbol: "",
			name: "",
			market: "",
			fallback_currency: "",
			cost_basis_price: "",
		}));
	}

	function applySearchResult(result: SecuritySearchResult): void {
		setLocalError(null);
		setSearchError(null);
		setSearchQuery(result.name);
		setSearchResults([]);
		setIsSearchOpen(false);
		setDraft((currentDraft) => ({
			...currentDraft,
			symbol: result.symbol,
			name: result.name,
			market: result.market,
			fallback_currency: normalizeSupportedCurrency(
				result.currency,
				currentDraft.fallback_currency || "CNY",
			),
			quantity: "",
			cost_basis_price: "",
			broker: shouldPrefillBroker(result.source)
				? result.source ?? currentDraft.broker
				: isImplicitSearchSourceLabel(currentDraft.broker)
					? ""
					: currentDraft.broker,
		}));
	}

	async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
		event.preventDefault();
		setLocalError(null);

		try {
			if (!isEditIntent && (!draft.symbol || !draft.name || !draft.market || !draft.fallback_currency)) {
				throw new Error(
					isSellTransaction
						? "请先从当前持仓中选择要卖出的标的。"
						: "请先选择投资标的，再填写数量。",
				);
			}

			const payload = toHoldingInput(draft);
			if (!Number.isFinite(payload.quantity) || payload.quantity <= 0) {
				throw new Error(
					isEditIntent
						? "请输入有效的持仓数量。"
						: isSellTransaction
							? "请输入有效的卖出数量。"
							: "请输入有效的买入数量。",
				);
			}
			if (!payload.started_on) {
				throw new Error(`${dateFieldLabel}为必填项。`);
			}
			if (
				payload.cost_basis_price !== undefined &&
				(!Number.isFinite(payload.cost_basis_price) || payload.cost_basis_price <= 0)
			) {
				throw new Error(isEditIntent ? "请输入有效的持仓价。" : "请输入有效的成交价。");
			}
			if (
				!isEditIntent &&
				payload.side === "SELL" &&
				payload.sell_proceeds_handling === "ADD_TO_EXISTING_CASH" &&
				!payload.sell_proceeds_account_id
			) {
				throw new Error("请选择一个已有现金账户来接收卖出回款。");
			}
			if (!isEditIntent && payload.side === "SELL" && selectedSellHolding == null) {
				throw new Error("请先从当前持仓中选择要卖出的标的。");
			}
			if (
				!isEditIntent &&
				payload.side === "SELL" &&
				selectedSellHolding != null &&
				payload.quantity > selectedSellHolding.quantity
			) {
				throw new Error(
					`卖出数量不能超过当前持仓，当前最多可卖 ${formatQuantity(selectedSellHolding.quantity)}。`,
				);
			}
			if (
				!allowsFractionalQuantity(draft.market) &&
				!Number.isInteger(payload.quantity)
			) {
				throw new Error("股票请使用整数数量，基金和加密货币可使用小数。");
			}
			if (
				payload.started_on &&
				maxStartedOnDate &&
				payload.started_on > maxStartedOnDate
			) {
				throw new Error(`${dateFieldLabel}不能晚于服务器今日日期（${maxStartedOnDate}）。`);
			}

			const duplicateHolding = !isEditIntent && payload.side === "BUY"
				? findDuplicateHolding(existingHoldings, payload.symbol, recordId)
				: null;
			if (duplicateHolding && isEditIntent) {
				throw new Error("该标的已存在持仓，请使用“新增买入”追加，或先处理现有持仓。");
			}
			if (duplicateHolding && onMergeDuplicate) {
				setPendingMergePreview(
					buildHoldingMergePreview(
						duplicateHolding,
						payload,
						isEditIntent ? recordId : null,
					),
				);
				return;
			}

			setIsWorking(true);

			if (isEditIntent && recordId !== null) {
				await onEdit?.(recordId, payload);
			} else {
				await onCreate?.(payload);
				setDraft(createHoldingResetDraft(resolvedIntent, maxStartedOnDate));
				setSearchQuery("");
				setSearchResults([]);
				setIsSearchOpen(false);
			}
		} catch (error) {
			setLocalError(toErrorMessage(error, defaultSaveErrorMessage));
		} finally {
			setIsWorking(false);
		}
	}

	async function handleConfirmMerge(): Promise<void> {
		if (!pendingMergePreview || !onMergeDuplicate) {
			return;
		}

		setLocalError(null);
		setIsWorking(true);

		try {
			await onMergeDuplicate({
				targetRecordId: pendingMergePreview.targetRecord.id,
				sourceRecordId: pendingMergePreview.sourceRecordId,
				mergedPayload: pendingMergePreview.mergedPayload,
			});
			setPendingMergePreview(null);
			if (!isEditIntent) {
				setDraft(createHoldingResetDraft(resolvedIntent, maxStartedOnDate));
				setSearchQuery("");
				setSearchResults([]);
				setIsSearchOpen(false);
			}
			onCancel?.();
		} catch (error) {
			setLocalError(toErrorMessage(error, "追加持仓失败，请稍后重试。"));
		} finally {
			setIsWorking(false);
		}
	}

	function handleDismissMergePreview(): void {
		if (isSubmitting) {
			return;
		}

		setPendingMergePreview(null);
	}

	async function handleDelete(): Promise<void> {
		if (!onDelete || recordId === null) {
			return;
		}

		setLocalError(null);
		setIsWorking(true);

		try {
			await onDelete(recordId);
		} catch (error) {
			setLocalError(toErrorMessage(error, "删除持仓失败，请稍后重试。"));
		} finally {
			setIsWorking(false);
		}
	}

	return (
		<section className="asset-manager__panel">
			<div className="asset-manager__panel-head">
				<div>
					<p className="asset-manager__eyebrow">HOLDING FORM</p>
					<h3>{resolvedTitle}</h3>
					{subtitle ? <p>{subtitle}</p> : null}
				</div>
				{isEditIntent && onCancel ? (
					<div className="asset-manager__panel-actions">
						<button
							type="button"
							className="asset-manager__button asset-manager__button--danger"
							onClick={onCancel}
							disabled={isSubmitting}
						>
							{cancelLabel}
						</button>
					</div>
				) : null}
			</div>

			{effectiveError ? (
				<div className="asset-manager__message asset-manager__message--error">
					{effectiveError}
				</div>
			) : null}

			{isEditIntent ? (
				<div className="asset-manager__helper-block asset-manager__helper-block--highlight">
					<strong>在这里修正当前持仓中的录入错误</strong>
					<p className="asset-manager__helper-text">
						如果数量、持仓价或买入日期录错了，请直接在这里修改。系统会保留一条编辑记录，方便后续核对。
					</p>
				</div>
			) : null}

			{isSellTransaction && sellableHoldings.length === 0 ? (
				<div className="asset-manager__message asset-manager__message--error">
					当前没有可卖持仓，请先新增买入
				</div>
			) : null}

			<form className="asset-manager__form" onSubmit={(event) => void handleSubmit(event)}>
				{shouldShowSearchInput ? (
					<HoldingSearchSection
						draft={draft}
						searchEnabled={searchEnabled}
						searchQuery={searchQuery}
						searchResults={searchResults}
						searchError={searchError}
						isSearching={isSearching}
						isSearchOpen={isSearchOpen}
						onSearchInputChange={handleSearchInput}
						onFocus={() => setIsSearchOpen(searchResults.length > 0)}
						onBlur={() => window.setTimeout(() => setIsSearchOpen(false), 120)}
						onSelect={applySearchResult}
						getSearchLabel={getSearchLabel}
						shouldPrefillBroker={shouldPrefillBroker}
					/>
				) : null}

				<HoldingTransactionFields
					draft={draft}
					isEditIntent={isEditIntent}
					isSellTransaction={isSellTransaction}
					sellableHoldings={sellableHoldings}
					selectedSellHolding={selectedSellHolding}
					shouldShowIdentityFields={shouldShowIdentityFields}
					quantityLabel={quantityLabel}
					quantityMin={quantityMin}
					quantityStep={quantityStep}
					priceLabel={priceLabel}
					pricePlaceholder={pricePlaceholder}
					targetAmountCny={targetAmountCny}
					dateLabel={dateLabel}
					datePlaceholder={datePlaceholder}
					maxStartedOnDate={maxStartedOnDate}
					searchEnabled={searchEnabled}
					onUpdateDraft={updateDraft}
					onQuantityChange={handleQuantityChange}
					onSellHoldingChange={handleSellHoldingChange}
					allowsFractionalQuantity={allowsFractionalQuantity}
					getHoldingSelectionKey={getHoldingSelectionKey}
				/>

				<HoldingCashSettlementFields
					draft={draft}
					isEditIntent={isEditIntent}
					isSellTransaction={isSellTransaction}
					shouldMergeIntoExistingCash={shouldMergeIntoExistingCash}
					canSelectExistingCashAccount={canSelectExistingCashAccount}
					canSelectBuyFundingCashAccount={canSelectBuyFundingCashAccount}
					availableSellProceedsOptions={availableSellProceedsOptions}
					sellProceedsSelectionValue={sellProceedsSelectionValue}
					cashAccounts={cashAccounts}
					onUpdateDraft={updateDraft}
					formatCashAccountOptionLabel={formatCashAccountOptionLabel}
				/>

				<div className="asset-manager__form-actions">
					<button
						type="submit"
						className="asset-manager__button asset-manager__button--primary"
						disabled={isSubmitting || (isSellTransaction && sellableHoldings.length === 0)}
					>
						{isSubmitting ? "保存中..." : resolvedSubmitLabel}
					</button>

					{!isEditIntent && onCancel ? (
						<button
							type="button"
							className="asset-manager__button asset-manager__button--secondary"
							onClick={onCancel}
							disabled={isSubmitting}
						>
							{cancelLabel}
						</button>
					) : null}

					{isEditIntent && recordId !== null && onDelete ? (
						<button
							type="button"
							className="asset-manager__button asset-manager__button--danger"
							onClick={() => void handleDelete()}
							disabled={isSubmitting}
						>
							删除持仓
						</button>
					) : null}
				</div>
			</form>

			{pendingMergePreview ? (
				<HoldingMergePreviewDialog
					preview={pendingMergePreview}
					busy={isSubmitting}
					onConfirm={() => void handleConfirmMerge()}
					onDismiss={handleDismissMergePreview}
				/>
			) : null}
		</section>
	);
}
