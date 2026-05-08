from fastapi import APIRouter

from app.schemas import (
	ActionMessageRead,
	AdminFeedbackListRead,
	AdminFeedbackRead,
	FeedbackSummaryRead,
	UserFeedbackRead,
)
from app.services.feedback_admin_service import (
	acknowledge_feedback_for_admin,
	classify_feedback_for_admin,
	close_feedback_for_admin,
	list_feedback_for_admin,
	list_system_feedback_for_admin,
	list_user_feedback_for_admin,
	reply_to_feedback_for_admin,
)
from app.services.feedback_user_service import (
	get_feedback_summary,
	hide_inbox_message_for_current_user,
	list_feedback_for_current_user,
	mark_feedback_seen_for_current_user,
	submit_feedback,
)

router = APIRouter()

router.add_api_route("/api/feedback", submit_feedback, methods=["POST"], response_model=UserFeedbackRead, status_code=201)
router.add_api_route("/api/feedback", list_feedback_for_current_user, methods=["GET"], response_model=list[UserFeedbackRead])
router.add_api_route(
	"/api/feedback/mark-seen",
	mark_feedback_seen_for_current_user,
	methods=["POST"],
	response_model=ActionMessageRead,
)
router.add_api_route(
	"/api/feedback/summary",
	get_feedback_summary,
	methods=["GET"],
	response_model=FeedbackSummaryRead,
)
router.add_api_route(
	"/api/messages/hide",
	hide_inbox_message_for_current_user,
	methods=["POST"],
	response_model=ActionMessageRead,
)
router.add_api_route("/api/admin/feedback", list_feedback_for_admin, methods=["GET"], response_model=list[UserFeedbackRead])
router.add_api_route(
	"/api/admin/feedback/user",
	list_user_feedback_for_admin,
	methods=["GET"],
	response_model=AdminFeedbackListRead,
)
router.add_api_route(
	"/api/admin/feedback/system",
	list_system_feedback_for_admin,
	methods=["GET"],
	response_model=AdminFeedbackListRead,
)
router.add_api_route(
	"/api/admin/feedback/{feedback_id}/reply",
	reply_to_feedback_for_admin,
	methods=["POST"],
	response_model=AdminFeedbackRead,
)
router.add_api_route(
	"/api/admin/feedback/{feedback_id}/close",
	close_feedback_for_admin,
	methods=["POST"],
	response_model=AdminFeedbackRead,
)
router.add_api_route(
	"/api/admin/feedback/{feedback_id}/ack",
	acknowledge_feedback_for_admin,
	methods=["POST"],
	response_model=AdminFeedbackRead,
)
router.add_api_route(
	"/api/admin/feedback/{feedback_id}/classify",
	classify_feedback_for_admin,
	methods=["POST"],
	response_model=AdminFeedbackRead,
)
