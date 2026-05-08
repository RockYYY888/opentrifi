import type { TimelinePoint, TimelineRange } from "../../types/portfolioAnalytics";
import { formatTimelinePointLabel } from "./formatters";

const SHANGHAI_TIME_ZONE = "Asia/Shanghai";
const SHANGHAI_UTC_OFFSET_MS = 8 * 60 * 60 * 1000;

export type TimelineBucketGranularity =
	| "second"
	| "minute"
	| "hour"
	| "day"
	| "month"
	| "year";

export function getTimelineSeries(
	range: TimelineRange,
	secondSeries: TimelinePoint[],
	minuteSeries: TimelinePoint[],
	hourSeries: TimelinePoint[],
	daySeries: TimelinePoint[],
	monthSeries: TimelinePoint[],
	yearSeries: TimelinePoint[],
): TimelinePoint[] {
	if (range === "second") {
		return secondSeries;
	}
	if (range === "minute") {
		return minuteSeries;
	}
	if (range === "hour") {
		return hourSeries;
	}
	if (range === "month") {
		return monthSeries;
	}
	if (range === "year") {
		return yearSeries;
	}
	return daySeries;
}

export function isSyntheticTimelinePoint(
	point: Pick<TimelinePoint, "synthetic"> | null | undefined,
): boolean {
	return point?.synthetic === true;
}

function toSortableTimestamp(point: TimelinePoint, fallbackIndex: number): number {
	if (!point.timestamp_utc) {
		return fallbackIndex;
	}

	const parsedTimestamp = Date.parse(point.timestamp_utc);
	if (!Number.isFinite(parsedTimestamp)) {
		return fallbackIndex;
	}

	return parsedTimestamp;
}

function toTimestampMs(point: TimelinePoint): number | null {
	if (!point.timestamp_utc) {
		return null;
	}

	const parsedTimestamp = Date.parse(point.timestamp_utc);
	return Number.isFinite(parsedTimestamp) ? parsedTimestamp : null;
}

function formatDisplayDatePart(
	timestampMs: number,
	options: Intl.DateTimeFormatOptions,
): string {
	return new Intl.DateTimeFormat("zh-CN", {
		timeZone: SHANGHAI_TIME_ZONE,
		...options,
	}).format(new Date(timestampMs));
}

function formatTimelineBucketLabel(
	timestampMs: number,
	granularity: TimelineBucketGranularity,
): string {
	if (granularity === "second") {
		return formatDisplayDatePart(timestampMs, {
			month: "2-digit",
			day: "2-digit",
			hour: "2-digit",
			minute: "2-digit",
			second: "2-digit",
			hour12: false,
		}).replace("/", "-");
	}

	if (granularity === "minute" || granularity === "hour") {
		return formatDisplayDatePart(timestampMs, {
			month: "2-digit",
			day: "2-digit",
			hour: "2-digit",
			minute: "2-digit",
			hour12: false,
		}).replace("/", "-");
	}

	if (granularity === "month") {
		return formatDisplayDatePart(timestampMs, {
			year: "numeric",
			month: "2-digit",
		}).replace("/", "-");
	}

	if (granularity === "year") {
		return formatDisplayDatePart(timestampMs, {
			year: "numeric",
		});
	}

	return formatDisplayDatePart(timestampMs, {
		month: "2-digit",
		day: "2-digit",
	}).replace("/", "-");
}

function mergeTimelineSeries(...seriesGroups: TimelinePoint[][]): TimelinePoint[] {
	const mergedLookup = new Map<string, TimelinePoint>();

	for (const point of seriesGroups.flat()) {
		const timestampMs = toTimestampMs(point);
		const pointKey =
			timestampMs === null
				? `${point.label}:${point.value}:${mergedLookup.size}`
				: `${timestampMs}`;
		mergedLookup.set(pointKey, point);
	}

	return prepareTimelineSeries([...mergedLookup.values()]);
}

function toShanghaiShiftedDate(timestampMs: number): Date {
	return new Date(timestampMs + SHANGHAI_UTC_OFFSET_MS);
}

function fromShanghaiShiftedDate(date: Date): number {
	return date.getTime() - SHANGHAI_UTC_OFFSET_MS;
}

