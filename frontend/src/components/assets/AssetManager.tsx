import { useEffect, useMemo, useRef, useState } from "react";
import { CashAccountForm } from "./CashAccountForm";
import { CashAccountList } from "./CashAccountList";
import { CashTransferPanel } from "./CashTransferPanel";
import { FixedAssetForm } from "./FixedAssetForm";
import { FixedAssetList } from "./FixedAssetList";
import { HoldingForm } from "./HoldingForm";
import { HoldingList } from "./HoldingList";
import { HoldingTransactionHistory } from "./HoldingTransactionHistory";
import { LiabilityForm } from "./LiabilityForm";
import { LiabilityList } from "./LiabilityList";
import { OtherAssetForm } from "./OtherAssetForm";
import { OtherAssetList } from "./OtherAssetList";
import type { SupportedCurrencyFxRates } from "../../lib/assetCurrency";
import { useAssetCollection } from "../../hooks/useAssetCollection";
import {
	ACTIVE_SECTION_STORAGE_KEY,
	EMPTY_CASH_ACCOUNTS,
	EMPTY_FIXED_ASSETS,
	EMPTY_HOLDINGS,
	EMPTY_HOLDING_TRANSACTIONS,
	EMPTY_LIABILITIES,
	EMPTY_OTHER_ASSETS,
	SECTION_RESOURCES,
	createLocalCashAccount,
	createLocalFixedAsset,
	createLocalHolding,
	createLocalLiability,
	createLocalOtherAsset,
	getLoadedResourcesFromInitialData,
	readInitialSection,
	toCashDraft,
	toFixedAssetDraft,
	toHoldingDraft,
	toLiabilityDraft,
	toOtherAssetDraft,
	updateLocalCashAccount,
	updateLocalFixedAsset,
	updateLocalHolding,
	updateLocalLiability,
	updateLocalOtherAsset,
	type AssetResource,
	type AssetSection,
	type SummarySection,
} from "./AssetManagerModel";
import type {
	AssetManagerController,
	CashAccountInput,
	CashAccountRecord,
	CashTransferInput,
	FixedAssetInput,
	FixedAssetRecord,
	HoldingEditorIntent,
	HoldingInput,
	HoldingRecord,
	HoldingTransactionRecord,
	LiabilityInput,
	LiabilityRecord,
	OtherAssetInput,
	OtherAssetRecord,
} from "../../types/assets";

export interface AssetManagerProps {
	initialCashAccounts?: CashAccountRecord[];
	initialHoldings?: HoldingRecord[];
	initialFixedAssets?: FixedAssetRecord[];
	initialLiabilities?: LiabilityRecord[];
	initialOtherAssets?: OtherAssetRecord[];
	cashActions?: AssetManagerController["cashAccounts"];
	cashTransferActions?: AssetManagerController["cashTransfers"];
	holdingActions?: AssetManagerController["holdings"];
	holdingTransactionActions?: AssetManagerController["holdingTransactions"];
	fixedAssetActions?: AssetManagerController["fixedAssets"];
	liabilityActions?: AssetManagerController["liabilities"];
	otherAssetActions?: AssetManagerController["otherAssets"];
	defaultSection?: AssetSection;
	title?: string;
	description?: string;
	loadOnMount?: boolean;
	maxStartedOnDate?: string;
	displayFxRates?: SupportedCurrencyFxRates;
	onRecordsCommitted?: (sections: AssetSection[]) => void;
}

