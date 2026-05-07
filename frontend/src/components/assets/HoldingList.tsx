import "./asset-components.css";
import {
	formatCnyAmount,
	formatDateValue,
	formatPercentValue,
	formatPriceAmount,
	formatQuantity,
	formatSecurityMarket,
	formatTimestamp,
} from "../../lib/assetFormatting";
import type { HoldingRecord } from "../../types/assets";
import { getCollectionLoadingState } from "./loadingState";

function shouldShowHoldingSource(source?: string | null): boolean {
	return Boolean(source && source !== "代码推断" && source !== "本地映射");
}

export interface HoldingListProps {
	holdings: HoldingRecord[];
	title?: string;
	subtitle?: string;
	loading?: boolean;
	busy?: boolean;
	errorMessage?: string | null;
	emptyMessage?: string;
	onCreateBuy?: () => void;
	onCreateSell?: () => void;
	onEdit?: (holding: HoldingRecord) => void;
}

export function HoldingList({
	holdings,
	title = "投资类持仓",
	subtitle,
	loading = false,
	busy = false,
	errorMessage = null,
	emptyMessage = "暂无投资类资产。",
	onCreateBuy,
	onCreateSell,
	onEdit,
}: HoldingListProps) {
	const isActionLocked = busy;
	const { showBlockingLoader, showRefreshingHint } = getCollectionLoadingState(
		loading,
		holdings.length,
	);

	function isHoldingQuotePending(holding: HoldingRecord): boolean {
		return !holding.last_updated || (holding.price ?? 0) <= 0;
	}

	return (
		<section className="asset-manager__panel">
			<div className="asset-manager__list-head">
				<div>
					<p className="asset-manager__eyebrow">HOLDING LIST</p>
					<h3>{title}</h3>
					{subtitle ? <p>{subtitle}</p> : null}
				</div>
				<div className="asset-manager__mini-actions">
					{onCreateBuy ? (
						<button
							type="button"
							className="asset-manager__button asset-manager__button--primary"
							onClick={onCreateBuy}
							disabled={isActionLocked}
						>
							新增买入
						</button>
					) : null}
					{onCreateSell ? (
						<button
							type="button"
							className="asset-manager__button asset-manager__button--danger"
							onClick={onCreateSell}
							disabled={isActionLocked}
						>
							新增卖出
						</button>
					) : null}
				</div>
			</div>

			{errorMessage ? (
				<div className="asset-manager__message asset-manager__message--error">
					{errorMessage}
				</div>
			) : null}

			{showRefreshingHint ? (
				<div className="asset-manager__status-note" role="status" aria-live="polite">
					正在更新投资类持仓...
				</div>
			) : null}

			{showBlockingLoader ? (
				<div className="asset-manager__empty-state">正在加载投资类资产...</div>
			) : holdings.length === 0 ? (
				<div className="asset-manager__empty-state">{emptyMessage}</div>
			) : (
				<ul className="asset-manager__list">
					{holdings.map((holding) => {
						const quotePending = isHoldingQuotePending(holding);

						return (
							<li key={holding.id} className="asset-manager__card">
								<div className="asset-manager__card-top">
									<div className="asset-manager__card-title">
										<div className="asset-manager__badge-row">
											<span className="asset-manager__badge">{holding.symbol}</span>
											<span className="asset-manager__badge asset-manager__badge--muted">
												{formatSecurityMarket(holding.market)}
											</span>
										</div>
										<h3>{holding.name}</h3>
										<p className="asset-manager__card-note">
											更新：{quotePending ? "更新中" : formatTimestamp(holding.last_updated)}
											{shouldShowHoldingSource(holding.broker?.trim())
												? ` · ${holding.broker?.trim()}`
												: ""}
											{holding.note?.trim() ? ` · ${holding.note}` : ""}
										</p>
									</div>
									<div className="asset-manager__card-actions">
										{onEdit ? (
											<button
												type="button"
												className="asset-manager__button asset-manager__button--secondary"
												onClick={() => onEdit(holding)}
												disabled={isActionLocked}
											>
												编辑
											</button>
										) : null}
									</div>
								</div>

								<div className="asset-manager__metric-grid">
									<div className="asset-manager__metric">
										<span>
											{holding.market === "FUND"
												? "份额"
												: holding.market === "CRYPTO"
													? "数量"
													: "数量（股/支）"}
										</span>
										<strong>{formatQuantity(holding.quantity)}</strong>
									</div>
									<div className="asset-manager__metric">
										<span>折算人民币</span>
										<strong>
											{quotePending ? "更新中" : formatCnyAmount(holding.value_cny)}
										</strong>
									</div>
									<div className="asset-manager__metric">
										<span>现价</span>
										<strong>
											{quotePending
												? "更新中"
												: formatPriceAmount(
													holding.price ?? 0,
													holding.price_currency ?? holding.fallback_currency,
												)}
										</strong>
									</div>
									<div className="asset-manager__metric">
										<span>持仓价</span>
										<strong>
											{holding.cost_basis_price != null
												? formatPriceAmount(
													holding.cost_basis_price,
													holding.fallback_currency,
												)
												: "待填写"}
										</strong>
									</div>
									<div className="asset-manager__metric">
										<span>收益率</span>
										<strong>
											{quotePending
												? "更新中"
												: holding.return_pct != null
												? formatPercentValue(holding.return_pct)
												: "待计算"}
										</strong>
									</div>
									<div className="asset-manager__metric">
										<span>计价币种</span>
										<strong>{holding.fallback_currency}</strong>
									</div>
									<div className="asset-manager__metric">
										<span>市场</span>
										<strong>{formatSecurityMarket(holding.market)}</strong>
									</div>
									<div className="asset-manager__metric">
										<span>持仓日</span>
										<strong>{formatDateValue(holding.started_on)}</strong>
									</div>
								</div>
							</li>
						);
					})}
				</ul>
			)}
		</section>
	);
}
