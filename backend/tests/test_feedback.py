from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.services.feedback_admin_service import (
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
from app.services.release_note_service import (
	create_release_note_for_admin,
	list_release_notes_for_current_user,
	mark_release_notes_seen_for_current_user,
	publish_changelog_release_note_for_admin,
	publish_release_note_for_admin,
)
from app.models import ReleaseNoteDelivery, UserAccount, UserFeedback
from app.schemas import (
	AdminFeedbackClassifyUpdate,
	AdminFeedbackReplyUpdate,
	InboxMessageHideCreate,
	ReleaseNoteCreate,
	ReleaseNotePublishChangelogCreate,
	UserFeedbackCreate,
)


@pytest.fixture
def session(postgres_engine) -> Iterator[Session]:
	engine = postgres_engine
	with Session(engine) as db_session:
		yield db_session


def make_user(session: Session, username: str = "tester") -> UserAccount:
	user = UserAccount(
		username=username,
		password_digest="scrypt$16384$8$1$bc13ea73dad1a1d781e1bf06e769ccda$"
		"de4af04355be41e4ec61f7dc8b3c19fcc4fc940ba47784324063d4169d57e80a"
		"14cc1588be5fea70338075226ff4b32aafe37ab0a114d05b70e0a2364a0d2bf7",
	)
	session.add(user)
	session.commit()
	session.refresh(user)
	return user


def test_submit_feedback_persists_feedback_for_current_user(session: Session) -> None:
	current_user = make_user(session)

	created_feedback = submit_feedback(
		UserFeedbackCreate(message="同步后投资类价格没有及时刷新。"),
		current_user,
		session,
	)

	persisted_feedback = session.exec(select(UserFeedback)).one()

	assert created_feedback.id == persisted_feedback.id
	assert created_feedback.message == persisted_feedback.message
	assert created_feedback.user_id == current_user.username
	assert persisted_feedback.user_id == current_user.username


def test_feedback_classification_defaults_and_system_submission(session: Session) -> None:
	admin_user = make_user(session, "admin")
	current_user = make_user(session, "classified_user")

	user_feedback = submit_feedback(
		UserFeedbackCreate(message="普通用户反馈默认应是中优先级。"),
		current_user,
		session,
	)
	assert user_feedback.category == "USER_REQUEST"
	assert user_feedback.priority == "MEDIUM"
	assert user_feedback.source == "USER"
	assert user_feedback.status == "OPEN"
	assert user_feedback.is_system is False

	for index in range(5):
		system_feedback = submit_feedback(
			UserFeedbackCreate(
				message=f"系统巡检心跳：{index}",
				category="SYSTEM_HEARTBEAT",
				priority="LOW",
				source="API_MONITOR",
			),
			admin_user,
			session,
		)
		assert system_feedback.user_id == "admin"
		assert system_feedback.category == "SYSTEM_HEARTBEAT"
		assert system_feedback.priority == "LOW"
		assert system_feedback.source == "API_MONITOR"
		assert system_feedback.is_system is True
		assert system_feedback.status == "RESOLVED"
		assert system_feedback.resolved_at is not None
		assert system_feedback.closed_by == "system-auto"

	system_alert_feedback = submit_feedback(
		UserFeedbackCreate(
			message="系统告警：行情源返回 5xx。",
			category="SYSTEM_ALERT",
			priority="HIGH",
			source="API_MONITOR",
		),
		admin_user,
		session,
	)
	assert system_alert_feedback.status == "OPEN"
	assert system_alert_feedback.resolved_at is None
	assert system_alert_feedback.closed_by is None


def test_admin_system_feedback_rewrites_user_source_and_skips_daily_limit(session: Session) -> None:
	admin_user = make_user(session, "admin")

	for index in range(5):
		created_feedback = submit_feedback(
			UserFeedbackCreate(
				message=f"系统巡检心跳兼容提交：{index}",
				category="SYSTEM_HEARTBEAT",
				priority="LOW",
				source="USER",
			),
			admin_user,
			session,
		)
		assert created_feedback.category == "SYSTEM_HEARTBEAT"
		assert created_feedback.source == "SYSTEM"
		assert created_feedback.is_system is True
		assert created_feedback.status == "RESOLVED"


def test_admin_user_request_with_user_source_still_has_daily_limit(session: Session) -> None:
	admin_user = make_user(session, "admin")

	for index in range(3):
		created_feedback = submit_feedback(
			UserFeedbackCreate(
				message=f"管理员模拟用户反馈第 {index + 1} 次。",
				source="USER",
			),
			admin_user,
			session,
		)
		assert created_feedback.category == "USER_REQUEST"
		assert created_feedback.source == "USER"

	with pytest.raises(HTTPException, match="今日反馈次数已达上限"):
		submit_feedback(
			UserFeedbackCreate(
				message="管理员模拟用户反馈第 4 次应受限。",
				source="USER",
			),
			admin_user,
			session,
		)


def test_submit_feedback_limits_each_user_to_three_per_day(session: Session) -> None:
	current_user = make_user(session)

	for index in range(3):
		created_feedback = submit_feedback(
			UserFeedbackCreate(message=f"第 {index + 1} 次问题反馈，用于验证每日上限。"),
			current_user,
			session,
		)
		assert created_feedback.id > 0

	with pytest.raises(HTTPException, match="今日反馈次数已达上限"):
		submit_feedback(
			UserFeedbackCreate(message="第 4 次提交应该被限制。"),
			current_user,
			session,
		)


def test_admin_can_list_and_close_feedback_without_affecting_daily_limit(session: Session) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "tester_2")

	created_feedback = submit_feedback(
		UserFeedbackCreate(message="一个需要处理的反馈。"),
		normal_user,
		session,
	)

	feedback_items = list_feedback_for_admin(admin_user, session, None)

	assert len(feedback_items) == 1
	assert feedback_items[0].id == created_feedback.id
	assert feedback_items[0].resolved_at is None

	closed_feedback = close_feedback_for_admin(created_feedback.id, admin_user, session, None)

	assert closed_feedback.user_id == normal_user.username
	assert closed_feedback.closed_by == "admin"
	assert closed_feedback.resolved_at is not None


