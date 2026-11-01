import { initThemes, text, refreshThemeContent } from "./theme.js";

const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries(
  "sidebar sidebarClose chatList chatSearch newChat statusDot statusText activeModel modelMeta messages welcome prompt sendButton stopButton composerStatus contextBar contextLabel settingsButton settingsPanel closeSettings overlay toast menuButton modelSelect quickModelSelect quickLoadModel customModel contextSize gpuLayers threads loadModel systemPrompt systemSummary systemPreset thinking quickThinking thinkingState temperature temperatureNumber topP topK minP repeatPenalty seed resetSettings optionsButton optionsPopover composerSettingsSlot modelPickerButton modelPopover modelPickerLabel modelReady selectedModelPath modelSettingsButton contextButton contextPopover contextSummary contextWarning"
    .split(" ").map((id) => [id, $(id)])
);

const defaults = { temperature: 0, top_p: .95, top_k: 40, min_p: .05, repeat_penalty: 1, seed: -1 };
let activeChat = null;
let generating = false;
let lastUserMessage = "";
let allChats = [];
let conversationChars = 0;
let generationTimer = null;
let loadedModelPath = "";
let loadingModel = false;
let lastStatus = { status: "not_loaded", detail: "" };
let settingsReturnFocus = null;
let sidebarExpanded = true;
const mobile = matchMedia("(max-width: 720px)");
let lastContext = null;
const composerPopovers = [[ui.optionsPopover, ui.optionsButton], [ui.modelPopover, ui.modelPickerButton], [ui.contextPopover, ui.contextButton]];

function closeComposerPopovers() {
  for (const [popover] of composerPopovers) if (popover.matches(":popover-open")) popover.hidePopover();
}

function positionComposerPopovers() {
  const viewport = window.visualViewport;
  const width = viewport?.width || window.innerWidth;
  const height = viewport?.height || window.innerHeight;
  const offsetTop = viewport?.offsetTop || 0;
  const offsetLeft = viewport?.offsetLeft || 0;
  for (const [popover, button] of composerPopovers) {
    if (!popover.matches(":popover-open")) continue;
    popover.style.setProperty("--popover-max-height", `${Math.max(80, height - 32)}px`);
    const anchor = button.getBoundingClientRect();
    const bounds = popover.getBoundingClientRect();
    const left = Math.max(offsetLeft + 16, Math.min(anchor.left, offsetLeft + width - bounds.width - 16));
    const preferredTop = anchor.top - bounds.height - 10;
    const top = preferredTop >= offsetTop + 16 ? preferredTop : Math.max(offsetTop + 16, Math.min(anchor.bottom + 10, offsetTop + height - bounds.height - 16));
    popover.style.left = `${left}px`; popover.style.top = `${top}px`;
  }
}

function initComposerPopovers() {
  // Reuse the live Settings sections; comments mark their original positions.
  const groups = ["conversationOptions", "generationOptions", "advancedOptions"].map(id => {
    const section = $(id);
    const home = document.createComment(id);
    section.before(home);
    return { section, home };
  });
  for (const [popover, button] of composerPopovers) {
    popover.addEventListener("beforetoggle", event => {
      if (popover !== ui.optionsPopover) return;
      if (event.newState === "open") {
        for (const { section } of groups) ui.composerSettingsSlot.append(section);
      } else {
        const restoreFocus = popover.contains(document.activeElement);
        for (const { section, home } of groups) home.after(section);
        if (restoreFocus) queueMicrotask(() => { if (ui.settingsPanel.hidden && !popover.matches(":popover-open")) button.focus({ preventScroll: true }); });
      }
    });
    popover.addEventListener("toggle", event => {
      const open = event.newState === "open";
      button.setAttribute("aria-expanded", String(open));
      if (open) positionComposerPopovers();
    });
  }
  const observer = new ResizeObserver(positionComposerPopovers);
  composerPopovers.forEach(([popover]) => observer.observe(popover));
  window.addEventListener("resize", positionComposerPopovers);
  window.visualViewport?.addEventListener("resize", positionComposerPopovers);
  window.visualViewport?.addEventListener("scroll", positionComposerPopovers);
}

function readableModelName(path) {
  return path.split(/[\\/]/).pop().replace(/\.gguf$/i, "")
    .replace(/[-_.](?:Q\d[\w.-]*|IQ\d[\w.-]*|BF16|FP16|F16)$/i, "")
    .replace(/-(\d+(?:\.\d+)?B)(?=-|$)/ig, " · $1");
}

