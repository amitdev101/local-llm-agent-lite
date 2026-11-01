#!/usr/bin/env python3
"""Isolated Playwright checks and screenshots for the themeable playground.

The preview serves only the frontend tree. Every /api/** request is intercepted
before navigation and answered from deterministic in-memory fixtures; the real
FastAPI app, model runtime, and user chat data are never imported or contacted.
"""

from __future__ import annotations

import argparse
import json
import os
import mimetypes
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from playwright.sync_api import Browser, Page, Route, sync_playwright
except ImportError:
    print("Playwright is missing. Install development tooling with: python -m pip install playwright", file=sys.stderr)
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
OUT = ROOT / "docs" / "images" / "themes"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright-browsers"))
CHAT_ID = "sample-chat-01"
MODEL_A = r"C:\models\assistant.gguf"
MODEL_B = r"D:\archive\assistant.gguf"

SETTINGS = {
    "model_path": MODEL_A,
    "context_size": 4096,
    "gpu_layers": 0,
    "threads": 4,
    "system_prompt": "You are a helpful local assistant.",
    "thinking": False,
    "temperature": 0.2,
    "top_p": 0.95,
    "top_k": 40,
    "min_p": 0.05,
    "repeat_penalty": 1,
    "seed": -1,
}
METRICS = {
    "total_seconds": 1.2,
    "first_token_seconds": 0.2,
    "tokens_per_second": 42.5,
    "prompt_tokens": 32,
    "completion_tokens": 48,
    "temperature": 0.2,
    "thinking": False,
    "context_used": 80,
    "context_size": 4096,
}
HISTORY_CHAT = {
    "id": CHAT_ID,
    "title": "A small Python helper",
    "updated_at": "2026-10-31T12:00:00+00:00",
    "settings": SETTINGS,
    "messages": [
        {"type": "user", "content": "Write a short Python greeting function."},
        {
            "type": "assistant",
            "content": "Here is a small example:\n\n```python\ndef greet(name):\n    return f\"Hello, {name}!\"\n```",
            "metrics": METRICS,
            "status": "complete",
        },
    ],
}


class PreviewHandler(BaseHTTPRequestHandler):
    """Static frontend host plus harmless deterministic API fixtures."""

    scenario = "history"
    request_log: list[tuple[str, str]] = []
    lock = threading.Lock()
    protocol_version = "HTTP/1.1"
    stop_requested = threading.Event()

    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            with self.lock:
                self.request_log.append(("GET", path))
            chats = [] if self.scenario == "empty" else [{
                "id": CHAT_ID,
                "title": HISTORY_CHAT["title"],
                "updated_at": HISTORY_CHAT["updated_at"],
            }]
            fixtures = {
                "/api/models": [
                    {"path": MODEL_A, "name": "assistant.gguf", "size": 2_147_483_648},
                    {"path": MODEL_B, "name": "assistant.gguf", "size": 3_221_225_472},
                ],
                "/api/settings": SETTINGS,
                "/api/status": {"status": "ready", "model_path": MODEL_A, "error": None},
                "/api/chats": chats,
                f"/api/chats/{CHAT_ID}": HISTORY_CHAT,
            }
            self._json(200, fixtures.get(path, {}))
            return

        relative = "index.html" if path in ("", "/") else path.removeprefix("/assets/")
        target = (FRONTEND / relative).resolve()
        try:
            target.relative_to(FRONTEND.resolve())
        except ValueError:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        if not target.is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or target.suffix in {".js", ".json", ".svg"}:
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        with self.lock:
            self.request_log.append(("POST", path))
        if path == "/api/chats":
            self._json(200, {**HISTORY_CHAT, "id": "sample-new-chat", "messages": []})
        elif path == "/api/generation/stop":
            self.stop_requested.set()
            self._json(200, {"stopping": True})
        elif path.endswith("/generate"):
            self.stop_requested.clear()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            def chunk(line: str) -> None:
                data = (line + "\n").encode("utf-8")
                self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
                self.wfile.flush()

            chunk('{"type":"start"}')
            time.sleep(1.2)  # Keep one deterministic generation live during the theme-switch check.
            chunk('{"type":"token","text":"Hello from the deterministic local fixture."}')
            time.sleep(0.35)
            kind = "stopped" if self.stop_requested.is_set() else "done"
            chunk(json.dumps({"type": kind, "metrics": METRICS}))
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        elif path == "/api/model/load":
            self._json(200, {"status": "ready", "model_path": MODEL_A})
        else:
            self._json(200, {"ok": True, "body": body.decode("utf-8", "replace")})

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        with self.lock:
            self.request_log.append(("PATCH", path))
        self._json(200, {"ok": True})

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        with self.lock:
            self.request_log.append(("DELETE", path))
        self._json(200, {"deleted": True})


