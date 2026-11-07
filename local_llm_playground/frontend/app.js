import { applyTheme, initThemes, text, refreshThemeContent } from "./theme.js?v=14";
import { initEnhancements } from "./enhancements.js?v=11";

const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries(
  "sidebar sidebarClose chatList chatSearch newChat statusDot statusText activeModel modelMeta messages welcome prompt sendButton stopButton composerStatus contextBar contextLabel settingsButton settingsPanel closeSettings overlay toast menuButton modelSelect quickModelSelect quickLoadModel customModel chooseDetectedModel contextSize gpuLayers threads loadModel systemPrompt systemSummary systemPreset thinking quickThinking thinkingState thinkingControl thinkingHelp thinkingSupportHelp temperature temperatureNumber topP topK minP repeatPenalty seed resetSettings saveResponseDefaults responseSettingsStatus responseSettingsError responseScope modelReloadNotice modelMenuReloadNotice optionsButton optionsPopover composerSettingsSlot modelPickerButton modelPopover modelPickerLabel modelReady selectedModelPath selectedModelState loadedModelInfo modelThinkingSupport menuLoadModel modelMenuError modelLoadError quickUnloadModel menuUnloadModel unloadModel modelReleaseHelp modelSettingsButton contextButton contextPopover contextSummary contextWarning"
    .split(" ").map((id) => [id, $(id)])
);

const defaults = { temperature: 0, top_p: .95, top_k: 40, min_p: .05, repeat_penalty: 1, seed: -1 };
let activeChat = null;
let generating = false;
let generationSession = null;
let stopRequestPending = false;
let enhancements = null;
let lastUserMessage = "";
let allChats = [];
let undoChatMove = null;
const movingChats = new Set();
let chatSearchTimer = null;
let chatSearchRequest = null;
let chatSearchResults = null;
let conversationChars = 0;
let generationTimer = null;
let loadedModelPath = "";
let canUnloadModel = false;
let loadingModel = false;
let unloadingModel = false;
let modelOperation = 0;
let loadRequestId = null;
let runtimeStatus = "not_loaded";
let runtimeRefreshTimer = null;
let idleUnloadMinutes = 0;
let unloadedForIdle = false;
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
const PROMPT_QUEUE_KEY = "playground.promptQueue.v1";
const MAX_QUEUED_PROMPTS = 20;
const promptQueues = new Map();
let queueStorageUnavailable = false;
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
  updateQueueControls();
  clearTimeout(draftSaveTimer);
  draftSaveTimer = setTimeout(saveDraft, 250);
}

function restoreDraft() {
  ui.prompt.value = readBrowserState(draftKey()) || "";
  draftBaseline = ui.prompt.value; promptRevision++;
  resizePrompt();
}

function backupDrafts(chatIds) {
  saveDraft();
  return [null, ...chatIds].flatMap(chatId => {
    const draft = readBrowserState(draftKey(chatId));
    return draft ? [{ chat_id: chatId, text: draft }] : [];
  });
}

function restoreBackupDrafts(drafts, chatIds) {
  saveDraft();
  const allowed = new Set([null, ...chatIds]);
  let added = 0, skipped = 0, persisted = true;
  for (const entry of drafts) {
    if (!entry || !allowed.has(entry.chat_id) || typeof entry.text !== "string" || !entry.text
        || readBrowserState(draftKey(entry.chat_id))) {
      skipped++;
      continue;
    }
    persisted = writeBrowserState(draftKey(entry.chat_id), entry.text) && persisted;
    added++;
    if (activeChat === entry.chat_id) { restoreDraft(); updateQueueControls(); }
  }
  return { draftsAdded: added, draftsSkipped: skipped, persisted };
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
  updateChatTools();
  renderPromptQueue();
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
  ui.thinking.disabled = ui.quickThinking.disabled = !supported || loadingModel || unloadingModel || ["loading", "unloading"].includes(runtimeStatus);
  ui.thinkingState.textContent = text(!selected ? "thinkingUnavailable"
    : support === "unknown" ? "thinkingUnknown" : support === "unsupported" ? "thinkingUnavailable"
    : thinkingPreference ? "thinkingOn" : "thinkingOff");
  ui.thinkingHelp.textContent = ui.thinkingSupportHelp.textContent = help;
  ui.thinkingControl.title = ui.quickThinking.title = ui.thinking.title = help;
  ui.thinkingControl.dataset.support = supported ? "supported" : support;
  ui.modelThinkingSupport.textContent = text(`thinkingSupport_${support}`);
}

function modelLoadRecovery(message) {
  if (/out of memory|cannot allocate memory|failed to allocate|bad_alloc|allocation failed/i.test(message)) return "Memory";
  if (/cuda|vulkan|gpu backend|gpu layers/i.test(message)) return "Gpu";
  if (/gguf|no such file|file not found/i.test(message)) return "File";
  return "Other";
}

