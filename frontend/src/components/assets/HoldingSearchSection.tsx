import type {
	HoldingFormDraft,
	SecuritySearchResult,
} from "../../types/assets";
import { formatSecurityMarket } from "../../lib/assetFormatting";

type HoldingSearchSectionProps = {
	draft: Pick<HoldingFormDraft, "symbol" | "name">;
	searchEnabled: boolean;
	searchQuery: string;
	searchResults: SecuritySearchResult[];
	searchError: string | null;
	isSearching: boolean;
	isSearchOpen: boolean;
	onSearchInputChange: (value: string) => void;
	onFocus: () => void;
	onBlur: () => void;
	onSelect: (result: SecuritySearchResult) => void;
	getSearchLabel: (selection: { name: string; symbol: string }) => string;
	shouldPrefillBroker: (source?: string | null) => boolean;
};

export function HoldingSearchSection({
	draft,
	searchEnabled,
	searchQuery,
	searchResults,
	searchError,
	isSearching,
	isSearchOpen,
	onSearchInputChange,
	onFocus,
	onBlur,
	onSelect,
	getSearchLabel,
	shouldPrefillBroker,
}: HoldingSearchSectionProps) {
	return (
		<>
			<label className="asset-manager__field asset-manager__search-field">
				<span>搜索投资标的</span>
				<div className="asset-manager__search-shell">
					<input
						value={searchQuery}
						onChange={(event) => onSearchInputChange(event.target.value)}
						onFocus={onFocus}
						onBlur={onBlur}
						placeholder="输入名称或代码，例如：寒武纪 / 理想 / BTC"
						autoComplete="off"
					/>

					{isSearching ? (
						<p className="asset-manager__helper-text">正在搜索…</p>
					) : searchEnabled &&
						searchQuery.trim() &&
						!draft.symbol &&
						searchResults.length === 0 &&
						!searchError ? (
						<p className="asset-manager__helper-text">
							没有找到匹配标的，试试代码、拼音或更完整的名称。
						</p>
					) : null}

					{isSearchOpen && searchResults.length > 0 ? (
						<div className="asset-manager__search-list" role="listbox">
							{searchResults.map((result) => (
								<button
									key={`${result.symbol}-${result.exchange ?? "unknown"}-${result.source ?? "unknown"}`}
									type="button"
									className="asset-manager__search-item"
									onMouseDown={(event) => {
										event.preventDefault();
										onSelect(result);
									}}
								>
									<strong>{result.name}</strong>
									<span>{result.symbol}</span>
									<small>
										{formatSecurityMarket(result.market)}
										{result.exchange ? ` · ${result.exchange}` : ""}
										{result.currency ? ` · ${result.currency}` : ""}
										{shouldPrefillBroker(result.source) ? ` · ${result.source}` : ""}
									</small>
								</button>
							))}
						</div>
					) : null}
				</div>
			</label>

			{searchError ? (
				<div className="asset-manager__message asset-manager__message--error">
					{searchError}
				</div>
			) : null}

			{searchEnabled && draft.symbol ? (
				<div className="asset-manager__selection-pill">{getSearchLabel(draft)}</div>
			) : null}
		</>
	);
}
