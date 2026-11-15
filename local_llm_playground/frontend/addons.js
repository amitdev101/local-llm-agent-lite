export function initAddons({ api, text, getState, manageAddons, notify }) {
  const $ = id => document.getElementById(id);
  const search = $("webSearchForReply"), query = $("webSearchQuery");
  const markitdown = $("markitdownEnabled"), searxng = $("searxngEnabled"), url = $("searxngUrl");
  const feedback = $("addonFeedback");
  const localFeedback = $("searxngLocalFeedback"), logOutput = $("searxngLogOutput");
  const localActions = ["startSearxng", "stopSearxng", "testLocalSearxng", "searxngLogs", "checkSearxngLocal",
    "searxngMethodNative", "searxngMethodDocker"];
  const localStatuses = new Map();
  const selections = new Map();
  let settings = null;
  let busy = false;
  let setupMethod = "docker";
  let localBusy = false;
  let testedUrl = "";

  function showMessage(node, value) {
    node.textContent = value;
    node.hidden = !value;
  }

  function update() {
    const { chatId, chatReadOnly } = getState();
    const stopped = [...localStatuses.values()].some(local => local.container === "stopped" && local.url === settings?.searxng_url);
    const ready = Boolean(settings?.searxng_enabled && settings.searxng_url && !stopped);
    const selection = ready && !chatReadOnly && selections.get(chatId);
    search.disabled = !ready || chatReadOnly;
    search.checked = Boolean(selection);
    $("webSearchHelp").textContent = text(ready ? "webSearchHelp" : stopped ? "webSearchLocalStopped" : "webSearchSetup");
    $("webSearchQueryField").hidden = !selection;
    if (selection && query.value !== selection.query) query.value = selection.query;
    $("clearWebSearch").hidden = !selection;
    $("workspaceToolsButton").toggleAttribute("data-web-search", Boolean(selection));
  }

  function render() {
    markitdown.disabled = searxng.disabled = busy || !settings;
    $("markitdownStatus").textContent = text(!settings ? "addonUnknown" : !settings.markitdown_enabled
      ? "addonOff" : settings.markitdown_available ? "addonReady" : "addonSetup");
    $("markitdownSetup").hidden = !settings || settings.markitdown_available;
    $("searxngStatus").textContent = text(!settings ? "addonUnknown" : !settings.searxng_enabled ? "addonOff"
      : !settings.searxng_url ? "addonSetup" : settings.searxng_url === testedUrl ? "addonReady" : "addonNotTested");
    $("refreshAddons").disabled = busy;
    $("saveSearxng").disabled = $("testSearxng").disabled = busy || !settings;
    if (settings) {
      markitdown.checked = settings.markitdown_enabled;
      searxng.checked = settings.searxng_enabled;
    }
    renderLocal();
    update();
  }

  function renderLocal() {
    const native = setupMethod === "native";
    const local = localStatuses.get(setupMethod);
    const state = local?.container;
    const running = state === "running", installed = running || state === "stopped";
    const selected = installed && settings?.searxng_enabled && settings.searxng_url === local.url;
    $("searxngMethodNative").setAttribute("aria-pressed", String(native));
    $("searxngMethodDocker").setAttribute("aria-pressed", String(!native));
    $("searxngLocalHelp").textContent = text(native ? "searxngNativeHelp" : "searxngDockerHelp");
    $("searxngLocalStatus").textContent = text({
      not_installed: native ? "searxngNativeReady" : "searxngDockerReady",
      running: "searxngLocalRunning", stopped: "searxngLocalStopped",
      unknown: "searxngLocalUnavailable",
    }[state] || (native ? "searxngCheckingNative" : "searxngCheckingDocker"));
    showMessage($("searxngLocalMessage"), local?.message || (selected ? text("searxngUsingLocal") : ""));
    $("startSearxng").hidden = state !== "not_installed" && state !== "stopped";
    $("startSearxng").textContent = text(installed ? "searxngStart" : "searxngInstall");
    $("stopSearxng").hidden = $("testLocalSearxng").hidden = !running;
    $("useLocalSearxng").hidden = !running || selected;
    $("useLocalSearxng").disabled = localBusy || busy || !settings;
    $("searxngLogs").hidden = $("searxngLocalFiles").hidden = !installed && !local?.has_logs;
    $("searxngLogs").textContent = text(logOutput.hidden ? "searxngLogs" : "searxngHideLogs");
    $("searxngLogs").setAttribute("aria-expanded", String(!logOutput.hidden));
    $("searxngLocalPath").textContent = local?.folder || "";
    $("installSearxngDocker").hidden = native || !local?.needs_docker;
    $("checkSearxngLocal").textContent = text(native ? "searxngCheckNative" : "searxngCheckDocker");
    for (const id of localActions) $(id).disabled = localBusy;
  }

  async function change(action, message = "") {
    if (busy) return;
    busy = true;
    showMessage(feedback, message ? text(message) : "");
    // Keep the changed switch visible while saving and revert it if the save fails.
    markitdown.disabled = searxng.disabled = $("refreshAddons").disabled = $("saveSearxng").disabled = $("testSearxng").disabled = true;
    renderLocal();
    try { await action(); }
    catch (error) { showMessage(feedback, error.message); notify(error.message); }
    finally { busy = false; render(); }
  }

  async function refresh(checkSetup = true) {
    await change(async () => {
      const firstLoad = !settings;
      settings = await api("/api/addons");
      if (firstLoad) {
        url.value = settings.searxng_url;
        setupMethod = settings.searxng_setup_method === "native" ? "native" : "docker";
      }
    });
    if (checkSetup) await checkLocal();
  }

  function localEndpoint() {
    return `/api/addons/searxng/local?method=${setupMethod}`;
  }

  async function checkLocal() {
    if (localBusy) return;
    localBusy = true;
    renderLocal();
    try {
      const local = await api(localEndpoint());
      localStatuses.set(setupMethod, local);
      if (local.container === "stopped" && testedUrl === local.url) testedUrl = "";
    } catch (error) {
      localStatuses.set(setupMethod, { container: "unknown", message: error.message });
    } finally {
      localBusy = false;
      render();
    }
  }

  async function manageLocal(action, progress) {
    if (localBusy) return;
    localBusy = true;
    showMessage(localFeedback, progress ? text(progress) : "");
    renderLocal();
    let refreshStatus = false;
    try {
      const local = localStatuses.get(setupMethod);
      const result = await api(localEndpoint(), { method: "POST", body: JSON.stringify({ action }) });
      local.container = result.container;
      if (action === "logs") {
        showMessage(logOutput, result.logs || text("searxngNoLogs"));
        logOutput.focus();
      } else if (action === "start" || action === "test") {
        if (result.search_count > 0) testedUrl = local.url;
        else if (testedUrl === local.url) testedUrl = "";
        showMessage(localFeedback, result.search_error || text(result.search_count > 0 ? "searxngSearchWorks" : "searxngSearchEmpty"));
      } else if (testedUrl === local.url) {
        testedUrl = "";
      }
    } catch (error) {
      showMessage(localFeedback, error.message);
      refreshStatus = true;
    } finally {
      localBusy = false;
      render();
    }
    if (refreshStatus) await checkLocal();
  }

  function selectMethod(method) {
    if (setupMethod === method || localBusy) return;
    setupMethod = method;
    showMessage(localFeedback, "");
    showMessage(logOutput, "");
    checkLocal();
  }

  function save(values) {
    return api("/api/addons", { method: "PATCH", body: JSON.stringify(values) });
  }

  markitdown.onchange = () => change(async () => { settings = await save({ markitdown_enabled: markitdown.checked }); });
  searxng.onchange = () => change(async () => {
    settings = await save({ searxng_enabled: searxng.checked });
    if (!settings.searxng_enabled) selections.clear();
  });
  $("searxngForm").onsubmit = event => {
    event.preventDefault();
    change(async () => {
      settings = await save({ searxng_url: url.value.trim() });
      url.value = settings.searxng_url;
      if (!settings.searxng_url) selections.clear();
      showMessage(feedback, text("searxngSaved"));
    });
  };
  $("testSearxng").onclick = () => {
    if (!url.value.trim()) { url.focus(); return; }
    if (!url.reportValidity()) return;
    change(async () => {
      testedUrl = "";
      const result = await api("/api/addons/searxng/test", { method: "POST", body: JSON.stringify({ url: url.value.trim() }) });
      if (result.search_count > 0) testedUrl = result.url;
      showMessage(feedback, text(!testedUrl ? "searxngSearchEmpty"
        : testedUrl === settings.searxng_url ? "searxngConnected" : "searxngConnectedSave"));
    }, "searxngTesting");
  };
  $("searxngMethodNative").onclick = () => selectMethod("native");
  $("searxngMethodDocker").onclick = () => selectMethod("docker");
  $("startSearxng").onclick = () => manageLocal("start", localStatuses.get(setupMethod).container === "not_installed"
    ? "searxngLocalInstalling" : "searxngLocalStarting");
  $("stopSearxng").onclick = () => manageLocal("stop", "searxngLocalStopping");
  $("testLocalSearxng").onclick = () => manageLocal("test", "searxngTesting");
  $("searxngLogs").onclick = () => {
    if (logOutput.hidden) manageLocal("logs");
    else {
      showMessage(logOutput, "");
      renderLocal();
    }
  };
  $("useLocalSearxng").onclick = () => change(async () => {
    settings = await save({ searxng_url: localStatuses.get(setupMethod).url, searxng_enabled: true });
    url.value = settings.searxng_url;
  });
  $("checkSearxngLocal").onclick = checkLocal;
  $("refreshAddons").onclick = () => refresh(false);
  $("manageAddons").onclick = manageAddons;
  search.onchange = () => {
    const { chatId } = getState();
    if (search.checked) selections.set(chatId, { chatId, query: "" });
    else selections.delete(chatId);
    update();
  };
  query.oninput = () => {
    const selection = selections.get(getState().chatId);
    if (selection) selection.query = query.value;
  };
  $("clearWebSearch").onclick = () => {
    selections.delete(getState().chatId);
    update();
    $("prompt").focus();
  };

  render();
  const initialLoad = refresh(false);
  return {
    update,
    refresh: () => initialLoad.then(() => refresh()),
    render,
    transferDraft(id) {
      const selection = selections.get(null);
      if (!selection) return;
      selection.chatId = id;
      selections.set(id, selection);
      selections.delete(null);
    },
    captureSearch() {
      const { chatId, draft } = getState();
      const selection = search.checked && selections.get(chatId);
      if (!selection) return null;
      const requested = selection.query.trim() || draft.trim();
      if (!requested || requested.length > 500) {
        notify(text("webSearchQueryNeeded"));
        const tools = $("toolsPopover");
        if (!tools.matches(":popover-open")) tools.showPopover();
        query.focus();
        return false;
      }
      return { selection, query: requested };
    },
    consumeSearch(captured) {
      const { selection } = captured;
      if (selections.get(selection.chatId) === selection && (selection.query.trim() || captured.query) === captured.query) {
        selections.delete(selection.chatId);
      }
      update();
    },
  };
}
