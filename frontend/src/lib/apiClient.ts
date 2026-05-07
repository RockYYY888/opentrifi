import type { DecimalString } from "../types/decimal";

const DEFAULT_API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const RUNTIME_API_KEY_STORAGE_KEY = "asset-tracker-runtime-api-key";
let inMemoryRuntimeApiKey: string | null = null;

export interface ApiClient {
	request: <T>(path: string, init?: RequestInit) => Promise<T>;
}

export interface ApiClientOptions {
	baseUrl?: string;
	fetcher?: typeof fetch;
}

const DECIMAL_API_FIELD_NAMES = new Set([
	"amount",
	"balance",
	"cash_value_cny",
	"cost_basis_price",
	"current_value_cny",
	"fixed_assets_value_cny",
	"fx_to_cny",
	"hkd_cny_rate",
	"holdings_value_cny",
	"liabilities_value_cny",
	"original_value_cny",
	"other_assets_value_cny",
	"price",
	"profit_amount",
	"profit_rate_pct",
	"purchase_value_cny",
	"quantity",
	"return_pct",
	"source_amount",
	"target_amount",
	"total_value_cny",
	"usd_cny_rate",
	"value",
	"value_cny",
]);
const DECIMAL_STRING_PATTERN = /^-?\d+(?:\.\d+)?$/;

export function parseDecimalString(value: number | DecimalString): number {
	if (typeof value === "number") {
		return value;
	}
	const parsedValue = Number(value);
	if (!Number.isFinite(parsedValue)) {
		throw new Error(`Invalid decimal string: ${value}`);
	}
	return parsedValue;
}

function normalizeApiDecimalStrings(value: unknown, fieldName?: string): unknown {
	if (typeof value === "string") {
		if (fieldName && DECIMAL_API_FIELD_NAMES.has(fieldName) && DECIMAL_STRING_PATTERN.test(value)) {
			return parseDecimalString(value);
		}
		return value;
	}
	if (Array.isArray(value)) {
		return value.map((item) => normalizeApiDecimalStrings(item));
	}
	if (value && typeof value === "object") {
		return Object.fromEntries(
			Object.entries(value).map(([key, item]) => [
				key,
				normalizeApiDecimalStrings(item, key),
			]),
		);
	}
	return value;
}

function normalizeApiKeyValue(value: string | null | undefined): string | null {
	if (typeof value !== "string") {
		return null;
	}
	const normalizedValue = value.trim();
	return normalizedValue || null;
}

export function getStoredRuntimeApiKey(): string | null {
	if (inMemoryRuntimeApiKey) {
		return inMemoryRuntimeApiKey;
	}

	if (typeof window === "undefined") {
		return null;
	}

	try {
		const storedApiKey =
			window.sessionStorage.getItem(RUNTIME_API_KEY_STORAGE_KEY)
			?? window.localStorage.getItem(RUNTIME_API_KEY_STORAGE_KEY);
		inMemoryRuntimeApiKey = normalizeApiKeyValue(storedApiKey);
		return inMemoryRuntimeApiKey;
	} catch {
		return inMemoryRuntimeApiKey;
	}
}

export function setStoredRuntimeApiKey(value: string): void {
	const normalizedValue = normalizeApiKeyValue(value);
	inMemoryRuntimeApiKey = normalizedValue;
	if (typeof window === "undefined") {
		return;
	}

	try {
		if (normalizedValue) {
			window.sessionStorage.setItem(RUNTIME_API_KEY_STORAGE_KEY, normalizedValue);
			window.localStorage.setItem(RUNTIME_API_KEY_STORAGE_KEY, normalizedValue);
		} else {
			window.sessionStorage.removeItem(RUNTIME_API_KEY_STORAGE_KEY);
			window.localStorage.removeItem(RUNTIME_API_KEY_STORAGE_KEY);
		}
	} catch {
		// Ignore storage failures and fall back to in-memory auth state.
	}
}

export function clearStoredRuntimeApiKey(): void {
	inMemoryRuntimeApiKey = null;
	if (typeof window === "undefined") {
		return;
	}

	try {
		window.sessionStorage.removeItem(RUNTIME_API_KEY_STORAGE_KEY);
		window.localStorage.removeItem(RUNTIME_API_KEY_STORAGE_KEY);
	} catch {
		// Ignore storage failures and fall back to in-memory auth state.
	}
}

function parsePayload<T>(responseText: string): T {
	if (!responseText.trim()) {
		return undefined as T;
	}

	try {
		return normalizeApiDecimalStrings(JSON.parse(responseText)) as T;
	} catch {
		return responseText as T;
	}
}

type ApiErrorDetailItem = {
	msg?: string;
};

const GENERIC_SERVER_ERROR_MESSAGES = new Set([
	"internal server error",
	"server error",
	"bad gateway",
	"service unavailable",
	"gateway timeout",
]);

