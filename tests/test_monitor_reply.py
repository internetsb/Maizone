"""Exercise the real monitor loop without a QQ session or network access."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import ast
import asyncio
import unittest


def load_loop():
    source = Path(__file__).resolve().parents[1] / "tasks.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    monitor = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FeedMonitor")
    method = next(node for node in monitor.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_monitor_loop")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_monitor_loop"], namespace


class MonitorReplyTests(unittest.IsolatedAsyncioTestCase):
    async def run_cycle(self, authority, enabled=True, silent=False):
        loop, namespace = load_loop()
        monitor = SimpleNamespace(
            is_running=True,
            config=SimpleNamespace(
                auto_read=SimpleNamespace(interval=1, silent_duration=[]),
                authority=SimpleNamespace(auto_read_authority_type=authority, auto_read_whitelist=["fixture-user"]),
                auto_reply=SimpleNamespace(enable_auto_reply=enabled),
            ),
        )

        async def sleep(_):
            monitor.is_running = False

        read = AsyncMock(return_value=(True, []))
        general = AsyncMock()
        reply = AsyncMock()
        namespace.update(
            asyncio=SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
            logger=Mock(), read_feed=read, monitor_read_feed=general, reply_feed=reply,
            _is_in_silent_period=lambda _: silent,
        )
        await loop(monitor)
        return read, general, reply

    async def test_whitelist_keeps_own_comment_replies(self):
        read, general, reply = await self.run_cycle("whitelist")
        read.assert_awaited_once_with("fixture-user")
        general.assert_not_awaited()
        reply.assert_awaited_once_with()

    async def test_disabled_reply_stays_disabled(self):
        _, general, reply = await self.run_cycle("whitelist", enabled=False)
        general.assert_not_awaited()
        reply.assert_not_awaited()

    async def test_other_modes_keep_general_scan(self):
        _, general, reply = await self.run_cycle("blacklist")
        general.assert_awaited_once_with()
        reply.assert_awaited_once_with()

    async def test_silent_period_skips_both_read_and_reply(self):
        read, general, reply = await self.run_cycle("whitelist", silent=True)
        read.assert_not_awaited()
        general.assert_not_awaited()
        reply.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
