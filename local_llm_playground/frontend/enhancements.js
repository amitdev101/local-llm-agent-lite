// Shared chat tools. Theme files continue to own appearance, not behavior.
import { initModelDownloads } from "./downloads.js?v=2";
import { initConversationTools } from "./conversation-tools.js?v=3";
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

  const attachment = dialog("attachTextDialog", "attachText", `
    <p class="setting-help" data-text="attachTextHelp"></p>
    <label for="attachTextFile" data-text="attachTextFile"></label><input id="attachTextFile" type="file">
    <p id="attachTextStatus" class="setting-help" role="status"></p>
    <pre id="attachTextPreview" class="library-preview" hidden></pre>`,
    '<button id="addTextAttachment" class="primary-button" data-text="attachTextAdd" disabled></button>');
  const attachmentFile = $("attachTextFile"), attachmentPreview = $("attachTextPreview");
  const attachmentStatus = $("attachTextStatus"), attachmentAdd = $("addTextAttachment");
  let attachmentRevision = 0, attachmentBlock = "";
  function resetAttachment() {
    attachmentRevision += 1;
    attachmentBlock = attachmentPreview.textContent = attachmentStatus.textContent = "";
    attachmentPreview.hidden = attachmentAdd.disabled = true;
  }
  $("attachTextButton").onclick = () => { attachmentFile.value = ""; resetAttachment(); open(attachment); attachmentFile.focus(); };
  attachmentFile.onchange = async () => {
    resetAttachment();
    const revision = attachmentRevision, file = attachmentFile.files[0];
    if (!file) return;
    if (file.size > 64 * 1024) { attachmentStatus.textContent = text("attachTextTooLarge"); return; }
    try {
      const contents = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      if (revision !== attachmentRevision || !attachment.open) return;
      if (!contents.trim() || /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(contents)) throw new Error();
      const longest = (contents.match(/^ {0,3}`+/gm) || []).reduce((length, run) => Math.max(length, run.trimStart().length), 2);
      const fence = "`".repeat(longest + 1);
      const name = file.name.replace(/[\r\n]/g, " ");
      attachmentBlock = `${name}\n${fence}\n${contents}\n${fence}`;
      attachmentStatus.textContent = text("attachTextEstimate", { name, tokens: Math.ceil(attachmentBlock.length / 4).toLocaleString() });
      attachmentPreview.textContent = contents;
      attachmentPreview.hidden = attachmentAdd.disabled = false;
    } catch (_) {
      if (revision === attachmentRevision && attachment.open) attachmentStatus.textContent = text("attachTextInvalid");
    }
  };
  attachmentAdd.onclick = () => {
    const { draft, chatReadOnly } = hooks.getState();
    if (!attachmentBlock || chatReadOnly) return;
    const block = attachmentBlock;
    resetAttachment();
    attachment.close();
    hooks.setDraft(draft ? `${draft}\n\n${block}` : block);
  };
  attachment.addEventListener("close", resetAttachment);

  const editDialog = dialog("editPromptDialog", "editResend", '<p class="setting-help" data-text="editHelp"></p><textarea id="editedPrompt" rows="12" maxlength="100000" required data-label="messageLabel"></textarea><p id="editPromptError" class="model-load-error" role="alert"></p>', '<button id="cancelEdit" class="secondary-button" data-text="cancel"></button><button id="sendEdit" class="primary-button" data-text="editResend"></button>');
  $("cancelEdit").onclick = () => editDialog.close();
  $("editedPrompt").oninput = () => $("editedPrompt").setCustomValidity("");
  $("sendEdit").onclick = () => {
    if (!$("editedPrompt").value.trim()) { $("editedPrompt").setCustomValidity(text("emptyPrompt")); $("editedPrompt").reportValidity(); return; }
    if (hooks.getState().generating) { $("editPromptError").textContent = text("generationBusy"); return; }
    const selected = editing; editDialog.close(); hooks.edit($("editedPrompt").value.trim(), selected);
  };

  const library = dialog("libraryDialog", "library", '<p class="setting-help" data-text="libraryHelp"></p><div class="tool-tabs"><button id="profilesTab" data-text="responseProfiles"></button><button id="promptsTab" data-text="savedPrompts"></button></div><p id="libraryStatus" role="status"></p><form id="saveLibraryForm"><label for="libraryName" data-text="itemName"></label><input id="libraryName" maxlength="80" required autocomplete="off"><button id="saveLibraryItem" class="secondary-button"></button></form><div id="libraryItems" class="library-items"></div>');
  const samplingFields = [
    ["temperatureNumber", "temperature", "temperatureTitle"],
    ["topP", "top_p", "Top-p"], ["topK", "top_k", "Top-k"], ["minP", "min_p", "Min-p"],
    ["repeatPenalty", "repeat_penalty", "repeatPenalty"], ["seed", "seed", "seed"],
  ];
  const libraryEditor = dialog("libraryEditor", "editSavedPrompt", `
    <form id="editLibraryForm">
      <label for="libraryEditName" data-text="itemName"></label>
      <input id="libraryEditName" maxlength="80" required autocomplete="off">
      <div id="libraryPromptFields">
        <label for="libraryEditContent" data-text="savedPromptText"></label>
        <textarea id="libraryEditContent" required></textarea>
        <p class="setting-help" data-text="promptVariableHelp"></p>
      </div>
      <fieldset id="libraryProfileFields" class="library-profile-fields">
        <p class="setting-help" data-text="editProfileHelp"></p>
        <label for="libraryEditSystem" data-text="systemPrompt"></label>
        <textarea id="libraryEditSystem" rows="4" data-placeholder="systemPlaceholder"></textarea>
        <label class="library-thinking-field"><input id="libraryEditThinking" type="checkbox"><span data-text="thinkingMode"></span></label>
        <div id="librarySamplingFields" class="grid-two"></div>
      </fieldset>
      <p id="libraryEditStatus" class="setting-help" role="status"></p>
    </form>`, `
    <button id="cancelLibraryEdit" type="button" class="secondary-button" data-text="cancel"></button>
    <button id="saveLibraryEdit" type="submit" form="editLibraryForm" class="primary-button" data-text="saveLibraryChanges"></button>`);
  const profileInputs = samplingFields.map(([id, key, label]) => {
    const field = $(id).cloneNode(false);
    field.removeAttribute("id");
    const caption = element("span", text(label));
    caption.dataset.text = label;
    const group = document.createElement("label");
    group.append(caption, field);
    $("librarySamplingFields").append(group);
    return [key, field];
  });
  let libraryEditTarget = null;

  function savedLibrary(value = read(libraryKey, {})) {
    const items = kind => (Array.isArray(value[kind]) ? value[kind] : []).filter(item => item && typeof item.id === "string" && typeof item.name === "string"
      && (kind === "prompts" ? typeof item.content === "string" : item.settings && typeof item.settings === "object" && !Array.isArray(item.settings)));
    return { profiles: items("profiles"), prompts: items("prompts") };
  }
  function libraryNameExists(kind, items, name, excludedId) {
    const normalized = name.toLowerCase();
    const matches = item => typeof item?.name === "string" && item.name.toLowerCase() === normalized;
    return presets[kind].some(matches) || items.some(item => item.id !== excludedId && matches(item));
  }
  function editLibraryItem(kind, item, copy) {
    libraryEditTarget = { kind, item, copy };
    const isProfile = kind === "profiles";
    const title = copy ? (isProfile ? "customizeResponseProfile" : "customizeSavedPrompt") : (isProfile ? "editResponseProfile" : "editSavedPrompt");
    $("libraryEditorTitle").dataset.text = title;
    $("saveLibraryEdit").dataset.text = copy ? "saveLibraryCopy" : "saveLibraryChanges";
    const nameInput = $("libraryEditName");
    nameInput.value = copy ? text("libraryCopyName", { name: item.name }).slice(0, nameInput.maxLength) : item.name;
    $("libraryPromptFields").hidden = $("libraryEditContent").disabled = isProfile;
    $("libraryProfileFields").hidden = $("libraryProfileFields").disabled = !isProfile;
    if (isProfile) {
      $("libraryEditSystem").value = item.settings.system_prompt ?? "";
      $("libraryEditThinking").checked = Boolean(item.settings.thinking);
      for (const [key, input] of profileInputs) input.value = item.settings[key] ?? input.defaultValue;
    } else {
      $("libraryEditContent").value = item.content;
    }
    $("libraryEditStatus").textContent = "";
    libraryEditor.scrollTop = 0;
    refreshThemeContent(libraryEditor);
    open(libraryEditor);
    nameInput.focus();
    nameInput.select();
  }
  $("cancelLibraryEdit").onclick = () => libraryEditor.close();
  $("editLibraryForm").oninput = () => { $("libraryEditStatus").textContent = ""; };
  $("editLibraryForm").onsubmit = event => {
    event.preventDefault();
    const { kind, item, copy } = libraryEditTarget;
    const name = $("libraryEditName").value.trim();
    const content = $("libraryEditContent").value;
    const status = $("libraryEditStatus");
    if (!name) { status.textContent = text("libraryNameRequired"); $("libraryEditName").focus(); return; }
    if (kind === "prompts" && !content.trim()) { status.textContent = text("libraryPromptRequired"); $("libraryEditContent").focus(); return; }
    const values = savedLibrary();
    const index = copy ? -1 : values[kind].findIndex(entry => entry.id === item.id);
    if (!copy && (index === -1 || JSON.stringify(values[kind][index]) !== JSON.stringify(item))) {
      status.textContent = text("libraryItemChanged");
      return;
    }
    if (libraryNameExists(kind, values[kind], name, copy ? undefined : item.id)) {
      status.textContent = text("nameExists"); $("libraryEditName").focus(); return;
    }
    const updated = copy ? { id: crypto.randomUUID(), name } : { ...item, name };
    if (kind === "profiles") {
      updated.settings = { ...item.settings, system_prompt: $("libraryEditSystem").value, thinking: $("libraryEditThinking").checked };
      for (const [key, input] of profileInputs) updated.settings[key] = input.valueAsNumber;
    } else {
      updated.content = content;
    }
    if (copy) values[kind].push(updated);
    else values[kind][index] = updated;
    $("libraryStatus").textContent = save(libraryKey, values) ? text("librarySaved") : text("storageUnavailable");
    libraryEditor.close();
    renderLibrary(updated.id);
  };
  let pendingPrompt = "";
  const draftChoice = dialog("promptChoiceDialog", "usePrompt", '<p data-text="draftExists"></p>', '<button id="cancelPromptChoice" class="secondary-button" data-text="cancel"></button><button id="appendPromptChoice" class="secondary-button" data-text="appendDraft"></button><button id="replacePromptChoice" class="primary-button" data-text="replaceDraft"></button>');
  const variables = dialog("promptVariablesDialog", "promptVariables", '<form id="promptVariablesForm"></form>',
    '<button class="primary-button" type="submit" form="promptVariablesForm" data-text="usePrompt"></button>');
  const variablePattern = /\{\{\s*([A-Za-z_][\w-]{0,39})\s*\}\}/g;
  function fillPromptVariables(templates, apply) {
    const names = new Set(templates.flatMap(template => [...template.matchAll(variablePattern)].map(match => match[1])));
    if (!names.size) return apply(templates);
    const form = $("promptVariablesForm");
    const fields = new Map();
    form.replaceChildren();
    for (const name of names) {
      const label = element("label", name);
      const input = document.createElement("input");
      input.required = true;
      input.autocomplete = "off";
      input.oninput = () => input.setCustomValidity("");
      label.append(input);
      form.append(label);
      fields.set(name, input);
    }
    form.onsubmit = event => {
      event.preventDefault();
      const empty = [...fields.values()].find(input => !input.value.trim());
      if (empty) { empty.setCustomValidity(text("promptVariableEmpty")); empty.reportValidity(); return; }
      const resolved = templates.map(template => template.replace(variablePattern, (_, name) => fields.get(name).value));
      variables.close();
      apply(resolved);
    };
    open(variables);
    fields.values().next().value.focus();
  }
  function insertPrompt(content) {
    if (hooks.getState().draft) { pendingPrompt = content; open(draftChoice); }
    else hooks.setDraft(content);
  }
  function usePrompt(content) {
    if (library.open) library.close();
    fillPromptVariables([content], ([resolved]) => insertPrompt(resolved));
  }
  $("cancelPromptChoice").onclick = () => draftChoice.close();
  $("appendPromptChoice").onclick = () => { hooks.setDraft(hooks.getState().draft + "\n\n" + pendingPrompt); draftChoice.close(); prompt.focus(); };
  $("replacePromptChoice").onclick = () => { hooks.setDraft(pendingPrompt); draftChoice.close(); prompt.focus(); };
  function renderLibrary(focusId) {
    const custom = savedLibrary(), items = [...custom[libraryTab], ...presets[libraryTab]];
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
        for (const [key, label] of [["system_prompt", "systemPrompt"], ["thinking", "thinkingMode"], ...samplingFields.map(([, key, label]) => [key, label])]) {
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
      const isCustom = custom[libraryTab].includes(item);
      const edit = element("button", text(isCustom ? "editLibraryAction" : "customizeLibraryAction"), "secondary-button");
      edit.setAttribute("aria-label", text(isCustom ? "editLibraryItem" : "customizeLibraryItem", { name: item.name }));
      edit.onclick = () => editLibraryItem(libraryTab, item, !isCustom);
      actions.append(use, edit);
      if (isCustom) {
        const remove = element("button", text("delete"), "text-button danger");
        remove.setAttribute("aria-label", text("delete") + " " + item.name);
        remove.onclick = () => {
          const latest = savedLibrary(); latest[libraryTab] = latest[libraryTab].filter(value => value.id !== item.id);
          $("libraryStatus").textContent = save(libraryKey, latest) ? "" : text("storageUnavailable"); renderLibrary();
        };
        actions.append(remove);
      }
      row.append(actions); $("libraryItems").append(row);
      if (isCustom && item.id === focusId) edit.focus();
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
    if (libraryNameExists(libraryTab, values[libraryTab], name)) { $("libraryStatus").textContent = text("nameExists"); return; }
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
  for (const [id, key, label] of samplingFields) {
    const reset = element("button", text("reset"), "parameter-reset text-button"); reset.type = "button";
    const name = text(label);
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
  const conversations = initConversationTools({ ...hooks, element, dialog, open,
    browserBackup: () => ({ library: savedLibrary(), preferences, appearance: { theme: $("themeSelect").value, mode: $("colorModeSelect").value } }),
    restoreBrowserBackup(data, restorePreferences) {
      const current = savedLibrary(), imported = savedLibrary(data.library);
      let added = 0;
      for (const kind of ["prompts", "profiles"]) {
        for (const item of imported[kind]) {
          const name = item.name.trim();
          if (!name || name.length > 80 || current[kind].some(entry => entry.id === item.id)
              || libraryNameExists(kind, current[kind], name)) continue;
          if (kind === "prompts" && !item.content.trim()) continue;
          if (kind === "profiles") {
            if (item.settings.system_prompt != null && typeof item.settings.system_prompt !== "string") continue;
            const valid = profileInputs.every(([key, field]) => {
              const input = field.cloneNode(false);
              input.value = item.settings[key] ?? input.defaultValue;
              return input.checkValidity();
            });
            if (!valid) continue;
          }
          current[kind].push({ ...item, name });
          added++;
        }
      }
      let persisted = !added || save(libraryKey, current);
      if (restorePreferences && data.preferences) {
        preferences = { ...preferenceDefaults, ...data.preferences };
        applyPreferences();
        persisted = save(preferenceKey, preferences) && persisted;
      }
      if (restorePreferences && ["light", "dark", "system"].includes(data.appearance?.mode)) {
        hooks.applyAppearance(data.appearance.theme, data.appearance.mode);
      }
      renderLibrary();
      return { added, persisted };
    },
  });
  return {
    compareResponse: conversations.compareResponse,
    isExpanded: () => editor.open, modifierSends: () => preferences.send === "modifier",
    finishCompose() { if (editor.open) editor.close(); },
    editPrompt(target) { editing = target; $("editedPrompt").value = target.message; $("editPromptError").textContent = ""; open(editDialog); $("editedPrompt").focus(); },
    update() {
      const state = hooks.getState();
      $("chatUsageButton").disabled = !state.chatId;
      $("attachTextButton").disabled = state.chatReadOnly;
      const queueMode = state.generating || state.queuePending;
      $("composerSend").disabled = state.chatReadOnly || (queueMode && !state.canQueuePrompt);
      $("composerSend").dataset.text = queueMode ? "queuePrompt" : "send";
      $("composerSend").textContent = text($("composerSend").dataset.text);
      if (wasGenerating && !state.generating && usage.open) loadUsage();
      wasGenerating = state.generating;
      downloader.update();
    },
    themeChanged() { [editor, attachment, editDialog, library, libraryEditor, variables, draftChoice, usage].forEach(node => refreshThemeContent(node)); renderLibrary(); if (usage.open) loadUsage(); downloader.themeChanged(); conversations.themeChanged(); }
  };
}