def test_admin_can_reply_and_user_can_see_reply(session: Session) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "reply_user")

	created_feedback = submit_feedback(
		UserFeedbackCreate(message="希望看到更清晰的收益率说明。"),
		normal_user,
		session,
	)

	replied_feedback = reply_to_feedback_for_admin(
		created_feedback.id,
		AdminFeedbackReplyUpdate(reply_message="已收到，我们会在下一版优化说明文字。", close=True),
		admin_user,
		session,
		None,
	)

	user_feedback_items = list_feedback_for_current_user(normal_user, session, None)

	assert replied_feedback.reply_message == "已收到，我们会在下一版优化说明文字。"
	assert replied_feedback.replied_by == "admin"
	assert replied_feedback.resolved_at is not None
	assert len(user_feedback_items) == 1
	assert user_feedback_items[0].reply_message == "已收到，我们会在下一版优化说明文字。"


def test_admin_cannot_reply_to_system_feedback(session: Session) -> None:
	admin_user = make_user(session, "admin")

	system_feedback = submit_feedback(
		UserFeedbackCreate(
			message="API 巡检发现价格接口出现 5xx。",
			category="SYSTEM_ALERT",
			priority="HIGH",
			source="API_MONITOR",
		),
		admin_user,
		session,
	)

	with pytest.raises(HTTPException, match="系统工单无需回复"):
		reply_to_feedback_for_admin(
			system_feedback.id,
			AdminFeedbackReplyUpdate(reply_message="已记录告警，准备排查。", close=False),
			admin_user,
			session,
			None,
		)

	closed_feedback = close_feedback_for_admin(system_feedback.id, admin_user, session, None)
	assert closed_feedback.status == "RESOLVED"
	assert closed_feedback.resolved_at is not None


def test_admin_can_classify_and_reopen_feedback(session: Session) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "classify_user")

	created_feedback = submit_feedback(
		UserFeedbackCreate(message="请支持代理自动下单前的风控校验。"),
		normal_user,
		session,
	)

	classified_feedback = classify_feedback_for_admin(
		created_feedback.id,
		AdminFeedbackClassifyUpdate(
			category="SYSTEM_TASK",
			priority="HIGH",
			source="TRADING_AGENT",
			status="IN_PROGRESS",
		),
		admin_user,
		session,
		None,
	)
	assert classified_feedback.category == "SYSTEM_TASK"
	assert classified_feedback.priority == "HIGH"
	assert classified_feedback.source == "TRADING_AGENT"
	assert classified_feedback.status == "IN_PROGRESS"

	resolved_feedback = classify_feedback_for_admin(
		created_feedback.id,
		AdminFeedbackClassifyUpdate(status="RESOLVED"),
		admin_user,
		session,
		None,
	)
	assert resolved_feedback.status == "RESOLVED"
	assert resolved_feedback.resolved_at is not None
	assert resolved_feedback.closed_by == "admin"

	reopened_feedback = classify_feedback_for_admin(
		created_feedback.id,
		AdminFeedbackClassifyUpdate(status="OPEN"),
		admin_user,
		session,
		None,
	)
	assert reopened_feedback.status == "OPEN"
	assert reopened_feedback.resolved_at is None