function showModelError(message = "", recover = false) {
  for (const node of [ui.modelMenuError, ui.modelLoadError]) {
    node.textContent = message; node.hidden = !message;
  }
  const kind = recover && message ? modelLoadRecovery(message) : "";
  const canReduce = kind === "Memory" && Number(ui.contextSize.value) > 256;
  const canUseCpu = kind === "Gpu" && Number(ui.gpuLayers.value) !== 0;
  const browse = kind === "File" || (kind === "Memory" && !canReduce);
  const action = browse ? "getModels" : canReduce ? "reduceLoadContext" : canUseCpu ? "loadOnCpu" : "modelSettings";
  for (const id of ["modelMenuRecovery", "modelLoadRecovery"]) {
    const button = $(id), help = $(id + "Help");
    help.hidden = button.hidden = !kind;
    help.textContent = kind ? text(`loadRecovery${kind}`) : "";
    button.textContent = kind ? text(action) : "";
    button.onclick = kind ? () => {
      openSettings(true);
      if (browse) { $("getModelsButton").click(); return; }
      if (canReduce) {
        ui.contextSize.value = Math.max(256, Math.floor(Number(ui.contextSize.value) / 2));
        showToast(text("smallerContextPrepared"));
      } else if (canUseCpu) {
        ui.gpuLayers.value = 0;
        showToast(text("cpuSettingsPrepared"));
      }
      updateModelPicker();
      (canReduce || canUseCpu ? ui.loadModel : ui.customModel).focus();
    } : null;
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
  const isUnloading = unloadingModel || runtimeStatus === "unloading";
  const busy = isLoading || isUnloading || generating || stopRequestPending || runtimeStatus === "generating";
  const state = isUnloading ? "unloading" : isLoading ? "loading" : matched ? "loaded" : path ? "selectedNotLoaded" : "not_loaded";
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
  ui.modelReady.hidden = !matched || isLoading || isUnloading;
  ui.modelPickerButton.disabled = busy;
  const canRelease = isLoading || isUnloading || canUnloadModel || Boolean(loadedModelPath);
  const releaseLabel = text(isUnloading ? "unloading" : isLoading ? "cancelModelLoad" : "unloadModel");
  const releaseHelp = text(generating || runtimeStatus === "generating" ? "unloadAfterResponse" : isLoading ? "cancelModelLoadHelp" : "unloadModelHelp");
  for (const button of [ui.quickUnloadModel, ui.menuUnloadModel, ui.unloadModel]) {
    button.hidden = !canRelease; button.textContent = releaseLabel;
    button.disabled = isUnloading || generating || runtimeStatus === "generating";
    button.title = releaseHelp; button.setAttribute("aria-label", releaseLabel);
  }
  ui.modelReleaseHelp.hidden = !canRelease; ui.modelReleaseHelp.textContent = isUnloading ? text("unloading") : releaseHelp;
  ui.modelSelect.disabled = ui.quickModelSelect.disabled = ui.customModel.disabled = busy;
  ui.quickLoadModel.hidden = isLoading || isUnloading || !path || (matched && !pending);
  ui.quickLoadModel.textContent = text(isLoading ? "loading" : matched && pending ? "reload" : "load");
  ui.menuLoadModel.hidden = isLoading || isUnloading || (matched && !pending);
  ui.loadModel.hidden = isLoading || isUnloading;
  ui.menuLoadModel.textContent = text(isLoading ? "loading" : matched && pending ? "reloadModel" : "loadSelectedModel");
  ui.loadModel.textContent = text(matched && pending ? "reloadModel" : "loadModel");
  for (const notice of [ui.modelReloadNotice, ui.modelMenuReloadNotice]) {
    notice.hidden = !pending;
    notice.textContent = text("modelReloadHelp");
  }
  ui.quickLoadModel.disabled = ui.menuLoadModel.disabled = ui.loadModel.disabled = busy || !path;
  const readOnly = activeChatVisibility() !== "active";
  ui.sendButton.disabled = busy || readOnly || runtimeStatus !== "ready" || !matched;
  const sendHelp = readOnly ? text("restoreBeforeSending") : busy ? text(isUnloading ? "unloading" : isLoading ? "loading" : "generating") : !path ? text("selectModel") : !matched ? text("loadSelectedFirst")
    : text("sendWithModel", { model: readableModelName(loadedModelPath) });
  ui.sendButton.title = sendHelp;
  ui.sendButton.setAttribute("aria-label", sendHelp);
  const detail = lastStatus.detail;
  const label = typeof detail === "object" ? text(detail.key, detail.values) : detail || text(lastStatus.status);
  const needsLoad = !busy && !matched && ["ready", "not_loaded"].includes(lastStatus.status);
  ui.composerStatus.textContent = readOnly ? text("restoreBeforeSending") : needsLoad ? unloadedForIdle ? text("idleModelUnloaded") : path ? text("loadSelectedFirst") : text("selectModel")
    : !busy && pending && lastStatus.status === "ready" ? text("readyReloadPending") : label;
  ui.composerStatus.dataset.status = needsLoad ? "not_loaded" : lastStatus.status;
  updateThinkingControls();
  updateQueueControls();
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

function messageElement(role, content = "", metrics = null, status = "complete", sourcePrompt = "", request = null, error = "", messageId = "", attach = true) {
  if (attach) document.body.dataset.chatState = "active";
  const wrapper = document.createElement("article");
  wrapper.className = `message ${role}`;
  wrapper.messageId = messageId;
  if (role === "user") {
    wrapper.innerHTML = `<div class="bubble"></div>`;
    wrapper.firstElementChild.textContent = content;
  } else {
    wrapper.generationRequest = request;
    wrapper.innerHTML = `<div class="assistant-head"><img class="assistant-icon" data-brand-logo alt=""><span data-text="assistantName"></span></div><div class="thinking-slot"><details class="thinking-box" hidden><summary></summary><div class="thinking-content"></div></details></div><div class="response-progress" role="status" hidden></div><div class="content"></div><div class="response-notice" hidden></div><details class="response-details" hidden><summary data-text="responseDetails"></summary><div class="metrics"></div></details><div class="message-actions"><button class="copy-response"><span data-icon="copy"></span><span data-text="copy"></span></button><button class="regenerate"><span data-icon="regenerate"></span><span data-text="regenerate"></span></button><button class="continue-response"><span data-icon="continue"></span><span data-text="continue"></span></button></div>`;
    const edit = document.createElement("button"); edit.className = "edit-prompt"; edit.dataset.text = "editPrompt"; edit.textContent = text("editPrompt");
    wrapper.querySelector(".message-actions").appendChild(edit);
    const compare = document.createElement("button"); compare.className = "compare-response";
    compare.dataset.text = "compareResponse"; compare.textContent = text("compareResponse");
    wrapper.querySelector(".message-actions").appendChild(compare);
    compare.onclick = () => enhancements?.compareResponse(activeChat, wrapper.messageId);
    const bookmark = document.createElement("button"); bookmark.className = "bookmark-response";
    wrapper.querySelector(".message-actions").appendChild(bookmark);
    bookmark.onclick = async () => {
      if (bookmark.disabled || bookmark.hidden) return;
      bookmark.disabled = true;
      try {
        const updated = await api(`/api/chats/${activeChat}/bookmark/${encodeURIComponent(wrapper.messageId)}`, {
          method: "PATCH", body: JSON.stringify({ value: !wrapper.bookmarked })
        });
        wrapper.bookmarked = updated.bookmarked;
        updateResponseActions();
      } catch (error) { showToast(error.message); }
      finally { bookmark.disabled = false; }
    };
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
  if (attach) {
    ui.messages.appendChild(wrapper);
    updateResponseActions();
    scrollDown();
  }
  return wrapper;
}

function updateResponseActions() {
  const latest = [...ui.messages.querySelectorAll(".message")].at(-1);
  const busy = generating || loadingModel || activeChatVisibility() !== "active" || ["loading", "unloading", "generating"].includes(runtimeStatus);
  ui.newChat.disabled = generating && !generationSession?.assistant && !generationSession?.queuedId;
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
    element.querySelector(".compare-response").hidden = !element.messageId || !element.generationRequest || element.dataset.status !== "complete";
    const bookmark = element.querySelector(".bookmark-response");
    bookmark.hidden = !element.messageId || element.dataset.status !== "complete" || activeChatVisibility() === "trashed";
    bookmark.dataset.text = element.bookmarked ? "removeBookmark" : "bookmarkResponse";
    bookmark.textContent = text(bookmark.dataset.text);
    bookmark.setAttribute("aria-pressed", String(Boolean(element.bookmarked)));
  });
  updateRunningResponse();
  enhancements?.update();
}

function updateRunningResponse() {
  const banner = $("runningResponse");
  banner.hidden = !generating || !generationSession?.chatId || generationSession.chatId === activeChat;
  $("runningResponseLabel").textContent = text("runningElsewhere");
}

function restorePromptQueues() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(PROMPT_QUEUE_KEY) || "[]");
    if (!Array.isArray(saved)) return;
    for (const entry of saved) {
      if (!Array.isArray(entry) || typeof entry[0] !== "string" || !Array.isArray(entry[1])) continue;
      const items = entry[1].filter(request => typeof request?.message === "string" && request.message.trim()
        && typeof request.request_id === "string" && typeof request.expected_model_path === "string");
      if (items.length) promptQueues.set(entry[0], { items: items.slice(0, MAX_QUEUED_PROMPTS), paused: true, reason: "promptQueueRefreshed" });
    }
  } catch (_) { queueStorageUnavailable = true; }
}

