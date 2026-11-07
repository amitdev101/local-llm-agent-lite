// Public model discovery and durable server downloads; chat state stays in app.js.
export function initModelDownloads(hooks) {
  const { api, text, element, dialog, open } = hooks;
  const $ = id => document.getElementById(id);
  const panel = dialog("modelDownloadsDialog", "getModels", '<p class="setting-help" data-text="downloadHelp"></p><div class="tool-tabs"><button id="downloadBrowseTab" data-text="getModels"></button><button id="downloadJobsTab" data-text="downloads"></button></div><section id="downloadBrowse"><div id="modelCatalog" class="download-catalog"></div><form id="repositoryForm"><label for="repositoryInput" data-text="modelRepository"></label><div class="download-search"><input id="repositoryInput" required maxlength="300" placeholder="owner/model-GGUF" autocomplete="off"><button class="secondary-button" data-text="browseFiles"></button></div></form><p id="repositoryStatus" role="status"></p><div id="repositoryFiles"></div></section><section id="downloadJobs" hidden><p id="downloadJobsStatus" role="status"></p><p id="downloadDestination" class="setting-help model-path"></p><div id="downloadJobList"></div></section>');
  panel.classList.add("download-dialog");
  const downloadResources = element("p", "", "setting-help");
  $("downloadBrowse").prepend(downloadResources);
  const guidance = dialog("modelGuidanceDialog", "modelGuidance", `
    <p id="modelResources" role="status"></p><p id="modelFit" class="setting-help"></p>
    <p class="setting-help" data-text="modelMemoryHelp"></p>
    <p class="setting-help" data-text="quantizationHelp"></p>
    <p id="cpuStarterSummary" class="setting-help"></p>
    <div class="settings-actions"><button id="refreshModelGuidance" class="secondary-button" data-text="refresh"></button>
    <button id="useCpuStarter" class="primary-button" data-text="useCpuStarter" disabled></button></div>`);
  let machine = null, machineRequest = 0;
  let jobs = [], catalogLoaded = false, timer = null, revision = 0, pollRevision = 0, pending = false, request = null, actionError = "";
  const rows = new Map(), fileControls = [];
  const activeStates = ["downloading", "verifying", "pausing", "cancelling"];
  const activeJob = () => jobs.find(job => activeStates.includes(job.status));
  const busyModel = () => hooks.getState().generating || hooks.getState().modelBusy;
  function bytes(value) {
    const units = ["B", "KiB", "MiB", "GiB", "TiB"]; let unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return value.toLocaleString(undefined, { maximumFractionDigits: unit ? 2 : 0 }) + " " + units[unit];
  }
  function fileDescription(file) {
    const format = file.filename?.match(/(?:^|[-_.])((?:I?Q[1-8](?:_[A-Z0-9]+)*)|BF16|F16|F32)(?=\.gguf$|[-.])/i)?.[1];
    return format ? `${bytes(file.size)} · ${format.toUpperCase()}` : bytes(file.size);
  }
  function renderGuidance() {
    const resources = machine ? text("availableModelResources", { ram: bytes(machine.available_ram), total: bytes(machine.total_ram), disk: bytes(machine.free_disk) }) : "";
    downloadResources.textContent = resources;
    $("modelResources").textContent = resources;
    const file = hooks.selectedModel();
    $("modelFit").textContent = file ? text("modelFileSize", { name: file.name, size: fileDescription({ ...file, filename: file.name }) }) : text("modelFitSelect");
    if (file && machine && file.size > machine.available_ram) $("modelFit").textContent += ` ${text("modelLargeForRam")}`;
    $("cpuStarterSummary").textContent = machine ? text("cpuStarterSummary", machine.cpu_starter) : "";
    $("useCpuStarter").disabled = !machine || busyModel();
  }
  async function readMachine() {
    const revision = ++machineRequest;
    if (guidance.open) $("modelResources").textContent = text("checkingModels");
    try {
      const data = await api("/api/model-guidance");
      if (!Number.isFinite(data.available_ram) || !Number.isFinite(data.total_ram) || !Number.isFinite(data.free_disk) || !data.cpu_starter) throw new Error(text("modelGuidanceUnavailable"));
      if (revision !== machineRequest) return;
      machine = data;
      renderGuidance(); updateButtons();
    } catch (_) {
      if (revision !== machineRequest) return;
      machine = null;
      renderGuidance(); updateButtons();
      if (guidance.open) $("modelResources").textContent = text("modelGuidanceUnavailable");
    }
  }
  function showTab(tab) {
    $("downloadBrowse").hidden = tab !== "browse"; $("downloadJobs").hidden = tab !== "jobs";
    $("downloadBrowseTab").setAttribute("aria-pressed", String(tab === "browse"));
    $("downloadJobsTab").setAttribute("aria-pressed", String(tab === "jobs"));
  }
  function updateButtons() {
    for (const item of fileControls) {
      const exists = item.file.installed && item.input.value === item.file.save_name;
      item.button.disabled = pending || Boolean(activeJob()) || exists;
      item.existing.hidden = !exists;
      item.existing.disabled = busyModel();
      item.notice.textContent = exists ? text("filenameExists") : item.input.value !== item.file.save_name && item.file.thinking_support === "supported" ? text("renamedThinking") : "";
      item.fit.textContent = machine && item.file.size > machine.free_disk ? text("modelLargeForDisk")
        : machine && item.file.size > machine.available_ram ? text("modelLargeForRam") : "";
    }
    for (const job of jobs) {
      const row = rows.get(job.id); if (!row) continue;
      row.pause.hidden = !["downloading", "verifying"].includes(job.status);
      row.resume.hidden = !["paused", "error"].includes(job.status);
      row.cancel.hidden = ["complete", "cancelled"].includes(job.status);
      row.load.hidden = job.status !== "complete";
      row.pause.disabled = row.cancel.disabled = pending || ["pausing", "cancelling"].includes(job.status);
      row.resume.disabled = pending || Boolean(activeJob()); row.load.disabled = busyModel();
    }
  }
  async function useModel(path) {
    if (busyModel() || !path) return;
    panel.close();
    try { await hooks.loadDownloaded(path); }
    catch (error) { hooks.notify(error.message); }
  }
  async function control(id, action) {
    if (pending) return;
    actionError = "";
    pending = true; updateButtons();
    try { await api(`/api/model-downloads/${id}`, { method: "POST", body: JSON.stringify({ action }) }); }
    catch (error) { actionError = error.message; }
    finally { pending = false; await poll(); updateButtons(); }
  }
  function renderJobs() {
    for (const [index, job] of jobs.entries()) {
      let row = rows.get(job.id);
      if (!row) {
        const node = element("section", "", "download-job"); node.dataset.jobId = job.id;
        const name = element("h3", job.save_name), source = element("p", job.repo_id, "setting-help");
        const status = element("p", "", "download-status"), progress = document.createElement("progress");
        progress.setAttribute("aria-label", job.save_name); const size = element("p", "", "setting-help");
        const message = element("p", "", "model-load-error"); message.setAttribute("role", "status");
        const actions = element("div", "", "settings-actions");
        const pause = element("button", "", "secondary-button"), resume = element("button", "", "secondary-button"), cancel = element("button", "", "secondary-button"), load = element("button", "", "primary-button");
        pause.onclick = () => control(job.id, "pause"); resume.onclick = () => control(job.id, "resume");
        cancel.onclick = () => control(job.id, "cancel"); load.onclick = () => useModel(jobs.find(value => value.id === job.id)?.path);
        actions.append(pause, resume, cancel, load); node.append(name, source, status, progress, size, message, actions);
        $("downloadJobList").append(node); row = { node, status, progress, size, message, pause, resume, cancel, load }; rows.set(job.id, row);
      }
      const expected = $("downloadJobList").children[index];
      if (expected !== row.node) $("downloadJobList").insertBefore(row.node, expected || null);
      // Keep controls in place so polling does not steal keyboard focus.
      const label = text("downloadState_" + job.status);
      if (row.status.textContent !== label) row.status.textContent = label;
      row.progress.max = job.size; row.progress.value = job.downloaded;
      row.size.textContent = bytes(job.downloaded) + " / " + bytes(job.size);
      row.message.textContent = job.message || ""; row.message.hidden = !job.message;
      row.pause.textContent = text("pauseDownload"); row.resume.textContent = text(job.status === "error" ? "retry" : "resumeDownload");
      row.cancel.textContent = text("cancelDownload"); row.load.textContent = text("loadModel");
    }
    const active = activeJob();
    const label = active ? text("downloadProgress", { percent: Math.floor(active.downloaded / active.size * 100) }) : jobs[0] ? text("downloadState_" + jobs[0].status) : "";
    if ($("downloadIndicator").textContent !== label) $("downloadIndicator").textContent = label;
    updateButtons();
  }
  async function poll() {
    const version = ++pollRevision;
    clearTimeout(timer); timer = null;
    try {
      const data = await api("/api/model-downloads");
      if (version !== pollRevision) return;
      jobs = Array.isArray(data.jobs) ? data.jobs : [];
      for (const [id, row] of rows) if (!jobs.some(job => job.id === id)) { row.node.remove(); rows.delete(id); }
      $("downloadJobsStatus").textContent = actionError || (jobs.length ? "" : text("noDownloads"));
      $("downloadDestination").textContent = data.destination ? text("downloadDestination", { path: data.destination }) : "";
      renderJobs();
    } catch (_) { if (version === pollRevision) $("downloadJobsStatus").textContent = text("downloadConnectionLost"); }
    if (version === pollRevision && (panel.open || activeJob())) timer = setTimeout(poll, 1000);
  }
  async function browse(source) {
    const token = ++revision; request?.abort(); request = new AbortController();
    $("repositoryStatus").textContent = text("checkingModels"); $("repositoryFiles").replaceChildren(); fileControls.length = 0;
    try {
      const data = await api("/api/model-repository?repo_id=" + encodeURIComponent(source), { signal: request.signal });
      if (token !== revision) return;
      $("repositoryStatus").textContent = data.files.length ? text("filesAtRevision", { repo: data.repo_id, revision: data.revision.slice(0, 10) }) : text("noModelFiles");
      const link = element("a", text("modelCard")); link.href = data.source_url; link.target = "_blank"; link.rel = "noopener noreferrer";
      $("repositoryFiles").append(link);
      for (const file of data.files) {
        const card = element("section", "", "download-file");
        const fit = element("p", "", "setting-help model-fit-note");
        card.append(element("h3", file.filename), element("p", fileDescription(file), "setting-help"), fit);
        const label = element("label", text("saveModelAs")), input = document.createElement("input");
        input.value = file.save_name; input.maxLength = 206; input.required = true; input.setAttribute("aria-label", text("saveModelAs") + " " + file.filename); label.append(input);
        const notice = element("p", "", "setting-help"), actions = element("div", "", "settings-actions");
        const button = element("button", text("downloadModel"), "primary-button"), existing = element("button", text("useInstalledModel"), "secondary-button");
        // The server provides the installed path; never construct an arbitrary download URL.
        existing.onclick = () => useModel(file.path);
        input.oninput = updateButtons;
        button.onclick = async () => {
          if (pending) return;
          if (!input.checkValidity()) return input.reportValidity();
          pending = true; updateButtons();
          try {
            await api("/api/model-downloads", { method: "POST", body: JSON.stringify({ repo_id: data.repo_id, revision: data.revision, filename: file.filename, save_name: input.value }) });
            showTab("jobs"); await poll();
          } catch (error) { $("repositoryStatus").textContent = error.message; }
          finally { pending = false; updateButtons(); }
        };
        actions.append(button, existing); card.append(label, notice, actions); $("repositoryFiles").append(card);
        fileControls.push({ file, input, button, existing, notice, fit });
      }
      updateButtons();
      $("repositoryStatus").scrollIntoView({ block: "nearest" });
    } catch (error) { if (token === revision && error.name !== "AbortError") $("repositoryStatus").textContent = error.message; }
  }
  async function catalog() {
    if (catalogLoaded) return;
    try {
      const data = await api("/api/model-catalog"); $("modelCatalog").replaceChildren();
      for (const entry of data.entries || []) {
        const card = element("section", "", "catalog-item"), button = element("button", text("browseFiles"), "secondary-button");
        card.append(element("h3", entry.name), element("p", entry.description, "setting-help"));
        button.onclick = () => { $("repositoryInput").value = entry.repo_id; browse(entry.repo_id); };
        card.append(button); $("modelCatalog").append(card);
      }
      if (data.error) $("repositoryStatus").textContent = data.error;
      catalogLoaded = true;
    } catch (_) { $("repositoryStatus").textContent = text("catalogUnavailable"); }
  }
  function show(jobsFirst = false) { open(panel); showTab(jobsFirst ? "jobs" : "browse"); catalog(); poll(); readMachine(); }
  $("getModelsButton").onclick = () => show(); $("getFirstModel").onclick = () => show();
  $("downloadsSidebar").onclick = () => show(jobs.length > 0);
  $("downloadBrowseTab").onclick = () => showTab("browse"); $("downloadJobsTab").onclick = () => showTab("jobs");
  $("repositoryForm").onsubmit = event => { event.preventDefault(); if ($("repositoryInput").value.trim()) browse($("repositoryInput").value.trim()); };
  $("refreshModelsButton").onclick = async () => {
    try { await hooks.refreshModels(); hooks.notify(text("modelsRefreshed")); }
    catch (error) { hooks.notify(error.message); }
  };
  $("modelGuidanceButton").onclick = () => { open(guidance); renderGuidance(); readMachine(); };
  $("refreshModelGuidance").onclick = readMachine;
  $("useCpuStarter").onclick = () => {
    if (!machine || busyModel()) return;
    guidance.close();
    hooks.useCpuSettings(machine.cpu_starter);
  };
  panel.addEventListener("close", () => { if (!activeJob()) { clearTimeout(timer); timer = null; } });
  poll(); // Local history only; Hugging Face is contacted after Browse or Download.
  return {
    update() { updateButtons(); $("getFirstModel").hidden = hooks.getState().hasModels; $("refreshModelsButton").disabled = busyModel(); if (guidance.open) $("useCpuStarter").disabled = !machine || busyModel(); },
    themeChanged() { hooks.refreshThemeContent(panel); hooks.refreshThemeContent(guidance); renderJobs(); renderGuidance(); }
  };
}
