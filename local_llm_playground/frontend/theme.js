// Three fixed layouts. JSON supplies values; each matching CSS file owns its structure.
const THEME_IDS = ["chatgpt", "claude", "github-copilot"];
const STORAGE_KEY = "playground.appearance.v1";
const root = document.documentElement;
const themes = new Map();
let current = null;
let appliedVars = [];
let mode = "dark";
let modePreference = "dark";
const systemAppearance = matchMedia("(prefers-color-scheme: dark)");

const defaults = {
  cancelModelLoad: "Cancel load", unloadModel: "Unload model", unloading: "Unloading…",
  modelUnloaded: "Model unloaded. Your chat and draft are still here.", modelLoadCancelled: "Model loading cancelled. Your chat and draft are still here.",
  cancelModelLoadHelp: "Stop loading and release the model's memory. Your chat and draft stay available.",
  unloadModelHelp: "Release the loaded model's memory. Load it again when you want to send a message.",
  unloadAfterResponse: "Stop the response before unloading the model.", modelUnloadFailed: "Couldn't unload the model. Check its status and try again.",
  idleUnload: "Unload after idle (minutes)", idleUnloadHelp: "Free model memory after inactivity. Enter 0 to disable automatic unloading.",
  saveIdleUnload: "Save idle timeout", idleUnloadSaved: "Idle timeout saved.",
  idleModelUnloaded: "Model unloaded after inactivity. Load it to continue.",
  getModels: "Get models", downloads: "Model downloads", getFirstModel: "Get your first model", refreshModels: "Refresh installed models", modelsRefreshed: "Installed model list refreshed.",
  modelGuidance: "Model guidance", availableModelResources: "Available RAM {ram} of {total} · Free model disk space {disk}",
  modelFileSize: "{name} · {size}", modelFitSelect: "Select a detected model in Settings to compare its file size with available RAM.",
  modelLargeForRam: "This file is larger than the currently available RAM. Consider a smaller model for CPU use.",
  modelLargeForDisk: "This file is larger than the available disk space.", modelGuidanceUnavailable: "Available RAM and disk space could not be read.",
  modelMemoryHelp: "File size is a rough CPU memory guide. The runtime and context need extra memory, and GPU layers change where weights are stored.",
  quantizationHelp: "Labels such as Q4 and Q8 describe weight precision. For the same model, lower precision usually gives a smaller file.",
  cpuStarterSummary: "CPU starter settings · {context_size} context · {threads} threads · {gpu_layers} GPU layers",
  useCpuStarter: "Use CPU starter settings", cpuSettingsPrepared: "CPU settings prepared. Load the model to apply them.",
  downloadHelp: "Browse public Hugging Face repositories for complete, single-file GGUF models. File size is disk space, not required RAM. Downloads continue when this panel closes.",
  modelRepository: "Hugging Face repository or URL", browseFiles: "Browse files", checkingModels: "Checking available model files…", filesAtRevision: "{repo} · revision {revision}",
  noModelFiles: "No complete single-file GGUFs were found. Split models and other formats are not listed.", modelCard: "Open model card", saveModelAs: "Save as",
  downloadModel: "Download", useInstalledModel: "Load installed file", filenameExists: "This filename is already on disk. Choose another name to download another copy.",
  renamedThinking: "A renamed file may need its Thinking support configured separately.", catalogUnavailable: "Suggested models could not be loaded. You can still enter a repository.",
  pauseDownload: "Pause", resumeDownload: "Resume", cancelDownload: "Cancel download", noDownloads: "No downloads yet. Choose a model under Get models.",
  downloadDestination: "Models folder: {path}", downloadConnectionLost: "Could not refresh download progress. Check that the playground server is running.", downloadProgress: "Downloading · {percent}%",
  downloadState_downloading: "Downloading", downloadState_verifying: "Checking download…", downloadState_pausing: "Pausing…", downloadState_paused: "Paused",
  downloadState_cancelling: "Cancelling…", downloadState_cancelled: "Cancelled", downloadState_error: "Download interrupted", downloadState_complete: "Ready to load",
  library: "Library", expandComposer: "Expand composer", close: "Close", done: "Done", cancel: "Cancel",
  responseProfiles: "Response profiles", savedPrompts: "Saved prompts", libraryHelp: "Customize a starter item or edit one you've saved. Your library stays in this browser.",
  saveProfile: "Save current response settings", savePrompt: "Save composer text", itemName: "Name", save: "Save",
  useProfile: "Apply profile", usePrompt: "Use prompt", profileApplied: "Profile applied to the next response in this chat.",
  profilePreview: "Profile settings", storageUnavailable: "Could not save the library or preferences. Keep this tab open and copy anything you need.",
  libraryEmpty: "Nothing saved here yet.", loadingLibrary: "Loading library…", libraryUnavailable: "Starter presets could not be loaded. Your saved items are still available.",
  emptyPrompt: "Write something in the composer before saving a prompt.", nameExists: "That name is already saved. Choose a different name.", librarySaved: "Saved in this browser.",
  editLibraryAction: "Edit", editLibraryItem: "Edit {name}", editResponseProfile: "Edit response profile", editSavedPrompt: "Edit saved prompt",
  customizeLibraryAction: "Customize", customizeLibraryItem: "Customize {name}", customizeResponseProfile: "Customize response profile",
  customizeSavedPrompt: "Customize prompt", saveLibraryCopy: "Save copy", libraryCopyName: "{name} (copy)",
  editProfileHelp: "Use Apply profile in the library to apply these settings to a chat.",
  savedPromptText: "Prompt text", saveLibraryChanges: "Save changes", libraryNameRequired: "Enter a name.", libraryPromptRequired: "Enter prompt text.",
  libraryItemChanged: "This item changed or was removed. Reopen the library to see the latest version.",
  draftExists: "A draft is already here. Choose how to use this prompt.", replaceDraft: "Replace draft", appendDraft: "Add to draft",
  editPrompt: "Edit prompt", editResend: "Edit and resend", editHelp: "Create an alternative chat with this edited prompt. The original conversation stays available.",
  expandedHelp: "Enter adds a line. Ctrl/Cmd+Enter sends. Edits are saved with this chat's draft.",
  comfort: "Reading and composing", textSize: "Text size", spacing: "Message spacing", defaultSize: "Theme default",
  largeSize: "Large", largerSize: "Extra large", comfortable: "Comfortable", systemAppearance: "Follow system",
  sendBehavior: "Send shortcut", enterSends: "Enter sends · Shift+Enter adds a line", modifierSends: "Ctrl/Cmd+Enter sends · Enter adds a line",
  temperatureHelp: "Lower values usually give more focused responses; higher values add variation. This does not guarantee accuracy.",
  samplingHelp: "Top-p and min-p filter unlikely choices; top-k limits how many choices remain. Repeat penalty discourages repetition. Seed -1 allows a fresh random seed.",
  resetSampling: "Reset sampling to saved defaults", runningElsewhere: "A response is running in another chat.", returnResponse: "View response",
  reset: "Reset", resetParameter: "Reset {name} to its saved default",
  tokenUsage: "Token usage", chatTokenUsage: "Token usage for this chat", allChats: "All chats", thisChat: "This chat",
  usageHelp: "Available tokenizer estimates include input context on each attempt. Stopped and failed attempts count when metrics are available; recovered answers and copied branch history count once. Usage remains after deleting a chat.",
  inputTokens: "Input tokens", outputTokens: "Output tokens", totalTokens: "Total tokens", responses: "Responses",
  noUsage: "No recorded token usage yet.", usageUnavailable: "Could not load token usage. Try again.", refresh: "Refresh", back: "All chats",
  usageChat: "Chat / response", usageLess: "Less", usageMore: "More", responseNumber: "Response {number}", usageLoading: "Loading token usage…",
  usageIntensity: "Color intensity compares token counts within this view. Select a chat to see its responses.",
  usageIntensityChat: "Color intensity compares token counts within this chat.",
  newChat: "New chat", recentChats: "Recent chats", searchChats: "Search chats and messages…",
  searchingChats: "Searching messages…", chatSearchMatches: "Matches shown: {count}", noChatSearchMatches: "No matching chats or messages.",
  chatSearchUnavailable: "Message search is unavailable. Showing title matches.",
  chatFolder: "Chat folder", archivedChats: "Archived chats", trash: "Trash", archiveChat: "Archive", trashChat: "Move to Trash",
  restoreChat: "Restore chat", deletePermanently: "Delete permanently", undo: "Undo", dismiss: "Dismiss",
  chatArchived: "Chat archived.", chatTrashed: "Chat moved to Trash.", chatRestored: "Chat restored.",
  restoreBeforeSending: "Restore this chat to send a message.", archivedChatNotice: "Archived chat", trashedChatNotice: "Chat in Trash",
  noChatsInFolder: "No chats in this folder.", chatBranches: "Branches", exportChat: "Export Markdown",
  conversationBranches: "Conversation branches", branchOriginal: "Original chat", branchAlternative: "Alternative",
  compareResponse: "Compare responses", comparisonLoading: "Loading saved responses…",
  comparisonHelp: "Load the model you want and set its response options before running an alternative. The original answer stays in its chat.",
  comparisonSamePrompt: "Shared prompt", comparisonOriginal: "Original response", comparisonAlternative: "Alternative response",
  comparisonEmpty: "Run an alternative to compare answers and performance.",
  comparisonUnknownModel: "Model unavailable",
  attachText: "Attach text or code", attachTextFile: "Choose a text or code file",
  attachTextHelp: "Preview a UTF-8 text or code file up to 64 KiB. Adding it puts the text in your draft so you can edit or remove it.",
  attachTextEstimate: "{name} · Roughly {tokens} input tokens",
  attachTextTooLarge: "Choose a file of 64 KiB or less.",
  attachTextInvalid: "Choose a nonempty UTF-8 text or code file.",
  attachTextAdd: "Add file to draft",
  promptVariables: "Fill in prompt fields", promptVariableHelp: "Use placeholders such as {{language}} in a saved prompt. You’ll fill them in when you use it.",
  promptVariableEmpty: "Enter a value for this field.",
  promptSets: "Prompt sets", createPromptSet: "Create prompt set", editPromptSet: "Edit prompt set",
  promptSetHelp: "Enter prompts in the order you want them sent. You can use fields such as {{topic}}.",
  promptSetRunHelp: "Run a set in a new chat using the loaded model and current response options.",
  promptSetNumber: "Prompt {number}", addSetPrompt: "Add prompt", runPromptSet: "Run in new chat",
  promptSetModelRequired: "Load the selected model and wait for the current response before running a set.",
  comparisonUnavailable: "Load a model and open an active chat to run an alternative.",
  comparisonDetails: "{model} · Temperature {temperature} · {seconds}s · {speed} tok/s · {input} input / {output} output tokens (estimated)",
  runComparison: "Run with {model} and current options",
  branchSourceResponse: "Source response in {title}", branchSourceUnavailable: "The source chat is unavailable.",
  noRelatedBranches: "No other branches are available.", openChat: "Open chat", openNamedChat: "Open {title}",
  pinChat: "Pin chat", unpinChat: "Unpin chat", pinnedChats: "Pinned",
  bookmarkResponse: "Bookmark", removeBookmark: "Remove bookmark",
  bookmarks: "Bookmarked responses", noBookmarks: "Bookmark a completed answer to find it here.", loadingBookmarks: "Loading bookmarks…",
  exportUser: "User", exportAssistant: "Assistant",
  chatBackups: "Chat backups", backupHelp: "Download a local backup of chats, prompts, response profiles, reading preferences, and appearance. Restore adds missing chats and library items.",
  downloadBackup: "Download backup", selectBackup: "Choose a backup file", restoreBackup: "Restore backup", restorePreferences: "Restore reading and appearance preferences",
  preparingBackup: "Preparing backup…", backupDownloaded: "Backup download started.", restoringBackup: "Restoring backup…",
  backupTooLarge: "Choose a backup smaller than 50 MiB.", invalidBackup: "Choose a valid MyLLM Playground backup file.",
  backupPreview: "This backup contains {chats} chats and {items} library items.",
  backupRestored: "Restored {restored} chats and {items} library items. Skipped {skipped} existing chats.",
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
  queuePrompt: "Queue prompt", queuedPromptCount: "Queued prompts ({count})", remove: "Remove", removeQueuedPrompt: "Remove queued prompt {number}",
  editQueueTitle: "Edit queued prompt", editQueuedPrompt: "Edit queued prompt {number}", saveQueuedPrompt: "Save queued prompt",
  moveQueuedPromptUp: "Move queued prompt {number} up", moveQueuedPromptDown: "Move queued prompt {number} down",
  resumePromptQueue: "Resume queue", promptQueuePaused: "Queue paused. Resume when you're ready.",
  promptQueueStopped: "Queue paused because the response stopped.",
  promptQueueFailed: "Queue paused because the response could not finish.",
  promptQueueRefreshed: "Queue paused after reopening this tab.",
  promptQueueMoved: "Queue paused when this chat was moved.",
  promptQueueWait: "Wait for the current model operation to finish.",
  promptQueueRetryFirst: "Resume or remove the first prompt before moving it. The server may have received it.",
  promptQueueLoadModel: "Load {model} to resume this queue.", promptQueueFull: "Remove a queued prompt before adding another.",
  promptQueueStorageUnavailable: "The queue could not be saved for refresh. Keep this tab open.",
  backgroundQueuePaused: "The response failed in another chat. Its queue is paused.",
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
  renameChat: "Rename chat", deleteConfirm: "Permanently delete “{title}”?", chatActions: "Chat actions",
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
  loadRecoveryMemory: "Reduce the context size and reload. If the model still cannot fit, try a smaller GGUF.",
  loadRecoveryGpu: "Try loading on the CPU if the selected GPU configuration fails.",
  loadRecoveryFile: "Check the path and confirm the file is a complete GGUF. Choose another model or download a fresh copy.",
  loadRecoveryOther: "Review the file path and loading settings before trying again.",
  reduceLoadContext: "Prepare smaller context", loadOnCpu: "Prepare CPU loading",
  smallerContextPrepared: "Smaller context prepared. Select Load model to try again.",
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
  previewContext: "Preview draft context", contextPreviewDraft: "Write a draft to preview what the model will receive.",
  contextPreviewWait: "Preview is available when the model is ready and the queue is empty.",
  contextPreviewLoading: "Estimating draft context…",
  contextPreviewResult: "Estimated input: {tokens} of {limit} tokens. {trimmed} older messages omitted.",
  contextPreviewTooLarge: "Shorten your draft or system prompt, or load the model with more context.",
  contextPreview_system: "System instructions", contextPreview_user: "Your message",
  contextPreview_assistant: "Assistant response", contextPreview_draft: "Draft",
  temperatureTitle: "Temperature", advancedGeneration: "Advanced generation", repeatPenalty: "Repeat penalty", seed: "Seed", resetDefaults: "Reset generation defaults",
  messageLabel: "Message", systemPreset: "System prompt preset", systemPlaceholder: "Optional—empty means no system prompt", customModelPlaceholder: "D:\\path\\to\\model.gguf"
};
const defaultIcons = { panel: "panel", newChat: "edit", settings: "settings", send: "arrow-up", stop: "stop", close: "close", copy: "copy", regenerate: "retry", continue: "arrow-right", thinking: "brain", options: "sliders", chevronDown: "chevron-down", expand: "expand", library: "book", attachment: "paperclip" };
const iconIds = new Set(Object.values(defaultIcons).concat("plus", "send"));
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