def test_feedback_summary_counts_pending_items_for_admin_and_user(session: Session) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "summary_user")

	submit_feedback(
		UserFeedbackCreate(message="第一条反馈。"),
		normal_user,
		session,
	)
	submit_feedback(
		UserFeedbackCreate(message="第二条反馈。"),
		normal_user,
		session,
	)

	admin_summary = get_feedback_summary(admin_user, session, None)
	user_summary_before_reply = get_feedback_summary(normal_user, session, None)

	reply_to_feedback_for_admin(
		1,
		AdminFeedbackReplyUpdate(reply_message="已收到。", close=False),
		admin_user,
		session,
		None,
	)

	user_summary_after_reply = get_feedback_summary(normal_user, session, None)
	mark_feedback_seen_for_current_user(normal_user, session, None)
	user_summary_after_seen = get_feedback_summary(normal_user, session, None)

	assert admin_summary.mode == "admin-open"
	assert admin_summary.inbox_count == 2
	assert user_summary_before_reply.mode == "user-unread"
	assert user_summary_before_reply.inbox_count == 0
	assert user_summary_after_reply.inbox_count == 1
	assert user_summary_after_seen.inbox_count == 0


def test_admin_feedback_lists_can_include_previously_hidden_items(session: Session) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "hidden_feedback_user")

	user_feedback = submit_feedback(
		UserFeedbackCreate(message="这是一条会被管理员隐藏后再重新查看的用户工单。"),
		normal_user,
		session,
	)
	system_feedback = submit_feedback(
		UserFeedbackCreate(
			message="系统告警：模拟隐藏后仍可在已移除列表中查看。",
			category="SYSTEM_ALERT",
			priority="HIGH",
			source="API_MONITOR",
		),
		admin_user,
		session,
	)

	hide_inbox_message_for_current_user(
		InboxMessageHideCreate(message_kind="FEEDBACK", message_id=user_feedback.id),
		admin_user,
		session,
		None,
	)
	hide_inbox_message_for_current_user(
		InboxMessageHideCreate(message_kind="FEEDBACK", message_id=system_feedback.id),
		admin_user,
		session,
		None,
	)

	visible_user_items = list_user_feedback_for_admin(
		admin_user,
		session,
		None,
		page=1,
		page_size=50,
		status=None,
		priority=None,
		include_hidden=False,
	)
	visible_system_items = list_system_feedback_for_admin(
		admin_user,
		session,
		None,
		page=1,
		page_size=50,
		status=None,
		priority=None,
		include_hidden=False,
	)
	all_user_items = list_user_feedback_for_admin(
		admin_user,
		session,
		None,
		page=1,
		page_size=50,
		status=None,
		priority=None,
		include_hidden=True,
	)
	all_system_items = list_system_feedback_for_admin(
		admin_user,
		session,
		None,
		page=1,
		page_size=50,
		status=None,
		priority=None,
		include_hidden=True,
	)

	assert all(item.id != user_feedback.id for item in visible_user_items.items)
	assert all(item.id != system_feedback.id for item in visible_system_items.items)
	assert any(item.id == user_feedback.id for item in all_user_items.items)
	assert any(item.id == system_feedback.id for item in all_system_items.items)


