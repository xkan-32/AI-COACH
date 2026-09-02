from typing import Literal

from pydantic import BaseModel, Field


class StravaWebhookEvent(BaseModel):
    object_type: Literal["activity", "athlete"]
    object_id: int = Field(gt=0)
    aspect_type: Literal["create", "update", "delete"]
    owner_id: int = Field(gt=0)
    subscription_id: int = Field(gt=0)
    event_time: int = Field(gt=0)
    updates: dict[str, object] = Field(default_factory=dict)

    @property
    def event_key(self) -> str:
        parts = (
            str(self.subscription_id),
            self.object_type,
            str(self.object_id),
            self.aspect_type,
        )
        if self.is_new_activity:
            return ":".join(parts)
        return ":".join((*parts, str(self.event_time)))

    @property
    def is_new_activity(self) -> bool:
        return self.object_type == "activity" and self.aspect_type == "create"
