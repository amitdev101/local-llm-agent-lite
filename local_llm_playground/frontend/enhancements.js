// Shared chat tools. Theme files continue to own appearance, not behavior.
import { initModelDownloads } from "./downloads.js";
export function initEnhancements(hooks) {
  const { text, api, refreshThemeContent } = hooks;
  const $ = id => document.getElementById(id);
  const memory = new Map();
  let presets = { profiles: [], prompts: [] }, libraryTab = "profiles", editing = null, usageChat = null, usageRevision = 0, wasGenerating = false;
  const libraryKey = "playground.library.v1", preferenceKey = "playground.preferences.v1";
  function read(key, fallback) {
    if (memory.has(key)) return memory.get(key);
    try { const value = JSON.parse(localStorage.getItem(key)); return value && typeof value === "object" ? value : fallback; }
    catch (_) { return fallback; }
  }
  function save(key, value) {
    memory.set(key, value);
    try { localStorage.setItem(key, JSON.stringify(value)); memory.delete(key); return true; }
    catch (_) { hooks.notify(text("storageUnavailable")); return false; }
  }
  function dialog(id, title, body, footer = "") {
    const node = document.createElement("dialog"); node.id = id; node.className = "workbench-dialog";
    node.setAttribute("aria-labelledby", id + "Title");
    node.innerHTML = `<header><h2 id="${id}Title" data-text="${title}"></h2><button class="icon-button" data-close data-title="close"><span data-icon="close"></span></button></header>${body}${footer ? `<footer>${footer}</footer>` : ""}`;
    document.body.append(node);
    node.querySelector("[data-close]").onclick = () => node.close();
    refreshThemeContent(node);
    return node;
  }
  function open(node) { hooks.closePanels(); if (!node.open) node.showModal(); }
  function element(tag, content, className = "") {
    const node = document.createElement(tag); node.textContent = content; node.className = className; return node;
  }

  const editor = dialog("composerDialog", "expandComposer", '<p class="setting-help" data-text="expandedHelp"></p><p id="expandedDraftWarning" class="draft-warning" role="status" data-text="draftUnavailable" hidden></p><div id="expandedComposerSlot"></div>', '<button id="composerDone" class="secondary-button" data-text="done"></button><button id="composerSend" class="primary-button" data-text="send"></button>');
  const mirrorDraftWarning = () => { $("expandedDraftWarning").hidden = $("draftWarning").hidden; };
  new MutationObserver(mirrorDraftWarning).observe($("draftWarning"), { attributes: true, attributeFilter: ["hidden"] });
  mirrorDraftWarning();
  const prompt = $("prompt"), promptHome = document.createComment("composer textarea"); prompt.before(promptHome);
  editor.addEventListener("close", () => { promptHome.after(prompt); hooks.resize(); prompt.focus(); });
  $("expandComposer").onclick = () => { open(editor); $("expandedComposerSlot").append(prompt); prompt.style.height = ""; prompt.focus(); };
  $("composerDone").onclick = () => editor.close();
  $("composerSend").onclick = () => { editor.close(); hooks.send(); };

  const editDialog = dialog("editPromptDialog", "editResend", '<p class="setting-help" data-text="editHelp"></p><textarea id="editedPrompt" rows="12" maxlength="100000" required data-label="messageLabel"></textarea><p id="editPromptError" class="model-load-error" role="alert"></p>', '<button id="cancelEdit" class="secondary-button" data-text="cancel"></button><button id="sendEdit" class="primary-button" data-text="editResend"></button>');
  $("cancelEdit").onclick = () => editDialog.close();
  $("editedPrompt").oninput = () => $("editedPrompt").setCustomValidity("");
  $("sendEdit").onclick = () => {
    if (!$("editedPrompt").value.trim()) { $("editedPrompt").setCustomValidity(text("emptyPrompt")); $("editedPrompt").reportValidity(); return; }
    if (hooks.getState().generating) { $("editPromptError").textContent = text("generationBusy"); return; }
    const selected = editing; editDialog.close(); hooks.edit($("editedPrompt").value.trim(), selected);
  };

  const library = dialog("libraryDialog", "library", '<p class="setting-help" data-text="libraryHelp"></p><div class="tool-tabs"><button id="profilesTab" data-text="responseProfiles"></button><button id="promptsTab" data-text="savedPrompts"></button></div><p id="libraryStatus" role="status"></p><form id="saveLibraryForm"><label for="libraryName" data-text="itemName"></label><input id="libraryName" maxlength="80" required autocomplete="off"><button id="saveLibraryItem" class="secondary-button"></button></form><div id="libraryItems" class="library-items"></div>');
  function savedLibrary() {
    const value = read(libraryKey, {});
    const items = kind => (Array.isArray(value[kind]) ? value[kind] : []).filter(item => item && typeof item.id === "string" && typeof item.name === "string"
      && (kind === "prompts" ? typeof item.content === "string" : item.settings && typeof item.settings === "object" && !Array.isArray(item.settings)));
    return { profiles: items("profiles"), prompts: items("prompts") };
  }
  let pendingPrompt = "";
  const draftChoice = dialog("promptChoiceDialog", "usePrompt", '<p data-text="draftExists"></p>', '<button id="cancelPromptChoice" class="secondary-button" data-text="cancel"></button><button id="appendPromptChoice" class="secondary-button" data-text="appendDraft"></button><button id="replacePromptChoice" class="primary-button" data-text="replaceDraft"></button>');
  function usePrompt(content) {
    if (library.open) library.close();
    if (hooks.getState().draft) { pendingPrompt = content; open(draftChoice); }
    else hooks.setDraft(content);
  }
  $("cancelPromptChoice").onclick = () => draftChoice.close();
  $("appendPromptChoice").onclick = () => { hooks.setDraft(hooks.getState().draft + "\n\n" + pendingPrompt); draftChoice.close(); prompt.focus(); };
  $("replacePromptChoice").onclick = () => { hooks.setDraft(pendingPrompt); draftChoice.close(); prompt.focus(); };
  function renderLibrary() {
    const custom = savedLibrary(), items = [...presets[libraryTab], ...custom[libraryTab]];
    $("libraryItems").replaceChildren();
    $("profilesTab").setAttribute("aria-pressed", String(libraryTab === "profiles"));
    $("promptsTab").setAttribute("aria-pressed", String(libraryTab === "prompts"));
    $("saveLibraryItem").textContent = text(libraryTab === "profiles" ? "saveProfile" : "savePrompt");
    if (!items.length) $("libraryItems").append(element("p", text("libraryEmpty"), "setting-help"));
    for (const item of items) {
      if (!item || typeof item.name !== "string" || (libraryTab === "prompts" && typeof item.content !== "string")
          || (libraryTab === "profiles" && (!item.settings || typeof item.settings !== "object"))) continue;
      const row = element("section", "", "library-item"), details = document.createElement("details");
      details.append(element("summary", item.name));
      if (item.description) details.append(element("p", item.description, "setting-help"));
      if (libraryTab === "prompts") details.append(element("pre", item.content, "library-preview"));
      else {
        const preview = element("dl", "", "profile-preview");
        for (const [key, label] of [["system_prompt", "systemPrompt"], ["thinking", "thinkingMode"], ["temperature", "temperatureTitle"],
          ["top_p", "Top-p"], ["top_k", "Top-k"], ["min_p", "Min-p"], ["repeat_penalty", "repeatPenalty"], ["seed", "seed"]]) {
          preview.append(element("dt", text(label)), element("dd", key === "thinking" ? text(item.settings[key] ? "thinkingOn" : "thinkingOff") : String(item.settings[key] ?? "")));
        }
        details.append(preview);
      }
      row.append(details);
      const actions = element("div", "", "settings-actions"), use = element("button", text(libraryTab === "profiles" ? "useProfile" : "usePrompt"), "secondary-button");
      use.onclick = () => {
        if (libraryTab === "prompts") return usePrompt(item.content);
        hooks.applyProfile(item.settings); library.close(); hooks.notify(text("profileApplied"));
      };
      actions.append(use);
      if (custom[libraryTab].some(value => value.id === item.id)) {
        const remove = element("button", text("delete"), "text-button danger");
        remove.setAttribute("aria-label", text("delete") + " " + item.name);
        remove.onclick = () => {
          const latest = savedLibrary(); latest[libraryTab] = latest[libraryTab].filter(value => value.id !== item.id);
          $("libraryStatus").textContent = save(libraryKey, latest) ? "" : text("storageUnavailable"); renderLibrary();
        };
        actions.append(remove);
      }
      row.append(actions); $("libraryItems").append(row);
    }
  }
  $("profilesTab").onclick = () => { libraryTab = "profiles"; renderLibrary(); };
  $("promptsTab").onclick = () => { libraryTab = "prompts"; renderLibrary(); };
  $("libraryButton").onclick = () => { renderLibrary(); open(library); };
  $("saveLibraryForm").onsubmit = event => {
    event.preventDefault(); const name = $("libraryName").value.trim(), state = hooks.getState();
    if (!name) { $("libraryName").setCustomValidity(text("itemName")); return $("libraryName").reportValidity(); }
    const values = savedLibrary();
    if (libraryTab === "prompts" && !state.draft.trim()) { $("libraryStatus").textContent = text("emptyPrompt"); return; }
    if (libraryTab === "profiles" && !hooks.validSettings()) { $("libraryStatus").textContent = text("invalidResponseSettings"); return; }
    if ([...presets[libraryTab], ...values[libraryTab]].some(item => typeof item?.name === "string" && item.name.toLowerCase() === name.toLowerCase())) { $("libraryStatus").textContent = text("nameExists"); return; }
    const value = { id: crypto.randomUUID(), name, ...(libraryTab === "profiles" ? { settings: state.settings } : { content: state.draft }) };
    values[libraryTab].push(value);
    $("libraryStatus").textContent = save(libraryKey, values) ? text("librarySaved") : text("storageUnavailable");
    $("libraryName").value = ""; renderLibrary();
  };
  $("libraryName").oninput = () => $("libraryName").setCustomValidity("");
  fetch("/assets/presets.json", { cache: "no-store" }).then(response => {
    if (!response.ok) throw new Error("presets"); return response.json();
  }).then(value => {
    if (!Array.isArray(value.profiles) || !Array.isArray(value.prompts)) throw new Error("presets");
    presets = value; renderLibrary();
    $("starterPrompts").replaceChildren(...presets.prompts.slice(0, 3).filter(item => typeof item.content === "string").map(item => {
      const button = element("button", item.name, "secondary-button"); button.onclick = () => usePrompt(item.content); return button;
    }));
  }).catch(() => { $("libraryStatus").textContent = text("libraryUnavailable"); renderLibrary(); });

  const preferenceDefaults = { size: "default", spacing: "default", send: "enter" };
  let preferences = { ...preferenceDefaults, ...read(preferenceKey, {}) };
  function applyPreferences() {
    const size = ["default", "large", "larger"].includes(preferences.size) ? preferences.size : "default";
    preferences = { size, spacing: preferences.spacing === "comfortable" ? "comfortable" : "default", send: preferences.send === "modifier" ? "modifier" : "enter" };
    document.documentElement.style.setProperty("--reading-scale", { default: "1", large: "1.15", larger: "1.3" }[size]);
    document.documentElement.dataset.readingSpacing = preferences.spacing === "comfortable" ? "comfortable" : "default";
    $("readingSize").value = size; $("readingSpacing").value = preferences.spacing; $("sendBehavior").value = preferences.send;
    const shortcuts = document.querySelectorAll(".keyboard-help dd kbd");
    if (shortcuts[0]) shortcuts[0].textContent = preferences.send === "modifier" ? "Ctrl / Cmd + Enter" : "Enter";
    if (shortcuts[1]) shortcuts[1].textContent = preferences.send === "modifier" ? "Enter" : "Shift + Enter";
    $("sendButton").setAttribute("aria-keyshortcuts", preferences.send === "modifier" ? "Control+Enter Meta+Enter" : "Enter");
  }
  for (const [id, key] of [["readingSize", "size"], ["readingSpacing", "spacing"], ["sendBehavior", "send"]]) {
    $(id).onchange = () => { preferences[key] = $(id).value; applyPreferences(); save(preferenceKey, preferences); };
  }
  applyPreferences();
  $("resetSampling").onclick = () => hooks.resetSampling();
  for (const [id, key] of [["temperatureNumber", "temperature"], ["topP", "top_p"], ["topK", "top_k"], ["minP", "min_p"], ["repeatPenalty", "repeat_penalty"], ["seed", "seed"]]) {
    const reset = element("button", text("reset"), "parameter-reset text-button"); reset.type = "button";
    const name = { temperature: text("temperatureTitle"), top_p: "Top-p", top_k: "Top-k", min_p: "Min-p", repeat_penalty: text("repeatPenalty"), seed: text("seed") }[key];
    reset.setAttribute("aria-label", text("resetParameter", { name })); reset.title = text("resetParameter", { name });
    reset.onclick = () => hooks.resetSampling(key); $(id).closest("label").append(reset);
  }

  const usage = dialog("usageDialog", "tokenUsage", '<div class="tool-tabs"><button id="usageAll" data-text="allChats"></button><button id="usageCurrent" data-text="thisChat"></button><button id="usageRefresh" data-text="refresh"></button></div><p id="usageStatus" role="status"></p><h3 id="usageScope"></h3><dl id="usageTotals" class="usage-totals"></dl><div class="usage-scroll"><div id="usageRows" role="table" data-label="tokenUsage"></div></div><p class="usage-legend"><span data-text="usageLess"></span><i data-level="1"></i><i data-level="2"></i><i data-level="3"></i><i data-level="4"></i><span data-text="usageMore"></span></p><p class="setting-help" data-text="usageHelp"></p>');
  usage.classList.add("usage-dialog");
  async function loadUsage(chatId = usageChat) {
    usageChat = chatId; const revision = ++usageRevision;
    $("usageStatus").textContent = text("usageLoading"); $("usageRows").replaceChildren(); $("usageTotals").replaceChildren();
    $("usageAll").setAttribute("aria-pressed", String(!chatId));
    $("usageCurrent").setAttribute("aria-pressed", String(Boolean(chatId) && chatId === hooks.getState().chatId));
    $("usageCurrent").disabled = !hooks.getState().chatId;
    try {
      const data = await api("/api/usage" + (chatId ? "?chat_id=" + encodeURIComponent(chatId) : ""));
      if (revision !== usageRevision || !usage.open) return;
      $("usageScope").textContent = chatId ? data.chats[0]?.title || text("thisChat") : text("allChats");
      const total = data.totals;
      for (const [label, count] of [["inputTokens", total.prompt_tokens], ["outputTokens", total.completion_tokens], ["totalTokens", total.prompt_tokens + total.completion_tokens], ["responses", total.responses]]) {
        const block = document.createElement("div"); block.append(element("dt", text(label)), element("dd", Number(count).toLocaleString())); $("usageTotals").append(block);
      }
      const rows = chatId ? data.records : data.chats;
      $("usageStatus").textContent = rows.length ? text(chatId ? "usageIntensityChat" : "usageIntensity") : text("noUsage");
      const header = element("div", "", "usage-row usage-heading"); header.setAttribute("role", "row");
      for (const label of ["usageChat", "inputTokens", "outputTokens", "totalTokens"]) { const cell = element("span", text(label)); cell.setAttribute("role", "columnheader"); header.append(cell); }
      $("usageRows").append(header);
      const maximum = Math.max(1, ...rows.map(row => row.prompt_tokens + row.completion_tokens));
      rows.forEach((row, index) => {
        const line = element("div", "", "usage-row"); line.setAttribute("role", "row");
        const label = element(chatId ? "span" : "button", chatId ? text("responseNumber", { number: index + 1 }) : row.title, "usage-name");
        if (chatId) label.append(element("small", new Date(row.time).toLocaleString() + " · " + row.status));
        else label.onclick = () => loadUsage(row.id);
        const nameCell = document.createElement("div"); nameCell.setAttribute("role", "rowheader"); nameCell.append(label); line.append(nameCell);
        for (const value of [row.prompt_tokens, row.completion_tokens, row.prompt_tokens + row.completion_tokens]) {
          const cell = element("span", "", "usage-cell"); cell.setAttribute("role", "cell");
          cell.append(element("span", value.toLocaleString())); const color = document.createElement("i");
          color.dataset.level = String(value ? Math.max(1, Math.ceil(value / maximum * 4)) : 0); color.setAttribute("aria-hidden", "true");
          cell.append(color); line.append(cell);
        }
        $("usageRows").append(line);
      });
    } catch (_) { if (revision === usageRevision) $("usageStatus").textContent = text("usageUnavailable"); }
  }
  function openUsage(chatId = null) { open(usage); loadUsage(chatId); }
  $("usageButton").onclick = () => openUsage();
  $("usageSidebar").onclick = () => openUsage();
  $("chatUsageButton").onclick = () => openUsage(hooks.getState().chatId);
  $("usageAll").onclick = () => loadUsage(null);
  $("usageCurrent").onclick = () => loadUsage(hooks.getState().chatId);
  $("usageRefresh").onclick = () => loadUsage();
  usage.addEventListener("close", () => { usageRevision++; });

  const downloader = initModelDownloads({ ...hooks, element, dialog, open });
  return {
    isExpanded: () => editor.open, modifierSends: () => preferences.send === "modifier",
    finishCompose() { if (editor.open) editor.close(); },
    editPrompt(target) { editing = target; $("editedPrompt").value = target.message; $("editPromptError").textContent = ""; open(editDialog); $("editedPrompt").focus(); },
    update() {
      const state = hooks.getState(); $("chatUsageButton").disabled = !state.chatId; $("composerSend").disabled = state.generating;
      if (wasGenerating && !state.generating && usage.open) loadUsage();
      wasGenerating = state.generating;
      downloader.update();
    },
    themeChanged() { [editor, editDialog, library, draftChoice, usage].forEach(node => refreshThemeContent(node)); renderLibrary(); if (usage.open) loadUsage(); downloader.themeChanged(); }
  };
}
