import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from token_usage import TokenUsageStore


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".usage-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.chats = self.root / "chats"; self.chats.mkdir()
        self.store = TokenUsageStore(self.root / "usage.jsonl")

    def test_restart_retains_counts_and_deduplicates_message_ids(self):
        metrics = {"prompt_tokens": 100, "completion_tokens": 20, "token_counts_estimated": False}
        self.store.record("chat", "answer", metrics)
        restarted = TokenUsageStore(self.store.path)
        restarted.record("chat", "answer", metrics)
        result = restarted.snapshot(self.chats, "chat")
        self.assertEqual(result["totals"]["responses"], 1)
        self.assertEqual(result["totals"]["estimated_responses"], 0)
        self.assertEqual(result["records"][0]["completion_tokens"], 20)

    def test_old_metrics_backfill_once_and_missing_metrics_are_not_invented(self):
        events = [{"type": "created", "title": "Old chat"},
                  {"type": "assistant", "time": "2025-02-03T10:00:00", "metrics": {"prompt_tokens": 40, "completion_tokens": 10}},
                  {"type": "assistant", "content": "No metrics"}]
        (self.chats / "old.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
        first = self.store.snapshot(self.chats)
        self.assertEqual(first["totals"]["responses"], 1)
        self.assertEqual(first["chats"][0]["title"], "Old chat")
        self.assertEqual(self.store.snapshot(self.chats)["totals"], first["totals"])

    def test_legacy_branch_copies_are_excluded(self):
        events = [{"type": "branched"},
                  {"type": "assistant", "message_id": "copied", "time": "2025-02-03T10:00:00", "metrics": {"prompt_tokens": 40, "completion_tokens": 10}},
                  {"type": "settings", "values": {"model": "local.gguf"}},
                  {"type": "assistant", "message_id": "fresh", "time": "2025-02-04T10:00:00", "metrics": {"prompt_tokens": 90, "completion_tokens": 30}}]
        (self.chats / "branch.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
        result = self.store.snapshot(self.chats)
        self.assertEqual(result["totals"]["responses"], 1)
        self.assertEqual(result["totals"]["prompt_tokens"], 90)

    def test_malformed_records_and_incomplete_tail_do_not_hide_later_usage(self):
        self.store.path.write_text('bad json\n{"incomplete":', encoding="utf-8")
        self.store.record("chat", "one", {"prompt_tokens": 8, "completion_tokens": 2})
        self.assertEqual(self.store.snapshot(self.chats)["totals"]["responses"], 1)
        for invalid in (None, {}, {"prompt_tokens": -1, "completion_tokens": 1}, {"prompt_tokens": True, "completion_tokens": 1}):
            self.store.record("chat", "invalid", invalid)
        self.assertEqual(self.store.snapshot(self.chats)["totals"]["responses"], 1)

    def test_write_failure_preserves_existing_usage(self):
        self.store.record("chat", "one", {"prompt_tokens": 8, "completion_tokens": 2})
        before = self.store.path.read_bytes()
        with patch.object(Path, "open", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.store.record("chat", "two", {"prompt_tokens": 8, "completion_tokens": 2})
        self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
