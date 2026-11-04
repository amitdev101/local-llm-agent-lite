import { initThemes, text, refreshThemeContent } from "./theme.js";
import { initEnhancements } from "./enhancements.js";

const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries(
  "sidebar sidebarClose chatList chatSearch newChat statusDot statusText activeModel modelMeta messages welcome prompt sendButton stopButton composerStatus contextBar contextLabel settingsButton settingsPanel closeSettings overlay toast menuButton modelSelect quickModelSelect quickLoadModel customModel chooseDetectedModel contextSize gpuLayers threads loadModel systemPrompt systemSummary systemPreset thinking quickThinking thinkingState thinkingControl thinkingHelp thinkingSupportHelp temperature temperatureNumber topP topK minP repeatPenalty seed resetSettings saveResponseDefaults responseSettingsStatus responseSettingsError responseScope modelReloadNotice modelMenuReloadNotice optionsButton optionsPopover composerSettingsSlot modelPickerButton modelPopover modelPickerLabel modelReady selectedModelPath selectedModelState loadedModelInfo modelThinkingSupport menuLoadModel modelMenuError modelLoadError modelSettingsButton contextButton contextPopover contextSummary contextWarning"
    .split(" ").map((id) => [id, $(id)])
);

const defaults = { temperature: 0, top_p: .95, top_k: 40, min_p: .05, repeat_penalty: 1, seed: -1 };
let activeChat = null;
let generating = false;
let generationSession = null;
let enhancements = null;
let lastUserMessage = "";
let allChats = [];
let conversationChars = 0;
let generationTimer = null;
let loadedModelPath = "";
let loadingModel = false;
let runtimeStatus = "not_loaded";
let runtimeRefreshTimer = null;
let availableModels = [];
let loadedThinkingSupport = "unknown";
let loadedModelSettings = {};
let thinkingPreference = false;
let responseDefaults = { ...defaults, system_prompt: "", thinking: false };
let responseSettingsBaseline = "";
let responseSettingsTimer = null;
let responseRevision = 0;
let composingPrompt = false;
let lastStatus = { status: "not_loaded", detail: "" };
let settingsReturnFocus = null;
let sidebarExpanded = true;
const mobile = matchMedia("(max-width: 720px)");
let lastContext = null;
const composerPopovers = [[ui.optionsPopover, ui.optionsButton], [ui.modelPopover, ui.modelPickerButton], [ui.contextPopover, ui.contextButton]];
const DRAFT_PREFIX = "playground.draft.v1:";
const ACTIVE_CHAT_KEY = "playground.activeChat.v1";
const RESPONSE_PREFIX = "playground.responseSettings.v1:";
const draftCache = new Map();
const browserStateErrors = new Map();
let draftSaveTimer = null;
let draftBaseline = "";
let promptRevision = 0;
let chatNavigation = 0;

function draftKey(id = activeChat) { return DRAFT_PREFIX + (id || "new"); }
function responseKey(id = activeChat) { return RESPONSE_PREFIX + (id || "new"); }
function responseInputIds() { return ["temperatureNumber", "topP", "topK", "minP", "repeatPenalty", "seed"]; }

function serializeResponseSettings() {
  const values = generationValues();
  if (!validateResponseSettings()) values._inputs = Object.fromEntries(responseInputIds().map(id => [id, ui[id].value]));
  return JSON.stringify(values);
}

function validateResponseSettings(report = false) {
  const fields = responseInputIds().map(id => ui[id]);
  const invalid = fields.find(field => !field.checkValidity());
  fields.forEach(field => field.setAttribute("aria-invalid", String(!field.checkValidity())));
  if (report && invalid) {
    ui.responseSettingsError.hidden = false;
    ui.responseSettingsError.textContent = text("invalidResponseSettings");
    if (ui.settingsPanel.hidden && !ui.optionsPopover.matches(":popover-open")) ui.optionsPopover.showPopover();
    if ([ui.topP, ui.topK, ui.minP, ui.repeatPenalty, ui.seed].includes(invalid)) $("advancedOptions").open = true;
    invalid.focus(); invalid.reportValidity();
  }
  if (!invalid) ui.responseSettingsError.hidden = true;
  return !invalid;
}

function showResponseStatus(key) {
  if (ui.responseSettingsStatus.dataset.text !== key) {
    ui.responseSettingsStatus.dataset.text = key;
    ui.responseSettingsStatus.textContent = text(key);
  }
  ui.responseSettingsStatus.classList.toggle("danger", key === "defaultsSaveFailed");
}

function saveResponseSettings() {
  clearTimeout(responseSettingsTimer); responseSettingsTimer = null;
  const value = serializeResponseSettings();
  if (value !== responseSettingsBaseline || browserStateErrors.get(responseKey()) === "write") {
    writeBrowserState(responseKey(), value);
    responseSettingsBaseline = value;
  }
}

function responseSettingsChanged() {
  responseRevision++;
  updateLabels();
  showResponseStatus("responseScopeHelp");
  clearTimeout(responseSettingsTimer);
  responseSettingsTimer = setTimeout(saveResponseSettings, 250);
  validateResponseSettings();
}

function restoreResponseSettings(settings = responseDefaults) {
  let saved = null;
  try { saved = JSON.parse(readBrowserState(responseKey())); } catch (_) {}
  const base = { ...(activeChat ? { ...defaults, system_prompt: "", thinking: false } : responseDefaults), ...settings };
  applySettings({ ...base, ...(saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {}) });
  if (saved?._inputs) for (const id of responseInputIds()) if (typeof saved._inputs[id] === "string") ui[id].value = saved._inputs[id];
  responseSettingsBaseline = serializeResponseSettings();
  showResponseStatus("responseScopeHelp");
}

