from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs

import httpx

MANAGED_NAME_PREFIX = "ai-coach-rich-menu-v1-"
SUPPORTED_MENU_VERSION = "1"


class RichMenuDefinitionError(ValueError):
    pass


class RichMenuApiError(RuntimeError):
    pass


class MenuActionError(ValueError):
    pass


MENU_MESSAGES = {
    "today_proposal": (
        "今日の提案は、直近のアクティビティ後に体調を記録すると作成されます。"
        "届いている最新の提案メッセージを確認してください。"
    ),
    "condition": (
        "体調記録は、アクティビティ後に届く体調確認メッセージから入力できます。"
        "リッチメニューからの単独記録は準備中です。"
    ),
    "manual_activity": (
        "運動の手動記録は準備中です。現時点ではStravaに記録された"
        "アクティビティを利用してください。ここからStravaを直接更新することはありません。"
    ),
    "goals": "「目標確認」で現在の目標を確認できます。登録する場合は「目標登録」と送信してください。",
    "progress": (
        "記録・進捗画面は準備中です。アクティビティ後の提案メッセージで"
        "直近のフィードバックを確認できます。"
    ),
    "settings": (
        "設定画面は準備中です。Stravaを連携する場合は「Strava連携」、"
        "運動環境を確認する場合は「運動環境確認」と送信してください。"
    ),
}


class MenuMessenger(Protocol):
    async def send_text(self, line_user_id: str, text: str) -> None: ...


class MenuActionRouter:
    def __init__(self, messenger: MenuMessenger) -> None:
        self._messenger = messenger

    async def handle(self, line_user_id: str, data: str) -> bool:
        values = _single_value_query(data)
        if values.get("action") != "menu":
            return False
        if values.get("version") != SUPPORTED_MENU_VERSION:
            raise MenuActionError(
                "このメニューは現在利用できません。LINEのトーク画面を開き直してください。"
            )
        target = values.get("target", "")
        message = MENU_MESSAGES.get(target)
        if message is None:
            raise MenuActionError("選択されたメニュー項目を確認できませんでした。")
        await self._messenger.send_text(line_user_id, message)
        return True


def _single_value_query(data: str) -> dict[str, str]:
    parsed = parse_qs(data, keep_blank_values=True, strict_parsing=False)
    return {key: values[0] for key, values in parsed.items() if len(values) == 1}


@dataclass(frozen=True)
class RichMenuDefinition:
    image_path: Path
    payload: dict[str, Any]
    managed_name: str


def load_rich_menu_definition(config_path: Path) -> RichMenuDefinition:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if raw.get("schemaVersion") != 1:
        raise RichMenuDefinitionError("schemaVersion must be 1")
    image_path = (config_path.parent / str(raw.get("image", ""))).resolve()
    payload = raw.get("richMenu")
    if not isinstance(payload, dict):
        raise RichMenuDefinitionError("richMenu must be an object")
    validate_rich_menu_payload(payload)
    if not image_path.is_file():
        raise RichMenuDefinitionError(f"image does not exist: {image_path}")
    image = image_path.read_bytes()
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RichMenuDefinitionError("image must be PNG")
    if len(image) > 1024 * 1024:
        raise RichMenuDefinitionError("image must not exceed 1 MB")
    image_size = struct.unpack(">II", image[16:24])
    menu_size = (payload["size"]["width"], payload["size"]["height"])
    if image_size != menu_size:
        raise RichMenuDefinitionError("image dimensions must match richMenu size")
    canonical_payload = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    digest = hashlib.sha256(canonical_payload + b"\0" + image).hexdigest()[:16]
    named_payload = dict(payload)
    named_payload["name"] = f"{MANAGED_NAME_PREFIX}{digest}"
    return RichMenuDefinition(image_path, named_payload, named_payload["name"])


def validate_rich_menu_payload(payload: dict[str, Any]) -> None:
    size = payload.get("size", {})
    width, height = size.get("width"), size.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise RichMenuDefinitionError("size must contain integer width and height")
    if width < 800 or width > 2500 or height < 250 or width / height < 1.45:
        raise RichMenuDefinitionError("size is outside LINE rich-menu limits")
    areas = payload.get("areas")
    if not isinstance(areas, list) or not 1 <= len(areas) <= 20:
        raise RichMenuDefinitionError("areas must contain between 1 and 20 items")
    rectangles: list[tuple[int, int, int, int]] = []
    for area in areas:
        bounds = area.get("bounds", {}) if isinstance(area, dict) else {}
        values = tuple(bounds.get(key) for key in ("x", "y", "width", "height"))
        if any(not isinstance(value, int) for value in values):
            raise RichMenuDefinitionError("area bounds must be integers")
        x, y, area_width, area_height = values
        if x < 0 or y < 0 or area_width <= 0 or area_height <= 0:
            raise RichMenuDefinitionError("area bounds must be positive and in range")
        if x + area_width > width or y + area_height > height:
            raise RichMenuDefinitionError("area exceeds image boundaries")
        for left, top, right, bottom in rectangles:
            if x < right and x + area_width > left and y < bottom and y + area_height > top:
                raise RichMenuDefinitionError("areas must not overlap")
        rectangles.append((x, y, x + area_width, y + area_height))
        action = area.get("action", {})
        if action.get("type") != "postback" or not action.get("data"):
            raise RichMenuDefinitionError("every area must have a postback action")