def start_preview(scenario: str) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    PreviewHandler.scenario = scenario
    server = ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler)
    thread = threading.Thread(target=server.serve_forever, name="playground-preview", daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}/"


def check(condition: bool, label: str, failures: list[str]) -> None:
    print(("PASS" if condition else "FAIL") + "  " + label)
    if not condition:
        failures.append(label)


def verify_initial(page: Page, failures: list[str], scenario: str) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(250)
    check(page.locator("#themeSelect").count() == 1, "theme selector exists", failures)
    check(page.locator("#colorModeSelect").count() == 1, "appearance selector exists", failures)
    check(page.locator("#prompt").count() == 1 and page.locator("#messages").count() == 1,
          "single composer and message region", failures)
    if page.locator("#themeSelect").count() and page.locator("#colorModeSelect").count():
        check(page.locator("html").get_attribute("data-theme") == "chatgpt", "default ChatGPT theme applied", failures)
        check(page.locator("html").get_attribute("data-color-mode") == "dark", "default dark appearance applied", failures)
    if scenario == "empty":
        check(page.locator("body").get_attribute("data-chat-state") == "empty", "empty chat state is exposed", failures)
    else:
        check(page.locator("body").get_attribute("data-chat-state") == "active", "active chat state is exposed", failures)
        check(page.get_by_text("A small Python helper").count() >= 1, "sample chat history rendered", failures)


def set_appearance(page: Page, theme: str, mode: str) -> None:
    panel = page.locator("#settingsPanel")
    was_open = panel.evaluate("el => el.classList.contains('open')")
    if not was_open:
        page.locator("#settingsButton").click()
    page.locator("#themeSelect").select_option(theme)
    page.locator("#colorModeSelect").select_option(mode)
    page.wait_for_function(
        "([theme, mode]) => document.documentElement.dataset.theme === theme && document.documentElement.dataset.colorMode === mode",
        arg=[theme, mode],
    )
    if not was_open:
        page.locator("#closeSettings").click()


def capture_gallery(page: Page) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for theme, filename in (("chatgpt", "chatgpt"), ("claude", "claude"), ("github-copilot", "github-copilot")):
        for mode in ("light", "dark"):
            set_appearance(page, theme, mode)
            page.wait_for_timeout(100)
            page.screenshot(path=str(OUT / f"{filename}-{mode}.png"), full_page=True, animations="disabled")
    page.set_viewport_size({"width": 390, "height": 844})
    for theme, filename in (("claude", "claude"), ("github-copilot", "github-copilot")):
        set_appearance(page, theme, "dark")
        page.screenshot(path=str(OUT / f"{filename}-dark-mobile.png"), full_page=True, animations="disabled")
    set_appearance(page, "chatgpt", "dark")
    page.locator("#settingsButton").click()
    page.screenshot(path=str(OUT / "chatgpt-dark-settings.png"), full_page=True, animations="disabled")
    page.locator("#closeSettings").click()


