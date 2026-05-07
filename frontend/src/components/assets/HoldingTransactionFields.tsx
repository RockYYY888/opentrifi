import { DatePickerField } from "./DatePickerField";
import {
	formatCnyAmount,
	formatPriceAmount,
	formatQuantity,
	formatSecurityMarket,
} from "../../lib/assetFormatting";
import { TARGET_DISPLAY_CURRENCY } from "../../lib/assetCurrency";
import type {
	HoldingFormDraft,
	HoldingRecord,
} from "../../types/assets";
import {
	SECURITY_MARKET_OPTIONS,
	SUPPORTED_CURRENCY_OPTIONS,
} from "../../types/assets";

type HoldingTransactionFieldsProps = {
	draft: HoldingFormDraft;
	isEditIntent: boolean;
	isSellTransaction: boolean;
	sellableHoldings: HoldingRecord[];
	selectedSellHolding: HoldingRecord | null;
	shouldShowIdentityFields: boolean;
	quantityLabel: string;
	quantityMin: string;
	quantityStep: string;
	priceLabel: string;
	pricePlaceholder: string;
	targetAmountCny: number | null;
	dateLabel: string;
	datePlaceholder: string;
	maxStartedOnDate?: string;
	searchEnabled: boolean;
	onUpdateDraft: <K extends keyof HoldingFormDraft>(
		key: K,
		value: HoldingFormDraft[K],
	) => void;
	onQuantityChange: (value: string) => void;
	onSellHoldingChange: (selectionKey: string) => void;
	allowsFractionalQuantity: (market: HoldingFormDraft["market"]) => boolean;
	getHoldingSelectionKey: (holding: Pick<HoldingRecord, "symbol" | "market">) => string;
};

