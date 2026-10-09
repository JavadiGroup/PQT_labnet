// Automation page: every project, newest first, and the shared lab library.
// A row expands to show the project's linked experiments and file counts.

(function () {
  "use strict";

  const { el, chevronIcon, store, dialog, ago, plural } = window.LabNet;
  const EXPANDED_KEY = "labnet-expanded-projects";
  const AUTHOR_KEY = "labnet-experiment-author"; // the same name as on the Experiments tab
  const OWNER_TEXT = { user: "Yours", agent: "Agent", mirror: "Automatic" };
  const expanded = new Set(store.get("sessionStorage", EXPANDED_KEY, []));
  const list = document.getElementById("projects");
  const summary = document.getElementById("summary");
  const count = document.getElementById("project-count");
  const notice = document.getElementById("notice");

  let projects = null; // null until the first load
  let unreadable = [];
  let lastSignature = "";

  // ---- Projects -----------------------------------------------------------------

  async function load() {
    try {
      const response = await fetch("/api/automation/projects", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const body = await response.json();
      projects = body.projects;
      unreadable = body.unreadable;
      render();
    } catch (error) {
      summary.textContent = "Couldn't load projects (" + error.message + ").";
    }
  }

  function countsText(counts) {
    return [
      plural(counts.literature, "paper"),
      plural(counts.data, "data file"),
      plural(counts.tasks, "task"),
      plural(counts.analysis, "analysis file"),
    ].join(" · ");
  }

  function detail(project, detailId) {
    const remove = el("button", { class: "button danger small", type: "button", text: "Delete…" });
    remove.addEventListener("click", () => removeProject(project));
    const linked = project.linked_experiments;
    return el("div", { class: "device-detail", id: detailId, hidden: !expanded.has(project.id) }, [
      project.description ? el("p", { class: "experiment-description", text: project.description }) : "",
      el("h3", { text: linked.length ? "Linked experiments" : "No experiments linked yet" }),
      linked.length
        ? el(
            "ul",
            { class: "experiment-devices" },
            linked.map((item) =>
              el("li", { class: "linked-experiment" }, [
                el("a", { href: "/experiments/" + encodeURIComponent(item.id), text: item.name || item.id }),
                el("span", { class: "experiment-device-type", text: "version " + item.version }),
              ])
            )
          )
        : "",
      el("dl", { class: "facts" }, [
        el("div", {}, [el("dt", { text: "Files" }), el("dd", { text: countsText(project.counts) })]),
        el("div", {}, [
          el("dt", { text: "Automation may control" }),
          el("dd", { text: project.allowed_devices.length ? project.allowed_devices.join(", ") : "no devices" }),
        ]),
        el("div", {}, [
          el("dt", { text: "Saved" }),
          el("dd", {
            text: new Date(project.updated_at).toLocaleString() +
              (project.updated_by ? " by " + project.updated_by : "") + " · version " + project.version,
          }),
        ]),
      ]),
      el("div", { class: "experiment-actions" }, [remove]),
    ]);
  }

  function row(project) {
    const detailId = "project-" + project.id;
    const isOpen = expanded.has(project.id);
    const chevron = el(
      "button",
      {
        class: "chevron",
        type: "button",
        "aria-expanded": String(isOpen),
        "aria-controls": detailId,
        "aria-label": (isOpen ? "Hide" : "Show") + " details of " + project.name,
      },
      [chevronIcon("down")]
    );
    const body = detail(project, detailId);
    const line = el("div", { class: "device-row project-row" }, [
      el("span", { class: "experiment-name", title: project.name, text: project.name }),
      el("span", { class: "experiment-by", text: project.created_by ? "By " + project.created_by : "" }),
      el("span", { class: "experiment-updated", title: new Date(project.updated_at).toLocaleString(), text: ago(project.updated_at) }),
      el("span", { class: "experiment-count", text: plural(project.counts.tasks, "task") + " · " + plural(project.counts.literature, "paper") }),
      el("a", { class: "device-control", href: "/automation/" + encodeURIComponent(project.id), text: "Open project" }),
      chevron,
    ]);
    line.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      const open = body.hidden;
      body.hidden = !open;
      chevron.setAttribute("aria-expanded", String(open));
      chevron.setAttribute("aria-label", (open ? "Hide" : "Show") + " details of " + project.name);
      if (open) expanded.add(project.id);
      else expanded.delete(project.id);
      store.set("sessionStorage", EXPANDED_KEY, [...expanded]);
    });
    return el("li", { class: "device project" }, [line, body]);
  }

  function render() {
    if (projects === null) return;
    const signature = JSON.stringify([projects, unreadable]);
    if (signature === lastSignature) return;
    lastSignature = signature;
    summary.textContent = projects.length
      ? plural(projects.length, "project") + " · last saved " + ago(projects[0].updated_at)
      : "No projects yet";
    count.textContent = projects.length ? "newest first" : "";
    const items = projects.map(row);
    for (const bad of unreadable) {
      items.push(
        el("li", { class: "empty experiment-unreadable" }, [
          "Can't read project " + bad.project + ": " + bad.error + ". Fix or remove it in the automation folder.",
        ])
      );
    }
    if (!items.length) {
      items.push(
        el("li", {
          class: "empty",
          text: "No projects yet. Press New project to start one; it comes with the folders shown below.",
        })
      );
    }
    list.replaceChildren(...items);
  }

  // ---- Create and delete ---------------------------------------------------------

  async function createProject() {
    const name = el("input", { maxlength: "120", placeholder: "e.g. AOM efficiency study" });
    const description = el("textarea", { maxlength: "4000", placeholder: "What the project is for (optional)" });
    const author = el("input", { maxlength: "64", placeholder: "Your name (optional)" });
    author.value = store.get("localStorage", AUTHOR_KEY, "");
    // Signed in (lab): the server records the login, so there is no name to type.
    const { user } = await window.LabNet.me;
    const choice = await dialog({
      title: "New project",
      body: el("div", { class: "dialog-fields" }, [
        el("label", {}, ["Name", name]),
        el("label", {}, ["Description", description]),
        el("label", { hidden: Boolean(user) }, ["Created by", author]),
      ]),
      actions: [{ label: "Create", value: "create", primary: true }],
      validate: () => (name.value.trim() ? null : "Give the project a name."),
    });
    if (choice !== "create") return;
    if (!user) store.set("localStorage", AUTHOR_KEY, author.value.trim());
    try {
      const response = await fetch("/api/automation/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.value.trim(), description: description.value.trim(), created_by: author.value.trim() }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "HTTP " + response.status);
      window.location.href = "/automation/" + encodeURIComponent(body.project.id);
    } catch (error) {
      showNotice("Couldn't create the project: " + error.message);
    }
  }

  async function removeProject(project) {
    const choice = await dialog({
      title: "Delete “" + project.name + "”?",
      body: el("p", {
        text: "The whole project folder moves to the trash on the Control Center computer, where an " +
          "administrator can restore it for 30 days.",
      }),
      actions: [{ label: "Delete", value: "delete", danger: true }],
    });
    if (choice !== "delete") return;
    const response = await fetch(
      "/api/automation/projects/" + encodeURIComponent(project.id) + "?version=" + project.version,
      { method: "DELETE" }
    );
    if (response.status === 409) showNotice("“" + project.name + "” changed since this page loaded; check it and try again.");
    else if (!response.ok) showNotice("Couldn't delete it (HTTP " + response.status + ").");
    await load();
  }

  function showNotice(text) {
    notice.hidden = false;
    notice.textContent = text;
    window.setTimeout(() => (notice.hidden = true), 10000);
  }

  // ---- The library and the folder guide -----------------------------------------------

  async function loadLibrary() {
    const target = document.getElementById("library");
    const libraryCount = document.getElementById("library-count");
    try {
      const library = await (await fetch("/api/automation/library", { cache: "no-store" })).json();
      if (!library.configured) {
        target.replaceChildren(
          el("p", {
            class: "card-intro",
            text: "No lab library is set up. Set LABNET_AGENT_LITERATURE_DIR to the folder of papers; every " +
              "project (and Photo-Agent) can then read it.",
          })
        );
        return;
      }
      const papers = library.entries.filter((entry) => entry.kind === "file");
      const topics = new Map();
      for (const paper of papers) {
        const topic = paper.path.includes("/") ? paper.path.split("/")[0] : "(top level)";
        topics.set(topic, (topics.get(topic) || 0) + 1);
      }
      libraryCount.textContent = plural(papers.length, "paper") + " · " + plural(topics.size, "topic");
      const chips = [...topics.entries()].map(([topic, n]) =>
        el("li", {}, [el("span", { text: topic.replace(/_/g, " ") }), el("b", { text: String(n) })])
      );
      const collections = library.collections.map((collection) =>
        el("li", {}, [el("span", { text: collection.name + " (reading list)" }), el("b", { text: String(collection.papers.length) })])
      );
      target.replaceChildren(
        el("p", {
          class: "card-intro",
          text: "Shared by every project: open a project and look under Literature → Lab library. Each project " +
            "can narrow the agent's search to some topics or a reading list.",
        }),
        el("ul", { class: "topic-chips" }, [...chips, ...collections]),
        indexBox
      );
      loadIndex();
    } catch (error) {
      target.replaceChildren(el("p", { class: "card-intro", text: "Couldn't load the library (" + error.message + ")." }));
    }
  }

  // ---- The library's search index (see automation_literature.py) ------------------------
  // Papers' text, page by page, for the project search box and the agent's search_literature.

  const indexBox = el("div", { class: "library-index" });
  let indexTimer = null;

  async function loadIndex() {
    window.clearTimeout(indexTimer);
    try {
      const response = await fetch("/api/automation/library/index", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      renderIndex((await response.json()).index);
    } catch (error) {
      indexBox.replaceChildren(el("p", { class: "library-index-line error", text: "Couldn't read the search index (" + error.message + ")." }));
    }
  }

  function renderIndex(index) {
    const running = index.state === "running";
    const button = el("button", {
      class: "button small",
      type: "button",
      text: running ? "Indexing…" : "Index library",
      disabled: running || !index.extractor,
      title: "Read every paper's text so projects (and their agents) can search it. Only new or changed papers are read again.",
    });
    button.addEventListener("click", startIndex);
    const facts = [plural(index.papers, "paper") + " indexed"];
    if (index.no_text) facts.push(index.no_text + " without a text layer (scans: not searchable)");
    if (index.errors) facts.push(plural(index.errors, "paper") + " couldn't be read");
    if (!running && index.finished_at) facts.push("updated " + ago(index.finished_at));
    const lines = [el("div", { class: "library-index-head" }, [el("span", { text: "Search index: " + facts.join(" · ") }), button])];
    if (running) {
      lines.push(
        el("progress", { class: "library-index-progress", max: String(index.total || 1), value: String(index.done) }),
        el("p", {
          class: "library-index-line",
          text: "Reading " + index.done + " of " + index.total + (index.current ? ": " + index.current : "…"),
        })
      );
    } else if (index.message) {
      lines.push(el("p", { class: "library-index-line" + (index.state === "failed" ? " error" : ""), text: index.message }));
    }
    if (!index.extractor) {
      lines.push(el("p", {
        class: "library-index-line error",
        text: "Reading PDFs needs pypdf on the Control Center: pip install \"pypdf>=5,<7\" (part of the control-center extra).",
      }));
    }
    indexBox.replaceChildren(...lines);
    if (running) indexTimer = window.setTimeout(loadIndex, 2000);
  }

  async function startIndex() {
    try {
      const response = await fetch("/api/automation/library/index", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok || response.status === 409) {
        if (body.index) renderIndex(body.index);
        else loadIndex();
        return;
      }
      throw new Error(typeof body.detail === "string" ? body.detail : "HTTP " + response.status);
    } catch (error) {
      showNotice("Couldn't start indexing: " + error.message);
      loadIndex();
    }
  }

  async function loadGuide() {
    try {
      const meta = await (await fetch("/api/automation/meta")).json();
      document.getElementById("zone-guide").replaceChildren(
        ...meta.zones.map((zone) =>
          el("li", {}, [
            el("code", { text: zone.path + (zone.exact ? "" : "/") }),
            el("span", { class: "zone-badge owner-" + zone.owner, text: OWNER_TEXT[zone.owner] }),
            el("span", { class: "zone-label", text: zone.label }),
          ])
        )
      );
    } catch (_ignored) {
      /* the guide is a nicety */
    }
  }

  document.getElementById("new-project").addEventListener("click", createProject);
  load();
  loadLibrary();
  loadGuide();
  window.setInterval(load, 30000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) load();
  });
})();