class LineRichMenuApi:
    api_base = "https://api.line.me"
    data_base = "https://api-data.line.me"

    def __init__(self, token: str, timeout_seconds: float = 15.0) -> None:
        if not token:
            raise ValueError("LINE channel access token is required")
        self._token = token
        self._timeout = timeout_seconds

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})},
                    **kwargs,
                )
                response.raise_for_status()
                return response
        except httpx.HTTPError as exc:
            raise RichMenuApiError("LINE Rich Menu API request failed") from exc

    async def list_menus(self) -> list[dict[str, Any]]:
        response = await self._request("GET", f"{self.api_base}/v2/bot/richmenu/list")
        return list(response.json().get("richmenus", []))

    async def get_default(self) -> str | None:
        try:
            response = await self._request("GET", f"{self.api_base}/v2/bot/user/all/richmenu")
        except RichMenuApiError as exc:
            cause = exc.__cause__
            if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 404:
                return None
            raise
        return str(response.json().get("richMenuId", "")) or None

    async def create(self, payload: dict[str, Any]) -> str:
        response = await self._request("POST", f"{self.api_base}/v2/bot/richmenu", json=payload)
        rich_menu_id = str(response.json().get("richMenuId", ""))
        if not rich_menu_id:
            raise RichMenuApiError("LINE Rich Menu API returned no richMenuId")
        return rich_menu_id

    async def upload_image(self, rich_menu_id: str, image: bytes) -> None:
        await self._request(
            "POST",
            f"{self.data_base}/v2/bot/richmenu/{rich_menu_id}/content",
            headers={"Content-Type": "image/png"},
            content=image,
        )

    async def set_default(self, rich_menu_id: str) -> None:
        await self._request("POST", f"{self.api_base}/v2/bot/user/all/richmenu/{rich_menu_id}")

    async def delete(self, rich_menu_id: str) -> None:
        await self._request("DELETE", f"{self.api_base}/v2/bot/richmenu/{rich_menu_id}")


class RichMenuApi(Protocol):
    async def list_menus(self) -> list[dict[str, Any]]: ...
    async def get_default(self) -> str | None: ...
    async def create(self, payload: dict[str, Any]) -> str: ...
    async def upload_image(self, rich_menu_id: str, image: bytes) -> None: ...
    async def set_default(self, rich_menu_id: str) -> None: ...
    async def delete(self, rich_menu_id: str) -> None: ...


async def sync_rich_menu(
    api: RichMenuApi, definition: RichMenuDefinition, *, dry_run: bool = False
) -> list[str]:
    menus = await api.list_menus()
    matches = [menu for menu in menus if menu.get("name") == definition.managed_name]
    stale = [
        menu for menu in menus
        if str(menu.get("name", "")).startswith(MANAGED_NAME_PREFIX)
        and menu.get("name") != definition.managed_name
    ]
    actions: list[str] = []
    rich_menu_id = str(matches[0].get("richMenuId", "")) if matches else ""
    if not rich_menu_id:
        actions.extend(["create", "upload-image"])
        if dry_run:
            rich_menu_id = "<new-rich-menu-id>"
        else:
            rich_menu_id = await api.create(definition.payload)
            try:
                await api.upload_image(rich_menu_id, definition.image_path.read_bytes())
            except RichMenuApiError as upload_error:
                try:
                    await api.delete(rich_menu_id)
                except RichMenuApiError as cleanup_error:
                    raise upload_error from cleanup_error
                raise
    default_id = await api.get_default()
    if default_id != rich_menu_id:
        actions.append("set-default")
        if not dry_run:
            await api.set_default(rich_menu_id)
    for menu in matches[1:] + stale:
        old_id = str(menu.get("richMenuId", ""))
        if old_id and old_id != rich_menu_id:
            actions.append(f"delete:{old_id}")
            if not dry_run:
                await api.delete(old_id)
    return actions