function savePromptQueues() {
  try {
    const entries = [...promptQueues].map(([id, queue]) => [id, queue.items]);
    sessionStorage.setItem(PROMPT_QUEUE_KEY, JSON.stringify(entries));
    queueStorageUnavailable = false;
  } catch (_) {
    queueStorageUnavailable = true;
    try { sessionStorage.removeItem(PROMPT_QUEUE_KEY); } catch (_) { /* The queue is still available in this tab. */ }
  }
}

function canQueueDraft() {
  if (!activeChat || activeChatVisibility() !== "active") return false;
  if (generating) return generationSession?.chatId === activeChat && generationSession.accepted;
  const next = promptQueues.get(activeChat)?.items[0];
  return Boolean(next && runtimeStatus === "ready" && next.expected_model_path === loadedModelPath);
}

function canResumeQueue(chatId) {
  const queue = promptQueues.get(chatId);
  const next = queue?.items[0];
  const chat = allChats.find(item => item.id === chatId);
  return Boolean(next && !queue.editing && (chat?.visibility || "active") === "active" && !generating && !stopRequestPending
    && !loadingModel && !unloadingModel && runtimeStatus === "ready" && next.expected_model_path === loadedModelPath);
}

function updateQueueControls() {
  const queue = promptQueues.get(activeChat);
  const canAdd = canQueueDraft();
  $("queuePrompt").hidden = !canAdd;
  $("queuePrompt").disabled = !canAdd || !ui.prompt.value.trim() || queue?.items.length >= MAX_QUEUED_PROMPTS;
  ui.sendButton.classList.toggle("hidden", generating || Boolean(queue?.items.length));
  $("resumePromptQueue").hidden = !queue?.paused;
  $("resumePromptQueue").disabled = !canResumeQueue(activeChat);
  const next = queue?.items[0];
  const status = $("promptQueueStatus");
  const notes = [];
  if (queue?.paused && next) {
    notes.push(text(queue.reason || "promptQueuePaused"));
    if (activeChatVisibility() !== "active") notes.push(text("restoreBeforeSending"));
    else if (next.expected_model_path !== loadedModelPath) notes.push(text("promptQueueLoadModel", { model: readableModelName(next.expected_model_path) }));
    else if (!canResumeQueue(activeChat)) notes.push(text("promptQueueWait"));
  }
  if (queue?.paused && next?.submitted) notes.push(text("promptQueueRetryFirst"));
  if (queue && queueStorageUnavailable) notes.push(text("promptQueueStorageUnavailable"));
  const notice = notes.join(" ");
  status.hidden = !notice;
  if (status.textContent !== notice) status.textContent = notice;
}

function renderPromptQueue(focusIndex = null, focusAction = "removeQueuedPrompt") {
  const chatId = activeChat;
  const queue = promptQueues.get(chatId);
  const sending = generating && generationSession?.chatId === chatId ? generationSession.queuedId : null;
  const items = queue?.items.filter(request => request.request_id !== sending) || [];
  $("promptQueue").hidden = !items.length;
  const title = text("queuedPromptCount", { count: items.length });
  if ($("promptQueueTitle").textContent !== title) $("promptQueueTitle").textContent = title;
  const list = $("promptQueueItems");
  const focusedRequest = document.activeElement?.dataset?.queuedRequestId;
  const focusedAction = document.activeElement?.dataset?.queueAction;
  list.replaceChildren();
  items.forEach((request, index) => {
    const item = document.createElement("li");
    const row = document.createElement("div"); row.className = "prompt-queue-row";
    const preview = document.createElement("p"); preview.textContent = request.message; preview.title = request.message;
    const actions = document.createElement("div"); actions.className = "prompt-queue-actions";
    const controls = [
      ["editQueuedPrompt", text("editLibraryAction"), () => editQueuedPrompt(chatId, request, index), false],
      ["moveQueuedPromptUp", "↑", () => moveQueuedPrompt(chatId, request, items[index - 1]), index === 0 || request.submitted || items[index - 1]?.submitted],
      ["moveQueuedPromptDown", "↓", () => moveQueuedPrompt(chatId, request, items[index + 1]), index === items.length - 1 || request.submitted || items[index + 1]?.submitted],
      ["removeQueuedPrompt", text("remove"), () => removeQueuedPrompt(chatId, request.request_id, index), false],
    ].map(([key, label, action, disabled]) => {
      const button = document.createElement("button"); button.className = "text-button";
      button.textContent = label; button.disabled = Boolean(disabled);
      button.dataset.queuedRequestId = request.request_id;
      button.dataset.queueAction = key;
      button.setAttribute("aria-label", text(key, { number: index + 1 }));
      button.onclick = action;
      actions.appendChild(button);
      return button;
    });
    row.appendChild(preview); row.appendChild(actions); item.appendChild(row); list.appendChild(item);
    if (focusIndex !== null && index === Math.min(focusIndex, items.length - 1)) controls.find(button => button.dataset.queueAction === focusAction)?.focus();
    else if (request.request_id === focusedRequest) (controls.find(button => button.dataset.queueAction === focusedAction && !button.disabled) || controls[0]).focus();
  });
  updateQueueControls();
  if (focusIndex !== null && !items.length) ui.prompt.focus();
}

function moveQueuedPrompt(chatId, request, neighbor) {
  const items = promptQueues.get(chatId)?.items;
  if (!items || !neighbor || request.submitted || neighbor.submitted) return;
  const index = items.indexOf(request), next = items.indexOf(neighbor);
  if (index < 0 || next < 0) return;
  [items[index], items[next]] = [items[next], items[index]];
  savePromptQueues();
  renderPromptQueue();
}

function editQueuedPrompt(chatId, request, index) {
  const queue = promptQueues.get(chatId);
  if (!queue?.items.includes(request) || queue.editing || !enhancements) return;
  queue.editing = true;
  enhancements.editQueuedPrompt(request.message, message => {
    queue.editing = false;
    if (promptQueues.get(chatId) !== queue || !queue.items.includes(request)) return;
    if (message && message !== request.message) {
      const { submitted, ...original } = request;
      queue.items[queue.items.indexOf(request)] = { ...original, message, request_id: crypto.randomUUID() };
      savePromptQueues();
    }
    if (chatId === activeChat) renderPromptQueue(index, "editQueuedPrompt");
    if (!generating) runPromptQueue(chatId);
  });
}

function removeQueuedPrompt(chatId, requestId, focusIndex = null) {
  const queue = promptQueues.get(chatId);
  if (!queue) return;
  queue.items = queue.items.filter(request => request.request_id !== requestId);
  if (!queue.items.length) promptQueues.delete(chatId);
  savePromptQueues();
  if (activeChat === chatId) renderPromptQueue(focusIndex);
}

function pausePromptQueue(chatId, reason = "promptQueuePaused") {
  const queue = promptQueues.get(chatId);
  if (queue) { queue.paused = true; queue.reason = reason; }
  if (activeChat === chatId) updateQueueControls();
}