export function AssetManager({
	initialCashAccounts,
	initialHoldings,
	initialFixedAssets,
	initialLiabilities,
	initialOtherAssets,
	cashActions,
	cashTransferActions,
	holdingActions,
	holdingTransactionActions,
	fixedAssetActions,
	liabilityActions,
	otherAssetActions,
	defaultSection = "cash",
	title = "资产管理",
	description,
	loadOnMount = false,
	maxStartedOnDate,
	displayFxRates,
	onRecordsCommitted,
}: AssetManagerProps) {
	const [activeSection, setActiveSection] = useState<AssetSection>(() =>
		readInitialSection(defaultSection)
	);
	const [holdingEditorIntent, setHoldingEditorIntent] = useState<HoldingEditorIntent>("buy");
	const [loadedResources, setLoadedResources] = useState<Record<AssetResource, boolean>>(() =>
		getLoadedResourcesFromInitialData({
			initialCashAccounts,
			initialHoldings,
			initialFixedAssets,
			initialLiabilities,
			initialOtherAssets,
		})
	);
	const [isCashTransferEditorOpen, setIsCashTransferEditorOpen] = useState(false);
	const [cashTransferError, setCashTransferError] = useState<string | null>(null);
	const [isSubmittingCashTransfer, setIsSubmittingCashTransfer] = useState(false);
	const [holdingTransactions, setHoldingTransactions] = useState<HoldingTransactionRecord[]>(
		EMPTY_HOLDING_TRANSACTIONS,
	);
	const [holdingTransactionsLoading, setHoldingTransactionsLoading] = useState(false);
	const [holdingTransactionsError, setHoldingTransactionsError] = useState<string | null>(null);
	const cashCollection = useAssetCollection({
		initialItems: initialCashAccounts ?? EMPTY_CASH_ACCOUNTS,
		actions: cashActions,
		createLocalRecord: createLocalCashAccount,
		updateLocalRecord: updateLocalCashAccount,
	});
	const holdingCollection = useAssetCollection({
		initialItems: initialHoldings ?? EMPTY_HOLDINGS,
		actions: holdingActions,
		createLocalRecord: createLocalHolding,
		updateLocalRecord: updateLocalHolding,
	});
	const fixedAssetCollection = useAssetCollection({
		initialItems: initialFixedAssets ?? EMPTY_FIXED_ASSETS,
		actions: fixedAssetActions,
		createLocalRecord: createLocalFixedAsset,
		updateLocalRecord: updateLocalFixedAsset,
	});
	const liabilityCollection = useAssetCollection({
		initialItems: initialLiabilities ?? EMPTY_LIABILITIES,
		actions: liabilityActions,
		createLocalRecord: createLocalLiability,
		updateLocalRecord: updateLocalLiability,
	});
	const otherAssetCollection = useAssetCollection({
		initialItems: initialOtherAssets ?? EMPTY_OTHER_ASSETS,
		actions: otherAssetActions,
		createLocalRecord: createLocalOtherAsset,
		updateLocalRecord: updateLocalOtherAsset,
	});
	const holdingCreateSeed = useMemo(
		() => ({
			side: holdingEditorIntent === "sell" ? ("SELL" as const) : ("BUY" as const),
		}),
		[holdingEditorIntent],
	);
	const hasLoadedInitialSectionRef = useRef(false);
	const loadingResourcesRef = useRef<Set<AssetResource>>(new Set());

	useEffect(() => {
		try {
			window.sessionStorage.setItem(ACTIVE_SECTION_STORAGE_KEY, activeSection);
		} catch {
			// Ignore storage errors and keep the in-memory section selection.
		}
	}, [activeSection]);

	useEffect(() => {
		const nextLoadedResources = getLoadedResourcesFromInitialData({
			initialCashAccounts,
			initialHoldings,
			initialFixedAssets,
			initialLiabilities,
			initialOtherAssets,
		});
		setLoadedResources((currentResources) => {
			let didChange = false;
			const mergedResources = { ...currentResources };
			for (const resource of Object.keys(nextLoadedResources) as AssetResource[]) {
				if (!nextLoadedResources[resource] || mergedResources[resource]) {
					continue;
				}
				mergedResources[resource] = true;
				didChange = true;
			}
			return didChange ? mergedResources : currentResources;
		});
	}, [
		initialCashAccounts,
		initialFixedAssets,
		initialHoldings,
		initialLiabilities,
		initialOtherAssets,
	]);

	function openHoldingBuyEditor(): void {
		setHoldingEditorIntent("buy");
		holdingCollection.openCreate();
	}

	function openHoldingSellEditor(): void {
		setHoldingEditorIntent("sell");
		holdingCollection.openCreate();
	}

	function openHoldingEditEditor(record: HoldingRecord): void {
		setHoldingEditorIntent("edit");
		holdingCollection.openEdit(record);
	}

	function closeHoldingEditor(): void {
		holdingCollection.closeEditor();
		setHoldingEditorIntent("buy");
	}

	function openCashCreateEditor(): void {
		setCashTransferError(null);
		setIsCashTransferEditorOpen(false);
		cashCollection.openCreate();
	}

	function openCashEditEditor(record: CashAccountRecord): void {
		setCashTransferError(null);
		setIsCashTransferEditorOpen(false);
		cashCollection.openEdit(record);
	}

	function closeCashEditor(): void {
		cashCollection.closeEditor();
	}

	function openCashTransferEditor(): void {
		cashCollection.closeEditor();
		setCashTransferError(null);
		setIsCashTransferEditorOpen(true);
	}

	function closeCashTransferEditor(): void {
		setCashTransferError(null);
		setIsCashTransferEditorOpen(false);
	}

	function markResourcesLoaded(...resources: AssetResource[]): void {
		setLoadedResources((currentResources) => {
			let didChange = false;
			const nextResources = { ...currentResources };
			for (const resource of resources) {
				if (nextResources[resource]) {
					continue;
				}
				nextResources[resource] = true;
				didChange = true;
			}
			return didChange ? nextResources : currentResources;
		});
	}

	function invalidateResources(...resources: AssetResource[]): void {
		setLoadedResources((currentResources) => {
			let didChange = false;
			const nextResources = { ...currentResources };
			for (const resource of resources) {
				if (!nextResources[resource]) {
					continue;
				}
				nextResources[resource] = false;
				didChange = true;
			}
			return didChange ? nextResources : currentResources;
		});
	}

	function notifyRecordsCommitted(...sections: AssetSection[]): void {
		if (!onRecordsCommitted) {
			return;
		}

		onRecordsCommitted(Array.from(new Set(sections)));
	}

	function hasLoadedSectionResources(section: AssetSection): boolean {
		return SECTION_RESOURCES[section].every((resource) => loadedResources[resource]);
	}

	function startResourceRefresh(resource: AssetResource): boolean {
		if (loadingResourcesRef.current.has(resource)) {
			return false;
		}

		loadingResourcesRef.current.add(resource);
		return true;
	}

	function finishResourceRefresh(resource: AssetResource): void {
		loadingResourcesRef.current.delete(resource);
	}

	async function refreshCashAccounts(): Promise<void> {
		if (!startResourceRefresh("cashAccounts")) {
			return;
		}

		try {
			const refreshed = await cashCollection.refresh();
			if (refreshed) {
				markResourcesLoaded("cashAccounts");
			}
		} finally {
			finishResourceRefresh("cashAccounts");
		}
	}

	async function refreshHoldings(): Promise<void> {
		if (!startResourceRefresh("holdings")) {
			return;
		}

		try {
			const refreshed = await holdingCollection.refresh();
			if (refreshed) {
				markResourcesLoaded("holdings");
			}
		} finally {
			finishResourceRefresh("holdings");
		}
	}

	async function refreshHoldingTransactions(): Promise<void> {
		if (!startResourceRefresh("holdingTransactions")) {
			return;
		}

		if (!holdingTransactionActions?.onRefresh) {
			try {
				setHoldingTransactions(EMPTY_HOLDING_TRANSACTIONS);
				setHoldingTransactionsLoading(false);
				setHoldingTransactionsError(null);
				markResourcesLoaded("holdingTransactions");
				return;
			} finally {
				finishResourceRefresh("holdingTransactions");
			}
		}

		setHoldingTransactionsLoading(true);
		setHoldingTransactionsError(null);
		try {
			const items = await holdingTransactionActions.onRefresh();
			setHoldingTransactions(items);
			markResourcesLoaded("holdingTransactions");
		} catch (error) {
			setHoldingTransactionsError(
				error instanceof Error ? error.message : "加载投资交易记录失败。",
			);
		} finally {
			setHoldingTransactionsLoading(false);
			finishResourceRefresh("holdingTransactions");
		}
	}

	async function refreshFixedAssets(): Promise<void> {
		if (!startResourceRefresh("fixedAssets")) {
			return;
		}

		try {
			const refreshed = await fixedAssetCollection.refresh();
			if (refreshed) {
				markResourcesLoaded("fixedAssets");
			}
		} finally {
			finishResourceRefresh("fixedAssets");
		}
	}

	async function refreshLiabilities(): Promise<void> {
		if (!startResourceRefresh("liabilities")) {
			return;
		}

		try {
			const refreshed = await liabilityCollection.refresh();
			if (refreshed) {
				markResourcesLoaded("liabilities");
			}
		} finally {
			finishResourceRefresh("liabilities");
		}
	}

	async function refreshOtherAssets(): Promise<void> {
		if (!startResourceRefresh("otherAssets")) {
			return;
		}

		try {
			const refreshed = await otherAssetCollection.refresh();
			if (refreshed) {
				markResourcesLoaded("otherAssets");
			}
		} finally {
			finishResourceRefresh("otherAssets");
		}
	}

	async function refreshCashSection(): Promise<void> {
		const pendingRefreshes: Promise<void>[] = [];
		if (!loadedResources.cashAccounts) {
			pendingRefreshes.push(refreshCashAccounts());
		}
		await Promise.all(pendingRefreshes);
	}

	async function refreshInvestmentSection(): Promise<void> {
		const pendingRefreshes: Promise<void>[] = [];
		if (!loadedResources.cashAccounts) {
			pendingRefreshes.push(refreshCashAccounts());
		}
		if (!loadedResources.holdings) {
			pendingRefreshes.push(refreshHoldings());
		}
		if (!loadedResources.holdingTransactions) {
			pendingRefreshes.push(refreshHoldingTransactions());
		}
		await Promise.all(pendingRefreshes);
	}

	async function refreshFixedSection(): Promise<void> {
		if (!loadedResources.fixedAssets) {
			await refreshFixedAssets();
		}
	}

	async function refreshLiabilitySection(): Promise<void> {
		if (!loadedResources.liabilities) {
			await refreshLiabilities();
		}
	}

	async function refreshOtherSection(): Promise<void> {
		if (!loadedResources.otherAssets) {
			await refreshOtherAssets();
		}
	}

	useEffect(() => {
		const isInitialMount = !hasLoadedInitialSectionRef.current;
		if (isInitialMount) {
			hasLoadedInitialSectionRef.current = true;
		}

		if (isInitialMount && !loadOnMount) {
			return;
		}

		if (hasLoadedSectionResources(activeSection)) {
			return;
		}

		switch (activeSection) {
			case "cash":
				void refreshCashSection();
				return;
			case "investment":
				void refreshInvestmentSection();
				return;
			case "fixed":
				void refreshFixedSection();
				return;
			case "liability":
				void refreshLiabilitySection();
				return;
			case "other":
				void refreshOtherSection();
				return;
		}
	}, [activeSection, loadOnMount, loadedResources]);

	async function removeCashRecord(recordId: number): Promise<void> {
		const record = cashCollection.items.find((item) => item.id === recordId);
		if (!record) {
			return;
		}
		const removed = await cashCollection.remove(record);
		if (removed) {
			markResourcesLoaded("cashAccounts");
			notifyRecordsCommitted("cash");
		}
	}

	async function submitCashRecord(payload: CashAccountInput): Promise<void> {
		const saved = await cashCollection.submit(payload);
		if (saved) {
			markResourcesLoaded("cashAccounts");
			notifyRecordsCommitted("cash");
		}
	}

	async function createCashTransferRecord(payload: CashTransferInput): Promise<void> {
		if (!cashTransferActions?.onCreate) {
			throw new Error("当前未配置账户划转能力。");
		}

		setCashTransferError(null);
		setIsSubmittingCashTransfer(true);
		try {
			await cashTransferActions.onCreate(payload);
			await refreshCashAccounts();
			notifyRecordsCommitted("cash");
			closeCashTransferEditor();
		} finally {
			setIsSubmittingCashTransfer(false);
		}
	}

	async function submitCashTransferRecord(payload: CashTransferInput): Promise<void> {
		try {
			await createCashTransferRecord(payload);
		} catch (error) {
			setCashTransferError(
				error instanceof Error ? error.message : "新增账户划转失败，请稍后重试。",
			);
		}
	}

	async function submitHoldingRecord(payload: HoldingInput): Promise<void> {
		const isHoldingMetadataEdit = holdingCollection.editingRecordId !== null;
		const saved = await holdingCollection.submit(payload);
		if (!saved) {
			return;
		}

		if (isHoldingMetadataEdit) {
			await Promise.all([refreshHoldings(), refreshHoldingTransactions()]);
			notifyRecordsCommitted("investment");
			return;
		}

		const touchesCashAccounts =
			payload.side === "SELL" || payload.buy_funding_account_id !== undefined;
		await Promise.all([
			refreshHoldings(),
			refreshHoldingTransactions(),
			touchesCashAccounts ? refreshCashAccounts() : Promise.resolve(),
		]);
		if (touchesCashAccounts) {
			invalidateResources("cashTransfers", "cashLedger");
			notifyRecordsCommitted("cash", "investment");
			return;
		}

		notifyRecordsCommitted("investment");
	}

	async function removeHoldingRecord(recordId: number): Promise<void> {
		const record = holdingCollection.items.find((item) => item.id === recordId);
		if (!record) {
			return;
		}
		const removed = await holdingCollection.remove(record);
		if (removed) {
			await Promise.all([
				refreshCashAccounts(),
				refreshHoldings(),
				refreshHoldingTransactions(),
			]);
			invalidateResources("cashTransfers", "cashLedger");
			notifyRecordsCommitted("cash", "investment");
		}
	}

	async function submitFixedAssetRecord(payload: FixedAssetInput): Promise<void> {
		const saved = await fixedAssetCollection.submit(payload);
		if (saved) {
			markResourcesLoaded("fixedAssets");
			notifyRecordsCommitted("fixed");
		}
	}

	async function removeFixedAssetRecord(recordId: number): Promise<void> {
		const record = fixedAssetCollection.items.find((item) => item.id === recordId);
		if (!record) {
			return;
		}
		const removed = await fixedAssetCollection.remove(record);
		if (removed) {
			markResourcesLoaded("fixedAssets");
			notifyRecordsCommitted("fixed");
		}
	}

	async function submitLiabilityRecord(payload: LiabilityInput): Promise<void> {
		const saved = await liabilityCollection.submit(payload);
		if (saved) {
			markResourcesLoaded("liabilities");
			notifyRecordsCommitted("liability");
		}
	}

	async function removeLiabilityRecord(recordId: number): Promise<void> {
		const record = liabilityCollection.items.find((item) => item.id === recordId);
		if (!record) {
			return;
		}
		const removed = await liabilityCollection.remove(record);
		if (removed) {
			markResourcesLoaded("liabilities");
			notifyRecordsCommitted("liability");
		}
	}

	async function submitOtherAssetRecord(payload: OtherAssetInput): Promise<void> {
		const saved = await otherAssetCollection.submit(payload);
		if (saved) {
			markResourcesLoaded("otherAssets");
			notifyRecordsCommitted("other");
		}
	}

	async function removeOtherAssetRecord(recordId: number): Promise<void> {
		const record = otherAssetCollection.items.find((item) => item.id === recordId);
		if (!record) {
			return;
		}
		const removed = await otherAssetCollection.remove(record);
		if (removed) {
			markResourcesLoaded("otherAssets");
			notifyRecordsCommitted("other");
		}
	}

	function summaryCountClass(section: AssetSection): string {
		return `asset-manager__summary-card asset-manager__summary-card--${section}`;
	}

	function getSummaryCount(count: number, isLoaded: boolean): number | string {
		return isLoaded ? count : "—";
	}

	const summarySections: SummarySection[] = [
		{
			key: "cash",
			label: "现金",
			count: getSummaryCount(cashCollection.items.length, loadedResources.cashAccounts),
		},
		{
			key: "investment",
			label: "投资类",
			count: getSummaryCount(holdingCollection.items.length, loadedResources.holdings),
		},
		{
			key: "fixed",
			label: "固定资产",
			count: getSummaryCount(fixedAssetCollection.items.length, loadedResources.fixedAssets),
		},
		{
			key: "liability",
			label: "负债",
			count: getSummaryCount(liabilityCollection.items.length, loadedResources.liabilities),
		},
		{
			key: "other",
			label: "其他",
			count: getSummaryCount(otherAssetCollection.items.length, loadedResources.otherAssets),
		},
	];
	const isCashEditorVisible = isCashTransferEditorOpen || cashCollection.isEditorOpen;
	const isFixedAssetEditorVisible = fixedAssetCollection.isEditorOpen;
	const isLiabilityEditorVisible = liabilityCollection.isEditorOpen;
	const isOtherAssetEditorVisible = otherAssetCollection.isEditorOpen;

	return (
		<section className="asset-manager">
			<header className="asset-manager__header">
				<div>
					<p className="asset-manager__eyebrow">ASSET MODULES</p>
					<h2>{title}</h2>
					{description ? <p>{description}</p> : null}
				</div>
				<div className="asset-manager__summary" role="tablist" aria-label="资产类型切换">
					{summarySections.map((section) => (
						<button
							key={section.key}
							type="button"
							role="tab"
							aria-selected={activeSection === section.key}
							className={`${summaryCountClass(section.key)} ${activeSection === section.key ? "is-active" : ""}`}
							onClick={() => setActiveSection(section.key)}
						>
							<span>{section.label}</span>
							<strong>{section.count}</strong>
						</button>
					))}
				</div>
			</header>

			<div className="asset-manager__workspace">
				{activeSection === "cash" ? (
					<>
						{isCashTransferEditorOpen ? (
							<CashTransferPanel
								accounts={cashCollection.items}
								busy={isSubmittingCashTransfer}
								errorMessage={cashTransferError}
								maxStartedOnDate={maxStartedOnDate}
								fxRates={displayFxRates}
								onCreate={(payload) => submitCashTransferRecord(payload)}
								onCancel={closeCashTransferEditor}
							/>
						) : null}
						{cashCollection.isEditorOpen ? (
							<CashAccountForm
								mode={cashCollection.editorMode ?? "create"}
								resetKey={cashCollection.editorSessionKey}
								value={
									cashCollection.editorSeedRecord
										? toCashDraft(cashCollection.editorSeedRecord)
										: null
								}
								activityAccount={cashCollection.editingRecord}
								fxRates={displayFxRates}
								recordId={cashCollection.editingRecordId}
								busy={cashCollection.isSubmitting}
								errorMessage={cashCollection.errorMessage}
								onCreate={(payload) => submitCashRecord(payload)}
								onEdit={(_recordId, payload) => submitCashRecord(payload)}
								onDelete={(recordId) => removeCashRecord(recordId)}
								onCancel={closeCashEditor}
							/>
						) : null}
						{isCashEditorVisible ? null : (
							<CashAccountList
								accounts={cashCollection.items}
								loading={cashCollection.isRefreshing}
								busy={cashCollection.isSubmitting}
								errorMessage={cashCollection.errorMessage}
								onCreate={openCashCreateEditor}
								onTransfer={openCashTransferEditor}
								onEdit={(account) => openCashEditEditor(account)}
								onDelete={(recordId) => removeCashRecord(recordId)}
							/>
						)}
					</>
				) : null}

				{activeSection === "investment" ? (
					<>
						{holdingCollection.isEditorOpen ? (
							<HoldingForm
								mode={holdingCollection.editorMode ?? "create"}
								resetKey={holdingCollection.editorSessionKey}
								intent={holdingCollection.editorMode === "edit" ? "edit" : holdingEditorIntent}
								value={
									holdingCollection.editorSeedRecord
										? toHoldingDraft(holdingCollection.editorSeedRecord)
										: holdingCreateSeed
								}
								existingHoldings={holdingCollection.items}
								cashAccounts={cashCollection.items}
								recordId={holdingCollection.editingRecordId}
								busy={holdingCollection.isSubmitting}
								errorMessage={holdingCollection.errorMessage}
								maxStartedOnDate={maxStartedOnDate}
								fxRates={displayFxRates}
								onCreate={(payload) => submitHoldingRecord(payload)}
								onEdit={(_recordId, payload) => submitHoldingRecord(payload)}
								onDelete={(recordId) => removeHoldingRecord(recordId)}
								onSearch={holdingActions?.onSearch}
								onMergeDuplicate={holdingActions?.onMergeDuplicate}
								onCancel={closeHoldingEditor}
							/>
						) : (
							<>
								<HoldingList
									holdings={holdingCollection.items}
									loading={holdingCollection.isRefreshing}
									busy={holdingCollection.isSubmitting}
									errorMessage={holdingCollection.errorMessage}
									onCreateBuy={openHoldingBuyEditor}
									onCreateSell={
										holdingCollection.items.length === 0
											? undefined
											: openHoldingSellEditor
									}
									onEdit={(holding) => openHoldingEditEditor(holding)}
								/>
								<HoldingTransactionHistory
									transactions={holdingTransactions}
									loading={holdingTransactionsLoading}
									errorMessage={holdingTransactionsError}
								/>
							</>
						)}
					</>
				) : null}

				{activeSection === "fixed" ? (
					<>
						{fixedAssetCollection.isEditorOpen ? (
							<FixedAssetForm
								mode={fixedAssetCollection.editorMode ?? "create"}
								resetKey={fixedAssetCollection.editorSessionKey}
								value={
									fixedAssetCollection.editorSeedRecord
										? toFixedAssetDraft(fixedAssetCollection.editorSeedRecord)
										: null
								}
								recordId={fixedAssetCollection.editingRecordId}
								busy={fixedAssetCollection.isSubmitting}
								errorMessage={fixedAssetCollection.errorMessage}
								onCreate={(payload) => submitFixedAssetRecord(payload)}
								onEdit={(_recordId, payload) => submitFixedAssetRecord(payload)}
								onDelete={(recordId) => removeFixedAssetRecord(recordId)}
								onCancel={fixedAssetCollection.closeEditor}
							/>
						) : null}
						{isFixedAssetEditorVisible ? null : (
							<FixedAssetList
								assets={fixedAssetCollection.items}
								loading={fixedAssetCollection.isRefreshing}
								busy={fixedAssetCollection.isSubmitting}
								errorMessage={fixedAssetCollection.errorMessage}
								onCreate={fixedAssetCollection.openCreate}
								onEdit={(asset) => fixedAssetCollection.openEdit(asset)}
								onDelete={(recordId) => removeFixedAssetRecord(recordId)}
							/>
						)}
					</>
				) : null}

				{activeSection === "liability" ? (
					<>
						{liabilityCollection.isEditorOpen ? (
							<LiabilityForm
								mode={liabilityCollection.editorMode ?? "create"}
								resetKey={liabilityCollection.editorSessionKey}
								value={
									liabilityCollection.editorSeedRecord
										? toLiabilityDraft(liabilityCollection.editorSeedRecord)
										: null
								}
								recordId={liabilityCollection.editingRecordId}
								busy={liabilityCollection.isSubmitting}
								errorMessage={liabilityCollection.errorMessage}
								fxRates={displayFxRates}
								fxToCny={liabilityCollection.editingRecord?.fx_to_cny ?? null}
								onCreate={(payload) => submitLiabilityRecord(payload)}
								onEdit={(_recordId, payload) => submitLiabilityRecord(payload)}
								onDelete={(recordId) => removeLiabilityRecord(recordId)}
								onCancel={liabilityCollection.closeEditor}
							/>
						) : null}
						{isLiabilityEditorVisible ? null : (
							<LiabilityList
								liabilities={liabilityCollection.items}
								loading={liabilityCollection.isRefreshing}
								busy={liabilityCollection.isSubmitting}
								errorMessage={liabilityCollection.errorMessage}
								onCreate={liabilityCollection.openCreate}
								onEdit={(entry) => liabilityCollection.openEdit(entry)}
								onDelete={(recordId) => removeLiabilityRecord(recordId)}
							/>
						)}
					</>
				) : null}

				{activeSection === "other" ? (
					<>
						{otherAssetCollection.isEditorOpen ? (
							<OtherAssetForm
								mode={otherAssetCollection.editorMode ?? "create"}
								resetKey={otherAssetCollection.editorSessionKey}
								value={
									otherAssetCollection.editorSeedRecord
										? toOtherAssetDraft(otherAssetCollection.editorSeedRecord)
										: null
								}
								recordId={otherAssetCollection.editingRecordId}
								busy={otherAssetCollection.isSubmitting}
								errorMessage={otherAssetCollection.errorMessage}
								onCreate={(payload) => submitOtherAssetRecord(payload)}
								onEdit={(_recordId, payload) => submitOtherAssetRecord(payload)}
								onDelete={(recordId) => removeOtherAssetRecord(recordId)}
								onCancel={otherAssetCollection.closeEditor}
							/>
						) : null}
						{isOtherAssetEditorVisible ? null : (
							<OtherAssetList
								assets={otherAssetCollection.items}
								loading={otherAssetCollection.isRefreshing}
								busy={otherAssetCollection.isSubmitting}
								errorMessage={otherAssetCollection.errorMessage}
								onCreate={otherAssetCollection.openCreate}
								onEdit={(asset) => otherAssetCollection.openEdit(asset)}
								onDelete={(recordId) => removeOtherAssetRecord(recordId)}
							/>
						)}
					</>
				) : null}
			</div>
		</section>
	);
}
