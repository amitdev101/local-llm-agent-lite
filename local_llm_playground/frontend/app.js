const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries(
  "sidebar chatList chatSearch newChat statusDot statusText activeModel modelMeta messages welcome prompt sendButton stopButton composerStatus temperatureLabel contextBar contextLabel settingsButton settingsPanel closeSettings overlay toast menuButton modelSelect quickModelSelect quickLoadModel customModel contextSize gpuLayers threads loadModel systemPrompt systemSummary systemPreset thinking temperature temperatureNumber topP topK minP repeatPenalty seed resetSettings"
    .split(" ").map((id) => [id, $(id)])
);

const defaults = { temperature: 0, top_p: .95, top_k: 40, min_p: .05, repeat_penalty: 1, seed: -1 };
let activeChat = null;
let generating = false;
let lastUserMessage = "";
let allChats = [];
let conversationChars = 0;
let generationTimer = null;

function showToast(message) {
  ui.toast.textContent = message;
  ui.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => ui.toast.classList.remove("show"), 2200);
}

async function api(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[character]));
}

function markdown(text) {
  const blocks = [];
  let safe = escapeHtml(text).replace(/```([^\n]*)\n([\s\S]*?)```/g, (_, language, code) => {
    const index = blocks.length;
    blocks.push(`<pre><button class="copy-code">Copy</button><code data-language="${language.trim()}">${code}</code></pre>`);
    return `@@BLOCK${index}@@`;
  });
  safe = safe
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .split(/\n{2,}/).map((part) => part.startsWith("@@BLOCK") ? part : `<p>${part.replace(/\n/g, "<br>")}</p>`).join("");
  blocks.forEach((block, index) => { safe = safe.replace(`<p>@@BLOCK${index}@@</p>`, block).replace(`@@BLOCK${index}@@`, block); });
  return safe;
}

function splitThinking(raw) {
  const start = raw.indexOf("<think>");
  if (start < 0) return { thinking: "", answer: raw };
  const end = raw.indexOf("</think>", start);
  if (end < 0) return { thinking: raw.slice(start + 7), answer: raw.slice(0, start) };
  return { thinking: raw.slice(start + 7, end), answer: raw.slice(0, start) + raw.slice(end + 8) };
}

function workedFor(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  return value < 10 ? `${value.toFixed(1)}s` : `${Math.round(value)}s`;
}

function attachCopyButtons(container) {
  container.querySelectorAll(".copy-code").forEach((button) => {
    button.onclick = async () => {
      await navigator.clipboard.writeText(button.nextElementSibling.textContent);
      button.textContent = "Copied";
      setTimeout(() => button.textContent = "Copy", 1000);
    };
  });
}

function messageElement(role, content = "", metrics = null, status = "complete", sourcePrompt = "") {
  ui.welcome?.remove();
  const wrapper = document.createElement("article");
  wrapper.className = `message ${role}`;
  if (role === "user") {
    wrapper.innerHTML = `<div class="bubble"></div>`;
    wrapper.firstElementChild.textContent = content;
  } else {
    wrapper.innerHTML = `<div class="assistant-head"><span class="assistant-icon">L</span><span>Local model</span></div><div class="thinking-slot"></div><div class="content"></div><div class="metrics"></div><div class="message-actions"><button class="copy-response">Copy</button><button class="regenerate">${status === "error" || status === "interrupted" ? "Retry" : "Regenerate"}</button><button class="continue-response">Continue</button></div>`;
    wrapper.querySelector(".copy-response").onclick = async () => { await navigator.clipboard.writeText(wrapper.dataset.raw || ""); showToast("Response copied"); };
    wrapper.querySelector(".regenerate").onclick = () => send(sourcePrompt || lastUserMessage);
    wrapper.querySelector(".continue-response").onclick = () => send("Continue.");
    updateAssistant(wrapper, content, metrics, status);
  }
  ui.messages.appendChild(wrapper);
  scrollDown();
  return wrapper;
}