function bucketStartTimestampMs(
	timestampMs: number,
	granularity: TimelineBucketGranularity,
): number {
	const localDate = toShanghaiShiftedDate(timestampMs);

	if (granularity === "second") {
		localDate.setUTCMilliseconds(0);
		return fromShanghaiShiftedDate(localDate);
	}

	if (granularity === "minute") {
		localDate.setUTCSeconds(0, 0);
		return fromShanghaiShiftedDate(localDate);
	}

	if (granularity === "hour") {
		localDate.setUTCMinutes(0, 0, 0);
		return fromShanghaiShiftedDate(localDate);
	}

	if (granularity === "day") {
		localDate.setUTCHours(0, 0, 0, 0);
		return fromShanghaiShiftedDate(localDate);
	}

	if (granularity === "month") {
		localDate.setUTCDate(1);
		localDate.setUTCHours(0, 0, 0, 0);
		return fromShanghaiShiftedDate(localDate);
	}

	localDate.setUTCMonth(0, 1);
	localDate.setUTCHours(0, 0, 0, 0);
	return fromShanghaiShiftedDate(localDate);
}

export function getTimelineBucketStartTimestampMs(
	timestampMs: number,
	granularity: TimelineBucketGranularity,
): number {
	return bucketStartTimestampMs(timestampMs, granularity);
}

function addBucketSteps(
	timestampMs: number,
	granularity: TimelineBucketGranularity,
	stepCount: number,
): number {
	const localDate = toShanghaiShiftedDate(timestampMs);

	if (granularity === "second") {
		localDate.setUTCSeconds(localDate.getUTCSeconds() + stepCount);
		return fromShanghaiShiftedDate(localDate);
	}

	if (granularity === "minute") {
		localDate.setUTCMinutes(localDate.getUTCMinutes() + stepCount);
		return fromShanghaiShiftedDate(localDate);
	}

	if (granularity === "hour") {
		localDate.setUTCHours(localDate.getUTCHours() + stepCount);
		return fromShanghaiShiftedDate(localDate);
	}

	if (granularity === "day") {
		localDate.setUTCDate(localDate.getUTCDate() + stepCount);
		return fromShanghaiShiftedDate(localDate);
	}

	if (granularity === "month") {
		localDate.setUTCMonth(localDate.getUTCMonth() + stepCount);
		return fromShanghaiShiftedDate(localDate);
	}

	localDate.setUTCFullYear(localDate.getUTCFullYear() + stepCount);
	return fromShanghaiShiftedDate(localDate);
}

function buildTimelinePointAtBucket(
	point: TimelinePoint,
	bucketStartMs: number,
	granularity: TimelineBucketGranularity,
	synthetic: boolean,
): TimelinePoint {
	return {
		...point,
		label: formatTimelineBucketLabel(bucketStartMs, granularity),
		timestamp_utc: new Date(bucketStartMs).toISOString(),
		synthetic,
	};
}

function buildRegularizedWindowedTimelineSeries(
	series: TimelinePoint[],
	granularity: TimelineBucketGranularity,
	lookbackBucketSteps: number,
): TimelinePoint[] {
	const preparedSeries = prepareTimelineSeries(series);
	if (preparedSeries.length < 2) {
		return preparedSeries;
	}

	const timestampedSeries = preparedSeries.map((point) => ({
		point,
		timestampMs: toTimestampMs(point),
	}));
	if (timestampedSeries.some((entry) => entry.timestampMs === null)) {
		return preparedSeries;
	}

	const bucketLookup = new Map<number, TimelinePoint>();
	const sortedBucketStarts: number[] = [];

	for (const entry of timestampedSeries) {
		const bucketStartMs = bucketStartTimestampMs(entry.timestampMs ?? 0, granularity);
		if (!bucketLookup.has(bucketStartMs)) {
			sortedBucketStarts.push(bucketStartMs);
		}
		bucketLookup.set(
			bucketStartMs,
			buildTimelinePointAtBucket(entry.point, bucketStartMs, granularity, false),
		);
	}

	sortedBucketStarts.sort((left, right) => left - right);
	const latestBucketStartMs = sortedBucketStarts[sortedBucketStarts.length - 1] ?? null;
	if (latestBucketStartMs === null) {
		return preparedSeries;
	}

	const desiredStartBucketMs = addBucketSteps(
		latestBucketStartMs,
		granularity,
		-Math.max(1, lookbackBucketSteps),
	);
	let lastKnownPoint: TimelinePoint | null = null;
	let resolvedStartBucketMs: number | null = null;

	for (const bucketStartMs of sortedBucketStarts) {
		const bucketPoint = bucketLookup.get(bucketStartMs) ?? null;
		if (bucketStartMs < desiredStartBucketMs) {
			lastKnownPoint = bucketPoint;
			continue;
		}

		if (resolvedStartBucketMs === null) {
			resolvedStartBucketMs = lastKnownPoint ? desiredStartBucketMs : bucketStartMs;
		}
		break;
	}

	if (resolvedStartBucketMs === null) {
		return lastKnownPoint
			? [
					buildTimelinePointAtBucket(
						lastKnownPoint,
						desiredStartBucketMs,
						granularity,
						true,
					),
				]
			: preparedSeries;
	}

	const regularizedSeries: TimelinePoint[] = [];
	for (
		let bucketStartMs = resolvedStartBucketMs;
		bucketStartMs <= latestBucketStartMs;
		bucketStartMs = addBucketSteps(bucketStartMs, granularity, 1)
	) {
		const bucketPoint = bucketLookup.get(bucketStartMs) ?? null;
		if (bucketPoint) {
			regularizedSeries.push(bucketPoint);
			lastKnownPoint = bucketPoint;
			continue;
		}

		if (lastKnownPoint) {
			regularizedSeries.push(
				buildTimelinePointAtBucket(
					lastKnownPoint,
					bucketStartMs,
					granularity,
					true,
				),
			);
		}
	}

	return regularizedSeries;
}

