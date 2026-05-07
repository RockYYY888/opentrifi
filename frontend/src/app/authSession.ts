import type {
	AuthLoginCredentials,
	AuthRegisterCredentials,
} from "../types/auth";

export type AuthStatus = "checking" | "anonymous" | "authenticated";

export const SESSION_CHECK_TIMEOUT_MS = 3000;
export const AUTH_SUBMISSION_TIMEOUT_MS = 10000;

const REMEMBERED_SESSION_USER_KEY = "asset-tracker-last-session-user";

export type AuthSubmissionPayload = AuthLoginCredentials | AuthRegisterCredentials;

export function readRememberedSessionUserId(): string | null {
	if (typeof window === "undefined") {
		return null;
	}

	try {
		const rememberedUserId =
			window.sessionStorage.getItem(REMEMBERED_SESSION_USER_KEY) ??
			window.localStorage.getItem(REMEMBERED_SESSION_USER_KEY);
		if (!rememberedUserId) {
			return null;
		}

		return rememberedUserId.trim() || null;
	} catch {
		return null;
	}
}

export function rememberSessionUserId(userId: string): void {
	try {
		window.sessionStorage.setItem(REMEMBERED_SESSION_USER_KEY, userId);
		window.localStorage.setItem(REMEMBERED_SESSION_USER_KEY, userId);
	} catch {
		// Storage can be blocked in private mode; the server session still remains authoritative.
	}
}

export function clearRememberedSessionUserId(): void {
	try {
		window.sessionStorage.removeItem(REMEMBERED_SESSION_USER_KEY);
		window.localStorage.removeItem(REMEMBERED_SESSION_USER_KEY);
	} catch {
		// Storage can be blocked in private mode; the server session still remains authoritative.
	}
}

export function isAuthenticationErrorMessage(message: string): boolean {
	return (
		message.includes("请先登录") ||
		message.includes("请重新登录") ||
		message.includes("请先提供 API Key") ||
		message.includes("API Key 无效") ||
		message.includes("API Key 已过期") ||
		message.includes("API Key 对应账号不存在")
	);
}

export async function withTimeout<T>(
	task: Promise<T>,
	timeoutMs: number,
	timeoutMessage: string,
): Promise<T> {
	let timeoutId = 0;

	try {
		return await Promise.race([
			task,
			new Promise<T>((_, reject) => {
				timeoutId = window.setTimeout(() => {
					reject(new Error(timeoutMessage));
				}, timeoutMs);
			}),
		]);
	} finally {
		if (timeoutId) {
			window.clearTimeout(timeoutId);
		}
	}
}