function updateAssistant(element, raw, metrics = null, status = "complete", liveSeconds = 0) {
  element.dataset.raw = raw;
  const parts = splitThinking(raw);
  const thinkingSlot = element.querySelector(".thinking-slot");
  const duration = metrics?.total_seconds ?? liveSeconds;
  const summary = status === "streaming" ? `Working for ${workedFor(duration)}` : `Worked for ${workedFor(duration)}`;
  const reasoning = parts.thinking.trim() || (status === "streaming"
    ? "Waiting for the model's reasoning…"
    : "This model did not return a separate thinking trace.");
  thinkingSlot.innerHTML = `<details class="thinking-box"${status === "streaming" && parts.thinking ? " open" : ""}><summary>${escapeHtml(summary)}</summary><div class="thinking-content">${markdown(reasoning)}</div></details>`;
  const content = element.querySelector(".content");
  if (status === "streaming") content.textContent = parts.answer;
  else {
    content.innerHTML = markdown(parts.answer || (status === "interrupted" ? "[Interrupted]" : ""));
    attachCopyButtons(content);
  }
  const metricsBox = element.querySelector(".metrics");
  if (metrics) {
    const values = [
      `${metrics.first_token_seconds}s first token`, `${metrics.tokens_per_second} tok/s`,
      `${metrics.prompt_tokens} prompt`, `${metrics.completion_tokens} output`,
      `temp ${Number(metrics.temperature).toFixed(2)}`, metrics.thinking ? "thinking on" : "thinking off"
    ];
    metricsBox.innerHTML = values.map((value) => `<span>${escapeHtml(value)}</span>`).join("");
  }
  element.querySelector(".message-actions")?.classList.toggle("hidden", status === "streaming");
}

function scrollDown() {
  const nearBottom = ui.messages.scrollHeight - ui.messages.scrollTop - ui.messages.clientHeight < 180;
  if (nearBottom) requestAnimationFrame(() => ui.messages.scrollTop = ui.messages.scrollHeight);
}

function updateContext(used = Math.ceil(conversationChars / 4), limit = Number(ui.contextSize.value) || 4096, exact = false) {
  const percent = Math.min(100, Math.round((used / limit) * 100));
  ui.contextBar.style.width = `${percent}%`;
  ui.contextBar.className = percent >= 90 ? "danger" : percent >= 75 ? "warning" : "";
  ui.contextLabel.textContent = `${percent >= 90 ? "Near limit · " : ""}${exact ? "Context" : "Approx. context"} ${used.toLocaleString()}/${limit.toLocaleString()} · ${percent}%`;
}

function contextNotice(message) {
  const notice = document.createElement("div");
  notice.className = "context-notice";
  notice.textContent = message;
  ui.messages.appendChild(notice);
  scrollDown();
}

function setStatus(status, detail = "") {
  const labels = { not_loaded: "Not loaded", loading: "Loading model", ready: "Ready", generating: "Generating", stopped: "Stopped", error: "Error" };
  ui.statusText.textContent = detail || labels[status] || status;
  ui.statusDot.className = `status-dot ${status === "ready" ? "ready" : status === "loading" || status === "generating" ? "busy" : status === "error" ? "error" : ""}`;
  ui.composerStatus.textContent = detail || labels[status] || status;
  ui.sendButton.disabled = status !== "ready" || generating;
}

function openSettings(open = true) {
  ui.settingsPanel.classList.toggle("open", open);
  ui.overlay.classList.toggle("open", open);
  ui.settingsPanel.setAttribute("aria-hidden", String(!open));
}

function generationValues() {
  return {
    system_prompt: ui.systemPrompt.value,
    thinking: ui.thinking.checked,
    temperature: Number(ui.temperature.value),
    top_p: Number(ui.topP.value), top_k: Number(ui.topK.value), min_p: Number(ui.minP.value),
    repeat_penalty: Number(ui.repeatPenalty.value), seed: Number(ui.seed.value)
  };
}

function applySettings(settings) {
  ui.systemPrompt.value = settings.system_prompt || "";
  ui.thinking.checked = Boolean(settings.thinking);
  ui.temperature.value = settings.temperature ?? 0;
  ui.temperatureNumber.value = settings.temperature ?? 0;
  ui.topP.value = settings.top_p ?? .95; ui.topK.value = settings.top_k ?? 40;
  ui.minP.value = settings.min_p ?? .05; ui.repeatPenalty.value = settings.repeat_penalty ?? 1;
  ui.seed.value = settings.seed ?? -1;
  updateLabels();
}

