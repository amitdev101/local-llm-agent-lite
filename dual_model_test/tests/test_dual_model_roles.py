from __future__ import annotations

import gc
import sys
import tempfile
import unittest
from pathlib import Path


DUAL_MODEL_DIRECTORY = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DUAL_MODEL_DIRECTORY.parent
sys.path.insert(0, str(DUAL_MODEL_DIRECTORY))

import myllm_dual_model_tools as dual_tools  # noqa: E402
from myllm_dual_model_prompts import (  # noqa: E402
    KID_SYSTEM_PROMPT,
    WORKER_SYSTEM_PROMPT,
)
from myllm_dual_model_test import (  # noqa: E402
    KID_CONTEXT,
    KID_MODEL_PATH,
    KID_TEMPERATURE,
    WORKER_CONTEXT,
    WORKER_MODEL_PATH,
    WORKER_TEMPERATURE,
    load_model,
    parse_kid_decision,
    parse_worker_action,
)
from myllm_dual_model_tools import ToolState  # noqa: E402


def complete(model, system_prompt: str, user_prompt: str, temperature: float) -> str:
    response = model.create_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        top_p=0.9,
        seed=42,
        stream=False,
    )
    content = response.get("choices", [{}])[0].get("message", {}).get("content")

    if not isinstance(content, str) or not content.strip():
        raise AssertionError("The model returned no text.")

    return content


class KidModelIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_model(KID_MODEL_PATH, KID_CONTEXT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.model.close()
        del cls.model
        gc.collect()

    def test_kid_rejects_read_only_evidence_for_create_goal(self) -> None:
        response = complete(
            self.model,
            KID_SYSTEM_PROMPT,
            """
GOAL:
Create a playable Snake game in Java 8.

WORKER RESULT:
TOOL: read_file
SUCCESS: True
RESULT:
The existing SnakeGame.java file was read successfully.

WORKSPACE NOW:
FILE SnakeGame.java

No file was created or changed. No build or check was run.
Decide whether the goal is complete.
""",
            KID_TEMPERATURE,
        )
        decision = parse_kid_decision(response)

        self.assertEqual("continue", decision["status"], response)
        self.assertTrue(decision["request"], response)

    def test_kid_accepts_successful_change_and_check(self) -> None:
        response = complete(
            self.model,
            KID_SYSTEM_PROMPT,
            """
GOAL:
Create a minimal Java 8 HelloWorld program.

WORKER RESULT:
TOOL: run_check
SUCCESS: True
RESULT:
Java compilation succeeded with exit code 0.

WORKSPACE NOW:
FILE HelloWorld.java

CURRENT RUN EVIDENCE:
write_file created HelloWorld.java successfully.
run_check compiled it successfully after the write.
Decide whether the goal is complete.
""",
            KID_TEMPERATURE,
        )
        decision = parse_kid_decision(response)

        self.assertEqual("done", decision["status"], response)

    def test_kid_rejects_failed_check_after_change(self) -> None:
        response = complete(
            self.model,
            KID_SYSTEM_PROMPT,
            """
GOAL:
Fix SnakeGame.java so it compiles.

WORKER RESULT:
TOOL: run_check
SUCCESS: False
RESULT:
EXIT_CODE: 1
SnakeGame.java:24: error: cannot find symbol

CURRENT RUN EVIDENCE:
patch_file changed SnakeGame.java successfully.
The check after that change failed with the compiler error above.
Decide whether the goal is complete.
""",
            KID_TEMPERATURE,
        )
        decision = parse_kid_decision(response)

        self.assertEqual("continue", decision["status"], response)
        self.assertTrue(decision["request"], response)

    def test_kid_rejects_check_that_precedes_latest_edit(self) -> None:
        response = complete(
            self.model,
            KID_SYSTEM_PROMPT,
            """
GOAL:
Add keyboard controls to SnakeGame.java and leave it compiling.

WORKER RESULT:
TOOL: write_file
SUCCESS: True
RESULT:
SnakeGame.java was updated after the previous compilation.

CURRENT RUN EVIDENCE IN ORDER:
1. run_check compiled the old source successfully.
2. write_file then changed SnakeGame.java.
3. No check has run after the latest write.

EDIT REVISION: 2
VERIFIED REVISION: 1
Decide whether the goal is complete.
""",
            KID_TEMPERATURE,
        )
        decision = parse_kid_decision(response)

        self.assertEqual("continue", decision["status"], response)
        self.assertIn("check", decision["request"].lower(), response)


class WorkerModelIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_model(WORKER_MODEL_PATH, WORKER_CONTEXT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.model.close()
        del cls.model
        gc.collect()

    def test_worker_creates_fenced_java_file_action(self) -> None:
        response = complete(
            self.model,
            WORKER_SYSTEM_PROMPT,
            """
GOAL:
Create a minimal Java 8 HelloWorld program.

NEXT STEP:
Create HelloWorld.java now.

WORKSPACE:
(empty)

LAST RESULT:
(none)

Return one tool action.
""",
            WORKER_TEMPERATURE,
        )
        action = parse_worker_action(response)

        self.assertEqual("write_file", action["tool"], response)
        self.assertEqual("HelloWorld.java", action["args"]["path"], response)
        self.assertIn("class HelloWorld", action["args"]["content"], response)

    def test_worker_reads_existing_file_before_editing(self) -> None:
        response = complete(
            self.model,
            WORKER_SYSTEM_PROMPT,
            """
GOAL:
Add keyboard controls to the existing SnakeGame.java file.

NEXT STEP:
Read the existing source before changing it.

WORKSPACE:
FILE SnakeGame.java

LAST RESULT:
(none)

Return one tool action.
""",
            WORKER_TEMPERATURE,
        )
        action = parse_worker_action(response)

        self.assertEqual("read_file", action["tool"], response)
        self.assertEqual("SnakeGame.java", action["args"]["path"], response)

    def test_worker_uses_patch_for_small_change_after_read(self) -> None:
        response = complete(
            self.model,
            WORKER_SYSTEM_PROMPT,
            """
GOAL:
Change the greeting in Demo.java from Hello to Hi.

NEXT STEP:
Apply the small exact change now.

WORKSPACE:
FILE Demo.java

LAST RESULT:
TOOL: read_file
SUCCESS: True
RESULT:
FILE: Demo.java
LINES: 1-5 / 5

public class Demo {
    public static void main(String[] args) {
        System.out.println("Hello");
    }
}

Return one tool action.
""",
            WORKER_TEMPERATURE,
        )
        action = parse_worker_action(response)

        self.assertEqual("patch_file", action["tool"], response)
        self.assertEqual("Demo.java", action["args"]["path"], response)
        self.assertIn("Hello", action["args"]["old_text"], response)
        self.assertIn("Hi", action["args"]["new_text"], response)

    def test_worker_runs_check_after_successful_write(self) -> None:
        response = complete(
            self.model,
            WORKER_SYSTEM_PROMPT,
            """
GOAL:
Create a compiling Java 8 HelloWorld program.

NEXT STEP:
Validate the latest file change.

WORKSPACE:
FILE HelloWorld.java

LAST RESULT:
TOOL: write_file
SUCCESS: True
RESULT:
Created HelloWorld.java.

Return one tool action.
""",
            WORKER_TEMPERATURE,
        )
        action = parse_worker_action(response)

        self.assertEqual("run_check", action["tool"], response)
        self.assertIn(action["args"].get("kind", "auto"), {"auto", "build"}, response)


class MarkdownProtocolTest(unittest.TestCase):
    def test_write_content_is_preserved(self) -> None:
        fence = "`" * 3
        response = (
            "<think>Create it.</think>\n"
            "## TOOL write_file\n"
            "path: Demo.java\n"
            f"{fence}java\n"
            "public class Demo {\n"
            "}\n"
            f"{fence}\n"
        )
        action = parse_worker_action(response)

        self.assertEqual("public class Demo {\n}\n", action["args"]["content"])

    def test_patch_blocks_are_preserved(self) -> None:
        fence = "`" * 3
        response = (
            "## TOOL patch_file\n"
            "path: Demo.java\n"
            "### OLD\n"
            f"{fence}java\nold line\n{fence}\n"
            "### NEW\n"
            f"{fence}java\nnew line\n{fence}\n"
        )
        action = parse_worker_action(response)

        self.assertEqual("old line\n", action["args"]["old_text"])
        self.assertEqual("new line\n", action["args"]["new_text"])

    def test_unknown_tool_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unexpected field"):
            parse_worker_action(
                "## TOOL read_file\npath: Demo.java\nforce: true\n"
            )

    def test_unclosed_thinking_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Thinking output was not closed"):
            parse_kid_decision(
                "<think>Still deciding.\n## DECISION done\nrequest:\n"
            )


class ToolSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_workspace = dual_tools.WORKSPACE
        dual_tools.WORKSPACE = Path(self.temporary_directory.name).resolve()

    def tearDown(self) -> None:
        dual_tools.WORKSPACE = self.original_workspace
        self.temporary_directory.cleanup()

    def test_existing_file_requires_read_and_undo_restores_it(self) -> None:
        target = dual_tools.WORKSPACE / "Demo.java"
        target.write_text("original\n", encoding="utf-8")
        state = ToolState()

        with self.assertRaisesRegex(ValueError, "FILE_NOT_READ"):
            dual_tools.write_file("Demo.java", "updated\n", state)

        dual_tools.read_file("Demo.java", state)
        dual_tools.write_file("Demo.java", "updated\n", state)
        self.assertEqual("updated\n", target.read_text(encoding="utf-8"))

        dual_tools.undo_last_edit(state)
        self.assertEqual("original\n", target.read_text(encoding="utf-8"))

    def test_external_change_blocks_stale_write(self) -> None:
        target = dual_tools.WORKSPACE / "Demo.java"
        target.write_text("original\n", encoding="utf-8")
        state = ToolState()
        dual_tools.read_file("Demo.java", state)
        target.write_text("changed externally\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "FILE_CHANGED"):
            dual_tools.write_file("Demo.java", "agent update\n", state)

    def test_workspace_escape_is_blocked(self) -> None:
        with self.assertRaisesRegex(ValueError, "escapes workspace"):
            dual_tools.resolve_workspace_path("../outside.txt")


if __name__ == "__main__":
    unittest.main()
