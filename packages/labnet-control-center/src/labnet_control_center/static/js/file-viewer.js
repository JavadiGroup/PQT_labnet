// The middle of an Automation project page: shows one file. Markdown renders
// as a document (and the person's own files can be edited), CSV/TSV as a
// table, images and PDFs as themselves, code and text in a code block.
// createFileViewer(container, options) -> {show, showNode, editing, current}.

(function () {
  "use strict";

  const { el, sizeText, fileKind } = window.LabNet;
  const TEXT_PREVIEW_BYTES = 2 * 1024 * 1024;
  const TABLE_PREVIEW_BYTES = 5 * 1024 * 1024;
  const TABLE_ROWS = 500;
  const OWNER_TEXT = { user: "Yours", agent: "Agent", mirror: "Automatic", shared: "Shared" };

  // Delimited text (quotes, doubled quotes, CRLF) into rows, stopping at maxRows.
  function parseDelimited(text, delimiter, maxRows) {
    const rows = [];
    let row = [];
    let field = "";
    let quoted = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '"') {
          if (text[i + 1] === '"') {
            field += '"';
            i++;
          } else {
            quoted = false;
          }
        } else {
          field += c;
        }
      } else if (c === '"' && field === "") {
        quoted = true;
      } else if (c === delimiter) {
        row.push(field);
        field = "";
      } else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field);
        rows.push(row);
        row = [];
        field = "";
        if (rows.length >= maxRows) return { rows, more: i < text.length - 1 };
      } else {
        field += c;
      }
    }
    if (field !== "" || row.length) {
      row.push(field);
      rows.push(row);
    }
    return { rows, more: false };
  }

  function createFileViewer(container, options) {
    let current = null; // {source, path, entry}
    let editing = false;
    let dirty = false;
    let token = 0; // ignores a slow load that finished after another file was opened

    window.addEventListener("beforeunload", (event) => {
      if (dirty) event.preventDefault();
    });

    async function fetchText(item) {
      const response = await fetch(options.urlFor(item), { cache: "no-store" });
      if (!response.ok) {
        let detail = "HTTP " + response.status;
        try {
          detail = (await response.json()).detail || detail;
        } catch (_ignored) {
          /* not JSON */
        }
        throw new Error(detail);
      }
      return response.text();
    }

    function owner(item) {
      if (item.source === "library") return "shared";
      return item.entry && item.entry.owner;
    }

    function header(item, extraActions) {
      const parts = item.path.split("/");
      const crumbs = [
        el("span", { class: "viewer-root", text: item.source === "library" ? "Lab library" : "Project" }),
        ...parts.map((part, index) =>
          el("span", { class: index === parts.length - 1 ? "viewer-file" : "viewer-folder", text: part })
        ),
      ];
      const facts = [];
      if (item.entry && item.entry.size !== undefined) facts.push(sizeText(item.entry.size));
      if (item.entry && item.entry.modified) facts.push("changed " + window.LabNet.ago(item.entry.modified));
      const who = owner(item);
      const download = el("a", {
        class: "button small",
        href: options.urlFor(item, { download: true }),
        download: parts[parts.length - 1],
        text: "Download",
      });
      return el("div", { class: "viewer-head" }, [
        el("div", { class: "viewer-path" }, crumbs),
        el("div", { class: "viewer-meta" }, [
          who ? el("span", { class: "zone-badge owner-" + who, text: OWNER_TEXT[who] }) : "",
          el("span", { text: facts.join(" · ") }),
        ]),
        el("div", { class: "viewer-actions" }, [...(extraActions || []), download]),
      ]);
    }

    function body(className, children) {
      return el("div", { class: "viewer-body" + (className ? " " + className : "") }, children);
    }

    function message(text, tone) {
      return el("p", { class: "viewer-message" + (tone ? " " + tone : ""), text });
    }

    function itemActions(item) {
      if (!options.canManage(item)) return [];
      const rename = el("button", { class: "button small", type: "button", text: "Rename…" });
      rename.addEventListener("click", () => options.rename(item));
      const remove = el("button", { class: "button small", type: "button", text: "Delete…" });
      remove.addEventListener("click", () => options.remove(item));
      return [rename, remove];
    }

    // ---- Markdown: view, and edit for the person's own files ----------------------

    async function showMarkdown(item, mine) {
      const text = await fetchText(item);
      if (mine !== token) return;
      const writable = options.writable(item);
      const viewButton = el("button", { class: "segment active", type: "button", text: "View" });
      const editButton = el("button", { class: "segment", type: "button", text: "Edit" });
      const segments = writable ? [el("div", { class: "segments" }, [viewButton, editButton])] : [];
      const content = el("div", { class: "md md-doc" });
      const area = body("doc", [content]);
      container.replaceChildren(header(item, [...segments, ...itemActions(item)]), area);
      let source = text;

      function view() {
        editing = false;
        dirty = false;
        viewButton.classList.add("active");
        editButton.classList.remove("active");
        content.replaceChildren(window.LabNet.markdown.render(source, { doc: options.doc(item) }));
        if (!source.trim()) content.append(message("This file is empty."));
        area.replaceChildren(content);
      }

      function edit() {
        editing = true;
        viewButton.classList.remove("active");
        editButton.classList.add("active");
        const editor = el("textarea", { class: "doc-editor", spellcheck: "true", "aria-label": "Edit " + item.path });
        editor.value = source;
        const status = el("span", { class: "panel-status" });
        const save = el("button", { class: "button primary", type: "button", text: "Save" });
        const cancel = el("button", { class: "button", type: "button", text: "Cancel" });
        editor.addEventListener("input", () => {
          dirty = editor.value !== source;
          status.textContent = dirty ? "Unsaved changes" : "";
        });
        editor.addEventListener("keydown", (event) => {
          if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
            event.preventDefault();
            save.click();
          }
        });
        save.addEventListener("click", async () => {
          save.disabled = true;
          status.textContent = "Saving…";
          try {
            const entry = await options.save(item, editor.value);
            item.entry = entry;
            source = editor.value;
            container.querySelector(".viewer-head").replaceWith(header(item, [...segments, ...itemActions(item)]));
            view();
          } catch (error) {
            status.textContent = error.message;
            status.classList.add("error");
          } finally {
            save.disabled = false;
          }
        });
        cancel.addEventListener("click", async () => {
          if (dirty && !(await confirmDiscard())) return;
          view();
        });
        area.replaceChildren(
          el("div", { class: "doc-edit" }, [
            editor,
            el("div", { class: "doc-edit-bar" }, [status, el("span", { class: "spacer" }), cancel, save]),
          ])
        );
        editor.focus();
      }

      viewButton.addEventListener("click", async () => {
        if (editing && dirty && !(await confirmDiscard())) return;
        view();
      });
      editButton.addEventListener("click", () => {
        if (!editing) edit();
      });
      if (writable && item.edit) edit();
      else view();
    }

    async function confirmDiscard() {
      const choice = await window.LabNet.dialog({
        title: "Discard your changes?",
        body: el("p", { text: "Your edits to this file haven't been saved." }),
        actions: [{ label: "Discard", value: "discard", danger: true }],
      });
      return choice === "discard";
    }

    // ---- Other kinds ------------------------------------------------------------

    async function showTable(item, mine) {
      if (item.entry && item.entry.size > TABLE_PREVIEW_BYTES) {
        container.replaceChildren(header(item, itemActions(item)), body("", [message("Too large to preview here; download it instead.")]));
        return;
      }
      const text = await fetchText(item);
      if (mine !== token) return;
      const delimiter = item.path.toLowerCase().endsWith(".tsv") ? "\t" : ",";
      const { rows, more } = parseDelimited(text, delimiter, TABLE_ROWS + 1);
      const [head, ...data] = rows;
      const table = el("table", { class: "csv-table" }, [
        el("thead", {}, [el("tr", {}, (head || []).map((cell) => el("th", { text: cell })))]),
        el("tbody", {}, data.slice(0, TABLE_ROWS).map((row) => el("tr", {}, row.map((cell) => el("td", { text: cell }))))),
      ]);
      const note = data.length > TABLE_ROWS || more
        ? message("Showing the first " + TABLE_ROWS + " rows. Download the file to see them all.")
        : message(data.length + " rows · " + (head ? head.length : 0) + " columns");
      container.replaceChildren(header(item, itemActions(item)), body("table", [note, el("div", { class: "csv-scroll" }, [table])]));
    }

    async function showText(item, mine, language) {
      if (item.entry && item.entry.size > TEXT_PREVIEW_BYTES) {
        container.replaceChildren(header(item, itemActions(item)), body("", [message("Too large to preview here; download it instead.")]));
        return;
      }
      let text = await fetchText(item);
      if (mine !== token) return;
      if (language === "json") {
        try {
          text = JSON.stringify(JSON.parse(text), null, 2);
        } catch (_ignored) {
          /* show it as it is */
        }
      }
      container.replaceChildren(
        header(item, itemActions(item)),
        body("code", [window.LabNet.markdown.codeBlock(language, text.replace(/\r\n?/g, "\n").split("\n"))])
      );
    }

    function showImage(item) {
      const image = el("img", { class: "viewer-image", src: options.urlFor(item), alt: item.path });
      container.replaceChildren(header(item, itemActions(item)), body("image", [image]));
    }

    function showPdf(item) {
      const frame = el("iframe", { class: "viewer-pdf", src: options.urlFor(item), title: item.path });
      container.replaceChildren(header(item, itemActions(item)), body("pdf", [frame]));
    }

    async function show(item) {
      if (editing && dirty && !(await confirmDiscard())) return false;
      editing = false;
      dirty = false;
      const mine = ++token;
      current = item;
      container.replaceChildren(header(item), body("", [message("Loading…")]));
      const name = item.path.split("/").pop();
      const ext = (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
      try {
        const kind = fileKind(name);
        if (kind === "md") await showMarkdown(item, mine);
        else if (kind === "table") await showTable(item, mine);
        else if (kind === "image") showImage(item);
        else if (kind === "pdf") showPdf(item);
        else if (ext === ".json") await showText(item, mine, "json");
        else if (ext === ".py") await showText(item, mine, "python");
        else if ([".txt", ".log", ".dat", ".bib", ".ris", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
                  ".tex", ".m", ".ipynb", ".sh", ".ps1", ".c", ".h", ".cpp", ".js", ".ts", ".proto"].includes(ext)) {
          await showText(item, mine, ext.slice(1));
        } else {
          container.replaceChildren(header(item, itemActions(item)), body("", [message("No preview for this kind of file.")]));
        }
      } catch (error) {
        if (mine === token) {
          container.replaceChildren(header(item, itemActions(item)), body("", [message("Couldn't open it: " + error.message, "error")]));
        }
      }
      return true;
    }

    async function showNode(node) {
      if (editing && dirty && !(await confirmDiscard())) return false;
      editing = false;
      dirty = false;
      token++;
      current = null;
      container.replaceChildren(node);
      return true;
    }

    return {
      show,
      showNode,
      editing: () => editing,
      current: () => current,
    };
  }

  Object.assign(window.LabNet, { createFileViewer, parseDelimited });
})();
