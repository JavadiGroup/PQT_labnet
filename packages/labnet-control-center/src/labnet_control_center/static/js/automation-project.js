// One Automation project: its files (left), the open file (middle), and the
// project's tasks, linked experiments, allowed devices and library scope
// (right). Photo-Agent is told which project is open and gets its text files.

(function () {
  "use strict";

  const { el, store, dialog, ago, plural, statusPill } = window.LabNet;
  const AUTHOR_KEY = "labnet-experiment-author";
  const PROJECT_ID = /^[a-z0-9][a-z0-9-]{0,40}-[a-z0-9]{6}$/;
  const projectId = decodeURIComponent(window.location.pathname.split("/")[2] || "");
  const api = "/api/automation/projects/" + encodeURIComponent(projectId);
  const nameHeading = document.getElementById("project-name");
  const metaLine = document.getElementById("project-meta");
  const notice = document.getElementById("notice");
  const toastBox = document.getElementById("toast");
  const panel = document.getElementById("project-panel");

  let zones = [];
  let maxFileBytes = 100 * 1024 * 1024;
  let project = null;
  let devices = []; // instruments on the linked canvases
  let entries = [];
  let library = { configured: false, entries: [], collections: [] };
  let savedExperiments = null; // the Experiments tab's list, to spot newer versions
  let lastTree = "";
  let tasksList = null;
  let runAllButton = null;
  let runsList = null;
  let runsNote = null;
  let runs = []; // newest first
  let runsSignature = "";
  let runsTimer = 0;
  let runLog = null; // the run whose log the viewer shows: {id, head, body, timer, signature}
  let agentInfo = null; // GET /api/automation/meta's run_models: the model and effort menus

  // ---- Helpers ------------------------------------------------------------------

  function pathUrl(path) {
    return path.split("/").map(encodeURIComponent).join("/");
  }

  function urlFor(item, options) {
    const base = item.source === "library" ? "/api/automation/library/files/" : api + "/files/";
    return base + pathUrl(item.path) + (options && options.download ? "?download=1" : "");
  }

  function zoneOf(path) {
    const parts = path.split("/").map((part) => part.toLowerCase());
    for (const zone of zones) {
      const prefix = zone.path.split("/").map((part) => part.toLowerCase());
      const inside = prefix.every((part, index) => part === parts[index]);
      if (inside && (!zone.exact || parts.length === prefix.length)) return zone;
    }
    return null;
  }

  function isMine(item) {
    const zone = item.source === "project" ? zoneOf(item.path) : null;
    return Boolean(zone && zone.owner === "user");
  }

  async function request(url, init) {
    const response = await fetch(url, { cache: "no-store", ...(init || {}) });
    let body = null;
    try {
      body = await response.json();
    } catch (_ignored) {
      /* 204, or not JSON */
    }
    if (!response.ok) {
      const error = new Error((body && body.detail) || "HTTP " + response.status);
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  }

  function jsonRequest(method, data) {
    return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
  }

  function putFile(path, blob, etag) {
    const headers = { "Content-Type": "application/octet-stream" };
    if (etag) headers["If-Match"] = etag;
    return request(api + "/files/" + pathUrl(path), { method: "PUT", headers, body: blob });
  }

  // A browser's file name, made acceptable (the server checks it again).
  function cleanName(name) {
    const base = name.split(/[\\/]/).pop()
      .replace(/[<>:"|?*\u0000-\u001f\u007f​-‏‪-‮⁠-⁯﻿]/g, "_")
      .replace(/^[\s.]+|[\s.]+$/g, "");
    return base || "file";
  }

  function showNotice(text) {
    notice.hidden = false;
    notice.textContent = text;
    window.clearTimeout(showNotice.timer);
    showNotice.timer = window.setTimeout(() => (notice.hidden = true), 12000);
  }

  function toast(text) {
    toastBox.textContent = text;
    toastBox.hidden = false;
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => (toastBox.hidden = true), 2500);
  }

  function findEntry(source, path) {
    const list = source === "library" ? library.entries : entries;
    const lower = path.toLowerCase();
    return list.find((entry) => entry.kind === "file" && entry.path.toLowerCase() === lower) || null;
  }

  // A link or image in a document, relative to the document; null if it leaves the project.
  function resolveRelative(fromPath, href) {
    const clean = href.split("#")[0].split("?")[0];
    if (!clean) return null;
    let decoded;
    try {
      decoded = decodeURIComponent(clean);
    } catch (_ignored) {
      return null;
    }
    const parts = clean.startsWith("/") ? [] : fromPath.split("/").slice(0, -1);
    for (const part of decoded.replace(/^\/+/, "").split("/")) {
      if (part === "" || part === ".") continue;
      if (part === "..") {
        if (!parts.length) return null;
        parts.pop();
      } else {
        parts.push(part);
      }
    }
    return parts.length ? parts.join("/") : null;
  }

  function docFor(item) {
    return {
      resolve(href) {
        const path = resolveRelative(item.path, href);
        const entry = path && findEntry(item.source, path);
        return entry ? urlFor({ source: item.source, path: entry.path }) : null;
      },
      navigate(href) {
        const path = resolveRelative(item.path, href);
        const entry = path && findEntry(item.source, path);
        if (entry) openFile({ source: item.source, path: entry.path, entry });
      },
    };
  }

  // ---- Tree and viewer ------------------------------------------------------------

  const tree = window.LabNet.createFileTree(document.getElementById("file-tree"), {
    storageKey: projectId,
    zoneOf,
    onOpen: (item) => openFile(item),
    onUpload: upload,
    onNewTask: newTask,
    onNewFolder: newFolder,
  });

  const viewer = window.LabNet.createFileViewer(document.getElementById("viewer"), {
    urlFor,
    writable: (item) => isMine(item) && item.path.toLowerCase().endsWith(".md"),
    canManage: (item) => isMine(item) && !zoneOf(item.path).exact,
    doc: docFor,
    save: saveText,
    rename: renameItem,
    remove: removeItem,
  });

  async function openFile(item) {
    if (!(await viewer.show(item))) return;
    tree.select(item.source, item.path);
    window.history.replaceState(null, "", "#" + item.source + "=" + encodeURIComponent(item.path));
  }

  function openFromHash() {
    const match = window.location.hash.match(/^#(project|library)=(.+)$/);
    if (!match) return false;
    let path;
    try {
      path = decodeURIComponent(match[2]);
    } catch (_ignored) {
      return false;
    }
    const entry = findEntry(match[1], path);
    if (!entry) return false;
    openFile({ source: match[1], path: entry.path, entry });
    return true;
  }

  async function showOverview() {
    if (!project) return;
    const steps = [
      ["Literature", "the lab library is already here. Add papers only this project needs."],
      ["Data/example_data", "example data (CSV, images) for the agent to learn from."],
      ["Experiment layout", "link the experiments whose instruments this project uses, and describe how they connect in LAYOUT.md."],
      ["Tasks", "one Markdown file per task: goal, inputs, constraints, deliverables."],
      ["Runs", "Run (or Run all) in the Tasks panel queues tasks for an Automation runner, which works through each with Claude Code; " +
        "its scripts, logs, data and analysis appear in ScriptsUtilized, Data/new_data and Analysis. " +
        "Until a runner is connected, queued runs wait."],
    ];
    const shown = await viewer.showNode(
      el("div", { class: "viewer-body doc" }, [
        el("div", { class: "md md-doc" }, [
          el("h2", { class: "md-heading", text: project.name }),
          project.description
            ? el("p", { class: "overview-description", text: project.description })
            : el("p", { class: "viewer-message", text: "No description yet: add one under Settings." }),
          el("h3", { class: "md-heading", text: "Getting started" }),
          el("ol", { class: "overview-steps" }, steps.map(([title, text]) => el("li", {}, [el("b", { text: title }), ": " + text]))),
          el("p", {
            class: "viewer-message",
            text: "Photo-Agent can see this project's text files while it is open: ask it about the papers, the data, " +
              "or how to phrase a task.",
          }),
        ]),
      ])
    );
    if (shown) {
      tree.select("", "");
      window.history.replaceState(null, "", window.location.pathname);
    }
  }

  // ---- Files: upload, create, save, rename, delete ------------------------------------

  async function upload(folder, files) {
    let done = 0;
    const failed = [];
    for (const [index, file] of files.entries()) {
      if (file.size > maxFileBytes) {
        failed.push(file.name + " is larger than " + Math.round(maxFileBytes / 1048576) + " MB");
        continue;
      }
      const path = folder + "/" + cleanName(file.name);
      toast("Uploading " + (index + 1) + " of " + files.length + "…");
      try {
        await putFile(path, file, null);
        done++;
      } catch (error) {
        if (error.status === 409 && error.body && error.body.exists) {
          const choice = await dialog({
            title: "Replace " + file.name + "?",
            body: el("p", { text: path + " already exists." }),
            actions: [{ label: "Replace", value: "replace", danger: true }],
          });
          if (choice !== "replace") continue;
          try {
            await putFile(path, file, error.body.etag);
            done++;
          } catch (again) {
            failed.push(file.name + ": " + again.message);
          }
        } else {
          failed.push(file.name + ": " + error.message);
        }
      }
    }
    await loadTree();
    if (failed.length) showNotice("Not uploaded: " + failed.join("; "));
    else if (done) toast(plural(done, "file") + " uploaded");
  }

  async function saveText(item, text) {
    const blob = new Blob([text], { type: "text/markdown" });
    try {
      const body = await putFile(item.path, blob, item.entry && item.entry.etag);
      await loadTree();
      return body.file;
    } catch (error) {
      if (error.status !== 409 || !error.body || !error.body.exists) throw error;
      const choice = await dialog({
        title: "This file changed elsewhere",
        body: el("p", { text: error.message + " Overwrite it with your version? (Cancel to copy your text first.)" }),
        actions: [{ label: "Overwrite", value: "overwrite", danger: true }],
      });
      if (choice !== "overwrite") throw new Error("Not saved: the file changed elsewhere.");
      const body = await putFile(item.path, blob, error.body.etag);
      await loadTree();
      return body.file;
    }
  }

  async function newTask() {
    const numbers = entries
      .filter((entry) => entry.kind === "file" && /^Tasks\/Task\d+\.md$/i.test(entry.path))
      .map((entry) => Number(entry.path.match(/Task(\d+)\.md$/i)[1]));
    const next = (numbers.length ? Math.max(...numbers) : 0) + 1;
    const title = el("input", { maxlength: "100", placeholder: "e.g. Calibrate the AOM efficiency curve" });
    const fileName = el("input", { maxlength: "120" });
    fileName.value = "Task" + next + ".md";
    const choice = await dialog({
      title: "New task",
      body: el("div", { class: "dialog-fields" }, [el("label", {}, ["Title", title]), el("label", {}, ["File name", fileName])]),
      actions: [{ label: "Create", value: "create", primary: true }],
      validate: () => (/\.md$/i.test(fileName.value.trim()) ? null : "The file name must end in .md."),
    });
    if (choice !== "create") return;
    const name = cleanName(fileName.value.trim());
    const heading = title.value.trim() || name.replace(/\.md$/i, "");
    const text =
      "# " + heading + "\n\n## Goal\n\nWhat should be true when this task is done?\n\n" +
      "## Inputs\n\nPapers, data files or experiments to start from (paths in this project).\n\n" +
      "## Constraints\n\nLimits to respect: which devices, which ranges, how long, and what not to touch.\n\n" +
      "## Deliverables\n\nFiles to produce, for example Data/new_data/<name>.csv and a section in Analysis/Analysis.md.\n";
    try {
      const body = await putFile("Tasks/" + name, new Blob([text], { type: "text/markdown" }), null);
      await loadTree();
      openFile({ source: "project", path: body.file.path, entry: body.file, edit: true });
    } catch (error) {
      showNotice("Couldn't create the task: " + error.message);
    }
  }

  async function newFolder(parent) {
    const name = el("input", { maxlength: "120", placeholder: "Folder name" });
    const choice = await dialog({
      title: "New folder in " + parent,
      body: el("div", { class: "dialog-fields" }, [el("label", {}, ["Name", name])]),
      actions: [{ label: "Create", value: "create", primary: true }],
      validate: () => (name.value.trim() ? null : "Name the folder."),
    });
    if (choice !== "create") return;
    try {
      await request(api + "/folders", jsonRequest("POST", { path: parent + "/" + cleanName(name.value.trim()) }));
      await loadTree();
    } catch (error) {
      showNotice("Couldn't create the folder: " + error.message);
    }
  }

  async function renameItem(item) {
    const parts = item.path.split("/");
    const old = parts.pop();
    const name = el("input", { maxlength: "160" });
    name.value = old;
    const choice = await dialog({
      title: "Rename " + old,
      body: el("div", { class: "dialog-fields" }, [el("label", {}, ["New name", name])]),
      actions: [{ label: "Rename", value: "rename", primary: true }],
      validate: () => (name.value.trim() ? null : "Enter a name."),
    });
    if (choice !== "rename" || name.value.trim() === old) return;
    try {
      const body = await request(api + "/move", jsonRequest("POST", { from: item.path, to: [...parts, cleanName(name.value.trim())].join("/") }));
      await loadTree();
      const entry = findEntry("project", body.item.path);
      if (entry) openFile({ source: "project", path: entry.path, entry });
    } catch (error) {
      showNotice("Couldn't rename it: " + error.message);
    }
  }

  async function removeItem(item) {
    const choice = await dialog({
      title: "Delete " + item.path.split("/").pop() + "?",
      body: el("p", { text: "It moves to the project's trash on the Control Center computer, kept for 30 days." }),
      actions: [{ label: "Delete", value: "delete", danger: true }],
    });
    if (choice !== "delete") return;
    try {
      const headers = item.entry && item.entry.etag ? { "If-Match": item.entry.etag } : {};
      await request(api + "/files/" + pathUrl(item.path), { method: "DELETE", headers });
      await loadTree();
      showOverview();
    } catch (error) {
      showNotice("Couldn't delete it: " + error.message);
    }
  }

  // ---- The project panel -------------------------------------------------------------

  function section(title, note, children, count) {
    return el("details", { class: "inspector-section", open: true }, [
      el("summary", {}, [el("span", { text: title }), count !== undefined ? el("span", { class: "count", text: String(count) }) : ""]),
      note ? el("p", { class: "section-note", text: note }) : "",
      ...children,
    ]);
  }

  function smallButton(label, onClick, primary) {
    const button = el("button", { class: "button small" + (primary ? " primary" : ""), type: "button", text: label });
    button.addEventListener("click", onClick);
    return button;
  }

  function fileLink(path, label) {
    const button = el("button", { class: "link-button", type: "button", text: label || path });
    button.addEventListener("click", () => {
      const entry = findEntry("project", path);
      if (entry) openFile({ source: "project", path: entry.path, entry });
      else showNotice(path + " isn't there yet.");
    });
    return button;
  }

  // Task2 before Task10.
  function naturalOrder(a, b) {
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
  }

  function taskEntries() {
    return entries
      .filter((entry) => entry.kind === "file" && /^Tasks\/[^/]+\.md$/i.test(entry.path))
      .sort((a, b) => naturalOrder(a.name, b.name));
  }

  function taskRow(entry) {
    const open = openRunFor(entry.path);
    const run = smallButton("Run", () => queueTask(entry.path));
    // On a wrapper too: a disabled button shows no tooltip in some browsers.
    const tip = open
      ? entry.name + " is already " + open.status + (open.status === "queued" ? " (waiting for a runner)" : "") + "."
      : "Queue " + entry.name + " for an Automation runner.";
    run.title = tip;
    run.disabled = Boolean(open);
    // "Run with…": this run only, with another model, effort, max turns or budget.
    const runWith = smallButton("…", () => runTaskWith(entry));
    runWith.classList.add("run-with-button");
    runWith.setAttribute("aria-label", "Run " + entry.name + " with…");
    runWith.title = open ? tip : "Run with… another model, effort, max turns or budget, for this run only.";
    runWith.disabled = Boolean(open);
    return el("li", {}, [
      fileLink(entry.path, entry.name),
      el("span", { class: "linked-tools" }, [
        el("span", { class: "outline-kind", text: ago(entry.modified) }),
        el("span", { class: "run-button-wrap run-split", title: tip }, [run, runWith]),
      ]),
    ]);
  }

  function renderTasks() {
    if (!tasksList) return;
    const tasks = taskEntries();
    tasksList.replaceChildren(...(tasks.length ? tasks.map(taskRow) : [el("li", { class: "palette-empty", text: "No tasks yet." })]));
    if (runAllButton) {
      runAllButton.disabled = !tasks.some((entry) => !openRunFor(entry.path));
      runAllButton.title = runAllButton.disabled
        ? tasks.length ? "Every task already has a queued or active run." : "Add a task first."
        : "Queue every task without a queued or active run, in name order.";
    }
  }

  // ---- Runs ----------------------------------------------------------------------
  // A run is one task worked through by an Automation runner. What a runner
  // wrote (its events and summary) is untrusted: it is only ever set as text.

  const RUN_LABELS = {
    queued: "Queued", active: "Active", done: "Done", failed: "Failed", stopped: "Stopped", cancelled: "Cancelled",
    ended_unsafe: "Ended unsafe",
  };
  // While a run is active: what it is doing (see automation_runs.py).
  const PHASE_LABELS = {
    planning: "Planning", awaiting_approval: "Waiting for approval", executing: "Controlling devices", analyzing: "Analyzing",
  };
  const MAX_SUBMISSIONS = 3; // protocols a run may send for approval
  const RUNS_POLL_MS = 5000; // the list, while any run is queued or active
  const LOG_POLL_MS = 2000; // an open run's log
  const RUN_EVENTS_SHOWN = 500; // what GET .../runs/{id} returns at most
  const RUN_ID = /^run-[0-9]{8}-[0-9]{6}-[a-z2-9]{4}$/;

  function isOpen(run) {
    return run.status === "queued" || run.status === "active";
  }

  function openRunFor(path) {
    const lower = path.toLowerCase();
    return runs.find((run) => isOpen(run) && run.task.toLowerCase() === lower) || null;
  }

  function taskName(run) {
    return run.task.replace(/^Tasks\//i, "");
  }

  function bytesText(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(bytes < 10240 ? 1 : 0) + " KB";
    return (bytes / 1048576).toFixed(bytes < 10485760 ? 1 : 0) + " MB";
  }

  // A server time, short when it is today.
  function when(iso) {
    const date = new Date(iso || "");
    if (Number.isNaN(date.getTime())) return "";
    return date.toDateString() === new Date().toDateString() ? date.toLocaleTimeString() : date.toLocaleString();
  }

  function runBadge(status) {
    return el("span", { class: "run-badge run-" + status, text: RUN_LABELS[status] || status });
  }

  function labelFrom(list, id) {
    const found = (list || []).find((item) => item.id === id);
    return (found && found.label) || id;
  }

  // "Sonnet 5.5 · High effort · up to 60 turns · $5.00", for resolved settings or the project's.
  function settingsText(model, effort, turns, budget) {
    return [
      labelFrom(agentInfo && agentInfo.models, model),
      effort ? labelFrom(agentInfo && agentInfo.efforts, effort) + " effort" : "",
      "up to " + plural(turns, "turn"),
      "$" + Number(budget).toFixed(2) + " budget",
    ].filter(Boolean).join(" · ");
  }

  function projectRunSettings() {
    const model = project.run_model || (agentInfo && agentInfo.model) || "";
    const fallback = model ? "" : "the Control Center's model";
    const chosen = agentInfo && (agentInfo.models || []).find((item) => item.id === model);
    const effort = chosen && chosen.effort === false ? null : project.run_effort || (agentInfo && agentInfo.effort) || null;
    return settingsText(model || fallback, effort, project.run_max_turns, project.run_budget_usd);
  }

  function grantLive(run) {
    return Boolean(run.grant && (run.grant.status === "active" || run.grant.status === "ending"));
  }

  function runFacts(run) {
    return [
      run.status === "queued" ? "Waiting for a runner" : "",
      run.status === "active" && run.phase ? PHASE_LABELS[run.phase] || run.phase : "",
      grantLive(run) && run.grant.status === "active" ? "devices held until " + when(run.grant.ends_at) : "",
      run.queued_by ? "by " + run.queued_by : "",
      run.claimed_by ? "on " + run.claimed_by : "",
      run.claimed_at ? "started " + when(run.claimed_at) : "queued " + when(run.queued_at),
      run.finished_at ? (run.status === "cancelled" ? "stopped " : "finished ") + when(run.finished_at) : "",
      run.status === "queued" ? "" : bytesText(run.bytes_uploaded) + " of " + bytesText(run.quota_bytes),
    ].filter(Boolean);
  }

  function runRow(run) {
    const open = el("button", { class: "link-button run-open", type: "button", title: "Show the run's log" }, [
      runBadge(run.status),
      el("span", { class: "run-task", text: taskName(run) }),
    ]);
    open.addEventListener("click", () => showRunLog(run.id));
    const waiting = run.status === "active" && run.phase === "awaiting_approval";
    return el("li", { class: "run-row" + (waiting ? " run-row-waiting" : "") }, [
      el("div", { class: "run-line" }, [
        open,
        el("span", { class: "run-buttons" }, [
          waiting ? smallButton("Review", () => showRunLog(run.id), true) : "",
          isOpen(run) ? smallButton("Stop", () => stopRun(run)) : "",
        ]),
      ]),
      el("span", { class: "outline-kind run-facts", text: runFacts(run).join(" · ") }),
      run.status === "ended_unsafe"
        ? el("span", { class: "run-unsafe-note", text: "Safe state failed: check the devices by hand." })
        : "",
    ]);
  }

  function renderRuns() {
    if (runsNote) runsNote.textContent = "Each run: " + projectRunSettings() + ". Change these under Settings.";
    if (!runsList) return;
    runsList.replaceChildren(
      ...(runs.length
        ? runs.map(runRow)
        : [el("li", {
          class: "palette-empty",
          text: "No runs yet. Runs need an Automation runner (a lab computer that runs Claude Code; coming with Phase 2). " +
            "Until one is connected, queued runs wait here.",
        })])
    );
  }

  function scheduleRuns() {
    window.clearTimeout(runsTimer);
    if (document.visibilityState === "visible" && runs.some(isOpen)) runsTimer = window.setTimeout(loadRuns, RUNS_POLL_MS);
  }

  async function loadRuns() {
    window.clearTimeout(runsTimer);
    try {
      const body = await request(api + "/runs");
      runs = body.runs;
      const signature = JSON.stringify(runs);
      if (signature !== runsSignature) {
        runsSignature = signature;
        renderRuns();
        renderTasks();
      }
    } catch (_ignored) {
      /* the next poll tries again */
    }
    scheduleRuns();
  }

  function author() {
    return store.get("localStorage", AUTHOR_KEY, "");
  }

  // ``settings`` (Run with…): {model, effort, max_turns, max_budget_usd} for this run only.
  async function queueTask(path, settings) {
    const body = { task: path, queued_by: author() };
    if (settings) body.settings = settings;
    try {
      await request(api + "/runs", jsonRequest("POST", body));
      toast(path.replace(/^Tasks\//i, "") + " queued");
    } catch (error) {
      showNotice("Couldn't queue " + path + ": " + error.message);
    }
    await loadRuns();
  }

  async function runTaskWith(entry) {
    const fields = await runSettingsFields(project);
    const choice = await dialog({
      title: "Run " + entry.name + " with…",
      body: el("div", { class: "dialog-fields" }, [
        el("p", {
          class: "section-note",
          text: "For this run only; the project's settings (" + projectRunSettings() + ") stay as they are. " +
            "Run uses those; change them under Settings.",
        }),
        fields.grid,
      ]),
      actions: [{ label: "Queue run", value: "run", primary: true }],
      validate: () => fields.validate(),
    });
    if (choice !== "run") return;
    const chosen = fields.values();
    // "Default" here means the Control Center's default, even when the project chose another
    // (the server reads a missing value as the project's).
    await queueTask(entry.path, {
      model: chosen.run_model || (agentInfo && agentInfo.model) || null,
      effort: chosen.run_effort || (agentInfo && agentInfo.effort) || null,
      max_turns: chosen.run_max_turns,
      max_budget_usd: chosen.run_budget_usd,
    });
  }

  async function runAll() {
    await loadRuns();
    const waiting = taskEntries().filter((entry) => !openRunFor(entry.path));
    if (!waiting.length) {
      showNotice("Every task already has a queued or active run.");
      return;
    }
    const choice = await dialog({
      title: "Run all tasks?",
      body: el("div", { class: "dialog-fields" }, [
        el("p", { text: "Each becomes its own run, queued in this order (" + projectRunSettings() + " each):" }),
        el("ol", { class: "run-queue-list" }, waiting.map((entry) => el("li", { text: entry.name }))),
      ]),
      actions: [{ label: "Queue " + plural(waiting.length, "run"), value: "run", primary: true }],
    });
    if (choice !== "run") return;
    let queued = 0;
    const failed = [];
    for (const entry of waiting) { // one at a time: queue order is the order runners take them
      try {
        await request(api + "/runs", jsonRequest("POST", { task: entry.path, queued_by: author() }));
        queued++;
      } catch (error) {
        failed.push(entry.name + ": " + error.message);
      }
    }
    await loadRuns();
    if (failed.length) showNotice("Not queued: " + failed.join("; "));
    else toast(plural(queued, "run") + " queued");
  }

  async function stopRun(run) {
    const choice = await dialog({
      title: "Stop " + taskName(run) + "?",
      body: el("p", {
        text: run.status === "active"
          ? (grantLive(run) ? "Its device grant ends first: the safe state is applied and the devices are released. " : "") +
            "The runner is told on its next request and stops the agent. Files it already uploaded stay."
          : "The run leaves the queue before a runner starts it.",
      }),
      actions: [{ label: "Stop run", value: "stop", danger: true }],
    });
    if (choice !== "stop") return;
    try {
      await request(api + "/runs/" + encodeURIComponent(run.id) + "/cancel", jsonRequest("POST", { cancelled_by: author() }));
      toast("Run stopped");
    } catch (error) {
      showNotice("Couldn't stop it: " + error.message);
    }
    await loadRuns();
    if (runLog && runLog.id === run.id) refreshLog();
  }

  // ---- A run's log (in the viewer) -------------------------------------------------

  async function showRunLog(runId) {
    const head = el("div", { class: "viewer-head" }, [el("div", { class: "viewer-path" }, [el("span", { class: "viewer-root", text: "Runs" })])]);
    const body = el("div", { class: "viewer-body run-log" }, [el("p", { class: "viewer-message", text: "Loading…" })]);
    const node = document.createDocumentFragment();
    node.append(head, body);
    if (!(await viewer.showNode(node))) return;
    stopLog();
    runLog = { id: runId, head, body, timer: 0, signature: "", protocol: null };
    tree.select("", "");
    window.history.replaceState(null, "", "#run=" + encodeURIComponent(runId));
    await refreshLog();
  }

  function stopLog() {
    if (runLog) window.clearTimeout(runLog.timer);
    runLog = null;
  }

  function scheduleLog() {
    const log = runLog;
    if (!log) return;
    window.clearTimeout(log.timer);
    if (document.visibilityState === "visible" && log.body.isConnected) log.timer = window.setTimeout(refreshLog, LOG_POLL_MS);
  }

  async function refreshLog() {
    const log = runLog;
    if (!log) return;
    window.clearTimeout(log.timer);
    if (!log.body.isConnected) {
      stopLog(); // another file is open now
      return;
    }
    let data;
    try {
      data = await request(api + "/runs/" + encodeURIComponent(log.id));
    } catch (error) {
      if (log !== runLog || !log.body.isConnected) return;
      if (error.status === 404) {
        log.body.replaceChildren(el("p", { class: "viewer-message error", text: "There is no such run." }));
      } else {
        scheduleLog(); // keep what is shown; try again
      }
      return;
    }
    if (log !== runLog || !log.body.isConnected) return;
    const signature = JSON.stringify(data);
    if (signature !== log.signature) {
      log.signature = signature;
      renderLog(log, data.run, data.events);
    }
    if (isOpen(data.run)) scheduleLog();
    const listed = runs.find((run) => run.id === data.run.id);
    if (listed && listed.status !== data.run.status) loadRuns();
  }

  function renderLog(log, run, events) {
    log.head.replaceChildren(
      el("div", { class: "viewer-path" }, [
        el("span", { class: "viewer-root", text: "Runs" }),
        el("span", { class: "viewer-file", text: taskName(run) }),
      ]),
      el("div", { class: "viewer-meta" }, [
        runBadge(run.status),
        run.status === "active" && run.phase
          ? el("span", { class: "run-phase run-phase-" + run.phase, text: PHASE_LABELS[run.phase] || run.phase })
          : "",
        el("span", { text: run.id }),
      ]),
      el("div", { class: "viewer-actions" }, [isOpen(run) ? smallButton("Stop", () => stopRun(run)) : ""])
    );
    const box = log.body;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const facts = runFacts(run);
    if (run.status === "active" && run.last_seen_at) facts.push("last heard from " + when(run.last_seen_at));
    if (run.settings) {
      facts.push(settingsText(run.settings.model, run.settings.effort, run.settings.max_turns, run.settings.max_budget_usd));
    }
    if (run.files_uploaded) facts.push(plural(run.files_uploaded, "file") + " uploaded");
    const children = [el("p", { class: "run-log-facts", text: facts.join(" · ") })];
    if (run.status === "ended_unsafe" || (run.grant && run.grant.status === "ended_unsafe")) children.unshift(unsafeBanner());
    if (run.grant) children.push(grantCard(run));
    if (run.protocol) children.push(protocolSection(log, run));
    if (run.summary) {
      children.push(el("h3", { class: "run-log-heading", text: "Summary" }), el("pre", { class: "run-log-text", text: run.summary }));
    }
    children.push(el("h3", { class: "run-log-heading", text: "Log" }));
    if (events.length >= RUN_EVENTS_SHOWN) {
      children.push(el("p", { class: "viewer-message", text: "Only the latest " + RUN_EVENTS_SHOWN + " events are shown." }));
    }
    if (events.length) {
      children.push(el("ol", { class: "run-events" }, events.map((event) =>
        el("li", { class: "run-event" }, [
          el("time", { class: "run-event-time", datetime: String(event.at || ""), title: String(event.at || ""), text: when(event.at) }),
          el("span", { class: "run-event-kind", text: String(event.kind || "") }),
          el("span", { class: "run-event-text", text: String(event.text || "") }),
        ])
      )));
    } else {
      children.push(el("p", {
        class: "viewer-message",
        text: run.status === "queued"
          ? "Waiting for a runner. Runs need an Automation runner (coming with Phase 2); until one is connected, this run stays queued."
          : "Nothing logged.",
      }));
    }
    // The approval card (and an edit in it) is the same node across refreshes; keep the field
    // being typed in focused, and the scroll position, while the runner's heartbeats refresh the log.
    const active = document.activeElement;
    const focused = active && box.contains(active) ? active : null;
    let selection = null;
    try {
      if (focused && typeof focused.selectionStart === "number") selection = [focused.selectionStart, focused.selectionEnd];
    } catch (_ignored) {
      /* number inputs have no selection */
    }
    const top = box.scrollTop;
    box.replaceChildren(...children);
    if (atBottom) box.scrollTop = box.scrollHeight;
    else box.scrollTop = top;
    if (focused && focused.isConnected) {
      focused.focus({ preventScroll: true });
      try {
        if (selection) focused.setSelectionRange(selection[0], selection[1]);
      } catch (_ignored) {
        /* not a text field */
      }
    }
  }

  // ---- Protocols, approval and the device grant ---------------------------------------
  // The review is built only from what the server returns as "review" (made from the
  // normalized protocol and the device contracts). The agent's prose is shown apart,
  // as plain text in a box that says who wrote it. Nothing here is ever set as HTML.

  let signedInUser = null; // the Caddy login, when there is one (then it is the approver)

  function num(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
    return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(10)));
  }

  function minutesText(minutes) {
    return num(minutes) + " min";
  }

  function unsafeBanner() {
    return el("div", { class: "run-unsafe-banner", role: "alert" }, [
      el("strong", { text: "Ended unsafe. " }),
      "The grant's safe state could not be applied when it ended, so the devices may still be as the agent left " +
        "them. Check them by hand before anyone uses them.",
    ]);
  }

  // "12:34 left", kept current by tickCountdowns().
  function countdownText(endsAt) {
    const left = Math.round((new Date(endsAt).getTime() - Date.now()) / 1000);
    if (!Number.isFinite(left)) return "";
    if (left <= 0) return "time is up";
    const hours = Math.floor(left / 3600);
    const minutes = Math.floor((left % 3600) / 60);
    const seconds = String(left % 60).padStart(2, "0");
    return (hours ? hours + ":" + String(minutes).padStart(2, "0") : String(minutes)) + ":" + seconds + " left";
  }

  function tickCountdowns() {
    for (const node of document.querySelectorAll(".grant-countdown[data-ends]")) {
      node.textContent = countdownText(node.getAttribute("data-ends"));
    }
  }

  function grantCard(run) {
    const grant = run.grant;
    const live = grant.status === "active";
    const facts = [
      grant.devices && grant.devices.length ? "Devices: " + grant.devices.join(", ") : "",
      grant.approved_by ? "approved by " + grant.approved_by : "",
      grant.starts_at ? "from " + when(grant.starts_at) : "",
      grant.ends_at ? (live ? "until " : "planned until ") + when(grant.ends_at) : "",
      plural(grant.calls || 0, "device call"),
      grant.refused_calls ? plural(grant.refused_calls, "refused call") : "",
    ].filter(Boolean);
    const labels = {
      active: "Grant active", ending: "Ending: applying the safe state", ended: "Grant ended",
      ended_unsafe: "Grant ended UNSAFE", interrupted: "Grant interrupted (the Control Center restarted)",
    };
    const stop = live ? smallButton("Stop now", () => stopGrant(run)) : "";
    if (stop) stop.classList.add("danger");
    return el("section", { class: "grant-card grant-" + grant.status }, [
      el("div", { class: "grant-head" }, [
        el("strong", { text: labels[grant.status] || grant.status }),
        live && grant.ends_at ? el("span", { class: "grant-countdown", "data-ends": grant.ends_at, text: countdownText(grant.ends_at) }) : "",
        stop,
      ]),
      el("p", { class: "grant-facts", text: facts.join(" · ") }),
      grant.ended_reason
        ? el("p", { class: "grant-facts", text: "Ended: " + grant.ended_reason + (grant.ended_at ? " (" + when(grant.ended_at) + ")" : "") })
        : "",
    ]);
  }

  async function stopGrant(run) {
    const choice = await dialog({
      title: "Stop the devices now?",
      body: el("p", {
        text: "The agent's device calls are refused from now on, the protocol's safe state is applied, and the " +
          "devices are released. The run goes on to its analysis.",
      }),
      actions: [{ label: "Stop now", value: "stop", danger: true }],
    });
    if (choice !== "stop") return;
    const body = author() ? { stopped_by: author() } : {};
    try {
      await request(api + "/runs/" + encodeURIComponent(run.id) + "/grant/stop", jsonRequest("POST", body));
      toast("Grant stopped");
    } catch (error) {
      showNotice("Couldn't stop the grant: " + error.message);
    }
    await loadRuns();
    if (runLog && runLog.id === run.id) refreshLog();
  }

  // The protocol part of a run's log. Kept across log refreshes (the runner heartbeats
  // every few seconds) while the same submission is in the same state, so a half-typed
  // duration or note isn't lost.
  function protocolSection(log, run) {
    const key = [run.id, run.protocol.sha256, run.protocol.status, run.status, run.phase].join("|");
    if (log.protocol && log.protocol.key === key) return log.protocol.node;
    const node = el("section", { class: "protocol-section" }, [
      el("h3", { class: "run-log-heading", text: "Protocol" }),
      el("p", { class: "viewer-message", text: "Loading the protocol…" }),
    ]);
    log.protocol = { key, node };
    loadProtocol(log, run, key);
    return node;
  }

  async function loadProtocol(log, run, key) {
    let data;
    try {
      data = await request(api + "/runs/" + encodeURIComponent(run.id) + "/protocol");
    } catch (error) {
      if (log.protocol && log.protocol.key === key) {
        log.protocol.node.replaceChildren(
          el("h3", { class: "run-log-heading", text: "Protocol" }),
          el("p", { class: "viewer-message error", text: "Couldn't load the protocol: " + error.message })
        );
        log.protocol.key = ""; // try again on the next refresh
      }
      return;
    }
    if (!log.protocol || log.protocol.key !== key) return; // the run moved on meanwhile
    log.protocol.node.replaceChildren(...renderProtocol(run, data));
  }

  const PROTOCOL_STATUS = { submitted: "waiting for approval", approved: "approved", rejected: "rejected" };

  function renderProtocol(run, data) {
    const record = run.protocol;
    const review = data.review;
    const parts = [
      el("h3", { class: "run-log-heading", text: "Protocol" }),
      el("p", { class: "run-log-facts" }, [
        "Submission " + data.submission + " of " + MAX_SUBMISSIONS + " · " + (PROTOCOL_STATUS[data.status] || data.status) +
          " · sha256 ",
        el("code", { class: "protocol-sha", title: data.sha256, text: data.sha256.slice(0, 16) + "…" }),
        record.reviewed_by ? " · reviewed by " + record.reviewed_by + (record.reviewed_at ? " " + when(record.reviewed_at) : "") : "",
        record.approved_minutes ? " · granted " + minutesText(record.approved_minutes) : "",
      ]),
    ];
    if (data.status === "rejected" && record.note) {
      parts.push(el("div", { class: "protocol-note" }, [
        el("span", { class: "protocol-note-label", text: "Reviewer's note (sent to the agent)" }),
        el("pre", { class: "run-log-text", text: record.note }),
      ]));
    }
    if (data.status === "submitted" && data.phase === "awaiting_approval" && data.run_status === "active") {
      parts.push(approvalCard(run, data));
    }
    if (data.approved_review && Array.isArray(data.changes)) {
      // Approved with the approver's edits: their version is what the grant enforces.
      parts.push(el("div", { class: "protocol-edited" }, [
        el("strong", { text: "Edited by " + (data.edited_by || "the approver") + " before approval" }),
        " · approved version sha256 ",
        el("code", { class: "protocol-sha", title: data.approved_sha256, text: String(data.approved_sha256).slice(0, 16) + "…" }),
        el("ul", { class: "edit-changes" }, data.changes.map((text) => el("li", {}, [el("code", { text })]))),
      ]));
      parts.push(el("h4", { class: "run-log-heading", text: "The approved version (what the grant enforces)" }), reviewNode(data.approved_review));
      parts.push(el("details", { class: "protocol-submitted" }, [
        el("summary", { text: "As the agent submitted it" }),
        reviewNode(review),
      ]));
    } else {
      parts.push(reviewNode(review));
    }
    parts.push(el("div", { class: "agent-prose" }, [
      el("div", { class: "agent-prose-label", text: "Written by the agent: its explanation, not checked by the Control Center" }),
      data.prose
        ? el("pre", { class: "agent-prose-text", text: data.prose })
        : el("p", { class: "viewer-message", text: "The agent sent no explanation." }),
    ]));
    return parts;
  }

  function boundText(arg) {
    // Every bounded argument is sent in every call (leaving one out would drive the driver's default).
    const always = arg.always_sent ? "; always sent" : "";
    if (arg.choices) {
      let choices = arg.choices.map((choice) => JSON.stringify(choice)).join(", ");
      if (arg.offered) {
        choices += " (" + (arg.limit_source === "site" ? "site allows " : "device offers ") +
          arg.offered.map((choice) => JSON.stringify(choice)).join(", ") + ")";
      }
      return choices + always;
    }
    const unit = arg.unit ? " " + arg.unit : "";
    let text = num(arg.min) + " to " + num(arg.max) + unit;
    if (arg.max_step !== null && arg.max_step !== undefined) {
      text += ", steps of at most " + num(arg.max_step) + unit;
      if (arg.step_from) text += " (the first measured from " + arg.step_from + ")";
    } else {
      text += ", any step";
    }
    // Where the hard limit comes from: the lab's site limit (Lab PC inventory) or the driver's own.
    const limit = arg.limit || arg.driver_limit;
    if (limit) {
      text += " (" + (arg.limit_source === "site" ? "site limit " : "driver allows ") + num(limit[0]) + " to " + num(limit[1]) + ")";
    }
    return text + always;
  }

  function reviewNode(review) {
    const devices = review.devices.map((device) => {
      const rows = device.methods.map((method) => {
        const args = method.args.map((arg) =>
          el("div", { class: "review-arg" }, [el("code", { text: arg.name }), ": " + boundText(arg)])
        );
        for (const name of method.never_sent) {
          args.push(el("div", { class: "review-arg muted" }, [el("code", { text: name }), ": never sent"]));
        }
        if (!args.length) args.push(el("span", { class: "muted", text: "no arguments" }));
        const limits = ["at most " + plural(method.max_calls, "call"), num(method.max_rate_hz) + " per second at most"];
        if (method.stream) {
          limits.push("streams of up to " + num(method.stream.max_samples) + " samples at up to " +
            num(method.stream.max_sample_rate_hz) + " per second");
        }
        return el("tr", {}, [
          el("td", {}, [el("code", { class: "review-method", text: method.name }), el("div", { class: "muted", text: method.kind + (method.unit ? ", " + method.unit : "") })]),
          el("td", {}, args),
          el("td", { text: limits.join("; ") }),
        ]);
      });
      return el("div", { class: "review-device" }, [
        el("h4", {}, [
          el("code", { text: device.device_id }),
          " · " + device.type_label + " · " + device.state,
        ]),
        el("table", { class: "review-table" }, [
          el("thead", {}, [el("tr", {}, [
            el("th", { text: "Method" }), el("th", { text: "Arguments it may send" }), el("th", { text: "Limits" }),
          ])]),
          el("tbody", {}, rows),
        ]),
      ]);
    });
    const safe = review.safe_state.length
      ? el("ol", { class: "review-safe" }, review.safe_state.map((call) => el("li", {}, [el("code", { text: call.text })])))
      : el("p", { class: "review-warning", text: "No safe state: nothing is set when the grant ends." });
    return el("div", { class: "protocol-review" }, [
      el("div", { class: "review-title" }, [
        el("span", { class: "muted", text: "Title: " }), el("span", { text: review.title }),
        el("span", { class: "muted", text: " · for " + review.task + " · asks for " + minutesText(review.duration_minutes) }),
      ]),
      ...devices,
      el("h4", { text: "Safe state (applied when the grant ends, in this order)" }),
      safe,
      review.warnings.length
        ? el("ul", { class: "review-warnings" }, review.warnings.map((text) => el("li", { text })))
        : "",
    ]);
  }

  function approvalCard(run, data) {
    const requested = data.protocol.duration_minutes;
    const sha256 = data.sha256; // the submission this card shows; an edit starts from it
    const runApi = api + "/runs/" + encodeURIComponent(run.id) + "/protocol/";
    const minutes = el("input", { type: "number", min: "1", max: String(requested), step: "any", value: String(requested) });
    const name = el("input", { type: "text", maxlength: "64", value: author(), autocomplete: "name" });
    const nameField = el("label", { class: "field approval-name" }, [el("span", { class: "field-label", text: "Your name" }), name]);
    if (signedInUser) nameField.hidden = true;
    window.LabNet.me.then((me) => {
      if (me && me.user) {
        signedInUser = me.user;
        nameField.hidden = true;
      }
    }).catch(() => {});
    const note = el("textarea", { rows: "3", maxlength: "2000", placeholder: "What should the agent change? (needed to reject; the agent reads it)" });
    const error = el("p", { class: "dialog-error", role: "alert", hidden: true });
    const approveButton = el("button", { class: "button primary", type: "button", text: "Approve" });
    const rejectButton = el("button", { class: "button danger", type: "button", text: "Reject" });
    const editButton = el("button", { class: "button", type: "button", text: "Edit…", title: "Change the bounds, caps, devices, duration or safe state, then approve your version" });
    const editorHost = el("div", { class: "protocol-editor-host" });
    const deviceList = data.review.devices.map((device) => device.device_id).join(", ");
    let editor = null; // the approver's edit, while "Edit…" is open

    function fail(text) {
      error.textContent = text;
      error.hidden = false;
    }

    function who() {
      return signedInUser || name.value.trim();
    }

    function busy(on) {
      approveButton.disabled = rejectButton.disabled = editButton.disabled = on;
    }

    function problemsText(problems) {
      return problems.map((item) => (item.path ? item.path + ": " : "") + item.message).join(" ");
    }

    async function send(path, body, done) {
      busy(true);
      try {
        await request(runApi + path, jsonRequest("POST", body));
        if (!signedInUser && name.value.trim()) store.set("localStorage", AUTHOR_KEY, name.value.trim());
        toast(done);
        await loadRuns();
        if (runLog && runLog.id === run.id) refreshLog();
      } catch (error_) {
        let text = error_.message;
        const problems = error_.body && Array.isArray(error_.body.problems) ? error_.body.problems : [];
        if (problems.length && editor && path === "approve") {
          editor.showProblems(problems); // an edit the server refused: shown beside the fields
        } else if (problems.length) {
          text += " " + problemsText(problems);
        }
        fail(text);
        busy(false);
        if (error_.status === 409 && runLog && runLog.id === run.id) refreshLog();
      }
    }

    function setEditing(next) {
      editor = next;
      editorHost.replaceChildren(...(editor ? [editor.node] : []));
      editButton.textContent = editor ? "Discard edits" : "Edit…";
      approveButton.textContent = editor ? "Review changes…" : "Approve";
      error.hidden = true;
    }

    editButton.addEventListener("click", async () => {
      if (!editor) {
        setEditing(protocolEditor(data));
        return;
      }
      const choice = await dialog({
        title: "Discard your edits?",
        body: el("p", { text: "The card goes back to the protocol as the agent submitted it." }),
        actions: [{ label: "Discard edits", value: "discard", danger: true }],
      });
      if (choice === "discard") setEditing(null);
    });

    // Edited: the server checks the edit (as it checks the agent's), lists what changed, and
    // the approval sends exactly the version it previewed; the server checks it once more.
    async function approveEdited(value) {
      const protocol = editor.build();
      busy(true);
      let preview;
      try {
        preview = await request(runApi + "preview", jsonRequest("POST", { sha256, protocol }));
      } catch (error_) {
        fail("Couldn't check your edits: " + error_.message);
        if (error_.status === 409 && runLog && runLog.id === run.id) refreshLog();
        return;
      } finally {
        busy(false);
      }
      editor.showProblems(preview.problems || []);
      if (preview.problems && preview.problems.length) {
        fail("Your edited protocol has " + plural(preview.problems.length, "problem") + ", listed in your edit. " +
          "Fix them, then review again.");
        return;
      }
      if (value > preview.normalized.duration_minutes) {
        fail("Your version asks for " + minutesText(preview.normalized.duration_minutes) + "; grant at most that.");
        return;
      }
      const unchanged = preview.sha256 === sha256;
      const granted = preview.normalized.devices.map((device) => device.device_id).join(", ");
      const choice = await dialog({
        title: unchanged ? "Approve this protocol?" : "Approve your edited protocol?",
        wide: !unchanged,
        body: el("div", { class: "dialog-fields" }, unchanged
          ? [el("p", { text: "Your edits change nothing, so this approves the protocol as the agent submitted it." })]
          : [
            el("p", { text: "The Control Center checked your version and found " + plural(preview.changes.length, "change") +
              " from the agent's. The agent is told about each one:" }),
            el("ul", { class: "edit-changes" }, preview.changes.map((text) => el("li", {}, [el("code", { text })]))),
            el("p", {
              text: "The agent may then control " + granted + " for " + minutesText(value) + ", calling only the " +
                "methods in your version, within its bounds. Your version's sha256 " + preview.sha256.slice(0, 16) + "…",
            }),
          ]),
        actions: [{ label: "Approve for " + minutesText(value), value: "approve", primary: true }],
      });
      if (choice !== "approve") return;
      const body = { sha256, duration_minutes: value, approved_by: who() };
      if (!unchanged) Object.assign(body, { protocol: preview.normalized, edited_sha256: preview.sha256 });
      await send("approve", body, unchanged ? "Approved: the grant has started" : "Approved with your edits: the grant has started");
    }

    approveButton.addEventListener("click", async () => {
      error.hidden = true;
      const value = Number(minutes.value);
      const longest = editor ? editor.duration() : requested;
      if (!minutes.value || !Number.isFinite(value) || value < 1) {
        fail("Grant at least 1 minute.");
        return;
      }
      if (typeof longest === "number" && value > longest) {
        fail("Grant between 1 and " + num(longest) + " minutes: an approval can shorten the time the protocol asks for, " +
          "never lengthen it" + (editor ? " (change \"Asks for\" in your edit to ask for more)." : "."));
        return;
      }
      if (!who()) {
        fail("Type your name: an approval records who gave it.");
        return;
      }
      if (editor) {
        await approveEdited(value);
        return;
      }
      const choice = await dialog({
        title: "Approve this protocol?",
        body: el("p", {
          text: "The agent may then control " + deviceList + " for " + minutesText(value) + ", calling only the methods " +
            "in the review, within its bounds. Protocol sha256 " + sha256.slice(0, 16) + "…",
        }),
        actions: [{ label: "Approve for " + minutesText(value), value: "approve", primary: true }],
      });
      if (choice !== "approve") return;
      await send("approve", { sha256, duration_minutes: value, approved_by: who() }, "Approved: the grant has started");
    });

    rejectButton.addEventListener("click", async () => {
      error.hidden = true;
      const text = note.value.trim();
      if (!text) {
        fail("Write a note for the agent first: what should change?");
        note.focus();
        return;
      }
      await send("reject", { sha256, note: text, rejected_by: who() }, "Rejected: the agent gets your note");
    });

    return el("section", { class: "approval-card" }, [
      el("h3", { text: "Approval needed" }),
      el("p", {
        text: "The agent asks to control " + deviceList + ". If you approve, it may call only the methods below, " +
          "within the bounds shown, until the time runs out; then the safe state is applied and the devices are released. " +
          "Edit… lets you change the protocol and approve your version instead; the agent is told what you changed.",
      }),
      el("dl", { class: "approval-facts" }, [
        el("dt", { text: "Protocol" }), el("dd", {}, [el("code", { title: sha256, text: "sha256 " + sha256.slice(0, 16) + "…" })]),
        el("dt", { text: "Asks for" }), el("dd", { text: minutesText(requested) + " (at most " + minutesText(data.max_minutes) + " here)" }),
      ]),
      editorHost,
      el("div", { class: "approval-fields" }, [
        el("label", { class: "field" }, [
          el("span", { class: "field-label", text: "Grant for (minutes)" }), minutes,
          el("span", { class: "field-help", text: "You can shorten it, not lengthen it." }),
        ]),
        nameField,
      ]),
      el("label", { class: "field" }, [el("span", { class: "field-label", text: "Note for the agent" }), note]),
      error,
      el("div", { class: "approval-actions" }, [editButton, el("span", { class: "approval-spacer" }), rejectButton, approveButton]),
    ]);
  }

  // ---- Edit and approve: the approver's own version of the protocol -------------------
  // Plain inputs over the normalized protocol: each argument's range, step or choices, the
  // call, rate and stream caps, removing a method or a device, the duration, and the safe
  // state (values, remove, reorder, add a call the protocol allows). Nothing is checked
  // here that the server doesn't check again: POST …/protocol/preview validates the edit
  // exactly like the agent's and lists the changes; approving sends what it previewed.

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  // A number input's value: null when empty or not a number (the server says what's wrong).
  function numberFrom(input) {
    const text = input.value.trim();
    if (!text) return null;
    const value = Number(text);
    return Number.isFinite(value) ? value : null;
  }

  function protocolEditor(data) {
    let draft = clone(data.protocol);
    let removedDevices = new Set();
    let removedMethods = new Set(); // "device|method"
    const types = {}; // "device|method|arg" → "float", "int", "bool", "str" (from the server's review)
    const units = {};
    for (const device of data.review.devices) {
      for (const method of device.methods) {
        for (const arg of method.args) {
          types[device.device_id + "|" + method.name + "|" + arg.name] = arg.type;
          if (arg.unit) units[device.device_id + "|" + method.name + "|" + arg.name] = arg.unit;
        }
      }
    }
    const node = el("div", { class: "protocol-editor" });
    const problemsList = el("ul", { class: "editor-problems", role: "alert", hidden: true });
    const jsonBox = el("textarea", { class: "editor-json", rows: "14", spellcheck: "false" });
    const jsonError = el("p", { class: "dialog-error", hidden: true });

    function gone(deviceId, method) {
      return removedDevices.has(deviceId) || removedMethods.has(deviceId + "|" + method);
    }

    function isStream(method) {
      return Boolean(method && method.stream);
    }

    // The edited protocol as the server gets it: removed devices and methods left out, and the
    // safe-state calls to them with them (the server's list of changes names those too).
    function build() {
      const protocol = clone(draft);
      protocol.devices = (protocol.devices || []).filter((device) => !removedDevices.has(device.device_id)).map((device) => {
        const methods = {};
        for (const [name, method] of Object.entries(device.methods || {})) {
          if (!removedMethods.has(device.device_id + "|" + name)) methods[name] = method;
        }
        return { ...device, methods };
      });
      protocol.safe_state = (protocol.safe_state || []).filter((call) => !gone(call.device_id, call.method));
      return protocol;
    }

    function numberInput(object, key, options) {
      const value = object[key];
      const input = el("input", {
        type: "number",
        step: options.step || "any",
        value: value === null || value === undefined ? "" : String(value),
        placeholder: options.placeholder || "",
        "aria-label": options.label,
      });
      input.addEventListener("input", () => {
        object[key] = numberFrom(input);
      });
      return el("label", { class: "editor-field" }, [el("span", { text: options.label }), input]);
    }

    function boolChoices(bound) {
      const boxes = [true, false].map((choice) => {
        const box = el("input", { type: "checkbox", checked: bound.choices.includes(choice) });
        box.addEventListener("change", () => {
          bound.choices = [true, false].filter((_item, index) => boxes[index].checked);
          renderSafe();
        });
        return box;
      });
      return el("span", { class: "editor-field" }, [
        el("span", { text: "choices" }),
        el("label", { class: "editor-check" }, [boxes[0], " true"]),
        el("label", { class: "editor-check" }, [boxes[1], " false"]),
      ]);
    }

    function textChoices(bound) {
      const input = el("input", { type: "text", value: bound.choices.join(", "), spellcheck: "false", "aria-label": "choices" });
      input.addEventListener("input", () => {
        bound.choices = input.value.split(",").map((item) => item.trim()).filter(Boolean);
      });
      input.addEventListener("change", renderSafe);
      return el("label", { class: "editor-field wide" }, [el("span", { text: "choices (comma-separated)" }), input]);
    }

    function argRow(deviceId, methodName, argName, bound) {
      const key = deviceId + "|" + methodName + "|" + argName;
      const type = types[key] || ("choices" in bound ? (typeof bound.choices[0] === "boolean" ? "bool" : "str") : "float");
      const unit = units[key] ? " (" + units[key] + ")" : "";
      const whole = type === "int" ? "1" : "any";
      let fields;
      if ("choices" in bound) {
        fields = [type === "bool" ? boolChoices(bound) : textChoices(bound)];
      } else {
        fields = [
          numberInput(bound, "min", { label: "min" + unit, step: whole }),
          numberInput(bound, "max", { label: "max" + unit, step: whole }),
          numberInput(bound, "max_step", { label: "max step", placeholder: "any step" }),
        ];
      }
      return el("div", { class: "editor-arg" }, [el("code", { class: "editor-arg-name", text: argName }), ...fields]);
    }

    function methodBlock(device, name) {
      const method = device.methods[name];
      const removed = gone(device.device_id, name);
      const toggle = smallButton(removedMethods.has(device.device_id + "|" + name) ? "Restore" : "Remove", () => {
        const key = device.device_id + "|" + name;
        if (removedMethods.has(key)) removedMethods.delete(key);
        else removedMethods.add(key);
        render();
      });
      toggle.disabled = removedDevices.has(device.device_id);
      const parts = [
        el("div", { class: "editor-method-head" }, [
          el("code", { class: "review-method", text: name }),
          removed ? el("span", { class: "muted", text: "removed: the agent may not call it" }) : "",
          toggle,
        ]),
      ];
      if (!removed) {
        for (const [argName, bound] of Object.entries(method.args || {})) parts.push(argRow(device.device_id, name, argName, bound));
        const caps = [
          numberInput(method, "max_calls", { label: "max calls", step: "1" }),
          numberInput(method, "max_rate_hz", { label: "max calls per second" }),
        ];
        if (method.stream) {
          caps.push(
            numberInput(method.stream, "max_sample_rate_hz", { label: "max sample rate (Hz)" }),
            numberInput(method.stream, "max_samples", { label: "max samples", step: "1" })
          );
        }
        parts.push(el("div", { class: "editor-arg editor-caps" }, caps));
      }
      return el("div", { class: "editor-method" + (removed ? " editor-removed" : "") }, parts);
    }

    function deviceBlock(device) {
      const removed = removedDevices.has(device.device_id);
      const toggle = smallButton(removed ? "Restore device" : "Remove device", () => {
        if (removed) removedDevices.delete(device.device_id);
        else removedDevices.add(device.device_id);
        render();
      });
      return el("div", { class: "editor-device" + (removed ? " editor-removed" : "") }, [
        el("h4", {}, [
          el("code", { text: device.device_id }), " · " + device.device_type,
          removed ? el("span", { class: "muted", text: " · removed: no calls to it at all" }) : "",
          toggle,
        ]),
        ...(removed ? [] : Object.keys(device.methods || {}).sort().map((name) => methodBlock(device, name))),
      ]);
    }

    // ---- the safe state
    const safeList = el("ol", { class: "editor-safe" });
    const addSelect = el("select", { "aria-label": "Add a safe-state call" });
    const addButton = smallButton("Add call", () => {
      const [deviceId, name] = addSelect.value.split("|");
      const device = (draft.devices || []).find((item) => item.device_id === deviceId);
      const method = device && device.methods[name];
      if (!method) return;
      const args = {};
      for (const [argName, bound] of Object.entries(method.args || {})) {
        if ("choices" in bound) {
          args[argName] = bound.choices[0];
        } else {
          const low = bound.min === null ? 0 : bound.min;
          args[argName] = types[deviceId + "|" + name + "|" + argName] === "int" ? Math.ceil(low) : low;
        }
      }
      draft.safe_state = draft.safe_state || [];
      draft.safe_state.push({ device_id: deviceId, method: name, args });
      renderSafe();
    });

    function valueInput(call, argName, bound, type) {
      const current = call.args[argName];
      if (bound && "choices" in bound) {
        const options = bound.choices.slice();
        if (current !== undefined && !options.some((item) => item === current)) options.push(current);
        const select = el("select", { "aria-label": argName }, options.map((item) =>
          el("option", { value: JSON.stringify(item), text: JSON.stringify(item) })
        ));
        select.value = JSON.stringify(current);
        select.addEventListener("change", () => {
          call.args[argName] = JSON.parse(select.value);
        });
        return select;
      }
      const input = el("input", {
        type: "number", step: type === "int" ? "1" : "any", "aria-label": argName,
        value: typeof current === "number" ? String(current) : "",
      });
      input.addEventListener("input", () => {
        call.args[argName] = numberFrom(input);
      });
      return input;
    }

    function renderSafe() {
      const calls = draft.safe_state || [];
      safeList.replaceChildren(...calls.map((call, index) => {
        const device = (draft.devices || []).find((item) => item.device_id === call.device_id);
        const method = device && device.methods[call.method];
        const dropped = gone(call.device_id, call.method);
        const args = Object.keys(call.args || {}).map((argName) => el("label", { class: "editor-field" }, [
          el("span", { text: argName }),
          valueInput(call, argName, method && method.args[argName], types[call.device_id + "|" + call.method + "|" + argName]),
        ]));
        const up = smallButton("↑", () => {
          [calls[index - 1], calls[index]] = [calls[index], calls[index - 1]];
          renderSafe();
        });
        up.disabled = index === 0;
        up.title = "Move earlier (calls run in this order)";
        return el("li", { class: "editor-safe-call" + (dropped ? " editor-removed" : "") }, [
          el("code", { text: call.device_id + "." + call.method }),
          dropped ? el("span", { class: "muted", text: "dropped: its method is removed" }) : "",
          ...(dropped ? [] : args),
          el("span", { class: "editor-safe-buttons" }, [
            up,
            smallButton("Remove", () => {
              calls.splice(index, 1);
              renderSafe();
            }),
          ]),
        ]);
      }));
      if (!calls.length) safeList.replaceChildren(el("li", { class: "muted", text: "No safe state: nothing is set when the grant ends." }));
      const choices = [];
      for (const device of draft.devices || []) {
        for (const name of Object.keys(device.methods || {}).sort()) {
          if (!gone(device.device_id, name) && !isStream(device.methods[name])) {
            choices.push(el("option", { value: device.device_id + "|" + name, text: device.device_id + "." + name }));
          }
        }
      }
      addSelect.replaceChildren(...choices);
      addSelect.disabled = addButton.disabled = !choices.length;
    }

    // ---- advanced: the whole edited protocol as JSON
    const useJson = smallButton("Use this JSON", () => {
      let parsed;
      try {
        parsed = JSON.parse(jsonBox.value);
      } catch (error) {
        jsonError.textContent = "Not valid JSON: " + error.message;
        jsonError.hidden = false;
        return;
      }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || !Array.isArray(parsed.devices)) {
        jsonError.textContent = "Send a protocol: a JSON object with devices (and safe_state).";
        jsonError.hidden = false;
        return;
      }
      jsonError.hidden = true;
      draft = parsed;
      removedDevices = new Set();
      removedMethods = new Set();
      render();
    });
    const advanced = el("details", { class: "editor-advanced" }, [
      el("summary", { text: "Advanced: edit as JSON" }),
      el("p", { class: "section-note", text: "The whole protocol, as the fields above have it. \"Use this JSON\" replaces the fields; the server checks it like any edit." }),
      jsonBox, jsonError, el("div", { class: "inspector-actions" }, [useJson]),
    ]);
    advanced.addEventListener("toggle", () => {
      if (advanced.open) jsonBox.value = JSON.stringify(build(), null, 2);
    });

    function render() {
      const duration = numberInput(draft, "duration_minutes", { label: "Asks for (minutes, at most " + num(data.max_minutes) + ")" });
      node.replaceChildren(
        el("h4", { class: "editor-title", text: "Your edit of the protocol" }),
        el("p", {
          class: "section-note",
          text: "Change what you'd allow; anything may be widened or narrowed within the device's and the lab's limits, " +
            "which the Control Center checks as it checks the agent's. Review changes… shows what you changed before you approve.",
        }),
        el("div", { class: "editor-arg editor-caps" }, [duration]),
        ...(draft.devices || []).map(deviceBlock),
        el("h4", { text: "Safe state (applied when the grant ends, in this order)" }),
        safeList,
        el("div", { class: "editor-add" }, [addSelect, addButton]),
        problemsList,
        advanced
      );
      renderSafe();
    }

    render();
    return {
      node,
      build,
      duration: () => (typeof draft.duration_minutes === "number" ? draft.duration_minutes : null),
      showProblems(problems) {
        problemsList.replaceChildren(...problems.map((item) =>
          el("li", {}, [item.path ? el("code", { text: item.path }) : "", item.path ? ": " : "", item.message])
        ));
        problemsList.hidden = !problems.length;
      },
    };
  }

  function experimentsSection() {
    const newer = new Map((savedExperiments || []).map((item) => [item.id, item.version]));
    const rows = project.linked_experiments.map((item) => {
      const unlink = el("button", { class: "link-button", type: "button", text: "Unlink" });
      unlink.addEventListener("click", () => unlinkExperiment(item));
      const latest = newer.get(item.id);
      let state = el("span", { class: "outline-kind", text: "v" + item.version });
      if (savedExperiments && latest === undefined) state = statusPill("deleted", "bad");
      else if (latest && latest !== item.version) state = statusPill("v" + latest + " saved", "warn");
      return el("li", {}, [
        el("a", { href: "/experiments/" + encodeURIComponent(item.id), text: item.name || item.id }),
        el("span", { class: "linked-tools" }, [state, unlink]),
      ]);
    });
    const behind = project.linked_experiments.some((item) => newer.has(item.id) && newer.get(item.id) !== item.version);
    return section(
      "Experiment layout",
      "Snapshots of these canvases are copied into Experiment_layout, with DEVICES.md describing every instrument.",
      [
        el("ul", { class: "outline" }, rows.length ? rows : [el("li", { class: "palette-empty", text: "No experiments linked yet." })]),
        el("div", { class: "inspector-actions" }, [
          behind ? smallButton("Update copies", refreshExperiments) : "",
          smallButton("Link experiment…", linkExperiment, true),
        ]),
        el("div", { class: "panel-links" }, [fileLink("Experiment_layout/LAYOUT.md", "LAYOUT.md"), fileLink("Experiment_layout/DEVICES.md", "DEVICES.md")]),
      ],
      project.linked_experiments.length
    );
  }

  function allowedSection() {
    if (!devices.length) {
      return section("Allowed devices", "Link an experiment first: only devices on a linked canvas can be allowed.", []);
    }
    const allowed = new Set(project.allowed_devices);
    const save = smallButton("Save", () => {
      const chosen = [...checks].filter((box) => box.checked).map((box) => box.value);
      saveSettings({ allowed_devices: chosen }, "Allowed devices");
    }, true);
    save.disabled = true;
    const checks = devices.map((device) => {
      const box = el("input", { type: "checkbox", value: device.device_id });
      box.checked = allowed.has(device.device_id);
      box.addEventListener("change", () => (save.disabled = false));
      return box;
    });
    return section(
      "Allowed devices",
      "Automation may only ever control the devices ticked here, and only within bounds a person approves.",
      [
        el("div", { class: "check-list" }, devices.map((device, index) =>
          el("label", { class: "field check" }, [
            checks[index],
            el("span", { class: "method-name", text: device.device_id }),
            el("span", { class: "outline-kind", text: device.label || device.device_type || "" }),
          ])
        )),
        el("div", { class: "inspector-actions" }, [save]),
      ],
      project.allowed_devices.length
    );
  }

  function librarySection() {
    if (!library.configured) {
      return section("Library scope", "No lab library is set up (LABNET_AGENT_LITERATURE_DIR).", []);
    }
    const topics = [...new Set(library.entries.filter((entry) => entry.kind === "dir" && !entry.path.includes("/")).map((entry) => entry.path))];
    const folders = new Set(project.library_folders);
    const lists = new Set(project.library_collections);
    const save = smallButton("Save", () => {
      saveSettings({
        library_folders: topicChecks.filter((box) => box.checked).map((box) => box.value),
        library_collections: listChecks.filter((box) => box.checked).map((box) => box.value),
      }, "Library scope");
    }, true);
    save.disabled = true;
    const check = (value, on) => {
      const box = el("input", { type: "checkbox", value });
      box.checked = on;
      box.addEventListener("change", () => (save.disabled = false));
      return box;
    };
    const topicChecks = topics.map((topic) => check(topic, folders.has(topic)));
    const listChecks = library.collections.map((collection) => check(collection.name, lists.has(collection.name)));
    const scoped = folders.size + lists.size;
    return section(
      "Library scope",
      "What the agent searches. Nothing ticked: the whole library.",
      [
        el("div", { class: "check-list" }, [
          ...topics.map((topic, index) => el("label", { class: "field check" }, [topicChecks[index], el("span", { text: topic.replace(/_/g, " ") })])),
          ...library.collections.map((collection, index) =>
            el("label", { class: "field check" }, [listChecks[index], el("span", { text: collection.name + " (reading list, " + collection.papers.length + ")" })])
          ),
        ]),
        el("div", { class: "inspector-actions" }, [save]),
      ],
      scoped ? scoped : "all"
    );
  }

  function renderPanel() {
    if (!project) return;
    tasksList = el("ul", { class: "outline" });
    runAllButton = smallButton("Run all", runAll);
    const tasks = section(
      "Tasks",
      "One Markdown file each. Run queues a task for an Automation runner, which works through it as its own Claude Code session, with the project's run settings; … beside it runs it with another model, effort, max turns or budget. Run all queues every task, in order, with the project's settings.",
      [tasksList, el("div", { class: "inspector-actions" }, [runAllButton, smallButton("New task", newTask, true)])]
    );
    runsList = el("ul", { class: "outline run-list" });
    runsNote = el("p", { class: "section-note" });
    const runsSection = section("Runs", "", [runsNote, runsList]);
    panel.replaceChildren(
      el("div", { class: "inspector-panel" }, [
        el("div", { class: "inspector-head" }, [
          el("span", { class: "inspector-kind", text: "Project" }),
          el("h2", { text: project.name }),
        ]),
        tasks,
        runsSection,
        experimentsSection(),
        allowedSection(),
        librarySection(),
      ])
    );
    renderTasks();
    renderRuns();
  }

  function renderHeader() {
    nameHeading.textContent = project.name;
    document.title = project.name + " · Automation · " + window.LabNet.title;
    metaLine.textContent = [
      project.created_by ? "Created by " + project.created_by : "",
      plural(project.linked_experiments.length, "linked experiment"),
      "saved " + ago(project.updated_at),
    ].filter(Boolean).join(" · ");
  }

  // ---- Settings and linked experiments ------------------------------------------------

  function applyProject(body) {
    project = body.project;
    devices = body.devices;
    renderHeader();
    renderPanel();
    window.LabNet.setAgentContext({ label: project.name, fields: { project_id: project.id } });
  }

  async function saveSettings(patch, what) {
    const data = {
      version: project.version,
      name: project.name,
      description: project.description,
      library_folders: project.library_folders,
      library_collections: project.library_collections,
      allowed_devices: project.allowed_devices,
      run_model: project.run_model,
      run_effort: project.run_effort,
      run_max_turns: project.run_max_turns,
      run_budget_usd: project.run_budget_usd,
      updated_by: store.get("localStorage", AUTHOR_KEY, ""),
      ...patch,
    };
    try {
      applyProject(await request(api, jsonRequest("PUT", data)));
      toast(what + " saved");
      await loadTree();
    } catch (error) {
      if (error.status === 409) {
        showNotice("Someone else changed this project, so it was reloaded. Make your change again.");
        await loadProject();
      } else {
        showNotice("Couldn't save: " + error.message);
      }
    }
  }

  // Model, effort, max turns and budget inputs, prefilled from ``initial`` (the project's
  // run_model, run_effort, run_max_turns, run_budget_usd). Empty model or effort: the
  // Control Center's defaults. Used by Settings… and by Run with….
  async function runSettingsFields(initial) {
    const info = await loadAgentInfo();
    const models = info && Array.isArray(info.models) && info.models.length ? info.models : null;
    const efforts = info && Array.isArray(info.efforts) && info.efforts.length
      ? info.efforts
      : ["low", "medium", "high", "xhigh", "max"].map((id) => ({ id, label: id }));
    const defaultModel = info && info.model;
    const defaultEffort = info && info.effort;
    let model;
    if (models) {
      model = el("select", {}, [
        el("option", { value: "", text: "Default" + (defaultModel ? " (" + labelFrom(models, defaultModel) + ")" : "") }),
        ...models.map((item) => el("option", { value: item.id, text: item.label || item.id })),
      ]);
      if (initial.run_model && !models.some((item) => item.id === initial.run_model)) {
        model.append(el("option", { value: initial.run_model, text: initial.run_model + " (no longer offered)" }));
      }
    } else {
      model = el("input", { maxlength: "100", placeholder: "Default (the Control Center's model)", spellcheck: "false" });
    }
    model.value = initial.run_model || "";
    const effort = el("select", {}, [
      el("option", { value: "", text: "Default" + (defaultEffort ? " (" + labelFrom(efforts, defaultEffort) + ")" : "") }),
      ...efforts.map((item) => el("option", { value: item.id, text: item.label || item.id })),
    ]);
    effort.value = initial.run_effort || "";
    const syncEffort = () => {
      const chosen = models && models.find((item) => item.id === (model.value || defaultModel));
      effort.disabled = Boolean(chosen && chosen.effort === false);
      effort.title = effort.disabled ? "This model has no effort levels." : "";
    };
    model.addEventListener("change", syncEffort);
    syncEffort();
    const turns = el("input", { type: "number", min: "1", max: "200", step: "1" });
    turns.value = String(initial.run_max_turns);
    const budget = el("input", { type: "number", min: "0.01", max: "100", step: "0.01" });
    budget.value = String(initial.run_budget_usd);
    return {
      grid: el("div", { class: "run-settings-grid" }, [
        el("label", {}, ["Model", model]),
        el("label", {}, ["Effort", effort]),
        el("label", {}, ["Max turns", turns]),
        el("label", {}, ["Budget per run (USD)", budget]),
      ]),
      validate() {
        const maxTurns = Number(turns.value);
        if (!Number.isInteger(maxTurns) || maxTurns < 1 || maxTurns > 200) return "Max turns must be a whole number from 1 to 200.";
        const dollars = Number(budget.value);
        if (!budget.value.trim() || !(dollars > 0 && dollars <= 100)) return "The budget must be more than $0 and at most $100.";
        return null;
      },
      values() {
        return {
          run_model: model.value.trim() || null,
          run_effort: effort.value || null,
          run_max_turns: Number(turns.value),
          run_budget_usd: Number(budget.value),
        };
      },
    };
  }

  async function editSettings() {
    const name = el("input", { maxlength: "120" });
    name.value = project.name;
    const description = el("textarea", { maxlength: "4000" });
    description.value = project.description;
    const runFields = await runSettingsFields(project); // how runs are run

    const choice = await dialog({
      title: "Project settings",
      body: el("div", { class: "dialog-fields" }, [
        el("label", {}, ["Name", name]),
        el("label", {}, ["Description", description]),
        el("h3", { class: "run-settings-head", text: "Runs" }),
        el("p", { class: "section-note", text: "Each run copies these when it is queued; changing them doesn't change runs already queued." }),
        runFields.grid,
      ]),
      actions: [{ label: "Save", value: "save", primary: true }],
      validate: () => {
        if (!name.value.trim()) return "Give the project a name.";
        return runFields.validate();
      },
    });
    if (choice !== "save") return;
    saveSettings({
      name: name.value.trim(),
      description: description.value.trim(),
      ...runFields.values(),
    }, "Settings");
  }

  async function loadAgentInfo() {
    if (agentInfo) return agentInfo;
    try {
      agentInfo = (await request("/api/automation/meta")).run_models || null;
    } catch (_ignored) {
      agentInfo = null; // the dialog falls back to a text field
    }
    return agentInfo;
  }

  async function linkExperiment() {
    try {
      savedExperiments = (await request("/api/experiments")).experiments;
    } catch (error) {
      showNotice("Couldn't list experiments: " + error.message);
      return;
    }
    const linked = new Set(project.linked_experiments.map((item) => item.id));
    const choices = savedExperiments.filter((item) => !linked.has(item.id));
    if (!choices.length) {
      showNotice(savedExperiments.length ? "Every saved experiment is already linked." : "No experiments saved yet: draw one on the Experiments tab first.");
      return;
    }
    const select = el("select", {}, choices.map((item) =>
      el("option", { value: item.id, text: item.name + " · " + plural(item.devices.length, "device") })
    ));
    const choice = await dialog({
      title: "Link an experiment",
      body: el("div", { class: "dialog-fields" }, [
        el("p", { text: "A snapshot of its canvas is copied into Experiment_layout. Later edits on the Experiments tab are picked up with Update copies." }),
        el("label", {}, ["Experiment", select]),
      ]),
      actions: [{ label: "Link", value: "link", primary: true }],
    });
    if (choice !== "link") return;
    try {
      applyProject(await request(api + "/experiments", jsonRequest("POST", {
        experiment_id: select.value,
        linked_by: store.get("localStorage", AUTHOR_KEY, ""),
      })));
      toast("Experiment linked");
      await loadTree();
    } catch (error) {
      showNotice("Couldn't link it: " + error.message);
    }
  }

  async function unlinkExperiment(item) {
    const choice = await dialog({
      title: "Unlink “" + (item.name || item.id) + "”?",
      body: el("p", { text: "Its snapshot leaves Experiment_layout, and its devices are no longer allowed. The experiment itself stays on the Experiments tab." }),
      actions: [{ label: "Unlink", value: "unlink", danger: true }],
    });
    if (choice !== "unlink") return;
    try {
      applyProject(await request(api + "/experiments/" + encodeURIComponent(item.id), { method: "DELETE" }));
      await loadTree();
    } catch (error) {
      showNotice("Couldn't unlink it: " + error.message);
    }
  }

  async function refreshExperiments() {
    try {
      const body = await request(api + "/experiments/refresh", jsonRequest("POST", {}));
      applyProject(body);
      await loadExperiments();
      await loadTree();
      toast(body.missing.length ? "Updated; " + plural(body.missing.length, "experiment") + " no longer exist" : "Copies updated");
    } catch (error) {
      showNotice("Couldn't update the copies: " + error.message);
    }
  }

  // ---- Loading ------------------------------------------------------------------

  async function loadProject() {
    applyProject(await request(api));
  }

  async function loadTree() {
    const body = await request(api + "/tree");
    entries = body.entries;
    const signature = JSON.stringify(entries);
    if (signature === lastTree) return;
    lastTree = signature;
    tree.render({ entries, library });
    renderTasks();
    const item = viewer.current();
    if (item) {
      tree.select(item.source, item.path);
      if (item.source === "project" && !viewer.editing()) {
        const entry = findEntry("project", item.path);
        if (!entry) showOverview();
        else if (item.entry && entry.etag !== item.entry.etag) viewer.show({ ...item, entry });
      }
    }
  }

  async function loadLibrary() {
    try {
      library = await request("/api/automation/library");
    } catch (_ignored) {
      library = { configured: false, entries: [], collections: [] };
    }
    tree.render({ entries, library });
    renderPanel();
  }

  async function loadExperiments() {
    try {
      savedExperiments = (await request("/api/experiments")).experiments;
      renderPanel();
    } catch (_ignored) {
      /* the panel just can't say whether copies are current */
    }
  }

  async function start() {
    if (!PROJECT_ID.test(projectId)) {
      nameHeading.textContent = "No such project";
      return;
    }
    try {
      const meta = await request("/api/automation/meta");
      zones = meta.zones;
      maxFileBytes = meta.max_file_bytes;
      await loadProject();
      await loadTree();
    } catch (error) {
      nameHeading.textContent = error.status === 404 ? "No such project" : "Couldn't load the project";
      metaLine.textContent = error.message;
      return;
    }
    window.LabNet.me.then((me) => { signedInUser = (me && me.user) || null; }).catch(() => {});
    window.setInterval(tickCountdowns, 1000); // a grant's time left
    await loadLibrary();
    loadExperiments();
    loadRuns();
    loadAgentInfo().then(() => renderRuns()); // model labels in the Runs note
    const runHash = window.location.hash.match(/^#run=(run-[0-9a-z-]+)$/);
    if (runHash && RUN_ID.test(runHash[1])) showRunLog(runHash[1]);
    else if (!openFromHash()) showOverview();
    window.setInterval(() => loadTree().catch(() => {}), 15000);
    // Runs and an open log are polled only while the page is visible.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        loadTree().catch(() => {});
        loadRuns();
        if (runLog) refreshLog();
      } else {
        window.clearTimeout(runsTimer);
        if (runLog) window.clearTimeout(runLog.timer);
      }
    });
  }

  document.getElementById("overview").addEventListener("click", showOverview);
  document.getElementById("settings").addEventListener("click", () => project && editSettings());
  document.getElementById("refresh").addEventListener("click", async () => {
    lastTree = "";
    await Promise.all([loadProject(), loadTree(), loadLibrary(), loadExperiments(), loadRuns()]);
    toast("Refreshed");
  });
  start();
})();
