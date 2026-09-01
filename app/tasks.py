import hashlib
import json
from typing import Protocol

from app.domain.events import StravaWebhookEvent


class ActivityTaskPublisher(Protocol):
    async def publish(self, event: StravaWebhookEvent) -> None: ...


class InMemoryActivityTaskPublisher:
    def __init__(self) -> None:
        self.events: list[StravaWebhookEvent] = []

    async def publish(self, event: StravaWebhookEvent) -> None:
        self.events.append(event)


class CloudTasksActivityPublisher:
    def __init__(
        self,
        client: object,
        queue_path: str,
        worker_url: str,
        service_account_email: str,
    ):
        self._client = client
        self._queue_path = queue_path
        self._worker_url = worker_url
        self._service_account_email = service_account_email

    async def publish(self, event: StravaWebhookEvent) -> None:
        from google.cloud import tasks_v2

        task_id = hashlib.sha256(event.event_key.encode()).hexdigest()
        task = tasks_v2.Task(
            name=f"{self._queue_path}/tasks/{task_id}",
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=f"{self._worker_url.rstrip('/')}/tasks/activities/ingest",
                headers={"Content-Type": "application/json"},
                body=json.dumps(event.model_dump()).encode(),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self._service_account_email,
                    audience=self._worker_url,
                ),
            ),
        )
        await self._client.create_task(parent=self._queue_path, task=task)


class ProposalDecisionPublisher(Protocol):
    async def publish_decision(self, task: "ProposalDecisionTask") -> None: ...


class InMemoryProposalDecisionPublisher:
    def __init__(self) -> None:
        self.items: list[ProposalDecisionTask] = []
        self._keys: set[tuple[str, str]] = set()

    async def publish_decision(self, task: "ProposalDecisionTask") -> None:
        key = (task.proposal_id, task.decision)
        if key not in self._keys:
            self._keys.add(key)
            self.items.append(task)


class CloudTasksProposalDecisionPublisher:
    def __init__(
        self,
        client: object,
        queue_path: str,
        worker_url: str,
        service_account_email: str,
    ):
        self._client = client
        self._queue_path = queue_path
        self._worker_url = worker_url
        self._service_account_email = service_account_email

    async def publish_decision(self, task: "ProposalDecisionTask") -> None:
        from google.cloud import tasks_v2

        task_id = hashlib.sha256(
            f"proposal:{task.proposal_id}:{task.decision}".encode()
        ).hexdigest()
        request = tasks_v2.Task(
            name=f"{self._queue_path}/tasks/{task_id}",
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=f"{self._worker_url.rstrip('/')}/tasks/proposals/decide",
                headers={"Content-Type": "application/json"},
                body=json.dumps(
                    {
                        "proposal_id": task.proposal_id,
                        "line_user_id": task.line_user_id,
                        "decision": task.decision,
                    }
                ).encode(),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self._service_account_email,
                    audience=self._worker_url,
                ),
            ),
        )
        from google.api_core.exceptions import AlreadyExists

        try:
            await self._client.create_task(parent=self._queue_path, task=request)
        except AlreadyExists:
            return


from app.approval import ProposalDecisionTask
