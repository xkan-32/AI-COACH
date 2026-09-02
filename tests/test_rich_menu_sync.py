from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from app.line_menu import (
    LineRichMenuApi,
    RichMenuApiError,
    load_rich_menu_definition,
    sync_rich_menu,
)

CONFIG = Path(__file__).resolve().parents[1] / "config/line-rich-menu/rich-menu-v1.json"


@dataclass
class FakeApi:
    menus: list[dict] = field(default_factory=list)
    default_id: str | None = None
    calls: list[tuple] = field(default_factory=list)
    fail_upload: bool = False

    async def list_menus(self) -> list[dict]:
        return list(self.menus)

    async def get_default(self) -> str | None:
        return self.default_id

    async def create(self, payload: dict) -> str:
        self.calls.append(("create", payload["name"]))
        self.menus.append({"richMenuId": "new-id", "name": payload["name"]})
        return "new-id"

    async def upload_image(self, rich_menu_id: str, image: bytes) -> None:
        self.calls.append(("upload", rich_menu_id, len(image)))
        if self.fail_upload:
            raise RichMenuApiError("upload failed")

    async def set_default(self, rich_menu_id: str) -> None:
        self.calls.append(("default", rich_menu_id))
        self.default_id = rich_menu_id

    async def delete(self, rich_menu_id: str) -> None:
        self.calls.append(("delete", rich_menu_id))
        self.menus = [menu for menu in self.menus if menu["richMenuId"] != rich_menu_id]


async def test_sync_is_idempotent_and_removes_managed_stale_menu() -> None:
    definition = load_rich_menu_definition(CONFIG)
    api = FakeApi(
        menus=[{"richMenuId": "old-id", "name": "ai-coach-rich-menu-v1-old"}],
        default_id="old-id",
    )
    first = await sync_rich_menu(api, definition)
    second = await sync_rich_menu(api, definition)
    assert first == ["create", "upload-image", "set-default", "delete:old-id"]
    assert second == []
    assert [call[0] for call in api.calls] == ["create", "upload", "default", "delete"]


async def test_dry_run_does_not_modify_remote_state() -> None:
    definition = load_rich_menu_definition(CONFIG)
    api = FakeApi()
    actions = await sync_rich_menu(api, definition, dry_run=True)
    assert actions == ["create", "upload-image", "set-default"]
    assert api.calls == []
    assert api.menus == []


async def test_upload_error_deletes_incomplete_menu_for_safe_retry() -> None:
    definition = load_rich_menu_definition(CONFIG)
    api = FakeApi(fail_upload=True)
    with pytest.raises(RichMenuApiError, match="upload failed"):
        await sync_rich_menu(api, definition)
    assert api.menus == []
    assert [call[:2] for call in api.calls] == [
        ("create", definition.managed_name),
        ("upload", "new-id"),
        ("delete", "new-id"),
    ]


async def test_api_error_does_not_expose_token(monkeypatch) -> None:
    token = "super-secret-channel-token"

    async def failing_request(self, method, url, **kwargs):
        request = httpx.Request(method, url)
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("failed", request=request, response=response)

    monkeypatch.setattr(httpx.AsyncClient, "request", failing_request)
    with pytest.raises(RichMenuApiError) as captured:
        await LineRichMenuApi(token).list_menus()
    assert token not in str(captured.value)
