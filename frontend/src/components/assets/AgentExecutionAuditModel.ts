import { ASSET_CLASS_BADGE_LABELS } from "../../lib/assetRecordMeta";
import type {
	AgentApiKeyRecord,
	AssetRecordAssetClass,
} from "../../types/assets";

export const MAX_ACTIVE_API_KEYS = 5;
export const MAX_DAILY_API_KEY_CREATIONS = 10;

const API_KEY_EXPIRY_WARNING_WINDOW_MS = 3 * 24 * 60 * 60 * 1000;
const API_KEY_HINT_PREFIX = "sk-";
const API_KEY_HINT_VISIBLE_CHARS = 2;
const API_KEY_HINT_MASK = "*".repeat(11);
const GENERIC_API_KEY_HINT_PLACEHOLDER = `${API_KEY_HINT_PREFIX}xx${API_KEY_HINT_MASK}`;

export const API_KEY_NAME_PATTERN = /^[a-z]+(?:-[a-z]+)*$/;

export const ASSET_CLASS_FILTERS: Array<{
	value: "ALL" | AssetRecordAssetClass;
	label: string;
}> = [
	{ value: "ALL", label: "全部类别" },
	{ value: "cash", label: ASSET_CLASS_BADGE_LABELS.cash },
	{ value: "investment", label: ASSET_CLASS_BADGE_LABELS.investment },
	{ value: "fixed", label: ASSET_CLASS_BADGE_LABELS.fixed },
	{ value: "liability", label: ASSET_CLASS_BADGE_LABELS.liability },
	{ value: "other", label: ASSET_CLASS_BADGE_LABELS.other },
];

export const EXPIRY_OPTIONS: Array<{
	value: string;
	label: string;
	description: string;
}> = [
	{ value: "7", label: "7 天", description: "适合短期调试或临时自动化。" },
	{ value: "30", label: "30 天", description: "适合日常本地开发或轻量服务。" },
	{ value: "365", label: "365 天", description: "适合长期稳定的生产接入。" },
	{ value: "never", label: "不过期", description: "仅建议在你有轮换机制时使用。" },
];

export type ActivitySourceFilter = "ALL" | "AGENT" | "API";
export type ActivityAssetClassFilter = "ALL" | AssetRecordAssetClass;

export function isApiKeyActive(apiKey: AgentApiKeyRecord): boolean {
	if (apiKey.revoked_at) {
		return false;
	}
	if (!apiKey.expires_at) {
		return true;
	}
	const expiresAt = Date.parse(apiKey.expires_at);
	return Number.isFinite(expiresAt) && expiresAt > Date.now();
}

export function isApiKeyExpiringSoon(apiKey: AgentApiKeyRecord): boolean {
	if (!apiKey.expires_at || apiKey.revoked_at) {
		return false;
	}
	const expiresAt = Date.parse(apiKey.expires_at);
	if (!Number.isFinite(expiresAt)) {
		return false;
	}
	const remainingMs = expiresAt - Date.now();
	return remainingMs > 0 && remainingMs <= API_KEY_EXPIRY_WARNING_WINDOW_MS;
}

export function getVisibleTokenHint(tokenHint: string): string {
	const normalized = tokenHint.trim();
	if (!normalized.startsWith(API_KEY_HINT_PREFIX)) {
		return GENERIC_API_KEY_HINT_PLACEHOLDER;
	}

	const visibleFragment = normalized
		.slice(API_KEY_HINT_PREFIX.length)
		.replace(/\*/g, "")
		.slice(0, API_KEY_HINT_VISIBLE_CHARS)
		.padEnd(API_KEY_HINT_VISIBLE_CHARS, "x");
	return `${API_KEY_HINT_PREFIX}${visibleFragment}${API_KEY_HINT_MASK}`;
}

export function getApiKeyStatus(apiKey: AgentApiKeyRecord): {
	label: string;
	className: string;
} {
	if (apiKey.revoked_at) {
		return {
			label: "已删除",
			className: "asset-manager__badge asset-manager__badge--muted",
		};
	}
	if (!isApiKeyActive(apiKey)) {
		return {
			label: "已过期",
			className: "asset-manager__badge asset-manager__badge--muted",
		};
	}
	if (isApiKeyExpiringSoon(apiKey)) {
		return {
			label: "即将到期",
			className: "asset-manager__badge asset-manager__badge--warning",
		};
	}
	return {
		label: "有效",
		className: "asset-manager__badge asset-records__source-badge",
	};
}

export function formatExpiryNotice(apiKey: AgentApiKeyRecord): string | null {
	if (!apiKey.expires_at || !isApiKeyExpiringSoon(apiKey)) {
		return null;
	}
	const expiresAt = Date.parse(apiKey.expires_at);
	if (!Number.isFinite(expiresAt)) {
		return null;
	}
	const remainingMs = expiresAt - Date.now();
	const remainingDays = Math.ceil(remainingMs / (24 * 60 * 60 * 1000));
	if (remainingDays <= 1) {
		return "这个 API Key 将在 24 小时内到期。请尽快轮换，避免自动化请求中断。";
	}
	return `这个 API Key 将在 ${remainingDays} 天内到期。建议提前完成轮换并更新调用方配置。`;
}

export function getExpirySelectionValue(expiresInDays: number | null): string {
	if (expiresInDays === null) {
		return "never";
	}
	return String(expiresInDays);
}

export function parseExpirySelectionValue(value: string): number | null {
	return value === "never" ? null : Number(value);
}

export async function copyTextToClipboard(value: string): Promise<void> {
	if (
		typeof navigator !== "undefined"
		&& navigator.clipboard
		&& typeof navigator.clipboard.writeText === "function"
	) {
		await navigator.clipboard.writeText(value);
		return;
	}

	if (typeof document === "undefined") {
		throw new Error("当前环境不支持剪贴板复制。");
	}

	const textarea = document.createElement("textarea");
	textarea.value = value;
	textarea.setAttribute("readonly", "true");
	textarea.style.position = "absolute";
	textarea.style.opacity = "0";
	document.body.appendChild(textarea);
	textarea.select();
	try {
		document.execCommand("copy");
	} finally {
		document.body.removeChild(textarea);
	}
}
