// Three fixed layouts. JSON supplies values; each matching CSS file owns its structure.
const THEME_IDS = ["chatgpt", "claude", "github-copilot"];
const STORAGE_KEY = "playground.appearance.v1";
const root = document.documentElement;
const themes = new Map();
let current = null;
let appliedVars = [];
let mode = "dark";

const defaults = {
  newChat: "New chat", recentChats: "Recent chats", searchChats: "Search chats…",
  settings: "Settings", settingsTitle: "Settings", settingsHelp: "Appearance, model loading, and response preferences.",
  closeSettings: "Close settings", closeSidebar: "Close sidebar", toggleSidebar: "Toggle sidebar",
  appearance: "Appearance", theme: "Theme", colorMode: "Color mode", light: "Light", dark: "Dark",
  appearanceHelp: "Appearance stays in this browser. Your model and chats stay the same.",
  welcomeTitle: "What are you working on?", welcomeSubtitle: "Chat directly with your local model.",
  promptPlaceholder: "Message {appName}", assistantName: "{appName}",
  footnote: "Local models can make mistakes. Check important information.",
  model: "Model", load: "Load", loaded: "Loaded", loading: "Loading…", loadModel: "Load model",
  noModel: "No model loaded", selectModel: "Select a model to begin", noModels: "No models detected",
  noSystem: "No system prompt", system: "System: {prompt}", temperature: "Temperature {value}",
  send: "Send message", stop: "Stop response", copy: "Copy", copied: "Copied", copiedResponse: "Response copied",
  regenerate: "Regenerate", retry: "Retry", continue: "Continue", rename: "Rename", delete: "Delete",
  responseFailed: "Response failed", responseStopped: "Response stopped",
  responseKept: "Your prompt and any partial answer have been kept.", errorDetails: "Error details",
  retryHelp: "Retry the same prompt and response settings. The previous attempt stays available to copy.",
  retryLatestOnly: "Only the latest response can be retried.",
  streamInterrupted: "The connection ended before the response finished. Retry to recover the answer or start another attempt.",
  requestFailed: "Couldn't send your message. Your draft is still here; try again.",
  responseRequestFailed: "The response request couldn't be accepted. Check the response settings and try again.",
  connectionLost: "Can't reach the local server. Check that it is running, then try again.",
  generationBusy: "A response is already running. Wait for it to finish before trying again.",
  chatListUnavailable: "Couldn't refresh the chat list. Your response is still shown here.",
  stopFailed: "Couldn't stop the response. You can try Stop again.",
  draftUnavailable: "Couldn't save your draft or response preferences in this browser. Keep this tab open or copy your text before refreshing.",
  renameChat: "Rename chat", deleteConfirm: "Delete “{title}”?", chatActions: "Chat actions",
  today: "Today", earlier: "Earlier", working: "Working for {duration}", worked: "Worked for {duration}",
  waitingReasoning: "Waiting for the model's reasoning…", noReasoning: "This model did not return a separate thinking trace.",
  localRuntime: "Local runtime", localBadge: "GGUF", chatLabel: "Chat",
  not_loaded: "Not loaded", ready: "Ready", generating: "Generating", stopped: "Stopped", error: "Error",
  loadingStatus: "Loading model · {seconds}s", generatingStatus: "Generating · {seconds}s",
  preparing: "Preparing prompt…", stopping: "Stopping…", modelReady: "{model} is ready", selectGGUF: "Select a GGUF model",
  modelLoading: "Model loading", requiresReload: "Requires reload", detectedModel: "Detected model", customModel: "Custom GGUF path",
  context: "Context", gpuLayers: "GPU layers", threads: "Threads", conversation: "Conversation", systemPrompt: "System prompt",
  noPrompt: "No prompt", helpfulAssistant: "Helpful assistant", codingAssistant: "Coding assistant", custom: "Custom",
  responseGeneration: "Response generation", nextResponse: "Next response", thinkingMode: "Thinking mode", compatibleModels: "Qwen-compatible models",
  thinkingOn: "On", thinkingOff: "Off", thinkingHelp: "Applies to the next response on compatible models.",
  thinkingShort: "Thinking", options: "Options", optionsCustom: "· Custom", responseOptions: "Response options", modelSettings: "Model loading settings",
  optionsHelp: "System instructions, temperature, and advanced sampling for the next response.",
  modelPickerDescription: "Model: {model}, {state}",
  modelSelection: "Select a model", selectedNotLoaded: "Selected · not loaded", loadSelectedModel: "Load selected model",
  chooseDetectedModel: "Choose a detected model",
  currentlyLoaded: "Currently loaded: {model}", loadSelectedFirst: "Load the selected model before sending.",
  sendWithModel: "Send with {model}", modelLoadFailed: "Couldn't load the selected model. Open model options for details.",
  modelReadySettingsUnsaved: "{model} is ready, but response settings couldn't be saved.",
  thinkingUnavailable: "Unavailable", thinkingUnknown: "Unknown", thinkingNoModel: "Select a model to check Thinking support.",
  thinkingUnsupportedHelp: "This model does not support the Thinking control. Your preference is kept for supported models.",
  thinkingUnknownHelp: "Thinking support is not configured for this model. Your preference is kept for supported models.",
  thinkingSupport_supported: "Thinking control supported", thinkingSupport_unsupported: "Thinking control not supported",
  thinkingSupport_unknown: "Thinking support not configured", thinkingMetric: "Thinking: {state}",
  responseModel: "Model: {model}",
  regenerateBranch: "Regenerate in new chat", regenerateHelp: "Create an alternative from this prompt and its original settings. Keep this chat unchanged.",
  regenerateReopen: "Reopen this chat to regenerate this response.",
  regenerateNotice: "This is an alternative response in a new chat. The original conversation is unchanged.",
  regenerateFailed: "Couldn't create an alternative response. Your original chat and draft are unchanged.",
  continueLatest: "Continue the latest completed response in this chat.",
  responseDetails: "Response details", metricFirstToken: "{value}s first token", metricSpeed: "{value} tok/s",
  metricPrompt: "{value} prompt tokens", metricOutput: "{value} output tokens",
  responseScopeCurrent: "This chat", responseScopeNew: "New chat",
  responseScopeHelp: "Applies to the next response in this chat. Unsent changes stay in this browser.",
  useSavedDefaults: "Use saved defaults", saveDefaults: "Save as defaults", savingDefaults: "Saving defaults…",
  defaultsSaved: "Saved for future new chats. Existing chats keep their settings.",
  defaultsSavedEarlier: "The submitted defaults were saved. Your newer edits apply only to this chat.",
  defaultsSaveFailed: "Couldn't save defaults. This chat keeps your changes; try saving again.",
  invalidResponseSettings: "Fix the highlighted response setting before sending or saving defaults.",
  reload: "Reload", reloadModel: "Reload model", readyReloadPending: "Ready · reload pending",
  modelReloadHelp: "Model, context, GPU, and thread changes apply after Load. Responses use the loaded settings until then.",
  loadedModelMeta: "{size} · {context} context · {gpu} GPU layers",
  modelLoadSettingsUnsaved: "{model} is ready, but its loading settings couldn't be remembered.",
  stopShortcut: "Stop response (Ctrl+Shift+X or Cmd+Shift+X)", keyboardShortcuts: "Keyboard shortcuts",
  newLine: "New line", closePanels: "Close panels",
  contextUsage: "Context usage", contextSummary: "Context {percent}%", contextNearLimit: "Near limit · {percent}%",
  contextExact: "{used} / {limit} tokens · {percent}%", contextApproximate: "Approx. {used} / {limit} tokens · {percent}%",
  contextWarning: "Older turns may be trimmed as the context fills up.",
  temperatureTitle: "Temperature", advancedGeneration: "Advanced generation", repeatPenalty: "Repeat penalty", seed: "Seed", resetDefaults: "Reset generation defaults",
  messageLabel: "Message", systemPreset: "System prompt preset", systemPlaceholder: "Optional—empty means no system prompt", customModelPlaceholder: "D:\\path\\to\\model.gguf"
};
const defaultIcons = { panel: "panel", newChat: "edit", settings: "settings", send: "arrow-up", stop: "stop", close: "close", copy: "copy", regenerate: "retry", continue: "arrow-right", thinking: "brain", options: "sliders", chevronDown: "chevron-down" };
const iconIds = new Set(["panel", "edit", "plus", "settings", "arrow-up", "arrow-right", "stop", "close", "copy", "retry", "send", "brain", "sliders", "chevron-down"]);
const colorVars = ["--bg", "--panel", "--panel-2", "--panel-3", "--text", "--muted", "--line", "--accent", "--on-accent", "--danger", "--warning", "--success", "--code-bg", "--overlay", "--focus"];
const dimensionVars = ["--sidebar-width", "--chat-max-width", "--composer-radius", "--button-radius", "--send-radius", "--control-height", "--text-size", "--line-height", "--shadow", "--font-sans", "--font-conversation", "--font-mono"];
const localAsset = (value) => typeof value === "string" && /^\/assets\/themes\/assets\/[\w./-]+\.svg$/.test(value) && !value.includes("..");