function trimLeadingInactivePoints(series: TimelinePoint[]): TimelinePoint[] {
	if (series.length <= 2) {
		return series;
	}

	const firstActiveIndex = series.findIndex((point) => Math.abs(point.value) > 1e-6);
	if (firstActiveIndex <= 0) {
		return series;
	}

	const leadingPoints = series.slice(0, firstActiveIndex);
	const areLeadingPointsInactive = leadingPoints.every((point) => Math.abs(point.value) <= 1e-6);
	if (!areLeadingPointsInactive) {
		return series;
	}

	return series.slice(firstActiveIndex);
}

function trimLeadingDiscontinuityPoints(series: TimelinePoint[]): TimelinePoint[] {
	if (series.length <= 2) {
		return series;
	}

	for (let index = 1; index < series.length; index += 1) {
		const previousMagnitude = Math.max(Math.abs(series[index - 1].value), 1e-6);
		const currentMagnitude = Math.abs(series[index].value);
		if (currentMagnitude < 10_000) {
			continue;
		}

		const jumpRatio = currentMagnitude / previousMagnitude;
		if (jumpRatio < 20) {
			continue;
		}

		const leadingPoints = series.slice(0, index);
		if (leadingPoints.length === 0 || series.length - index < 2) {
			continue;
		}

		const lowValueThreshold = Math.max(currentMagnitude * 0.05, 1_000);
		const areLeadingPointsLowValue = leadingPoints.every(
			(point) => Math.abs(point.value) < lowValueThreshold,
		);
		if (areLeadingPointsLowValue) {
			return series.slice(index);
		}
	}

	return series;
}

export function prepareTimelineSeries(series: TimelinePoint[]): TimelinePoint[] {
	const normalizedPoints = series
		.filter((point) => Number.isFinite(point.value))
		.map((point) => ({ ...point }));
	const indexedPoints = normalizedPoints.map((point, index) => ({ point, index }));
	indexedPoints.sort(
		(left, right) =>
			toSortableTimestamp(left.point, left.index) -
			toSortableTimestamp(right.point, right.index),
	);

	const chronologicallySorted = indexedPoints.map((entry) => entry.point);
	return trimLeadingDiscontinuityPoints(trimLeadingInactivePoints(chronologicallySorted));
}

export type PreparedTimelineSeriesByRange = Record<TimelineRange, TimelinePoint[]>;

export function buildPreparedTimelineSeriesByRange(
	secondOrHourSeries: TimelinePoint[],
	minuteOrDaySeries: TimelinePoint[],
	hourOrMonthSeries: TimelinePoint[],
	dayOrYearSeries: TimelinePoint[],
	monthSeries?: TimelinePoint[],
	yearSeries?: TimelinePoint[],
): PreparedTimelineSeriesByRange {
	const secondSeries = yearSeries === undefined ? [] : secondOrHourSeries;
	const minuteSeries = yearSeries === undefined ? secondOrHourSeries : minuteOrDaySeries;
	const hourSeries = yearSeries === undefined ? secondOrHourSeries : hourOrMonthSeries;
	const daySeries = yearSeries === undefined ? minuteOrDaySeries : dayOrYearSeries;
	const resolvedMonthSeries = yearSeries === undefined ? hourOrMonthSeries : (monthSeries ?? []);
	const resolvedYearSeries = yearSeries === undefined ? dayOrYearSeries : yearSeries;

	return {
		second: prepareTimelineSeries(secondSeries),
		minute: prepareTimelineSeries(minuteSeries),
		hour: prepareTimelineSeries(hourSeries),
		day: prepareTimelineSeries(daySeries),
		month: prepareTimelineSeries(resolvedMonthSeries),
		year: prepareTimelineSeries(resolvedYearSeries),
	};
}

