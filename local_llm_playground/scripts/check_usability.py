"""Playwright journeys against the real app with temporary data and fake inference.

Run from the repository root: .venv/Scripts/python.exe local_llm_playground/scripts/check_usability.py
Never imports the application with its real data directory or loads a GGUF.
"""
import argparse
import logging
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright-browsers"))
from playwright.sync_api import sync_playwright
import uvicorn
from check_themes import set_appearance


class PreviewModel:
    def __init__(self, **_kwargs):
        self.release = threading.Event()

    def tokenize(self, value):
        return list(value)

    def create_chat_completion(self, **kwargs):
        if not self.release.wait(timeout=15):
            raise RuntimeError("Preview response was not released by its test")
        yield {"choices": [{"delta": {"content": "A fresh response from the isolated preview."}}]}


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print("PASS " + label, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true", help="refresh the three usability guide screenshots")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix=".usability-", dir=ROOT / "tests") as directory:
        sandbox = Path(directory).resolve(); sandbox.relative_to((ROOT / "tests").resolve())
        shutil.copytree(ROOT / "frontend", sandbox / "frontend")
        (sandbox / "model_capabilities.json").write_text('{"preview.gguf":"supported"}', encoding="utf-8")
        (sandbox / "models").mkdir(); model_path = sandbox / "models" / "preview.gguf"; model_path.touch()
        backend = types.ModuleType("playground_usability_preview"); backend.__file__ = str(sandbox / "backend.py")
        with patch.dict(sys.modules, {backend.__name__: backend, "llama_cpp": types.SimpleNamespace(Llama=PreviewModel)}), \
                patch("logging.basicConfig"), patch("logging.FileHandler", return_value=logging.NullHandler()):
            exec(compile((ROOT / "backend.py").read_text(encoding="utf-8"), str(ROOT / "backend.py"), "exec"), backend.__dict__)
        backend.PROJECT_ROOT = sandbox
        backend.runtime.llm = PreviewModel(); backend.runtime.status = "ready"; backend.runtime.model_path = str(model_path)
        backend.runtime.loaded_settings = {"model_path": str(model_path), "context_size": 4096, "gpu_layers": 0, "threads": 4}
        streams = []
        response_type = backend.StreamingResponse
        def tracked_response(content, **kwargs):
            streams.append(content)
            return response_type(content, **kwargs)
        backend.StreamingResponse = tracked_response
        backend.write_config(backend.runtime.loaded_settings)
        chats = []
        for index in (1, 2):
            chat = backend.create_chat(backend.ChatCreate(temperature=0.3))
            backend.append_event(chat["id"], {"type": "user", "content": f"Sample question {index}", "request_id": f"seed-{index}"})
            backend.append_event(chat["id"], {"type": "assistant", "content": "A saved sample answer.", "status": "complete", "message_id": f"seed-answer-{index}",
                                            "metrics": {"prompt_tokens": index * 100, "completion_tokens": index * 20}})
            backend.rename_chat(chat["id"], backend.ChatUpdate(title=f"Sample chat {index}")); chats.append(chat["id"])
        original = backend.chat_path(chats[0]).read_bytes()
        listener = socket.socket(); listener.bind(("127.0.0.1", 0)); port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(backend.app, log_level="error", access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True); worker.start()
        try:
            for _ in range(200):
                if server.started: break
                time.sleep(.05)
            check(server.started, "isolated preview started")
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chromium", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page(); errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                page.get_by_role("button", name="Sample chat 1", exact=True).click()
                page.locator("#prompt").fill("An unsent draft")
                page.locator("#optionsButton").click(); page.locator("#temperatureNumber").fill("0.42")
                page.locator("#thinking").check(); page.keyboard.press("Escape")
                page.locator("#libraryButton").click(); page.locator("#libraryName").fill("My setup")
                page.locator("#saveLibraryItem").click()
                page.locator(".library-item").filter(has=page.get_by_text("Coding", exact=True)).get_by_role("button", name="Apply profile").click()
                check(page.locator("#prompt").input_value() == "An unsent draft", "applying a profile keeps the draft")
                page.locator("#libraryButton").click()
                page.locator(".library-item").filter(has=page.get_by_text("My setup", exact=True)).get_by_role("button", name="Apply profile").click()
                check(page.locator("#quickThinking").is_checked(), "saved profiles restore Thinking")
                check(page.locator("#temperatureNumber").input_value() == "0.42", "saved profiles restore sampling preferences")
                check(backend.read_config()["temperature"] == backend.DEFAULTS["temperature"], "profiles leave server defaults unchanged")
                page.locator("#libraryButton").click(); page.locator("#promptsTab").click()
                page.locator("#libraryName").fill("My prompt"); page.locator("#saveLibraryItem").click()
                page.keyboard.press("Escape"); page.locator("#prompt").fill("Another draft")
                page.locator("#libraryButton").click(); page.locator("#promptsTab").click()
                page.locator(".library-item").filter(has=page.get_by_text("My prompt", exact=True)).get_by_role("button", name="Use prompt").click()
                page.locator("#appendPromptChoice").click()
                check(page.locator("#prompt").input_value() == "Another draft\n\nAn unsent draft", "saved prompts append without losing existing text")
                page.reload(wait_until="networkidle")
                check(page.locator("#prompt").input_value() == "Another draft\n\nAn unsent draft", "library and drafts survive reload")
                page.locator("#newChat").click()
                check(page.locator("#chatUsageButton").is_disabled(), "an unsent chat has no misleading per-chat usage link")
                page.get_by_role("button", name="Explain a concept", exact=True).click()
                check(page.locator("#prompt").input_value().startswith("Explain the following"), "starter prompts fill the composer without sending")
                page.locator("#expandComposer").click(); page.locator("#prompt").fill("Expanded question")
                page.locator("#prompt").press("Enter")
                check(page.locator("#composerDialog").is_visible() and page.locator("#prompt").input_value().endswith("\n"), "expanded composer uses Enter for a new line")
                with page.expect_response(lambda response: response.url.endswith("/generate") and response.request.method == "POST"):
                    page.locator("#prompt").press("Control+Enter")
                page.get_by_role("button", name="Sample chat 2", exact=True).click()
                page.locator("#prompt").fill("A separate draft")
                check(page.locator("#runningResponse").is_visible(), "background response shows a return affordance")
                page.locator("#returnToResponse").click()
                page.locator(".assistant[data-status='streaming']").wait_for()
                check(page.locator(".assistant[data-status='streaming']").count() == 1, "returning to a live response keeps one streaming answer")
                page.get_by_role("button", name="Sample chat 2", exact=True).click()
                backend.runtime.llm.release.set()
                page.locator("#stopButton").wait_for(state="hidden", timeout=10000)
                check(page.locator("#prompt").input_value() == "A separate draft", "background completion preserves the viewed draft")
                page.get_by_role("button", name="Sample chat 1", exact=True).click()
                page.locator(".edit-prompt").click(); page.locator("#editedPrompt").fill("Edited sample question")
                backend.runtime.llm.release.clear()
                page.locator("#sendEdit").click(); page.locator("#stopButton").wait_for(state="visible")
                page.locator("#usageSidebar").click(); page.locator("#usageRows .usage-row").nth(1).wait_for()
                check(page.locator("#usageTotals dd").last.inner_text() == "3", "usage excludes an attempt until its counts are stored")
                backend.runtime.llm.release.set()
                page.locator("#stopButton").wait_for(state="hidden", timeout=10000)
                page.wait_for_function("document.querySelector('#usageTotals dd:last-child') !== null && [...document.querySelectorAll('#usageTotals dd')].at(-1).textContent === '4'")
                check(page.locator("#usageTotals dd").last.inner_text() == "4", "an open usage view refreshes when generation finishes")
                page.keyboard.press("Escape")
                check(backend.chat_path(chats[0]).read_bytes() == original, "Edit and resend preserves the original chat")
                check(page.locator(".user").count() == 1 and "Edited sample question" in page.locator(".user").inner_text(), "edited branch contains one revised prompt")
                page.locator("#settingsButton").click()
                page.locator("#readingSize").select_option("large"); page.locator("#readingSpacing").select_option("comfortable")
                page.locator("#sendBehavior").select_option("modifier"); page.locator("#colorModeSelect").select_option("system")
                page.emulate_media(color_scheme="light")
                page.wait_for_function("document.documentElement.dataset.colorMode === 'light'")
                check(page.locator("html").get_attribute("data-color-mode") == "light", "Follow system responds to light appearance")
                page.emulate_media(color_scheme="dark")
                page.wait_for_function("document.documentElement.dataset.colorMode === 'dark'")
                check(page.locator("html").get_attribute("data-color-mode") == "dark", "Follow system responds to dark appearance")
                page.locator("#closeSettings").click(); page.reload(wait_until="networkidle")
                check(page.locator("#readingSize").input_value() == "large" and page.locator("#sendBehavior").input_value() == "modifier", "reading and send preferences survive reload")
                page.locator("#prompt").fill("A multiline draft"); page.locator("#prompt").press("Enter")
                check(page.locator("#stopButton").is_hidden() and page.locator("#prompt").input_value().endswith("\n"), "the chosen send shortcut prevents accidental Enter submission")
                page.locator("#usageSidebar").click(); page.locator("#usageRows .usage-row").nth(1).wait_for()
                check(page.locator("#usageTotals dd").last.inner_text() == "4", "All chats usage includes four actual attempts")
                page.locator("#usageCurrent").click(); page.locator("#usageRows .usage-row").nth(1).wait_for()
                check(page.locator("#usageTotals dd").last.inner_text() == "1", "This chat usage excludes inherited history")
                page.keyboard.press("Escape")
                if args.screenshots:
                    output = ROOT / "docs" / "images" / "usability"; output.mkdir(parents=True, exist_ok=True)
                    set_appearance(page, "claude", "dark")
                    page.locator("#libraryButton").click(); page.locator("#profilesTab").click()
                    page.locator(".library-item details").first.locator("summary").click()
                    (output / "library.png").write_bytes(page.screenshot(animations="disabled"))
                    page.keyboard.press("Escape"); page.locator("#usageSidebar").click()
                    page.locator("#usageRows .usage-row").nth(1).wait_for()
                    (output / "token-usage.png").write_bytes(page.screenshot(animations="disabled"))
                    page.keyboard.press("Escape"); page.locator("#expandComposer").click()
                    (output / "expanded-composer.png").write_bytes(page.screenshot(animations="disabled"))
                    page.keyboard.press("Escape")
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 1000 if width == 1440 else 844})
                    for theme in ("chatgpt", "claude", "github-copilot"):
                        for mode in ("light", "dark"):
                            set_appearance(page, theme, mode)
                            page.locator("#libraryButton").click()
                            check(page.evaluate("document.documentElement.scrollWidth <= innerWidth") and page.locator("#libraryDialog").is_visible(), f"{theme}/{mode}/{width}: library is reachable without page overflow")
                            check(page.locator("#saveLibraryItem").evaluate("el => el.getBoundingClientRect().bottom <= innerHeight"), f"{theme}/{mode}/{width}: library save action is immediately visible")
                            page.keyboard.press("Escape"); page.locator("#expandComposer").click()
                            check(page.locator("#composerDialog #prompt").count() == 1 and page.locator("#prompt").count() == 1, f"{theme}/{mode}/{width}: expanded editor uses the same textarea")
                            page.keyboard.press("Escape"); page.locator("#settingsButton").click(); page.locator("#usageButton").click()
                            page.locator("#usageRows .usage-row").nth(1).wait_for()
                            check(page.locator("#usageDialog").evaluate("el => {const r=el.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth && r.bottom<=innerHeight; }"), f"{theme}/{mode}/{width}: usage stays within the viewport")
                            page.keyboard.press("Escape")
                blocked = browser.new_context(viewport={"width": 1200, "height": 900})
                blocked.add_init_script("Storage.prototype.setItem = function() { throw new DOMException('Storage full', 'QuotaExceededError'); };")
                blocked_page = blocked.new_page(); blocked_page.on("pageerror", lambda error: errors.append(str(error)))
                blocked_page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                blocked_page.locator("#prompt").fill("Keep this draft")
                blocked_page.locator("#expandComposer").click()
                blocked_page.locator("#expandedDraftWarning").wait_for(state="visible")
                check(blocked_page.locator("#prompt").input_value() == "Keep this draft", "expanded editor keeps the draft and shows storage failure")
                blocked_page.keyboard.press("Escape"); blocked_page.locator("#libraryButton").click()
                blocked_page.locator("#libraryName").fill("In-memory profile"); blocked_page.locator("#saveLibraryItem").click()
                check("Could not save" in blocked_page.locator("#libraryStatus").inner_text(), "library storage failure is explained inside its dialog")
                check(blocked_page.get_by_text("In-memory profile", exact=True).count() == 1, "an unsaved profile remains available in the open tab")
                blocked.close()
                check(not errors, "no uncaught browser errors: " + repr(errors))
                context.close(); browser.close()
        finally:
            backend.runtime.llm.release.set()
            server.should_exit = True; worker.join(timeout=10); listener.close()
            for stream in streams:
                stream.close()
    print("All usability journeys passed.", flush=True)


if __name__ == "__main__":
    main()
