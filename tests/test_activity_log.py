from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import activity_log


def run(awaitable):
    return asyncio.run(awaitable)


class ActivityLogTests(unittest.TestCase):
    def test_write_read_filter_limit_and_bad_line(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activity_log.jsonl"
            with patch.object(activity_log, "_LOG_PATH", log_path):
                self.assertTrue(run(activity_log.record("send_feed", feed="first", text="alpha")))
                self.assertTrue(
                    run(activity_log.record("comment", target="Alice", feed="second", text="beta"))
                )
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write("not json\n")
                self.assertTrue(
                    run(activity_log.record("reply_comment", target="Bob", text="gamma"))
                )

                entries = run(activity_log.recent(limit=2))
                self.assertEqual(
                    [entry["kind"] for entry in entries],
                    ["reply_comment", "comment"],
                )
                filtered = run(activity_log.recent(limit=10, keyword="Alice"))
                self.assertEqual(len(filtered), 1)
                self.assertEqual(filtered[0]["text"], "beta")

    def test_trim_keeps_latest_500_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activity_log.jsonl"
            with patch.object(activity_log, "_LOG_PATH", log_path):
                log_path.write_text(
                    "\n".join(
                        json.dumps({"kind": "old", "index": index}) for index in range(500)
                    )
                    + "\n",
                    encoding="utf-8",
                )

                self.assertTrue(run(activity_log.record("new", index=500)))
                lines = log_path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 500)
                self.assertEqual(json.loads(lines[0])["index"], 1)
                self.assertEqual(json.loads(lines[-1])["kind"], "new")

    def test_write_failure_warns_without_raising(self):
        class Logger:
            def __init__(self):
                self.messages = []

            def warning(self, message):
                self.messages.append(message)

        logger = Logger()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(activity_log, "_LOG_PATH", Path(directory)):
                self.assertFalse(run(activity_log.record("send_feed", logger=logger, text="x")))
        self.assertTrue(logger.messages)
        self.assertIn("记录空间活动失败", logger.messages[0])


if __name__ == "__main__":
    unittest.main()
