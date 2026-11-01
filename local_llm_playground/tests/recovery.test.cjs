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
  .replace(/\ninitComposerPopovers\(\);\nawait initThemes\(\);[\s\S]*$/, "");
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
  const document = { body: new Element(), activeElement: null,
    getElementById: id => ui[id], createElement: () => new Element(), addEventListener() {} };
  const requests = [], copied = [], toasts = [], timers = new Set();
  let requestIndex = 0, timerIndex = 0, statusCalls = 0;
  const context = vm.createContext({ document, matchMedia: () => ({ matches: false, addEventListener() {} }),
    text: (key, values = {}) => (words[key] || key).replace(/\{(\w+)\}/g, (_, name) => values[name] ?? name),
    refreshThemeContent() {}, crypto: { randomUUID: () => `request-${++requestIndex}` },
    navigator: { clipboard: { writeText: async value => copied.push(value) } },
    TextDecoder, Uint8Array, requestAnimationFrame: fn => fn(),
    setInterval: () => { timers.add(++timerIndex); return timerIndex; }, clearInterval: id => timers.delete(id),
    setTimeout: () => 0, clearTimeout() {},
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
      if (url === "/api/chats/chat-1" && options.chat) return Response.json(options.chat);
      if (url === "/api/generation/stop") return options.stop?.() || Response.json({ stopping: true });
      if (url.endsWith("/generate")) return options.generate?.(body) || stream([token("Answer"), done()]);
      throw new Error(`Unexpected request: ${url}`);
    } });
  vm.runInContext(app + `\nactiveChat = ${options.newChat ? "null" : '"chat-1"'};
    globalThis.controller = { send, stop, openChat, updateAssistant,
      state: () => ({ generating, generationTimer }),
      notify: handler => { showToast = handler; } };`, context);
  context.controller.notify(message => toasts.push(message));
  return { ui, requests, copied, toasts, timers, ...context.controller };
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
