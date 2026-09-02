#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from app.line_menu import (
    LineRichMenuApi,
    load_rich_menu_definition,
    sync_rich_menu,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LINEリッチメニューを冪等に同期します。")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPOSITORY_ROOT / "config/line-rich-menu/rich-menu-v1.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="変更予定だけを表示します。")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        print("LINE_CHANNEL_ACCESS_TOKEN environment variable is required", file=sys.stderr)
        return 2
    definition = load_rich_menu_definition(args.config.resolve())
    actions = await sync_rich_menu(
        LineRichMenuApi(token), definition, dry_run=args.dry_run
    )
    prefix = "DRY-RUN " if args.dry_run else ""
    if actions:
        for action in actions:
            print(f"{prefix}{action}")
    else:
        print(f"{prefix}already-synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
