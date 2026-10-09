// The file tree on an Automation project page: the project's folders in a
// fixed order, with the shared lab library inside Literature. Folders people
// own take uploads (button or drop); the agent's and the Control Center's are
// marked read-only. createFileTree(container, options) -> {render, select}.

(function () {
  "use strict";

  const { el, store } = window.LabNet;
  const SECTION_ORDER = ["Literature", "Data", "Experiment_layout", "Tasks", "Protocols", "ScriptsUtilized", "Analysis"];
  const OWNER_TEXT = { user: "Yours", agent: "Agent", mirror: "Automatic", shared: "Shared" };
  const OWNER_TITLE = {
    user: "You add and edit files here",
    agent: "Written by the agent; read-only here",
    mirror: "Copied from the Experiments tab by the Control Center",
    shared: "The lab library, shared by every project; read-only here",
  };

  function sizeText(bytes) {
    if (bytes === undefined || bytes === null) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(bytes < 10240 ? 1 : 0) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  function fileKind(name) {
    const ext = (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    if (ext === ".pdf") return "pdf";
    if (ext === ".md") return "md";
    if ([".csv", ".tsv"].includes(ext)) return "table";
    if ([".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"].includes(ext)) return "image";
    if ([".py", ".json", ".jsonl", ".m", ".ipynb"].includes(ext)) return "code";
    return "text";
  }

  // Flat entries ({path, name, kind, ...}) into nested nodes.
  function nest(entries) {
    const root = { children: [] };
    const byPath = new Map([["", root]]);
    for (const entry of entries) {
      const parentPath = entry.path.includes("/") ? entry.path.slice(0, entry.path.lastIndexOf("/")) : "";
      const parent = byPath.get(parentPath);
      if (!parent) continue; // its folder was cut off by the listing limit
      const node = { entry, children: [] };
      parent.children.push(node);
      if (entry.kind === "dir") byPath.set(entry.path, node);
    }
    const sortNodes = (node) => {
      node.children.sort((a, b) => {
        const aDir = a.entry.kind === "dir" ? 0 : 1;
        const bDir = b.entry.kind === "dir" ? 0 : 1;
        return aDir - bDir || a.entry.name.localeCompare(b.entry.name, undefined, { numeric: true, sensitivity: "base" });
      });
      node.children.forEach(sortNodes);
    };
    sortNodes(root);
    return root;
  }

  function countFiles(node) {
    return node.children.reduce((total, child) => total + (child.entry.kind === "dir" ? countFiles(child) : 1), 0);
  }

  function createFileTree(container, options) {
    const openKey = "labnet-tree-open-" + options.storageKey;
    const open = new Set(store.get("sessionStorage", openKey, []));
    let selectedKey = null;

    function remember(key, isOpen) {
      if (isOpen) open.add(key);
      else open.delete(key);
      store.set("sessionStorage", openKey, [...open]);
    }

    function badge(owner) {
      return el("span", { class: "zone-badge owner-" + owner, title: OWNER_TITLE[owner], text: OWNER_TEXT[owner] });
    }

    function actionButton(label, title, onClick) {
      const button = el("button", { class: "tree-action", type: "button", title, "aria-label": title, text: label });
      button.addEventListener("click", (event) => {
        event.preventDefault(); // a button in a <summary> must not also toggle it
        event.stopPropagation();
        onClick();
      });
      return button;
    }

    function uploadButton(folder) {
      const input = el("input", { type: "file", multiple: true, hidden: true, tabindex: "-1" });
      input.addEventListener("change", () => {
        if (input.files.length) options.onUpload(folder, [...input.files]);
        input.value = "";
      });
      const button = actionButton("Upload", "Add files to " + folder, () => input.click());
      return [button, input];
    }

    function acceptDrops(target, folder) {
      target.addEventListener("dragover", (event) => {
        if (![...event.dataTransfer.types].includes("Files")) return;
        event.preventDefault();
        target.classList.add("dropping");
      });
      target.addEventListener("dragleave", () => target.classList.remove("dropping"));
      target.addEventListener("drop", (event) => {
        target.classList.remove("dropping");
        if (!event.dataTransfer.files.length) return;
        event.preventDefault();
        options.onUpload(folder, [...event.dataTransfer.files]);
      });
    }

    function fileButton(source, entry, label) {
      const key = source + ":" + entry.path;
      const kind = entry.kind === "link" ? "link" : fileKind(entry.name);
      const button = el(
        "button",
        {
          class: "tree-file" + (key === selectedKey ? " selected" : "") + (entry.refused ? " refused" : ""),
          type: "button",
          title: entry.refused ? entry.refused : entry.path,
          "data-key": key,
        },
        [
          el("span", { class: "tree-icon kind-" + kind, "aria-hidden": "true" }),
          el("span", { class: "tree-name", text: label || entry.name }),
          el("span", { class: "tree-size", text: entry.kind === "link" ? "link" : sizeText(entry.size) }),
        ]
      );
      button.disabled = Boolean(entry.refused) || entry.kind === "link";
      button.addEventListener("click", () => {
        select(source, entry.path);
        options.onOpen({ source, path: entry.path, entry });
      });
      return button;
    }

    // One folder: <details> with its name, owner badge, file count and actions.
    function folder(source, node, { label, owner, actions, section, defaultOpen }) {
      const path = node.entry ? node.entry.path : label;
      const key = source + ":" + path;
      const details = el("details", { class: "tree-folder" + (section ? " tree-section" : "") });
      details.open = open.has(key) || (defaultOpen && !open.has("closed:" + key));
      details.addEventListener("toggle", () => {
        remember(key, details.open);
        remember("closed:" + key, !details.open);
      });
      const count = countFiles(node);
      const summary = el("summary", {}, [
        el("span", { class: "tree-name", text: label || node.entry.name }),
        owner ? badge(owner) : "",
        el("span", { class: "tree-count", text: count ? String(count) : "" }),
        ...(actions || []),
      ]);
      const children = node.children.map((child) => el("li", {}, [renderNode(source, child)]));
      if (!children.length) {
        children.push(el("li", { class: "tree-empty", text: owner === "user" ? "Empty: drop files here" : "Empty" }));
      }
      details.append(summary, el("ul", { class: "tree-list" }, children));
      return details;
    }

    function renderNode(source, node) {
      const entry = node.entry;
      if (entry.kind !== "dir") return fileButton(source, entry);
      const zone = source === "project" ? options.zoneOf(entry.path) : null;
      const writable = zone && zone.owner === "user" && !zone.exact && !entry.refused;
      const actions = writable
        ? [...uploadButton(entry.path), actionButton("+", "New folder in " + entry.path, () => options.onNewFolder(entry.path))]
        : [];
      // A zone's own folder (Data/example_data, Data/new_data) says whose it is.
      const isZoneFolder = zone && zone.path.toLowerCase() === entry.path.toLowerCase();
      const element = folder(source, node, { actions, owner: isZoneFolder ? zone.owner : null });
      if (writable) acceptDrops(element, entry.path);
      return element;
    }

    function librarySection(library) {
      if (!library || !library.configured) {
        return el("li", { class: "tree-empty", text: "No lab library is set up (LABNET_AGENT_LITERATURE_DIR)." });
      }
      const root = nest(library.entries);
      const lists = library.collections.map((collection) => {
        const papers = collection.papers.map((path) => ({
          entry: (library.entries.find((item) => item.path === path)) || { path, name: path.split("/").pop(), kind: "file" },
          children: [],
        }));
        const node = { entry: { path: "collections/" + collection.name, name: collection.name, kind: "dir" }, children: papers };
        const details = folder("library", node, { label: collection.name + " (reading list)" });
        details.querySelectorAll(".tree-file").forEach((button) => (button.title = button.title + " (in " + collection.name + ")"));
        return el("li", {}, [details]);
      });
      const shared = folder("library", { entry: null, children: root.children }, { label: "Lab library", owner: "shared" });
      shared.querySelector(":scope > .tree-list").prepend(...lists);
      return el("li", {}, [shared]);
    }

    function render({ entries, library }) {
      const root = nest(entries);
      const sections = new Map(root.children.map((node) => [node.entry.name, node]));
      const ordered = SECTION_ORDER.filter((name) => sections.has(name)).map((name) => sections.get(name));
      const rest = root.children.filter((node) => !SECTION_ORDER.includes(node.entry.name));
      const items = [...ordered, ...rest].map((node) => {
        if (node.entry.kind !== "dir") return el("li", {}, [fileButton("project", node.entry)]);
        const zone = options.zoneOf(node.entry.path);
        const name = node.entry.name;
        let actions = [];
        let owner = zone ? zone.owner : null;
        let dropFolder = null;
        if (zone && zone.owner === "user" && !zone.exact) {
          actions = [...uploadButton(node.entry.path), actionButton("+", "New folder in " + name, () => options.onNewFolder(node.entry.path))];
          dropFolder = node.entry.path;
        }
        if (name === "Tasks") actions = [actionButton("New task", "Write a new task", options.onNewTask), ...actions];
        if (name === "Data") owner = null; // its two subfolders say whose they are
        if (name === "Experiment_layout") owner = "mirror";
        const details = folder("project", node, { label: name.replace(/_/g, " "), owner, actions, section: true, defaultOpen: true });
        if (dropFolder) acceptDrops(details, dropFolder);
        if (name === "Literature") {
          const list = details.querySelector(":scope > .tree-list");
          const empty = list.querySelector(":scope > .tree-empty");
          if (empty) empty.textContent = "No project-only papers yet: drop PDFs here";
          list.prepend(librarySection(library));
        }
        return el("li", {}, [details]);
      });
      container.replaceChildren(el("ul", { class: "tree-list tree-root" }, items));
    }

    function select(source, path) {
      selectedKey = source + ":" + path;
      container.querySelectorAll(".tree-file.selected").forEach((node) => node.classList.remove("selected"));
      const button = container.querySelector('.tree-file[data-key="' + CSS.escape(selectedKey) + '"]');
      if (button) {
        button.classList.add("selected");
        for (let parent = button.parentElement; parent && parent !== container; parent = parent.parentElement) {
          if (parent.tagName === "DETAILS") parent.open = true;
        }
        button.scrollIntoView({ block: "nearest" });
      }
    }

    return { render, select };
  }

  Object.assign(window.LabNet, { createFileTree, fileKind, sizeText });
})();