export function HoldingTransactionFields({
	draft,
	isEditIntent,
	isSellTransaction,
	sellableHoldings,
	selectedSellHolding,
	shouldShowIdentityFields,
	quantityLabel,
	quantityMin,
	quantityStep,
	priceLabel,
	pricePlaceholder,
	targetAmountCny,
	dateLabel,
	datePlaceholder,
	maxStartedOnDate,
	searchEnabled,
	onUpdateDraft,
	onQuantityChange,
	onSellHoldingChange,
	allowsFractionalQuantity,
	getHoldingSelectionKey,
}: HoldingTransactionFieldsProps) {
	return (
		<>
			{isSellTransaction ? (
				<label className="asset-manager__field">
					<span>卖出持仓</span>
					<select
						aria-label="卖出持仓"
						value={selectedSellHolding ? getHoldingSelectionKey(selectedSellHolding) : ""}
						onChange={(event) => onSellHoldingChange(event.target.value)}
					>
						<option value="">请选择一笔当前持仓</option>
						{sellableHoldings.map((holding) => (
							<option
								key={getHoldingSelectionKey(holding)}
								value={getHoldingSelectionKey(holding)}
							>
								{holding.name} ({holding.symbol}) · {formatQuantity(holding.quantity)}
							</option>
						))}
					</select>
					{selectedSellHolding ? (
						<p className="asset-manager__helper-text">
							当前可卖 {formatQuantity(selectedSellHolding.quantity)}
							{selectedSellHolding.price != null && selectedSellHolding.price > 0
								? `，当前实时卖出参考价 ${formatPriceAmount(
									selectedSellHolding.price,
									selectedSellHolding.price_currency ??
										selectedSellHolding.fallback_currency,
								)}`
								: ""}
						</p>
					) : (
						<p className="asset-manager__helper-text">只能从当前持仓里选择要卖出的标的</p>
					)}
				</label>
			) : null}

			{shouldShowIdentityFields ? (
				<div className="asset-manager__field-grid">
					<label className="asset-manager__field">
						<span>代码</span>
						<input
							required
							value={draft.symbol}
							onChange={(event) => onUpdateDraft("symbol", event.target.value)}
							placeholder="选择后自动填入"
							readOnly={searchEnabled}
						/>
					</label>

					<label className="asset-manager__field">
						<span>名称</span>
						<input
							required
							value={draft.name}
							onChange={(event) => onUpdateDraft("name", event.target.value)}
							placeholder="选择后自动填入"
							readOnly={searchEnabled}
						/>
					</label>
				</div>
			) : null}

			{isEditIntent ? (
				<div className="asset-manager__helper-block">
					<p>
						当前持仓：{draft.name || "未命名标的"}
						{draft.symbol ? ` (${draft.symbol})` : ""}
						{draft.market ? ` · ${formatSecurityMarket(draft.market)}` : ""}
					</p>
				</div>
			) : null}

			<div className="asset-manager__field-grid">
				{isSellTransaction || isEditIntent ? (
					<label className="asset-manager__field">
						<span>市场</span>
						<input
							value={draft.market ? formatSecurityMarket(draft.market) : ""}
							placeholder={isSellTransaction ? "选择持仓后自动带出" : "当前持仓市场"}
							readOnly
						/>
					</label>
				) : (
					<label className="asset-manager__field">
						<span>市场</span>
						<select
							value={draft.market}
							onChange={(event) =>
								onUpdateDraft("market", event.target.value as HoldingFormDraft["market"])
							}
						>
							<option value="">请选择市场</option>
							{SECURITY_MARKET_OPTIONS.map((option) => (
								<option key={option.value} value={option.value}>
									{option.label}
								</option>
							))}
						</select>
					</label>
				)}

				<label className="asset-manager__field">
					<span>{quantityLabel}</span>
					<input
						required
						type="number"
						min={quantityMin}
						max={
							isSellTransaction && selectedSellHolding
								? String(selectedSellHolding.quantity)
								: undefined
						}
						step={quantityStep}
						value={draft.quantity}
						onChange={(event) => onQuantityChange(event.target.value)}
						placeholder={allowsFractionalQuantity(draft.market) ? "1.0000" : "100"}
					/>
				</label>
			</div>

			<div className="asset-manager__field-grid">
				{isSellTransaction || isEditIntent ? (
					<label className="asset-manager__field">
						<span>当前币种</span>
						<input
							required
							value={draft.fallback_currency}
							placeholder={isSellTransaction ? "选择持仓后自动带出" : "当前持仓币种"}
							readOnly
						/>
					</label>
				) : (
					<label className="asset-manager__field">
						<span>当前币种</span>
						<select
							required
							value={draft.fallback_currency}
							onChange={(event) =>
								onUpdateDraft(
									"fallback_currency",
									event.target.value as HoldingFormDraft["fallback_currency"],
								)
							}
						>
							<option value="">请选择当前币种</option>
							{SUPPORTED_CURRENCY_OPTIONS.map((option) => (
								<option key={option.value} value={option.value}>
									{option.label}
								</option>
							))}
						</select>
					</label>
				)}

				<label className="asset-manager__field">
					<span>目标币种</span>
					<input value={TARGET_DISPLAY_CURRENCY} readOnly />
				</label>
			</div>

			<div className="asset-manager__field-grid">
				<label className="asset-manager__field">
					<span>{priceLabel}</span>
					<input
						type="number"
						min="0.0001"
						step="0.0001"
						value={draft.cost_basis_price}
						onChange={(event) => onUpdateDraft("cost_basis_price", event.target.value)}
						placeholder={pricePlaceholder}
					/>
				</label>

				<label className="asset-manager__field">
					<span>目标币种金额（CNY）</span>
					<input
						value={targetAmountCny != null ? formatCnyAmount(targetAmountCny) : ""}
						placeholder="按当前汇率自动计算"
						readOnly
					/>
				</label>
			</div>

			{!isEditIntent ? (
				<label className="asset-manager__field">
					<span>账户 / 来源</span>
					<input
						value={draft.broker}
						onChange={(event) => onUpdateDraft("broker", event.target.value)}
						placeholder="搜索后自动填入，可手动修改"
					/>
				</label>
			) : null}

			<label className="asset-manager__field">
				<span>{dateLabel}</span>
				<DatePickerField
					value={draft.started_on}
					onChange={(nextValue) => onUpdateDraft("started_on", nextValue)}
					maxDate={maxStartedOnDate}
					placeholder={datePlaceholder}
				/>
			</label>

			{!isEditIntent ? (
				<label className="asset-manager__field">
					<span>备注</span>
					<textarea
						value={draft.note}
						onChange={(event) => onUpdateDraft("note", event.target.value)}
						placeholder="可选"
					/>
				</label>
			) : null}
		</>
	);
}
