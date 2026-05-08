export function clamp(value: number, minValue: number, maxValue: number): number {
	return Math.min(Math.max(value, minValue), maxValue);
}