export function buildDisplayTimelineSeriesByRange(
	secondSeries: TimelinePoint[],
	minuteSeries: TimelinePoint[],
	hourSeries: TimelinePoint[],
	daySeries: TimelinePoint[],
	monthSeries: TimelinePoint[],
	yearSeries: TimelinePoint[],
): PreparedTimelineSeriesByRange {
	const preparedDaySeries = prepareTimelineSeries(daySeries);
	const preparedMonthSeries = prepareTimelineSeries(monthSeries);
	const preparedYearSeries = prepareTimelineSeries(yearSeries);
	const yearUsesMonthlyBuckets = preparedMonthSeries.length >= 2;
	const yearSourceSeries = yearUsesMonthlyBuckets
		? preparedMonthSeries
		: preparedYearSeries;

	return {
		second: buildRegularizedWindowedTimelineSeries(
			prepareTimelineSeries(secondSeries),
			"second",
			60,
		),
		minute: buildRegularizedWindowedTimelineSeries(
			prepareTimelineSeries(minuteSeries),
			"minute",
			60,
		),
		hour: buildRegularizedWindowedTimelineSeries(
			mergeTimelineSeries(daySeries, hourSeries),
			"hour",
			24,
		),
		day: buildRegularizedWindowedTimelineSeries(preparedDaySeries, "day", 7),
		month: buildRegularizedWindowedTimelineSeries(preparedDaySeries, "day", 30),
		year: buildRegularizedWindowedTimelineSeries(
			yearSourceSeries,
			yearUsesMonthlyBuckets ? "month" : "year",
			yearUsesMonthlyBuckets ? 12 : 1,
		),
	};
}

export function getTimelineDisplayGranularity(
	range: TimelineRange,
	series: TimelinePoint[],
): TimelineBucketGranularity {
	if (range === "second" || range === "minute" || range === "hour") {
		return range;
	}

	if (range === "day" || range === "month") {
		return "day";
	}

	const timestampValues = series
		.map((point) => toTimestampMs(point))
		.filter((value): value is number => value !== null)
		.sort((left, right) => left - right);
	if (timestampValues.length >= 2) {
		const uniqueValues = [...new Set(timestampValues)];
		const firstGap = uniqueValues[1] - uniqueValues[0];
		return firstGap < 300 * 24 * 60 * 60 * 1000 ? "month" : "year";
	}

	return series.some((point) => point.label.includes("-")) ? "month" : "year";
}

export function getFirstRenderableTimelineRange(
	seriesByRange: PreparedTimelineSeriesByRange,
): TimelineRange | null {
	for (const range of ["hour", "day", "month", "year", "minute", "second"] satisfies TimelineRange[]) {
		if (seriesByRange[range].length >= 2) {
			return range;
		}
	}

	return null;
}

export type TimelineSelectablePoint = {
	key: string;
	label: string;
	point: TimelinePoint;
	index: number;
};

export function buildSelectableTimelinePoints(
	series: TimelinePoint[],
): TimelineSelectablePoint[] {
	return series.reduce<TimelineSelectablePoint[]>((selectablePoints, point, index) => {
		if (!Number.isFinite(point.value) || isSyntheticTimelinePoint(point)) {
			return selectablePoints;
		}

		const timestampKey = point.timestamp_utc?.trim();
		selectablePoints.push({
			key: timestampKey ? `${timestampKey}::${index}` : `${point.label}::${index}`,
			label: formatTimelinePointLabel(point, `时间点 ${index + 1}`),
			point,
			index,
		});
		return selectablePoints;
	}, []);
}

export function getFirstSelectableTimelineRange(
	seriesByRange: PreparedTimelineSeriesByRange,
): TimelineRange | null {
	for (const range of ["hour", "day", "month", "year", "minute", "second"] satisfies TimelineRange[]) {
		if (buildSelectableTimelinePoints(seriesByRange[range]).length >= 2) {
			return range;
		}
	}

	return null;
}
