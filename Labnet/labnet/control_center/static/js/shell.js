// Shared page frame: top bar with tabs, and the Photo-Agent chat sidebar.
// Each page provides only <main id="page"> and sets <body data-page="...">.

(function () {
  "use strict";

  const AGENT = "Photo-Agent";
  const CHAT_KEY = "pqt-agent-chat";
  const COLLAPSED_KEY = "pqt-agent-collapsed";

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

  // ---- Top bar --------------------------------------------------------------

  const current = document.body.dataset.page;
  const tab = (label, href, page) =>
    el("a", { class: "tab", href, "aria-current": current === page ? "page" : false, text: label });

  const topbar = el("header", { class: "topbar" }, [
    el("a", { class: "brand", href: "/status" }, [
      el("span", { class: "brand-mark", "aria-hidden": "true" }),
      "PQT Lab Control Center",
    ]),
    el("nav", { class: "tabs", "aria-label": "Sections" }, [
      tab("Network", "/status", "network"),
      tab("Devices", "/devices", "devices"),
    ]),
  ]);

  // ---- Photo-Agent sidebar --------------------------------------------------

  const toggle = el("button", { class: "agent-toggle", type: "button", "aria-controls": "agent" }, [
    chevronIcon("right"),
  ]);
  const transcript = el("ol", { class: "transcript", "aria-live": "polite", "aria-label": "Conversation" });
  const input = el("textarea", {
    rows: "3",
    placeholder: "Ask " + AGENT + " to help plan an experiment…",
    "aria-label": "Message " + AGENT,
  });
  const send = el("button", { type: "submit", text: "Send" });
  const composer = el("form", { class: "composer" }, [
    input,
    el("div", { class: "composer-bar" }, [
      el("span", { text: "Enter to send · Shift+Enter for a new line" }),
      send,
    ]),
  ]);
  const agent = el("aside", { class: "agent", id: "agent", "aria-label": AGENT }, [
    el("div", { class: "agent-head" }, [
      toggle,
      el("h2", { text: AGENT }),
      el("span", { class: "agent-badge", text: "Not connected" }),
    ]),
    transcript,
    composer,
  ]);

  // ---- Assemble the page ----------------------------------------------------

  const main = document.getElementById("page");
  const layout = el("div", { class: "layout" });
  main.replaceWith(layout);
  layout.append(main, agent);
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

  // ---- Chat -----------------------------------------------------------------
  // The conversation lives in sessionStorage so it survives moving between
  // tabs; the agent itself is not connected yet (see /api/agent/messages).

  let messages = store.get("sessionStorage", CHAT_KEY, []);
  if (!Array.isArray(messages)) messages = [];

  function bubble(message) {
    // "bubble-agent", not "agent": that class name belongs to the sidebar.
    const item = el("li", { class: "bubble bubble-" + message.role + (message.pending ? " pending" : "") });
    if (message.role === "agent") item.append(el("span", { class: "from", text: AGENT }));
    item.append(document.createTextNode(message.text));
    return item;
  }

  function renderChat() {
    transcript.replaceChildren();
    if (messages.length === 0) {
      transcript.append(
        el("li", {
          class: "hint",
          text: "Ask " + AGENT + " to help plan an experiment. The lab agent will be connected here soon.",
        })
      );
    }
    for (const message of messages) transcript.append(bubble(message));
    transcript.scrollTop = transcript.scrollHeight;
  }

  function remember() {
    store.set("sessionStorage", CHAT_KEY, messages.filter((message) => !message.pending).slice(-100));
  }

  async function submit() {
    const text = input.value.trim();
    if (!text || send.disabled) return;
    input.value = "";
    send.disabled = true;
    messages.push({ role: "user", text });
    const pending = { role: "agent", text: "…", pending: true };
    messages.push(pending);
    renderChat();
    let reply;
    try {
      const response = await fetch("/api/agent/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      reply = (await response.json()).reply;
    } catch (error) {
      reply = "Couldn't reach the Control Center (" + error.message + "). Try again in a moment.";
    }
    messages.splice(messages.indexOf(pending), 1, { role: "agent", text: reply });
    remember();
    renderChat();
    send.disabled = false;
    input.focus();
  }

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

  renderChat();

  // Shared helpers for page scripts.
  window.PQT = { el, chevronIcon, store };
})();
