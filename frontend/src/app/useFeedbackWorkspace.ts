import { useState } from "react";

import { removeRecordById, replaceRecordById } from "./feedbackInbox";
import {
	createReleaseNoteForAdmin,
	closeFeedbackForAdmin,
	getFeedbackSummary,
	hideInboxMessageForCurrentUser,
	listFeedbackForCurrentUser,
	listReleaseNotesForAdmin,
	listReleaseNotesForCurrentUser,
	listSystemFeedbackForAdmin,
	listUserFeedbackForAdmin,
	markFeedbackSeenForCurrentUser,
	markReleaseNotesSeenForCurrentUser,
	publishReleaseNoteForAdmin,
	replyToFeedbackForAdmin,
	submitUserFeedback,
} from "../lib/feedbackApi";
import { updateCurrentUserEmail } from "../lib/authApi";
import type { AuthStatus } from "./authSession";
import type { UserEmailUpdate } from "../types/auth";
import type {
	AdminFeedbackRecord,
	ReleaseNoteDeliveryRecord,
	ReleaseNoteInput,
	ReleaseNoteRecord,
	UserFeedbackRecord,
} from "../types/feedback";

export interface UseFeedbackWorkspaceOptions {
	authStatus: AuthStatus;
	currentUserId: string | null;
	onEmailUpdated: (email: string | null) => void;
}

