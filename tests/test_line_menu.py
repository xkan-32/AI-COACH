import json
import struct
from pathlib import Path

import pytest

from app.line import InMemoryConditionPromptSender
from app.line_menu import (
    CONDITION_ACTIVITY_POSTBACK,
    CONDITION_HUB_PROMPT,
    MENU_MESSAGES,
    WEIGHT_START_POSTBACK,
    MenuActionError,
    MenuActionRouter,
    RichMenuDefinitionError,
    load_rich_menu_definition,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/line-rich-menu/rich-menu-v1.json"


@pytest.mark.parametrize(
    "target", sorted(item for item in MENU_MESSAGES if item != "condition")
)
async def test_every_menu_action_responds(target: str) -> None:
    messenger = InMemoryConditionPromptSender()
    handled = await MenuActionRouter(messenger).handle(
        "U123", f"action=menu&version=1&target={target}"
    )
    assert handled is True
    assert messenger.texts == [("U123", MENU_MESSAGES[target])]


async def test_condition_menu_offers_weight_and_condition_choices() -> None:
    messenger = InMemoryConditionPromptSender()
    handled = await MenuActionRouter(messenger).handle(
        "U123", "action=menu&version=1&target=condition"
    )
    assert handled is True
    assert messenger.texts == []
    assert messenger.quick_replies[-1][1] == CONDITION_HUB_PROMPT
    choices = dict(messenger.quick_replies[-1][2])
    assert choices["体重"] == WEIGHT_START_POSTBACK
    assert choices["コンディション"] == CONDITION_ACTIVITY_POSTBACK


async def test_condition_activity_choice_explains_follow_up_flow() -> None:
    messenger = InMemoryConditionPromptSender()
    handled = await MenuActionRouter(messenger).handle(
        "U123", CONDITION_ACTIVITY_POSTBACK
    )
    assert handled is True
    assert messenger.texts == [("U123", MENU_MESSAGES["condition"])]


async def test_non_menu_postback_is_not_consumed() -> None:
    messenger = InMemoryConditionPromptSender()
    handled = await MenuActionRouter(messenger).handle(
        "U123", "action=condition&activity_id=A1&level=good"
    )
    assert handled is False
    assert messenger.texts == []


@pytest.mark.parametrize(
    "data,message",
    [
        ("action=menu&version=2&target=goals", "現在利用できません"),
        ("action=menu&version=1&target=unknown", "確認できませんでした"),
    ],
)
async def test_invalid_menu_action_has_user_facing_error(
    data: str, message: str
) -> None:
    with pytest.raises(MenuActionError, match=message):
        await MenuActionRouter(InMemoryConditionPromptSender()).handle("U123", data)


def test_definition_has_six_non_overlapping_areas_and_expected_actions() -> None:
    definition = load_rich_menu_definition(CONFIG)
    payload = definition.payload
    assert payload["size"] == {"width": 2500, "height": 1686}
    assert len(payload["areas"]) == 6
    assert [area["action"]["data"] for area in payload["areas"]] == [
        "action=menu&version=1&target=today_proposal",
        "action=menu&version=1&target=condition",
        "action=menu&version=1&target=manual_activity",
        "action=menu&version=1&target=goals",
        "action=menu&version=1&target=progress",
        "action=menu&version=1&target=settings",
    ]


async def test_manual_activity_callback_is_used_when_provided() -> None:
    started: list[str] = []

    async def on_start(line_user_id: str) -> None:
        started.append(line_user_id)

    messenger = InMemoryConditionPromptSender()
    handled = await MenuActionRouter(
        messenger, on_manual_activity_requested=on_start
    ).handle("U123", "action=menu&version=1&target=manual_activity")
    assert handled is True
    assert started == ["U123"]
    assert messenger.texts == []


def test_png_dimensions_match_definition() -> None:
    definition = load_rich_menu_definition(CONFIG)
    png = definition.image_path.read_bytes()
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (2500, 1686)
    assert len(png) <= 1024 * 1024


@pytest.mark.parametrize(
    "bounds",
    [
        {"x": -1, "y": 0, "width": 10, "height": 10},
        {"x": 2499, "y": 0, "width": 2, "height": 10},
        {"x": 0, "y": 1685, "width": 10, "height": 2},
        {"x": 0, "y": 0, "width": 0, "height": 10},
    ],
)
def test_definition_rejects_out_of_boundary_area(tmp_path: Path, bounds: dict) -> None:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    raw["image"] = str((ROOT / "assets/line-rich-menu/rich-menu-v1.png").resolve())
    raw["richMenu"]["areas"][0]["bounds"] = bounds
    config = tmp_path / "menu.json"
    config.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RichMenuDefinitionError):
        load_rich_menu_definition(config)
