import type {
	CashAccountRecord,
	HoldingFormDraft,
} from "../../types/assets";

type SellProceedsOption = {
	value: HoldingFormDraft["sell_proceeds_handling"];
	label: string;
};

type HoldingCashSettlementFieldsProps = {
	draft: HoldingFormDraft;
	isEditIntent: boolean;
	isSellTransaction: boolean;
	shouldMergeIntoExistingCash: boolean;
	canSelectExistingCashAccount: boolean;
	canSelectBuyFundingCashAccount: boolean;
	availableSellProceedsOptions: SellProceedsOption[];
	sellProceedsSelectionValue: HoldingFormDraft["sell_proceeds_handling"];
	cashAccounts: CashAccountRecord[];
	onUpdateDraft: <K extends keyof HoldingFormDraft>(
		key: K,
		value: HoldingFormDraft[K],
	) => void;
	formatCashAccountOptionLabel: (account: CashAccountRecord) => string;
};

export function HoldingCashSettlementFields({
	draft,
	isEditIntent,
	isSellTransaction,
	shouldMergeIntoExistingCash,
	canSelectExistingCashAccount,
	canSelectBuyFundingCashAccount,
	availableSellProceedsOptions,
	sellProceedsSelectionValue,
	cashAccounts,
	onUpdateDraft,
	formatCashAccountOptionLabel,
}: HoldingCashSettlementFieldsProps) {
	return (
		<>
			{isSellTransaction ? (
				<>
					<label className="asset-manager__field">
						<span>卖出回款去向</span>
						<select
							aria-label="卖出回款去向"
							value={sellProceedsSelectionValue}
							onChange={(event) =>
								onUpdateDraft(
									"sell_proceeds_handling",
									event.target.value as HoldingFormDraft["sell_proceeds_handling"],
								)
							}
						>
							{availableSellProceedsOptions.map((option) => (
								<option key={option.value} value={option.value}>
									{option.label}
								</option>
							))}
						</select>
						{!canSelectExistingCashAccount ? (
							<p className="asset-manager__helper-text">
								当前没有现金账户 如需并入现有账户 请先新增现金账户
							</p>
						) : null}
					</label>

					{shouldMergeIntoExistingCash ? (
						<label className="asset-manager__field">
							<span>目标现金账户</span>
							<select
								aria-label="目标现金账户"
								value={draft.sell_proceeds_account_id}
								onChange={(event) =>
									onUpdateDraft("sell_proceeds_account_id", event.target.value)
								}
							>
								<option value="">请选择一个现金账户</option>
								{cashAccounts.map((account) => (
									<option key={account.id} value={String(account.id)}>
										{formatCashAccountOptionLabel(account)}
									</option>
								))}
							</select>
							{!canSelectExistingCashAccount ? (
								<p className="asset-manager__helper-text">
									当前还没有现金账户 请先新增现金账户 或改用自动新建现金账户
								</p>
							) : (
								<p className="asset-manager__helper-text">
									系统会按目标账户币种自动换算并累加余额 同时在备注里记录本次卖出来源
								</p>
							)}
						</label>
					) : null}
				</>
			) : null}

			{!isEditIntent && !isSellTransaction ? (
				<label className="asset-manager__field">
					<span>买入扣款账户</span>
					<select
						value={draft.buy_funding_account_id}
						onChange={(event) =>
							onUpdateDraft("buy_funding_account_id", event.target.value)
						}
					>
						<option value="">无（不从现金账户扣款）</option>
						{cashAccounts.map((account) => (
							<option key={account.id} value={String(account.id)}>
								{formatCashAccountOptionLabel(account)}
							</option>
						))}
					</select>
					{!canSelectBuyFundingCashAccount ? (
						<p className="asset-manager__helper-text">
							当前没有现金账户 如需记录买入扣款 请先新增现金账户
						</p>
					) : null}
				</label>
			) : null}
		</>
	);
}
