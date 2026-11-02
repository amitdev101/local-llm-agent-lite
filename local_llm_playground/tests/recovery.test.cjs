// Run: node --test local_llm_playground/tests/recovery.test.cjs
// Exercises the actual controller with fake DOM/network surfaces; no browser or model.
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");

const frontend = join(__dirname, "../frontend");
const html = readFileSync(join(frontend, "index.html"), "utf8");
const app = readFileSync(join(frontend, "app.js"), "utf8").replace(/\r/g, "")
  .replace(/^import .*;\n/, "")
  .replace(/\ninitComposerPopovers\(\);[\s\S]*$/, "");
const theme = readFileSync(join(frontend, "theme.js"), "utf8");
const words = vm.runInNewContext(theme.slice(theme.indexOf("const defaults ="), theme.indexOf("const defaultIcons")) + "defaults");

class Element {
  constructor() {
    this.value = ""; this.checked = false; this.dataset = {}; this.hidden = false;
    this.children = []; this.nodes = new Map(); this.options = []; this.selectedOptions = [];
    this.style = {}; this.attributes = {}; this.className = ""; this.scrollHeight = 100;
    const classes = new Set();
    this.classList = { add: k => classes.add(k), remove: k => classes.delete(k),
      contains: k => classes.has(k), toggle: (k, on) => on ? classes.add(k) : classes.delete(k) };
    this.firstElementChild = { textContent: "" };
    this.open = false;
  }
  set innerHTML(value) { this.markup = value; this.children = []; }
  set textContent(value) { this.plainText = String(value); this.textWrites = (this.textWrites || 0) + 1; }
  get textContent() { return this.plainText || ""; }
  set value(value) { this.controlValue = value == null ? "" : String(value); }
  get value() { return this.controlValue || ""; }
  get innerHTML() { return this.markup || ""; }
  get lastElementChild() { return this.children.at(-1); }
  querySelector(selector) {
    if (selector === "details") return null;
    if (!this.nodes.has(selector)) this.nodes.set(selector, new Element());
    return this.nodes.get(selector);
  }
  querySelectorAll(selector) {
    return this.children.filter(node => node.className.split(" ").includes(selector.slice(1)));
  }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attributes[key] = value; }
  matches() { return Boolean(this.popoverOpen); }
  showPopover() { this.popoverOpen = true; }
  hidePopover() { this.popoverOpen = false; }
  checkValidity() { return this.valid !== false; }
  reportValidity() { this.reported = true; return this.checkValidity(); }
  getClientRects() { return this.hidden ? [] : [{}]; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  focus() { this.focused = true; }
}

const token = text => JSON.stringify({ type: "token", text }) + "\n";
const done = (type = "done") => JSON.stringify({ type }) + "\n";
function stream(parts) {
  let index = 0;
  return new Response(new ReadableStream({ pull(controller) {
    if (index === parts.length) return controller.close();
    const part = parts[index++];
    if (part instanceof Error) return controller.error(part);
    controller.enqueue(typeof part === "string" ? new TextEncoder().encode(part) : part);
  } }));
}