function queueDraft(message) {
  if (!canQueueDraft()) return;
  enhancements?.finishCompose();
  if (!validateResponseSettings(true)) return;
  const queue = promptQueues.get(activeChat) || { items: [], paused: Boolean(generationSession?.stopRequested), reason: "promptQueueStopped" };
  if (queue.items.length >= MAX_QUEUED_PROMPTS) return showToast(text("promptQueueFull"));
  const modelPath = queue.items[0]?.expected_model_path || loadedModelPath;
  queue.items.push({ message, ...generationValues(), request_id: crypto.randomUUID(), expected_model_path: modelPath });
  promptQueues.set(activeChat, queue);
  savePromptQueues();
  ui.prompt.value = "";
  promptInput(); saveDraft();
  renderPromptQueue();
  ui.prompt.focus();
}

function runPromptQueue(chatId, resume = false) {
  const queue = promptQueues.get(chatId);
  if (!queue || queue.editing || (queue.paused && !resume)) return;
  if (!canResumeQueue(chatId)) { pausePromptQueue(chatId); return; }
  queue.paused = false;
  const request = queue.items[0];
  void send(request.message, null, null, { chatId, request });
}

function runPromptSet(prompts) {
  if (ui.sendButton.disabled || generating || stopRequestPending) return showToast(text("promptSetModelRequired"));
  if (!validateResponseSettings(true)) return;
  const items = prompts.slice(0, MAX_QUEUED_PROMPTS).map(message => ({ message, ...generationValues(),
    request_id: crypto.randomUUID(), expected_model_path: loadedModelPath }));
  if (items.length) return send(items[0].message, null, null, { request: items[0], newChatItems: items });
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

function initContextPreview() {
  const button = $("previewContext");
  const status = $("contextPreviewStatus");
  const list = $("contextPreviewMessages");
  let revision = 0;
  const reset = () => {
    revision += 1;
    status.hidden = list.hidden = true;
    list.replaceChildren();
  };
  ui.contextPopover.addEventListener("beforetoggle", reset);
  button.onclick = async () => {
    reset();
    const current = revision;
    const snapshot = () => JSON.stringify({ chat_id: activeChat, message: ui.prompt.value.trim(),
      expected_model_path: loadedModelPath, ...generationValues() });
    const show = message => { status.textContent = message; status.hidden = false; };
    if (!ui.prompt.value.trim()) return show(text("contextPreviewDraft"));
    if (ui.sendButton.disabled || generating || promptQueues.has(activeChat)) return show(text("contextPreviewWait"));
    if (!validateResponseSettings()) return show(text("invalidResponseSettings"));
    const body = snapshot();
    const navigation = chatNavigation;
    show(text("contextPreviewLoading"));
    try {
      const result = await api("/api/context-preview", { method: "POST", body });
      if (current !== revision) return;
      if (snapshot() !== body || navigation !== chatNavigation || generating) return reset();
      const summary = text("contextPreviewResult", { tokens: result.prompt_tokens.toLocaleString(),
        limit: result.context_size.toLocaleString(), trimmed: result.trimmed_messages });
      show(result.prompt_tokens >= result.context_size ? `${summary} ${text("contextPreviewTooLarge")}` : summary);
      result.messages.forEach(({ role, content }, index) => {
        const details = document.createElement("details");
        const label = document.createElement("summary");
        label.textContent = text(`contextPreview_${index === result.messages.length - 1 ? "draft" : role}`);
        const message = document.createElement("pre");
        message.className = "library-preview";
        message.textContent = content;
        details.append(label, message);
        list.append(details);
      });
      list.hidden = false;
    } catch (error) {
      if (current === revision) show(error.message);
    }
  };
}

function setStatus(status, detail = "") {
  lastStatus = { status, detail };
  const label = typeof detail === "object" ? text(detail.key, detail.values) : detail || text(status);
  ui.statusText.textContent = label;
  ui.statusDot.className = `status-dot ${status === "ready" ? "ready" : ["loading", "unloading", "generating"].includes(status) ? "busy" : status === "error" ? "error" : ""}`;
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
    idleUnloadMinutes = settings.idle_unload_minutes || 0;
    $("idleUnloadMinutes").value = idleUnloadMinutes;
    $("idleUnloadMinutes").disabled = $("saveIdleUnload").disabled = false;
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
      const initialChat = chats.find(chat => chat.id === selected) || (selected === "new" ? null : chats.find(chat => (chat.visibility || "active") === "active"));
      if (initialChat) await openChat(initialChat.id);
      else { activateChat(null); restoreResponseSettings(); }
    } else if (activeChat === null) writeBrowserState(ACTIVE_CHAT_KEY, "new");
    if (
      status.status === "not_loaded"
      && !status.auto_load_suppressed
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
  const target = preferred && !generating && !loadingModel && !unloadingModel && !["loading", "unloading", "generating"].includes(runtimeStatus) ? preferred : current;
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
  unloadedForIdle = Boolean(status.unloaded_for_idle);
  canUnloadModel = status.can_unload ?? Boolean(status.model_path);
  const previousPath = loadedModelPath;
  const previousContext = loadedModelSettings.context_size;
  loadedModelPath = ["error", "not_loaded", "loading", "unloading"].includes(status.status) ? "" : status.model_path || "";
  if (status.loaded_settings && typeof status.loaded_settings === "object") loadedModelSettings = { ...status.loaded_settings };
  else if (settings.model_path === loadedModelPath) loadedModelSettings = { ...settings };
  else if (previousPath !== loadedModelPath) loadedModelSettings = {};
  if (!loadedModelPath) loadedModelSettings = {};
  if (loadedModelSettings.context_size && Number(previousContext) !== Number(loadedModelSettings.context_size)) updateContext();
  loadedThinkingSupport = status.thinking_support || "unknown";
  if (status.status === "error" && status.error && ui.modelLoadError.hidden) showModelError(status.error, true);
  setStatus(status.status, status.error || "");
  if (loadedModelPath) {
    ui.activeModel.textContent = loadedModelPath.split(/[\\/]/).pop();
    const model = availableModels.find(model => model.path === loadedModelPath);
    const size = model ? `${(model.size / 1073741824).toFixed(2)} GiB` : text("custom");
    ui.modelMeta.textContent = text("loadedModelMeta", { size, context: loadedModelSettings.context_size || ui.contextSize.value,
      gpu: loadedModelSettings.gpu_layers ?? ui.gpuLayers.value });
  } else { ui.activeModel.textContent = text("noModel"); ui.modelMeta.textContent = text("selectModel"); }
  if (!generating && !loadingModel && !unloadingModel && ["loading", "unloading", "generating"].includes(runtimeStatus)) {
    runtimeRefreshTimer = setTimeout(refreshRuntimeStatus, 1000);
  } else scheduleIdleRefresh();
  updateResponseActions();
}

function scheduleIdleRefresh() {
  if (idleUnloadMinutes > 0 && runtimeStatus === "ready" && !generating && !loadingModel && !unloadingModel) {
    runtimeRefreshTimer = setTimeout(refreshRuntimeStatus, 5000);
  }
}

async function refreshRuntimeStatus() {
  if (generating || loadingModel || unloadingModel) return;
  const operation = modelOperation;
  try {
    const status = await api("/api/status");
    if (operation === modelOperation && !generating && !loadingModel && !unloadingModel) updateModelStatus(status);
  } catch (_) {
    if (operation === modelOperation && !generating && !loadingModel && !unloadingModel) {
      if (runtimeStatus === "ready" && idleUnloadMinutes > 0) scheduleIdleRefresh();
      else updateModelStatus({ status: "error", error: text("connectionLost") });
    }
  }
}

async function saveIdleUnload() {
  const input = $("idleUnloadMinutes"), button = $("saveIdleUnload"), status = $("idleUnloadStatus");
  if (button.disabled || !input.reportValidity()) return;
  input.disabled = button.disabled = true;
  status.textContent = "";
  try {
    const settings = await api("/api/settings", { method: "PATCH", body: JSON.stringify({ idle_unload_minutes: Number(input.value) }) });
    idleUnloadMinutes = settings.idle_unload_minutes;
    status.textContent = text("idleUnloadSaved");
    if (runtimeStatus === "ready") {
      clearTimeout(runtimeRefreshTimer); runtimeRefreshTimer = null;
      scheduleIdleRefresh();
    }
  } catch (error) { status.textContent = error.message; }
  finally { input.disabled = button.disabled = false; }
}

async function refreshChats() {
  allChats = await api("/api/chats");
  if (ui.chatSearch.value.trim()) queueChatSearch();
  else renderChatList();
}

function activeChatVisibility() {
  return allChats.find(chat => chat.id === activeChat)?.visibility || "active";
}

function updateChatTools() {
  const visibility = activeChatVisibility();
  $("chatTools").hidden = !activeChat;
  $("restoreCurrentChat").hidden = visibility === "active";
  const pin = $("pinCurrentChat");
  const pinned = Boolean(allChats.find(chat => chat.id === activeChat)?.pinned);
  pin.hidden = visibility !== "active";
  pin.dataset.text = pinned ? "unpinChat" : "pinChat";
  pin.textContent = text(pin.dataset.text);
  pin.setAttribute("aria-pressed", String(pinned));
  $("chatVisibilityNotice").hidden = visibility === "active";
  $("chatVisibilityNotice").textContent = visibility === "archived" ? text("archivedChatNotice") : text("trashedChatNotice");
  updateModelPicker();
}

function highlightSearchMatch(parent, value, query) {
  const literal = query.replace(/[.*+?^$(){}|[\]\\]/g, "\\$&");
  const parts = value.split(new RegExp("(" + literal + ")", "gi"));
  parts.forEach((part, index) => {
    if (index % 2 === 0) parent.appendChild(document.createTextNode(part));
    else {
      const match = document.createElement("mark");
      match.textContent = part;
      parent.appendChild(match);
    }
  });
}

function renderUndo() {
  $("chatUndo").hidden = !undoChatMove;
  if (undoChatMove) $("chatUndoLabel").textContent = text(undoChatMove.message);
}

async function moveChat(chat, visibility, canUndo = true) {
  if (!chat || movingChats.has(chat.id)) return;
  const previous = chat.visibility || "active";
  movingChats.add(chat.id);
  try {
    const updated = await api(`/api/chats/${chat.id}/visibility`, { method: "PATCH", body: JSON.stringify({ visibility }) });
    const current = allChats.find(item => item.id === chat.id) || chat;
    Object.assign(current, { visibility: updated.visibility, updated_at: updated.updated_at });
    const from = updated.previous_visibility || previous;
    if (visibility !== "active") pausePromptQueue(chat.id, "promptQueueMoved");
    if (canUndo && visibility !== "active" && from !== visibility) {
      undoChatMove = { chat: current, visibility: from, message: visibility === "archived" ? "chatArchived" : "chatTrashed" };
    } else {
      if (undoChatMove?.chat.id === chat.id) undoChatMove = null;
      showToast(text("chatRestored"));
    }
    renderUndo();
    if (activeChat === chat.id && visibility !== "active") await newChat();
    if (visibility === "active") $("chatFolder").value = "active";
    renderChatList();
    updateResponseActions();
    await refreshChats();
  } catch (error) { showToast(error.message); }
  finally { movingChats.delete(chat.id); }
}

function renderChatList() {
  updateChatTools();
  ui.chatList.innerHTML = "";
  const query = ui.chatSearch.value.trim().toLowerCase();
  const folder = $("chatFolder").value || "active";
  if (chatSearchResults?.query === query && chatSearchResults.folder === folder) {
    for (const match of chatSearchResults.items) {
      const button = document.createElement("button"); button.className = "chat-item chat-search-result";
      const title = document.createElement("strong");
      highlightSearchMatch(title, match.title, ui.chatSearch.value.trim());
      button.appendChild(title);
      if (match.snippet) {
        const snippet = document.createElement("span");
        const author = text(match.role === "user" ? "exportUser" : "exportAssistant");
        snippet.appendChild(document.createTextNode(author + " · "));
        highlightSearchMatch(snippet, match.snippet, ui.chatSearch.value.trim().replace(/\s+/g, " "));
        button.appendChild(snippet);
      }
      button.onclick = () => openChat(match.id, match.message_id, true).catch(error => showToast(error.message));
      ui.chatList.appendChild(button);
    }
    return;
  }
  const chats = allChats.filter(chat => (chat.visibility || "active") === folder && chat.title.toLowerCase().includes(query));
  if (folder === "active") chats.sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));
  if (!chats.length && !query) {
    const empty = document.createElement("p"); empty.className = "chat-group"; empty.textContent = text("noChatsInFolder"); ui.chatList.appendChild(empty);
  }
  let currentGroup = "";
  chats.forEach((chat) => {
    const date = new Date(chat.updated_at);
    const today = new Date();
    const group = folder === "active" && chat.pinned ? "pinnedChats" : date.toDateString() === today.toDateString() ? "today" : "earlier";
    if (group !== currentGroup) {
      const heading = document.createElement("div"); heading.className = "chat-group"; heading.dataset.text = group; heading.textContent = text(group);
      ui.chatList.appendChild(heading); currentGroup = group;
    }
    const row = document.createElement("div"); row.className = "chat-row";
    row.innerHTML = `<button class="chat-item ${chat.id === activeChat ? "active" : ""}">${escapeHtml(chat.title)}</button><details class="chat-menu"><summary data-title="chatActions" aria-label="${escapeHtml(text("chatActions"))}">•••</summary><div><button class="rename-chat">${escapeHtml(text("rename"))}</button><button class="archive-chat">${escapeHtml(text("archiveChat"))}</button><button class="trash-chat">${escapeHtml(text("trashChat"))}</button><button class="restore-chat">${escapeHtml(text("restoreChat"))}</button><button class="delete-chat danger">${escapeHtml(text("deletePermanently"))}</button></div></details>`;
    row.querySelector(".chat-item").onclick = () => openChat(chat.id).catch(error => showToast(error.message));
    row.querySelector(".rename-chat").hidden = folder !== "active";
    row.querySelector(".archive-chat").hidden = folder !== "active";
    row.querySelector(".trash-chat").hidden = folder === "trashed";
    row.querySelector(".restore-chat").hidden = folder === "active";
    row.querySelector(".delete-chat").hidden = folder !== "trashed";
    row.querySelector(".archive-chat").onclick = () => moveChat(chat, "archived");
    row.querySelector(".trash-chat").onclick = () => moveChat(chat, "trashed");
    row.querySelector(".restore-chat").onclick = () => moveChat(chat, "active");
    row.querySelector(".rename-chat").onclick = async () => {
      const title = prompt(text("renameChat"), chat.title);
      if (title?.trim()) { await api(`/api/chats/${chat.id}`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) }); await refreshChats(); }
    };
    row.querySelector(".delete-chat").onclick = async (event) => {
      event.stopPropagation();
      if (generating && chat.id === generationSession?.chatId) return;
      if (!confirm(text("deleteConfirm", { title: chat.title }))) return;
      try { await api(`/api/chats/${chat.id}`, { method: "DELETE" }); }
      catch (error) { showToast(error.message); return; }
      if (promptQueues.delete(chat.id)) { savePromptQueues(); renderPromptQueue(); }
      if (undoChatMove?.chat.id === chat.id) { undoChatMove = null; renderUndo(); }
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

function queueChatSearch() {
  clearTimeout(chatSearchTimer);
  chatSearchRequest?.abort();
  chatSearchResults = null;
  const query = ui.chatSearch.value.trim();
  const folder = $("chatFolder").value || "active";
  const status = $("chatSearchStatus");
  status.hidden = !query;
  status.textContent = query ? text("searchingChats") : "";
  renderChatList();
  if (!query) return;
  const request = chatSearchRequest = new AbortController();
  chatSearchTimer = setTimeout(async () => {
    try {
      const items = await api(`/api/chat-search?q=${encodeURIComponent(query)}&visibility=${encodeURIComponent(folder)}`, { signal: request.signal });
      if (request.signal.aborted) return;
      if (!Array.isArray(items)) throw new Error(text("chatSearchUnavailable"));
      chatSearchResults = { query: query.toLowerCase(), folder, items };
      status.textContent = items.length ? text("chatSearchMatches", { count: items.length }) : text("noChatSearchMatches");
      renderChatList();
    } catch (_) {
      if (!request.signal.aborted) status.textContent = text("chatSearchUnavailable");
    }
  }, 250);
}

async function newChat() {
  if (generating && !generationSession?.assistant && !generationSession?.queuedId) return;
  chatNavigation++;
  closeComposerPopovers();
  // An unsent new chat has its own draft; create its server record on Send.
  activateChat(null); restoreResponseSettings(); resetMessages(); lastUserMessage = ""; conversationChars = ui.systemPrompt.value.length;
  updateContext();
  renderChatList(); if (mobile.matches) setSidebar(false); ui.prompt.focus();
}

async function openChat(id, messageId = null, searchResult = false) {
  if (generating && !generationSession?.assistant && !generationSession?.queuedId) return;
  const navigation = ++chatNavigation;
  closeComposerPopovers();
  saveWorkspaceState();
  const chat = await api(`/api/chats/${id}`);
  if (navigation !== chatNavigation) return;
  displayChat(chat, id);
  await refreshChats();
  if (navigation !== chatNavigation) return;
  if (mobile.matches) setSidebar(false);
  if (messageId) {
    const target = [...ui.messages.querySelectorAll(".message")].find(message => message.messageId === messageId);
    if (target) {
      if (searchResult) target.classList.add("search-jump");
      target.tabIndex = -1;
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "center" });
    }
  }
}

