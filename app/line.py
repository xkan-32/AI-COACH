import httpx

from app.domain.models import Activity, WorkoutProposal
from app.security import ApprovalActionSigner


class LineApiError(RuntimeError):
    pass


class LineConditionPromptSender:
    push_url = "https://api.line.me/v2/bot/message/push"

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
        )

    async def send_text(self, line_user_id: str, text: str) -> None:
        await self._push(line_user_id, {"type": "text", "text": text})

    async def send_proposal(self, line_user_id: str, proposal: WorkoutProposal) -> None:
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
                },
            }
            for label, decision in (("投稿", "approve"), ("投稿しない", "reject"))
        ]
        summary = (
            f"明日の提案: {proposal.title}"
            f"（{proposal.duration_minutes}分・{proposal.intensity}）\n"
            f"{proposal.rationale}"
        )
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

    async def _push(self, line_user_id: str, message: dict) -> None:
        await self._push_many(line_user_id, [message])

    async def _push_many(self, line_user_id: str, messages: list[dict]) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self.push_url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"to": line_user_id, "messages": messages},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LineApiError("LINE message failed") from exc


class InMemoryConditionPromptSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Activity]] = []
        self.texts: list[tuple[str, str]] = []
        self.proposals: list[tuple[str, WorkoutProposal]] = []

    async def send(self, line_user_id: str, activity: Activity) -> None:
        self.sent.append((line_user_id, activity))

    async def send_text(self, line_user_id: str, text: str) -> None:
        self.texts.append((line_user_id, text))

    async def send_proposal(self, line_user_id: str, proposal: WorkoutProposal) -> None:
        self.proposals.append((line_user_id, proposal))