function updateModelPicker() {
  const path = ui.customModel.value.trim() || ui.quickModelSelect.value;
  const matched = runtimeModelMatchesSelection();
  ui.modelPickerLabel.textContent = path ? readableModelName(path) : text("selectModel");
  ui.modelPickerButton.setAttribute("aria-label", text("modelPickerDescription", { model: ui.modelPickerLabel.textContent, state: text(loadingModel ? "loading" : matched ? "loaded" : "not_loaded") }));
  ui.modelPickerButton.title = path || text("selectModel");
  ui.selectedModelPath.textContent = path;
  ui.modelReady.hidden = !matched || loadingModel;
  ui.modelPickerButton.disabled = loadingModel || generating;
  ui.quickLoadModel.hidden = matched && !loadingModel;
  ui.quickLoadModel.textContent = text(loadingModel ? "loading" : "load");
}

function resetMessages() {
  ui.messages.replaceChildren(ui.welcome);
  document.body.dataset.chatState = "empty";
}

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

function markdown(source) {
  const blocks = [];
  let safe = escapeHtml(source).replace(/```([^\n]*)\n([\s\S]*?)```/g, (_, language, code) => {
    const index = blocks.length;
    blocks.push(`<pre><button class="copy-code" data-text="copy">${escapeHtml(text("copy"))}</button><code data-language="${language.trim()}">${code}</code></pre>`);
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
      button.dataset.text = "copied"; button.textContent = text("copied");
      setTimeout(() => { button.dataset.text = "copy"; button.textContent = text("copy"); }, 1000);
    };
  });
}

function messageElement(role, content = "", metrics = null, status = "complete", sourcePrompt = "", request = null, error = "") {
  document.body.dataset.chatState = "active";
  const wrapper = document.createElement("article");
  wrapper.className = `message ${role}`;
  if (role === "user") {
    wrapper.innerHTML = `<div class="bubble"></div>`;
    wrapper.firstElementChild.textContent = content;
  } else {
    wrapper.generationRequest = request;
    wrapper.innerHTML = `<div class="assistant-head"><img class="assistant-icon" data-brand-logo alt=""><span data-text="assistantName"></span></div><div class="thinking-slot"></div><div class="content"></div><div class="response-notice" hidden></div><div class="metrics"></div><div class="message-actions"><button class="copy-response"><span data-icon="copy"></span><span data-text="copy"></span></button><button class="regenerate"><span data-icon="regenerate"></span><span data-text="regenerate"></span></button><button class="continue-response"><span data-icon="continue"></span><span data-text="continue"></span></button></div>`;
    wrapper.querySelector(".copy-response").onclick = async () => { await navigator.clipboard.writeText(wrapper.dataset.raw || ""); showToast(text("copiedResponse")); };
    wrapper.querySelector(".regenerate").onclick = () => {
      const retry = ["error", "interrupted"].includes(wrapper.dataset.status);
      return send(sourcePrompt || lastUserMessage, retry ? wrapper.generationRequest : null);
    };
    wrapper.querySelector(".continue-response").onclick = () => send("Continue.");
    updateAssistant(wrapper, content, metrics, status, 0, error);
    refreshThemeContent(wrapper);
  }
  ui.messages.appendChild(wrapper);
  updateResponseActions();
  scrollDown();
  return wrapper;
}

function updateResponseActions() {
  const latest = [...ui.messages.querySelectorAll(".message")].at(-1);
  ui.messages.querySelectorAll(".assistant").forEach(element => {
    const retry = ["error", "interrupted"].includes(element.dataset.status);
    const available = element === latest && Boolean(element.generationRequest);
    const button = element.querySelector(".regenerate");
    button.disabled = generating || (retry && !available);
    button.title = retry ? text(available ? "retryHelp" : "retryLatestOnly") : "";
    element.querySelector(".continue-response").hidden = retry;
    element.querySelector(".continue-response").disabled = generating;
  });
}