function updateLabels() {
  ui.temperatureLabel.textContent = `Temperature ${Number(ui.temperature.value).toFixed(2)}`;
  const prompt = ui.systemPrompt.value.trim();
  $("systemSummary").textContent = prompt ? `System: ${prompt}` : "No system prompt";
  $("systemSummary").classList.toggle("active", Boolean(prompt));
}

async function loadInitial() {
  try {
    const [models, settings, status, chats] = await Promise.all([
      api("/api/models"), api("/api/settings"), api("/api/status"), api("/api/chats")
    ]);
    const modelOptions = models.length
      ? models.map((model) => `<option value="${escapeHtml(model.path)}">${escapeHtml(model.name)} · ${(model.size / 1073741824).toFixed(2)} GB</option>`).join("")
      : '<option value="">No models detected</option>';
    ui.modelSelect.innerHTML = modelOptions;
    ui.quickModelSelect.innerHTML = modelOptions;
    if (settings.model_path && models.some((model) => model.path === settings.model_path)) ui.modelSelect.value = settings.model_path;
    else if (settings.model_path) ui.customModel.value = settings.model_path;
    ui.quickModelSelect.value = ui.modelSelect.value;
    ui.contextSize.value = settings.context_size; ui.gpuLayers.value = settings.gpu_layers; ui.threads.value = settings.threads;
    applySettings(settings);
    updateModelStatus(status, settings);
    allChats = chats;
    renderChatList();
    if (chats.length) await openChat(chats[0].id);
    if (
      status.status === "not_loaded"
      && settings.model_path
      && models.some((model) => model.path === settings.model_path)
    ) await loadModel();
  } catch (error) { setStatus("error", error.message); }
}

function updateModelStatus(status, settings = {}) {
  setStatus(status.status, status.error || "");
  if (status.model_path) {
    ui.activeModel.textContent = status.model_path.split(/[\\/]/).pop();
    const size = ui.quickModelSelect.selectedOptions[0]?.textContent.split(" · ")[1] || "custom path";
    ui.modelMeta.textContent = `${size} · ${settings.context_size || ui.contextSize.value} context · ${ui.gpuLayers.value} GPU layers`;
    ui.quickLoadModel.textContent = runtimeModelMatchesSelection() ? "Loaded" : "Load";
  }
}

async function refreshChats() {
  allChats = await api("/api/chats");
  renderChatList();
}

function renderChatList() {
  ui.chatList.innerHTML = "";
  const query = ui.chatSearch.value.trim().toLowerCase();
  const chats = allChats.filter((chat) => chat.title.toLowerCase().includes(query));
  let currentGroup = "";
  chats.forEach((chat) => {
    const date = new Date(chat.updated_at);
    const today = new Date();
    const group = date.toDateString() === today.toDateString() ? "Today" : "Earlier";
    if (group !== currentGroup) {
      const heading = document.createElement("div"); heading.className = "chat-group"; heading.textContent = group;
      ui.chatList.appendChild(heading); currentGroup = group;
    }
    const row = document.createElement("div"); row.className = "chat-row";
    row.innerHTML = `<button class="chat-item ${chat.id === activeChat ? "active" : ""}">${escapeHtml(chat.title)}</button><details class="chat-menu"><summary aria-label="Chat actions">•••</summary><div><button class="rename-chat">Rename</button><button class="delete-chat danger">Delete</button></div></details>`;
    row.querySelector(".chat-item").onclick = () => openChat(chat.id);
    row.querySelector(".rename-chat").onclick = async () => {
      const title = prompt("Rename chat", chat.title);
      if (title?.trim()) { await api(`/api/chats/${chat.id}`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) }); await refreshChats(); }
    };
    row.querySelector(".delete-chat").onclick = async (event) => {
      event.stopPropagation();
      if (!confirm(`Delete “${chat.title}”?`)) return;
      await api(`/api/chats/${chat.id}`, { method: "DELETE" });
      if (activeChat === chat.id) { activeChat = null; ui.messages.innerHTML = ""; await newChat(); }
      else await refreshChats();
    };
    ui.chatList.appendChild(row);
  });
}

