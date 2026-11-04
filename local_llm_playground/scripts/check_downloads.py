"""Playwright model-download journeys using temporary files and a simulated Hub.

Run from the repository root: .venv/Scripts/python.exe local_llm_playground/scripts/check_downloads.py
No public model weights or personal app data are downloaded/read by this check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sys
import threading
import time

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from check_themes import set_appearance
from check_usability import PreviewModel, check
from model_downloads import ModelDownloads
from test_recovery import RecoveryTests
from playwright.sync_api import sync_playwright


class Hub:
    data = b"GGUF" + bytes(196604)
    revision = "b" * 40

    def __init__(self):
        self.requests = []
        self.hold = True
        self.gate = threading.Event()

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path.startswith("/api/models/"):
            return httpx.Response(200, json={"sha": self.revision, "siblings": [{"rfilename": "downloaded.gguf", "size": len(self.data),
                "lfs": {"size": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}}]})
        offset = int(request.headers.get("range", "bytes=0-").split("=")[1].rstrip("-"))
        hub = self
        class Body(httpx.SyncByteStream):
            def __iter__(self):
                yield hub.data[offset:offset + 65536]
                if hub.hold:
                    if not hub.gate.wait(timeout=15):
                        raise httpx.ReadTimeout("Test download was not released")
                    hub.hold = False
                yield hub.data[offset + 65536:]
        headers = {"content-range": f"bytes {offset}-{len(self.data)-1}/{len(self.data)}"} if offset else {}
        return httpx.Response(206 if offset else 200, headers=headers, stream=Body())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    args = parser.parse_args()
    fixture = RecoveryTests(); fixture.setUp()
    backend = fixture.backend; root = backend.ROOT; models = root / "models"; models.mkdir()
    shutil.copytree(ROOT / "frontend", root / "frontend", dirs_exist_ok=True)
    (root / "model_catalog.json").write_text(json.dumps([{"name": "Example model", "repo_id": "preview/model", "description": "Isolated sample model data."}]), encoding="utf-8")
    installed = models / "test.gguf"; installed.write_bytes(b"GGUF" + bytes(12))
    backend.runtime.model_path = str(installed); backend.runtime.loaded_settings = {"model_path": str(installed), "context_size": 4096, "gpu_layers": 0, "threads": 4}
    backend.runtime.llm = PreviewModel(); backend.Llama = PreviewModel
    backend.write_config(backend.runtime.loaded_settings)
    backend.rename_chat(fixture.chat_id, backend.ChatUpdate(title="Download test chat"))
    before_chat = backend.chat_path(fixture.chat_id).read_bytes()
    hub = Hub(); backend.downloads = ModelDownloads(models, root / "data" / "downloads.json", httpx.MockTransport(hub))
    listener = socket.socket(); listener.bind(("127.0.0.1", 0)); port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(backend.app, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True); thread.start()
    try:
        for _ in range(200):
            if server.started: break
            time.sleep(.05)
        check(server.started, "isolated download preview started")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chromium", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}); errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            check(not hub.requests, "app startup makes no Hub requests")
            page.locator("#prompt").fill("Keep this draft while downloading")
            page.locator("#settingsButton").click(); page.locator("#getModelsButton").click()
            check(not hub.requests, "opening Get models reads only the local catalog")
            page.locator(".catalog-item").get_by_role("button", name="Browse files").click()
            card = page.locator(".download-file"); card.wait_for()
            check(card.locator("h3").inner_text() == "downloaded.gguf", "repository browsing shows GGUF files")
            card.get_by_role("button", name="Download", exact=True).click()
            page.wait_for_function("document.querySelector('.download-job progress')?.value === 65536")
            page.get_by_role("button", name="Pause", exact=True).click(); hub.gate.set()
            page.locator(".download-status").filter(has_text="Paused").wait_for()
            check(not (models / "downloaded.gguf").exists(), "partial downloads stay out of the model selector")
            page.reload(wait_until="networkidle")
            check(page.locator("#prompt").input_value() == "Keep this draft while downloading", "refreshing during a paused download keeps the draft")
            page.locator("#downloadsSidebar").click()
            resume = page.get_by_role("button", name="Resume", exact=True); resume.wait_for(); resume.focus()
            with page.expect_response(lambda response: response.url.endswith("/api/model-downloads")):
                pass  # The open dialog polls local history once per second.
            check(resume.evaluate("el => document.activeElement === el"), "progress polling keeps keyboard focus")
            resume.click(); page.locator(".download-status").filter(has_text="Ready to load").wait_for()
            check(any(request.headers.get("range") == "bytes=65536-" for request in hub.requests), "Resume requests only the remaining bytes")
            check((models / "downloaded.gguf").read_bytes() == hub.data, "download contents are verified before publication")
            check(backend.runtime.model_path == str(installed), "downloading does not replace the running model")
            page.keyboard.press("Escape"); page.locator("#settingsButton").click(); page.locator("#getModelsButton").click()
            page.locator("#repositoryInput").fill("preview/model"); page.locator("#repositoryForm").get_by_role("button", name="Browse files").click()
            card = page.locator(".download-file"); card.get_by_role("button", name="Load installed file").wait_for()
            check(card.get_by_role("button", name="Download", exact=True).is_disabled(), "existing filenames require an explicit new name")
            card.locator("input").fill("cancelled.gguf")
            hub.hold = True; hub.gate.clear()
            card.get_by_role("button", name="Download", exact=True).click()
            active = page.locator(".download-job").filter(has=page.get_by_role("heading", name="cancelled.gguf", exact=True))
            page.wait_for_function("[...document.querySelectorAll('.download-job')].find(el => el.textContent.includes('cancelled.gguf'))?.querySelector('progress').value === 65536")
            active.get_by_role("button", name="Cancel download", exact=True).click(); hub.gate.set()
            active.locator(".download-status").filter(has_text="Cancelled").wait_for()
            check(not (models / "cancelled.gguf").exists() and (models / "downloaded.gguf").read_bytes() == hub.data, "Cancel keeps installed models intact")
            completed = page.locator(".download-job").filter(has=page.get_by_role("heading", name="downloaded.gguf", exact=True))
            completed.get_by_role("button", name="Load model", exact=True).click()
            page.wait_for_function("document.querySelector('#activeModel').textContent === 'downloaded.gguf'")
            check(backend.runtime.model_path == str(models / "downloaded.gguf"), "Load explicitly activates the downloaded model")
            check(page.locator("#prompt").input_value() == "Keep this draft while downloading", "loading the downloaded model preserves the draft")
            check(backend.chat_path(fixture.chat_id).read_bytes() == before_chat, "model downloads and loading preserve the conversation")
            check(backend.read_config()["temperature"] == backend.DEFAULTS["temperature"], "model loading leaves response defaults unchanged")
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 1000 if width == 1440 else 844})
                for theme in ("chatgpt", "claude", "github-copilot"):
                    for mode in ("light", "dark"):
                        set_appearance(page, theme, mode)
                        page.locator("#settingsButton").click(); page.locator("#getModelsButton").click()
                        check(page.locator("#modelDownloadsDialog").evaluate("el => {const r=el.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth && r.bottom<=innerHeight && el.scrollWidth<=el.clientWidth;}"), f"{theme}/{mode}/{width}: download panel fits the viewport")
                        page.locator("#downloadJobsTab").click()
                        check(page.locator(".download-job").count() == 2, f"{theme}/{mode}/{width}: persistent jobs remain accessible")
                        page.keyboard.press("Escape")
            if args.screenshots:
                page.set_viewport_size({"width": 1440, "height": 1000}); set_appearance(page, "claude", "dark")
                page.locator("#settingsButton").click(); page.locator("#getModelsButton").click()
                page.locator("#repositoryInput").fill("preview/model"); page.locator("#repositoryForm").get_by_role("button", name="Browse files").click()
                page.locator(".download-file").wait_for()
                output = ROOT / "docs" / "images" / "usability"; output.mkdir(exist_ok=True, parents=True)
                (output / "model-downloads.png").write_bytes(page.screenshot(animations="disabled"))
            check(not errors, "no uncaught browser errors: " + repr(errors))
            browser.close()
    finally:
        hub.gate.set(); backend.downloads.stop.set()
        if backend.downloads.worker: backend.downloads.worker.join(timeout=5)
        server.should_exit = True; thread.join(timeout=10); listener.close(); fixture.doCleanups()
    print("All model-download journeys passed.", flush=True)


if __name__ == "__main__":
    main()
