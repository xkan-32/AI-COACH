import logging
import uuid
from contextvars import ContextVar

import httpx

from app.domain.models import Activity, WorkoutProposal
from app.security import ApprovalActionSigner

logger = logging.getLogger(__name__)
_reply_token: ContextVar[str | None] = ContextVar("line_reply_token", default=None)


class LineApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def set_line_reply_token(token: str | None) -> None:
    _reply_token.set(token)


class LineConditionPromptSender:
    push_url = "https://api.line.me/v2/bot/message/push"
    reply_url = "https://api.line.me/v2/bot/message/reply"

    def __init__(
        self,
        channel_access_token: str,
        timeout_seconds: float = 10.0,
        action_signing_key: str = "local-development-only",
    ) -> None:
        self._token = channel_access_token
        self._timeout = timeout_seconds
        self._action_signer = ApprovalActionSigner(action_signing_key)

    async def send(self, line_user_id: str, activity: Activity) -> None:
        labels = [
            ("良好", "good"),
            ("疲労あり", "fatigued"),
            ("違和感あり", "discomfort"),
            ("痛みあり", "pain"),
        ]
        items = [
            {
                "type": "action",
                "action": {
                    "type": "postback",
                    "label": label,
                    "data": f"action=condition&activity_id={activity.id}&level={value}",
                    "displayText": label,
                },
            }
            for label, value in labels
        ]
        await self._push(
            line_user_id,
            {
                "type": "text",
                "text": "お疲れさまでした。今日の状態を教えてください。",
                "quickReply": {"items": items},
            },
            retry_key=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"ai-coach:condition-prompt:{line_user_id}:{activity.id}",
                )
            ),
        )

    async def send_text(self, line_user_id: str, text: str) -> None:
        await self._push(line_user_id, {"type": "text", "text": text})

    async def send_quick_reply(
        self,
        line_user_id: str,
        text: str,
        choices: list[tuple[str, str]],
    ) -> None:
        items = [
            {
                "type": "action",
                "action": {
                    "type": "postback",
                    "label": label,
                    "data": data,
                    "displayText": label,
                },
            }
            for label, data in choices
        ]
        await self._push(
            line_user_id,
            {"type": "text", "text": text, "quickReply": {"items": items}},
        )

    async def send_settings_link(self, line_user_id: str, url: str) -> None:
        await self._push(
            line_user_id,
            {
                "type": "template",
                "altText": "目標と運動環境の設定ページを開きます。",
                "template": {
                    "type": "buttons",
                    "text": "目標と運動環境をWebページで設定できます。",
                    "actions": [
                        {"type": "uri", "label": "設定ページを開く", "uri": url}
                    ],
                },
            },
        )

    async def send_weekly_plan_link(self, line_user_id: str, url: str) -> None:
        await self._push(
            line_user_id,
            {
                "type": "template",
                "altText": "週間トレーニング計画を確認します。",
                "template": {
                    "type": "buttons",
                    "text": "7日分の計画、理由、安全制約を確認して承認できます。",
                    "actions": [{"type": "uri", "label": "週間計画を開く", "uri": url}],
                },
            },
        )

    async def send_proposal(
        self,
        line_user_id: str,
        proposal: WorkoutProposal,
        *,
        publish_to_strava: bool = True,
    ) -> None:
        summary = (
            f"明日の提案: {proposal.title}"
            f"（{proposal.duration_minutes}分・{proposal.intensity}）\n"
            f"{proposal.rationale}"
        )
        if not publish_to_strava:
            await self._push(
                line_user_id,
                {
                    "type": "text",
                    "text": summary + "\n\nこの提案はアプリ内の記録です。"
                    "Stravaへは投稿しません。",
                },
            )
            return
        items = [
            {
                "type": "action",
                "action": {
                    "type": "postback",
                    "label": label,
                    "data": (
                        self._action_signer.create(
                            proposal.id,
                            line_user_id,
                            decision,
                            int(proposal.expires_at.timestamp()),
                        )
                    ),
                    "displayText": label,
                },
            }
            for label, decision in (("投稿", "approve"), ("投稿しない", "reject"))
        ]
        await self._push_many(
            line_user_id,
            [
                {"type": "text", "text": summary},
                {
                    "type": "template",
                    "altText": "明日の提案を承認または却下してください。",
                    "template": {
                        "type": "buttons",
                        "text": "この提案をStravaへ反映しますか？",
                        "actions": [item["action"] for item in items],
                    },
                },
            ],
        )

    async def _push(
        self, line_user_id: str, message: dict, retry_key: str | None = None
    ) -> None:
        await self._push_many(line_user_id, [message], retry_key=retry_key)

    async def _push_many(
        self,
        line_user_id: str,
        messages: list[dict],
        retry_key: str | None = None,
    ) -> None:
        if await self._try_reply(messages):
            return
        try:
            headers = {"Authorization": f"Bearer {self._token}"}
            if retry_key is not None:
                headers["X-Line-Retry-Key"] = retry_key
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self.push_url,
                    headers=headers,
                    json={"to": line_user_id, "messages": messages},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _line_api_error(exc) from exc

    async def _try_reply(self, messages: list[dict]) -> bool:
        reply_token = _reply_token.get()
        if not reply_token:
            return False
        _reply_token.set(None)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self.reply_url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"replyToken": reply_token, "messages": messages},
                )
                response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                logger.info("line_reply_rejected falling_back_to_push")
                return False
            raise _line_api_error(exc) from exc
        except httpx.HTTPError as exc:
            raise _line_api_error(exc) from exc


def _line_api_error(exc: httpx.HTTPError) -> LineApiError:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    body = ""
    response = getattr(exc, "response", None)
    if response is not None:
        body = (response.text or "")[:200]
    logger.warning("line_http_error status=%s body=%s", status, body)
    return LineApiError("LINE message failed", status_code=status)


class InMemoryConditionPromptSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Activity]] = []
        self.texts: list[tuple[str, str]] = []
        self.proposals: list[tuple[str, WorkoutProposal]] = []
        self.proposal_publish_flags: list[bool] = []
        self.quick_replies: list[tuple[str, str, list[tuple[str, str]]]] = []
        self.settings_links: list[tuple[str, str]] = []
        self.weekly_plan_links: list[tuple[str, str]] = []

    async def send(self, line_user_id: str, activity: Activity) -> None:
        self.sent.append((line_user_id, activity))

    async def send_text(self, line_user_id: str, text: str) -> None:
        self.texts.append((line_user_id, text))

    async def send_quick_reply(
        self, line_user_id: str, text: str, choices: list[tuple[str, str]]
    ) -> None:
        self.quick_replies.append((line_user_id, text, choices))

    async def send_settings_link(self, line_user_id: str, url: str) -> None:
        self.settings_links.append((line_user_id, url))

    async def send_weekly_plan_link(self, line_user_id: str, url: str) -> None:
        self.weekly_plan_links.append((line_user_id, url))

    async def send_proposal(
        self,
        line_user_id: str,
        proposal: WorkoutProposal,
        *,
        publish_to_strava: bool = True,
    ) -> None:
        self.proposals.append((line_user_id, proposal))
        self.proposal_publish_flags.append(publish_to_strava)