export function applyTheme(id, colorMode = modePreference) {
  const config = themes.get(id);
  if (!config || !["light", "dark", "system"].includes(colorMode)) return false;
  const messages = document.getElementById("messages");
  const anchor = [...messages.querySelectorAll(".message")].find(node => node.getBoundingClientRect().bottom > messages.getBoundingClientRect().top);
  const anchorOffset = anchor ? anchor.getBoundingClientRect().top - messages.getBoundingClientRect().top : 0;
  const atBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 20;
  const focused = document.activeElement;
  const selection = focused?.tagName === "TEXTAREA" ? [focused.selectionStart, focused.selectionEnd] : null;
  current = config; modePreference = colorMode;
  mode = colorMode === "system" ? (systemAppearance.matches ? "dark" : "light") : colorMode;
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
  document.getElementById("colorModeSelect").value = modePreference;
  refreshThemeContent();
  document.dispatchEvent(new Event("themechange"));
  if (focused && focused !== document.body && !focused.closest("[inert]")) {
    focused.focus({ preventScroll: true }); if (selection) focused.setSelectionRange(...selection);
  }
  requestAnimationFrame(() => {
    if (atBottom) messages.scrollTop = messages.scrollHeight;
    else if (anchor?.isConnected) messages.scrollTop += anchor.getBoundingClientRect().top - messages.getBoundingClientRect().top - anchorOffset;
  });
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ theme: id, mode: modePreference })); } catch (_) { /* Private browsing can disable storage. */ }
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
  applyTheme(themes.has(saved.theme) ? saved.theme : themes.has("chatgpt") ? "chatgpt" : select.value, ["light", "system"].includes(saved.mode) ? saved.mode : "dark");
  if (!current) refreshThemeContent();
  if (errors.length) {
    const error = document.getElementById("themeError"); error.textContent = errors.join(" · "); error.hidden = false;
  }
  select.onchange = () => applyTheme(select.value);
  document.getElementById("colorModeSelect").onchange = event => applyTheme(select.value, event.target.value);
  systemAppearance.addEventListener("change", () => { if (modePreference === "system" && current) applyTheme(current.id, "system"); });
}
