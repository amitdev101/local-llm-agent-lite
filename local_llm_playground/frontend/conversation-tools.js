function downloadFile(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function initConversationTools(hooks) {
  const { api, text, dialog, open, element } = hooks;
  const $ = id => document.getElementById(id);
  const branches = dialog("branchDialog", "conversationBranches", '<p id="branchSource" class="setting-help"></p><div id="branchList" class="branch-list"></div>');

  async function visit(id, messageId) {
    branches.close();
    try { await hooks.openChat(id, messageId); }
    catch (error) { hooks.notify(error.message); }
  }

  function renderBranches() {
    const active = hooks.getState().chatId;
    const chats = hooks.getChats().filter(chat => chat.visibility !== "trashed" || chat.id === active);
    const byId = new Map(chats.map(chat => [chat.id, chat]));
    function rootOf(id) {
      const seen = new Set();
      while (byId.get(id)?.branch_of?.chat_id && !seen.has(id)) {
        seen.add(id);
        id = byId.get(id).branch_of.chat_id;
      }
      return id;
    }
    const root = rootOf(active);
    const current = byId.get(active);
    const source = byId.get(current?.branch_of?.chat_id);
    const sourceNode = $("branchSource");
    sourceNode.textContent = current?.branch_of && !source ? text("branchSourceUnavailable") : "";
    if (source) {
      const jump = element("button", text("branchSourceResponse", { title: source.title }), "secondary-button");
      jump.onclick = () => visit(source.id, current.branch_of.message_id);
      sourceNode.append(jump);
    }
    const list = $("branchList");
    list.replaceChildren();
    const related = chats.filter(chat => rootOf(chat.id) === root);
    related.sort((a, b) => Number(b.id === root) - Number(a.id === root));
    if (related.length < 2) list.append(element("p", text("noRelatedBranches"), "setting-help"));
    for (const chat of related) {
      const row = element("div", "", "branch-row");
      const title = element("span", chat.title);
      const label = chat.id === root ? "branchOriginal" : "branchAlternative";
      row.append(title, element("small", text(chat.visibility === "archived" ? "archivedChatNotice" : label), "setting-help"));
      if (chat.id === active) {
        row.setAttribute("aria-current", "true");
        row.append(element("small", text("thisChat"), "setting-help"));
      } else {
        const button = element("button", text("openChat"), "secondary-button");
        button.setAttribute("aria-label", text("openNamedChat", { title: chat.title }));
        button.onclick = () => visit(chat.id);
        row.append(button);
      }
      list.append(row);
    }
  }

  $("chatBranches").onclick = () => { renderBranches(); open(branches); };
  const bookmarks = dialog("bookmarksDialog", "bookmarks", '<p id="bookmarksStatus" class="setting-help" role="status"></p><div id="bookmarksList"></div>');
  let bookmarkRevision = 0;
  $("bookmarksSidebar").onclick = async () => {
    const revision = ++bookmarkRevision;
    const list = $("bookmarksList"), status = $("bookmarksStatus");
    list.replaceChildren();
    status.textContent = text("loadingBookmarks");
    open(bookmarks);
    try {
      const items = await api("/api/bookmarks");
      if (revision !== bookmarkRevision || !bookmarks.open) return;
      status.textContent = items.length ? "" : text("noBookmarks");
      for (const item of items) {
        const row = element("div", "", "library-item");
        const jump = element("button", item.title, "secondary-button");
        jump.onclick = async () => {
          bookmarks.close();
          try { await hooks.openChat(item.chat_id, item.message_id); }
          catch (error) { hooks.notify(error.message); }
        };
        row.append(jump, element("p", item.snippet, "setting-help"));
        list.append(row);
      }
    } catch (error) {
      if (revision === bookmarkRevision && bookmarks.open) status.textContent = error.message;
    }
  };
  $("exportChat").onclick = async () => {
    const id = hooks.getState().chatId;
    if (!id) return;
    try {
      const chat = await api(`/api/chats/${id}`);
      const sections = [`# ${chat.title.replace(/\s+/g, " ")}`];
      for (const message of chat.messages) {
        const author = text(message.type === "user" ? "exportUser" : "exportAssistant");
        const status = ["error", "interrupted"].includes(message.status) ? ` (${text(message.status === "error" ? "responseFailed" : "responseStopped")})` : "";
        sections.push(`## ${author}${status}\n\n${message.content}`);
      }
      const name = chat.title.replace(/[^\p{L}\p{N}_-]+/gu, "-").slice(0, 70).replace(/^-+|-+$/g, "") || "chat";
      downloadFile(`${name}.md`, sections.join("\n\n") + "\n", "text/markdown;charset=utf-8");
    } catch (error) { hooks.notify(error.message); }
  };

  const backups = dialog("backupDialog", "chatBackups", `
    <p class="setting-help" data-text="backupHelp"></p>
    <button id="downloadChatBackup" class="primary-button" data-text="downloadBackup"></button>
    <div class="backup-restore">
      <label for="chatBackupFile" data-text="selectBackup"></label>
      <input id="chatBackupFile" type="file" accept=".json,application/json">
      <p id="backupContents" class="setting-help" role="status"></p>
      <label class="library-thinking-field"><input id="restoreBackupPreferences" type="checkbox"><span data-text="restorePreferences"></span></label>
      <button id="restoreChatBackup" class="secondary-button" data-text="restoreBackup" disabled></button>
    </div>
    <p id="backupStatus" class="setting-help" role="status"></p>`);
  let backup = null, backupBusy = false, fileRevision = 0;

  function updateBackupButtons() {
    $("downloadChatBackup").disabled = $("chatBackupFile").disabled = backupBusy;
    $("restoreChatBackup").disabled = backupBusy || !backup;
  }

  $("openChatBackups").onclick = () => {
    if (!backupBusy) {
      backup = null;
      $("chatBackupFile").value = "";
      $("restoreBackupPreferences").checked = false;
      $("backupContents").textContent = $("backupStatus").textContent = "";
    }
    updateBackupButtons();
    open(backups);
  };
  $("downloadChatBackup").onclick = async () => {
    backupBusy = true;
    updateBackupButtons();
    $("backupStatus").textContent = text("preparingBackup");
    try {
      const data = await api("/api/chat-backup");
      const contents = { format: "myllm-playground", ...data, ...hooks.browserBackup() };
      downloadFile("myllm-playground-backup.json", JSON.stringify(contents, null, 2), "application/json");
      $("backupStatus").textContent = text("backupDownloaded");
    } catch (error) { $("backupStatus").textContent = error.message; }
    finally { backupBusy = false; updateBackupButtons(); }
  };
  $("chatBackupFile").onchange = async event => {
    const revision = ++fileRevision;
    const file = event.target.files[0];
    backup = null;
    $("backupContents").textContent = $("backupStatus").textContent = "";
    updateBackupButtons();
    if (!file) return;
    try {
      if (file.size > 50 * 1024 * 1024) throw new Error(text("backupTooLarge"));
      let data;
      try { data = JSON.parse(await file.text()); }
      catch (_) { throw new Error(text("invalidBackup")); }
      if (revision !== fileRevision || !backups.open) return;
      if (data?.format !== "myllm-playground" || data.version !== 1 || !Array.isArray(data.chats)
          || !Array.isArray(data.library?.profiles) || !Array.isArray(data.library?.prompts)) throw new Error(text("invalidBackup"));
      backup = data;
      $("backupContents").textContent = text("backupPreview", { chats: data.chats.length, items: data.library.profiles.length + data.library.prompts.length });
    } catch (error) { if (revision === fileRevision && backups.open) $("backupStatus").textContent = error.message; }
    finally { updateBackupButtons(); }
  };
  $("restoreChatBackup").onclick = async () => {
    if (!backup || backupBusy) return;
    backupBusy = true;
    updateBackupButtons();
    const restorePreferences = $("restoreBackupPreferences").checked;
    $("backupStatus").textContent = text("restoringBackup");
    try {
      const result = await api("/api/chat-backup/restore", { method: "POST", body: JSON.stringify({ version: backup.version, chats: backup.chats }) });
      const browser = hooks.restoreBrowserBackup(backup, restorePreferences);
      const message = text("backupRestored", { ...result, items: browser.added });
      $("backupStatus").textContent = browser.persisted ? message : `${message} ${text("storageUnavailable")}`;
      backup = null;
      $("chatBackupFile").value = "";
      $("backupContents").textContent = "";
      try { await hooks.refreshChats(); }
      catch (_) { hooks.notify(text("chatListUnavailable")); }
    } catch (error) { $("backupStatus").textContent = error.message; }
    finally { backupBusy = false; updateBackupButtons(); }
  };
  backups.addEventListener("close", () => { fileRevision++; });

  const comparison = dialog("comparisonDialog", "compareResponse", `
    <p class="setting-help" data-text="comparisonHelp"></p>
    <p id="comparisonStatus" class="setting-help" role="status"></p>
    <details id="comparisonPrompt" hidden><summary data-text="comparisonSamePrompt"></summary><pre class="library-preview"></pre></details>
    <label id="comparisonChoice" hidden><span data-text="comparisonAlternative"></span> <select id="comparisonAlternative"></select></label>
    <div id="comparisonAnswers" class="comparison-answers"></div>`,
    '<button id="runComparison" class="primary-button" hidden></button>');
  let comparisonRevision = 0;
  function comparisonCard(item, label) {
    const metrics = item.metrics || {};
    const card = element("section", "", "comparison-answer");
    const heading = element("h3", text(label));
    const info = element("p", text("comparisonDetails", { model: metrics.model || text("comparisonUnknownModel"),
      temperature: item.request.temperature ?? "—", seconds: metrics.total_seconds ?? "—",
      speed: metrics.tokens_per_second ?? "—", input: metrics.prompt_tokens ?? "—", output: metrics.completion_tokens ?? "—" }), "setting-help");
    const answer = element("div", "", "content");
    hooks.renderComparisonAnswer(answer, item.content);
    card.append(heading, info, answer);
    return card;
  }
  async function compareResponse(chatId, messageId) {
    const revision = ++comparisonRevision;
    const status = $("comparisonStatus"), select = $("comparisonAlternative"), answers = $("comparisonAnswers");
    const run = $("runComparison");
    $("comparisonChoice").hidden = $("comparisonPrompt").hidden = run.hidden = true;
    answers.replaceChildren();
    status.textContent = text("comparisonLoading");
    open(comparison);
    try {
      const { original, alternatives } = await api(`/api/chats/${chatId}/compare/${encodeURIComponent(messageId)}`);
      if (revision !== comparisonRevision || !comparison.open) return;
      $("comparisonPrompt").hidden = false;
      $("comparisonPrompt").querySelector("pre").textContent = original.request.message;
      alternatives.sort((a, b) => Number(b.chat_id === chatId) - Number(a.chat_id === chatId));
      select.replaceChildren(...alternatives.map((item, index) => {
        const option = element("option", `${index + 1}. ${item.metrics?.model || item.title}`);
        option.value = index;
        return option;
      }));
      const render = () => {
        answers.replaceChildren(comparisonCard(original, "comparisonOriginal"));
        if (alternatives.length) answers.append(comparisonCard(alternatives[Number(select.value)], "comparisonAlternative"));
      };
      $("comparisonChoice").hidden = alternatives.length < 2;
      select.onchange = render;
      render();
      const model = hooks.comparisonModel();
      run.hidden = !model || original.visibility !== "active";
      run.textContent = text("runComparison", { model });
      run.onclick = () => { comparison.close(); hooks.runComparison(original); };
      status.textContent = run.hidden ? text("comparisonUnavailable") : alternatives.length ? "" : text("comparisonEmpty");
    } catch (error) {
      if (revision === comparisonRevision && comparison.open) status.textContent = error.message;
    }
  }

  return {
    compareResponse,
    themeChanged() { [branches, bookmarks, backups, comparison].forEach(hooks.refreshThemeContent); if (branches.open) renderBranches(); },
  };
}
