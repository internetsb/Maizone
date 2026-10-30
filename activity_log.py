"""Non-blocking local activity history for QZone actions."""

from __future__ import annotations

import asyncio
import datetime
import json
import threading
from pathlib import Path
from typing import Any


_LOG_PATH = Path(__file__).resolve().parent / "data" / "activity_log.jsonl"
_MAX_KEEP = 500
_lock = threading.Lock()


def _record_sync(kind: str, fields: dict[str, Any]) -> None:
    entry: dict[str, Any] = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
    }
    for key, value in fields.items():
        text = str(value or "").strip()
        if text:
            entry[key] = text[:500]

    with _lock:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_KEEP:
            _LOG_PATH.write_text("\n".join(lines[-_MAX_KEEP:]) + "\n", encoding="utf-8")


async def record(kind: str, *, logger: Any = None, **fields: Any) -> bool:
    """Append an entry without blocking the event loop.

    Logging is best-effort: a disk failure must not turn a successful QZone
    action into a failed action, but it must remain observable as a warning.
    """
    try:
        await asyncio.to_thread(_record_sync, kind, fields)
        return True
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning(f"记录空间活动失败: {exc}")
        return False


def _recent_sync(limit: int, keyword: str) -> list[dict[str, Any]]:
    try:
        with _lock:
            lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []

    result: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(entry, dict):
            continue
        if keyword and not any(keyword in str(value) for value in entry.values()):
            continue
        result.append(entry)
        if len(result) >= max(1, limit):
            break
    return result


async def recent(limit: int = 10, keyword: str = "") -> list[dict[str, Any]]:
    """Return matching entries newest first without blocking the event loop."""
    return await asyncio.to_thread(_recent_sync, limit, keyword)


_KIND_LABELS = {
    "send_feed": "发了说说",
    "comment": "评论了",
    "skip_comment": "看了没评论",
    "reply_comment": "回复了评论",
}


def format_entries(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "空间活动记录里没有相关内容。"
    lines = []
    for entry in entries:
        parts = [
            str(entry.get("time", "")),
            _KIND_LABELS.get(str(entry.get("kind", "")), str(entry.get("kind", ""))),
        ]
        for key, label in (
            ("target", "对象："),
            ("feed", "说说："),
            ("comment", "对方评论："),
            ("text", "我写的："),
        ):
            if entry.get(key):
                parts.append(label + str(entry[key]))
        lines.append(" | ".join(parts))
    return "\n".join(lines)