def run_checks(page: Page, failures: list[str], scenario: str, theme_variant: dict[str, str]) -> None:
    verify_initial(page, failures, scenario)
    if page.locator("#themeSelect").count() != 1 or page.locator("#colorModeSelect").count() != 1:
        return

    # Exercise all six variants at desktop, checking identity, state, and bounded layout.
    page.set_viewport_size({"width": 1440, "height": 1000})
    for theme in ("chatgpt", "claude", "github-copilot"):
        for mode in ("light", "dark"):
            set_appearance(page, theme, mode)
            check(page.locator("html").get_attribute("data-theme") == theme, f"{theme}/{mode} theme selected", failures)
            overflow = page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            check(overflow, f"{theme}/{mode} desktop has no horizontal overflow", failures)

    # Draft, settings values and open settings survive presentation changes.
    page.locator("#prompt").fill("draft survives a theme switch")
    sys_prompt = page.locator("#systemPrompt")
    page.locator("#settingsButton").click()
    if sys_prompt.count():
        sys_prompt.fill("fixture preference stays local")
    before_mutations = list(PreviewHandler.request_log)
    set_appearance(page, "claude", "light")
    check(page.locator("#prompt").input_value() == "draft survives a theme switch", "draft survives theme switch", failures)
    if sys_prompt.count():
        check(sys_prompt.input_value() == "fixture preference stays local", "settings value survives theme switch", failures)
    check(page.locator("#settingsPanel").evaluate("el => el.classList.contains('open')"), "open settings panel survives theme switch", failures)
    after_mutations = PreviewHandler.request_log[len(before_mutations):]
    forbidden = [(method, path) for method, path in after_mutations if path in {
        "/api/model/load", "/api/settings", "/api/chats", "/api/generation/stop"
    } and method in {"POST", "PATCH", "DELETE"}]
    check(not forbidden, "theme switches make no mutation API calls", failures)

    # Model identity must come from the selected path, even with duplicate display names.
    selectors = page.locator("#quickModelSelect, #modelSelect")
    if selectors.count():
        select = page.locator("#quickModelSelect") if page.locator("#quickModelSelect").count() else page.locator("#modelSelect")
        values = select.locator("option").evaluate_all("els => els.map(el => el.value)")
        check(MODEL_A in values and MODEL_B in values, "same-basename model fixtures have distinct path values", failures)
        if select.get_attribute("id") == "quickModelSelect":
            loaded_label = page.locator("#quickLoadModel").inner_text().strip()
            check(bool(loaded_label) and loaded_label.lower() not in {"load", "load model"} and "loading" not in loaded_label.lower(),
                  "loaded model status is shown for the selected path", failures)
            select.select_option(MODEL_B)
            page.wait_for_function("el => !el.disabled && !el.textContent.toLowerCase().includes('loading')", arg=page.locator("#quickLoadModel").element_handle(), timeout=10000)
            other_label = page.locator("#quickLoadModel").inner_text().strip()
            check(other_label != loaded_label, "same display basename at another path is not mistaken for the loaded model", failures)

    # Preference is restored by a reload with the same isolated browser context.
    set_appearance(page, "github-copilot", "light")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(200)
    check(page.locator("html").get_attribute("data-theme") == "github-copilot", "theme preference survives reload", failures)
    check(page.locator("html").get_attribute("data-color-mode") == "light", "mode preference survives reload", failures)

    # Invalid fetched theme JSON is isolated to that family; missing optional image files fall back.
    for variant in ("invalid-palette", "unknown-placement"):
        theme_variant["value"] = variant
        page.reload(wait_until="networkidle")
        check("claude" not in page.locator("#themeSelect option").evaluate_all("els => els.map(el => el.value)"),
              f"{variant} config is rejected without blocking valid themes", failures)
    theme_variant["value"] = "missing-logo"
    page.reload(wait_until="networkidle")
    if "claude" in page.locator("#themeSelect option").evaluate_all("els => els.map(el => el.value)"):
        set_appearance(page, "claude", "light")
        logo = page.locator("[data-brand-logo]").first
        page.wait_for_function("img => img.currentSrc.endsWith('/fallback.svg')", arg=logo.element_handle(), timeout=5000)
        check(logo.get_attribute("src").endswith("fallback.svg"), "missing logo asset uses local fallback mark", failures)
    else:
        check(False, "missing-logo theme remains configurable", failures)
    theme_variant["value"] = "normal"
    page.evaluate("localStorage.setItem('playground.appearance.v1', JSON.stringify({theme:'removed-theme', mode:'light'}))")
    page.reload(wait_until="networkidle")
    check(page.locator("html").get_attribute("data-theme") == "chatgpt", "removed saved theme falls back to ChatGPT", failures)
    check(page.locator("html").get_attribute("data-color-mode") == "light", "valid saved mode survives removed-theme fallback", failures)

    # Mobile smoke: no horizontal overflow and settings/theme controls remain reachable.
    page.set_viewport_size({"width": 390, "height": 844})
    for theme in ("chatgpt", "claude", "github-copilot"):
        for mode in ("light", "dark"):
            set_appearance(page, theme, mode)
            check(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"{theme}/{mode} mobile has no horizontal overflow", failures)
            check(page.locator("#settingsButton").is_visible(), f"{theme}/{mode} mobile settings control visible", failures)
            check(page.locator("#prompt").is_visible(), f"{theme}/{mode} mobile composer visible", failures)

    page.set_viewport_size({"width": 768, "height": 900})
    for theme in ("chatgpt", "claude", "github-copilot"):
        set_appearance(page, theme, "dark")
        check(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"{theme} tablet has no horizontal overflow", failures)
    page.set_viewport_size({"width": 320, "height": 600})
    set_appearance(page, "chatgpt", "dark")
    page.locator("#prompt").fill("First line\nSecond line\nThird line")
    check(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "320px multiline composer has no horizontal overflow", failures)
    check(page.locator("#prompt").is_visible(), "320px short viewport keeps composer visible", failures)
    check(page.locator("[id]").evaluate_all("nodes => { const ids=nodes.map(n=>n.id).filter(Boolean); return ids.length===new Set(ids).size; }"),
          "no duplicate element IDs after layout changes", failures)

    # Basic labels and keyboard focus visibility.
    check(bool(page.locator("#prompt").get_attribute("aria-label") or page.locator("label[for='prompt']").count()),
          "composer has an accessible label", failures)
    page.keyboard.press("Tab")
    focus_visible = page.evaluate("!!document.activeElement && (document.activeElement.matches(':focus-visible') || document.activeElement === document.body)")
    check(focus_visible, "keyboard focus is available", failures)

    # Hold a deterministic NDJSON generation open while presentation changes.
    page.set_viewport_size({"width": 1440, "height": 1000})
    if page.locator("#settingsPanel").evaluate("el => el.classList.contains('open')"):
        page.locator("#closeSettings").click()
    if scenario == "empty":
        page.locator("#newChat").click()
        page.wait_for_timeout(100)
    page.locator("#prompt").fill("A fixture prompt")
    before_generate = len(PreviewHandler.request_log)
    page.locator("#sendButton").click()
    page.locator("#stopButton").wait_for(state="visible", timeout=3000)
    set_appearance(page, "github-copilot", "light")
    check(page.locator("#stopButton").is_visible(), "in-flight generation remains active through theme switch", failures)
    check(page.locator("#prompt").input_value() == "", "sent prompt remains committed while streaming", failures)
    page.locator("#stopButton").wait_for(state="hidden", timeout=7000)
    check(page.get_by_text("Hello from the deterministic local fixture.").count() >= 1,
          "NDJSON completion is rendered after theme switch", failures)

    # Verify the stop API path against the same harmless fixture.
    page.locator("#prompt").fill("Stop fixture")
    page.locator("#sendButton").click()
    page.locator("#stopButton").wait_for(state="visible", timeout=3000)
    page.locator("#stopButton").click()
    page.locator("#stopButton").wait_for(state="hidden", timeout=7000)
    stop_seen = any(method == "POST" and path == "/api/generation/stop" for method, path in PreviewHandler.request_log[before_generate:])
    check(stop_seen, "stop control reaches deterministic stop route", failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true", help="capture six README themes and selected mobile/settings views")
    parser.add_argument("--scenario", choices=("history", "empty"), default="history", help="deterministic API fixture state")
    parser.add_argument("--channel", choices=("chromium", "msedge"), default="chromium", help="Playwright browser channel")
    args = parser.parse_args()

    server, thread, base_url = start_preview(args.scenario)
    failures: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser_exe = ROOT / ".playwright-browsers" / "chromium-1248" / "chrome-win64" / "chrome.exe"
                location = f"{browser_exe} (exists={browser_exe.is_file()})" if args.channel == "chromium" else "installed Microsoft Edge"
                print(f"Launching {args.channel}: {location}", flush=True)
                browser: Browser = p.chromium.launch(channel=args.channel, headless=True, timeout=15000)
            except Exception as exc:
                print(f"Could not launch Playwright Chromium: {exc}", file=sys.stderr)
                return 2
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            context.set_default_timeout(10000)
            context.set_default_navigation_timeout(10000)
            page = context.new_page()
            browser_errors: list[str] = []
            page.on("pageerror", lambda error: browser_errors.append(str(error)))
            page.on("console", lambda message: browser_errors.append(message.text) if message.type == "error" else None)
            # Interception is installed before navigation. Continue only to our local fixture server.
            def fixtures_only(route: Route) -> None:
                request = route.request
                path = urlparse(request.url).path
                if path.startswith("/api/"):
                    route.continue_(url=base_url.rstrip("/") + path)
                else:
                    route.continue_()

            page.route("**/api/**", fixtures_only)
            theme_variant = {"value": "normal"}

            def themed_config(route: Route) -> None:
                path = urlparse(route.request.url).path
                if theme_variant["value"] == "normal" or not path.endswith("/claude.json"):
                    route.continue_()
                    return
                response = route.fetch()
                config = json.loads(response.text())
                if theme_variant["value"] == "invalid-palette":
                    config["modes"]["dark"].pop("--bg", None)
                elif theme_variant["value"] == "unknown-placement":
                    config["layout"]["modelPicker"] = "footer"
                elif theme_variant["value"] == "missing-logo":
                    config["branding"]["logoLight"] = "/assets/themes/assets/missing-playwright-logo.svg"
                route.fulfill(response=response, body=json.dumps(config))

            page.route("**/assets/themes/*.json", themed_config)
            page.goto(base_url, wait_until="domcontentloaded")
            run_checks(page, failures, args.scenario, theme_variant)
            check(not browser_errors, "no browser console or uncaught page errors", failures)
            for error in browser_errors:
                print("  browser error:", error)
            if args.screenshots:
                # Capture a clean, deterministic history page separate from interaction checks.
                PreviewHandler.scenario = "history"
                gallery_context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
                gallery_page = gallery_context.new_page()
                gallery_page.route("**/api/**", lambda route: route.continue_(url=base_url.rstrip("/") + urlparse(route.request.url).path))
                gallery_page.goto(base_url, wait_until="networkidle")
                capture_gallery(gallery_page)
                gallery_context.close()

                # Add one clean empty-state reference for each theme family.
                PreviewHandler.scenario = "empty"
                empty_context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
                empty_page = empty_context.new_page()
                empty_page.route("**/api/**", lambda route: route.continue_(url=base_url.rstrip("/") + urlparse(route.request.url).path))
                empty_page.goto(base_url, wait_until="networkidle")
                OUT.mkdir(parents=True, exist_ok=True)
                for theme, filename in (("chatgpt", "chatgpt"), ("claude", "claude"), ("github-copilot", "github-copilot")):
                    set_appearance(empty_page, theme, "dark")
                    empty_page.screenshot(path=str(OUT / f"{filename}-dark-empty.png"), full_page=True, animations="disabled")
                empty_context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nAll theme checks passed.")
    if args.screenshots:
        print(f"Screenshots: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
