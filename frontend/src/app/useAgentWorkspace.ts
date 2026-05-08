import { useRef, useState } from "react";

import { defaultAssetApiClient } from "../lib/assetApi";
import type {
	AgentApiKeyIssueRecord,
	AgentApiKeyRecord,
	AgentRegistrationRecord,
	AssetRecordRecord,
	CreateAgentApiKeyInput,
} from "../types/assets";

const EMPTY_AGENT_REGISTRATIONS: AgentRegistrationRecord[] = [];
const EMPTY_AGENT_API_KEYS: AgentApiKeyRecord[] = [];
const EMPTY_AGENT_RECORDS: AssetRecordRecord[] = [];

function sortAssetRecordsByCreatedAt(records: AssetRecordRecord[]): AssetRecordRecord[] {
	return [...records].sort((left, right) => {
		const leftTime = left.created_at ? Date.parse(left.created_at) : 0;
		const rightTime = right.created_at ? Date.parse(right.created_at) : 0;
		if (leftTime !== rightTime) {
			return rightTime - leftTime;
		}
		return right.id - left.id;
	});
}

export function useAgentWorkspace(currentUserId: string | null) {
	const [agentRegistrations, setAgentRegistrations] = useState<AgentRegistrationRecord[]>(
		EMPTY_AGENT_REGISTRATIONS,
	);
	const [agentApiKeys, setAgentApiKeys] = useState<AgentApiKeyRecord[]>(EMPTY_AGENT_API_KEYS);
	const [issuedAgentApiKey, setIssuedAgentApiKey] = useState<AgentApiKeyIssueRecord | null>(null);
	const [agentRecords, setAgentRecords] = useState<AssetRecordRecord[]>(EMPTY_AGENT_RECORDS);
	const [isLoadingAgentAudit, setIsLoadingAgentAudit] = useState(false);
	const [agentAuditErrorMessage, setAgentAuditErrorMessage] = useState<string | null>(null);
	const [isCreatingAgentApiKey, setIsCreatingAgentApiKey] = useState(false);
	const [revokingAgentApiKeyId, setRevokingAgentApiKeyId] = useState<number | null>(null);
	const [agentApiKeyErrorMessage, setAgentApiKeyErrorMessage] = useState<string | null>(null);
	const [agentApiKeyNoticeMessage, setAgentApiKeyNoticeMessage] = useState<string | null>(null);
	const hasLoadedAgentAuditRef = useRef(false);
	const agentAuditRequestInFlightRef = useRef<Promise<void> | null>(null);
	const latestAgentAuditRequestIdRef = useRef(0);

	function resetAgentWorkspaceState(): void {
		setAgentRegistrations(EMPTY_AGENT_REGISTRATIONS);
		setAgentApiKeys(EMPTY_AGENT_API_KEYS);
		setIssuedAgentApiKey(null);
		setAgentRecords(EMPTY_AGENT_RECORDS);
		setIsLoadingAgentAudit(false);
		setAgentAuditErrorMessage(null);
		setIsCreatingAgentApiKey(false);
		setRevokingAgentApiKeyId(null);
		setAgentApiKeyErrorMessage(null);
		setAgentApiKeyNoticeMessage(null);
		hasLoadedAgentAuditRef.current = false;
		agentAuditRequestInFlightRef.current = null;
		latestAgentAuditRequestIdRef.current += 1;
	}

	async function loadAgentAudit(options: { force?: boolean } = {}): Promise<void> {
		if (!currentUserId) {
			return;
		}
		if (hasLoadedAgentAuditRef.current && !options.force) {
			return;
		}
		if (agentAuditRequestInFlightRef.current && !options.force) {
			await agentAuditRequestInFlightRef.current;
			return;
		}

		const requestId = latestAgentAuditRequestIdRef.current + 1;
		latestAgentAuditRequestIdRef.current = requestId;
		setIsLoadingAgentAudit(true);
		setAgentAuditErrorMessage(null);

		let requestPromise: Promise<void> | null = null;
		requestPromise = Promise.all([
			defaultAssetApiClient.listAgentRegistrations({
				includeAllUsers: currentUserId === "admin",
			}),
			defaultAssetApiClient.listAgentApiKeys(),
			defaultAssetApiClient.listAssetRecords({
				source: "AGENT",
				limit: 200,
			}),
			defaultAssetApiClient.listAssetRecords({
				source: "API",
				limit: 200,
			}),
		])
			.then(([registrations, apiKeys, agentRecords, directApiRecords]) => {
				if (latestAgentAuditRequestIdRef.current !== requestId) {
					return;
				}
				setAgentRegistrations(registrations);
				setAgentApiKeys(apiKeys);
				setAgentRecords(sortAssetRecordsByCreatedAt([...agentRecords, ...directApiRecords]));
				hasLoadedAgentAuditRef.current = true;
			})
			.catch((error) => {
				if (latestAgentAuditRequestIdRef.current !== requestId) {
					return;
				}
				hasLoadedAgentAuditRef.current = false;
				setAgentAuditErrorMessage(
					error instanceof Error ? error.message : "加载智能体审计失败。",
				);
			})
			.finally(() => {
				if (agentAuditRequestInFlightRef.current === requestPromise) {
					agentAuditRequestInFlightRef.current = null;
				}
				if (latestAgentAuditRequestIdRef.current === requestId) {
					setIsLoadingAgentAudit(false);
				}
			});
		agentAuditRequestInFlightRef.current = requestPromise;
		await requestPromise;
	}

	async function handleCreateAgentApiKey(payload: CreateAgentApiKeyInput): Promise<void> {
		const normalizedName = payload.name.trim();
		if (normalizedName.length < 3) {
			setAgentApiKeyErrorMessage("API Key 名称至少需要 3 个字符。");
			setAgentApiKeyNoticeMessage(null);
			return;
		}

		setIsCreatingAgentApiKey(true);
		setAgentApiKeyErrorMessage(null);
		setAgentApiKeyNoticeMessage(null);

		try {
			const issuedKey = await defaultAssetApiClient.createAgentApiKey({
				name: normalizedName,
				expires_in_days: payload.expires_in_days ?? null,
			});
			setIssuedAgentApiKey(issuedKey);
			await loadAgentAudit({ force: true });
		} catch (error) {
			setAgentApiKeyErrorMessage(
				error instanceof Error ? error.message : "API Key 创建失败，请稍后再试。",
			);
		} finally {
			setIsCreatingAgentApiKey(false);
		}
	}

	async function handleRevokeAgentApiKey(tokenId: number): Promise<void> {
		setRevokingAgentApiKeyId(tokenId);
		setAgentApiKeyErrorMessage(null);
		setAgentApiKeyNoticeMessage(null);

		try {
			await defaultAssetApiClient.revokeAgentApiKey(tokenId);
			setIssuedAgentApiKey((currentIssuedKey) =>
				currentIssuedKey?.id === tokenId ? null : currentIssuedKey,
			);
			setAgentApiKeyNoticeMessage("API Key 已撤销。");
			await loadAgentAudit({ force: true });
		} catch (error) {
			setAgentApiKeyErrorMessage(
				error instanceof Error ? error.message : "API Key 撤销失败，请稍后再试。",
			);
		} finally {
			setRevokingAgentApiKeyId(null);
		}
	}

	function clearIssuedAgentApiKey(): void {
		setIssuedAgentApiKey(null);
		setAgentApiKeyNoticeMessage(null);
	}

	return {
		agentApiKeyErrorMessage,
		agentApiKeyNoticeMessage,
		agentApiKeys,
		agentAuditErrorMessage,
		agentRecords,
		agentRegistrations,
		clearIssuedAgentApiKey,
		handleCreateAgentApiKey,
		handleRevokeAgentApiKey,
		hasLoadedAgentAuditRef,
		isCreatingAgentApiKey,
		isLoadingAgentAudit,
		issuedAgentApiKey,
		loadAgentAudit,
		resetAgentWorkspaceState,
		revokingAgentApiKeyId,
	};
}
