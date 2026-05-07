import { formatMoneyAmount } from "../../lib/assetFormatting";
import type {
	HoldingInput,
	HoldingRecord,
} from "../../types/assets";

export type HoldingMergePreview = {
	targetRecord: HoldingRecord;
	sourceRecordId: number | null;
	mergedPayload: HoldingInput;
	existingQuantity: number;
	incomingQuantity: number;
	mergedQuantity: number;
	existingCostBasis: number | null;
	incomingCostBasis: number | null;
	mergedCostBasis: number | null;
	knownCostTotal: number | null;
	estimatedReturnPct: number | null;
};

type HoldingMergePreviewDialogProps = {
	preview: HoldingMergePreview;
	busy: boolean;
	onConfirm: () => void;
	onDismiss: () => void;
};

function formatPreviewPrice(value: number | null | undefined, currency: string): string {
	if (value == null || !Number.isFinite(value)) {
		return "待计算";
	}

	return formatMoneyAmount(value, currency);
}

export function HoldingMergePreviewDialog({
	preview,
	busy,
	onConfirm,
	onDismiss,
}: HoldingMergePreviewDialogProps) {
	return (
		<div
			className="asset-manager__modal"
			role="dialog"
			aria-modal="true"
			aria-labelledby="holding-merge-title"
		>
			<button
				type="button"
				className="asset-manager__modal-backdrop"
				onClick={onDismiss}
				aria-label="关闭重复持仓提示"
			/>
			<div className="asset-manager__modal-panel">
				<div className="asset-manager__modal-head">
					<div>
						<p className="asset-manager__eyebrow">DUPLICATE HOLDING</p>
						<h3 id="holding-merge-title">已存在相同投资标的</h3>
						<p>
							{preview.targetRecord.name}（{preview.targetRecord.symbol}）已经在持仓列表中，
							确认后会按追加买入合并到原条目。
						</p>
					</div>
				</div>

				<div className="asset-manager__preview-grid">
					<div className="asset-manager__preview-item">
						<span>原持仓数量</span>
						<strong>{preview.existingQuantity}</strong>
					</div>
					<div className="asset-manager__preview-item">
						<span>本次追加数量</span>
						<strong>{preview.incomingQuantity}</strong>
					</div>
					<div className="asset-manager__preview-item">
						<span>合并后数量</span>
						<strong>{preview.mergedQuantity}</strong>
					</div>
					<div className="asset-manager__preview-item">
						<span>合并后持仓均价</span>
						<strong>
							{formatPreviewPrice(
								preview.mergedCostBasis,
								preview.mergedPayload.fallback_currency,
							)}
						</strong>
					</div>
					<div className="asset-manager__preview-item">
						<span>已知成本总额</span>
						<strong>
							{formatPreviewPrice(
								preview.knownCostTotal,
								preview.mergedPayload.fallback_currency,
							)}
						</strong>
					</div>
					<div className="asset-manager__preview-item">
						<span>预估收益率</span>
						<strong>
							{preview.estimatedReturnPct != null
								? `${preview.estimatedReturnPct.toFixed(2)}%`
								: "待计算"}
						</strong>
					</div>
				</div>

				<div className="asset-manager__helper-block">
					<p>
						系统会用原持仓数量 x 原持仓价，加上本次数量 x 本次持仓价，计算新的加权均价，
						然后直接覆盖原条目。
					</p>
				</div>

				<div className="asset-manager__form-actions">
					<button
						type="button"
						className="asset-manager__button"
						onClick={onConfirm}
						disabled={busy}
					>
						{busy ? "处理中..." : "确认追加"}
					</button>
					<button
						type="button"
						className="asset-manager__button asset-manager__button--secondary"
						onClick={onDismiss}
						disabled={busy}
					>
						返回修改
					</button>
				</div>
			</div>
		</div>
	);
}