export function text(key, values = {}) {
  const template = current?.text?.[key] ?? defaults[key] ?? key;
  const variables = { appName: current?.branding.appName || "Local LLM Playground", ...values };
  return template.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(variables, name) ? String(variables[name]) : match);
}

function validate(config, id) {
  const require = (condition, field) => { if (!condition) throw new Error(`${id}.json: invalid ${field}`); };
  require(config && typeof config === "object" && config.id === id, "id");
  require(typeof config.displayName === "string" && config.displayName.trim(), "displayName");
  require(config.branding && typeof config.branding.appName === "string" && config.branding.appName.trim(), "branding.appName");
  for (const key of ["logoLight", "logoDark"]) require(localAsset(config.branding[key]), `branding.${key}`);
  for (const [key, value] of Object.entries(config.branding)) require(typeof value === "string", `branding.${key}`);
  require(!config.text || (typeof config.text === "object" && !Array.isArray(config.text)), "text");
  require(!config.icons || (typeof config.icons === "object" && !Array.isArray(config.icons)), "icons");
  const placements = { modelPicker: ["header", "composer"], emptyComposer: ["center", "bottom"], settings: ["dialog", "drawer"] };
  for (const [key, choices] of Object.entries(placements)) require(choices.includes(config.layout?.[key]), `layout.${key}`);
  for (const [key, value] of Object.entries(config.text || {})) require(typeof value === "string", `text.${key}`);
  for (const [key, value] of Object.entries(config.icons || {})) require(Object.hasOwn(defaultIcons, key) && (iconIds.has(value) || localAsset(value)), `icons.${key}`);
  const checkVars = (vars, field, required = false) => {
    require(vars && typeof vars === "object" && !Array.isArray(vars), field);
    if (required) for (const key of colorVars) require(typeof vars[key] === "string", `${field}.${key}`);
    for (const [key, value] of Object.entries(vars)) {
      require([...colorVars, ...dimensionVars].includes(key) && typeof value === "string" && value.trim(), `${field}.${key}`);
      require(!/[;{}]|url\s*\(/i.test(value), `${field}.${key}`);
      if (colorVars.includes(key)) require(CSS.supports("color", value) && !/var\(/i.test(value), `${field}.${key}`);
      else {
        const property = key.startsWith("--font-") ? "font-family" : key === "--shadow" ? "box-shadow" : key === "--line-height" ? "line-height" : "width";
        require(CSS.supports(property, value), `${field}.${key}`);
      }
    }
  };
  checkVars(config.vars || {}, "vars");
  for (const colorMode of ["light", "dark"]) checkVars(config.modes?.[colorMode], `modes.${colorMode}`, true);
  return config;
}

export function refreshThemeContent(container = document) {
  container.querySelectorAll("[data-text]").forEach(node => { node.textContent = text(node.dataset.text); });
  container.querySelectorAll("[data-title]").forEach(node => { node.title = text(node.dataset.title); node.setAttribute("aria-label", text(node.dataset.title)); });
  container.querySelectorAll("[data-label]").forEach(node => { node.setAttribute("aria-label", text(node.dataset.label)); });
  container.querySelectorAll("[data-placeholder]").forEach(node => { node.placeholder = text(node.dataset.placeholder); });
  container.querySelectorAll("[data-brand-name]").forEach(node => { node.textContent = current?.branding.appName || "Local LLM Playground"; });
  container.querySelectorAll("[data-brand-logo]").forEach(node => {
    node.src = current?.branding[mode === "light" ? "logoLight" : "logoDark"] || "/assets/themes/assets/fallback.svg";
    node.alt = ""; // Adjacent text supplies the name.
    node.onerror = () => { node.onerror = null; node.src = "/assets/themes/assets/fallback.svg"; };
  });
  container.querySelectorAll("[data-icon]").forEach(node => {
    const icon = current?.icons?.[node.dataset.icon] || defaultIcons[node.dataset.icon];
    node.replaceChildren();
    if (localAsset(icon)) {
      const img = document.createElement("img"); img.src = icon; img.alt = "";
      img.onerror = () => { img.onerror = null; img.src = "/assets/themes/assets/fallback.svg"; }; node.append(img);
    } else {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("aria-hidden", "true"); svg.setAttribute("viewBox", "0 0 24 24");
      const use = document.createElementNS(svg.namespaceURI, "use");
      use.setAttribute("href", `/assets/themes/assets/icons.svg#${icon || "edit"}`); svg.append(use); node.append(svg);
    }
  });
  const prompt = document.getElementById("prompt");
  if (prompt) prompt.placeholder = text("promptPlaceholder");
  const search = document.getElementById("chatSearch");
  if (search) { search.placeholder = text("searchChats"); search.setAttribute("aria-label", text("searchChats")); }
  const load = document.getElementById("loadModel"); if (load) load.textContent = text("loadModel");
}

export function applyTheme(id, colorMode = mode) {
  const config = themes.get(id);
  if (!config || !["light", "dark"].includes(colorMode)) return false;
  const messages = document.getElementById("messages");
  const anchor = [...messages.querySelectorAll(".message")].find(node => node.getBoundingClientRect().bottom > messages.getBoundingClientRect().top);
  const anchorOffset = anchor ? anchor.getBoundingClientRect().top - messages.getBoundingClientRect().top : 0;
  const atBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 20;
  const focused = document.activeElement;
  const selection = focused?.tagName === "TEXTAREA" ? [focused.selectionStart, focused.selectionEnd] : null;
  current = config; mode = colorMode;
  for (const key of appliedVars) root.style.removeProperty(key);
  const variables = { ...config.vars, ...config.modes[mode] };
  for (const [key, value] of Object.entries(variables)) root.style.setProperty(key, value);
  appliedVars = Object.keys(variables);
  root.style.colorScheme = mode;
  root.dataset.theme = id; root.dataset.colorMode = mode;
  root.dataset.modelPicker = config.layout.modelPicker;
  root.dataset.emptyComposer = config.layout.emptyComposer; root.dataset.settings = config.layout.settings;
  document.title = config.branding.documentTitle || config.branding.appName;
  const picker = document.getElementById("modelPicker");
  const parent = document.getElementById(config.layout.modelPicker === "header" ? "headerModelSlot" : "composerModelSlot");
  if (picker.parentElement !== parent) parent.prepend(picker);
  document.getElementById("themeSelect").value = id;
  document.getElementById("colorModeSelect").value = mode;
  refreshThemeContent();
  document.dispatchEvent(new Event("themechange"));
  if (focused && focused !== document.body && !focused.closest("[inert]")) {
    focused.focus({ preventScroll: true }); if (selection) focused.setSelectionRange(...selection);
  }
  requestAnimationFrame(() => {
    if (atBottom) messages.scrollTop = messages.scrollHeight;
    else if (anchor?.isConnected) messages.scrollTop += anchor.getBoundingClientRect().top - messages.getBoundingClientRect().top - anchorOffset;
  });
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ theme: id, mode })); } catch (_) { /* Private browsing can disable storage. */ }
  return true;
}

