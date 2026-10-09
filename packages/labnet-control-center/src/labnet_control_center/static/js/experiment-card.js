// Photo-Agent's experiment proposals. A ```labnet-experiment block in an answer
// becomes a card: on an experiment's canvas, "Apply to canvas" replaces the
// diagram as one undo step; anywhere else, "Create experiment" saves it as a
// new experiment and opens it. The Control Center checks every proposal
// first, so nothing with errors is applied, and the person always decides.
//
// Cards are rebuilt whenever the chat re-renders the block, so their state
// (busy, confirmed, applied) is kept here by proposal, not in the DOM; what
// was created or applied is remembered for the session.

(function () {
  "use strict";

  const RESULTS_KEY = "labnet-proposals";
  const AUTHOR_KEY = "labnet-experiment-author";
  const states = new Map(); // proposal hash -> state
  const mounted = new Set(); // repaint functions of cards on screen

  function node(tag, attributes, children) {
    const element = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes || {})) {
      if (value === false || value === null || value === undefined) continue;
      if (name === "text") element.textContent = value;
      else element.setAttribute(name, value === true ? "" : value);
    }
    for (const child of children || []) if (child) element.append(child);
    return element;
  }

  function hash(text) {
    let value = 0x811c9dc5;
    for (let index = 0; index < text.length; index++) {
      value ^= text.charCodeAt(index);
      value = Math.imul(value, 0x01000193) >>> 0;
    }
    return value.toString(16);
  }

  function read(area, key, fallback) {
    try {
      const raw = window[area].getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (_error) {
      return fallback;
    }
  }

  function remember(key, result) {
    const results = read("sessionStorage", RESULTS_KEY, {});
    results[key] = result;
    try {
      window.sessionStorage.setItem(RESULTS_KEY, JSON.stringify(results));
    } catch (_error) {
      /* best effort */
    }
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const reply = await response.json().catch(() => ({}));
    return { status: response.status, ok: response.ok, reply };
  }

  function stats(proposal) {
    const s = window.LabNet.experiments.summarize(proposal);
    const word = (n, one, many) => n + " " + (n === 1 ? one : many);
    const parts = [word(s.devices, "device", "devices"), word(s.optics, "optic", "optics"), word(s.links, "link", "links")];
    if (s.sweeps) parts.push(word(s.sweeps, "sweep", "sweeps"));
    if (s.records) parts.push(word(s.records, "reading", "readings") + " recorded");
    if (s.sweeps) parts.push(word(s.points, "point", "points"));
    return parts.join(" · ");
  }

  function render(source, { closed }) {
    const card = node("div", { class: "proposal-card" });
    if (!closed) {
      card.classList.add("drafting");
      card.append(
        node("div", { class: "proposal-head" }, [node("span", { class: "proposal-kind", text: "Experiment proposal" })]),
        node("p", { class: "proposal-meta", text: "Drafting… " + source.split("\n").length + " lines so far" })
      );
      return card;
    }
    const key = hash(source);
    let proposal = null;
    let problem = null;
    try {
      proposal = JSON.parse(source);
      if (!proposal || typeof proposal !== "object" || !Array.isArray(proposal.nodes)) problem = "it has no list of nodes";
      else if ((proposal.nodes || []).length > 300) problem = "it has more than 300 components";
    } catch (error) {
      problem = error.message;
    }
    if (problem) {
      card.classList.add("invalid");
      card.append(
        node("div", { class: "proposal-head" }, [node("span", { class: "proposal-kind", text: "Experiment proposal" })]),
        node("p", { class: "proposal-status error", text: "This proposal can't be used: " + problem + ". Ask Photo-Agent to send it again." }),
        window.LabNet.markdown.codeBlock("json", source.split("\n"))
      );
      return card;
    }
    if (!states.has(key)) states.set(key, { busy: false, message: "", tone: "", items: [], armed: false, applied: null, json: false });
    const paint = () => paintCard(card, key, proposal, paint);
    paint();
    mounted.add({ card, paint });
    return card;
  }

  function paintCard(card, key, proposal, paint) {
    const state = states.get(key);
    const page = window.LabNet.experimentPage;
    const result = read("sessionStorage", RESULTS_KEY, {})[key];
    const set = (changes) => {
      Object.assign(state, changes);
      paint();
    };

    const children = [
      node("div", { class: "proposal-head" }, [
        node("span", { class: "proposal-kind", text: "Experiment proposal" }),
        node("strong", { class: "proposal-name", text: String(proposal.name || "Untitled experiment") }),
      ]),
      node("p", { class: "proposal-meta", text: stats(proposal) }),
    ];
    const devices = proposal.nodes.filter((item) => item && item.kind === "instrument" && item.device_id);
    if (devices.length) {
      children.push(node("ul", { class: "proposal-devices" }, devices.map((item) => node("li", { text: String(item.device_id) }))));
    }
    if (proposal.description) children.push(node("p", { class: "proposal-description", text: String(proposal.description) }));

    const actions = node("div", { class: "proposal-actions" });
    let rename = null;
    if (result && result.kind === "created") {
      actions.append(node("a", { class: "button small", href: "/experiments/" + encodeURIComponent(result.id), text: "Open the experiment" }));
    } else if (page) {
      const current = page.document();
      if (current && proposal.name && proposal.name !== current.name) {
        rename = node("input", { type: "checkbox" });
        children.push(node("label", { class: "proposal-rename" }, [rename, " Also use its name, “" + proposal.name + "”"]));
      }
      if (state.applied) {
        const undo = node("button", { class: "button small", type: "button", text: "Undo" });
        undo.addEventListener("click", () => {
          page.undoIf(state.applied);
          set({ applied: null, message: "Undone; the canvas is back as it was.", tone: "", items: [] });
        });
        actions.append(undo);
      } else {
        const apply = node("button", {
          class: "button primary small",
          type: "button",
          text: state.armed ? "Apply anyway" : "Apply to canvas",
          disabled: state.busy,
        });
        apply.addEventListener("click", () => applyProposal(proposal, key, rename && rename.checked, set, state));
        actions.append(apply);
      }
      const copy = node("button", { class: "button small", type: "button", text: "Create as new", disabled: state.busy });
      copy.addEventListener("click", () => createExperiment(proposal, key, set));
      actions.append(copy);
    } else {
      const create = node("button", { class: "button primary small", type: "button", text: "Create experiment", disabled: state.busy });
      create.addEventListener("click", () => createExperiment(proposal, key, set));
      actions.append(create);
    }
    const toggle = node("button", { class: "link-button", type: "button", text: state.json ? "Hide JSON" : "Show JSON" });
    toggle.addEventListener("click", () => set({ json: !state.json }));
    actions.append(toggle);
    children.push(actions);

    if (state.message || state.items.length) {
      children.push(
        node("div", { class: "proposal-status " + state.tone }, [
          state.message ? node("p", { text: state.message }) : null,
          state.items.length ? node("ul", {}, state.items.map((text) => node("li", { text }))) : null,
        ])
      );
    } else if (result && result.kind === "applied" && !state.applied) {
      children.push(node("div", { class: "proposal-status" }, [node("p", { text: "Applied to a canvas earlier." })]));
    }
    if (state.json) children.push(window.LabNet.markdown.codeBlock("json", JSON.stringify(proposal, null, 2).split("\n")));
    card.replaceChildren(...children);
  }

  async function applyProposal(proposal, key, rename, set, state) {
    const page = window.LabNet.experimentPage;
    set({ busy: true, message: "Checking the proposal…", tone: "", items: [] });
    try {
      await page.flushSave();
      const current = page.document();
      const body = { ...proposal };
      if (!rename) {
        body.name = current.name;
        body.description = current.description || proposal.description || ""; // fill an empty one
      }
      const { ok, reply } = await postJson("/api/experiments/validate", body);
      if (!ok || !reply.experiment) {
        const errors = (reply.report && reply.report.errors) || [];
        set({ busy: false, armed: false, tone: "error", message: "Not applied. Photo-Agent can fix these if you paste them back:",
          items: errors.map((item) => item.message).slice(0, 8) });
        return;
      }
      const report = reply.report;
      if (report.errors.length) {
        set({ busy: false, armed: false, tone: "error", message: "It can be placed, but these would stop the export:",
          items: report.errors.map((item) => item.message).slice(0, 8) });
      }
      if (report.warnings.length && !state.armed && !report.errors.length) {
        set({ busy: false, armed: true, tone: "warning", message: "Check these first, then press Apply anyway:",
          items: report.warnings.map((item) => item.message).slice(0, 8) });
        return;
      }
      if (current.nodes.length) {
        const choice = await window.LabNet.dialog({
          title: "Replace the diagram?",
          body: node("p", { text: "The proposal replaces the " + current.nodes.length + " components on this canvas. Undo brings them back." }),
          actions: [{ label: "Replace", value: "replace", primary: true }],
        });
        if (choice !== "replace") {
          set({ busy: false, message: "", items: [], tone: "" });
          return;
        }
      }
      const label = page.applyProposal(reply.experiment, { rename });
      remember(key, { kind: "applied", id: page.id });
      set({ busy: false, armed: false, applied: label, tone: report.errors.length ? "error" : "ok",
        message: report.errors.length ? "Applied. Fix the problems above before exporting." : "Applied to the canvas.",
        items: report.errors.length ? state.items : [] });
    } catch (error) {
      set({ busy: false, tone: "error", message: "Couldn't apply it: " + error.message, items: [] });
    }
  }

  async function createExperiment(proposal, key, set) {
    set({ busy: true, message: "Creating the experiment…", tone: "", items: [] });
    try {
      const author = read("localStorage", AUTHOR_KEY, "") || "";
      const { status, ok, reply } = await postJson("/api/experiments", { ...proposal, created_by: author });
      if (!ok) {
        const errors = (reply.errors || []).map((item) => item.message);
        set({ busy: false, tone: "error", message: status === 422 ? "Not created:" : "Couldn't create it: " + (reply.detail || status), items: errors.slice(0, 8) });
        return;
      }
      remember(key, { kind: "created", id: reply.experiment.id });
      if (window.LabNet.experimentPage) await window.LabNet.experimentPage.flushSave();
      window.location.href = "/experiments/" + encodeURIComponent(reply.experiment.id);
    } catch (error) {
      set({ busy: false, tone: "error", message: "Couldn't create it: " + error.message, items: [] });
    }
  }

  // Called by the canvas page once it has loaded: cards drawn before it was
  // ready offer Apply instead of Create.
  function refreshProposals() {
    for (const entry of [...mounted]) {
      if (entry.card.isConnected) entry.paint();
      else mounted.delete(entry);
    }
  }

  window.LabNet.markdown.register("labnet-experiment", render);
  window.LabNet = Object.assign(window.LabNet || {}, { refreshProposals });
})();