function updateAssistant(element, raw, metrics = null, status = "complete", liveSeconds = 0, error = "") {
  const statusChanged = element.dataset.status !== status;
  element.dataset.raw = raw;
  element.dataset.status = status;
  const parts = splitThinking(raw);
  const thinkingSlot = element.querySelector(".thinking-slot");
  const duration = metrics?.total_seconds ?? liveSeconds;
  element.dataset.duration = workedFor(duration);
  const summary = text(status === "streaming" ? "working" : "worked", { duration: element.dataset.duration });
  const reasoningKey = status === "streaming" ? "waitingReasoning" : "noReasoning";
  const previous = thinkingSlot.querySelector("details");
  const expanded = previous ? previous.open : status === "streaming" && Boolean(parts.thinking);
  thinkingSlot.innerHTML = `<details class="thinking-box"${expanded ? " open" : ""}><summary>${escapeHtml(summary)}</summary><div class="thinking-content">${parts.thinking.trim() ? markdown(parts.thinking.trim()) : `<p data-text="${reasoningKey}">${escapeHtml(text(reasoningKey))}</p>`}</div></details>`;
  const content = element.querySelector(".content");
  if (status === "streaming") content.textContent = parts.answer;
  else {
    content.innerHTML = markdown(parts.answer);
    attachCopyButtons(content);
  }
  const notice = element.querySelector(".response-notice");
  const failed = status === "error" || status === "interrupted";
  notice.hidden = !failed;
  notice.setAttribute("role", status === "error" ? "alert" : "status");
  notice.innerHTML = failed ? `<strong data-text="${status === "error" ? "responseFailed" : "responseStopped"}">${escapeHtml(text(status === "error" ? "responseFailed" : "responseStopped"))}</strong><p data-text="responseKept">${escapeHtml(text("responseKept"))}</p>${error ? `<details><summary data-text="errorDetails">${escapeHtml(text("errorDetails"))}</summary><p>${escapeHtml(error)}</p></details>` : ""}` : "";
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
  const retryLabel = element.querySelector(".regenerate [data-text]");
  retryLabel.dataset.text = status === "error" || status === "interrupted" ? "retry" : "regenerate";
  retryLabel.textContent = text(retryLabel.dataset.text);
  if (statusChanged) updateResponseActions();
}

function scrollDown() {
  const nearBottom = ui.messages.scrollHeight - ui.messages.scrollTop - ui.messages.clientHeight < 180;
  if (nearBottom) requestAnimationFrame(() => ui.messages.scrollTop = ui.messages.scrollHeight);
}

function updateContext(used = Math.ceil(conversationChars / 4), limit = Number(ui.contextSize.value) || 4096, exact = false) {
  lastContext = { used, limit, exact };
  const percent = Math.min(100, Math.round((used / limit) * 100));
  ui.contextBar.style.width = `${percent}%`;
  ui.contextBar.className = percent >= 90 ? "danger" : percent >= 75 ? "warning" : "";
  ui.contextLabel.textContent = text(exact ? "contextExact" : "contextApproximate", { used: used.toLocaleString(), limit: limit.toLocaleString(), percent });
  ui.contextSummary.textContent = text(percent >= 90 ? "contextNearLimit" : "contextSummary", { percent });
  ui.contextButton.classList.toggle("warning", percent >= 75 && percent < 90);
  ui.contextButton.classList.toggle("danger", percent >= 90);
  ui.contextWarning.hidden = percent < 75;
}

function contextNotice(message) {
  const notice = document.createElement("div");
  notice.className = "context-notice";
  notice.textContent = message;
  ui.messages.appendChild(notice);
  scrollDown();
}

function setStatus(status, detail = "") {
  lastStatus = { status, detail };
  const label = typeof detail === "object" ? text(detail.key, detail.values) : detail || text(status);
  ui.statusText.textContent = label;
  ui.statusDot.className = `status-dot ${status === "ready" ? "ready" : status === "loading" || status === "generating" ? "busy" : status === "error" ? "error" : ""}`;
  ui.composerStatus.textContent = label;
  ui.composerStatus.dataset.status = status;
  ui.sendButton.disabled = status !== "ready" || generating;
  updateModelPicker();
}

function openSettings(open = true) {
  if (open && ui.settingsPanel.hidden) settingsReturnFocus = composerPopovers.find(([popover]) => popover.contains(document.activeElement))?.[1] || document.activeElement;
  if (open) closeComposerPopovers();
  if (open && mobile.matches) setSidebar(false);
  ui.settingsPanel.hidden = !open;
  ui.settingsPanel.classList.toggle("open", open);
  ui.overlay.classList.toggle("open", open || (mobile.matches && ui.sidebar.classList.contains("open")));
  ui.settingsPanel.setAttribute("aria-hidden", String(!open));
  document.querySelector(".main").inert = open;
  ui.sidebar.inert = open || (mobile.matches ? !ui.sidebar.classList.contains("open") : !sidebarExpanded);
  if (open) ui.closeSettings.focus();
  else if (settingsReturnFocus?.isConnected) { settingsReturnFocus.focus({ preventScroll: true }); settingsReturnFocus = null; }
}