export function useFeedbackWorkspace({
	authStatus,
	currentUserId,
	onEmailUpdated,
}: UseFeedbackWorkspaceOptions) {
	const [isFeedbackOpen, setIsFeedbackOpen] = useState(false);
	const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
	const [feedbackErrorMessage, setFeedbackErrorMessage] = useState<string | null>(null);
	const [feedbackNoticeMessage, setFeedbackNoticeMessage] = useState<string | null>(null);
	const [feedbackInboxCount, setFeedbackInboxCount] = useState(0);
	const [isAdminInboxOpen, setIsAdminInboxOpen] = useState(false);
	const [isAdminReleaseNotesOpen, setIsAdminReleaseNotesOpen] = useState(false);
	const [isUserInboxOpen, setIsUserInboxOpen] = useState(false);
	const [isLoadingAdminInbox, setIsLoadingAdminInbox] = useState(false);
	const [isAdminInboxShowingDismissed, setIsAdminInboxShowingDismissed] = useState(false);
	const [isLoadingAdminReleaseNotes, setIsLoadingAdminReleaseNotes] = useState(false);
	const [adminInboxErrorMessage, setAdminInboxErrorMessage] = useState<string | null>(null);
	const [adminReleaseNotesErrorMessage, setAdminReleaseNotesErrorMessage] = useState<string | null>(
		null,
	);
	const [adminUserFeedbackItems, setAdminUserFeedbackItems] = useState<AdminFeedbackRecord[]>([]);
	const [adminSystemFeedbackItems, setAdminSystemFeedbackItems] = useState<AdminFeedbackRecord[]>(
		[],
	);
	const [adminInboxReleaseNotes, setAdminInboxReleaseNotes] = useState<ReleaseNoteDeliveryRecord[]>(
		[],
	);
	const [adminReleaseNotes, setAdminReleaseNotes] = useState<ReleaseNoteRecord[]>([]);
	const [isLoadingUserInbox, setIsLoadingUserInbox] = useState(false);
	const [userInboxErrorMessage, setUserInboxErrorMessage] = useState<string | null>(null);
	const [userFeedbackItems, setUserFeedbackItems] = useState<UserFeedbackRecord[]>([]);
	const [userReleaseNotes, setUserReleaseNotes] = useState<ReleaseNoteDeliveryRecord[]>([]);
	const [isEmailDialogOpen, setIsEmailDialogOpen] = useState(false);
	const [isSubmittingEmail, setIsSubmittingEmail] = useState(false);
	const [emailDialogErrorMessage, setEmailDialogErrorMessage] = useState<string | null>(null);
	const [emailNoticeMessage, setEmailNoticeMessage] = useState<string | null>(null);

	function resetFeedbackWorkspaceState(): void {
		setFeedbackNoticeMessage(null);
		setFeedbackInboxCount(0);
		setFeedbackErrorMessage(null);
		setIsFeedbackOpen(false);
		setIsLoadingAdminInbox(false);
		setAdminInboxErrorMessage(null);
		setIsAdminInboxOpen(false);
		setIsLoadingAdminReleaseNotes(false);
		setAdminReleaseNotesErrorMessage(null);
		setIsAdminReleaseNotesOpen(false);
		setAdminUserFeedbackItems([]);
		setAdminSystemFeedbackItems([]);
		setAdminInboxReleaseNotes([]);
		setAdminReleaseNotes([]);
		setUserInboxErrorMessage(null);
		setIsUserInboxOpen(false);
		setUserFeedbackItems([]);
		setUserReleaseNotes([]);
		setEmailNoticeMessage(null);
		setEmailDialogErrorMessage(null);
		setIsEmailDialogOpen(false);
	}

	function openFeedbackDialog(): void {
		if (authStatus !== "authenticated") {
			return;
		}

		setFeedbackErrorMessage(null);
		setFeedbackNoticeMessage(null);
		setIsFeedbackOpen(true);
	}

	function closeFeedbackDialog(): void {
		if (isSubmittingFeedback) {
			return;
		}

		setFeedbackErrorMessage(null);
		setIsFeedbackOpen(false);
	}

	async function refreshFeedbackSummary(): Promise<void> {
		if (authStatus !== "authenticated") {
			setFeedbackInboxCount(0);
			return;
		}

		try {
			const summary = await getFeedbackSummary();
			setFeedbackInboxCount(summary.inbox_count);
		} catch {
			// Keep current badge value when summary refresh fails.
		}
	}

	async function loadAdminInbox(includeHidden: boolean, openDialog = true): Promise<void> {
		if (authStatus !== "authenticated" || currentUserId !== "admin") {
			return;
		}

		setAdminInboxErrorMessage(null);
		setIsAdminInboxShowingDismissed(includeHidden);
		if (openDialog) {
			setIsAdminInboxOpen(true);
		}
		setIsLoadingAdminInbox(true);

		try {
			const [userFeedbackItems, systemFeedbackItems, releaseNotes] = await Promise.all([
				listUserFeedbackForAdmin(includeHidden),
				listSystemFeedbackForAdmin(includeHidden),
				listReleaseNotesForCurrentUser(),
			]);
			setAdminUserFeedbackItems(userFeedbackItems.items);
			setAdminSystemFeedbackItems(systemFeedbackItems.items);
			setAdminInboxReleaseNotes(releaseNotes);
			await markReleaseNotesSeenForCurrentUser();
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminInboxErrorMessage(
				error instanceof Error ? error.message : "消息加载失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminInbox(false);
		}
	}

	async function openAdminInbox(): Promise<void> {
		await loadAdminInbox(false);
	}

	function closeAdminInbox(): void {
		if (isLoadingAdminInbox) {
			return;
		}

		setAdminInboxErrorMessage(null);
		setIsAdminInboxShowingDismissed(false);
		setIsAdminInboxOpen(false);
	}

	async function handleAdminInboxShowDismissedChange(showDismissed: boolean): Promise<void> {
		await loadAdminInbox(showDismissed, false);
	}

	async function openAdminReleaseNotes(): Promise<void> {
		if (authStatus !== "authenticated" || currentUserId !== "admin") {
			return;
		}

		setAdminReleaseNotesErrorMessage(null);
		setIsAdminReleaseNotesOpen(true);
		setIsLoadingAdminReleaseNotes(true);

		try {
			const releaseNotes = await listReleaseNotesForAdmin();
			setAdminReleaseNotes(releaseNotes);
		} catch (error) {
			setAdminReleaseNotesErrorMessage(
				error instanceof Error ? error.message : "更新日志加载失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminReleaseNotes(false);
		}
	}

	function closeAdminReleaseNotes(): void {
		if (isLoadingAdminReleaseNotes) {
			return;
		}

		setAdminReleaseNotesErrorMessage(null);
		setIsAdminReleaseNotesOpen(false);
	}

	async function openUserInbox(): Promise<void> {
		if (authStatus !== "authenticated") {
			return;
		}

		setUserInboxErrorMessage(null);
		setIsUserInboxOpen(true);
		setIsLoadingUserInbox(true);

		try {
			const feedbackItems = await listFeedbackForCurrentUser();
			const releaseNotes = await listReleaseNotesForCurrentUser();
			setUserFeedbackItems(feedbackItems);
			setUserReleaseNotes(releaseNotes);
			await markFeedbackSeenForCurrentUser();
			await markReleaseNotesSeenForCurrentUser();
			await refreshFeedbackSummary();
		} catch (error) {
			setUserInboxErrorMessage(
				error instanceof Error ? error.message : "消息加载失败，请稍后再试。",
			);
		} finally {
			setIsLoadingUserInbox(false);
		}
	}

	function closeUserInbox(): void {
		if (isLoadingUserInbox) {
			return;
		}

		setUserInboxErrorMessage(null);
		setIsUserInboxOpen(false);
	}

	async function handleSubmitFeedback(message: string): Promise<void> {
		setIsSubmittingFeedback(true);
		setFeedbackErrorMessage(null);

		try {
			await submitUserFeedback({ message });
			setFeedbackNoticeMessage("问题反馈已记录。");
			setIsFeedbackOpen(false);
			await refreshFeedbackSummary();
		} catch (error) {
			setFeedbackErrorMessage(
				error instanceof Error ? error.message : "反馈提交失败，请稍后再试。",
			);
		} finally {
			setIsSubmittingFeedback(false);
		}
	}

	async function handleCloseFeedbackItem(feedbackId: number): Promise<void> {
		setIsLoadingAdminInbox(true);
		setAdminInboxErrorMessage(null);

		try {
			const updatedItem = await closeFeedbackForAdmin(feedbackId);
			setAdminUserFeedbackItems((currentItems) =>
				replaceRecordById(currentItems, updatedItem),
			);
			setAdminSystemFeedbackItems((currentItems) =>
				replaceRecordById(currentItems, updatedItem),
			);
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminInboxErrorMessage(
				error instanceof Error ? error.message : "关闭反馈失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminInbox(false);
		}
	}

	async function handleReplyFeedbackItem(
		feedbackId: number,
		replyMessage: string,
		close: boolean,
	): Promise<void> {
		setIsLoadingAdminInbox(true);
		setAdminInboxErrorMessage(null);

		try {
			const updatedItem = await replyToFeedbackForAdmin(feedbackId, {
				reply_message: replyMessage,
				close,
			});
			setAdminUserFeedbackItems((currentItems) =>
				replaceRecordById(currentItems, updatedItem),
			);
			setAdminSystemFeedbackItems((currentItems) =>
				replaceRecordById(currentItems, updatedItem),
			);
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminInboxErrorMessage(
				error instanceof Error ? error.message : "回复失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminInbox(false);
		}
	}

	async function handleHideAdminFeedbackItem(feedbackId: number): Promise<void> {
		setIsLoadingAdminInbox(true);
		setAdminInboxErrorMessage(null);

		try {
			await hideInboxMessageForCurrentUser({
				message_kind: "FEEDBACK",
				message_id: feedbackId,
			});
			if (isAdminInboxShowingDismissed) {
				const [userFeedbackItems, systemFeedbackItems] = await Promise.all([
					listUserFeedbackForAdmin(true),
					listSystemFeedbackForAdmin(true),
				]);
				setAdminUserFeedbackItems(userFeedbackItems.items);
				setAdminSystemFeedbackItems(systemFeedbackItems.items);
			} else {
				setAdminUserFeedbackItems((currentItems) =>
					removeRecordById(currentItems, feedbackId),
				);
				setAdminSystemFeedbackItems((currentItems) =>
					removeRecordById(currentItems, feedbackId),
				);
			}
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminInboxErrorMessage(
				error instanceof Error ? error.message : "移除消息失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminInbox(false);
		}
	}

	async function handleCreateReleaseNote(payload: ReleaseNoteInput): Promise<void> {
		setIsLoadingAdminReleaseNotes(true);
		setAdminReleaseNotesErrorMessage(null);

		try {
			const createdReleaseNote = await createReleaseNoteForAdmin(payload);
			setAdminReleaseNotes((currentItems) => [createdReleaseNote, ...currentItems]);
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminReleaseNotesErrorMessage(
				error instanceof Error ? error.message : "创建更新日志失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminReleaseNotes(false);
		}
	}

	async function handlePublishReleaseNote(releaseNoteId: number): Promise<void> {
		setIsLoadingAdminReleaseNotes(true);
		setAdminReleaseNotesErrorMessage(null);

		try {
			const publishedReleaseNote = await publishReleaseNoteForAdmin(releaseNoteId);
			setAdminReleaseNotes((currentItems) =>
				currentItems.map((item) =>
					item.id === publishedReleaseNote.id ? publishedReleaseNote : item
				),
			);
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminReleaseNotesErrorMessage(
				error instanceof Error ? error.message : "发布更新日志失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminReleaseNotes(false);
		}
	}

	function openEmailDialog(): void {
		if (authStatus !== "authenticated") {
			return;
		}

		setEmailDialogErrorMessage(null);
		setEmailNoticeMessage(null);
		setIsEmailDialogOpen(true);
	}

	function closeEmailDialog(): void {
		if (isSubmittingEmail) {
			return;
		}

		setEmailDialogErrorMessage(null);
		setIsEmailDialogOpen(false);
	}

	async function handleSubmitEmail(payload: UserEmailUpdate): Promise<void> {
		setIsSubmittingEmail(true);
		setEmailDialogErrorMessage(null);
		setEmailNoticeMessage(null);

		try {
			const session = await updateCurrentUserEmail(payload);
			onEmailUpdated(session.email ?? null);
			setEmailNoticeMessage("邮箱已更新。");
			setIsEmailDialogOpen(false);
		} catch (error) {
			setEmailDialogErrorMessage(
				error instanceof Error ? error.message : "邮箱保存失败，请稍后再试。",
			);
		} finally {
			setIsSubmittingEmail(false);
		}
	}

	return {
		adminInboxErrorMessage,
		adminInboxReleaseNotes,
		adminReleaseNotes,
		adminReleaseNotesErrorMessage,
		adminSystemFeedbackItems,
		adminUserFeedbackItems,
		closeAdminInbox,
		closeAdminReleaseNotes,
		closeEmailDialog,
		closeFeedbackDialog,
		closeUserInbox,
		emailDialogErrorMessage,
		emailNoticeMessage,
		feedbackErrorMessage,
		feedbackInboxCount,
		feedbackNoticeMessage,
		handleAdminInboxShowDismissedChange,
		handleCloseFeedbackItem,
		handleCreateReleaseNote,
		handleHideAdminFeedbackItem,
		handlePublishReleaseNote,
		handleReplyFeedbackItem,
		handleSubmitEmail,
		handleSubmitFeedback,
		isAdminInboxOpen,
		isAdminInboxShowingDismissed,
		isAdminReleaseNotesOpen,
		isEmailDialogOpen,
		isFeedbackOpen,
		isLoadingAdminInbox,
		isLoadingAdminReleaseNotes,
		isLoadingUserInbox,
		isSubmittingEmail,
		isSubmittingFeedback,
		isUserInboxOpen,
		openAdminInbox,
		openAdminReleaseNotes,
		openEmailDialog,
		openFeedbackDialog,
		openUserInbox,
		refreshFeedbackSummary,
		resetFeedbackWorkspaceState,
		userFeedbackItems,
		userInboxErrorMessage,
		userReleaseNotes,
	};
}
