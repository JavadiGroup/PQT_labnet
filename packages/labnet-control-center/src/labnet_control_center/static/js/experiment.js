// The experiment canvas page (/experiments/<id>): the controller. It owns
// the experiment, and every change goes through commit(), which records undo
// history, re-renders the diagram, palette and inspector, and schedules an
// autosave. Saves carry the version they are based on, so a change made
// elsewhere in the meantime is caught (409) instead of being overwritten.

(function () {
  "use strict";

  const { el, store, dialog, watchNetwork, allDevices } = window.LabNet;
  const X = window.LabNet.experiments;
  const id = decodeURIComponent(window.location.pathname.split("/").filter(Boolean)[1] || "");
  const AUTHOR_KEY = "labnet-experiment-author";
  const DRAFT_KEY = "labnet-experiment-draft-" + id;
  const VIEW_KEY = "labnet-experiment-view-" + id;
  const CONTENT_KEYS = ["name", "description", "run", "nodes", "links"];

  const title = document.getElementById("experiment-name");
  const subtitle = document.getElementById("experiment-meta");
  const status = document.getElementById("save-status");
  const saveBanner = document.getElementById("save-banner");
  const buttons = {
    undo: document.getElementById("undo"),
    redo: document.getElementById("redo"),
    layout: document.getElementById("auto-layout"),
    fit: document.getElementById("fit"),
    focus: document.getElementById("focus-mode"),
    exportScript: document.getElementById("export-script"),
  };

  let meta = null;
  let doc = null; // the experiment being edited (content plus id, version, ...)
  let report = null;
  let live = new Map();
  let networkKnown = false;
  let diagram = null;
  let palette = null;
  let inspector = null;
  const history = X.createHistory(100);

  // Saving
  let baseVersion = null; // the server version this edit is based on
  let savedJson = null; // content as last saved
  let saving = false;
  let saveAgain = false;
  let saveTimer = 0;
  let retryDelay = 2000;
  let conflict = null;
  let saveState = "saved";

  // ---- The document ---------------------------------------------------------------

  function contentOf(experiment) {
    const content = {};
    for (const key of CONTENT_KEYS) content[key] = experiment[key];
    return content;
  }

  function contentJson(experiment) {
    return JSON.stringify(contentOf(experiment));
  }

  function author() {
    return store.get("localStorage", AUTHOR_KEY, "") || "";
  }

  function methodsFor(node) {
    const device = live.get(node.device_id);
    if (device && device.type === node.device_type && device.known_contract) return device.methods;
    const type = meta && meta.device_types[node.device_type];
    return type ? type.methods : null;
  }

  function typeLabel(node) {
    const device = live.get(node.device_id);
    if (device) return device.type_label;
    const type = meta && meta.device_types[node.device_type];
    return type ? type.label : node.device_type;
  }

  function kindLabel(kind) {
    if (kind === "note") return "Note";
    const optic = meta && meta.optics.find((item) => item.kind === kind);
    return optic ? optic.label : kind;
  }

  function describe(node) {
    const device = live.get(node.device_id);
    const errors = report ? report.errors.filter((item) => item.node_id === node.id).length : 0;
    let state = "unknown";
    if (networkKnown) {
      if (!device) state = "missing";
      else if (device.control_center || device.state === "leased") state = "warn";
      else state = device.state === "available" ? "ok" : "bad";
    }
    const settings = node.settings || [];
    const sweeps = settings.filter((item) => item.sweep).length;
    const badges = [];
    if (sweeps) badges.push({ text: sweeps > 1 ? "SWEEP " + sweeps : "SWEEP", tone: "sweep" });
    if (settings.length - sweeps) badges.push({ text: "SET " + (settings.length - sweeps), tone: "set" });
    if ((node.records || []).length) badges.push({ text: "REC " + node.records.length, tone: "rec" });
    if (errors) badges.push({ text: "! " + errors, tone: "error" });
    return {
      title: node.label || node.device_id,
      subtitle: node.label ? node.device_id + " · " + typeLabel(node) : typeLabel(node),
      state,
      missing: networkKnown && !device,
      errors,
      badges,
    };
  }

  // Blocks without a position (Photo-Agent proposals) are laid out.
  function placeMissing(experiment) {
    if (!experiment.nodes.some((node) => node.x == null || node.y == null)) return false;
    const positions = X.layout(experiment.nodes, experiment.links);
    for (const node of experiment.nodes) {
      if (node.x == null || node.y == null) Object.assign(node, positions.get(node.id));
    }
    return true;
  }

  // ---- Changes ----------------------------------------------------------------

  function commit(label, mutate, options) {
    const opts = options || {};
    if (conflict || !doc) return;
    const before = JSON.stringify(doc);
    const next = JSON.parse(before);
    mutate(next);
    const after = JSON.stringify(next);
    if (after === before) return;
    history.record(label, before, opts.coalesce || null);
    doc = next;
    changed(opts);
  }

  function changed(opts) {
    diagram.setDocument(doc);
    if (opts.select) diagram.select(opts.select.nodes || [], opts.select.links || [], { reveal: true });
    palette.refresh();
    if (opts.source !== "inspector" || opts.rerender) inspector.show(diagram.selection(), { force: true });
    updateHeader();
    updateButtons();
    keepDraft();
    scheduleSave();
  }

  function undo() {
    if (conflict || diagram.busy()) return;
    const entry = history.undo(JSON.stringify(doc));
    if (!entry) return;
    doc = JSON.parse(entry.json);
    changed({});
    toast("Undid " + entry.label.toLowerCase());
  }

  function redo() {
    if (conflict || diagram.busy()) return;
    const entry = history.redo(JSON.stringify(doc));
    if (!entry) return;
    doc = JSON.parse(entry.json);
    changed({});
  }

  function remove(selection) {
    const nodes = new Set(selection.nodes);
    const links = new Set(selection.links);
    if (!nodes.size && !links.size) return;
    const count = nodes.size + links.size;
    commit(count === 1 ? "Remove" : "Remove " + count + " items", (d) => {
      d.nodes = d.nodes.filter((node) => !nodes.has(node.id));
      d.links = d.links.filter((link) => !links.has(link.id) && !nodes.has(link.source) && !nodes.has(link.target));
    });
    diagram.select([], []);
    inspector.show(diagram.selection(), { force: true });
  }

  function addItem(entry, position) {
    const taken = new Set(doc.nodes.map((node) => node.id));
    const kind = entry.device ? "instrument" : entry.kind;
    const spot = position || diagram.freeSpot(diagram.centerWorld(), kind);
    let node;
    if (entry.device) {
      const existing = doc.nodes.find((item) => item.device_id === entry.device.id);
      if (existing) {
        diagram.select([existing.id], [], { reveal: true });
        inspector.show(diagram.selection());
        return;
      }
      node = { id: X.freeId(entry.device.id, taken), kind, label: "", notes: "", x: spot.x, y: spot.y,
        device_id: entry.device.id, device_type: entry.device.type, settings: [], records: [] };
    } else {
      node = { id: X.freeId(kind, taken), kind, label: "", notes: "", x: spot.x, y: spot.y };
    }
    commit("Add " + entry.label, (d) => d.nodes.push(node), { select: { nodes: [node.id] } });
    inspector.show(diagram.selection(), { force: true });
  }

  function linkKind(source, target, flip) {
    const kind = source.kind === "instrument" && target.kind === "instrument" ? "signal" : "beam";
    return flip ? (kind === "beam" ? "signal" : "beam") : kind;
  }

  function connect(source, target, type) {
    const existing = doc.links.find((link) => link.source === source.id && link.target === target.id && link.type === type);
    if (existing) {
      diagram.select([], [existing.id]);
      inspector.show(diagram.selection());
      return;
    }
    const taken = new Set(doc.links.map((link) => link.id));
    let n = doc.links.length + 1;
    while (taken.has("l" + n)) n++;
    const link = { id: "l" + n, source: source.id, target: target.id, type, label: "" };
    commit(type === "beam" ? "Connect beam" : "Connect cable", (d) => d.links.push(link), { select: { links: [link.id] } });
    inspector.show(diagram.selection(), { force: true });
  }

  // ---- Saving ----------------------------------------------------------------------

  function setStatus(state, detail) {
    saveState = state;
    const text = {
      saved: "Saved",
      dirty: "Unsaved changes",
      saving: "Saving…",
      retrying: "Can't save right now; retrying" + (detail ? " (" + detail + ")" : "") + "…",
      invalid: "Not saved: " + (detail || "the experiment has a problem"),
      conflict: "Not saved: changed elsewhere",
    }[state];
    status.textContent = text;
    status.dataset.state = state;
  }

  function scheduleSave(delay) {
    if (conflict) return;
    if (contentJson(doc) === savedJson) {
      if (!saving) setStatus("saved");
      return;
    }
    setStatus("dirty");
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(save, delay === undefined ? 800 : delay);
  }

  async function save() {
    if (conflict) return;
    if (saving) {
      saveAgain = true;
      return;
    }
    const sent = contentJson(doc);
    if (sent === savedJson) {
      setStatus("saved");
      return;
    }
    saving = true;
    setStatus("saving");
    try {
      const response = await fetch("/api/experiments/" + encodeURIComponent(id), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...JSON.parse(sent), version: baseVersion, updated_by: author() }),
      });
      const reply = await response.json().catch(() => ({}));
      if (response.status === 409) {
        showConflict(reply.current);
        return;
      }
      if (response.status === 422 || response.status === 413) {
        setStatus("invalid", reply.errors && reply.errors.length ? reply.errors[0].message : reply.detail);
        return;
      }
      if (!response.ok) throw new Error(reply.detail || "HTTP " + response.status);
      baseVersion = reply.experiment.version;
      savedJson = sent;
      Object.assign(doc, { version: reply.experiment.version, updated_at: reply.experiment.updated_at, updated_by: reply.experiment.updated_by });
      setReport(reply.report);
      retryDelay = 2000;
      if (contentJson(doc) === savedJson) {
        setStatus("saved");
        clearDraft();
      } else {
        scheduleSave(200);
      }
      updateHeader();
    } catch (error) {
      setStatus("retrying", error.message);
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(save, retryDelay);
      retryDelay = Math.min(30000, retryDelay * 2);
    } finally {
      saving = false;
      if (saveAgain) {
        saveAgain = false;
        scheduleSave(100);
      }
    }
  }

  // Save now (before exporting, or before Photo-Agent reads the experiment).
  async function flushSave() {
    window.clearTimeout(saveTimer);
    if (!doc || conflict) return;
    for (let attempt = 0; attempt < 3 && (saving || contentJson(doc) !== savedJson); attempt++) {
      if (saving) await new Promise((resolve) => window.setTimeout(resolve, 150));
      else await save();
      if (saveState === "invalid" || saveState === "retrying") break;
    }
  }

  function keepDraft() {
    if (contentJson(doc) === savedJson) clearDraft();
    else store.set("localStorage", DRAFT_KEY, { base_version: baseVersion, content: contentOf(doc), at: Date.now() });
  }

  function clearDraft() {
    try {
      window.localStorage.removeItem(DRAFT_KEY);
    } catch (_ignored) {
      /* best effort */
    }
  }

  function showConflict(current) {
    conflict = current;
    window.clearTimeout(saveTimer);
    setStatus("conflict");
    const when = new Date(current.updated_at).toLocaleTimeString();
    const reload = el("button", { class: "button small", type: "button", text: "Reload" });
    const copy = el("button", { class: "button small", type: "button", text: "Save my version as a copy" });
    reload.addEventListener("click", () => {
      saveBanner.hidden = true;
      adopt(current, null, { keepView: true });
      history.clear();
      updateButtons();
    });
    copy.addEventListener("click", async () => {
      const response = await fetch("/api/experiments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...contentOf(doc), name: (doc.name + " (copy)").slice(0, 120), created_by: author() }),
      });
      const reply = await response.json().catch(() => ({}));
      if (response.ok) {
        clearDraft();
        window.location.href = "/experiments/" + encodeURIComponent(reply.experiment.id);
      } else {
        saveBanner.firstChild.textContent = "Couldn't save a copy: " + (reply.detail || response.status) + ". ";
      }
    });
    saveBanner.replaceChildren(
      el("span", { text: "This experiment was changed elsewhere at " + when + (current.updated_by ? " by " + current.updated_by : "") +
        ", so your latest changes weren't saved. " }),
      reload,
      " ",
      copy
    );
    saveBanner.hidden = false;
  }

  function setReport(next) {
    report = next;
    diagram.refresh();
    inspector.refreshProblems();
    buttons.exportScript.title = report && !report.exportable
      ? report.errors.length + " problem(s) stop the export; see the inspector"
      : "Generate a Python script that runs this experiment";
  }

  // ---- Loading -----------------------------------------------------------------------

  function adopt(experiment, nextReport, { keepView } = {}) {
    doc = JSON.parse(JSON.stringify(experiment));
    baseVersion = experiment.version;
    savedJson = contentJson(experiment);
    conflict = null;
    const placed = placeMissing(doc); // shown laid out, saved only with the next edit
    diagram.setDocument(doc);
    if (nextReport) setReport(nextReport);
    palette.refresh();
    inspector.show(diagram.selection(), { force: true });
    if (!keepView) restoreView();
    updateHeader();
    updateButtons();
    setStatus("saved");
    if (placed) scheduleSave(1500);
  }

  function restoreView() {
    const saved = store.get("localStorage", VIEW_KEY, null);
    if (saved && Number.isFinite(saved.k)) diagram.setView(saved);
    else diagram.fit();
  }

  async function poll() {
    if (!doc || saving || conflict || contentJson(doc) !== savedJson || document.hidden || diagram.busy()) return;
    try {
      const response = await fetch("/api/experiments/" + encodeURIComponent(id), { cache: "no-store" });
      if (!response.ok) return;
      const reply = await response.json();
      if (reply.experiment.version > baseVersion) {
        const selection = diagram.selection();
        adopt(reply.experiment, reply.report, { keepView: true });
        diagram.select(selection.nodes, selection.links);
        inspector.show(diagram.selection(), { force: true });
        history.clear();
        updateButtons();
        toast("Updated" + (reply.experiment.updated_by ? " by " + reply.experiment.updated_by : "") + " (version " + reply.experiment.version + ")");
      } else {
        setReport(reply.report); // device states may have changed
      }
    } catch (_ignored) {
      /* try again later */
    }
  }

  async function offerDraft(server) {
    const draft = store.get("localStorage", DRAFT_KEY, null);
    if (!draft || !draft.content || JSON.stringify(draft.content) === contentJson(server)) {
      clearDraft();
      return;
    }
    const newer = draft.base_version !== server.version;
    const choice = await dialog({
      title: "Restore unsaved changes?",
      body: el("p", {
        text: "This browser has changes from " + new Date(draft.at).toLocaleString() + " that were never saved." +
          (newer ? " The experiment has been saved elsewhere since then; restoring replaces those changes." : ""),
      }),
      actions: [{ label: "Discard them", value: "discard" }, { label: "Restore", value: "restore", primary: true }],
    });
    if (choice === "restore") {
      commit("Restore unsaved changes", (d) => Object.assign(d, draft.content));
    } else if (choice === "discard") {
      clearDraft();
    }
  }

  // ---- Header, toolbar, keys ---------------------------------------------------------

  function updateHeader() {
    title.textContent = doc.name;
    document.title = doc.name + " · " + window.LabNet.title;
    const s = X.summarize(doc);
    subtitle.textContent = [
      s.devices + (s.devices === 1 ? " device" : " devices"),
      s.optics + (s.optics === 1 ? " optic" : " optics"),
      s.links + (s.links === 1 ? " link" : " links"),
      s.points + (s.points === 1 ? " point" : " points"),
      "version " + doc.version,
    ].join(" · ");
    if (window.LabNet.setAgentContext && agentContextOn) {
      window.LabNet.setAgentContext({ label: doc.name, fields: { experiment_id: id }, prepare: flushSave });
    }
  }

  function updateButtons() {
    buttons.undo.disabled = !history.latestLabel() || Boolean(conflict);
    buttons.undo.title = history.latestLabel() ? "Undo: " + history.latestLabel() + " (Ctrl+Z)" : "Nothing to undo";
    buttons.redo.disabled = !history.nextRedoLabel() || Boolean(conflict);
    buttons.redo.title = history.nextRedoLabel() ? "Redo: " + history.nextRedoLabel() + " (Ctrl+Y)" : "Nothing to redo";
  }

  let toastTimer = 0;
  function toast(text) {
    const box = document.getElementById("toast");
    box.textContent = text;
    box.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => (box.hidden = true), 2500);
  }

  function editableTarget(target) {
    return target && target.closest && target.closest("input, textarea, select, [contenteditable], dialog");
  }

  document.addEventListener("keydown", (event) => {
    if (!doc || editableTarget(event.target) || document.querySelector("dialog[open]")) return;
    const command = event.ctrlKey || event.metaKey;
    if (command && event.key.toLowerCase() === "z" && !event.shiftKey) {
      event.preventDefault();
      undo();
    } else if (command && (event.key.toLowerCase() === "y" || (event.key.toLowerCase() === "z" && event.shiftKey))) {
      event.preventDefault();
      redo();
    } else if (event.key === "Delete" || event.key === "Backspace") {
      const selection = diagram.selection();
      if (selection.nodes.length || selection.links.length) {
        event.preventDefault();
        remove(selection);
      }
    } else if (command && event.key.toLowerCase() === "a" && document.activeElement === diagram.element) {
      event.preventDefault();
      diagram.select(doc.nodes.map((node) => node.id), []);
      inspector.show(diagram.selection());
    } else if (event.key === "Escape") {
      diagram.select([], []);
      inspector.show(diagram.selection());
    } else if (event.key.startsWith("Arrow") && document.activeElement === diagram.element) {
      const selected = new Set(diagram.selection().nodes);
      if (!selected.size) return;
      event.preventDefault();
      const step = event.shiftKey ? X.GRID * 4 : X.GRID;
      const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
      const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
      commit("Nudge", (d) => {
        for (const node of d.nodes) {
          if (selected.has(node.id)) {
            node.x += dx;
            node.y += dy;
          }
        }
      }, { coalesce: "nudge" });
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (doc && contentJson(doc) !== savedJson && !conflict) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  buttons.undo.addEventListener("click", undo);
  buttons.redo.addEventListener("click", redo);
  buttons.fit.addEventListener("click", () => diagram.fit());
  buttons.layout.addEventListener("click", () => {
    const positions = X.layout(doc.nodes, doc.links);
    commit("Auto-layout", (d) => {
      for (const node of d.nodes) Object.assign(node, positions.get(node.id));
    });
    diagram.fit();
  });
  let focused = false;
  buttons.focus.hidden = !window.LabNet.features.photo_agent; // no sidebar to hide
  buttons.focus.addEventListener("click", () => {
    focused = !focused;
    window.LabNet.setAgentCollapsed(focused);
    buttons.focus.setAttribute("aria-pressed", String(focused));
  });
  buttons.exportScript.addEventListener("click", () => exportScript());

  // Save first, so the script is made from exactly what is on screen.
  async function exportScript() {
    await flushSave();
    if (conflict) return;
    let response;
    let text;
    try {
      response = await fetch("/api/experiments/" + encodeURIComponent(id) + "/script?version=" + baseVersion, { cache: "no-store" });
      text = await response.text();
    } catch (error) {
      toast("Couldn't export: " + error.message);
      return;
    }
    if (response.status === 409) {
      showConflict(JSON.parse(text).current);
      return;
    }
    if (response.status === 422) {
      const body = JSON.parse(text);
      await dialog({
        title: "Not ready to export",
        body: el("div", { class: "dialog-fields" }, [
          el("p", { text: "These stop the script from being generated. The inspector shows them too; click one there to find its block." }),
          el("ul", { class: "problems" }, (body.errors || []).map((item) => el("li", { class: "problem error", text: item.message }))),
        ]),
        actions: [{ label: "OK", value: "ok", primary: true }],
      });
      return;
    }
    if (!response.ok) {
      toast("Couldn't export (HTTP " + response.status + ").");
      return;
    }
    const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
    const filename = match ? match[1] : "experiment.py";
    const lines = text.replace(/\n$/, "").split("\n");
    const choice = await dialog({
      title: "Export script",
      wide: true,
      body: el("div", { class: "export-body" }, [
        el("p", {}, [
          "Runs this experiment from any computer with LabNet installed. Point it at a client settings file and run it: ",
          el("code", { text: "LABNET_ENV_FILE=researcher.env python " + filename }),
        ]),
        window.LabNet.markdown.codeBlock("python", lines),
      ]),
      actions: [{ label: "Download " + filename, value: "download", primary: true }],
    });
    if (choice === "download") {
      const url = URL.createObjectURL(new Blob([text], { type: "text/x-python" }));
      const link = el("a", { href: url, download: filename });
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 2000);
    }
  }

  // ---- Start ----------------------------------------------------------------------

  let agentContextOn = true;

  async function start() {
    try {
      const [metaResponse, experimentResponse] = await Promise.all([
        fetch("/api/experiments/meta"),
        fetch("/api/experiments/" + encodeURIComponent(id), { cache: "no-store" }),
      ]);
      if (experimentResponse.status === 404) {
        title.textContent = "No such experiment";
        subtitle.replaceChildren("It may have been deleted. ", el("a", { href: "/experiments", text: "All experiments" }));
        document.querySelector(".editor").hidden = true;
        document.querySelector(".editor-toolbar").hidden = true;
        return;
      }
      if (!experimentResponse.ok || !metaResponse.ok) throw new Error("HTTP " + (experimentResponse.ok ? metaResponse.status : experimentResponse.status));
      meta = await metaResponse.json();
      const reply = await experimentResponse.json();

      diagram = window.LabNet.createDiagram(document.getElementById("canvas"), {
        commit,
        connect,
        linkKind,
        describe,
        kindLabel,
        onSelect: (selection) => inspector.show(selection),
        onView: (view) => store.set("localStorage", VIEW_KEY, view),
      });
      palette = window.LabNet.createPalette(document.getElementById("palette"), {
        preview: (entry, x, y) => Boolean(diagram.previewDrop(entry.device ? "instrument" : entry.kind, x, y)),
        endPreview: () => diagram.endPreview(),
        drop: (entry, x, y) => {
          const position = diagram.previewDrop(entry.device ? "instrument" : entry.kind, x, y);
          diagram.endPreview();
          if (position) addItem(entry, position);
        },
        add: (entry) => addItem(entry, null),
        placed: (deviceId) => {
          const node = doc && doc.nodes.find((item) => item.device_id === deviceId);
          return node ? node.id : null;
        },
        reveal: (nodeId) => {
          diagram.select([nodeId], [], { reveal: true });
          inspector.show(diagram.selection());
        },
      });
      palette.setOptics(meta.optics);
      inspector = window.LabNet.createInspector(document.getElementById("inspector"), {
        doc: () => doc,
        report: () => report,
        commit,
        select: (nodes, links) => {
          diagram.select(nodes, links, { reveal: true });
          inspector.show(diagram.selection());
        },
        methodsFor,
        live: (deviceId) => live.get(deviceId) || null,
        networkKnown: () => networkKnown,
        typeLabel,
        kindLabel,
        optics: meta.optics,
        remove,
        connect,
      });

      adopt(reply.experiment, reply.report);
      window.LabNet.refreshProposals(); // proposal cards can now offer Apply
      await offerDraft(reply.experiment);

      watchNetwork((snapshot) => {
        live = new Map(allDevices(snapshot).map((device) => [device.id, device]));
        networkKnown = snapshot.central.ok || live.size > 0;
        palette.setDevices([...live.values()]);
        diagram.refresh();
        inspector.refreshLive();
      });
      window.setInterval(poll, 20000);
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) poll();
      });
    } catch (error) {
      title.textContent = "Couldn't open the experiment";
      subtitle.textContent = error.message;
    }
  }

  // For Photo-Agent's proposal cards (experiment-card.js).
  window.LabNet.experimentPage = {
    id,
    document: () => (doc ? JSON.parse(JSON.stringify(doc)) : null),
    flushSave,
    applyProposal(proposal, { rename } = {}) {
      const label = "Apply Photo-Agent proposal";
      commit(label, (d) => {
        d.nodes = proposal.nodes;
        d.links = proposal.links;
        if (proposal.run) d.run = proposal.run;
        if (rename) d.name = proposal.name;
        if (rename || !d.description) d.description = proposal.description || d.description;
        placeMissing(d);
      });
      diagram.select([], []);
      inspector.show(diagram.selection(), { force: true });
      diagram.fit();
      return label;
    },
    undoIf(label) {
      if (history.latestLabel() === label) undo();
    },
  };

  start();
})();
