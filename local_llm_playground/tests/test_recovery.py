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
        (root / "model_capabilities.json").write_text('{"test.gguf": "supported"}', encoding="utf-8")
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
        self.backend.PROJECT_ROOT = root
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

    def test_thinking_support_is_explicit_case_insensitive_and_never_guessed(self):
        self.assertEqual(self.backend.thinking_support(r"C:\models\TEST.GGUF"), "supported")
        self.assertEqual(self.backend.thinking_support("/models/Qwen3-other.gguf"), "unknown")
        for content in ['{"test.gguf": "unsupported"}', '{"test.gguf": {"invalid": true}}', '[]', 'invalid json']:
            self.backend.MODEL_CAPABILITIES.write_text(content, encoding="utf-8")
            expected = "unsupported" if '"unsupported"' in content else "unknown"
            self.assertEqual(self.backend.thinking_support("/models/test.gguf"), expected)

    def test_a_missing_capability_file_leaves_generation_available_without_directives(self):
        self.backend.MODEL_CAPABILITIES = self.backend.ROOT / "missing-capabilities.json"
        result = self.generate()
        self.assertEqual(self.backend.runtime.llm.calls[0]["messages"][-1]["content"], "Explain this")
        self.assertEqual(result[-1]["metrics"]["thinking_support"], "unknown")

    def test_unsupported_and_unknown_models_do_not_receive_qwen_directives(self):
        for support in ("unsupported", "unknown"):
            self.backend.MODEL_CAPABILITIES.write_text(json.dumps({"test.gguf": support}), encoding="utf-8")
            result = self.generate({**self.request, "request_id": support})
            self.assertEqual(self.backend.runtime.llm.calls[-1]["messages"][-1]["content"], "Explain this")
            self.assertFalse(result[-1]["metrics"]["thinking"])
            self.assertEqual(result[-1]["metrics"]["thinking_support"], support)
            self.assertTrue(self.messages()[-1]["request"]["thinking"])

    def test_supported_models_receive_no_think_when_preference_is_off(self):
        self.generate({**self.request, "thinking": False})
        self.assertTrue(self.backend.runtime.llm.calls[-1]["messages"][-1]["content"].endswith("/no_think"))

    def test_changed_model_is_rejected_before_any_chat_events_are_written(self):
        before = self.backend.read_events(self.chat_id)
        with self.assertRaises(self.backend.HTTPException) as error:
            self.generate({**self.request, "expected_model_path": "/models/different.gguf"})
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.backend.read_events(self.chat_id), before)
        self.assertFalse(self.backend.runtime.generation_lock.locked())
        self.generate({**self.request, "expected_model_path": self.backend.runtime.model_path})

    def test_retry_with_an_unsupported_loaded_model_keeps_prompt_and_masks_thinking(self):
        self.backend.runtime.llm = FakeModel(("Partial",), "Failed")
        self.generate()
        self.backend.runtime.model_path = "/models/other.gguf"
        self.backend.MODEL_CAPABILITIES.write_text('{"other.gguf": "unsupported"}', encoding="utf-8")
        self.backend.runtime.llm = FakeModel()
        result = self.generate({**self.messages()[-1]["request"], "expected_model_path": "/models/other.gguf"})
        self.assertEqual(self.backend.runtime.llm.calls[0]["messages"][-1]["content"], "Explain this")
        self.assertEqual(sum(item["type"] == "user" for item in self.messages()), 1)
        self.assertFalse(result[-1]["metrics"]["thinking"])

    def test_model_load_holds_lock_and_reports_loading_until_ready(self):
        model = self.backend.ROOT / "test.gguf"
        model.touch()

        def load(**kwargs):
            self.assertTrue(self.backend.runtime.generation_lock.locked())
            self.assertEqual(self.backend.status()["status"], "loading")
            with self.assertRaises(self.backend.HTTPException):
                self.backend.generate(self.chat_id, self.backend.GenerateRequest(**self.request))
            return FakeModel()

        with patch.object(self.backend, "Llama", side_effect=load):
            result = self.backend.load_model(self.backend.LoadRequest(model_path=str(model)))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model_path"], str(model.resolve()))
        self.assertEqual(result["thinking_support"], "supported")
        self.assertFalse(self.backend.runtime.generation_lock.locked())

    def test_busy_load_and_invalid_path_keep_the_previous_runtime_intact(self):
        model = self.backend.ROOT / "test.gguf"
        model.touch()
        previous = self.backend.runtime.llm
        self.backend.runtime.generation_lock.acquire()
        try:
            with self.assertRaises(self.backend.HTTPException) as busy:
                self.backend.runtime.load({"model_path": str(model)})
            self.assertEqual(busy.exception.status_code, 409)
        finally:
            self.backend.runtime.generation_lock.release()
        with self.assertRaises(self.backend.HTTPException) as invalid:
            self.backend.runtime.load({"model_path": str(self.backend.ROOT / "missing.gguf")})
        self.assertEqual(invalid.exception.status_code, 400)
        self.assertIs(self.backend.runtime.llm, previous)
        self.assertEqual(self.backend.status()["model_path"], "/models/test.gguf")

    def test_failed_load_clears_stale_path_and_support(self):
        model = self.backend.ROOT / "test.gguf"
        model.touch()
        with patch.object(self.backend, "Llama", side_effect=RuntimeError("Out of memory")):
            with self.assertRaises(self.backend.HTTPException):
                self.backend.runtime.load({"model_path": str(model), "context_size": 4096, "gpu_layers": 0, "threads": 4})
        result = self.backend.status()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["model_path"], "")
        self.assertEqual(result["thinking_support"], "unknown")
        self.assertFalse(self.backend.runtime.generation_lock.locked())

    def test_model_catalog_exposes_configured_capability_without_loading(self):
        models = self.backend.PROJECT_ROOT / "models"
        models.mkdir()
        (models / "test.gguf").touch()
        (models / "unverified.gguf").touch()
        catalog = {item["name"]: item for item in self.backend.models()}
        self.assertEqual(catalog["test.gguf"]["thinking_support"], "supported")
        self.assertEqual(catalog["unverified.gguf"]["thinking_support"], "unknown")
        self.assertEqual(self.backend.runtime.llm.calls, [])


    def test_regenerate_branch_preserves_source_and_uses_selected_turn_settings(self):
        self.backend.runtime.llm = FakeModel(("First answer",))
        self.generate()
        first = self.messages()[-1]
        self.backend.runtime.llm = FakeModel(("Second answer",))
        self.generate({**self.request, "request_id": "turn-2", "message": "Later question", "temperature": 1.2})
        original_id = self.chat_id
        original_bytes = self.backend.chat_path(original_id).read_bytes()
        branch = self.backend.branch_chat(original_id, self.backend.BranchRequest(message_id=first["id"]))
        self.assertEqual(branch["branch_of"]["message_id"], first["id"])
        self.assertEqual([item["content"] for item in branch["messages"]], ["Explain this"])
        self.assertEqual(branch["settings"]["temperature"], 0.6)
        self.chat_id = branch["id"]
        self.backend.runtime.llm = FakeModel(("Alternative",))
        self.generate(branch["messages"][-1]["request"])
        self.assertEqual([item["content"] for item in self.messages()], ["Explain this", "Alternative"])
        self.assertEqual(self.backend.chat_path(original_id).read_bytes(), original_bytes)
        self.assertEqual(self.backend.runtime.llm.calls[0]["messages"][-1]["content"], "Explain this\n\n/think")

    def test_later_branch_copies_history_only_through_the_target_user(self):
        self.generate()
        self.generate({**self.request, "request_id": "turn-2", "message": "Second question", "seed": 42})
        selected = self.messages()[-1]
        self.generate({**self.request, "request_id": "turn-3", "message": "Future question"})
        branch = self.backend.branch_chat(self.chat_id, self.backend.BranchRequest(message_id=selected["id"]))
        self.assertEqual([item["type"] for item in branch["messages"]], ["user", "assistant", "user"])
        self.assertEqual(branch["messages"][-1]["content"], "Second question")
        self.assertEqual(branch["messages"][-1]["request"]["seed"], 42)
        self.chat_id = branch["id"]
        self.generate(branch["messages"][-1]["request"])
        history = self.backend.runtime.llm.calls[-1]["messages"]
        self.assertEqual([item["role"] for item in history], ["system", "user", "assistant", "user"])
        self.assertNotIn("Future question", json.dumps(history))

    def test_failed_and_missing_branch_targets_do_not_create_chats(self):
        self.backend.runtime.llm = FakeModel((), "Failed")
        self.generate()
        before = set(self.backend.CHATS.glob("*.jsonl"))
        for message_id, status in [(self.messages()[-1]["id"], 409), ("missing", 404)]:
            with self.assertRaises(self.backend.HTTPException) as error:
                self.backend.branch_chat(self.chat_id, self.backend.BranchRequest(message_id=message_id))
            self.assertEqual(error.exception.status_code, status)
        self.assertEqual(set(self.backend.CHATS.glob("*.jsonl")), before)

    def test_legacy_completed_answers_can_be_branched(self):
        self.backend.append_event(self.chat_id, {"type": "user", "content": "Legacy question"})
        self.backend.append_event(self.chat_id, {"type": "assistant", "content": "Legacy answer", "status": "complete"})
        legacy = self.messages()[-1]
        self.assertTrue(legacy["id"].startswith("legacy-"))
        branch = self.backend.branch_chat(self.chat_id, self.backend.BranchRequest(message_id=legacy["id"]))
        self.chat_id = branch["id"]
        self.generate(branch["messages"][-1]["request"])
        self.assertEqual(sum(item["type"] == "user" for item in self.messages()), 1)

    def test_stream_and_replay_message_ids_identify_the_saved_answer(self):
        first = self.generate()
        saved_id = self.messages()[-1]["id"]
        self.assertEqual(first[0]["message_id"], saved_id)
        self.assertEqual(self.generate()[0]["message_id"], saved_id)
        self.assertEqual(self.backend.read_events(self.chat_id)[-1]["message_id"], saved_id)

    def test_saving_response_defaults_affects_new_chats_only(self):
        original = self.backend.chat_path(self.chat_id).read_bytes()
        self.backend.save_settings(self.backend.SettingsUpdate(temperature=1.1, system_prompt="Future instructions", thinking=True))
        new_chat = self.backend.create_chat(self.backend.ChatCreate())
        self.assertEqual(new_chat["settings"]["temperature"], 1.1)
        self.assertEqual(new_chat["settings"]["system_prompt"], "Future instructions")
        self.assertTrue(new_chat["settings"]["thinking"])
        explicit = self.backend.create_chat(self.backend.ChatCreate(temperature=0.2, thinking=False))
        self.assertEqual(explicit["settings"]["temperature"], 0.2)
        self.assertFalse(explicit["settings"]["thinking"])
        self.assertEqual(self.backend.chat_path(self.chat_id).read_bytes(), original)

    def test_invalid_defaults_are_rejected_without_changing_configuration(self):
        before = self.backend.read_config()
        for values in ({"temperature": 3}, {"top_k": 1001}, {"threads": 0}, {"context_size": 1}, {"unknown": True}):
            with self.assertRaises(ValueError):
                self.backend.SettingsUpdate(**values)
        self.assertEqual(self.backend.read_config(), before)

    def test_failed_atomic_settings_write_keeps_the_previous_config(self):
        self.backend.write_config({"temperature": 0.3})
        before = self.backend.CONFIG.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.backend.save_settings(self.backend.SettingsUpdate(temperature=1.1))
        self.assertEqual(self.backend.CONFIG.read_bytes(), before)
        self.assertEqual(list(self.backend.DATA.glob(".config.json.*.tmp")), [])

    def test_generation_uses_loaded_context_instead_of_pending_defaults(self):
        self.backend.runtime.loaded_settings = {"context_size": 2048, "gpu_layers": 0, "threads": 4}
        self.backend.save_settings(self.backend.SettingsUpdate(context_size=8192))
        result = self.generate()
        self.assertEqual(result[-1]["metrics"]["context_size"], 2048)
        self.assertEqual(self.backend.status()["loaded_settings"]["context_size"], 2048)
        self.assertEqual(self.backend.read_config()["context_size"], 8192)

    def test_model_loading_remembers_only_loading_parameters(self):
        self.backend.save_settings(self.backend.SettingsUpdate(temperature=1.1, system_prompt="Saved default", top_k=5))
        model = self.backend.ROOT / "test.gguf"; model.touch()
        with patch.object(self.backend, "Llama", return_value=FakeModel()):
            result = self.backend.load_model(self.backend.LoadRequest(model_path=str(model), context_size=2048))
        self.assertTrue(result["load_settings_saved"])
        self.assertEqual(result["loaded_settings"]["context_size"], 2048)
        config = self.backend.read_config()
        self.assertEqual(config["temperature"], 1.1)
        self.assertEqual(config["system_prompt"], "Saved default")
        self.assertEqual(config["top_k"], 5)

    def test_loading_settings_save_failure_keeps_a_successfully_loaded_model_ready(self):
        model = self.backend.ROOT / "test.gguf"; model.touch()
        with patch.object(self.backend, "Llama", return_value=FakeModel()), \
                patch.object(self.backend, "write_config", side_effect=OSError("Disk unavailable")):
            result = self.backend.load_model(self.backend.LoadRequest(model_path=str(model)))
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["load_settings_saved"])
        self.assertFalse(self.backend.runtime.generation_lock.locked())
        self.assertEqual(self.backend.status()["model_path"], str(model.resolve()))


if __name__ == "__main__":
    unittest.main()
