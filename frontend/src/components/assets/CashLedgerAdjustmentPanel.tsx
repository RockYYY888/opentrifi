import { useEffect, useMemo, useState } from "react";
import "./asset-components.css";
import { DatePickerField } from "./DatePickerField";
import {
	calculateTargetCnyAmount,
	TARGET_DISPLAY_CURRENCY,
	type SupportedCurrencyFxRates,
} from "../../lib/assetCurrency";
import { formatCnyAmount, formatDateValue, formatMoneyAmount } from "../../lib/assetFormatting";
import { useAutoRefreshGuard } from "../../lib/autoRefreshGuards";
import { toErrorMessage } from "../../lib/apiClient";
import type {
	CashAccountRecord,
	CashLedgerAdjustmentFormDraft,
	CashLedgerAdjustmentInput,
	CashLedgerEntryRecord,
	MaybePromise,
} from "../../types/assets";
import { DEFAULT_CASH_LEDGER_ADJUSTMENT_FORM_DRAFT } from "../../types/assets";
import { getCollectionLoadingState } from "./loadingState";

export interface CashLedgerAdjustmentPanelProps {
	accounts: CashAccountRecord[];
	entries: CashLedgerEntryRecord[];
	loading?: boolean;
	busy?: boolean;
	errorMessage?: string | null;
	maxStartedOnDate?: string;
	fxRates?: SupportedCurrencyFxRates;
	onCreate?: (payload: CashLedgerAdjustmentInput) => MaybePromise<CashLedgerEntryRecord | null>;
	onEdit?: (
		recordId: number,
		payload: CashLedgerAdjustmentInput,
	) => MaybePromise<CashLedgerEntryRecord>;
	onDelete?: (recordId: number) => MaybePromise<void>;
}

function getTodayDateValue(): string {
	const now = new Date();
	const year = now.getFullYear();
	const month = String(now.getMonth() + 1).padStart(2, "0");
	const day = String(now.getDate()).padStart(2, "0");
	return `${year}-${month}-${day}`;
}

function createAdjustmentDraft(maxStartedOnDate?: string): CashLedgerAdjustmentFormDraft {
	return {
		...DEFAULT_CASH_LEDGER_ADJUSTMENT_FORM_DRAFT,
		happened_on: maxStartedOnDate ?? getTodayDateValue(),
	};
}

function toDraft(entry: CashLedgerEntryRecord): CashLedgerAdjustmentFormDraft {
	return {
		cash_account_id: String(entry.cash_account_id),
		amount: String(entry.amount),
		happened_on: entry.happened_on,
		note: entry.note ?? "",
	};
}

