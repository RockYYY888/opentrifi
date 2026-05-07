import { AssetRecordsDialog } from "../components/assets/AssetRecordsDialog";
import { EmailDialog } from "../components/auth/EmailDialog";
import { AdminFeedbackDialog } from "../components/feedback/AdminFeedbackDialog";
import { AdminReleaseNotesDialog } from "../components/feedback/AdminReleaseNotesDialog";
import { FeedbackDialog } from "../components/feedback/FeedbackDialog";
import { UserFeedbackInboxDialog } from "../components/feedback/UserFeedbackInboxDialog";
import type { AssetApiClient } from "../types/assets";
import type { UserEmailUpdate } from "../types/auth";
import type {
	AdminFeedbackRecord,
	ReleaseNoteDeliveryRecord,
	ReleaseNoteInput,
	ReleaseNoteRecord,
	UserFeedbackRecord,
} from "../types/feedback";

export interface AppDialogsProps {
	adminInboxErrorMessage: string | null;
	adminInboxReleaseNotes: ReleaseNoteDeliveryRecord[];
	adminReleaseNotes: ReleaseNoteRecord[];
	adminReleaseNotesErrorMessage: string | null;
	adminSystemFeedbackItems: AdminFeedbackRecord[];
	adminUserFeedbackItems: AdminFeedbackRecord[];
	assetRecordRefreshToken: number;
	assetRecordsDialogVersion: number;
	currentUserEmail: string | null;
	currentUserId: string;
	emailDialogErrorMessage: string | null;
	feedbackErrorMessage: string | null;
	isAdminInboxOpen: boolean;
	isAdminInboxShowingDismissed: boolean;
	isAdminReleaseNotesOpen: boolean;
	isAssetRecordsOpen: boolean;
	isEmailDialogOpen: boolean;
	isFeedbackOpen: boolean;
	isLoadingAdminInbox: boolean;
	isLoadingAdminReleaseNotes: boolean;
	isLoadingUserInbox: boolean;
	isSubmittingEmail: boolean;
	isSubmittingFeedback: boolean;
	isUserInboxOpen: boolean;
	listAssetRecords: AssetApiClient["listAssetRecords"];
	userFeedbackItems: UserFeedbackRecord[];
	userInboxErrorMessage: string | null;
	userReleaseNotes: ReleaseNoteDeliveryRecord[];
	onCloseAdminInbox: () => void;
	onCloseAdminReleaseNotes: () => void;
	onCloseAssetRecords: () => void;
	onCloseEmail: () => void;
	onCloseFeedback: () => void;
	onCloseUserInbox: () => void;
	onCreateReleaseNote: (payload: ReleaseNoteInput) => Promise<void>;
	onHideAdminFeedbackItem: (feedbackId: number) => Promise<void>;
	onPublishReleaseNote: (releaseNoteId: number) => Promise<void>;
	onReplyFeedbackItem: (feedbackId: number, replyMessage: string, close: boolean) => Promise<void>;
	onShowDismissedChange: (showDismissed: boolean) => Promise<void>;
	onSubmitEmail: (payload: UserEmailUpdate) => Promise<void>;
	onSubmitFeedback: (message: string) => Promise<void>;
	onCloseFeedbackItem: (feedbackId: number) => Promise<void>;
}

export function AppDialogs({
	adminInboxErrorMessage,
	adminInboxReleaseNotes,
	adminReleaseNotes,
	adminReleaseNotesErrorMessage,
	adminSystemFeedbackItems,
	adminUserFeedbackItems,
	assetRecordRefreshToken,
	assetRecordsDialogVersion,
	currentUserEmail,
	currentUserId,
	emailDialogErrorMessage,
	feedbackErrorMessage,
	isAdminInboxOpen,
	isAdminInboxShowingDismissed,
	isAdminReleaseNotesOpen,
	isAssetRecordsOpen,
	isEmailDialogOpen,
	isFeedbackOpen,
	isLoadingAdminInbox,
	isLoadingAdminReleaseNotes,
	isLoadingUserInbox,
	isSubmittingEmail,
	isSubmittingFeedback,
	isUserInboxOpen,
	listAssetRecords,
	userFeedbackItems,
	userInboxErrorMessage,
	userReleaseNotes,
	onCloseAdminInbox,
	onCloseAdminReleaseNotes,
	onCloseAssetRecords,
	onCloseEmail,
	onCloseFeedback,
	onCloseUserInbox,
	onCreateReleaseNote,
	onHideAdminFeedbackItem,
	onPublishReleaseNote,
	onReplyFeedbackItem,
	onShowDismissedChange,
	onSubmitEmail,
	onSubmitFeedback,
	onCloseFeedbackItem,
}: AppDialogsProps) {
	return (
		<>
			<FeedbackDialog
				open={isFeedbackOpen}
				busy={isSubmittingFeedback}
				errorMessage={feedbackErrorMessage}
				onClose={onCloseFeedback}
				onSubmit={onSubmitFeedback}
			/>
			<AdminFeedbackDialog
				open={isAdminInboxOpen}
				busy={isLoadingAdminInbox}
				viewerUserId={currentUserId}
				userItems={adminUserFeedbackItems}
				systemItems={adminSystemFeedbackItems}
				releaseNotes={adminInboxReleaseNotes}
				showDismissed={isAdminInboxShowingDismissed}
				errorMessage={adminInboxErrorMessage}
				onClose={onCloseAdminInbox}
				onShowDismissedChange={onShowDismissedChange}
				onHideItem={onHideAdminFeedbackItem}
				onCloseItem={onCloseFeedbackItem}
				onReplyItem={onReplyFeedbackItem}
			/>
			<AdminReleaseNotesDialog
				open={isAdminReleaseNotesOpen}
				busy={isLoadingAdminReleaseNotes}
				releaseNotes={adminReleaseNotes}
				errorMessage={adminReleaseNotesErrorMessage}
				onClose={onCloseAdminReleaseNotes}
				onCreateReleaseNote={onCreateReleaseNote}
				onPublishReleaseNote={onPublishReleaseNote}
			/>
			<UserFeedbackInboxDialog
				open={isUserInboxOpen}
				busy={isLoadingUserInbox}
				viewerUserId={currentUserId}
				items={userFeedbackItems}
				releaseNotes={userReleaseNotes}
				errorMessage={userInboxErrorMessage}
				onClose={onCloseUserInbox}
			/>
			<EmailDialog
				open={isEmailDialogOpen}
				busy={isSubmittingEmail}
				initialEmail={currentUserEmail}
				errorMessage={emailDialogErrorMessage}
				onClose={onCloseEmail}
				onSubmit={(email) => onSubmitEmail({ email })}
			/>
			<AssetRecordsDialog
				key={assetRecordsDialogVersion}
				open={isAssetRecordsOpen}
				onClose={onCloseAssetRecords}
				onLoadRecords={listAssetRecords}
				refreshToken={assetRecordRefreshToken}
			/>
		</>
	);
}