function setSidebar(open) {
  if (open) closeComposerPopovers();
  const hadSidebarFocus = ui.sidebar.contains(document.activeElement);
  if (mobile.matches) {
    ui.sidebar.classList.toggle("open", open);
    ui.overlay.classList.toggle("open", open || !ui.settingsPanel.hidden);
    document.querySelector(".main").inert = open || !ui.settingsPanel.hidden;
  } else {
    sidebarExpanded = open;
    document.body.classList.toggle("sidebar-collapsed", !open);
    ui.sidebar.classList.remove("open");
    ui.overlay.classList.toggle("open", !ui.settingsPanel.hidden);
    document.querySelector(".main").inert = !ui.settingsPanel.hidden;
  }
  ui.sidebar.inert = !open || !ui.settingsPanel.hidden;
  ui.menuButton.setAttribute("aria-expanded", String(open));
  if (mobile.matches && open) ui.sidebarClose.focus();
  else if (hadSidebarFocus) ui.menuButton.focus();
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
  // Settings remains the single value read by generationValues() and newChat().
  ui.quickThinking.checked = ui.thinking.checked;
  ui.thinkingState.textContent = text(ui.thinking.checked ? "thinkingOn" : "thinkingOff");
  ui.quickThinking.title = text("thinkingHelp");
  const prompt = ui.systemPrompt.value.trim();
  ui.systemSummary.textContent = text("optionsCustom");
  ui.systemSummary.hidden = !prompt;
  ui.optionsButton.title = text("optionsHelp");
}

async function loadInitial() {
  try {
    const [models, settings, status, chats] = await Promise.all([
      api("/api/models"), api("/api/settings"), api("/api/status"), api("/api/chats")
    ]);
    const modelOptions = models.length
      ? models.map((model) => `<option value="${escapeHtml(model.path)}">${escapeHtml(model.name)} · ${(model.size / 1073741824).toFixed(2)} GB</option>`).join("")
      : `<option value="">${escapeHtml(text("noModels"))}</option>`;
    ui.modelSelect.innerHTML = modelOptions;
    ui.quickModelSelect.innerHTML = modelOptions;
    if (settings.model_path && models.some((model) => model.path === settings.model_path)) ui.modelSelect.value = settings.model_path;
    else if (settings.model_path) ui.customModel.value = settings.model_path;
    ui.quickModelSelect.value = ui.modelSelect.value;
    ui.contextSize.value = settings.context_size; ui.gpuLayers.value = settings.gpu_layers; ui.threads.value = settings.threads;
    applySettings(settings);
    updateContext();
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
  loadedModelPath = status.model_path || "";
  setStatus(status.status, status.error || "");
  if (status.model_path) {
    ui.activeModel.textContent = status.model_path.split(/[\\/]/).pop();
    const size = ui.quickModelSelect.selectedOptions[0]?.textContent.split(" · ")[1] || "custom path";
    ui.modelMeta.textContent = `${size} · ${settings.context_size || ui.contextSize.value} context · ${ui.gpuLayers.value} GPU layers`;
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
    const group = date.toDateString() === today.toDateString() ? "today" : "earlier";
    if (group !== currentGroup) {
      const heading = document.createElement("div"); heading.className = "chat-group"; heading.dataset.text = group; heading.textContent = text(group);
      ui.chatList.appendChild(heading); currentGroup = group;
    }
    const row = document.createElement("div"); row.className = "chat-row";
    row.innerHTML = `<button class="chat-item ${chat.id === activeChat ? "active" : ""}">${escapeHtml(chat.title)}</button><details class="chat-menu"><summary data-title="chatActions" aria-label="${escapeHtml(text("chatActions"))}">•••</summary><div><button class="rename-chat" data-text="rename">${escapeHtml(text("rename"))}</button><button class="delete-chat danger" data-text="delete">${escapeHtml(text("delete"))}</button></div></details>`;
    row.querySelector(".chat-item").onclick = () => openChat(chat.id);
    row.querySelector(".rename-chat").onclick = async () => {
      const title = prompt(text("renameChat"), chat.title);
      if (title?.trim()) { await api(`/api/chats/${chat.id}`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) }); await refreshChats(); }
    };
    row.querySelector(".delete-chat").onclick = async (event) => {
      event.stopPropagation();
      if (!confirm(text("deleteConfirm", { title: chat.title }))) return;
      await api(`/api/chats/${chat.id}`, { method: "DELETE" });
      if (activeChat === chat.id) { activeChat = null; resetMessages(); await newChat(); }
      else await refreshChats();
    };
    ui.chatList.appendChild(row);
  });
}