def test_release_note_publish_pushes_station_message_to_users(session: Session) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "release_note_user")

	created_release_note = create_release_note_for_admin(
		ReleaseNoteCreate(
			version="0.2.0",
			title="Chart readability improvements",
			content="Added zero-axis segmentation and clearer legends for positive and negative returns.",
			source_feedback_ids=[5, 6],
		),
		admin_user,
		session,
		None,
	)
	assert created_release_note.published_at is None

	published_release_note = publish_release_note_for_admin(
		created_release_note.id,
		admin_user,
		session,
		None,
	)
	assert published_release_note.published_at is not None
	assert published_release_note.delivery_count == 2

	admin_release_notes = list_release_notes_for_current_user(admin_user, session, None)
	assert len(admin_release_notes) == 1
	assert admin_release_notes[0].version == "0.2.0"
	admin_summary = get_feedback_summary(admin_user, session, None)
	assert admin_summary.inbox_count == 1
	mark_release_notes_seen_for_current_user(admin_user, session, None)
	admin_summary_after_seen = get_feedback_summary(admin_user, session, None)
	assert admin_summary_after_seen.inbox_count == 0

	user_release_notes = list_release_notes_for_current_user(normal_user, session, None)
	assert len(user_release_notes) == 1
	assert user_release_notes[0].version == "0.2.0"
	assert user_release_notes[0].source_feedback_ids == [5, 6]
	assert user_release_notes[0].seen_at is None

	user_summary = get_feedback_summary(normal_user, session, None)
	assert user_summary.inbox_count == 1

	mark_release_notes_seen_for_current_user(normal_user, session, None)
	user_summary_after_seen = get_feedback_summary(normal_user, session, None)
	assert user_summary_after_seen.inbox_count == 0


def test_release_note_stream_keeps_single_user_message_after_multiple_publishes(
	session: Session,
) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "release_stream_user")

	first_note = create_release_note_for_admin(
		ReleaseNoteCreate(
			version="0.4.0",
			title="First wave of improvements",
			content="Fixed dynamic midline behavior in trend charts.",
			source_feedback_ids=[4],
		),
		admin_user,
		session,
		None,
	)
	publish_release_note_for_admin(first_note.id, admin_user, session, None)
	mark_release_notes_seen_for_current_user(normal_user, session, None)

	second_note = create_release_note_for_admin(
		ReleaseNoteCreate(
			version="0.5.0",
			title="Second wave of improvements",
			content="Unified release-note delivery into a single rolling message stream.",
			source_feedback_ids=[5],
		),
		admin_user,
		session,
		None,
	)
	publish_release_note_for_admin(second_note.id, admin_user, session, None)

	user_release_notes = list_release_notes_for_current_user(normal_user, session, None)
	assert len(user_release_notes) == 1
	assert user_release_notes[0].version == "0.5.0"
	assert user_release_notes[0].title == "Product Updates"
	assert "## v0.5.0" in user_release_notes[0].content
	assert "## v0.4.0" in user_release_notes[0].content
	assert user_release_notes[0].seen_at is None

	deliveries = list(
		session.exec(
			select(ReleaseNoteDelivery).where(ReleaseNoteDelivery.user_id == normal_user.username),
		),
	)
	assert len(deliveries) == 1

	user_summary = get_feedback_summary(normal_user, session, None)
	assert user_summary.inbox_count == 1


def test_release_note_version_must_be_unique(session: Session) -> None:
	admin_user = make_user(session, "admin")

	create_release_note_for_admin(
		ReleaseNoteCreate(
			version="0.3.0",
			title="Initial release note",
			content="Initial release note content.",
		),
		admin_user,
		session,
		None,
	)

	with pytest.raises(HTTPException, match="This release note version already exists"):
		create_release_note_for_admin(
			ReleaseNoteCreate(
				version="0.3.0",
				title="Duplicate release note",
				content="Duplicate release note versions must be rejected.",
			),
			admin_user,
			session,
			None,
		)


def test_publish_changelog_release_note_creates_and_pushes_stream_message(
	session: Session,
) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "changelog_user")

	published_release_note = publish_changelog_release_note_for_admin(
		ReleaseNotePublishChangelogCreate(
			version="0.7.1",
			title="Stability and workflow updates",
			content="- Standardized the production update flow\n- Added a changelog push entry point",
			release_url="https://github.com/RockYYY888/opentrifi/releases/tag/v0.7.1",
		),
		admin_user,
		session,
		None,
	)

	assert published_release_note.version == "0.7.1"
	assert published_release_note.published_at is not None
	assert published_release_note.delivery_count == 2
	assert "GitHub Release:" in published_release_note.content

	admin_release_notes = list_release_notes_for_current_user(admin_user, session, None)
	assert len(admin_release_notes) == 1
	assert admin_release_notes[0].version == "0.7.1"

	user_release_notes = list_release_notes_for_current_user(normal_user, session, None)
	assert len(user_release_notes) == 1
	assert user_release_notes[0].version == "0.7.1"
	assert "v0.7.1" in user_release_notes[0].content