function displayChat(chat, id = chat.id) {
  if (typeof chat.title === "string") {
    const summary = { id, title: chat.title, updated_at: chat.updated_at, visibility: chat.visibility, branch_of: chat.branch_of, pinned: chat.pinned };
    allChats = [summary, ...allChats.filter(item => item.id !== id)];
  }
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
    const node = messageElement(message.type, message.content, message.metrics, message.status, sourcePrompt, message.request, message.error, message.id);
    node.bookmarked = message.bookmarked;
    conversationChars += message.content.length;
  });
  if (generationSession?.chatId === id) generationSession.countedInView = chat.messages.some(message => message.id && message.id === generationSession.assistant?.messageId);
  if (generationSession?.chatId === id && generationSession.assistant
      && !chat.messages.some(message => message.id && message.id === generationSession.assistant.messageId)) {
    if (!chat.messages.some(message => message.type === "user" && message.request?.request_id === generationSession.request.request_id)) {
      messageElement("user", generationSession.request.message, null, "complete", "", null, "", generationSession.request.request_id);
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
  if (loadingModel || unloadingModel || generating || ["loading", "unloading", "generating"].includes(runtimeStatus)) return;
  const modelPath = selectedModelPath();
  if (!modelPath) return setStatus("error", text("selectGGUF"));
  const invalid = [ui.contextSize, ui.gpuLayers, ui.threads].find(field => !field.checkValidity());
  if (invalid) { openSettings(true); invalid.focus(); invalid.reportValidity(); return; }
  showModelError();
  closeComposerPopovers();
  const operation = ++modelOperation;
  loadRequestId = crypto.randomUUID();
  loadingModel = true;
  clearTimeout(runtimeRefreshTimer); runtimeRefreshTimer = null;
  let seconds = 0;
  let loaded = false;
  setStatus("loading", { key: "loadingStatus", values: { seconds } });
  const loadingTimer = setInterval(() => {
    if (operation === modelOperation) setStatus("loading", { key: "loadingStatus", values: { seconds: ++seconds } });
  }, 1000);
  try {
    const loadValues = { model_path: modelPath, context_size: Number(ui.contextSize.value), gpu_layers: Number(ui.gpuLayers.value), threads: Number(ui.threads.value), load_id: loadRequestId };
    const status = await api("/api/model/load", { method: "POST", body: JSON.stringify(loadValues) });
    if (operation !== modelOperation) return;
    loadingModel = false;
    loadRequestId = null;
    updateModelStatus(status, loadValues);
    if (status.load_cancelled || status.status !== "ready") return;
    loaded = true;
    if (ui.customModel.value.trim()) ui.customModel.value = status.model_path;
    else { ui.quickModelSelect.value = status.model_path; ui.modelSelect.value = status.model_path; }
    openSettings(false);
    showToast(text(status.load_settings_saved === false ? "modelLoadSettingsUnsaved" : "modelReady", { model: readableModelName(status.model_path) }));
  } catch (error) {
    if (operation !== modelOperation) return;
    showModelError(error.message, true);
    setStatus("error", error.message);
    showToast(text("modelLoadFailed"));
  } finally {
    clearInterval(loadingTimer);
    if (operation === modelOperation) {
      loadingModel = false; loadRequestId = null;
      try {
        const status = await api("/api/status");
        if (operation === modelOperation) updateModelStatus(status);
      } catch (_) {
        if (operation === modelOperation && !loaded) updateModelStatus({ status: "error", error: text("connectionLost") });
      }
      updateModelPicker();
    }
  }
}

async function unloadModel() {
  if (unloadingModel || generating || runtimeStatus === "generating") return;
  const wasLoading = loadingModel || runtimeStatus === "loading";
  if (!wasLoading && !loadedModelPath && !canUnloadModel) return;
  const requestId = loadRequestId;
  const operation = ++modelOperation;
  loadingModel = false; loadRequestId = null; unloadingModel = true;
  clearTimeout(runtimeRefreshTimer); runtimeRefreshTimer = null;
  runtimeStatus = "unloading";
  showModelError(); setStatus("unloading");
  try {
    const status = await api("/api/model/unload", { method: "POST", body: JSON.stringify({ load_id: requestId }) });
    if (operation !== modelOperation) return;
    unloadingModel = false;
    updateModelStatus(status);
    if (status.status === "not_loaded") showToast(text(wasLoading ? "modelLoadCancelled" : "modelUnloaded"));
  } catch (error) {
    unloadingModel = false;
    showModelError(error.message);
    try { updateModelStatus(await api("/api/status")); }
    catch (_) { updateModelStatus({ status: "error", error: text("connectionLost") }); }
    showToast(text("modelUnloadFailed"));
  } finally {
    unloadingModel = false; updateModelPicker();
    // The load/unload button can become hidden when the operation finishes.
    if (document.activeElement?.hidden) ui.modelPickerButton.focus({ preventScroll: true });
  }
}

function runtimeModelMatchesSelection() {
  return Boolean(loadedModelPath) && loadedModelPath === selectedModelPath();
}

async function send(message, retryRequest = null, branchTarget = null, queued = null) {
  const fromComposer = message === undefined && !queued;
  message ??= ui.prompt.value.trim();
  if (!message) return;
  if (fromComposer && (generating || promptQueues.has(activeChat))) {
    queueDraft(message);
    return;
  }
  if (generating || stopRequestPending) return;
  if (!queued && activeChatVisibility() !== "active") return showToast(text("restoreBeforeSending"));
  if (!queued) enhancements?.finishCompose();
  if (!retryRequest && !queued && !validateResponseSettings(true)) return;
  const submittedDraft = ui.prompt.value;
  let submittedRevision = promptRevision;
  let request = queued?.request || { ...(retryRequest || { message, ...generationValues(), request_id: crypto.randomUUID() }),
    expected_model_path: selectedModelPath() || null };
  // Reserve the UI before the first await so rapid clicks cannot submit twice.
  generating = true;
  const newSet = queued?.newChatItems;
  const session = generationSession = { chatId: queued ? queued.chatId || null : activeChat, assistant: null, request, draftEdited: false,
    accepted: false, queuedId: newSet ? null : queued?.request.request_id, stopRequested: false, finishing: false };
  clearTimeout(runtimeRefreshTimer); runtimeRefreshTimer = null;
  if (!queued || newSet) chatNavigation++;
  saveWorkspaceState();
  if (!queued) closeComposerPopovers();
  ui.quickModelSelect.disabled = true; ui.quickLoadModel.disabled = true; ui.loadModel.disabled = true;
  ui.sendButton.classList.add("hidden"); ui.stopButton.classList.remove("hidden"); ui.stopButton.disabled = true;
  renderPromptQueue();
  updateResponseActions();
  let elapsedSeconds = 0;
  let raw = "", assistant = null, failed = false, completed = false;
  setStatus("generating", { key: "preparing" });
  try {
    const status = await api("/api/status");
    updateModelStatus(status);
    if (!["ready", "stopped"].includes(status.status)) {
      if (!queued || newSet) {
        if (status.status === "generating") showToast(text("generationBusy"));
        else openSettings(true);
      }
      return;
    }
    if (queued ? loadedModelPath !== request.expected_model_path : !runtimeModelMatchesSelection()) {
      if (!queued || newSet) showToast(text("loadSelectedFirst"));
      return;
    }
    if (branchTarget) {
      const branch = await api(`/api/chats/${branchTarget.chatId}/branch`, { method: "POST", body: JSON.stringify({ message_id: branchTarget.messageId,
        ...(branchTarget.editedMessage ? { message: branchTarget.editedMessage } : {}),
        ...(branchTarget.responseSettings ? { response_settings: branchTarget.responseSettings } : {}) }) });
      displayChat(branch);
      session.chatId = branch.id;
      if (branchTarget.editedMessage || branchTarget.responseSettings) request = { ...branch.messages.at(-1).request, expected_model_path: request.expected_model_path };
    }
    if (!session.chatId) {
      const chat = await api("/api/chats", { method: "POST", body: JSON.stringify(request) });
      const revisionBeforeTransfer = promptRevision;
      session.chatId = chat.id;
      if (newSet) {
        session.queuedId = request.request_id;
        promptQueues.set(chat.id, { items: newSet, paused: false });
        savePromptQueues();
      }
      activateChat(chat.id, !newSet); resetMessages(); conversationChars = request.system_prompt.length;
      if (newSet) restoreResponseSettings(request);
      // Moving the same draft from the new-chat slot is not a user edit.
      if (submittedRevision === revisionBeforeTransfer) submittedRevision = promptRevision;
      if (mobile.matches) setSidebar(false);
    }
    const showingChat = activeChat === session.chatId;
    if (showingChat) {
      lastUserMessage = request.message;
      const alreadyShown = queued && [...ui.messages.querySelectorAll(".user")].some(node => node.messageId === request.request_id);
      if (!retryRequest && !alreadyShown) {
        messageElement("user", request.message, null, "complete", "", null, "", request.request_id);
        conversationChars += request.message.length; updateContext();
      }
    }
    assistant = messageElement("assistant", "", null, "streaming", request.message, request, "", "", showingChat);
    session.assistant = assistant; session.request = request;
    updateResponseActions();
    if (showingChat) scrollDown(!queued);
    generationTimer = setInterval(() => {
      elapsedSeconds += 1;
      setStatus("generating", { key: "generatingStatus", values: { seconds: elapsedSeconds } });
      updateAssistant(assistant, raw, null, "streaming", elapsedSeconds);
    }, 1000);
    if (queued) {
      // A lost connection can leave a saved request that must be retried before later prompts.
      request.submitted = true;
      savePromptQueues();
    }
    const { submitted, ...payload } = request;
    const response = await fetch(`/api/chats/${session.chatId}/generate`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
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
    session.accepted = true;
    if (queued) removeQueuedPrompt(session.chatId, session.queuedId);
    updateQueueControls();
    enhancements?.update();
    ui.stopButton.disabled = false;
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = "";
    let terminal = false;
    const consume = line => {
      if (!line.trim() || terminal) return;
      const item = JSON.parse(line);
      if (item.type === "start") {
        if (queued && item.message_id && activeChat === session.chatId) {
          const duplicate = [...ui.messages.querySelectorAll(".assistant")].find(node => node !== assistant && node.messageId === item.message_id);
          if (duplicate) {
            conversationChars -= (duplicate.dataset.raw || "").length;
            duplicate.remove();
            session.countedInView = false;
          }
        }
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
        completed = item.type === "done";
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
    if (queued && activeChat !== session.chatId) showToast(text(promptQueues.has(session.chatId) ? "backgroundQueuePaused" : "responseFailed"));
    else if (!assistant) showToast(text(branchTarget ? "regenerateFailed" : "requestFailed"));
  } finally {
    clearInterval(generationTimer); generationTimer = null;
    if (activeChat === session.chatId && !session.countedInView) conversationChars += raw.length;
    if (session.stopRequested || (!completed && !failed && assistant?.dataset.status === "interrupted")) {
      pausePromptQueue(session.chatId, "promptQueueStopped");
    } else if (!completed || failed) pausePromptQueue(session.chatId, "promptQueueFailed");
    session.finishing = true;
    ui.stopButton.disabled = true;
    // Cleanup failures must not relabel a completed answer or hide a failed one.
    const [statusResult, chatsResult] = await Promise.allSettled([api("/api/status"), refreshChats()]);
    generating = false;
    ui.quickModelSelect.disabled = false; ui.quickLoadModel.disabled = false; ui.loadModel.disabled = false;
    ui.sendButton.classList.remove("hidden"); ui.stopButton.classList.add("hidden"); ui.stopButton.disabled = false;
    renderPromptQueue();
    updateResponseActions();
    if (statusResult.status === "fulfilled") {
      const current = statusResult.value;
      updateModelStatus(current);
      if (failed && !assistant && current.status === "ready") setStatus("ready", { key: "requestFailed" });
    } else {
      setStatus("error", { key: "connectionLost" });
      ui.sendButton.disabled = activeChatVisibility() !== "active" || stopRequestPending;
    }
    if (chatsResult.status === "rejected" && statusResult.status === "fulfilled") showToast(text("chatListUnavailable"));
    if ([ui.sendButton, ui.stopButton, document.body].includes(document.activeElement)) ui.prompt.focus();
    if (completed && !failed && !session.stopRequested) runPromptQueue(session.chatId);
  }
}

async function stop() {
  if (!generating || ui.stopButton.disabled) return;
  const session = generationSession;
  session.stopRequested = true;
  stopRequestPending = true;
  pausePromptQueue(session.chatId, "promptQueueStopped");
  ui.stopButton.disabled = true;
  setStatus("generating", { key: "stopping" });
  try {
    await api("/api/generation/stop", { method: "POST" });
    if (generationSession === session) clearInterval(generationTimer);
  } catch (_) {
    showToast(text("stopFailed"));
  } finally {
    stopRequestPending = false;
    if (generationSession === session) ui.stopButton.disabled = !generating || session.finishing;
    updateModelPicker();
  }
}
function resizePrompt() {
  ui.prompt.style.height = "auto";
  ui.prompt.style.height = enhancements?.isExpanded() ? "" : `${Math.min(ui.prompt.scrollHeight, 180)}px`;
}

ui.settingsButton.onclick = () => openSettings(true); ui.closeSettings.onclick = () => openSettings(false); ui.overlay.onclick = () => { openSettings(false); if (mobile.matches) setSidebar(false); };
$("saveIdleUnload").onclick = saveIdleUnload;
ui.modelSettingsButton.onclick = () => { openSettings(true); ui.modelSelect.focus(); };
ui.menuButton.onclick = () => setSidebar(mobile.matches ? !ui.sidebar.classList.contains("open") : !sidebarExpanded);
ui.sidebarClose.onclick = () => setSidebar(false);
mobile.addEventListener("change", () => setSidebar(mobile.matches ? false : sidebarExpanded));
ui.newChat.onclick = newChat; ui.loadModel.onclick = loadModel; ui.sendButton.onclick = () => send(); ui.stopButton.onclick = stop;
$("queuePrompt").onclick = () => send();
$("resumePromptQueue").onclick = () => runPromptQueue(activeChat, true);
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
ui.quickUnloadModel.onclick = ui.menuUnloadModel.onclick = ui.unloadModel.onclick = unloadModel;
ui.chatSearch.oninput = queueChatSearch;
$("chatFolder").onchange = queueChatSearch;
$("restoreCurrentChat").onclick = () => moveChat(allChats.find(chat => chat.id === activeChat), "active");
$("pinCurrentChat").onclick = async () => {
  const button = $("pinCurrentChat"), chat = allChats.find(item => item.id === activeChat);
  if (button.disabled || !chat || activeChatVisibility() !== "active") return;
  button.disabled = true;
  try {
    const updated = await api(`/api/chats/${chat.id}/pin`, { method: "PATCH", body: JSON.stringify({ value: !chat.pinned }) });
    const current = allChats.find(item => item.id === chat.id);
    if (current) current.pinned = updated.pinned;
    renderChatList();
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; }
};
$("undoChatMove").onclick = () => { if (undoChatMove) moveChat(undoChatMove.chat, undoChatMove.visibility, false); };
$("dismissChatUndo").onclick = () => { undoChatMove = null; renderUndo(); };
ui.quickModelSelect.onchange = () => {
  if (generating || loadingModel || unloadingModel) return;
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
  updateChatTools(); renderUndo();
  renderPromptQueue();
  resizePrompt();
  enhancements?.themeChanged();
});

initComposerPopovers();
initContextPreview();
restorePromptQueues();
initDrafts();
await initThemes();
enhancements = initEnhancements({ api, text, refreshThemeContent, notify: showToast, resize: resizePrompt, send: () => send(),
  getState: () => ({ chatId: activeChat, draft: ui.prompt.value, settings: generationValues(), generating, chatReadOnly: activeChatVisibility() !== "active",
    canQueuePrompt: canQueueDraft(), queuePending: promptQueues.has(activeChat),
    modelBusy: loadingModel || unloadingModel || ["loading", "unloading", "generating"].includes(runtimeStatus), hasModels: Boolean(availableModels.length || selectedModelPath()) }),
  refreshModels,
  selectedModel: () => availableModels.find(model => model.path === selectedModelPath()),
  useCpuSettings(values) {
    ui.contextSize.value = values.context_size;
    ui.gpuLayers.value = values.gpu_layers;
    ui.threads.value = values.threads;
    updateModelPicker(); openSettings(true);
    showToast(text("cpuSettingsPrepared"));
  },
  openChat, refreshChats, getChats: () => allChats, applyAppearance: applyTheme,
  runPromptSet, maxQueuedPrompts: MAX_QUEUED_PROMPTS,
  backupDrafts, restoreBackupDrafts,
  loadDownloaded: async path => { if (await refreshModels(path)) await loadModel(); },
  closePanels: () => { openSettings(false); closeComposerPopovers(); },
  setDraft: value => { ui.prompt.value = value; promptInput(); saveDraft(); ui.prompt.focus(); },
  applyProfile: values => { applySettings(values); responseSettingsChanged(); saveResponseSettings(); },
  validSettings: () => validateResponseSettings(),
  edit: (message, target) => send(message, target.request, { chatId: target.chatId, messageId: target.messageId, editedMessage: message }),
  comparisonModel: () => ui.sendButton.disabled ? "" : readableModelName(loadedModelPath),
  runComparison(target) {
    if (ui.sendButton.disabled) return showToast(text("comparisonUnavailable"));
    if (!validateResponseSettings(true)) return;
    return send(target.request.message, target.request, { chatId: target.chat_id, messageId: target.message_id,
      responseSettings: generationValues() });
  },
  renderComparisonAnswer(element, content) {
    element.innerHTML = markdown(splitThinking(content).answer);
    attachCopyButtons(element);
  },
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