async function newChat() {
  if (generating) return;
  closeComposerPopovers();
  const chat = await api("/api/chats", { method: "POST", body: JSON.stringify({
    system_prompt: ui.systemPrompt.value, thinking: ui.thinking.checked, temperature: Number(ui.temperature.value)
  }) });
  activeChat = chat.id; resetMessages(); lastUserMessage = ""; conversationChars = ui.systemPrompt.value.length;
  updateContext();
  await refreshChats(); if (mobile.matches) setSidebar(false); ui.prompt.focus();
}

async function openChat(id) {
  if (generating) return;
  closeComposerPopovers();
  const chat = await api(`/api/chats/${id}`);
  activeChat = id; resetMessages(); lastUserMessage = "";
  applySettings({ ...defaults, ...chat.settings });
  conversationChars = ui.systemPrompt.value.length;
  let sourcePrompt = "";
  chat.messages.forEach((message) => {
    if (message.type === "user") { sourcePrompt = message.content; lastUserMessage = message.content; }
    messageElement(message.type, message.content, message.metrics, message.status, sourcePrompt, message.request, message.error);
    conversationChars += message.content.length;
  });
  const lastMetrics = [...chat.messages].reverse().find((message) => message.metrics)?.metrics;
  if (lastMetrics) updateContext(lastMetrics.context_used, lastMetrics.context_size, true);
  else updateContext();
  await refreshChats();
  if (mobile.matches) setSidebar(false);
}

async function loadModel() {
  const modelPath = ui.customModel.value.trim() || ui.modelSelect.value;
  if (!modelPath) return setStatus("error", text("selectGGUF"));
  loadingModel = true;
  let seconds = 0;
  setStatus("loading", { key: "loadingStatus", values: { seconds } }); ui.loadModel.disabled = true; ui.quickModelSelect.disabled = true; ui.quickLoadModel.disabled = true;
  ui.quickLoadModel.textContent = text("loading");
  const loadingTimer = setInterval(() => setStatus("loading", { key: "loadingStatus", values: { seconds: ++seconds } }), 1000);
  try {
    const loadValues = { model_path: modelPath, context_size: Number(ui.contextSize.value), gpu_layers: Number(ui.gpuLayers.value), threads: Number(ui.threads.value) };
    await api("/api/model/load", { method: "POST", body: JSON.stringify(loadValues) });
    await api("/api/settings", { method: "PATCH", body: JSON.stringify({ ...loadValues, ...generationValues() }) });
    if ([...ui.quickModelSelect.options].some((option) => option.value === modelPath)) ui.quickModelSelect.value = modelPath;
    updateModelStatus(await api("/api/status"), loadValues); openSettings(false);
    if (ui.customModel.value.trim()) ui.customModel.value = loadedModelPath;
    const label = [...ui.quickModelSelect.options].find((option) => option.value === modelPath)?.textContent || modelPath.split(/[\\/]/).pop();
    showToast(text("modelReady", { model: label }));
  } catch (error) { setStatus("error", error.message); }
  finally {
    clearInterval(loadingTimer);
    loadingModel = false;
    ui.loadModel.disabled = false; ui.quickModelSelect.disabled = false; ui.quickLoadModel.disabled = false;
    updateModelPicker();
  }
}

function runtimeModelMatchesSelection() {
  return Boolean(loadedModelPath) && loadedModelPath === (ui.customModel.value.trim() || ui.quickModelSelect.value);
}

