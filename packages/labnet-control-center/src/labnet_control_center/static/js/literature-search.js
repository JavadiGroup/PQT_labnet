// The literature search box on an Automation project page: keyword search over
// the project's part of the lab library and its own papers, the same search
// the agent's search_literature tool uses. Each result cites "<path> p.<N>" and
// opens the paper at that page in a new tab.
//
// Paper text is never treated as HTML: a snippet arrives with \u0002 and \u0003
// around each hit, and is split on those into text nodes and <mark> elements.

(function () {
  "use strict";

  const { el, plural } = window.LabNet;
  const container = document.getElementById("literature-search");
  const match = window.location.pathname.match(/^\/automation\/([a-z0-9][a-z0-9-]{0,40}-[a-z0-9]{6})\/?$/);
  if (!container || !match) return;
  const projectId = match[1];
  const api = "/api/automation/projects/" + encodeURIComponent(projectId) + "/literature/";
  const MAX_QUERY = 500;

  let paper = null; // search within one paper, or null for all
  let token = 0; // ignores a slow reply after a newer search

  const input = el("input", {
    type: "search",
    class: "literature-query",
    maxlength: String(MAX_QUERY),
    placeholder: "Search this project's papers, e.g. Pound–Drever–Hall error signal",
    "aria-label": "Search this project's papers",
  });
  const submit = el("button", { class: "button small primary", type: "submit", text: "Search" });
  const filter = el("span", { class: "literature-filter", hidden: true });
  const form = el("form", { class: "literature-form", role: "search" }, [input, filter, submit]);
  const status = el("p", { class: "literature-status", hidden: true });
  const results = el("ol", { class: "literature-results", hidden: true });
  const close = el("button", { class: "button small", type: "button", text: "Close", hidden: true });
  container.replaceChildren(form, el("div", { class: "literature-output" }, [status, results]), close);

  function pathUrl(path) {
    return path.split("/").map(encodeURIComponent).join("/");
  }

  function paperUrl(item) {
    const base = item.source === "library"
      ? "/api/automation/library/files/"
      : "/api/automation/projects/" + encodeURIComponent(projectId) + "/files/";
    const isPdf = /\.pdf$/i.test(item.path);
    return base + pathUrl(item.path) + (isPdf ? "#page=" + item.page : "");
  }

  // "…the \u0002error signal\u0003 is…" -> [text, <mark>error signal</mark>, text]
  function snippetNodes(snippet) {
    const nodes = [];
    let marked = false;
    let text = "";
    const flush = () => {
      if (text) nodes.push(marked ? el("mark", { text }) : document.createTextNode(text));
      text = "";
    };
    for (const character of String(snippet)) {
      if (character === "\u0002" || character === "\u0003") {
        flush();
        marked = character === "\u0002";
      } else {
        text += character;
      }
    }
    flush();
    return nodes;
  }

  function setFilter(path) {
    paper = path;
    if (!path) {
      filter.hidden = true;
      filter.replaceChildren();
      return;
    }
    const clear = el("button", { class: "literature-filter-clear", type: "button", "aria-label": "Search all papers", text: "×" });
    clear.addEventListener("click", () => {
      setFilter(null);
      if (input.value.trim()) search();
    });
    filter.replaceChildren(el("span", { text: "In " + path.split("/").pop() }), clear);
    filter.title = path;
    filter.hidden = false;
  }

  function result(item) {
    const within = el("button", { class: "literature-within", type: "button", text: "Search this paper" });
    within.addEventListener("click", () => {
      setFilter(item.path);
      search();
    });
    return el("li", { class: "literature-result" }, [
      el("div", { class: "literature-cite" }, [
        el("a", { href: paperUrl(item), target: "_blank", rel: "noopener", title: "Open at page " + item.page, text: item.cite }),
        el("span", { class: "zone-badge " + (item.source === "library" ? "owner-shared" : "owner-user"),
          text: item.source === "library" ? "Lab library" : "Project" }),
        paper ? "" : within,
      ]),
      el("p", { class: "literature-snippet" }, snippetNodes(item.snippet)),
    ]);
  }

  function show(text, tone) {
    status.textContent = text;
    status.className = "literature-status" + (tone ? " " + tone : "");
    status.hidden = !text;
    close.hidden = false;
  }

  async function search() {
    const query = input.value.trim();
    if (!query) return;
    const mine = ++token;
    submit.disabled = true;
    show("Searching…");
    results.hidden = true;
    const params = new URLSearchParams({ q: query, k: "20" });
    if (paper) params.set("paper", paper);
    try {
      const response = await fetch(api + "search?" + params, { cache: "no-store" });
      let body = null;
      try {
        body = await response.json();
      } catch (_ignored) {
        /* not JSON */
      }
      if (mine !== token) return;
      if (!response.ok) {
        const detail = body && typeof body.detail === "string" ? body.detail : "HTTP " + response.status;
        throw new Error(detail);
      }
      const found = body.results || [];
      const notes = (body.notes || []).join(" ");
      let summary = found.length
        ? plural(found.length, "page") + (body.mode === "any" ? " with some of the words (no page has them all)." : " with all the words.")
        : "Nothing found.";
      if (notes) summary += " " + notes;
      show(summary, found.length ? "" : "muted");
      results.replaceChildren(...found.map(result));
      results.hidden = !found.length;
    } catch (error) {
      if (mine === token) show("Couldn't search: " + error.message, "error");
    } finally {
      if (mine === token) submit.disabled = false;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    search();
  });
  close.addEventListener("click", () => {
    token++;
    status.hidden = true;
    results.hidden = true;
    results.replaceChildren();
    close.hidden = true;
    setFilter(null);
    submit.disabled = false;
  });
})();
