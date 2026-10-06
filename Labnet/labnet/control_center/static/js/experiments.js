// Experiments page: every saved experiment, newest first. A row expands to
// show the devices it uses (with their live state), its optics and counts.

(function () {
  "use strict";

  const { el, chevronIcon, store, dialog, statusPill, deviceState, allDevices, ago, plural, watchNetwork } =
    window.PQT;
  const EXPANDED_KEY = "pqt-expanded-experiments";
  const AUTHOR_KEY = "pqt-experiment-author";
  const expanded = new Set(store.get("sessionStorage", EXPANDED_KEY, []));
  const list = document.getElementById("experiments");
  const summary = document.getElementById("summary");
  const count = document.getElementById("experiment-count");
  const notice = document.getElementById("notice"); // #banner belongs to watchNetwork

  let experiments = null; // null until the first load
  let unreadable = [];
  let live = null; // device id -> device, from the network picture
  let opticLabels = {};
  let lastSignature = "";

  // ---- Loading ----------------------------------------------------------------

  async function load() {
    try {
      const response = await fetch("/api/experiments", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const body = await response.json();
      experiments = body.experiments;
      unreadable = body.unreadable;
      render();
    } catch (error) {
      summary.textContent = "Couldn't load experiments (" + error.message + ").";
    }
  }

  async function loadMeta() {
    try {
      const meta = await (await fetch("/api/experiments/meta")).json();
      opticLabels = Object.fromEntries(meta.optics.map((item) => [item.kind, item.label]));
      render(true);
    } catch (_ignored) {
      /* labels fall back to the kind */
    }
  }

  // ---- Rows -------------------------------------------------------------------

  function deviceLine(device) {
    const current = live && live.get(device.device_id);
    let pill;
    if (!live) pill = statusPill("Checking…", "warn");
    else if (!current) pill = statusPill("Not on the network", "bad");
    else {
      const state = deviceState(current);
      pill = statusPill(state.label, state.tone);
    }
    const children = [
      el("span", { class: "experiment-device-name", text: device.device_id }),
      el("span", { class: "experiment-device-type", text: device.label || (current ? current.type_label : device.device_type) }),
      pill,
    ];
    if (current) children.push(el("a", { href: current.control_url, text: "Device control" }));
    return el("li", { class: "experiment-device" }, children);
  }

  function opticsText(optics) {
    const parts = Object.entries(optics).map(([kind, n]) => (opticLabels[kind] || kind) + (n > 1 ? " ×" + n : ""));
    return parts.length ? parts.join(" · ") : "No passive optics yet";
  }

  function detail(experiment, detailId) {
    const counts = experiment.counts;
    const remove = el("button", { class: "button danger small", type: "button", text: "Delete…" });
    remove.addEventListener("click", () => removeExperiment(experiment));
    return el("div", { class: "device-detail", id: detailId, hidden: !expanded.has(experiment.id) }, [
      experiment.description ? el("p", { class: "experiment-description", text: experiment.description }) : "",
      el("h3", { text: experiment.devices.length ? "Devices" : "No devices yet" }),
      experiment.devices.length ? el("ul", { class: "experiment-devices" }, experiment.devices.map(deviceLine)) : "",
      el("dl", { class: "facts" }, [
        el("div", {}, [el("dt", { text: "Optics" }), el("dd", { text: opticsText(experiment.optics) })]),
        el("div", {}, [
          el("dt", { text: "Diagram" }),
          el("dd", { text: plural(counts.components, "component") + " · " + plural(counts.links, "link") }),
        ]),
        el("div", {}, [
          el("dt", { text: "Run" }),
          el("dd", {
            text: plural(counts.sweeps, "sweep") + " · " + plural(counts.records, "reading") + " recorded · " +
              plural(experiment.points, "point"),
          }),
        ]),
        el("div", {}, [
          el("dt", { text: "Saved" }),
          el("dd", {
            text: new Date(experiment.updated_at).toLocaleString() +
              (experiment.updated_by ? " by " + experiment.updated_by : "") + " · version " + experiment.version,
          }),
        ]),
      ]),
      el("div", { class: "experiment-actions" }, [remove]),
    ]);
  }

  function row(experiment) {
    const detailId = "experiment-" + experiment.id;
    const isOpen = expanded.has(experiment.id);
    const chevron = el(
      "button",
      {
        class: "chevron",
        type: "button",
        "aria-expanded": String(isOpen),
        "aria-controls": detailId,
        "aria-label": (isOpen ? "Hide" : "Show") + " details of " + experiment.name,
      },
      [chevronIcon("down")]
    );
    const body = detail(experiment, detailId);
    const line = el("div", { class: "device-row experiment-row" }, [
      el("span", { class: "experiment-name", title: experiment.name, text: experiment.name }),
      el("span", { class: "experiment-by", text: experiment.created_by ? "By " + experiment.created_by : "" }),
      el("span", { class: "experiment-updated", title: new Date(experiment.updated_at).toLocaleString(), text: ago(experiment.updated_at) }),
      el("span", { class: "experiment-count", text: plural(experiment.devices.length, "device") }),
      el("a", { class: "device-control", href: "/experiments/" + encodeURIComponent(experiment.id), text: "View Experiment" }),
      chevron,
    ]);
    line.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      const open = body.hidden;
      body.hidden = !open;
      chevron.setAttribute("aria-expanded", String(open));
      chevron.setAttribute("aria-label", (open ? "Hide" : "Show") + " details of " + experiment.name);
      if (open) expanded.add(experiment.id);
      else expanded.delete(experiment.id);
      store.set("sessionStorage", EXPANDED_KEY, [...expanded]);
    });
    return el("li", { class: "device experiment" }, [line, body]);
  }

  function render(force) {
    if (experiments === null) return;
    const liveKey = live ? [...live.values()].map((d) => d.id + d.state).join() : "";
    const signature = JSON.stringify([experiments, unreadable, liveKey, Object.keys(opticLabels).length]);
    if (!force && signature === lastSignature) return;
    lastSignature = signature;

    summary.textContent = experiments.length
      ? plural(experiments.length, "experiment") + " · last saved " + ago(experiments[0].updated_at)
      : "No experiments yet";
    count.textContent = experiments.length ? "newest first" : "";
    const items = experiments.map(row);
    for (const bad of unreadable) {
      items.push(
        el("li", { class: "empty experiment-unreadable" }, [
          "Can't read " + bad.file + ": " + bad.error + ". Fix or remove it in the experiments folder.",
        ])
      );
    }
    if (!items.length) {
      items.push(
        el("li", {
          class: "empty",
          text: "No experiments yet. Press New experiment, or ask Photo-Agent to set one up.",
        })
      );
    }
    list.replaceChildren(...items);
  }

  // ---- Create and delete --------------------------------------------------------

  async function createExperiment() {
    const name = el("input", { maxlength: "120", placeholder: "e.g. AOM frequency scan" });
    const description = el("textarea", { maxlength: "4000", placeholder: "What it measures, and why (optional)" });
    const author = el("input", { maxlength: "64", placeholder: "Your name (optional)" });
    author.value = store.get("localStorage", AUTHOR_KEY, "");
    const choice = await dialog({
      title: "New experiment",
      body: el("div", { class: "dialog-fields" }, [
        el("label", {}, ["Name", name]),
        el("label", {}, ["Description", description]),
        el("label", {}, ["Created by", author]),
      ]),
      actions: [{ label: "Create", value: "create", primary: true }],
      validate: () => (name.value.trim() ? null : "Give the experiment a name."),
    });
    if (choice !== "create") return;
    store.set("localStorage", AUTHOR_KEY, author.value.trim());
    try {
      const response = await fetch("/api/experiments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.value.trim(), description: description.value.trim(), created_by: author.value.trim() }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "HTTP " + response.status);
      window.location.href = "/experiments/" + encodeURIComponent(body.experiment.id);
    } catch (error) {
      showBanner("Couldn't create the experiment: " + error.message);
    }
  }

  async function removeExperiment(experiment) {
    const choice = await dialog({
      title: "Delete “" + experiment.name + "”?",
      body: el("p", {
        text: "It moves to the trash folder on the Control Center computer, where an administrator can " +
          "restore it for 30 days.",
      }),
      actions: [{ label: "Delete", value: "delete", danger: true }],
    });
    if (choice !== "delete") return;
    const response = await fetch(
      "/api/experiments/" + encodeURIComponent(experiment.id) + "?version=" + experiment.version,
      { method: "DELETE" }
    );
    if (response.status === 409) showBanner("“" + experiment.name + "” changed since this page loaded; check it and try again.");
    else if (!response.ok) showBanner("Couldn't delete it (HTTP " + response.status + ").");
    await load();
  }

  function showBanner(text) {
    notice.hidden = false;
    notice.textContent = text;
    window.setTimeout(() => (notice.hidden = true), 10000);
  }

  document.getElementById("new-experiment").addEventListener("click", createExperiment);
  watchNetwork((snapshot) => {
    live = new Map(allDevices(snapshot).map((device) => [device.id, device]));
    render();
  });
  load();
  loadMeta();
  window.setInterval(load, 30000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) load();
  });
})();
