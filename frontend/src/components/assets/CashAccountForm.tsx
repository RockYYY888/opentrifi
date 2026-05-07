import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import "./asset-components.css";
import { DatePickerField } from "./DatePickerField";
import {
	calculateTargetCnyAmount,
	TARGET_DISPLAY_CURRENCY,
	type SupportedCurrencyFxRates,
} from "../../lib/assetCurrency";
import { toErrorMessage } from "../../lib/apiClient";
import { useAutoRefreshGuard } from "../../lib/autoRefreshGuards";
import { formatCnyAmount } from "../../lib/assetFormatting";
import type {
	AssetEditorMode,
	CashAccountFormDraft,
	CashAccountInput,
	CashAccountRecord,
	MaybePromise,
} from "../../types/assets";
import {
	CASH_ACCOUNT_TYPE_OPTIONS,
	DEFAULT_CASH_ACCOUNT_FORM_DRAFT,
	getCashAccountTypeLabel,
	SUPPORTED_CURRENCY_OPTIONS,
} from "../../types/assets";
import { AssetDeleteDialog } from "./AssetDeleteDialog";
import {
	CASH_ACCOUNT_DELETE_DESCRIPTION,
	CASH_ACCOUNT_DELETE_IMPACT_ITEMS,
} from "./cashAccountDeleteCopy";

export interface CashAccountFormProps {
	mode?: AssetEditorMode;
	resetKey?: number;
	value?: Partial<CashAccountFormDraft> | null;
	recordId?: number | null;
	title?: string;
	subtitle?: string;
	submitLabel?: string;
	busy?: boolean;
	errorMessage?: string | null;
	activityAccount?: CashAccountRecord | null;
	fxRates?: SupportedCurrencyFxRates;
	onCreate?: (payload: CashAccountInput) => MaybePromise<unknown>;
	onEdit?: (recordId: number, payload: CashAccountInput) => MaybePromise<unknown>;
	onDelete?: (recordId: number) => MaybePromise<unknown>;
	onCancel?: () => void;
}

function toCashAccountDraft(
	value?: Partial<CashAccountFormDraft> | null,
): CashAccountFormDraft {
	return {
		...DEFAULT_CASH_ACCOUNT_FORM_DRAFT,
		...value,
	};
}

function toCashAccountInput(draft: CashAccountFormDraft): CashAccountInput {
	const normalizedNote = draft.note.trim();
	const platformLabel = getCashAccountTypeLabel(draft.account_type);

	return {
		name: draft.name.trim(),
		platform: platformLabel,
		currency: draft.currency,
		balance: Number(draft.balance),
		account_type: draft.account_type,
		started_on: draft.started_on.trim() || undefined,
		note: normalizedNote || undefined,
	};
}