async function newChat() {
  if (generating) return;
  const chat = await api("/api/chats", { method: "POST", body: JSON.stringify({
    system_prompt: ui.systemPrompt.value, thinking: ui.thinking.checked, temperature: Number(ui.temperature.value)
  }) });
  activeChat = chat.id; ui.messages.innerHTML = ""; lastUserMessage = ""; conversationChars = ui.systemPrompt.value.length;
  updateContext();
  await refreshChats(); ui.prompt.focus();
}

async function openChat(id) {
  if (generating) return;
  const chat = await api(`/api/chats/${id}`);
  activeChat = id; ui.messages.innerHTML = ""; lastUserMessage = "";
  applySettings({ ...defaults, ...chat.settings });
  conversationChars = ui.systemPrompt.value.length;
  let sourcePrompt = "";
  chat.messages.forEach((message) => {
    if (message.type === "user") { sourcePrompt = message.content; lastUserMessage = message.content; }
    messageElement(message.type, message.content, message.metrics, message.status, sourcePrompt);
    conversationChars += message.content.length;
  });
  const lastMetrics = [...chat.messages].reverse().find((message) => message.metrics)?.metrics;
  if (lastMetrics) updateContext(lastMetrics.context_used, lastMetrics.context_size, true);
  else updateContext();
  await refreshChats();
  ui.sidebar.classList.remove("open"); ui.overlay.classList.remove("open");
}

async function loadModel() {
  const modelPath = ui.customModel.value.trim() || ui.modelSelect.value;
  if (!modelPath) return setStatus("error", "Select a GGUF model");
  let seconds = 0;
  setStatus("loading", "Loading model · 0s"); ui.loadModel.disabled = true; ui.quickModelSelect.disabled = true; ui.quickLoadModel.disabled = true;
  ui.quickLoadModel.textContent = "Loading…";
  const loadingTimer = setInterval(() => setStatus("loading", `Loading model · ${++seconds}s`), 1000);
  try {
    const loadValues = { model_path: modelPath, context_size: Number(ui.contextSize.value), gpu_layers: Number(ui.gpuLayers.value), threads: Number(ui.threads.value) };
    await api("/api/model/load", { method: "POST", body: JSON.stringify(loadValues) });
    await api("/api/settings", { method: "PATCH", body: JSON.stringify({ ...loadValues, ...generationValues() }) });
    if ([...ui.quickModelSelect.options].some((option) => option.value === modelPath)) ui.quickModelSelect.value = modelPath;
    updateModelStatus(await api("/api/status"), loadValues); openSettings(false);
    const label = [...ui.quickModelSelect.options].find((option) => option.value === modelPath)?.textContent || modelPath.split(/[\\/]/).pop();
    showToast(`${label} is ready`);
  } catch (error) { setStatus("error", error.message); }
  finally {
    clearInterval(loadingTimer);
    ui.loadModel.disabled = false; ui.quickModelSelect.disabled = false; ui.quickLoadModel.disabled = false;
    ui.quickLoadModel.textContent = runtimeModelMatchesSelection() ? "Loaded" : "Load";
  }
}

function runtimeModelMatchesSelection() {
  return ui.activeModel.textContent === ui.quickModelSelect.selectedOptions[0]?.textContent.split(" · ")[0];
}

