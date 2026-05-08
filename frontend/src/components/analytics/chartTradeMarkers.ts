import { formatQuantity } from "../../lib/assetFormatting";
import type { HoldingTransactionRecord } from "../../types/assets";
import type {
	TimelinePoint,
	TimelineRange,
} from "../../types/portfolioAnalytics";
import {
	getTimelineDisplayGranularity,
	getTimelineBucketStartTimestampMs,
} from "../../utils/portfolioAnalytics";
import { TREND_CHART_COLORS } from "./chartTheme";

export const TRADE_MARKER_POSITIVE_COLOR = TREND_CHART_COLORS.positiveMarker;
export const TRADE_MARKER_NEGATIVE_COLOR = TREND_CHART_COLORS.negativeMarker;
export const TRADE_MARKER_FILL = "rgba(8, 18, 34, 0.96)";
const SHANGHAI_UTC_OFFSET_MS = 8 * 60 * 60 * 1000;

export type ChartTradeMarkerEvent = {
	id: number;
	side: "BUY" | "SELL";
	description: string;
};

export type ChartTradeMarker = {
	xValue: number;
	yValue: number;
	label: "B" | "S" | "B/S";
	dominantSide: "BUY" | "SELL";
	stroke: string;
	labelColor: string;
	fill: string;
	events: ChartTradeMarkerEvent[];
};

type ChartTradePoint = {
	xValue: number;
	value: number;
};

function toCreatedAtLocalTimeParts(createdAt: string | undefined): {
	hour: number;
	minute: number;
	second: number;
	millisecond: number;
} {
	if (!createdAt) {
		return {
			hour: 12,
			minute: 0,
			second: 0,
			millisecond: 0,
		};
	}

	const parsedTimestamp = Date.parse(createdAt);
	if (!Number.isFinite(parsedTimestamp)) {
		return {
			hour: 12,
			minute: 0,
			second: 0,
			millisecond: 0,
		};
	}

	const shanghaiDate = new Date(parsedTimestamp + SHANGHAI_UTC_OFFSET_MS);
	return {
		hour: shanghaiDate.getUTCHours(),
		minute: shanghaiDate.getUTCMinutes(),
		second: shanghaiDate.getUTCSeconds(),
		millisecond: shanghaiDate.getUTCMilliseconds(),
	};
}

function toTradeEventTimestampMs(transaction: HoldingTransactionRecord): number | null {
	const tradedOn = transaction.traded_on?.trim() ?? "";
	const matchedDate = tradedOn.match(/^(\d{4})-(\d{2})-(\d{2})$/);
	if (!matchedDate) {
		return null;
	}

	const [, yearText, monthText, dayText] = matchedDate;
	const timeParts = toCreatedAtLocalTimeParts(transaction.created_at);
	return Date.UTC(
		Number(yearText),
		Number(monthText) - 1,
		Number(dayText),
		timeParts.hour,
		timeParts.minute,
		timeParts.second,
		timeParts.millisecond,
	) - SHANGHAI_UTC_OFFSET_MS;
}

function compareTransactions(
	left: HoldingTransactionRecord,
	right: HoldingTransactionRecord,
): number {
	const leftTimestamp = toTradeEventTimestampMs(left) ?? 0;
	const rightTimestamp = toTradeEventTimestampMs(right) ?? 0;
	if (leftTimestamp !== rightTimestamp) {
		return leftTimestamp - rightTimestamp;
	}

	const leftCreatedAt = Date.parse(left.created_at ?? "");
	const rightCreatedAt = Date.parse(right.created_at ?? "");
	if (Number.isFinite(leftCreatedAt) && Number.isFinite(rightCreatedAt) && leftCreatedAt !== rightCreatedAt) {
		return leftCreatedAt - rightCreatedAt;
	}

	return left.id - right.id;
}

function buildTradeEventDescription(transaction: HoldingTransactionRecord): string {
	const sideLabel = transaction.side === "BUY" ? "B" : "S";
	const displayName = transaction.name?.trim() || transaction.symbol;
	return `${sideLabel} · ${displayName} (${transaction.symbol}) · ${formatQuantity(transaction.quantity)} 股/份`;
}

export function buildChartTradeMarkers(params: {
	range: TimelineRange;
	series: TimelinePoint[];
	chartPoints: ChartTradePoint[];
	transactions: HoldingTransactionRecord[];
	symbol?: string;
}): ChartTradeMarker[] {
	const {
		range,
		series,
		chartPoints,
		transactions,
		symbol,
	} = params;
	if (series.length === 0 || chartPoints.length === 0 || transactions.length === 0) {
		return [];
	}

	const pointLookup = new Map<number, ChartTradePoint>();
	for (const point of chartPoints) {
		if (Number.isFinite(point.xValue)) {
			pointLookup.set(point.xValue, point);
		}
	}
	if (pointLookup.size === 0) {
		return [];
	}

	const granularity = getTimelineDisplayGranularity(range, series);
	const groupedTransactions = new Map<number, HoldingTransactionRecord[]>();

	for (const transaction of transactions) {
		if (transaction.side !== "BUY" && transaction.side !== "SELL") {
			continue;
		}
		if (symbol && transaction.symbol !== symbol) {
			continue;
		}

		const eventTimestampMs = toTradeEventTimestampMs(transaction);
		if (eventTimestampMs === null) {
			continue;
		}

		const bucketStartMs = getTimelineBucketStartTimestampMs(eventTimestampMs, granularity);
		if (!pointLookup.has(bucketStartMs)) {
			continue;
		}

		const nextTransactions = groupedTransactions.get(bucketStartMs) ?? [];
		nextTransactions.push(transaction);
		groupedTransactions.set(bucketStartMs, nextTransactions);
	}

	return [...groupedTransactions.entries()]
		.map<ChartTradeMarker | null>(([xValue, grouped]) => {
			const orderedTransactions = [...grouped].sort(compareTransactions);
			const buyCount = orderedTransactions.filter((transaction) => transaction.side === "BUY").length;
			const sellCount = orderedTransactions.length - buyCount;
			const hasBuy = buyCount > 0;
			const hasSell = sellCount > 0;
			const latestSide = orderedTransactions[orderedTransactions.length - 1]?.side === "SELL"
				? "SELL"
				: "BUY";
			const dominantSide =
				buyCount === sellCount
					? latestSide
					: buyCount > sellCount
						? "BUY"
						: "SELL";
			const toneColor =
				dominantSide === "BUY"
					? TRADE_MARKER_POSITIVE_COLOR
					: TRADE_MARKER_NEGATIVE_COLOR;
			const point = pointLookup.get(xValue);
			if (!point) {
				return null;
			}

			return {
				xValue,
				yValue: point.value,
				label: hasBuy && hasSell ? "B/S" : hasBuy ? "B" : "S",
				dominantSide,
				stroke: toneColor,
				labelColor: toneColor,
				fill: TRADE_MARKER_FILL,
				events: orderedTransactions.map((transaction) => ({
					id: transaction.id,
					side: transaction.side === "SELL" ? "SELL" : "BUY",
					description: buildTradeEventDescription(transaction),
				})),
			};
		})
		.filter((marker): marker is ChartTradeMarker => marker !== null)
		.sort((left, right) => left.xValue - right.xValue);
}