async function send(message = ui.prompt.value.trim(), retryRequest = null) {
  if (!message || generating) return;
  const request = retryRequest || { message, ...generationValues(), request_id: crypto.randomUUID() };
  // Reserve the UI before the first await so rapid clicks cannot submit twice.
  generating = true;
  closeComposerPopovers();
  ui.quickModelSelect.disabled = true; ui.quickLoadModel.disabled = true; ui.loadModel.disabled = true;
  ui.sendButton.classList.add("hidden"); ui.stopButton.classList.remove("hidden"); ui.stopButton.disabled = true;
  updateResponseActions();
  let elapsedSeconds = 0;
  let raw = "", assistant = null, failed = false;
  setStatus("generating", { key: "preparing" });
  try {
    const status = await api("/api/status");
    if (!["ready", "stopped"].includes(status.status)) {
      if (status.status === "generating") showToast(text("generationBusy"));
      else openSettings(true);
      return;
    }
    if (!activeChat) {
      const chat = await api("/api/chats", { method: "POST", body: JSON.stringify(request) });
      activeChat = chat.id; resetMessages(); conversationChars = request.system_prompt.length;
      if (mobile.matches) setSidebar(false);
    }
    lastUserMessage = request.message;
    if (!retryRequest) {
      // The prompt remains editable until we have a chat ready to receive it.
      if (ui.prompt.value.trim() === request.message) { ui.prompt.value = ""; resizePrompt(); }
      messageElement("user", request.message);
      conversationChars += request.message.length; updateContext();
    }
    assistant = messageElement("assistant", "", null, "streaming", request.message, request);
    generationTimer = setInterval(() => {
      elapsedSeconds += 1;
      setStatus("generating", { key: "generatingStatus", values: { seconds: elapsedSeconds } });
      updateAssistant(assistant, raw, null, "streaming", elapsedSeconds);
    }, 1000);
    const response = await fetch(`/api/chats/${activeChat}/generate`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request)
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(typeof detail === "string" ? detail : text("responseRequestFailed"));
    }
    if (!response.body) throw new Error(text("streamInterrupted"));
    ui.stopButton.disabled = false;
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = "";
    let terminal = false;
    const consume = line => {
      if (!line.trim() || terminal) return;
      const item = JSON.parse(line);
      if (item.type === "start") setStatus("generating", { key: "generatingStatus", values: { seconds: elapsedSeconds } });
      else if (item.type === "context_trimmed") contextNotice(item.message);
      else if (item.type === "token") { raw += item.text; updateAssistant(assistant, raw, null, "streaming", elapsedSeconds); scrollDown(); }
      else if (item.type === "done" || item.type === "stopped") {
        terminal = true;
        clearInterval(generationTimer);
        updateAssistant(assistant, raw, item.metrics, item.type === "stopped" ? "interrupted" : "complete", elapsedSeconds);
        if (item.metrics) updateContext(item.metrics.context_used, item.metrics.context_size, true);
      }
      else if (item.type === "error") throw new Error(item.message);
    };
    try {
      while (!terminal) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = pending.split("\n"); pending = lines.pop();
        for (const line of lines) consume(line);
        if (done) { consume(pending); break; }
      }
      if (!terminal) throw new Error(text("streamInterrupted"));
    } finally {
      reader.releaseLock();
    }
  } catch (error) {
    failed = true;
    if (assistant) updateAssistant(assistant, raw, null, "error", elapsedSeconds, error.message);
    else showToast(text("requestFailed"));
  } finally {
    clearInterval(generationTimer); generationTimer = null; conversationChars += raw.length;
    generating = false;
    ui.quickModelSelect.disabled = false; ui.quickLoadModel.disabled = false; ui.loadModel.disabled = false;
    ui.sendButton.classList.remove("hidden"); ui.stopButton.classList.add("hidden"); ui.stopButton.disabled = false;
    updateResponseActions();
    // Cleanup failures must not relabel a completed answer or hide a failed one.
    const [statusResult, chatsResult] = await Promise.allSettled([api("/api/status"), refreshChats()]);
    if (statusResult.status === "fulfilled") {
      const current = statusResult.value;
      updateModelStatus(current);
      if (failed && !assistant && current.status === "ready") setStatus("ready", { key: "requestFailed" });
    } else {
      setStatus("error", { key: "connectionLost" });
      ui.sendButton.disabled = false;
    }
    if (chatsResult.status === "rejected" && statusResult.status === "fulfilled") showToast(text("chatListUnavailable"));
    if ([ui.sendButton, ui.stopButton, document.body].includes(document.activeElement)) ui.prompt.focus();
  }
}