async function send(message = ui.prompt.value.trim()) {
  if (!message || generating) return;
  const status = await api("/api/status");
  if (!["ready", "stopped"].includes(status.status)) return openSettings(true);
  if (!activeChat) await newChat();
  lastUserMessage = message; ui.prompt.value = ""; resizePrompt();
  messageElement("user", message);
  conversationChars += message.length; updateContext();
  const assistant = messageElement("assistant", "", null, "streaming", message);
  generating = true; ui.quickModelSelect.disabled = true; ui.quickLoadModel.disabled = true; ui.sendButton.classList.add("hidden"); ui.stopButton.classList.remove("hidden");
  let elapsedSeconds = 0;
  setStatus("generating", "Preparing prompt…");
  generationTimer = setInterval(() => {
    elapsedSeconds += 1;
    setStatus("generating", `Generating · ${elapsedSeconds}s`);
    updateAssistant(assistant, raw, null, "streaming", elapsedSeconds);
  }, 1000);
  let raw = "";
  try {
    const response = await fetch(`/api/chats/${activeChat}/generate`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, ...generationValues() })
    });
    if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = "";
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = pending.split("\n"); pending = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const item = JSON.parse(line);
        if (item.type === "start") setStatus("generating", `Generating · ${elapsedSeconds}s`);
        else if (item.type === "context_trimmed") contextNotice(item.message);
        else if (item.type === "token") { raw += item.text; updateAssistant(assistant, raw, null, "streaming", elapsedSeconds); scrollDown(); }
        else if (item.type === "done" || item.type === "stopped") {
          updateAssistant(assistant, raw, item.metrics, item.type === "stopped" ? "interrupted" : "complete");
          updateContext(item.metrics.context_used, item.metrics.context_size, true);
        }
        else if (item.type === "error") throw new Error(item.message);
      }
      if (done) break;
    }
  } catch (error) {
    updateAssistant(assistant, `${raw}\n\n[Error: ${error.message}]`, null, "complete"); setStatus("error", error.message);
  } finally {
    clearInterval(generationTimer); generationTimer = null; conversationChars += raw.length;
    generating = false; ui.quickModelSelect.disabled = false; ui.quickLoadModel.disabled = false; ui.sendButton.classList.remove("hidden"); ui.stopButton.classList.add("hidden");
    const current = await api("/api/status"); if (current.status !== "error") setStatus("ready");
    await refreshChats(); ui.prompt.focus();
  }
}

async function stop() { if (generating) { ui.stopButton.disabled = true; clearInterval(generationTimer); setStatus("generating", "Stopping…"); await api("/api/generation/stop", { method: "POST" }); ui.stopButton.disabled = false; } }
function resizePrompt() { ui.prompt.style.height = "auto"; ui.prompt.style.height = `${Math.min(ui.prompt.scrollHeight, 180)}px`; }

ui.settingsButton.onclick = () => openSettings(true); ui.closeSettings.onclick = () => openSettings(false); ui.overlay.onclick = () => { openSettings(false); ui.sidebar.classList.remove("open"); };
ui.systemSummary.onclick = () => { openSettings(true); setTimeout(() => ui.systemPrompt.focus(), 220); };
ui.menuButton.onclick = () => { ui.sidebar.classList.toggle("open"); ui.overlay.classList.toggle("open", ui.sidebar.classList.contains("open")); };
ui.newChat.onclick = newChat; ui.loadModel.onclick = loadModel; ui.sendButton.onclick = () => send(); ui.stopButton.onclick = stop;
ui.prompt.oninput = resizePrompt; ui.prompt.onkeydown = (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } };
ui.temperature.oninput = () => { ui.temperatureNumber.value = ui.temperature.value; updateLabels(); };
ui.temperatureNumber.oninput = () => { ui.temperature.value = Math.max(0, Math.min(2, Number(ui.temperatureNumber.value))); updateLabels(); };
ui.systemPrompt.oninput = () => { ui.systemPreset.value = "custom"; updateLabels(); };
ui.systemPreset.onchange = () => { if (ui.systemPreset.value !== "custom") ui.systemPrompt.value = ui.systemPreset.value; updateLabels(); };
ui.resetSettings.onclick = () => applySettings(defaults);
ui.modelSelect.onchange = () => { ui.customModel.value = ""; ui.quickModelSelect.value = ui.modelSelect.value; };
ui.quickLoadModel.onclick = loadModel;
ui.chatSearch.oninput = renderChatList;
ui.quickModelSelect.onchange = async () => {
  if (generating || !ui.quickModelSelect.value) return;
  ui.modelSelect.value = ui.quickModelSelect.value;
  ui.customModel.value = "";
  await loadModel();
};
document.addEventListener("keydown", (event) => { if (event.key === "Escape") openSettings(false); if (event.ctrlKey && event.key.toLowerCase() === "c" && generating) stop(); });

loadInitial();