def test_publish_changelog_release_note_is_idempotent_for_same_payload(
	session: Session,
) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "repeat_user")
	payload = ReleaseNotePublishChangelogCreate(
		version="0.7.2",
		title="Reliable release-note delivery",
		content="- Unified the release-note publish path\n- Avoided duplicate user notifications",
		release_url="https://github.com/RockYYY888/opentrifi/releases/tag/v0.7.2",
	)

	first_release_note = publish_changelog_release_note_for_admin(
		payload,
		admin_user,
		session,
		None,
	)
	second_release_note = publish_changelog_release_note_for_admin(
		payload,
		admin_user,
		session,
		None,
	)

	assert second_release_note.id == first_release_note.id
	deliveries = list(
		session.exec(
			select(ReleaseNoteDelivery).where(ReleaseNoteDelivery.user_id == normal_user.username),
		),
	)
	assert len(deliveries) == 1


def test_publish_changelog_release_note_repairs_missing_admin_delivery_without_resetting_seen(
	session: Session,
) -> None:
	admin_user = make_user(session, "admin")
	normal_user = make_user(session, "repeat_repair_user")
	payload = ReleaseNotePublishChangelogCreate(
		version="0.7.2",
		title="Reliable release-note delivery",
		content="- Unified the release-note publish path\n- Avoided duplicate user notifications",
		release_url="https://github.com/RockYYY888/opentrifi/releases/tag/v0.7.2",
	)

	first_release_note = publish_changelog_release_note_for_admin(
		payload,
		admin_user,
		session,
		None,
	)
	mark_release_notes_seen_for_current_user(normal_user, session, None)
	admin_delivery = session.exec(
		select(ReleaseNoteDelivery).where(ReleaseNoteDelivery.user_id == admin_user.username),
	).one()
	session.delete(admin_delivery)
	session.commit()

	second_release_note = publish_changelog_release_note_for_admin(
		payload,
		admin_user,
		session,
		None,
	)

	assert second_release_note.id == first_release_note.id
	assert second_release_note.delivery_count == 2
	admin_release_notes = list_release_notes_for_current_user(admin_user, session, None)
	assert len(admin_release_notes) == 1
	normal_release_notes = list_release_notes_for_current_user(normal_user, session, None)
	assert len(normal_release_notes) == 1
	assert normal_release_notes[0].seen_at is not None


def test_publish_changelog_release_note_rejects_older_than_latest_published_version(
	session: Session,
) -> None:
	admin_user = make_user(session, "admin")
	make_user(session, "older_version_user")

	publish_changelog_release_note_for_admin(
		ReleaseNotePublishChangelogCreate(
			version="0.8.0",
			title="Already published newer version",
			content="- Version 0.8.0 is already published",
			release_url="https://github.com/RockYYY888/opentrifi/releases/tag/v0.8.0",
		),
		admin_user,
		session,
		None,
	)

	with pytest.raises(
		HTTPException,
		match="Release note version cannot be older than the latest published version",
	):
		publish_changelog_release_note_for_admin(
			ReleaseNotePublishChangelogCreate(
				version="0.7.9",
				title="Backdated version",
				content="- This version should be rejected",
				release_url="https://github.com/RockYYY888/opentrifi/releases/tag/v0.7.9",
			),
			admin_user,
			session,
			None,
		)


def test_publish_changelog_release_note_rejects_unpublished_older_draft_version(
	session: Session,
) -> None:
	admin_user = make_user(session, "admin")
	make_user(session, "draft_guard_user")

	create_release_note_for_admin(
		ReleaseNoteCreate(
			version="0.7.9",
			title="Older draft",
			content="- An older draft version",
		),
		admin_user,
		session,
		None,
	)
	publish_changelog_release_note_for_admin(
		ReleaseNotePublishChangelogCreate(
			version="0.8.0",
			title="Already published newer version",
			content="- Version 0.8.0 is already published",
			release_url="https://github.com/RockYYY888/opentrifi/releases/tag/v0.8.0",
		),
		admin_user,
		session,
		None,
	)

	with pytest.raises(
		HTTPException,
		match="Release note version cannot be older than the latest published version",
	):
		publish_changelog_release_note_for_admin(
			ReleaseNotePublishChangelogCreate(
				version="0.7.9",
				title="Republished old draft",
				content="- This older draft should not be published again",
				release_url="https://github.com/RockYYY888/opentrifi/releases/tag/v0.7.9",
			),
			admin_user,
			session,
			None,
		)
