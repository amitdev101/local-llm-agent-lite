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
  }
  set innerHTML(value) { this.markup = value; this.children = []; }
  get innerHTML() { return this.markup || ""; }
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
  matches() { return false; }
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
  ui.draftWarning.hidden = true;
  const lifecycle = {}, documentEvents = {};
  const document = { body: new Element(), activeElement: null, visibilityState: "visible",
    getElementById: id => ui[id], createElement: () => new Element(),
    addEventListener(type, handler) { documentEvents[type] = handler; } };
  const requests = [], copied = [], toasts = [], timers = new Set();
  const timeouts = new Map();
  const storage = options.storage || new Map();
  const localStorage = options.localStorage || { getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  let requestIndex = 0, timerIndex = 0, statusCalls = 0;
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
        if (options.status) return options.status(statusCalls);
        return Response.json({ status: "ready", model_path: "/models/test.gguf" });
      }
      if (url === "/api/chats" && init.method === "POST") return options.createChat?.() || Response.json({ id: "chat-1" });
      if (url === "/api/chats") return options.chats?.() || Response.json([]);
      if (url === "/api/models") return Response.json([]);
      if (url === "/api/settings") return Response.json({ context_size: 4096, gpu_layers: 0, threads: 4 });
      if (url.startsWith("/api/chats/") && init.method === "DELETE") return options.deleteChat?.(url) || Response.json({ deleted: true });
      if (url.startsWith("/api/chats/") && !init.method) return options.getChat?.(url.slice(11)) || Response.json(options.chat || { settings: {}, messages: [] });
      if (url === "/api/generation/stop") return options.stop?.() || Response.json({ stopping: true });
      if (url.endsWith("/generate")) return options.generate?.(body) || stream([token("Answer"), done()]);
      throw new Error(`Unexpected request: ${url}`);
    } });
  vm.runInContext(app + `\nactiveChat = ${options.newChat ? "null" : '"chat-1"'};
    globalThis.controller = { send, stop, openChat, newChat, initDrafts, loadInitial, saveDraft, updateAssistant,
      state: () => ({ generating, generationTimer, activeChat }),
      notify: handler => { showToast = handler; } };`, context);
  context.controller.notify(message => toasts.push(message));
  return { ui, requests, copied, toasts, timers, storage, lifecycle, document, documentEvents, timeouts,
    flushTimeouts() { const pending = [...timeouts.values()]; timeouts.clear(); pending.forEach(handler => handler()); },
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
  assert.deepEqual(f.requests.find(item => item.url.endsWith("/generate")).body, request);
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