function saveWorkspaceState() { saveDraft(); saveResponseSettings(); }

async function saveResponseDefaults() {
  if (!validateResponseSettings(true) || ui.saveResponseDefaults.disabled) return;
  const values = generationValues();
  const revision = responseRevision;
  ui.saveResponseDefaults.disabled = true;
  showResponseStatus("savingDefaults");
  try {
    await api("/api/settings", { method: "PATCH", body: JSON.stringify(values) });
    responseDefaults = { ...values };
    saveResponseSettings();
    showResponseStatus(responseRevision === revision ? "defaultsSaved" : "defaultsSavedEarlier");
  } catch (_) { showResponseStatus("defaultsSaveFailed"); }
  finally { ui.saveResponseDefaults.disabled = false; }
}

function readBrowserState(key) {
  if (browserStateErrors.get(key) === "write") return draftCache.get(key);
  try {
    const value = localStorage.getItem(key);
    draftCache.set(key, value); browserStateErrors.delete(key);
    $("draftWarning").hidden = !browserStateErrors.size;
    return value;
  } catch (_) {
    browserStateErrors.set(key, "read"); $("draftWarning").hidden = false;
    return draftCache.get(key) ?? null;
  }
}

function writeBrowserState(key, value) {
  draftCache.set(key, value);
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
    browserStateErrors.delete(key);
    $("draftWarning").hidden = !browserStateErrors.size;
    return true;
  } catch (_) {
    browserStateErrors.set(key, "write"); $("draftWarning").hidden = false;
    return false;
  }
}

function saveDraft() {
  clearTimeout(draftSaveTimer); draftSaveTimer = null;
  if (ui.prompt.value === draftBaseline && browserStateErrors.get(draftKey()) !== "write") return;
  writeBrowserState(draftKey(), ui.prompt.value || null);
  draftBaseline = ui.prompt.value;
}

function promptInput() {
  promptRevision++;
  if (generating && generationSession?.chatId === activeChat) generationSession.draftEdited = true;
  resizePrompt();
  clearTimeout(draftSaveTimer);
  draftSaveTimer = setTimeout(saveDraft, 250);
}

function restoreDraft() {
  ui.prompt.value = readBrowserState(draftKey()) || "";
  draftBaseline = ui.prompt.value; promptRevision++;
  resizePrompt();
}

function activateChat(id, transferDraft = false) {
  saveWorkspaceState();
  const previousKey = draftKey();
  const previousResponseKey = responseKey();
  const responseSnapshot = serializeResponseSettings();
  const value = ui.prompt.value;
  activeChat = id;
  if (transferDraft && writeBrowserState(draftKey(), value || null)) writeBrowserState(previousKey, null);
  if (transferDraft && responseSnapshot && writeBrowserState(responseKey(), responseSnapshot)) writeBrowserState(previousResponseKey, null);
  writeBrowserState(ACTIVE_CHAT_KEY, id || "new");
  restoreDraft();
  updateLabels();
  updateRunningResponse();
  enhancements?.update();
}

