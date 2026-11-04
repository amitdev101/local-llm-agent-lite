"""Persistent generation usage, separate from editable/deletable chat history."""

import json
import threading
from datetime import datetime
from pathlib import Path


class TokenUsageStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    @staticmethod
    def entry(chat_id, message_id, metrics, status="complete", timestamp=None):
        if not isinstance(chat_id, str) or not chat_id or not isinstance(message_id, str) or not message_id:
            return None
        if not isinstance(metrics, dict):
            return None
        counts = [metrics.get(key) for key in ("prompt_tokens", "completion_tokens")]
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
            return None
        timestamp = timestamp or datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            datetime.fromisoformat(timestamp)
        except (ValueError, TypeError):
            return None
        return {"id": message_id, "chat_id": chat_id, "time": timestamp,
                "prompt_tokens": counts[0], "completion_tokens": counts[1],
                "status": status, "estimated": metrics.get("token_counts_estimated", metrics.get("estimated", True))}

    def _read(self):
        if not self.path.exists():
            return []
        records, seen = [], set()
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                clean = self.entry(row["chat_id"], row["id"], row, row.get("status", "complete"), row["time"])
                if clean and clean["id"] not in seen:
                    records.append(clean)
                    seen.add(clean["id"])
            except (ValueError, TypeError, KeyError):
                continue
        return records

    def _append(self, candidates):
        with self.lock:
            records = self._read()
            seen = {row["id"] for row in records}
            pending = []
            for row in candidates:
                if row and row["id"] not in seen:
                    pending.append(row)
                    seen.add(row["id"])
            if pending:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("ab+") as handle:
                    if handle.tell():
                        handle.seek(-1, 2)
                        if handle.read(1) != b"\n":
                            handle.write(b"\n")
                    handle.write(("\n".join(json.dumps(row) for row in pending) + "\n").encode("utf-8"))
            return records + pending

    def record(self, chat_id, message_id, metrics, status="complete", timestamp=None):
        self._append([self.entry(chat_id, message_id, metrics, status, timestamp)])

    def snapshot(self, chats_dir: Path, chat_id=None):
        """Backfill recorded legacy metrics; inherited branch messages add no work."""
        candidates, titles = [], {}
        with self.lock:
            for path in sorted(chats_dir.glob("*.jsonl")):
                inherited = False
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except FileNotFoundError:
                    continue
                for index, line in enumerate(lines):
                    try:
                        event = json.loads(line)
                        if event.get("type") in {"created", "renamed"}:
                            titles[path.stem] = event.get("title", "New chat")
                        if event.get("type") == "branched":
                            inherited = True
                        if event.get("type") == "settings" and event.get("values", {}).get("model"):
                            inherited = False
                        if event.get("type") != "assistant" or inherited or event.get("usage_inherited"):
                            continue
                        identifier = event.get("message_id") or f"{path.stem}:legacy-{index}"
                        candidates.append(self.entry(path.stem, identifier, event.get("metrics"),
                                                     event.get("status", "complete"), event.get("time", "")))
                    except (ValueError, TypeError, AttributeError):
                        continue
            records = self._append(candidates)
        selected = [row for row in records if chat_id is None or row["chat_id"] == chat_id]
        chats = {}
        for row in selected:
            summary = chats.setdefault(row["chat_id"], {"id": row["chat_id"], "title": titles.get(row["chat_id"], "Deleted chat"),
                                                       "prompt_tokens": 0, "completion_tokens": 0,
                                                       "responses": 0, "estimated_responses": 0})
            for key in ("prompt_tokens", "completion_tokens"):
                summary[key] += row[key]
            summary["responses"] += 1
            summary["estimated_responses"] += bool(row["estimated"])
        totals = {key: sum(row[key] for row in chats.values())
                  for key in ("prompt_tokens", "completion_tokens", "responses", "estimated_responses")}
        return {"chat_id": chat_id, "chats": sorted(chats.values(), key=lambda row: row["prompt_tokens"] + row["completion_tokens"], reverse=True),
                "records": selected if chat_id else [], "totals": totals}