function setup(options = {}) {
  const ui = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  Object.assign(ui.prompt, { value: "Original prompt" });
  Object.assign(ui.contextSize, { value: "4096" });
  Object.assign(ui.temperature, { value: "0.6" });
  Object.assign(ui.systemPrompt, { value: "Original instructions" });
  ui.thinking.checked = true;
  ui.quickModelSelect.value = ui.modelSelect.value = "/models/test.gguf";
  ui.gpuLayers.value = "0"; ui.threads.value = "4";
  ui.draftWarning.hidden = true;
  ui.settingsPanel.hidden = true;
  ui.responseSettingsError.hidden = true;
  const lifecycle = {}, documentEvents = {};
  const document = { body: new Element(), activeElement: null, visibilityState: "visible",
    getElementById: id => ui[id], createElement: () => new Element(), querySelector: () => new Element(),
    addEventListener(type, handler) { documentEvents[type] = handler; } };
  const requests = [], copied = [], toasts = [], timers = new Set();
  const timeouts = new Map();
  const storage = options.storage || new Map();
  const localStorage = options.localStorage || { getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  let requestIndex = 0, timerIndex = 0, statusCalls = 0;
  const models = options.models || [{ path: "/models/test.gguf", name: "test.gguf", size: 1024, thinking_support: "supported" }];
  const baseResponse = { system_prompt: "Original instructions", thinking: true, temperature: 0.6,
    top_p: 0.95, top_k: 40, min_p: 0.05, repeat_penalty: 1, seed: -1 };
  let savedSettings = { ...baseResponse, model_path: "/models/test.gguf", context_size: 4096, gpu_layers: 0, threads: 4, ...options.settings };
  let serverStatus = { status: "ready", model_path: "/models/test.gguf", thinking_support: "supported",
    loaded_settings: { model_path: "/models/test.gguf", context_size: 4096, gpu_layers: 0, threads: 4 }, ...options.initialStatus };
  const context = vm.createContext({ document, localStorage, confirm: () => true,
    window: { addEventListener(type, handler) { lifecycle[type] = handler; } },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    text: (key, values = {}) => (words[key] || key).replace(/\{(\w+)\}/g, (_, name) => values[name] ?? name),
    refreshThemeContent() {}, crypto: { randomUUID: () => `request-${++requestIndex}` },
    navigator: { clipboard: { writeText: async value => copied.push(value) } },
    TextDecoder, Uint8Array, requestAnimationFrame: fn => fn(),
    setInterval: () => { timers.add(++timerIndex); return timerIndex; }, clearInterval: id => timers.delete(id),
    setTimeout: handler => { timeouts.set(++timerIndex, handler); return timerIndex; }, clearTimeout: id => timeouts.delete(id),
    fetch: async (url, init = {}) => {
      const body = init.body ? JSON.parse(init.body) : null;
      requests.push({ url, body });
      if (url === "/api/status") {
        statusCalls++;
        if (options.status) {
          const response = await options.status(statusCalls);
          if (!response.ok) return response;
          return Response.json({ ...serverStatus, ...await response.json() });
        }
        return Response.json(serverStatus);
      }
      if (url === "/api/model/load") {
        const response = options.loadModel ? await options.loadModel(body) : Response.json({ status: "ready", model_path: body.model_path,
          thinking_support: models.find(model => model.path === body.model_path)?.thinking_support || "unknown", loaded_settings: body, load_settings_saved: true });
        if (response.ok) serverStatus = { loaded_settings: body, ...await response.clone().json() };
        return response;
      }
      if (url === "/api/chats" && init.method === "POST") return options.createChat?.() || Response.json({ id: "chat-1" });
      if (url === "/api/chats") return options.chats?.() || Response.json([]);
      if (url === "/api/models") return Response.json(models);
      if (url === "/api/settings" && init.method === "PATCH") {
        if (options.saveSettings) return options.saveSettings(body);
        savedSettings = { ...savedSettings, ...body }; return Response.json(savedSettings);
      }
      if (url === "/api/settings") return options.getSettings?.() || Response.json(savedSettings);
      if (url.startsWith("/api/chats/") && init.method === "DELETE") return options.deleteChat?.(url) || Response.json({ deleted: true });
      if (url.startsWith("/api/chats/") && !init.method) return options.getChat?.(url.slice(11)) || Response.json(options.chat || { settings: {}, messages: [] });
      if (url === "/api/generation/stop") return options.stop?.() || Response.json({ stopping: true });
      if (url.endsWith("/branch") && options.branchChat) return options.branchChat(body, url);
      if (url.endsWith("/generate")) return options.generate?.(body) || stream([JSON.stringify({ type: "start", message_id: "answer-1" }) + "\n", token("Answer"), done()]);
      throw new Error(`Unexpected request: ${url}`);
    } });
  vm.runInContext(app + `\nactiveChat = ${options.newChat ? "null" : '"chat-1"'};
    availableModels = ${JSON.stringify(models)};
    responseDefaults = ${JSON.stringify(baseResponse)};
    applySettings(responseDefaults); responseSettingsBaseline = JSON.stringify(generationValues());
    updateModelStatus(${JSON.stringify(serverStatus)});
    globalThis.controller = { send, stop, openChat, newChat, initDrafts, loadInitial, saveDraft, updateAssistant, loadModel, updateModelStatus, generationValues,
      saveResponseDefaults, saveResponseSettings, updateContext,
      state: () => ({ generating, generationTimer, activeChat, loadedModelPath, loadingModel, thinkingPreference, responseDefaults, loadedModelSettings }),
      notify: handler => { showToast = handler; } };`, context);
  context.controller.notify(message => toasts.push(message));
  return { ui, requests, copied, toasts, timers, storage, lifecycle, document, documentEvents, timeouts,
    flushTimeouts() { const pending = [...timeouts.values()]; timeouts.clear(); return pending.map(handler => handler()); },
    ...context.controller };
}

const assistants = fixture => fixture.ui.messages.querySelectorAll(".assistant");
const users = fixture => fixture.ui.messages.querySelectorAll(".message").filter(node => node.className.includes("user"));
function restored(fixture) {
  assert.equal(fixture.state().generating, false);
  assert.equal(fixture.timers.size, 0);
  assert.equal(fixture.ui.sendButton.classList.contains("hidden"), false);
  assert.equal(fixture.ui.stopButton.classList.contains("hidden"), true);
  assert.equal(fixture.ui.loadModel.disabled, false);
}

test("partial answers stay copyable, with failure details outside the answer", async () => {
  const f = setup({ generate: () => stream([token("Kept partial"), JSON.stringify({ type: "error", message: "<memory error>" }) + "\n"]) });
  await f.send();
  const answer = assistants(f)[0];
  assert.equal(answer.dataset.status, "error");
  assert.equal(answer.dataset.raw, "Kept partial");
  assert.match(answer.querySelector(".response-notice").innerHTML, /&lt;memory error&gt;/);
  assert.equal(answer.querySelector(".response-notice").attributes.role, "alert");
  assert.equal(answer.querySelector(".regenerate [data-text]").textContent, "Retry");
  assert.equal(answer.querySelector(".regenerate").disabled, false);
  await answer.querySelector(".copy-response").onclick();
  assert.deepEqual(f.copied, ["Kept partial"]);
  restored(f);
});

test("Retry reuses the request snapshot and keeps previous attempts without another user message", async () => {
  let attempt = 0;
  const f = setup({ generate: () => ++attempt === 1 ? stream([token("Original partial"), new Error("Disconnected")]) : stream([token("Recovered"), done()]) });
  await f.send();
  const failed = assistants(f)[0];
  f.ui.temperature.value = "2"; f.ui.thinking.checked = false; f.ui.systemPrompt.value = "Changed";
  f.ui.prompt.value = "A different draft";
  await failed.querySelector(".regenerate").onclick();
  const posts = f.requests.filter(item => item.url.endsWith("/generate"));
  assert.equal(JSON.stringify(posts[0].body), JSON.stringify(posts[1].body));
  assert.equal(users(f).length, 1);
  assert.equal(assistants(f).length, 2);
  assert.equal(failed.dataset.raw, "Original partial");
  assert.equal(assistants(f)[1].dataset.status, "complete");
  assert.equal(f.ui.prompt.value, "A different draft");
  assert.equal(failed.querySelector(".regenerate").disabled, true);
  restored(f);
});

test("an empty or truncated stream is a failure even without a thrown network error", async () => {
  for (const parts of [[], [token("Truncated partial")]]) {
    const f = setup({ generate: () => stream(parts) });
    await f.send();
    assert.equal(assistants(f)[0].dataset.status, "error");
    assert.match(assistants(f)[0].querySelector(".response-notice").innerHTML, /connection ended/);
    restored(f);
  }
});

test("reopening a failed chat restores its original Retry settings and error details", async () => {
  const request = { message: "Saved prompt", request_id: "saved-request", system_prompt: "Saved instructions",
    temperature: 0.35, thinking: true, top_p: 0.95, top_k: 40, min_p: 0.05, repeat_penalty: 1, seed: -1 };
  const f = setup({ chat: { settings: { temperature: 2 }, messages: [
    { type: "user", content: "Saved prompt", status: "complete", request },
    { type: "assistant", content: "Saved partial", status: "error", error: "Saved failure", request },
  ] } });
  await f.openChat("chat-1");
  const failed = assistants(f)[0];
  assert.match(failed.querySelector(".response-notice").innerHTML, /Saved failure/);
  assert.equal(failed.querySelector(".regenerate").disabled, false);
  await failed.querySelector(".regenerate").onclick();
  assert.deepEqual(f.requests.find(item => item.url.endsWith("/generate")).body, { ...request, expected_model_path: "/models/test.gguf" });
  assert.equal(users(f).length, 1);
  assert.equal(failed.dataset.raw, "Saved partial");
  restored(f);
});

test("first send creates one chat and submits one prompt", async () => {
  const f = setup({ newChat: true });
  await f.send();
  assert.equal(f.requests.filter(item => item.url === "/api/chats" && item.body).length, 1);
  assert.equal(f.requests.filter(item => item.url.endsWith("/generate")).length, 1);
  assert.equal(users(f).length, 1);
  assert.equal(f.ui.prompt.value, "");
  assert.equal(assistants(f)[0].dataset.status, "complete");
  restored(f);
});

test("a busy runtime keeps the draft and creates no duplicate attempt", async () => {
  const f = setup({ status: count => Response.json({ status: count === 1 ? "generating" : "ready" }) });
  await f.send();
  assert.equal(f.ui.prompt.value, "Original prompt");
  assert.equal(users(f).length, 0);
  assert.equal(assistants(f).length, 0);
  assert.match(f.toasts.at(-1), /already running/);
  restored(f);
});

test("malformed stream data and HTTP errors retain a recoverable failure", async () => {
  for (const generate of [() => stream([token("Partial"), "invalid json\n"]),
    () => new Response("Not JSON", { status: 502, statusText: "Bad Gateway" })]) {
    const f = setup({ generate });
    await f.send();
    assert.equal(assistants(f)[0].dataset.status, "error");
    assert.equal(assistants(f)[0].querySelector(".regenerate").disabled, false);
    restored(f);
  }
});

test("final records without a newline and split UTF-8 chunks finish successfully", async () => {
  const bytes = new TextEncoder().encode(token("Hello 🌍") + done().trimEnd());
  const f = setup({ generate: () => stream(Array.from(bytes, byte => Uint8Array.of(byte))) });
  await f.send();
  assert.equal(assistants(f)[0].dataset.status, "complete");
  assert.equal(assistants(f)[0].dataset.raw, "Hello 🌍");
  restored(f);
});

test("cleanup request failures cannot turn a successful answer into an error", async () => {
  const f = setup({ status: count => count === 1 ? Response.json({ status: "ready" }) : Promise.reject(new Error("Offline")),
    chats: () => new Response("Unavailable", { status: 503 }) });
  await f.send();
  assert.equal(assistants(f)[0].dataset.status, "complete");
  assert.equal(assistants(f)[0].querySelector(".response-notice").hidden, true);
  assert.equal(f.ui.sendButton.disabled, false);
  restored(f);
});

test("preflight and chat creation failures keep the editable draft", async () => {
  for (const options of [
    { status: count => count === 1 ? Promise.reject(new Error("Offline")) : Response.json({ status: "ready" }) },
    { newChat: true, createChat: () => new Response("Unavailable", { status: 503 }) },
  ]) {
    const f = setup(options);
    await f.send();
    assert.equal(f.ui.prompt.value, "Original prompt");
    assert.equal(assistants(f).length, 0);
    assert.equal(users(f).length, 0);
    assert.ok(f.toasts.length);
    restored(f);
  }
});

test("rapid send clicks submit only one prompt", async () => {
  let release;
  const f = setup({ status: count => count === 1 ? new Promise(resolve => { release = resolve; }) : Response.json({ status: "ready" }) });
  const first = f.send();
  const second = f.send();
  release(Response.json({ status: "ready" }));
  await Promise.all([first, second]);
  assert.equal(f.requests.filter(item => item.url.endsWith("/generate")).length, 1);
  assert.equal(users(f).length, 1);
  restored(f);
});

test("stopped responses preserve partial text and offer Retry instead of Continue", async () => {
  const f = setup({ generate: () => stream([token("Stopped partial"), done("stopped")]) });
  await f.send();
  const answer = assistants(f)[0];
  assert.equal(answer.dataset.status, "interrupted");
  assert.equal(answer.dataset.raw, "Stopped partial");
  assert.equal(answer.querySelector(".continue-response").hidden, true);
  assert.equal(answer.querySelector(".regenerate").disabled, false);
  restored(f);
});

test("a failed Stop request leaves Stop available while the stream continues", async () => {
  let output;
  const f = setup({ stop: () => new Response("Unavailable", { status: 503 }),
    generate: () => new Response(new ReadableStream({ start(controller) { output = controller; } })) });
  const sending = f.send();
  for (let i = 0; i < 20 && !output; i++) await new Promise(resolve => setImmediate(resolve));
  await f.stop();
  assert.equal(f.ui.stopButton.disabled, false);
  assert.match(f.toasts.at(-1), /Couldn't stop/);
  output.enqueue(new TextEncoder().encode(token("Answer") + done())); output.close();
  await sending;
  restored(f);
});

const draftKey = id => `playground.draft.v1:${id || "new"}`;
const activeKey = "playground.activeChat.v1";
function typeDraft(f, value) { f.ui.prompt.value = value; f.ui.prompt.oninput(); }
async function until(predicate) {
  for (let i = 0; i < 30 && !predicate(); i++) await new Promise(resolve => setImmediate(resolve));
  assert.ok(predicate(), "operation reached the expected point");
}

test("each chat and the unsent new chat restore their own exact drafts", async () => {
  const storage = new Map([[activeKey, "chat-a"], [draftKey("chat-a"), "Draft A"],
    [draftKey("chat-b"), "Draft B"], [draftKey(null), "Unsent new chat"]]);
  const f = setup({ storage }); f.initDrafts();
  typeDraft(f, "  Revised A\n\nwith 🌍 and spacing  ");
  await f.openChat("chat-b");
  assert.equal(f.ui.prompt.value, "Draft B");
  typeDraft(f, "Revised B");
  await f.openChat("chat-a");
  assert.equal(f.ui.prompt.value, "  Revised A\n\nwith 🌍 and spacing  ");
  await f.newChat();
  assert.equal(f.ui.prompt.value, "Unsent new chat");
  typeDraft(f, "Revised new chat");
  await f.openChat("chat-b");
  assert.equal(f.ui.prompt.value, "Revised B");
  await f.newChat();
  assert.equal(f.ui.prompt.value, "Revised new chat");
  assert.equal(f.requests.filter(item => item.url === "/api/chats" && item.body).length, 0);
});

test("refresh restores the last-opened chat even when another chat is more recent", async () => {
  const storage = new Map([[activeKey, "chat-a"], [draftKey("chat-a"), "Saved A"]]);
  const chats = () => Response.json([
    { id: "chat-b", title: "More recent", updated_at: "2026-10-02" },
    { id: "chat-a", title: "Last opened", updated_at: "2026-10-01" },
  ]);
  const first = setup({ storage, chats }); first.initDrafts(); await first.loadInitial();
  assert.equal(first.state().activeChat, "chat-a");
  typeDraft(first, "Last keystroke before refresh");
  first.lifecycle.pagehide();
  const reloaded = setup({ storage, chats }); reloaded.initDrafts(); await reloaded.loadInitial();
  assert.equal(reloaded.state().activeChat, "chat-a");
  assert.equal(reloaded.ui.prompt.value, "Last keystroke before refresh");
  assert.equal(reloaded.requests.some(item => item.url.endsWith("/generate")), false);
});

test("a refreshed new-chat draft stays on New chat while saved chats exist", async () => {
  const f = setup({ storage: new Map([[activeKey, "new"], [draftKey(null), "Still composing"]]),
    chats: () => Response.json([{ id: "chat-a", title: "Existing", updated_at: "2026-10-01" }]) });
  f.initDrafts(); await f.loadInitial();
  assert.equal(f.state().activeChat, null);
  assert.equal(f.ui.prompt.value, "Still composing");
});

test("typing is debounced and visibility/pagehide flush the latest text", () => {
  const f = setup({ storage: new Map([[activeKey, "chat-a"]]) }); f.initDrafts();
  typeDraft(f, "One"); typeDraft(f, "Two");
  assert.equal(f.timeouts.size, 1);
  assert.equal(f.storage.has(draftKey("chat-a")), false);
  f.flushTimeouts();
  assert.equal(f.storage.get(draftKey("chat-a")), "Two");
  typeDraft(f, "Three"); f.document.visibilityState = "hidden"; f.documentEvents.visibilitychange();
  assert.equal(f.storage.get(draftKey("chat-a")), "Three");
  typeDraft(f, "Four"); f.lifecycle.pagehide();
  assert.equal(f.storage.get(draftKey("chat-a")), "Four");
  assert.equal(f.timeouts.size, 0);
});

test("accepted sends clear only the submitted draft; rejected sends survive refresh", async () => {
  for (const accepted of [true, false]) {
    const storage = new Map([[activeKey, "chat-a"], [draftKey("chat-a"), "Submitted prompt"], [draftKey("chat-b"), "Other draft"]]);
    const f = setup({ storage, generate: () => accepted ? stream([token("Answer"), done()]) : new Response("Unavailable", { status: 503 }) });
    f.initDrafts(); await f.send(); f.lifecycle.pagehide();
    const reloaded = setup({ storage }); reloaded.initDrafts();
    assert.equal(reloaded.ui.prompt.value, accepted ? "" : "Submitted prompt");
    assert.equal(storage.get(draftKey("chat-b")), "Other draft");
    restored(f);
  }
});

test("typing while Send waits preserves newer edits, including edits back to the same text", async () => {
  for (const backToOriginal of [false, true]) {
    let accept;
    const f = setup({ storage: new Map([[activeKey, "chat-a"], [draftKey("chat-a"), "Original prompt"]]),
      generate: () => new Promise(resolve => { accept = resolve; }) });
    f.initDrafts(); const sending = f.send();
    await until(() => Boolean(accept));
    typeDraft(f, "A newer draft");
    if (backToOriginal) typeDraft(f, "Original prompt");
    accept(stream([token("Answer"), done()])); await sending; f.lifecycle.pagehide();
    const expected = backToOriginal ? "Original prompt" : "A newer draft";
    assert.equal(f.ui.prompt.value, expected);
    assert.equal(f.storage.get(draftKey("chat-a")), expected);
  }
});

test("creating a chat transfers the draft and preserves edits made during creation", async () => {
  for (const edit of [null, "Next draft", "Same text again"]) {
    let create;
    const storage = new Map([[activeKey, "new"], [draftKey(null), "First prompt"]]);
    const f = setup({ storage, createChat: () => new Promise(resolve => { create = resolve; }) });
    f.initDrafts(); const sending = f.send();
    await until(() => Boolean(create));
    if (edit) typeDraft(f, edit);
    if (edit === "Same text again") typeDraft(f, "First prompt");
    create(Response.json({ id: "created-chat" })); await sending; f.lifecycle.pagehide();
    const expected = edit === "Same text again" ? "First prompt" : edit || "";
    assert.equal(f.state().activeChat, "created-chat");
    assert.equal(f.ui.prompt.value, expected);
    assert.equal(storage.get(draftKey("created-chat")) || "", expected);
    assert.equal(storage.has(draftKey(null)), false);
    assert.equal(storage.get(activeKey), "created-chat");
  }
});

test("Retry and Continue preserve an unrelated composer draft", async () => {
  for (const action of ["retry", "continue"]) {
    let attempt = 0;
    const f = setup({ generate: () => ++attempt === 1 && action === "retry" ?
      stream([JSON.stringify({ type: "error", message: "Try again" }) + "\n"]) : stream([token("Answer"), done()]) });
    await f.send();
    // Matching the action's prompt still does not make this an explicitly sent draft.
    const draft = action === "retry" ? "Original prompt" : "Continue.";
    typeDraft(f, draft);
    const button = assistants(f)[0].querySelector(action === "retry" ? ".regenerate" : ".continue-response");
    await button.onclick(); f.saveDraft();
    assert.equal(f.ui.prompt.value, draft);
    assert.equal(f.storage.get(draftKey("chat-1")), draft);
  }
});

test("unavailable browser storage keeps drafts in this tab and shows a warning", async () => {
  const unavailable = () => { throw new Error("Storage disabled"); };
  const f = setup({ localStorage: { getItem: unavailable, setItem: unavailable, removeItem: unavailable } });
  f.initDrafts();
  typeDraft(f, "Kept in memory");
  await f.openChat("chat-b");
  await f.newChat();
  assert.equal(f.ui.prompt.value, "Kept in memory");
  assert.equal(f.ui.draftWarning.hidden, false);
  assert.equal(f.requests.some(item => item.url.endsWith("/generate")), false);
});

test("quota failures preserve both drafts and clear the warning after successful saves", async () => {
  let blocked = true;
  const storage = new Map([[activeKey, "chat-a"], [draftKey("chat-a"), "Original A"]]);
  const localStorage = { getItem: key => storage.get(key) ?? null,
    setItem(key, value) { if (blocked) throw new Error("Quota exceeded"); storage.set(key, value); },
    removeItem(key) { if (blocked) throw new Error("Quota exceeded"); storage.delete(key); } };
  const f = setup({ storage, localStorage }); f.initDrafts();
  typeDraft(f, "Revised A"); f.flushTimeouts();
  await f.openChat("chat-b"); typeDraft(f, "Revised B"); f.flushTimeouts();
  await f.openChat("chat-a");
  assert.equal(f.ui.prompt.value, "Revised A");
  assert.equal(f.ui.draftWarning.hidden, false);
  blocked = false; f.saveDraft();
  await f.openChat("chat-b"); f.saveDraft();
  assert.equal(f.ui.prompt.value, "Revised B");
  assert.equal(storage.get(draftKey("chat-a")), "Revised A");
  assert.equal(storage.get(draftKey("chat-b")), "Revised B");
  assert.equal(f.ui.draftWarning.hidden, true);
});

test("deleting a chat removes its draft and preserves the unsent new-chat draft", async () => {
  let chats = [{ id: "chat-a", title: "Delete me", updated_at: "2026-10-01" }];
  const storage = new Map([[activeKey, "chat-a"], [draftKey("chat-a"), "Deleted draft"], [draftKey(null), "Other unsent work"]]);
  const f = setup({ storage, chats: () => Response.json(chats), deleteChat: () => { chats = []; return Response.json({ deleted: true }); } });
  f.initDrafts(); await f.loadInitial();
  typeDraft(f, "Newest deleted draft");
  const row = f.ui.chatList.children.find(node => node.className === "chat-row");
  await row.querySelector(".delete-chat").onclick({ stopPropagation() {} });
  f.flushTimeouts();
  assert.equal(storage.has(draftKey("chat-a")), false);
  assert.equal(f.ui.prompt.value, "Other unsent work");
  assert.equal(f.state().activeChat, null);
});

test("a late chat response cannot replace a newer selection or lose edits made while loading", async () => {
  const pending = {};
  const storage = new Map([[activeKey, "chat-a"], [draftKey("chat-a"), "A"],
    [draftKey("chat-b"), "B"], [draftKey("chat-c"), "C"]]);
  const f = setup({ storage, getChat: id => new Promise(resolve => { pending[id] = resolve; }) });
  f.initDrafts();
  const first = f.openChat("chat-b"), second = f.openChat("chat-c");
  typeDraft(f, "Latest edit to A");
  pending["chat-c"](Response.json({ settings: {}, messages: [] })); await second;
  assert.equal(storage.get(draftKey("chat-a")), "Latest edit to A");
  pending["chat-b"](Response.json({ settings: {}, messages: [] })); await first;
  assert.equal(f.state().activeChat, "chat-c");
  assert.equal(f.ui.prompt.value, "C");
});

test("a missing last-opened chat falls back without putting its draft in another chat", async () => {
  const f = setup({ storage: new Map([[activeKey, "deleted-chat"], [draftKey("deleted-chat"), "Old draft"], [draftKey("chat-a"), "A draft"]]),
    chats: () => Response.json([{ id: "chat-a", title: "Available", updated_at: "2026-10-01" }]) });
  f.initDrafts(); await f.loadInitial();
  assert.equal(f.state().activeChat, "chat-a");
  assert.equal(f.ui.prompt.value, "A draft");
  assert.equal(f.storage.get(draftKey("deleted-chat")), "Old draft");
});

test("a failed chat switch keeps the current draft and selection", async () => {
  const f = setup({ storage: new Map([[activeKey, "chat-a"], [draftKey("chat-a"), "A draft"]]),
    getChat: () => Promise.reject(new Error("Offline")) });
  f.initDrafts(); typeDraft(f, "Newest A draft");
  await assert.rejects(f.openChat("chat-b"), /Offline/);
  assert.equal(f.state().activeChat, "chat-a");
  assert.equal(f.ui.prompt.value, "Newest A draft");
  assert.equal(f.storage.get(draftKey("chat-a")), "Newest A draft");
});

test("theme changes leave the exact draft, pending save, and generation settings intact", () => {
  const f = setup({ storage: new Map([[activeKey, "chat-a"]]) }); f.initDrafts();
  typeDraft(f, "  A multiline\ndraft 🌍  ");
  const requestsBefore = f.requests.length;
  f.documentEvents.themechange(); f.flushTimeouts();
  assert.equal(f.storage.get(draftKey("chat-a")), "  A multiline\ndraft 🌍  ");
  assert.equal(f.ui.prompt.value, "  A multiline\ndraft 🌍  ");
  assert.equal(f.ui.temperature.value, "0.6");
  assert.equal(f.ui.thinking.checked, true);
  assert.equal(f.requests.length, requestsBefore);
});

const otherModel = { path: "/models/other.gguf", name: "other.gguf", size: 2048, thinking_support: "supported" };
const testModels = [
  { path: "/models/test.gguf", name: "test.gguf", size: 1024, thinking_support: "supported" }, otherModel,
];
function selectModel(f, path) { f.ui.quickModelSelect.value = path; f.ui.quickModelSelect.onchange(); }

test("selecting another model does not load it and clearly separates selected from loaded", () => {
  const f = setup({ models: testModels });
  selectModel(f, otherModel.path);
  assert.equal(f.requests.length, 0);
  assert.equal(f.ui.selectedModelState.textContent, "Selected · not loaded");
  assert.match(f.ui.loadedModelInfo.textContent, /Currently loaded: test/);
  assert.equal(f.ui.activeModel.textContent, "test.gguf");
  assert.equal(f.ui.modelReady.hidden, true);
  assert.equal(f.ui.quickLoadModel.hidden, false);
  assert.equal(f.ui.menuLoadModel.hidden, false);
  assert.equal(f.ui.sendButton.disabled, true);
  assert.match(f.ui.composerStatus.textContent, /Load the selected model/);
  assert.equal(f.ui.prompt.value, "Original prompt");
});

test("explicit Load makes the selected model ready without clearing the draft", async () => {
  const f = setup({ models: testModels });
  selectModel(f, otherModel.path);
  await f.ui.menuLoadModel.onclick();
  assert.equal(f.requests.filter(item => item.url === "/api/model/load").length, 1);
  assert.equal(f.state().loadedModelPath, otherModel.path);
  assert.equal(f.ui.selectedModelState.textContent, "Loaded");
  assert.equal(f.ui.modelReady.hidden, false);
  assert.equal(f.ui.quickLoadModel.hidden, true);
  assert.equal(f.ui.sendButton.disabled, false);
  assert.equal(f.ui.prompt.value, "Original prompt");
  assert.equal(f.ui.quickThinking.checked, true);
});

test("a pending load disables model changes and ignores repeated Load clicks", async () => {
  let complete;
  const f = setup({ models: testModels, loadModel: () => new Promise(resolve => { complete = resolve; }) });
  selectModel(f, otherModel.path);
  const loading = f.loadModel(); await f.loadModel();
  assert.equal(f.requests.filter(item => item.url === "/api/model/load").length, 1);
  assert.equal(f.ui.selectedModelState.textContent, "Loading…");
  for (const control of [f.ui.modelSelect, f.ui.quickModelSelect, f.ui.customModel, f.ui.sendButton, f.ui.menuLoadModel, f.ui.quickThinking]) assert.equal(control.disabled, true);
  complete(Response.json({ status: "ready", model_path: otherModel.path, thinking_support: "supported" }));
  await loading;
  assert.equal(f.state().loadingModel, false);
  assert.equal(f.ui.modelSelect.disabled, false);
  assert.equal(f.ui.sendButton.disabled, false);
  assert.equal(f.timers.size, 0);
});

test("unknown and unsupported Thinking states preserve the preference for supported models", () => {
  for (const support of ["unknown", "unsupported"]) {
    const f = setup({ models: [testModels[0], { ...otherModel, thinking_support: support }] });
    selectModel(f, otherModel.path);
    assert.equal(f.ui.quickThinking.disabled, true);
    assert.equal(f.ui.thinking.disabled, true);
    assert.equal(f.ui.quickThinking.checked, false);
    assert.equal(f.ui.thinkingState.textContent, support === "unknown" ? "Unknown" : "Unavailable");
    assert.equal(f.generationValues().thinking, true);
    selectModel(f, testModels[0].path);
    assert.equal(f.ui.quickThinking.disabled, false);
    assert.equal(f.ui.quickThinking.checked, true);
    f.ui.quickThinking.checked = false; f.ui.quickThinking.onchange();
    assert.equal(f.ui.thinking.checked, false);
    assert.equal(f.generationValues().thinking, false);
    selectModel(f, otherModel.path); selectModel(f, testModels[0].path);
    assert.equal(f.ui.thinkingState.textContent, "Off");
    f.ui.thinking.checked = true; f.ui.thinking.onchange();
    assert.equal(f.ui.quickThinking.checked, true);
  }
});

test("loaded capability metadata overrides stale catalog support", () => {
  const f = setup();
  f.updateModelStatus({ status: "ready", model_path: "/models/test.gguf", thinking_support: "unknown" });
  assert.equal(f.ui.thinkingState.textContent, "Unknown");
  assert.equal(f.ui.quickThinking.disabled, true);
  f.updateModelStatus({ status: "ready", model_path: "/models/test.gguf", thinking_support: "supported" });
  assert.equal(f.ui.thinkingState.textContent, "On");
});

test("an empty selection never claims Thinking is Off or a model is ready", () => {
  const f = setup(); selectModel(f, "");
  f.updateModelStatus({ status: "not_loaded", model_path: "", thinking_support: "unknown" });
  assert.equal(f.ui.thinkingState.textContent, "Unavailable");
  assert.equal(f.ui.quickThinking.disabled, true);
  assert.equal(f.ui.modelReady.hidden, true);
  assert.equal(f.ui.sendButton.disabled, true);
  assert.equal(f.ui.loadModel.disabled, true);
  assert.equal(f.ui.activeModel.textContent, "No model loaded");
});

test("a changed runtime model blocks Send before submitting or clearing the draft", async () => {
  const f = setup({ models: testModels, status: () => Response.json({ status: "ready", model_path: otherModel.path, thinking_support: "supported" }) });
  await f.send();
  assert.equal(f.requests.some(item => item.url.endsWith("/generate")), false);
  assert.equal(users(f).length, 0);
  assert.equal(f.ui.prompt.value, "Original prompt");
  assert.equal(f.ui.activeModel.textContent, "other.gguf");
  assert.equal(f.ui.sendButton.disabled, true);
});

test("generation requests identify the selected model for the server guard", async () => {
  const f = setup(); await f.send();
  const request = f.requests.find(item => item.url.endsWith("/generate"));
  assert.equal(request.body.expected_model_path, "/models/test.gguf");
});

test("a failed load keeps persistent details and can return to the still-loaded model", async () => {
  const f = setup({ models: testModels, loadModel: () => Response.json({ detail: "Select a valid GGUF model" }, { status: 400 }) });
  selectModel(f, otherModel.path); await f.loadModel();
  assert.equal(f.ui.selectedModelState.textContent, "Selected · not loaded");
  assert.equal(f.ui.sendButton.disabled, true);
  assert.equal(f.ui.modelMenuError.hidden, false);
  assert.equal(f.ui.modelLoadError.textContent, "Select a valid GGUF model");
  assert.equal(f.ui.activeModel.textContent, "test.gguf");
  selectModel(f, "/models/test.gguf");
  assert.equal(f.ui.modelMenuError.hidden, true);
  assert.equal(f.ui.modelLoadError.hidden, true);
  assert.equal(f.ui.sendButton.disabled, false);
});

test("a failed replacement that unloads the runtime clears the previous loaded name", async () => {
  const f = setup({ models: testModels, loadModel: () => Response.json({ detail: "Out of memory" }, { status: 500 }),
    status: () => Response.json({ status: "error", model_path: "", error: "Out of memory", thinking_support: "unknown" }) });
  selectModel(f, otherModel.path); await f.loadModel();
  assert.equal(f.state().loadedModelPath, "");
  assert.equal(f.ui.activeModel.textContent, "No model loaded");
  assert.equal(f.ui.modelReady.hidden, true);
  assert.equal(f.ui.sendButton.disabled, true);
  assert.equal(f.ui.modelLoadError.hidden, false);
});

test("custom paths use the server's normalized loaded path and capability", async () => {
  const canonical = "/outside/models/custom.gguf";
  const f = setup({ loadModel: () => Response.json({ status: "ready", model_path: canonical, thinking_support: "supported" }) });
  f.ui.customModel.value = "../models/custom.gguf"; f.ui.customModel.oninput();
  assert.equal(f.ui.thinkingState.textContent, "Unknown");
  await f.loadModel();
  assert.equal(f.ui.customModel.value, canonical);
  assert.equal(f.ui.sendButton.disabled, false);
  assert.equal(f.ui.thinkingState.textContent, "On");
});

test("custom model details do not show an unrelated detected-model selection", () => {
  const f = setup();
  f.ui.customModel.value = "/outside/models/custom.gguf"; f.ui.customModel.oninput();
  assert.equal(f.ui.quickModelSelect.hidden, true);
  assert.equal(f.ui.chooseDetectedModel.hidden, false);
  assert.equal(f.ui.selectedModelPath.textContent, "/outside/models/custom.gguf");
  f.ui.chooseDetectedModel.onclick();
  assert.equal(f.ui.customModel.value, "");
  assert.equal(f.ui.quickModelSelect.hidden, false);
  assert.equal(f.ui.chooseDetectedModel.hidden, true);
  assert.equal(f.ui.selectedModelState.textContent, "Loaded");
  assert.equal(f.requests.length, 0);
});

test("loading-settings save errors do not mislabel a successful model load as failed", async () => {
  const f = setup({ models: testModels, loadModel: () => Response.json({ status: "ready", model_path: otherModel.path,
    thinking_support: "supported", load_settings_saved: false }) });
  selectModel(f, otherModel.path); await f.loadModel();
  assert.equal(f.ui.selectedModelState.textContent, "Loaded");
  assert.equal(f.ui.modelMenuError.hidden, true);
  assert.equal(f.ui.sendButton.disabled, false);
  assert.match(f.toasts.at(-1), /loading settings couldn't be remembered/);
  assert.equal(f.requests.some(item => item.url === "/api/settings" && item.body), false);
});

test("response metrics identify the model that answered and keep unknown support explicit", async () => {
  const f = setup(); await f.send();
  const answer = assistants(f)[0];
  f.updateAssistant(answer, "Saved answer", { temperature: 0.6, thinking: true, thinking_support: "unknown", model: "OriginalModel-7B-Q8_0.gguf" });
  assert.match(answer.querySelector(".metrics").innerHTML, /Model: OriginalModel · 7B/);
  assert.match(answer.querySelector(".metrics").innerHTML, /Thinking: Unknown/);
});

test("runtime loading from another client disables controls and hides the ready dot", () => {
  const f = setup();
  f.updateModelStatus({ status: "loading", model_path: "/models/test.gguf", thinking_support: "supported" });
  assert.equal(f.ui.selectedModelState.textContent, "Loading…");
  assert.equal(f.ui.modelReady.hidden, true);
  assert.equal(f.ui.sendButton.disabled, true);
  assert.equal(f.ui.quickThinking.disabled, true);
  assert.equal(f.ui.loadModel.disabled, true);
});

test("an error response cannot retain a stale loaded-model name", () => {
  const f = setup();
  f.updateModelStatus({ status: "error", model_path: "/models/test.gguf", error: "Load failed" });
  assert.equal(f.state().loadedModelPath, "");
  assert.equal(f.ui.activeModel.textContent, "No model loaded");
  assert.equal(f.ui.modelReady.hidden, true);
  assert.equal(f.ui.sendButton.disabled, true);
});

test("a remote load or generation returns to Ready automatically without changing the draft", async () => {
  for (const status of ["loading", "generating"]) {
    const f = setup();
    f.updateModelStatus({ status, model_path: "/models/test.gguf", thinking_support: "supported" });
    assert.equal(f.ui.sendButton.disabled, true);
    await Promise.all(f.flushTimeouts());
    assert.equal(f.ui.sendButton.disabled, false);
    assert.equal(f.ui.modelReady.hidden, false);
    assert.equal(f.ui.prompt.value, "Original prompt");
    assert.equal(f.timeouts.size, 0);
    assert.equal(users(f).length, 0);
  }
});

test("a failed busy-state refresh shows a connection error and restores selection controls", async () => {
  const f = setup({ status: () => Promise.reject(new Error("Offline")) });
  f.updateModelStatus({ status: "loading", model_path: "/models/test.gguf", thinking_support: "supported" });
  await Promise.all(f.flushTimeouts());
  assert.match(f.ui.composerStatus.textContent, /Can't reach the local server/);
  assert.equal(f.ui.modelSelect.disabled, false);
  assert.equal(f.ui.loadModel.disabled, false);
  assert.equal(f.ui.sendButton.disabled, true);
  assert.equal(f.ui.prompt.value, "Original prompt");
  assert.equal(f.timeouts.size, 0);
});

function keyEvent(key, flags = {}) {
  return { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...flags };
}

test("Enter respects composition, Shift, modifiers, and key repeat before sending", async () => {
  const f = setup();
  for (const flags of [{ isComposing: true }, { keyCode: 229 }, { shiftKey: true }, { ctrlKey: true },
    { metaKey: true }, { altKey: true }, { repeat: true }, { defaultPrevented: true }]) {
    const event = keyEvent("Enter", flags); f.ui.prompt.onkeydown(event);
    if (!flags.defaultPrevented) assert.equal(event.defaultPrevented, false);
  }
  f.ui.prompt.oncompositionstart();
  const composing = keyEvent("Enter"); f.ui.prompt.onkeydown(composing);
  assert.equal(composing.defaultPrevented, false);
  assert.equal(f.requests.length, 0);
  f.ui.prompt.oncompositionend();
  const enter = keyEvent("Enter"); await f.ui.prompt.onkeydown(enter);
  assert.equal(enter.defaultPrevented, true);
  assert.equal(f.requests.filter(item => item.url.endsWith("/generate")).length, 1);
});

test("Copy shortcuts remain native while Ctrl/Cmd+Shift+X stops generation", async () => {
  for (const modifier of ["ctrlKey", "metaKey"]) {
    let output;
    const f = setup({ generate: () => new Response(new ReadableStream({ start(controller) { output = controller; } })) });
    const sending = f.send(); await until(() => Boolean(output));
    const copy = keyEvent("c", { [modifier]: true }); f.documentEvents.keydown(copy);
    assert.equal(copy.defaultPrevented, false);
    assert.equal(f.requests.some(item => item.url === "/api/generation/stop"), false);
    const stop = keyEvent("X", { [modifier]: true, shiftKey: true }); f.documentEvents.keydown(stop);
    await until(() => f.requests.some(item => item.url === "/api/generation/stop"));
    assert.equal(stop.defaultPrevented, true);
    output.enqueue(new TextEncoder().encode(done("stopped"))); output.close(); await sending;
    restored(f);
  }
});

test("Escape lets native popovers dismiss before closing a settings dialog", () => {
  const f = setup(); f.ui.optionsPopover.showPopover();
  const first = keyEvent("Escape"); f.documentEvents.keydown(first);
  assert.equal(first.defaultPrevented, false);
  f.ui.optionsPopover.hidePopover(); f.ui.settingsPanel.hidden = false;
  const second = keyEvent("Escape"); f.documentEvents.keydown(second);
  assert.equal(second.defaultPrevented, true);
  assert.equal(f.ui.settingsPanel.hidden, true);
});

function sourceConversation() {
  const first = { message: "First question", request_id: "turn-1", system_prompt: "Original system", thinking: true,
    temperature: 0.35, top_p: 0.9, top_k: 20, min_p: 0.05, repeat_penalty: 1, seed: 42 };
  const second = { ...first, message: "Second question", request_id: "turn-2", temperature: 1.2 };
  return { id: "chat-1", settings: second, first, second, messages: [
    { id: "turn-1", type: "user", content: first.message, status: "complete", request: first },
    { id: "answer-one", type: "assistant", content: "First answer", status: "complete", request: first },
    { id: "turn-2", type: "user", content: second.message, status: "complete", request: second },
    { id: "answer-two", type: "assistant", content: "Second answer", status: "complete", request: second },
  ] };
}

test("Regenerate branches the selected answer using its original prompt and settings", async () => {
  const source = sourceConversation(); const before = JSON.stringify(source);
  const f = setup({ chat: source, branchChat: body => {
    assert.equal(body.message_id, "answer-one");
    return Response.json({ id: "branch-1", settings: source.first, branch_of: { chat_id: "chat-1", message_id: body.message_id }, messages: [source.messages[0]] });
  } });
  await f.openChat("chat-1");
  f.ui.temperatureNumber.value = "1.8"; f.ui.temperatureNumber.oninput();
  typeDraft(f, "My unrelated unsent draft");
  const button = assistants(f)[0].querySelector(".regenerate");
  assert.equal(assistants(f)[0].querySelector(".regenerate [data-text]").textContent, "Regenerate in new chat");
  await button.onclick();
  const generated = f.requests.find(item => item.url.endsWith("/generate"));
  assert.equal(generated.url, "/api/chats/branch-1/generate");
  assert.equal(generated.body.message, "First question");
  assert.equal(generated.body.temperature, 0.35);
  assert.equal(generated.body.seed, 42);
  assert.equal(generated.body.request_id, "turn-1");
  assert.equal(f.state().activeChat, "branch-1");
  assert.equal(users(f).length, 1);
  assert.equal(assistants(f).length, 1);
  assert.equal(f.storage.get(draftKey("chat-1")), "My unrelated unsent draft");
  assert.equal(JSON.stringify(source), before);
  await f.openChat("chat-1");
  assert.equal(f.ui.prompt.value, "My unrelated unsent draft");
  assert.equal(f.generationValues().temperature, 1.8);
});

test("failed branch creation leaves the original chat and draft intact", async () => {
  const source = sourceConversation();
  const f = setup({ chat: source, branchChat: () => new Response("Unavailable", { status: 503 }) });
  await f.openChat("chat-1"); typeDraft(f, "Keep this draft");
  await assistants(f)[0].querySelector(".regenerate").onclick();
  assert.equal(f.state().activeChat, "chat-1");
  assert.equal(f.ui.prompt.value, "Keep this draft");
  assert.equal(assistants(f).length, 2);
  assert.equal(f.requests.some(item => item.url.endsWith("/generate")), false);
  assert.match(f.toasts.at(-1), /original chat and draft are unchanged/);
});

test("Retry of a failed alternative stays in its branch without duplicating its prompt", async () => {
  const source = sourceConversation(); let attempt = 0;
  const f = setup({ chat: source, branchChat: () => Response.json({ id: "branch-1", settings: source.first,
    branch_of: { chat_id: "chat-1", message_id: "answer-one" }, messages: [source.messages[0]] }),
    generate: () => ++attempt === 1 ? Response.json({ detail: "Failed" }, { status: 500 }) : stream([token("Recovered"), done()]) });
  await f.openChat("chat-1"); await assistants(f)[0].querySelector(".regenerate").onclick();
  assert.equal(assistants(f)[0].dataset.status, "error");
  await assistants(f)[0].querySelector(".regenerate").onclick();
  assert.equal(f.requests.filter(item => item.url.endsWith("/branch")).length, 1);
  assert.equal(users(f).length, 1);
  assert.equal(assistants(f).at(-1).dataset.status, "complete");
});

test("Continue acts only on the latest completed response and preserves the draft", async () => {
  const f = setup({ chat: sourceConversation() }); await f.openChat("chat-1");
  typeDraft(f, "An unrelated draft");
  const old = assistants(f)[0].querySelector(".continue-response");
  assert.equal(old.disabled, true); await old.onclick();
  assert.equal(f.requests.some(item => item.url.endsWith("/generate")), false);
  await assistants(f)[1].querySelector(".continue-response").onclick();
  assert.equal(f.requests.find(item => item.url.endsWith("/generate")).body.message, "Continue.");
  assert.equal(f.ui.prompt.value, "An unrelated draft");
  assert.equal(f.requests.some(item => item.url.endsWith("/branch")), false);
});

test("reasoning and response details stay collapsed and preserve a user's open choice", async () => {
  const f = setup(); await f.send(); const answer = assistants(f)[0];
  assert.equal(answer.querySelector(".thinking-box").hidden, true);
  const thinking = answer.querySelector(".thinking-box");
  f.updateAssistant(answer, "<think>Reasoning", null, "streaming", 1);
  assert.equal(thinking.hidden, false); assert.equal(thinking.open, false);
  thinking.open = true;
  f.updateAssistant(answer, "<think>More reasoning</think>Answer", { temperature: 0.6, total_seconds: 2 });
  assert.equal(answer.querySelector(".thinking-box"), thinking);
  assert.equal(thinking.open, true);
  const details = answer.querySelector(".response-details");
  assert.equal(details.hidden, false); assert.equal(details.open, false);
  details.open = true; f.documentEvents.themechange();
  assert.equal(details.open, true); assert.equal(thinking.open, true);
  assert.equal(answer.querySelector(".response-progress").hidden, true);
});

test("streaming progress does not repeatedly announce the same status", async () => {
  let output;
  const f = setup({ generate: () => new Response(new ReadableStream({ start(controller) { output = controller; } })) });
  const sending = f.send(); await until(() => Boolean(output));
  const answer = assistants(f)[0], progress = answer.querySelector(".response-progress");
  output.enqueue(new TextEncoder().encode(JSON.stringify({ type: "start", message_id: "progress-answer" }) + "\n" + token("One")));
  await until(() => progress.textContent === "Generating");
  const writes = progress.textWrites;
  f.updateAssistant(answer, "Two", null, "streaming", 2); f.updateAssistant(answer, "Three", null, "streaming", 3);
  assert.equal(progress.textWrites, writes);
  output.enqueue(new TextEncoder().encode(done())); output.close(); await sending;
});

test("response edits stay with their chat across navigation and refresh without changing defaults", async () => {
  const storage = new Map();
  const getChat = id => Response.json({ id, settings: { temperature: id === "chat-a" ? 0.35 : 0.8, thinking: true, system_prompt: id }, messages: [] });
  const f = setup({ storage, getChat }); await f.openChat("chat-a");
  f.ui.temperatureNumber.value = "1.1"; f.ui.temperatureNumber.oninput();
  f.ui.systemPrompt.value = "Edited instructions"; f.ui.systemPrompt.oninput();
  f.ui.quickThinking.checked = false; f.ui.quickThinking.onchange();
  await f.openChat("chat-b"); assert.equal(f.generationValues().temperature, 0.8);
  await f.openChat("chat-a"); assert.equal(f.generationValues().temperature, 1.1);
  assert.equal(f.generationValues().system_prompt, "Edited instructions");
  assert.equal(f.generationValues().thinking, false);
  const reloaded = setup({ storage, getChat }); reloaded.initDrafts(); await reloaded.openChat("chat-a");
  assert.equal(reloaded.generationValues().temperature, 1.1);
  assert.equal(reloaded.generationValues().system_prompt, "Edited instructions");
  assert.equal(f.requests.some(item => item.url === "/api/settings" && item.body), false);
});

test("Save as defaults changes future new chats while existing chats keep their preferences", async () => {
  const f = setup({ getChat: id => Response.json({ id, settings: { temperature: 0.2, system_prompt: "Existing" }, messages: [] }) });
  f.ui.temperatureNumber.value = "1.1"; f.ui.temperatureNumber.oninput();
  f.ui.systemPrompt.value = "Future default"; f.ui.systemPrompt.oninput();
  await f.ui.saveResponseDefaults.onclick();
  const request = f.requests.find(item => item.url === "/api/settings" && item.body);
  assert.equal(request.body.temperature, 1.1);
  assert.equal(Object.hasOwn(request.body, "context_size"), false);
  assert.match(f.ui.responseSettingsStatus.textContent, /Saved for future new chats/);
  await f.newChat(); assert.equal(f.generationValues().temperature, 1.1);
  await f.openChat("existing-chat"); assert.equal(f.generationValues().temperature, 0.2);
  f.ui.resetSettings.onclick(); assert.equal(f.generationValues().temperature, 1.1);
  assert.equal(f.requests.filter(item => item.url === "/api/settings" && item.body).length, 1);
});

test("defaults-save failure preserves current preferences and can be retried", async () => {
  let failed = true;
  const f = setup({ saveSettings: () => failed ? new Response("Unavailable", { status: 503 }) : Response.json({}) });
  f.ui.temperatureNumber.value = "1.1"; f.ui.temperatureNumber.oninput();
  await f.saveResponseDefaults();
  assert.equal(f.generationValues().temperature, 1.1);
  assert.equal(f.state().responseDefaults.temperature, 0.6);
  assert.match(f.ui.responseSettingsStatus.textContent, /Couldn't save defaults/);
  assert.equal(f.ui.saveResponseDefaults.disabled, false);
  failed = false; await f.saveResponseDefaults();
  assert.equal(f.state().responseDefaults.temperature, 1.1);
});

test("edits made while defaults save is pending stay local and are not mislabeled as saved", async () => {
  let finish;
  const f = setup({ saveSettings: () => new Promise(resolve => { finish = resolve; }) });
  f.ui.temperatureNumber.value = "1.1"; f.ui.temperatureNumber.oninput();
  const saving = f.saveResponseDefaults(); await until(() => Boolean(finish));
  f.ui.temperatureNumber.value = "1.7"; f.ui.temperatureNumber.oninput();
  finish(Response.json({})); await saving;
  assert.equal(f.generationValues().temperature, 1.7);
  assert.equal(f.state().responseDefaults.temperature, 1.1);
  assert.match(f.ui.responseSettingsStatus.textContent, /newer edits apply only to this chat/);
});

test("invalid response values are retained locally but block Send and Save as defaults", async () => {
  const storage = new Map(); const f = setup({ storage });
  f.ui.topK.value = "2001"; f.ui.topK.valid = false; f.ui.topK.oninput();
  await f.send(); await f.saveResponseDefaults();
  assert.equal(f.requests.length, 0);
  assert.equal(f.ui.topK.reported, true);
  assert.equal(f.ui.topK.attributes["aria-invalid"], "true");
  assert.equal(f.ui.responseSettingsError.hidden, false);
  f.saveResponseSettings();
  const reloaded = setup({ storage }); reloaded.initDrafts();
  await reloaded.openChat("chat-1");
  assert.equal(reloaded.ui.topK.value, "2001");
  f.ui.topK.value = "40"; f.ui.topK.valid = true; f.ui.topK.oninput();
  await f.send(); assert.equal(assistants(f).at(-1).dataset.status, "complete");
});

test("pending model settings remain unapplied until Reload and do not change context accounting", async () => {
  const f = setup();
  f.ui.contextSize.value = "8192"; f.ui.contextSize.oninput();
  assert.equal(f.ui.modelReloadNotice.hidden, false);
  assert.equal(f.ui.quickLoadModel.textContent, "Reload");
  assert.match(f.ui.modelMeta.textContent, /4096 context/);
  f.updateContext(1000); assert.match(f.ui.contextLabel.textContent, /4,096/);
  await f.send(); assert.equal(f.requests.some(item => item.url === "/api/model/load"), false);
  await f.loadModel();
  assert.equal(f.ui.modelReloadNotice.hidden, true);
  assert.match(f.ui.modelMeta.textContent, /8192 context/);
  assert.match(f.ui.contextLabel.textContent, /8,192/);
  assert.equal(f.requests.some(item => item.url === "/api/settings" && item.body), false);
});

test("invalid loading settings block Reload while normal sends keep using the loaded model", async () => {
  const f = setup(); f.ui.threads.value = "0"; f.ui.threads.valid = false;
  await f.loadModel();
  assert.equal(f.requests.length, 0); assert.equal(f.ui.threads.reported, true);
  f.ui.closeSettings.onclick(); await f.send();
  assert.equal(assistants(f).at(-1).dataset.status, "complete");
});

test("startup keeps edits made before defaults finish loading in their new-chat composition", async () => {
  let defaults;
  const f = setup({ newChat: true, chats: () => Response.json([{ id: "older-chat", title: "Older chat", updated_at: "2026-10-01" }]),
    getSettings: () => new Promise(resolve => { defaults = resolve; }) });
  f.initDrafts(); typeDraft(f, "New work while opening");
  f.ui.temperatureNumber.value = "1.4"; f.ui.temperatureNumber.oninput();
  const loading = f.loadInitial(); await until(() => Boolean(defaults));
  defaults(Response.json({ context_size: 4096, gpu_layers: 0, threads: 4, temperature: 0.2 })); await loading;
  assert.equal(f.state().activeChat, null);
  assert.equal(f.ui.prompt.value, "New work while opening");
  assert.equal(f.generationValues().temperature, 1.4);
});

test("Send reveals the new answer but streaming respects a user reading older messages", async () => {
  let output;
  const f = setup({ generate: () => new Response(new ReadableStream({ start(controller) { output = controller; } })) });
  f.ui.messages.scrollHeight = 5000; f.ui.messages.clientHeight = 500; f.ui.messages.scrollTop = 0;
  const sending = f.send(); await until(() => Boolean(output));
  assert.equal(f.ui.messages.scrollTop, 5000);
  f.ui.messages.scrollTop = 0;
  output.enqueue(new TextEncoder().encode(token("More text")));
  await until(() => assistants(f)[0].dataset.raw === "More text");
  assert.equal(f.ui.messages.scrollTop, 0);
  output.enqueue(new TextEncoder().encode(done())); output.close(); await sending;
});

test("precise numeric preferences reach generation without being rounded by the temperature slider", async () => {
  const f = setup();
  f.ui.temperatureNumber.value = "0.613"; f.ui.temperatureNumber.oninput();
  // A native range may snap to its coarser step; the numeric input owns the value.
  f.ui.temperature.value = "0.6";
  f.ui.topP.value = "0.923"; f.ui.topP.oninput();
  await f.send();
  const request = f.requests.find(item => item.url.endsWith("/generate"));
  assert.equal(request.body.temperature, 0.613);
  assert.equal(request.body.top_p, 0.923);
});