function initDrafts() {
  const selected = readBrowserState(ACTIVE_CHAT_KEY);
  activeChat = selected && selected !== "new" ? selected : null;
  restoreDraft();
  restoreResponseSettings();
  window.addEventListener("pagehide", saveWorkspaceState);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") saveWorkspaceState(); });
}

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
  const groups = ["conversationOptions", "generationOptions", "advancedOptions", "responsePreferences"].map(id => {
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

function selectedModelPath() { return ui.customModel.value.trim() || ui.quickModelSelect.value; }

function selectedThinkingSupport() {
  const support = runtimeModelMatchesSelection() ? loadedThinkingSupport
    : availableModels.find(model => model.path === selectedModelPath())?.thinking_support;
  return ["supported", "unsupported"].includes(support) ? support : "unknown";
}

function updateThinkingControls() {
  const support = selectedThinkingSupport();
  const selected = Boolean(selectedModelPath());
  const supported = selected && support === "supported";
  const helpKey = !selected ? "thinkingNoModel" : support === "supported" ? "thinkingHelp"
    : support === "unsupported" ? "thinkingUnsupportedHelp" : "thinkingUnknownHelp";
  const help = text(helpKey);
  ui.thinking.checked = ui.quickThinking.checked = supported && thinkingPreference;
  ui.thinking.disabled = ui.quickThinking.disabled = !supported || loadingModel || runtimeStatus === "loading";
  ui.thinkingState.textContent = text(!selected ? "thinkingUnavailable"
    : support === "unknown" ? "thinkingUnknown" : support === "unsupported" ? "thinkingUnavailable"
    : thinkingPreference ? "thinkingOn" : "thinkingOff");
  ui.thinkingHelp.textContent = ui.thinkingSupportHelp.textContent = help;
  ui.thinkingControl.title = ui.quickThinking.title = ui.thinking.title = help;
  ui.thinkingControl.dataset.support = supported ? "supported" : support;
  ui.modelThinkingSupport.textContent = text(`thinkingSupport_${support}`);
}

function showModelError(message = "") {
  for (const node of [ui.modelMenuError, ui.modelLoadError]) {
    node.textContent = message; node.hidden = !message;
  }
}

function modelSettingsPending() {
  return Boolean(loadedModelPath) && (!runtimeModelMatchesSelection()
    || ["context_size", "gpu_layers", "threads"].some((key, index) => loadedModelSettings[key] !== undefined
      && Number([ui.contextSize, ui.gpuLayers, ui.threads][index].value) !== Number(loadedModelSettings[key])));
}

function updateModelPicker() {
  const path = selectedModelPath();
  const matched = runtimeModelMatchesSelection();
  const pending = modelSettingsPending();
  const isLoading = loadingModel || runtimeStatus === "loading";
  const busy = isLoading || generating || runtimeStatus === "generating";
  const state = isLoading ? "loading" : matched ? "loaded" : path ? "selectedNotLoaded" : "not_loaded";
  ui.modelPickerLabel.textContent = path ? readableModelName(path) : text("selectModel");
  ui.modelPickerButton.setAttribute("aria-label", text("modelPickerDescription", { model: ui.modelPickerLabel.textContent, state: text(state) }));
  ui.modelPickerButton.title = path || text("selectModel");
  ui.selectedModelPath.textContent = path;
  ui.quickModelSelect.hidden = Boolean(ui.customModel.value.trim());
  ui.chooseDetectedModel.hidden = !ui.quickModelSelect.hidden;
  ui.selectedModelState.textContent = text(state);
  ui.selectedModelState.dataset.state = state;
  ui.loadedModelInfo.textContent = loadedModelPath ? text("currentlyLoaded", { model: readableModelName(loadedModelPath) }) : text("noModel");
  ui.loadedModelInfo.title = loadedModelPath;
  ui.loadedModelInfo.hidden = matched;
  ui.modelReady.hidden = !matched || isLoading;
  ui.modelPickerButton.disabled = busy;
  ui.modelSelect.disabled = ui.quickModelSelect.disabled = ui.customModel.disabled = busy;
  ui.quickLoadModel.hidden = !path || (matched && !isLoading && !pending);
  ui.quickLoadModel.textContent = text(isLoading ? "loading" : matched && pending ? "reload" : "load");
  ui.menuLoadModel.hidden = matched && !isLoading && !pending;
  ui.menuLoadModel.textContent = text(isLoading ? "loading" : matched && pending ? "reloadModel" : "loadSelectedModel");
  ui.loadModel.textContent = text(matched && pending ? "reloadModel" : "loadModel");
  for (const notice of [ui.modelReloadNotice, ui.modelMenuReloadNotice]) {
    notice.hidden = !pending;
    notice.textContent = text("modelReloadHelp");
  }
  ui.quickLoadModel.disabled = ui.menuLoadModel.disabled = ui.loadModel.disabled = busy || !path;
  ui.sendButton.disabled = busy || runtimeStatus !== "ready" || !matched;
  const sendHelp = busy ? text(isLoading ? "loading" : "generating") : !path ? text("selectModel") : !matched ? text("loadSelectedFirst")
    : text("sendWithModel", { model: readableModelName(loadedModelPath) });
  ui.sendButton.title = sendHelp;
  ui.sendButton.setAttribute("aria-label", sendHelp);
  const detail = lastStatus.detail;
  const label = typeof detail === "object" ? text(detail.key, detail.values) : detail || text(lastStatus.status);
  const needsLoad = !busy && !matched && ["ready", "not_loaded"].includes(lastStatus.status);
  ui.composerStatus.textContent = needsLoad ? path ? text("loadSelectedFirst") : text("selectModel")
    : !busy && pending && lastStatus.status === "ready" ? text("readyReloadPending") : label;
  ui.composerStatus.dataset.status = needsLoad ? "not_loaded" : lastStatus.status;
  updateThinkingControls();
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
    try { message = errorDetail((await response.json()).detail, message); } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

function errorDetail(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(item => `${item.loc?.at(-1) || "setting"}: ${item.msg}`).join("; ");
  return fallback;
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

function messageElement(role, content = "", metrics = null, status = "complete", sourcePrompt = "", request = null, error = "", messageId = "") {
  document.body.dataset.chatState = "active";
  const wrapper = document.createElement("article");
  wrapper.className = `message ${role}`;
  if (role === "user") {
    wrapper.innerHTML = `<div class="bubble"></div>`;
    wrapper.firstElementChild.textContent = content;
  } else {
    wrapper.generationRequest = request;
    wrapper.messageId = messageId;
    wrapper.innerHTML = `<div class="assistant-head"><img class="assistant-icon" data-brand-logo alt=""><span data-text="assistantName"></span></div><div class="thinking-slot"><details class="thinking-box" hidden><summary></summary><div class="thinking-content"></div></details></div><div class="response-progress" role="status" hidden></div><div class="content"></div><div class="response-notice" hidden></div><details class="response-details" hidden><summary data-text="responseDetails"></summary><div class="metrics"></div></details><div class="message-actions"><button class="copy-response"><span data-icon="copy"></span><span data-text="copy"></span></button><button class="regenerate"><span data-icon="regenerate"></span><span data-text="regenerate"></span></button><button class="continue-response"><span data-icon="continue"></span><span data-text="continue"></span></button></div>`;
    const edit = document.createElement("button"); edit.className = "edit-prompt"; edit.dataset.text = "editPrompt"; edit.textContent = text("editPrompt");
    wrapper.querySelector(".message-actions").appendChild(edit);
    edit.onclick = () => {
      if (!edit.disabled && request) enhancements?.editPrompt({ chatId: activeChat, messageId: wrapper.messageId, message: request.message, request });
    };
    wrapper.querySelector(".copy-response").onclick = async () => { await navigator.clipboard.writeText(wrapper.dataset.raw || ""); showToast(text("copiedResponse")); };
    wrapper.querySelector(".regenerate").onclick = () => {
      if (wrapper.querySelector(".regenerate").disabled) return;
      const retry = ["error", "interrupted"].includes(wrapper.dataset.status);
      if (retry) return send(sourcePrompt || lastUserMessage, wrapper.generationRequest);
      return send(wrapper.generationRequest.message, wrapper.generationRequest, { chatId: activeChat, messageId: wrapper.messageId });
    };
    wrapper.querySelector(".continue-response").onclick = () => { if (!wrapper.querySelector(".continue-response").disabled) return send("Continue."); };
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
  const busy = generating || loadingModel || ["loading", "generating"].includes(runtimeStatus);
  ui.newChat.disabled = generating && !generationSession?.assistant;
  ui.chatList.querySelectorAll(".delete-chat").forEach(button => { button.disabled = generating && button.dataset.chatId === generationSession?.chatId; });
  ui.messages.querySelectorAll(".assistant").forEach(element => {
    const retry = ["error", "interrupted"].includes(element.dataset.status);
    const available = element === latest && Boolean(element.generationRequest);
    const button = element.querySelector(".regenerate");
    button.disabled = busy || (retry ? !available : !element.messageId || !element.generationRequest);
    button.title = retry ? text(available ? "retryHelp" : "retryLatestOnly") : text(element.messageId ? "regenerateHelp" : "regenerateReopen");
    element.querySelector(".continue-response").hidden = retry;
    element.querySelector(".continue-response").disabled = busy || element !== latest || element.dataset.status !== "complete";
    element.querySelector(".continue-response").title = text("continueLatest");
    element.querySelector(".edit-prompt").disabled = busy || !element.messageId || !element.generationRequest || element.dataset.status !== "complete";
  });
  updateRunningResponse();
  enhancements?.update();
}

function updateRunningResponse() {
  const banner = $("runningResponse");
  banner.hidden = !generating || !generationSession?.chatId || generationSession.chatId === activeChat;
  $("runningResponseLabel").textContent = text("runningElsewhere");
}

function updateAssistant(element, raw, metrics = null, status = "complete", liveSeconds = 0, error = "") {
  element.responseMetrics = metrics;
  const statusChanged = element.dataset.status !== status;
  element.dataset.raw = raw;
  element.dataset.status = status;
  const parts = splitThinking(raw);
  const duration = metrics?.total_seconds ?? liveSeconds;
  element.dataset.duration = workedFor(duration);
  const summary = text(status === "streaming" ? "working" : "worked", { duration: element.dataset.duration });
  element.querySelector(".thinking-box").hidden = !parts.thinking.trim();
  element.querySelector(".thinking-box summary").textContent = summary;
  const thinkingContent = element.querySelector(".thinking-content");
  if (status === "streaming") thinkingContent.textContent = parts.thinking.trim();
  else thinkingContent.innerHTML = markdown(parts.thinking.trim());
  const progress = element.querySelector(".response-progress");
  progress.hidden = status !== "streaming";
  const progressLabel = text(element.dataset.started ? "generating" : "preparing");
  if (progress.textContent !== progressLabel) progress.textContent = progressLabel;
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
  element.querySelector(".response-details").hidden = !metrics;
  if (metrics) {
    const values = [
      text("metricFirstToken", { value: metrics.first_token_seconds }), text("metricSpeed", { value: metrics.tokens_per_second }),
      text("metricPrompt", { value: metrics.prompt_tokens }), text("metricOutput", { value: metrics.completion_tokens }),
      text("temperature", { value: Number(metrics.temperature) }), text("thinkingMetric", {
        state: text(metrics.thinking_support === "unknown" ? "thinkingUnknown"
          : metrics.thinking_support === "unsupported" ? "thinkingUnavailable" : metrics.thinking ? "thinkingOn" : "thinkingOff") })
    ];
    if (metrics.model) values.push(text("responseModel", { model: readableModelName(metrics.model) }));
    metricsBox.innerHTML = values.map((value) => `<span>${escapeHtml(value)}</span>`).join("");
  }
  element.querySelector(".message-actions")?.classList.toggle("hidden", status === "streaming");
  const retryLabel = element.querySelector(".regenerate [data-text]");
  retryLabel.dataset.text = status === "error" || status === "interrupted" ? "retry" : "regenerateBranch";
  retryLabel.textContent = text(retryLabel.dataset.text);
  if (statusChanged) updateResponseActions();
}

function scrollDown(force = false) {
  const nearBottom = ui.messages.scrollHeight - ui.messages.scrollTop - ui.messages.clientHeight < 180;
  if (force || nearBottom) requestAnimationFrame(() => ui.messages.scrollTop = ui.messages.scrollHeight);
}

function updateContext(used = Math.ceil(conversationChars / 4), limit = Number(loadedModelSettings.context_size) || Number(ui.contextSize.value) || 4096, exact = false) {
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
    thinking: thinkingPreference,
    temperature: Number(ui.temperatureNumber.value),
    top_p: Number(ui.topP.value), top_k: Number(ui.topK.value), min_p: Number(ui.minP.value),
    repeat_penalty: Number(ui.repeatPenalty.value), seed: Number(ui.seed.value)
  };
}

function applySettings(settings) {
  ui.systemPrompt.value = settings.system_prompt || "";
  ui.systemPreset.value = [...ui.systemPreset.options].some(option => option.value === ui.systemPrompt.value) ? ui.systemPrompt.value : "custom";
  thinkingPreference = Boolean(settings.thinking);
  ui.temperature.value = settings.temperature ?? 0;
  ui.temperatureNumber.value = settings.temperature ?? 0;
  ui.topP.value = settings.top_p ?? .95; ui.topK.value = settings.top_k ?? 40;
  ui.minP.value = settings.min_p ?? .05; ui.repeatPenalty.value = settings.repeat_penalty ?? 1;
  ui.seed.value = settings.seed ?? -1;
  updateLabels();
}

function updateLabels() {
  updateThinkingControls();
  const prompt = ui.systemPrompt.value.trim();
  ui.systemSummary.textContent = text("optionsCustom");
  ui.systemSummary.hidden = !prompt;
  ui.optionsButton.title = text("optionsHelp");
  ui.responseScope.dataset.text = activeChat ? "responseScopeCurrent" : "responseScopeNew";
  ui.responseScope.textContent = text(ui.responseScope.dataset.text);
}

async function loadInitial() {
  saveWorkspaceState();
  const navigation = chatNavigation;
  const initialResponseRevision = responseRevision;
  const initialPromptRevision = promptRevision;
  const preserveNewComposition = activeChat === null && (Boolean(ui.prompt.value) || readBrowserState(responseKey()) !== null);
  try {
    const [models, settings, status, chats] = await Promise.all([
      api("/api/models"), api("/api/settings"), api("/api/status"), api("/api/chats")
    ]);
    populateModels(models);
    if (settings.model_path && models.some((model) => model.path === settings.model_path)) ui.modelSelect.value = settings.model_path;
    else if (settings.model_path) ui.customModel.value = settings.model_path;
    ui.quickModelSelect.value = ui.modelSelect.value;
    ui.contextSize.value = settings.context_size; ui.gpuLayers.value = settings.gpu_layers; ui.threads.value = settings.threads;
    responseDefaults = { ...responseDefaults, ...Object.fromEntries(Object.keys(generationValues())
      .filter(key => Object.hasOwn(settings, key)).map(key => [key, settings[key]])) };
    if (responseRevision === initialResponseRevision) restoreResponseSettings(responseDefaults);
    updateContext();
    updateModelStatus(status, settings);
    allChats = chats;
    renderChatList();
    if (navigation === chatNavigation && !preserveNewComposition && !(activeChat === null
        && (promptRevision !== initialPromptRevision || responseRevision !== initialResponseRevision))) {
      const selected = readBrowserState(ACTIVE_CHAT_KEY);
      const initialChat = chats.find(chat => chat.id === selected) || (selected === "new" ? null : chats[0]);
      if (initialChat) await openChat(initialChat.id);
      else { activateChat(null); restoreResponseSettings(); }
    } else if (activeChat === null) writeBrowserState(ACTIVE_CHAT_KEY, "new");
    if (
      status.status === "not_loaded"
      && settings.model_path
      && models.some((model) => model.path === settings.model_path)
    ) await loadModel();
  } catch (error) { setStatus("error", error.message); }
}

function populateModels(models) {
  availableModels = models;
  const options = models.length ? models.map(model => `<option value="${escapeHtml(model.path)}">${escapeHtml(model.name)} · ${(model.size / 1073741824).toFixed(2)} GiB</option>`).join("")
    : `<option value="">${escapeHtml(text("noModels"))}</option>`;
  ui.modelSelect.innerHTML = ui.quickModelSelect.innerHTML = options;
}

async function refreshModels(preferred = null) {
  const models = await api("/api/models");
  const current = selectedModelPath();
  populateModels(models);
  const target = preferred && !generating && !loadingModel && !["loading", "generating"].includes(runtimeStatus) ? preferred : current;
  if (models.some(model => model.path === target)) {
    ui.customModel.value = ""; ui.modelSelect.value = ui.quickModelSelect.value = target;
  } else {
    ui.customModel.value = target || ""; ui.modelSelect.value = ui.quickModelSelect.value = "";
  }
  updateModelPicker(); enhancements?.update();
  return Boolean(preferred && target === preferred);
}

function updateModelStatus(status, settings = {}) {
  clearTimeout(runtimeRefreshTimer); runtimeRefreshTimer = null;
  runtimeStatus = status.status;
  const previousPath = loadedModelPath;
  const previousContext = loadedModelSettings.context_size;
  loadedModelPath = ["error", "not_loaded"].includes(status.status) ? "" : status.model_path || "";
  if (status.loaded_settings && typeof status.loaded_settings === "object") loadedModelSettings = { ...status.loaded_settings };
  else if (settings.model_path === loadedModelPath) loadedModelSettings = { ...settings };
  else if (previousPath !== loadedModelPath) loadedModelSettings = {};
  if (!loadedModelPath) loadedModelSettings = {};
  if (loadedModelSettings.context_size && Number(previousContext) !== Number(loadedModelSettings.context_size)) updateContext();
  loadedThinkingSupport = status.thinking_support || "unknown";
  setStatus(status.status, status.error || "");
  if (loadedModelPath) {
    ui.activeModel.textContent = loadedModelPath.split(/[\\/]/).pop();
    const model = availableModels.find(model => model.path === loadedModelPath);
    const size = model ? `${(model.size / 1073741824).toFixed(2)} GiB` : text("custom");
    ui.modelMeta.textContent = text("loadedModelMeta", { size, context: loadedModelSettings.context_size || ui.contextSize.value,
      gpu: loadedModelSettings.gpu_layers ?? ui.gpuLayers.value });
  } else { ui.activeModel.textContent = text("noModel"); ui.modelMeta.textContent = text("selectModel"); }
  if (!generating && !loadingModel && ["loading", "generating"].includes(runtimeStatus)) {
    runtimeRefreshTimer = setTimeout(refreshRuntimeStatus, 1000);
  }
  updateResponseActions();
}

async function refreshRuntimeStatus() {
  if (generating || loadingModel) return;
  try {
    const status = await api("/api/status");
    if (!generating && !loadingModel) updateModelStatus(status);
  } catch (_) {
    if (!generating && !loadingModel) updateModelStatus({ status: "error", error: text("connectionLost") });
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
      if (generating && chat.id === generationSession?.chatId) return;
      if (!confirm(text("deleteConfirm", { title: chat.title }))) return;
      await api(`/api/chats/${chat.id}`, { method: "DELETE" });
      if (activeChat === chat.id) {
        clearTimeout(draftSaveTimer); draftSaveTimer = null;
        clearTimeout(responseSettingsTimer); responseSettingsTimer = null;
        writeBrowserState(draftKey(chat.id), null);
        writeBrowserState(responseKey(chat.id), null);
        activeChat = null; ui.prompt.value = ""; draftBaseline = "";
        responseSettingsBaseline = serializeResponseSettings();
        writeBrowserState(ACTIVE_CHAT_KEY, "new");
        resetMessages(); await newChat();
      } else { writeBrowserState(draftKey(chat.id), null); writeBrowserState(responseKey(chat.id), null); }
      await refreshChats();
    };
    row.querySelector(".delete-chat").dataset.chatId = chat.id;
    row.querySelector(".delete-chat").disabled = generating && chat.id === generationSession?.chatId;
    ui.chatList.appendChild(row);
  });
}

async function newChat() {
  if (generating && !generationSession?.assistant) return;
  chatNavigation++;
  closeComposerPopovers();
  // An unsent new chat has its own draft; create its server record on Send.
  activateChat(null); restoreResponseSettings(); resetMessages(); lastUserMessage = ""; conversationChars = ui.systemPrompt.value.length;
  updateContext();
  renderChatList(); if (mobile.matches) setSidebar(false); ui.prompt.focus();
}

async function openChat(id) {
  if (generating && !generationSession?.assistant) return;
  const navigation = ++chatNavigation;
  closeComposerPopovers();
  saveWorkspaceState();
  const chat = await api(`/api/chats/${id}`);
  if (navigation !== chatNavigation) return;
  displayChat(chat, id);
  await refreshChats();
  if (mobile.matches) setSidebar(false);
}

function displayChat(chat, id = chat.id) {
  activateChat(id); resetMessages(); lastUserMessage = "";
  restoreResponseSettings(chat.settings);
  conversationChars = ui.systemPrompt.value.length;
  if (chat.branch_of) {
    contextNotice(text("regenerateNotice"));
    ui.messages.lastElementChild.dataset.text = "regenerateNotice";
    ui.messages.lastElementChild.classList.add("branch-notice");
  }
  let sourcePrompt = "";
  chat.messages.forEach((message) => {
    if (message.type === "user") { sourcePrompt = message.content; lastUserMessage = message.content; }
    messageElement(message.type, message.content, message.metrics, message.status, sourcePrompt, message.request, message.error, message.id);
    conversationChars += message.content.length;
  });
  if (generationSession?.chatId === id) generationSession.countedInView = chat.messages.some(message => message.id && message.id === generationSession.assistant?.messageId);
  if (generationSession?.chatId === id && generationSession.assistant
      && !chat.messages.some(message => message.id && message.id === generationSession.assistant.messageId)) {
    if (!chat.messages.some(message => message.type === "user" && message.request?.request_id === generationSession.request.request_id)) {
      messageElement("user", generationSession.request.message);
      conversationChars += generationSession.request.message.length;
    }
    ui.messages.appendChild(generationSession.assistant);
    refreshThemeContent(generationSession.assistant);
    generationSession.assistant.querySelector(".thinking-box summary").textContent = text(generationSession.assistant.dataset.status === "streaming" ? "working" : "worked", { duration: generationSession.assistant.dataset.duration });
    if (!generating) conversationChars += generationSession.assistant.dataset.raw.length;
  }
  const lastMetrics = (generationSession?.chatId === id ? generationSession.assistant?.responseMetrics : null)
    || [...chat.messages].reverse().find((message) => message.metrics)?.metrics;
  if (lastMetrics) updateContext(lastMetrics.context_used, lastMetrics.context_size, true);
  else updateContext();
  scrollDown(true);
  updateResponseActions();
}

async function loadModel() {
  if (loadingModel || generating || ["loading", "generating"].includes(runtimeStatus)) return;
  const modelPath = selectedModelPath();
  if (!modelPath) return setStatus("error", text("selectGGUF"));
  const invalid = [ui.contextSize, ui.gpuLayers, ui.threads].find(field => !field.checkValidity());
  if (invalid) { openSettings(true); invalid.focus(); invalid.reportValidity(); return; }
  showModelError();
  closeComposerPopovers();
  loadingModel = true;
  clearTimeout(runtimeRefreshTimer); runtimeRefreshTimer = null;
  let seconds = 0;
  let loaded = false;
  setStatus("loading", { key: "loadingStatus", values: { seconds } });
  const loadingTimer = setInterval(() => setStatus("loading", { key: "loadingStatus", values: { seconds: ++seconds } }), 1000);
  try {
    const loadValues = { model_path: modelPath, context_size: Number(ui.contextSize.value), gpu_layers: Number(ui.gpuLayers.value), threads: Number(ui.threads.value) };
    const status = await api("/api/model/load", { method: "POST", body: JSON.stringify(loadValues) });
    loaded = true;
    if (ui.customModel.value.trim()) ui.customModel.value = status.model_path;
    else { ui.quickModelSelect.value = status.model_path; ui.modelSelect.value = status.model_path; }
    updateModelStatus(status, loadValues);
    openSettings(false);
    showToast(text(status.load_settings_saved === false ? "modelLoadSettingsUnsaved" : "modelReady", { model: readableModelName(status.model_path) }));
  } catch (error) {
    showModelError(error.message);
    setStatus("error", error.message);
    showToast(text("modelLoadFailed"));
  }
  finally {
    clearInterval(loadingTimer);
    loadingModel = false;
    try { updateModelStatus(await api("/api/status")); }
    catch (_) {
      if (!loaded) updateModelStatus({ status: "error", error: text("connectionLost") });
    }
    updateModelPicker();
  }
}

function runtimeModelMatchesSelection() {
  return Boolean(loadedModelPath) && loadedModelPath === selectedModelPath();
}

async function send(message, retryRequest = null, branchTarget = null) {
  const fromComposer = message === undefined;
  message ??= ui.prompt.value.trim();
  if (!message || generating) return;
  enhancements?.finishCompose();
  if (!retryRequest && !validateResponseSettings(true)) return;
  const submittedDraft = ui.prompt.value;
  let submittedRevision = promptRevision;
  let request = { ...(retryRequest || { message, ...generationValues(), request_id: crypto.randomUUID() }),
    expected_model_path: selectedModelPath() || null };
  // Reserve the UI before the first await so rapid clicks cannot submit twice.
  generating = true;
  const session = generationSession = { chatId: activeChat, assistant: null, request, draftEdited: false };
  clearTimeout(runtimeRefreshTimer); runtimeRefreshTimer = null;
  chatNavigation++;
  saveWorkspaceState();
  closeComposerPopovers();
  ui.quickModelSelect.disabled = true; ui.quickLoadModel.disabled = true; ui.loadModel.disabled = true;
  ui.sendButton.classList.add("hidden"); ui.stopButton.classList.remove("hidden"); ui.stopButton.disabled = true;
  updateResponseActions();
  let elapsedSeconds = 0;
  let raw = "", assistant = null, failed = false;
  setStatus("generating", { key: "preparing" });
  try {
    const status = await api("/api/status");
    updateModelStatus(status);
    if (!["ready", "stopped"].includes(status.status)) {
      if (status.status === "generating") showToast(text("generationBusy"));
      else openSettings(true);
      return;
    }
    if (!runtimeModelMatchesSelection()) { showToast(text("loadSelectedFirst")); return; }
    if (branchTarget) {
      const branch = await api(`/api/chats/${branchTarget.chatId}/branch`, { method: "POST", body: JSON.stringify({ message_id: branchTarget.messageId,
        ...(branchTarget.editedMessage ? { message: branchTarget.editedMessage } : {}) }) });
      displayChat(branch);
      if (branchTarget.editedMessage) request = { ...branch.messages.at(-1).request, expected_model_path: request.expected_model_path };
    }
    if (!activeChat) {
      const chat = await api("/api/chats", { method: "POST", body: JSON.stringify(request) });
      const revisionBeforeTransfer = promptRevision;
      activateChat(chat.id, true); resetMessages(); conversationChars = request.system_prompt.length;
      // Moving the same draft from the new-chat slot is not a user edit.
      if (submittedRevision === revisionBeforeTransfer) submittedRevision = promptRevision;
      if (mobile.matches) setSidebar(false);
    }
    lastUserMessage = request.message;
    if (!retryRequest) {
      messageElement("user", request.message);
      conversationChars += request.message.length; updateContext();
    }
    assistant = messageElement("assistant", "", null, "streaming", request.message, request);
    session.chatId = activeChat; session.assistant = assistant; session.request = request;
    updateResponseActions();
    scrollDown(true);
    generationTimer = setInterval(() => {
      elapsedSeconds += 1;
      setStatus("generating", { key: "generatingStatus", values: { seconds: elapsedSeconds } });
      updateAssistant(assistant, raw, null, "streaming", elapsedSeconds);
    }, 1000);
    const response = await fetch(`/api/chats/${session.chatId}/generate`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request)
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = errorDetail((await response.json()).detail, detail); } catch (_) {}
      throw new Error(detail || text("responseRequestFailed"));
    }
    if (!response.body) throw new Error(text("streamInterrupted"));
    if (fromComposer && !session.draftEdited) {
      if (activeChat === session.chatId && ui.prompt.value === submittedDraft && promptRevision === submittedRevision) {
        ui.prompt.value = ""; resizePrompt(); saveDraft();
      } else if (activeChat !== session.chatId && readBrowserState(draftKey(session.chatId)) === submittedDraft) {
        writeBrowserState(draftKey(session.chatId), null);
      }
    }
    ui.stopButton.disabled = false;
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = "";
    let terminal = false;
    const consume = line => {
      if (!line.trim() || terminal) return;
      const item = JSON.parse(line);
      if (item.type === "start") {
        assistant.messageId = item.message_id || assistant.messageId;
        assistant.dataset.started = "true";
        setStatus("generating", { key: "generatingStatus", values: { seconds: elapsedSeconds } });
        updateAssistant(assistant, raw, null, "streaming", elapsedSeconds);
      }
      else if (item.type === "context_trimmed" && activeChat === session.chatId) contextNotice(item.message);
      else if (item.type === "token") {
        const follow = ui.messages.scrollHeight - ui.messages.scrollTop - ui.messages.clientHeight < 180;
        raw += item.text; updateAssistant(assistant, raw, null, "streaming", elapsedSeconds);
        if (activeChat === session.chatId) scrollDown(follow);
      }
      else if (item.type === "done" || item.type === "stopped") {
        terminal = true;
        clearInterval(generationTimer);
        updateAssistant(assistant, raw, item.metrics, item.type === "stopped" ? "interrupted" : "complete", elapsedSeconds);
        if (item.metrics && activeChat === session.chatId) updateContext(item.metrics.context_used, item.metrics.context_size, true);
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
    else showToast(text(branchTarget ? "regenerateFailed" : "requestFailed"));
  } finally {
    clearInterval(generationTimer); generationTimer = null;
    if (activeChat === session.chatId && !session.countedInView) conversationChars += raw.length;
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
function resizePrompt() {
  ui.prompt.style.height = "auto";
  ui.prompt.style.height = enhancements?.isExpanded() ? "" : `${Math.min(ui.prompt.scrollHeight, 180)}px`;
}

ui.settingsButton.onclick = () => openSettings(true); ui.closeSettings.onclick = () => openSettings(false); ui.overlay.onclick = () => { openSettings(false); if (mobile.matches) setSidebar(false); };
ui.modelSettingsButton.onclick = () => { openSettings(true); ui.modelSelect.focus(); };
ui.menuButton.onclick = () => setSidebar(mobile.matches ? !ui.sidebar.classList.contains("open") : !sidebarExpanded);
ui.sidebarClose.onclick = () => setSidebar(false);
mobile.addEventListener("change", () => setSidebar(mobile.matches ? false : sidebarExpanded));
ui.newChat.onclick = newChat; ui.loadModel.onclick = loadModel; ui.sendButton.onclick = () => send(); ui.stopButton.onclick = stop;
ui.prompt.oninput = promptInput;
ui.prompt.oncompositionstart = () => { composingPrompt = true; };
ui.prompt.oncompositionend = () => { composingPrompt = false; };
ui.prompt.onkeydown = event => {
  if (event.defaultPrevented || event.isComposing || composingPrompt || event.keyCode === 229 || event.repeat) return;
  const modifier = enhancements?.isExpanded() || enhancements?.modifierSends();
  if (event.key === "Enter" && !event.shiftKey && !event.altKey && (modifier ? (event.ctrlKey !== event.metaKey) : !event.ctrlKey && !event.metaKey)) {
    event.preventDefault(); return send();
  }
};
ui.temperature.oninput = () => { ui.temperatureNumber.value = ui.temperature.value; responseSettingsChanged(); };
ui.temperatureNumber.oninput = () => { ui.temperature.value = Math.max(0, Math.min(2, Number(ui.temperatureNumber.value))); responseSettingsChanged(); };
ui.thinking.onchange = () => { if (!ui.thinking.disabled) { thinkingPreference = ui.thinking.checked; responseSettingsChanged(); } else updateLabels(); };
ui.quickThinking.onchange = () => { if (!ui.quickThinking.disabled) { thinkingPreference = ui.quickThinking.checked; responseSettingsChanged(); } else updateLabels(); };
ui.systemPrompt.oninput = () => { ui.systemPreset.value = "custom"; responseSettingsChanged(); };
ui.systemPreset.onchange = () => { if (ui.systemPreset.value !== "custom") ui.systemPrompt.value = ui.systemPreset.value; responseSettingsChanged(); };
for (const field of [ui.topP, ui.topK, ui.minP, ui.repeatPenalty, ui.seed]) field.oninput = responseSettingsChanged;
for (const field of [ui.contextSize, ui.gpuLayers, ui.threads]) field.oninput = updateModelPicker;
ui.resetSettings.onclick = () => { applySettings(responseDefaults); responseSettingsChanged(); saveResponseSettings(); };
ui.saveResponseDefaults.onclick = saveResponseDefaults;
ui.modelSelect.onchange = () => { ui.customModel.value = ""; ui.quickModelSelect.value = ui.modelSelect.value; showModelError(); updateModelPicker(); };
ui.customModel.oninput = () => { showModelError(); updateModelPicker(); };
ui.chooseDetectedModel.onclick = () => { ui.customModel.value = ""; showModelError(); updateModelPicker(); ui.quickModelSelect.focus(); };
ui.quickLoadModel.onclick = ui.menuLoadModel.onclick = loadModel;
ui.chatSearch.oninput = renderChatList;
ui.quickModelSelect.onchange = () => {
  if (generating || loadingModel) return;
  ui.modelSelect.value = ui.quickModelSelect.value;
  ui.customModel.value = "";
  showModelError(); updateModelPicker();
};
document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || event.isComposing || composingPrompt || event.keyCode === 229) return;
  if (event.key === "Escape") {
    if (composerPopovers.some(([popover]) => popover.matches(":popover-open"))) return;
    if (!ui.settingsPanel.hidden) { event.preventDefault(); openSettings(false); }
    else if (mobile.matches && ui.sidebar.classList.contains("open")) { event.preventDefault(); setSidebar(false); }
  }
  if ((event.ctrlKey || event.metaKey) && event.shiftKey && !event.altKey && !event.repeat
      && event.key.toLowerCase() === "x" && generating && !ui.stopButton.disabled) {
    event.preventDefault(); stop();
  }
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
  enhancements?.themeChanged();
});

initComposerPopovers();
initDrafts();
await initThemes();
enhancements = initEnhancements({ api, text, refreshThemeContent, notify: showToast, resize: resizePrompt, send: () => send(),
  getState: () => ({ chatId: activeChat, draft: ui.prompt.value, settings: generationValues(), generating,
    modelBusy: loadingModel || ["loading", "generating"].includes(runtimeStatus), hasModels: Boolean(availableModels.length || selectedModelPath()) }),
  refreshModels,
  loadDownloaded: async path => { if (await refreshModels(path)) await loadModel(); },
  closePanels: () => { openSettings(false); closeComposerPopovers(); },
  setDraft: value => { ui.prompt.value = value; promptInput(); saveDraft(); ui.prompt.focus(); },
  applyProfile: values => { applySettings(values); responseSettingsChanged(); saveResponseSettings(); },
  validSettings: () => validateResponseSettings(),
  edit: (message, target) => send(message, target.request, { chatId: target.chatId, messageId: target.messageId, editedMessage: message }),
  resetSampling: (parameter = null) => {
    for (const [key, id] of [["temperature", "temperatureNumber"], ["top_p", "topP"], ["top_k", "topK"], ["min_p", "minP"], ["repeat_penalty", "repeatPenalty"], ["seed", "seed"]]) {
      if (parameter === null ? key !== "temperature" : key === parameter) ui[id].value = responseDefaults[key];
    }
    ui.temperature.value = ui.temperatureNumber.value; responseSettingsChanged();
  }
});
$("returnToResponse").onclick = () => { if (generationSession?.chatId) openChat(generationSession.chatId).catch(error => showToast(error.message)); };
setSidebar(!mobile.matches);
await loadInitial();