function translateValidationMessage(message: string): string {
	const normalizedMessage = message.replace(/^Value error,\s*/, "").trim();
	if (!normalizedMessage) {
		return "输入内容不符合要求。";
	}

	if (normalizedMessage === "Field required") {
		return "请完整填写必填项。";
	}

	const minLengthMatch = normalizedMessage.match(
		/^String should have at least (\d+) characters?$/,
	);
	if (minLengthMatch) {
		return `输入内容至少需要 ${minLengthMatch[1]} 个字符。`;
	}

	const maxLengthMatch = normalizedMessage.match(
		/^String should have at most (\d+) characters?$/,
	);
	if (maxLengthMatch) {
		return `输入内容不能超过 ${maxLengthMatch[1]} 个字符。`;
	}

	if (normalizedMessage === "Input should be a valid string") {
		return "输入格式不正确。";
	}

	return normalizedMessage;
}

function extractValidationErrorMessage(detail: unknown): string | null {
	if (!Array.isArray(detail)) {
		return null;
	}

	const messages = detail
		.map((item) => {
			if (!item || typeof item !== "object") {
				return null;
			}

			const message = (item as ApiErrorDetailItem).msg;
			if (typeof message !== "string") {
				return null;
			}

			return translateValidationMessage(message);
		})
		.filter((message): message is string => message !== null);

	if (messages.length === 0) {
		return null;
	}

	return Array.from(new Set(messages)).join("；");
}

function getStatusFallbackMessage(statusCode: number): string {
	switch (statusCode) {
		case 400:
			return "请求内容不正确，请检查后重试。";
		case 401:
			return "身份验证失败，请重新登录。";
		case 403:
			return "当前请求被服务器拒绝。";
		case 404:
			return "请求的内容不存在。";
		case 409:
			return "当前内容已存在或状态冲突。";
		case 422:
			return "输入内容不符合要求，请检查后重试。";
		case 429:
			return "请求过于频繁，请稍后再试。";
		default:
			if (statusCode >= 500) {
				return "服务器暂时不可用，请稍后再试。";
			}

			return `请求失败（${statusCode}）。`;
	}
}

function isGenericServerErrorText(message: string): boolean {
	const normalizedMessage = message.trim().toLowerCase();
	if (!normalizedMessage) {
		return true;
	}

	if (GENERIC_SERVER_ERROR_MESSAGES.has(normalizedMessage)) {
		return true;
	}

	return (
		normalizedMessage.startsWith("<!doctype html")
		|| normalizedMessage.startsWith("<html")
		|| normalizedMessage.includes("<body")
		|| normalizedMessage.includes("traceback")
	);
}

function extractErrorMessage(responseText: string, statusCode: number): string {
	const fallbackMessage = getStatusFallbackMessage(statusCode);
	if (!responseText.trim()) {
		return fallbackMessage;
	}

	try {
		const parsed = JSON.parse(responseText) as { detail?: string | unknown[] };
		if (typeof parsed.detail === "string" && parsed.detail.trim()) {
			if (statusCode >= 500 && isGenericServerErrorText(parsed.detail)) {
				return fallbackMessage;
			}
			return parsed.detail;
		}

		const validationMessage = extractValidationErrorMessage(parsed.detail);
		if (validationMessage) {
			if (statusCode >= 500 && isGenericServerErrorText(validationMessage)) {
				return fallbackMessage;
			}
			return validationMessage;
		}
	} catch {
		if (statusCode >= 500 && isGenericServerErrorText(responseText)) {
			return fallbackMessage;
		}
		return responseText.trim() || fallbackMessage;
	}

	return fallbackMessage;
}

function toNetworkErrorMessage(error: unknown): string {
	if (error instanceof Error) {
		const normalizedMessage = error.message.trim();
		if (
			normalizedMessage === "Failed to fetch"
			|| normalizedMessage === "Load failed"
			|| normalizedMessage === "NetworkError when attempting to fetch resource."
		) {
			return "无法连接到服务器，请检查网络或服务状态。";
		}

		if (normalizedMessage) {
			return normalizedMessage;
		}
	}

	return "无法连接到服务器，请检查网络或服务状态。";
}

/**
 * Creates a lightweight request wrapper shared by feature modules.
 */
export function createApiClient(options: ApiClientOptions = {}): ApiClient {
	const baseUrl = options.baseUrl ?? DEFAULT_API_BASE_URL;
	const fetcher = options.fetcher ?? fetch;

	return {
		async request<T>(path: string, init?: RequestInit): Promise<T> {
			const requestHeaders = new Headers(init?.headers ?? undefined);
			if (!requestHeaders.has("Content-Type") && init?.body) {
				requestHeaders.set("Content-Type", "application/json");
			}

			let response: Response;
			try {
				response = await fetcher(`${baseUrl}${path}`, {
					...init,
					credentials: init?.credentials ?? "include",
					headers: requestHeaders,
				});
			} catch (error) {
				throw new Error(toNetworkErrorMessage(error));
			}
			const responseText = await response.text();

			if (!response.ok) {
				throw new Error(extractErrorMessage(responseText, response.status));
			}

			return parsePayload<T>(responseText);
		},
	};
}

/**
 * Normalizes thrown values into user-facing copy.
 */
export function toErrorMessage(error: unknown, fallbackMessage: string): string {
	if (error instanceof Error && error.message.trim()) {
		return error.message;
	}

	return fallbackMessage;
}