export function CashLedgerAdjustmentPanel({
	accounts,
	entries,
	loading = false,
	busy = false,
	errorMessage = null,
	maxStartedOnDate,
	fxRates,
	onCreate,
	onEdit,
	onDelete,
}: CashLedgerAdjustmentPanelProps) {
	const [isFormOpen, setIsFormOpen] = useState(false);
	const [editingId, setEditingId] = useState<number | null>(null);
	const [draft, setDraft] = useState<CashLedgerAdjustmentFormDraft>(() =>
		createAdjustmentDraft(maxStartedOnDate),
	);
	const [localError, setLocalError] = useState<string | null>(null);
	const [isWorking, setIsWorking] = useState(false);
	const [deletingId, setDeletingId] = useState<number | null>(null);
	const effectiveError = localError ?? errorMessage;
	const adjustmentEntries = useMemo(
		() => entries.filter((entry) => entry.entry_type === "MANUAL_ADJUSTMENT"),
		[entries],
	);
	const editingEntry = useMemo(
		() => adjustmentEntries.find((entry) => entry.id === editingId) ?? null,
		[adjustmentEntries, editingId],
	);
	const selectedAccount = useMemo(
		() => accounts.find((account) => String(account.id) === draft.cash_account_id) ?? null,
		[accounts, draft.cash_account_id],
	);
	const parsedAmount = draft.amount.trim() ? Number(draft.amount) : null;
	const targetAmountCny = selectedAccount
		? calculateTargetCnyAmount(parsedAmount, selectedAccount.currency, {
			explicitFxToCny: selectedAccount.fx_to_cny ?? null,
			fxRates,
		})
		: null;
	const { showBlockingLoader, showRefreshingHint } = getCollectionLoadingState(
		loading,
		adjustmentEntries.length,
	);
	useAutoRefreshGuard(isFormOpen, "cash-ledger-adjustment-form");

	useEffect(() => {
		if (!isFormOpen || draft.happened_on) {
			return;
		}
		setDraft((currentDraft) => ({
			...currentDraft,
			happened_on: maxStartedOnDate ?? getTodayDateValue(),
		}));
	}, [draft.happened_on, isFormOpen, maxStartedOnDate]);

	function updateDraft<K extends keyof CashLedgerAdjustmentFormDraft>(
		field: K,
		nextValue: CashLedgerAdjustmentFormDraft[K],
	): void {
		setLocalError(null);
		setDraft((currentDraft) => ({
			...currentDraft,
			[field]: nextValue,
		}));
	}

	function openCreateForm(): void {
		setLocalError(null);
		setEditingId(null);
		setDraft(createAdjustmentDraft(maxStartedOnDate));
		setIsFormOpen(true);
	}

	function openEditForm(entry: CashLedgerEntryRecord): void {
		setLocalError(null);
		setEditingId(entry.id);
		setDraft(toDraft(entry));
		setIsFormOpen(true);
	}

	function closeForm(): void {
		setLocalError(null);
		setEditingId(null);
		setDraft(createAdjustmentDraft(maxStartedOnDate));
		setIsFormOpen(false);
	}

	async function handleSubmit(): Promise<void> {
		try {
			if (!draft.cash_account_id) {
				throw new Error("请选择需要修正的现金账户。");
			}
			const amount = Number(draft.amount);
			if (!Number.isFinite(amount) || amount === 0) {
				throw new Error("请输入不为 0 的调整金额。");
			}
			if (!draft.happened_on) {
				throw new Error("请选择账本调整日。");
			}
			const payload: CashLedgerAdjustmentInput = {
				cash_account_id: Number(draft.cash_account_id),
				amount,
				happened_on: draft.happened_on,
				note: draft.note.trim() || undefined,
			};
			setIsWorking(true);
			if (editingId != null) {
				if (!onEdit) {
					return;
				}
				await onEdit(editingId, payload);
			} else {
				if (!onCreate) {
					return;
				}
				await onCreate(payload);
			}
			closeForm();
		} catch (error) {
			setLocalError(toErrorMessage(error, "保存手工账本调整失败，请稍后重试。"));
		} finally {
			setIsWorking(false);
		}
	}

	async function handleDelete(recordId: number): Promise<void> {
		if (!onDelete) {
			return;
		}

		try {
			setLocalError(null);
			setDeletingId(recordId);
			await onDelete(recordId);
			if (editingId === recordId) {
				closeForm();
			}
		} catch (error) {
			setLocalError(toErrorMessage(error, "删除手工账本调整失败，请稍后重试。"));
		} finally {
			setDeletingId(null);
		}
	}

	return (
		<section className="asset-manager__panel">
			<div className="asset-manager__list-head">
				<div>
					<p className="asset-manager__eyebrow">CASH LEDGER</p>
					<h3>手工账本调整</h3>
					<p>只用于修正漏记、差额或对账异常 自动生成的现金分录请回到交易或账户划转里修正。</p>
				</div>
				<div className="asset-manager__mini-actions">
					{onCreate ? (
						<button
							type="button"
							className="asset-manager__button asset-manager__button--primary"
							onClick={openCreateForm}
							disabled={busy || accounts.length === 0}
						>
							新增调整
						</button>
					) : null}
				</div>
			</div>

			<div className="asset-manager__helper-block">
				<strong>调整规则</strong>
				<p>正数表示补记流入 负数表示补记流出 账本会立即回放到账户余额和总资产历史。</p>
			</div>

			{effectiveError ? (
				<div className="asset-manager__message asset-manager__message--error">
					{effectiveError}
				</div>
			) : null}

			{showRefreshingHint ? (
				<div className="asset-manager__status-note" role="status" aria-live="polite">
					正在更新手工账本调整...
				</div>
			) : null}

			{isFormOpen ? (
				<div className="asset-manager__form">
					<div className="asset-manager__field-grid">
						<label className="asset-manager__field">
							<span>现金账户</span>
							<select
								value={draft.cash_account_id}
								onChange={(event) => updateDraft("cash_account_id", event.target.value)}
								disabled={editingId != null}
							>
								<option value="">请选择</option>
								{accounts.map((account) => (
									<option key={account.id} value={String(account.id)}>
										{account.name} · {formatMoneyAmount(account.balance, account.currency)}
									</option>
								))}
							</select>
						</label>
						<label className="asset-manager__field">
							<span>当前币种</span>
							<input
								value={selectedAccount?.currency ?? ""}
								placeholder="选择现金账户后自动带出"
								readOnly
							/>
						</label>
						<label className="asset-manager__field">
							<span>目标币种</span>
							<input value={TARGET_DISPLAY_CURRENCY} readOnly />
						</label>
					</div>

					<div className="asset-manager__field-grid">
						<label className="asset-manager__field">
							<span>当前币种变动金额</span>
							<input
								type="text"
								inputMode="decimal"
								value={draft.amount}
								onChange={(event) => updateDraft("amount", event.target.value)}
								placeholder="正数补记流入 负数补记流出"
							/>
						</label>
						<label className="asset-manager__field">
							<span>目标币种变动金额（CNY）</span>
							<input
								value={targetAmountCny != null ? formatCnyAmount(targetAmountCny) : ""}
								placeholder="按当前汇率自动计算"
								readOnly
							/>
						</label>
					</div>

					<div className="asset-manager__field-grid">
						<label className="asset-manager__field">
							<span>调整日</span>
							<DatePickerField
								value={draft.happened_on}
								onChange={(nextValue) => updateDraft("happened_on", nextValue)}
								maxDate={maxStartedOnDate}
								placeholder="选择调整日"
							/>
						</label>
					</div>

					<label className="asset-manager__field">
						<span>备注</span>
						<textarea
							value={draft.note}
							onChange={(event) => updateDraft("note", event.target.value)}
							placeholder="比如银行对账差额、补录现金收入等"
						/>
					</label>

					<div className="asset-manager__form-actions">
						<button
							type="button"
							className="asset-manager__button asset-manager__button--primary"
							onClick={() => void handleSubmit()}
							disabled={busy || isWorking}
						>
							{busy || isWorking ? "保存中..." : editingId != null ? "保存修正" : "确认调整"}
						</button>
						<button
							type="button"
							className="asset-manager__button asset-manager__button--secondary"
							onClick={closeForm}
							disabled={busy || isWorking}
						>
							取消
						</button>
					</div>
				</div>
			) : null}

			{showBlockingLoader ? (
				<div className="asset-manager__empty-state">正在加载手工账本调整...</div>
			) : adjustmentEntries.length === 0 ? (
				<div className="asset-manager__empty-state">还没有手工账本调整。</div>
			) : (
				<ul className="asset-manager__list">
					{adjustmentEntries.map((entry) => (
						<li key={entry.id} className="asset-manager__card">
							<div className="asset-manager__card-top">
								<div className="asset-manager__card-title">
									<div className="asset-manager__badge-row">
										<span className="asset-manager__badge">ADJUSTMENT</span>
									</div>
									<h3>
										{accounts.find((account) => account.id === entry.cash_account_id)?.name ??
											`#${entry.cash_account_id}`}
									</h3>
									<p className="asset-manager__card-note">
										{entry.note?.trim() || `账本调整 #${entry.id}`}
									</p>
								</div>
								<div className="asset-manager__card-actions">
									{onEdit ? (
										<button
											type="button"
											className="asset-manager__button asset-manager__button--secondary"
											onClick={() => openEditForm(entry)}
											disabled={busy || isWorking}
										>
											编辑调整
										</button>
									) : null}
									{onDelete ? (
										<button
											type="button"
											className="asset-manager__button asset-manager__button--danger"
											onClick={() => void handleDelete(entry.id)}
											disabled={busy || deletingId === entry.id}
										>
											{deletingId === entry.id ? "删除中..." : "删除"}
										</button>
									) : null}
								</div>
							</div>

							<div className="asset-manager__metric-grid">
								<div className="asset-manager__metric">
									<span>调整金额</span>
									<strong>{formatMoneyAmount(entry.amount, entry.currency)}</strong>
								</div>
								<div className="asset-manager__metric">
									<span>调整日</span>
									<strong>{formatDateValue(entry.happened_on)}</strong>
								</div>
								<div className="asset-manager__metric">
									<span>当前状态</span>
									<strong>{editingEntry?.id === entry.id ? "编辑中" : "已入账"}</strong>
								</div>
							</div>
						</li>
					))}
				</ul>
			)}
		</section>
	);
}
