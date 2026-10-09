// Shared page frame: top bar with tabs, and the Photo-Agent chat sidebar.
// Each page provides only <main id="page"> and sets <body data-page="...">.
// /api/features.js (loaded first) says which optional parts this Control Center
// offers; Automation and Photo-Agent appear only when they are on.

(function () {
  "use strict";

  const AGENT = "Photo-Agent";
  const FEATURES = Object.assign(
    { site_name: "LabNet", title: "LabNet Control Center", automation: false, photo_agent: false },
    window.LabNetFeatures || {}
  );
  const CHAT_KEY = "labnet-agent-chat";
  const COLLAPSED_KEY = "labnet-agent-collapsed";
  const MODEL_KEY = "labnet-agent-model";
  const EFFORT_KEY = "labnet-agent-effort";

  // Storage can be unavailable (private windows, blocked site data); the
  // page must work without it, so every access is guarded.
  const store = {
    get(area, key, fallback) {
      try {
        const raw = window[area].getItem(key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (error) {
        return fallback;
      }
    },
    set(area, key, value) {
      try {
        window[area].setItem(key, JSON.stringify(value));
      } catch (error) {
        /* best effort only */
      }
    },
  };

  function el(tag, attributes, children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes || {})) {
      if (value === false || value === null || value === undefined) continue;
      if (name === "text") node.textContent = value;
      else node.setAttribute(name, value === true ? "" : value);
    }
    for (const child of children || []) {
      node.append(child);
    }
    return node;
  }

  function chevronIcon(direction) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", direction === "right" ? "M6 3l5 5-5 5" : "M3 6l5 5 5-5");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.8");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.append(path);
    return svg;
  }

  // Shared helpers for page scripts. Assigned before anything renders, and
  // merged (not replaced), so scripts loaded earlier keep what they added.
  window.LabNet = Object.assign(window.LabNet || {}, { el, chevronIcon, store, features: FEATURES, title: FEATURES.title });
  // Page <title>s say "LabNet Control Center"; this lab may have its own name.
  document.title = document.title.replace("LabNet Control Center", FEATURES.title);

  // ---- Top bar --------------------------------------------------------------

  const current = document.body.dataset.page;
  const tab = (label, href, page) =>
    el("a", { class: "tab", href, "aria-current": current === page ? "page" : false, text: label });

  const signedIn = el("span", { class: "signed-in", hidden: true });
  const topbar = el("header", { class: "topbar" }, [
    el("a", { class: "brand", href: "/status" }, [
      el("span", { class: "brand-mark", "aria-hidden": "true" }),
      FEATURES.title,
    ]),
    el("div", { class: "topbar-end" }, [
      el("nav", { class: "tabs", "aria-label": "Sections" }, [
        tab("Network", "/status", "network"),
        tab("Devices", "/devices", "devices"),
        tab("Experiments", "/experiments", "experiments"),
        ...(FEATURES.automation ? [tab("Automation", "/automation", "automation")] : []),
      ]),
      signedIn,
    ]),
  ]);

  // ---- Who is signed in -----------------------------------------------------
  // In the lab, Caddy signs each person in and the Control Center records that
  // name as the author of what they create. LabNet.me resolves (once per page) to
  // {user, logins}; user is null in development, where names are typed instead.

  const me = fetch("/api/me", { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null)
    .then((body) => ({
      user: body && typeof body.user === "string" && body.user ? body.user : null,
      logins: Boolean(body && body.logins),
    }));
  me.then(({ user }) => {
    if (!user) return;
    signedIn.replaceChildren(el("span", { class: "signed-in-label", text: "Signed in as " }), el("b", { text: user }));
    signedIn.title = "Your lab login. Experiments and projects you create or change are recorded under this name.";
    signedIn.hidden = false;
  });
  window.LabNet.me = me;

  // ---- Photo-Agent sidebar --------------------------------------------------

  // Line icons on a 24-unit grid (one or more SVG paths each).
  const ICONS = {
    attach: ["m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"],
    send: ["M12 19V5", "M5 12l7-7 7 7"],
    stop: ["M7 7h10v10H7z"],
    newChat: ["M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7", "M18.4 2.6a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z"],
  };

  function icon(name, size) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", String(size || 16));
    svg.setAttribute("height", String(size || 16));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    for (const d of ICONS[name]) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", name === "stop" ? "currentColor" : "none");
      path.setAttribute("stroke", "currentColor");
      path.setAttribute("stroke-width", "2");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
      svg.append(path);
    }
    return svg;
  }

  const toggle = el("button", { class: "agent-toggle", type: "button", "aria-controls": "agent" }, [
    chevronIcon("right"),
  ]);
  const badge = el("span", { class: "agent-badge", text: "Checking…" });
  const newChat = el("button", { class: "agent-new", type: "button", title: "New chat", "aria-label": "New chat" }, [
    icon("newChat"),
  ]);
  const transcript = el("ol", { class: "transcript", "aria-live": "polite", "aria-label": "Conversation" });
  const input = el("textarea", {
    rows: "3",
    placeholder: "Ask " + AGENT + "… (Shift+Enter for a new line)",
    "aria-label": "Message " + AGENT,
  });
  const contextChip = el("div", { class: "context-chip", hidden: true });
  const chips = el("ul", { class: "attachments", "aria-label": "Files to send", hidden: true });
  const attachError = el("p", { class: "attach-error", role: "status", hidden: true });
  const fileInput = el("input", { type: "file", multiple: true, hidden: true, tabindex: "-1" });
  const attachButton = el(
    "button",
    {
      class: "tool-button",
      type: "button",
      title: "Attach files: images, PDFs, CSV or other text (or paste / drop them here)",
      "aria-label": "Attach files",
    },
    [icon("attach")]
  );
  const modelSelect = el("select", { "aria-label": "Model" });
  const effortSelect = el("select", { "aria-label": "Effort" });
  const modelPill = el("label", { class: "pill", title: "Model" }, [modelSelect]);
  const effortPill = el("label", { class: "pill" }, [el("span", { class: "pill-label", text: "Effort" }), effortSelect]);
  const send = el("button", { class: "send", type: "submit" });
  const composer = el("form", { class: "composer" }, [
    contextChip,
    chips,
    input,
    attachError,
    el("div", { class: "composer-bar" }, [attachButton, modelPill, effortPill, el("span", { class: "spacer" }), send]),
    fileInput,
  ]);
  const agent = el("aside", { class: "agent", id: "agent", "aria-label": AGENT }, [
    el("div", { class: "agent-head" }, [toggle, el("h2", { text: AGENT }), badge, newChat]),
    transcript,
    composer,
  ]);

  // ---- Assemble the page ----------------------------------------------------

  const main = document.getElementById("page");
  const layout = el("div", { class: "layout" });
  main.replaceWith(layout);
  if (FEATURES.photo_agent) layout.append(main, agent);
  else layout.append(main);
  layout.classList.toggle("no-agent", !FEATURES.photo_agent);
  document.body.prepend(topbar);

  // ---- Collapse -------------------------------------------------------------

  function setCollapsed(collapsed) {
    agent.classList.toggle("collapsed", collapsed);
    layout.classList.toggle("agent-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", (collapsed ? "Open " : "Collapse ") + AGENT);
    store.set("localStorage", COLLAPSED_KEY, collapsed);
  }

  toggle.addEventListener("click", () => setCollapsed(!agent.classList.contains("collapsed")));
  setCollapsed(store.get("localStorage", COLLAPSED_KEY, false));

  // ---- Model and effort -----------------------------------------------------
  // The choices come from /api/agent; each viewer's last pick is remembered.

  let agentInfo = null;

  function fillChoices() {
    const models = (agentInfo && agentInfo.models) || [];
    modelPill.hidden = effortPill.hidden = models.length === 0;
    if (!models.length) return;
    const wantedModel = modelSelect.value || store.get("localStorage", MODEL_KEY, agentInfo.model);
    modelSelect.replaceChildren(...models.map((model) => el("option", { value: model.id, text: model.label })));
    modelSelect.value = models.some((model) => model.id === wantedModel) ? wantedModel : agentInfo.model;
    if (!effortSelect.options.length) {
      effortSelect.replaceChildren(
        ...agentInfo.efforts.map((effort) => el("option", { value: effort.id, text: effort.label }))
      );
      const wantedEffort = store.get("localStorage", EFFORT_KEY, agentInfo.effort);
      effortSelect.value = agentInfo.efforts.some((effort) => effort.id === wantedEffort)
        ? wantedEffort
        : agentInfo.effort;
    }
    fileInput.accept = agentInfo.attachments.extensions.join(",");
    showEffort();
  }

  function selectedModel() {
    return ((agentInfo && agentInfo.models) || []).find((model) => model.id === modelSelect.value) || null;
  }

  function showEffort() {
    const model = selectedModel();
    const usable = !model || model.effort;
    effortSelect.disabled = !usable;
    effortPill.classList.toggle("disabled", !usable);
    effortPill.title = usable
      ? "Effort: how much the model thinks before answering"
      : model.label + " has no effort setting";
  }

  modelSelect.addEventListener("change", () => {
    store.set("localStorage", MODEL_KEY, modelSelect.value);
    showEffort();
  });
  effortSelect.addEventListener("change", () => store.set("localStorage", EFFORT_KEY, effortSelect.value));

  // ---- Attachments ----------------------------------------------------------
  // Files wait here until Send; the Control Center saves them for this
  // conversation, where only its answers can read them.

  let files = [];
  let previews = [];
  let errorTimer = null;

  function sizeText(bytes) {
    if (bytes < 1024) return bytes + " bytes";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  function extension(name) {
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot).toLowerCase() : "";
  }

  function showAttachError(text) {
    attachError.textContent = text;
    attachError.hidden = !text;
    window.clearTimeout(errorTimer);
    if (text) errorTimer = window.setTimeout(() => (attachError.hidden = true), 8000);
  }

  function addFiles(list) {
    const limits = agentInfo && agentInfo.attachments;
    if (!limits) return showAttachError("Photo-Agent isn't available, so files can't be sent.");
    const problems = [];
    let total = files.reduce((sum, file) => sum + file.size, 0);
    for (const file of list) {
      if (!limits.extensions.includes(extension(file.name))) {
        problems.push(file.name + ": can't be read (images, PDFs and text files only)");
      } else if (!file.size) {
        problems.push(file.name + " is empty");
      } else if (file.size > limits.max_bytes) {
        problems.push(file.name + " is larger than " + sizeText(limits.max_bytes));
      } else if (files.length >= limits.max_files) {
        problems.push("At most " + limits.max_files + " files per message");
        break;
      } else if (total + file.size > limits.max_total_bytes) {
        problems.push("Files in one message must add up to " + sizeText(limits.max_total_bytes) + " or less");
        break;
      } else {
        files.push(file);
        total += file.size;
      }
    }
    showAttachError(problems.join(" · "));
    renderChips();
  }

  function renderChips() {
    previews.forEach((url) => URL.revokeObjectURL(url));
    previews = [];
    chips.replaceChildren(
      ...files.map((file, index) => {
        let preview;
        if (file.type.startsWith("image/")) {
          const url = URL.createObjectURL(file);
          previews.push(url);
          preview = el("img", { src: url, alt: "" });
        } else {
          preview = el("span", { class: "chip-kind", text: extension(file.name).slice(1, 5).toUpperCase() });
        }
        const remove = el("button", { class: "chip-remove", type: "button", "aria-label": "Remove " + file.name }, [
          "×",
        ]);
        remove.addEventListener("click", () => {
          files.splice(index, 1);
          renderChips();
          input.focus();
        });
        return el("li", { class: "chip", title: file.name + " · " + sizeText(file.size) }, [
          preview,
          el("span", { class: "chip-name", text: file.name }),
          remove,
        ]);
      })
    );
    chips.hidden = files.length === 0;
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve({ name: file.name, data: String(reader.result).split(",", 2)[1] || "" });
      reader.onerror = () => reject(new Error("couldn't read " + file.name));
      reader.readAsDataURL(file);
    });
  }

  attachButton.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    addFiles([...fileInput.files]);
    fileInput.value = "";
  });
  // Pasted screenshots arrive as "image.png"; give each its own name.
  input.addEventListener("paste", (event) => {
    const pasted = [...((event.clipboardData && event.clipboardData.files) || [])];
    if (!pasted.length) return;
    event.preventDefault();
    const stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
    addFiles(
      pasted.map((file, index) =>
        file.name && file.name !== "image.png"
          ? file
          : new File([file], "pasted-" + stamp + (index ? "-" + (index + 1) : "") + ".png", { type: file.type })
      )
    );
  });
  const carriesFiles = (event) => [...((event.dataTransfer && event.dataTransfer.types) || [])].includes("Files");
  composer.addEventListener("dragover", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    composer.classList.add("dropping");
  });
  composer.addEventListener("dragleave", (event) => {
    if (!composer.contains(event.relatedTarget)) composer.classList.remove("dropping");
  });
  composer.addEventListener("drop", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    composer.classList.remove("dropping");
    addFiles([...event.dataTransfer.files]);
  });

  // ---- Chat -----------------------------------------------------------------
  // Answers stream from /api/agent/messages as server-sent events. The chat
  // (and its conversation id, so follow-ups keep their context) lives in
  // sessionStorage, so it survives moving between tabs.

  const saved = store.get("sessionStorage", CHAT_KEY, null);
  let chat = saved && Array.isArray(saved.messages) ? saved : { conversation_id: null, messages: [] };
  let controller = null; // set while an answer is streaming

  async function refreshAgent() {
    try {
      const response = await fetch("/api/agent", { cache: "no-store" });
      agentInfo = await response.json();
    } catch (error) {
      agentInfo = { enabled: false, ready: false, reason: "Can't reach the Control Center." };
    }
    badge.textContent = agentInfo.ready ? "Ready" : agentInfo.enabled ? "Sign-in needed" : "Not connected";
    badge.classList.toggle("ready", Boolean(agentInfo.ready));
    badge.title = agentInfo.reason || "Answers come from Claude Code on the Control Center computer.";
    fillChoices();
    renderChat();
  }

  // Each message keeps its own elements (a WeakMap), so re-rendering while an
  // answer streams only patches what changed. Buttons in earlier answers stay
  // clickable, and an opened "Looked at" list stays open.
  const views = new WeakMap();
  const hintItem = el("li", { class: "hint" });

  function sourcesList(items) {
    return el("details", { class: "sources" }, [
      el("summary", { text: "Looked at " + items.length + (items.length === 1 ? " thing" : " things") }),
      el("ul", {}, items.map((text) => el("li", { text }))),
    ]);
  }

  function userView(message) {
    const item = el("li", { class: "bubble bubble-user" });
    if (message.attachments && message.attachments.length) {
      item.append(
        el(
          "ul",
          { class: "sent-files" },
          message.attachments.map((file) => el("li", { title: sizeText(file.size), text: file.name }))
        )
      );
    }
    if (message.text) item.append(document.createTextNode(message.text));
    return { item, update() {} }; // a sent message never changes
  }

  function agentView() {
    // "bubble-agent", not "agent": that class name belongs to the sidebar.
    const item = el("li", { class: "bubble bubble-agent" });
    const from = el("span", { class: "from" });
    const body = el("div", { class: "md" });
    const tail = el("div", { class: "bubble-tail" });
    item.append(from, body, tail);
    let tailKey = null;
    return {
      item,
      update(message) {
        item.classList.toggle("pending", Boolean(message.pending));
        item.classList.toggle("error", Boolean(message.error));
        from.textContent = AGENT + (message.model ? " · " + message.model : "");
        window.LabNet.markdown.renderInto(body, message.text || "");
        const activity = message.activity || [];
        const latest = activity[activity.length - 1];
        const key = message.pending ? "pending:" + (message.status || "") + ":" + (latest || "") : "done:" + activity.length;
        if (key === tailKey) return;
        tailKey = key;
        if (message.pending) {
          tail.replaceChildren(
            el("span", { class: "activity", text: message.status || (latest ? latest + "…" : "Thinking…") })
          );
        } else {
          tail.replaceChildren(...(activity.length ? [sourcesList(activity)] : []));
        }
      },
    };
  }

  function viewFor(message) {
    let view = views.get(message);
    if (!view) {
      view = message.role === "agent" ? agentView() : userView(message);
      views.set(message, view);
    }
    view.update(message);
    return view.item;
  }

  function renderChat() {
    const nearBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
    let wanted;
    if (chat.messages.length === 0) {
      hintItem.textContent =
        agentInfo && !agentInfo.ready && agentInfo.reason
          ? AGENT + " isn't available: " + agentInfo.reason
          : "Ask " + AGENT + " how LabNet works, what is online, or to help plan an experiment " +
            "and write its script. Attach data, plots or papers with the paperclip.";
      wanted = [hintItem];
    } else {
      wanted = chat.messages.map(viewFor);
    }
    wanted.forEach((item, index) => {
      if (transcript.children[index] !== item) transcript.insertBefore(item, transcript.children[index] || null);
    });
    while (transcript.children.length > wanted.length) transcript.lastElementChild.remove();
    if (nearBottom) transcript.scrollTop = transcript.scrollHeight;
  }

  // Re-render at most once per frame while text streams in.
  let renderQueued = false;
  function renderSoon() {
    if (renderQueued) return;
    renderQueued = true;
    window.requestAnimationFrame(() => {
      renderQueued = false;
      renderChat();
    });
  }

  function remember() {
    const messages = chat.messages
      .filter((message) => !message.pending)
      .slice(-60)
      .map(({ role, text, activity, error, attachments, model }) => ({ role, text, activity, error, attachments, model }));
    store.set("sessionStorage", CHAT_KEY, { conversation_id: chat.conversation_id, messages });
  }

  function setAnswering(answering) {
    send.replaceChildren(icon(answering ? "stop" : "send", answering ? 14 : 16));
    send.type = answering ? "button" : "submit";
    send.classList.toggle("stop", answering);
    send.setAttribute("aria-label", answering ? "Stop" : "Send");
    send.title = answering ? "Stop the answer" : "Send (Enter)";
    newChat.disabled = answering;
  }

  function handle(event, reply) {
    if (event.type === "session" && event.conversation_id) {
      chat.conversation_id = event.conversation_id;
    } else if (event.type === "delta") {
      reply.text += event.text;
      reply.status = null;
    } else if (event.type === "activity") {
      reply.activity.push(event.text);
    } else if (event.type === "status") {
      reply.status = event.text;
    } else if (event.type === "done") {
      if (event.conversation_id) chat.conversation_id = event.conversation_id;
      if (event.model) reply.model = event.model;
      if (event.is_error) {
        reply.error = true;
        reply.text = (reply.text ? reply.text + "\n\n" : "") + (event.text || "Something went wrong.");
      } else if (!reply.text) {
        reply.text = event.text || "";
      }
    }
  }

  // Reads "data: {...}" lines from a server-sent event stream sent to a POST.
  async function readEvents(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const line of block.split("\n")) {
          if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
        }
      }
    }
  }

  async function submit() {
    const text = input.value.trim();
    if ((!text && !files.length) || controller) return;
    const sending = files;
    files = [];
    renderChips();
    showAttachError("");
    input.value = "";
    chat.messages.push({
      role: "user",
      text,
      attachments: sending.map((file) => ({ name: file.name, size: file.size })),
    });
    const reply = { role: "agent", text: "", activity: [], pending: true };
    if (sending.length) reply.status = "Sending " + (sending.length === 1 ? "the file" : sending.length + " files") + "…";
    chat.messages.push(reply);
    controller = new AbortController();
    setAnswering(true);
    transcript.scrollTop = transcript.scrollHeight;
    renderChat();
    try {
      const attachments = await Promise.all(sending.map(readFile));
      const context = await contextFields();
      const response = await fetch("/api/agent/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          conversation_id: chat.conversation_id,
          model: modelSelect.value || null,
          effort: effortSelect.disabled ? null : effortSelect.value || null,
          attachments,
          ...context,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail = "HTTP " + response.status;
        try {
          const body = await response.json();
          detail = typeof body.detail === "string" ? body.detail : detail;
        } catch (_ignored) {}
        throw new Error(detail);
      }
      if ((response.headers.get("Content-Type") || "").startsWith("text/event-stream")) {
        await readEvents(response, (event) => {
          handle(event, reply);
          renderSoon();
        });
      } else {
        reply.text = (await response.json()).reply; // the agent isn't set up
      }
    } catch (error) {
      if (error.name === "AbortError") {
        reply.text = (reply.text ? reply.text + "\n\n" : "") + "_Stopped._";
      } else {
        reply.error = true;
        reply.text = (reply.text ? reply.text + "\n\n" : "") + "Couldn't get an answer (" + error.message + ").";
      }
    }
    reply.pending = false;
    if (!reply.text) reply.text = "(No answer.)";
    controller = null;
    setAnswering(false);
    remember();
    renderChat();
    refreshAgent();
    input.focus();
  }

  send.addEventListener("click", (event) => {
    if (controller) {
      event.preventDefault();
      controller.abort(); // the Control Center then stops Claude Code too
    }
  });
  newChat.addEventListener("click", () => {
    chat = { conversation_id: null, messages: [] };
    remember();
    renderChat();
    input.focus();
  });
  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });

  setAnswering(false);
  renderChat();
  if (FEATURES.photo_agent) refreshAgent();

  // ---- Page context ---------------------------------------------------------
  // A page can tell Photo-Agent what the person is looking at, e.g. the
  // experiment on the canvas: setAgentContext({label, fields, prepare}).
  // `fields` are added to each message; prepare() runs first (at most 2 s),
  // e.g. to save pending edits so the agent reads what is on screen.

  let agentContext = null;

  function setAgentContext(context) {
    agentContext = context || null;
    contextChip.hidden = !agentContext;
    if (!agentContext) {
      contextChip.replaceChildren();
      return;
    }
    const dismiss = el(
      "button",
      { class: "chip-remove", type: "button", title: "Don't share this", "aria-label": "Don't share this with " + AGENT },
      ["×"]
    );
    dismiss.addEventListener("click", () => setAgentContext(null));
    contextChip.replaceChildren(
      el("span", { class: "context-label", text: AGENT + " sees:" }),
      el("b", { text: agentContext.label || "this page" }),
      dismiss
    );
  }

  async function contextFields() {
    if (!agentContext) return {};
    if (agentContext.prepare) {
      try {
        await Promise.race([
          Promise.resolve(agentContext.prepare()),
          new Promise((resolve) => window.setTimeout(resolve, 2000)),
        ]);
      } catch (_ignored) {
        /* send anyway */
      }
    }
    return agentContext ? agentContext.fields || {} : {};
  }

  // ---- Dialogs --------------------------------------------------------------
  // dialog({title, body, actions: [{label, value, primary, danger}], validate})
  // resolves with the chosen action's value, or null when cancelled (Esc,
  // Cancel, a click outside). validate(value) may return an error to keep it open.

  function dialog({ title, body, actions, validate, wide }) {
    return new Promise((resolve) => {
      const error = el("p", { class: "dialog-error", role: "alert", hidden: true });
      const buttons = (actions || [{ label: "OK", value: "ok", primary: true }]).map((action, index) =>
        el("button", {
          class: "button" + (action.primary ? " primary" : "") + (action.danger ? " danger" : ""),
          type: "submit",
          value: String(index),
          text: action.label,
        })
      );
      const cancel = el("button", { class: "button", type: "button", text: "Cancel" });
      const form = el("form", { method: "dialog" }, [
        el("h2", { text: title }),
        el("div", { class: "dialog-body" }, body ? [body] : []),
        error,
        el("div", { class: "dialog-actions" }, [cancel, ...buttons]),
      ]);
      const box = el("dialog", { class: "ln-dialog" + (wide ? " wide" : "") }, [form]);
      let result = null;
      form.addEventListener("submit", (event) => {
        const chosen = actions ? actions[Number(event.submitter && event.submitter.value)] : { value: "ok" };
        const problem = chosen && validate ? validate(chosen.value) : null;
        if (problem) {
          event.preventDefault();
          error.textContent = problem;
          error.hidden = false;
          return;
        }
        result = chosen ? chosen.value : null;
      });
      cancel.addEventListener("click", () => box.close());
      box.addEventListener("click", (event) => {
        if (event.target === box) box.close(); // the backdrop
      });
      box.addEventListener("close", () => {
        box.remove();
        resolve(result);
      });
      document.body.append(box);
      box.showModal();
      const first = form.querySelector("input, textarea, select");
      if (first) first.focus();
    });
  }

  Object.assign(window.LabNet, { icon, dialog, setAgentContext, setAgentCollapsed: setCollapsed });
})();