async function stop() {
  if (!generating || ui.stopButton.disabled) return;
  ui.stopButton.disabled = true;
  setStatus("generating", { key: "stopping" });
  try {
    await api("/api/generation/stop", { method: "POST" });
    clearInterval(generationTimer);
  } catch (_) {
    showToast(text("stopFailed"));
  } finally {
    ui.stopButton.disabled = !generating;
  }
}
function resizePrompt() { ui.prompt.style.height = "auto"; ui.prompt.style.height = `${Math.min(ui.prompt.scrollHeight, 180)}px`; }

ui.settingsButton.onclick = () => openSettings(true); ui.closeSettings.onclick = () => openSettings(false); ui.overlay.onclick = () => { openSettings(false); if (mobile.matches) setSidebar(false); };
ui.modelSettingsButton.onclick = () => { openSettings(true); ui.modelSelect.focus(); };
ui.menuButton.onclick = () => setSidebar(mobile.matches ? !ui.sidebar.classList.contains("open") : !sidebarExpanded);
ui.sidebarClose.onclick = () => setSidebar(false);
mobile.addEventListener("change", () => setSidebar(mobile.matches ? false : sidebarExpanded));
ui.newChat.onclick = newChat; ui.loadModel.onclick = loadModel; ui.sendButton.onclick = () => send(); ui.stopButton.onclick = stop;
ui.prompt.oninput = resizePrompt; ui.prompt.onkeydown = (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } };
ui.temperature.oninput = () => { ui.temperatureNumber.value = ui.temperature.value; updateLabels(); };
ui.temperatureNumber.oninput = () => { ui.temperature.value = Math.max(0, Math.min(2, Number(ui.temperatureNumber.value))); updateLabels(); };
ui.thinking.onchange = updateLabels;
ui.quickThinking.onchange = () => { ui.thinking.checked = ui.quickThinking.checked; updateLabels(); };
ui.systemPrompt.oninput = () => { ui.systemPreset.value = "custom"; updateLabels(); };
ui.systemPreset.onchange = () => { if (ui.systemPreset.value !== "custom") ui.systemPrompt.value = ui.systemPreset.value; updateLabels(); };
ui.resetSettings.onclick = () => applySettings(defaults);
ui.modelSelect.onchange = () => { ui.customModel.value = ""; ui.quickModelSelect.value = ui.modelSelect.value; updateModelPicker(); };
ui.customModel.oninput = updateModelPicker;
ui.quickLoadModel.onclick = loadModel;
ui.chatSearch.oninput = renderChatList;
ui.quickModelSelect.onchange = async () => {
  if (generating || !ui.quickModelSelect.value) return;
  ui.modelSelect.value = ui.quickModelSelect.value;
  ui.customModel.value = "";
  closeComposerPopovers();
  await loadModel();
};
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { if (!ui.settingsPanel.hidden) openSettings(false); else if (mobile.matches) setSidebar(false); }
  if (event.ctrlKey && event.key.toLowerCase() === "c" && generating) stop();
  const panel = !ui.settingsPanel.hidden ? ui.settingsPanel : mobile.matches && ui.sidebar.classList.contains("open") ? ui.sidebar : null;
  if (event.key === "Tab" && panel) {
    const controls = [...panel.querySelectorAll('button, input, select, textarea, summary')].filter(node => !node.disabled && node.getClientRects().length);
    const target = event.shiftKey ? controls.at(-1) : controls[0];
    if (document.activeElement === (event.shiftKey ? controls[0] : controls.at(-1))) { event.preventDefault(); target?.focus(); }
  }
});

document.addEventListener("themechange", () => {
  closeComposerPopovers();
  updateLabels(); setStatus(lastStatus.status, lastStatus.detail);
  if (lastContext) updateContext(lastContext.used, lastContext.limit, lastContext.exact);
  if (!loadedModelPath) { ui.activeModel.textContent = text("noModel"); ui.modelMeta.textContent = text("selectModel"); }
  ui.messages.querySelectorAll(".assistant").forEach(node => {
    node.querySelector(".thinking-box summary").textContent = text(node.dataset.status === "streaming" ? "working" : "worked", { duration: node.dataset.duration });
  });
  updateResponseActions();
  resizePrompt();
});

initComposerPopovers();
await initThemes();
setSidebar(!mobile.matches);
await loadInitial();