export function CashAccountForm({
	mode = "create",
	resetKey = 0,
	value,
	recordId = null,
	title,
	subtitle,
	submitLabel,
	busy = false,
	errorMessage = null,
	activityAccount = null,
	fxRates,
	onCreate,
	onEdit,
	onDelete,
	onCancel,
}: CashAccountFormProps) {
	useAutoRefreshGuard(true, "cash-account-form");
	const [draft, setDraft] = useState<CashAccountFormDraft>(() =>
		toCashAccountDraft(value),
	);
	const [localError, setLocalError] = useState<string | null>(null);
	const [isWorking, setIsWorking] = useState(false);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);

	useEffect(() => {
		setDraft(toCashAccountDraft(value));
		setLocalError(null);
		setIsDeleteDialogOpen(false);
	}, [mode, resetKey]);

	const effectiveError = localError ?? errorMessage;
	const isSubmitting = busy || isWorking;
	const resolvedTitle = title ?? (mode === "edit" ? "编辑现金账户" : "新增现金账户");
	const resolvedSubmitLabel = submitLabel ?? (mode === "edit" ? "编辑" : "新增");
	const cancelLabel = mode === "edit" ? "取消编辑" : "取消";
	const parsedBalance = draft.balance.trim() ? Number(draft.balance) : null;
	const targetBalanceCny = calculateTargetCnyAmount(parsedBalance, draft.currency, {
		explicitFxToCny: activityAccount?.fx_to_cny ?? null,
		fxRates,
	});

	function updateDraft<K extends keyof CashAccountFormDraft>(
		field: K,
		nextValue: CashAccountFormDraft[K],
	): void {
		setLocalError(null);
		setDraft((currentDraft) => ({
			...currentDraft,
			[field]: nextValue,
		}));
	}

	async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
		event.preventDefault();
		setLocalError(null);
		setIsWorking(true);

		try {
			const payload = toCashAccountInput(draft);
			if (!payload.name || !payload.currency) {
				throw new Error("请完整填写账户名称、平台和币种。");
			}
			if (!Number.isFinite(payload.balance) || payload.balance < 0) {
				throw new Error("请输入有效的账户余额。");
			}

			if (mode === "edit" && recordId !== null) {
				await onEdit?.(recordId, payload);
			} else {
				await onCreate?.(payload);
				setDraft(DEFAULT_CASH_ACCOUNT_FORM_DRAFT);
			}
		} catch (error) {
			setLocalError(toErrorMessage(error, "保存现金账户失败，请稍后重试。"));
		} finally {
			setIsWorking(false);
		}
	}

	async function handleDelete(): Promise<boolean> {
		if (!onDelete || recordId === null) {
			return false;
		}

		setLocalError(null);
		setIsWorking(true);

		try {
			await onDelete(recordId);
			return true;
		} catch (error) {
			setLocalError(toErrorMessage(error, "删除现金账户失败，请稍后重试。"));
			return false;
		} finally {
			setIsWorking(false);
		}
	}

	return (
		<>
			<section className="asset-manager__panel">
				<div className="asset-manager__panel-head">
					<div>
						<p className="asset-manager__eyebrow">CASH FORM</p>
						<h3>{resolvedTitle}</h3>
						{subtitle ? <p>{subtitle}</p> : null}
					</div>
				</div>

				{effectiveError ? (
					<div className="asset-manager__message asset-manager__message--error">
						{effectiveError}
					</div>
				) : null}

				<form className="asset-manager__form" onSubmit={(event) => void handleSubmit(event)}>
					<label className="asset-manager__field">
						<span>账户名称</span>
						<input
							required
							value={draft.name}
							onChange={(event) => updateDraft("name", event.target.value)}
							placeholder="例如：日常备用金"
						/>
					</label>

				<div className="asset-manager__field-grid">
					<label className="asset-manager__field">
						<span>平台</span>
						<select
							value={draft.account_type}
							onChange={(event) =>
								updateDraft(
									"account_type",
									event.target.value as CashAccountFormDraft["account_type"],
								)
							}
						>
							{CASH_ACCOUNT_TYPE_OPTIONS.map((option) => (
								<option key={option.value} value={option.value}>
									{option.label}
								</option>
							))}
						</select>
					</label>

					<label className="asset-manager__field">
						<span>当前币种</span>
						<select
							value={draft.currency}
							onChange={(event) =>
								updateDraft(
									"currency",
									event.target.value as CashAccountFormDraft["currency"],
								)
							}
						>
							{SUPPORTED_CURRENCY_OPTIONS.map((option) => (
								<option key={option.value} value={option.value}>
									{option.label}
								</option>
							))}
						</select>
					</label>

					<label className="asset-manager__field">
						<span>目标币种</span>
						<input value={TARGET_DISPLAY_CURRENCY} readOnly />
					</label>

					<label className="asset-manager__field">
						<span>存入日</span>
						<DatePickerField
							value={draft.started_on}
							onChange={(nextValue) => updateDraft("started_on", nextValue)}
							placeholder="选择存入日"
						/>
					</label>
				</div>

				<div className="asset-manager__field-grid">
					<label className="asset-manager__field">
						<span>当前币种余额</span>
						<input
							required
							type="text"
							inputMode="decimal"
							value={draft.balance}
							onChange={(event) => updateDraft("balance", event.target.value)}
							placeholder="10000"
						/>
					</label>

					<label className="asset-manager__field">
						<span>目标币种估值（CNY）</span>
						<input
							value={targetBalanceCny != null ? formatCnyAmount(targetBalanceCny) : ""}
							placeholder="按当前汇率自动计算"
							readOnly
						/>
					</label>
				</div>

					<label className="asset-manager__field">
						<span>备注</span>
						<textarea
							value={draft.note}
							onChange={(event) => updateDraft("note", event.target.value)}
							placeholder="可选，例如：仅作流动资金 / 固定储蓄"
						/>
					</label>

					<div className="asset-manager__form-actions">
						<button
							type="submit"
							className="asset-manager__button asset-manager__button--primary"
							disabled={isSubmitting}
						>
							{isSubmitting ? "保存中..." : resolvedSubmitLabel}
						</button>

						{onCancel ? (
							<button
								type="button"
								className="asset-manager__button asset-manager__button--secondary"
								onClick={onCancel}
								disabled={isSubmitting}
							>
								{cancelLabel}
							</button>
						) : null}

						{mode === "edit" && recordId !== null && onDelete ? (
							<button
								type="button"
								className="asset-manager__button asset-manager__button--danger"
								onClick={() => setIsDeleteDialogOpen(true)}
								disabled={isSubmitting}
							>
								删除账户
							</button>
						) : null}
					</div>
				</form>
			</section>

			<AssetDeleteDialog
				open={isDeleteDialogOpen}
				busy={isSubmitting}
				title={`确认删除 ${draft.name.trim() || "这个现金账户"}？`}
				description={CASH_ACCOUNT_DELETE_DESCRIPTION}
				impactItems={[...CASH_ACCOUNT_DELETE_IMPACT_ITEMS]}
				onClose={() => {
					if (!isSubmitting) {
						setIsDeleteDialogOpen(false);
					}
				}}
				onConfirm={() => {
					void handleDelete().then((deleted) => {
						if (deleted) {
							setIsDeleteDialogOpen(false);
						}
					});
				}}
			/>
		</>
	);
}
