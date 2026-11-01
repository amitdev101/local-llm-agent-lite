"""Recovery handler checks with temporary storage and a fake model.

Run: .venv/Scripts/python.exe -m unittest discover -s local_llm_playground/tests
"""

import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

class FakeModel:
    def __init__(self, chunks=("A complete answer",), failure=None, cancel=None):
        self.chunks = chunks
        self.failure = failure
        self.cancel = cancel
        self.calls = []

    def tokenize(self, value):
        return list(value)

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        for chunk in self.chunks:
            yield {"choices": [{"delta": {"content": chunk}}]}
        if self.cancel:
            self.cancel.set()
        if self.failure:
            raise RuntimeError(self.failure)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tests_dir = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(prefix=".recovery-", dir=tests_dir)
        root = Path(self.temp.name).resolve()
        root.relative_to(tests_dir)
        self.addCleanup(self.temp.cleanup)
        (root / "frontend").mkdir()
        # Execute the actual module with a temporary __file__ so import-time
        # directory/log initialization cannot touch personal chats or models.
        module = types.ModuleType("playground_recovery_test")
        module.__file__ = str(root / "backend.py")
        source = Path(__file__).resolve().parents[1] / "backend.py"
        with patch.dict(sys.modules, {module.__name__: module,
                                      "llama_cpp": types.SimpleNamespace(Llama=object)}), \
                patch("logging.basicConfig"), \
                patch("logging.FileHandler", return_value=logging.NullHandler()):
            exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), module.__dict__)
        self.backend = module
        self.backend.logger = logging.getLogger("playground.recovery.tests")
        self.backend.logger.disabled = True
        self.backend.runtime.llm = FakeModel()
        self.backend.runtime.status = "ready"
        self.backend.runtime.model_path = "/models/test.gguf"
        self.chat_id = module.create_chat(module.ChatCreate())["id"]
        self.request = {"message": "Explain this", "request_id": "request-1",
                        "temperature": 0.6, "thinking": True, "system_prompt": "Be concise"}

    def generate(self, request=None):
        with patch.object(self.backend, "StreamingResponse", side_effect=lambda content, **kw: content):
            stream = self.backend.generate(self.chat_id, self.backend.GenerateRequest(**(request or self.request)))
        result = [json.loads(line) for line in stream]
        self.assertFalse(self.backend.runtime.generation_lock.locked())
        return result

    def messages(self):
        return self.backend.get_chat(self.chat_id)["messages"]

    def test_partial_failure_retries_original_request_without_duplicate_user(self):
        self.backend.runtime.llm = FakeModel(("Partial answer",), "Model ran out of memory")
        self.assertEqual(self.generate()[-1]["type"], "error")
        failed = self.messages()[-1]
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["content"], "Partial answer")
        self.assertEqual(failed["error"], "Model ran out of memory")

        model = FakeModel()
        self.backend.runtime.llm = model
        retry = {**failed["request"], "temperature": 2, "thinking": False,
                 "system_prompt": "Changed since the attempt"}
        self.assertEqual(self.generate(retry)[-1]["type"], "done")
        messages = self.messages()
        self.assertEqual([item["type"] for item in messages], ["user", "assistant", "assistant"])
        self.assertEqual(messages[1]["content"], "Partial answer")
        self.assertEqual(messages[-1]["status"], "complete")
        self.assertEqual(model.calls[0]["temperature"], 0.6)
        self.assertEqual(model.calls[0]["messages"], [
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "Explain this\n\n/think"},
        ])

    def test_failure_before_first_token_can_be_retried_repeatedly(self):
        self.backend.runtime.llm = FakeModel((), "Failed before first token")
        for _ in range(3):
            self.assertEqual(self.generate()[-1]["type"], "error")
        self.assertEqual(sum(item["type"] == "user" for item in self.messages()), 1)
        self.assertEqual(len(self.messages()), 4)
        self.backend.runtime.llm = FakeModel()
        self.assertEqual(self.generate()[-1]["type"], "done")

    def test_lost_success_is_replayed_without_another_generation_or_events(self):
        self.generate()
        model = self.backend.runtime.llm
        before = self.backend.read_events(self.chat_id)
        replay = self.generate()
        self.assertEqual(replay[-1]["type"], "done")
        self.assertEqual(replay[1]["text"], "A complete answer")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(self.backend.read_events(self.chat_id), before)

    def test_legacy_chat_can_recover_after_reopening(self):
        self.backend.append_event(self.chat_id, {"type": "settings", "values": {
            "temperature": 0.35, "system_prompt": "Original instructions"}})
        self.backend.append_event(self.chat_id, {"type": "user", "content": "Old prompt"})
        self.backend.append_event(self.chat_id, {"type": "assistant", "content": "Old partial",
                                                 "status": "error", "error": "Original error"})
        request = self.messages()[-1]["request"]
        self.assertTrue(request["request_id"].startswith("legacy-"))
        self.assertEqual(self.messages()[-1]["request"], request)
        self.generate(request)
        self.assertEqual(sum(item["type"] == "user" for item in self.messages()), 1)
        self.assertEqual(self.backend.runtime.llm.calls[0]["temperature"], 0.35)

    def test_stopped_attempt_keeps_partial_and_reuses_user_on_retry(self):
        self.backend.runtime.llm = FakeModel(("Stopped partial",), cancel=self.backend.runtime.cancel)
        self.assertEqual(self.generate()[-1]["type"], "stopped")
        self.assertEqual(self.messages()[-1]["status"], "interrupted")
        self.backend.runtime.llm = FakeModel()
        self.generate(self.messages()[-1]["request"])
        self.assertEqual([m["content"] for m in self.messages()],
                         ["Explain this", "Stopped partial", "A complete answer"])

    def test_old_retries_and_mismatched_prompt_are_rejected_without_lock_leak(self):
        self.generate()
        with self.assertRaises(self.backend.HTTPException) as changed:
            self.generate({**self.request, "message": "Another prompt"})
        self.assertEqual(changed.exception.status_code, 409)
        self.generate({**self.request, "request_id": "request-2", "message": "Next prompt"})
        with self.assertRaises(self.backend.HTTPException) as stale:
            self.generate()
        self.assertEqual(stale.exception.status_code, 409)
        self.assertFalse(self.backend.runtime.generation_lock.locked())
        self.assertEqual(sum(item["type"] == "user" for item in self.messages()), 2)

    def test_preparation_errors_release_lock_before_a_subsequent_request(self):
        with self.assertRaises(self.backend.HTTPException) as missing:
            self.backend.generate("00000000-0000-0000-0000-000000000000",
                                  self.backend.GenerateRequest(**self.request))
        self.assertEqual(missing.exception.status_code, 404)
        self.assertFalse(self.backend.runtime.generation_lock.locked())
        with self.assertRaises(self.backend.HTTPException) as blank:
            self.generate({**self.request, "message": "  "})
        self.assertEqual(blank.exception.status_code, 400)
        self.generate()

    def test_failed_turns_are_not_sent_as_unanswered_history(self):
        self.backend.runtime.llm = FakeModel(("Failed partial",), "Failed")
        self.generate()
        model = FakeModel()
        self.backend.runtime.llm = model
        self.generate({**self.request, "message": "New question", "request_id": "request-2"})
        self.assertEqual(len(model.calls[0]["messages"]), 2)
        self.assertEqual(model.calls[0]["messages"][-1]["content"], "New question\n\n/think")

    def test_closing_stream_keeps_partial_and_releases_lock(self):
        with patch.object(self.backend, "StreamingResponse", side_effect=lambda content, **kw: content):
            stream = self.backend.generate(self.chat_id, self.backend.GenerateRequest(**self.request))
        self.assertEqual(json.loads(next(stream))["type"], "start")
        self.assertEqual(json.loads(next(stream))["type"], "token")
        stream.close()
        self.assertFalse(self.backend.runtime.generation_lock.locked())
        self.assertEqual(self.messages()[-1]["status"], "interrupted")
        self.assertEqual(self.messages()[-1]["content"], "A complete answer")

    def test_closing_after_done_does_not_append_an_interrupted_attempt(self):
        with patch.object(self.backend, "StreamingResponse", side_effect=lambda content, **kw: content):
            stream = self.backend.generate(self.chat_id, self.backend.GenerateRequest(**self.request))
        while json.loads(next(stream))["type"] != "done":
            pass
        self.assertFalse(self.backend.runtime.generation_lock.locked())
        stream.close()
        self.assertEqual(len(self.messages()), 2)
        self.assertEqual(self.messages()[-1]["status"], "complete")
        self.assertFalse(self.backend.runtime.generation_lock.locked())


if __name__ == "__main__":
    unittest.main()