export async function initThemes() {
  const errors = [];
  await Promise.all(THEME_IDS.map(async id => {
    try {
      const response = await fetch(`/assets/themes/${id}.json`, { cache: "no-store", signal: AbortSignal.timeout(4000) });
      if (!response.ok) throw new Error(`${id}.json: HTTP ${response.status}`);
      themes.set(id, validate(await response.json(), id));
    } catch (error) { errors.push(error.message); console.warn("Theme unavailable:", error.message); }
  }));
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch (_) { /* Use defaults. */ }
  const select = document.getElementById("themeSelect"); select.replaceChildren();
  for (const id of THEME_IDS) if (themes.has(id)) select.add(new Option(themes.get(id).displayName, id));
  if (!themes.size) { select.add(new Option("Default", "")); select.disabled = true; document.getElementById("colorModeSelect").disabled = true; }
  applyTheme(themes.has(saved.theme) ? saved.theme : themes.has("chatgpt") ? "chatgpt" : select.value, saved.mode === "light" ? "light" : "dark");
  if (!current) refreshThemeContent();
  if (errors.length) {
    const error = document.getElementById("themeError"); error.textContent = errors.join(" · "); error.hidden = false;
  }
  select.onchange = () => applyTheme(select.value);
  document.getElementById("colorModeSelect").onchange = event => applyTheme(select.value, event.target.value);
}
