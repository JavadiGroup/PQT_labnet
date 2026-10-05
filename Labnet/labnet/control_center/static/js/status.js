// Network (status) page: Lab PCs and their devices, refreshed from /api/status.

(function () {
  "use strict";

  const { el, chevronIcon, store } = window.PQT;
  const EXPANDED_KEY = "pqt-expanded-devices";

  const list = document.getElementById("lab-pcs");
  const summary = document.getElementById("summary");
  const updated = document.getElementById("updated");
  const pulse = document.getElementById("pulse");
  const banner = document.getElementById("banner");

  const expanded = new Set(store.get("sessionStorage", EXPANDED_KEY, []));
  let lastRendered = "";
  let lastSnapshot = null;

  const DEVICE_STATES = {
    available: { label: "Available", tone: "ok" },
    leased: { label: "Leased", tone: "warn" },
    unavailable: { label: "Unavailable", tone: "bad" },
  };

  function statusPill(label, tone) {
    return el("span", { class: "status " + tone, text: label });
  }

  function ago(iso) {
    if (!iso) return "never";
    const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (seconds < 5) return "just now";
    if (seconds < 90) return seconds + " s ago";
    if (seconds < 5400) return Math.round(seconds / 60) + " min ago";
    return new Date(iso).toLocaleString();
  }

  function plural(count, word) {
    return count + " " + word + (count === 1 ? "" : "s");
  }

  // ---- Device row -----------------------------------------------------------

  function methodItem(method) {
    return el("li", { class: "method" }, [
      el("span", { class: "fn", text: method.name }),
      "(",
      el("span", { class: "params", text: method.parameters.join(", ") }),
      ") ",
      el("span", { class: "arrow", text: "→ " }),
      el("span", { class: "returns", text: method.returns }),
      el("span", { class: "kind " + method.kind, text: method.kind }),
    ]);
  }

  function deviceDetail(device, detailId) {
    const methods = device.known_contract
      ? el("ul", { class: "methods" }, device.methods.map(methodItem))
      : el("p", {
          text:
            "This Control Center has no contract for '" +
            device.type +
            "'. Update it to the Lab PC's LabNet release.",
        });
    return el("div", { class: "device-detail", id: detailId, hidden: !expanded.has(device.id) }, [
      el("h3", { text: "Callable methods" }),
      methods,
      el("dl", { class: "facts" }, [
        el("div", {}, [el("dt", { text: "Status" }), el("dd", { text: device.detail })]),
        el("div", {}, [el("dt", { text: "Runtime" }), el("dd", { text: device.runtime_state })]),
        el("div", {}, [el("dt", { text: "Last report" }), el("dd", { text: ago(device.last_seen) })]),
      ]),
    ]);
  }

  function deviceItem(device) {
    const state = DEVICE_STATES[device.state] || DEVICE_STATES.unavailable;
    const detailId = "detail-" + device.id;
    const isOpen = expanded.has(device.id);
    const chevron = el(
      "button",
      {
        class: "chevron",
        type: "button",
        "aria-expanded": String(isOpen),
        "aria-controls": detailId,
        "aria-label": (isOpen ? "Hide" : "Show") + " methods for " + device.id,
      },
      [chevronIcon("down")]
    );
    const detail = deviceDetail(device, detailId);
    const row = el("div", { class: "device-row" }, [
      el("span", { class: "device-name", title: device.id, text: device.id }),
      statusPill(state.label, state.tone),
      el("span", { class: "device-type" }, ["Type: ", el("b", { text: device.type_label })]),
      el("a", { class: "device-control", href: device.control_url, text: "Device control" }),
      chevron,
    ]);

    function toggle() {
      const open = detail.hidden;
      detail.hidden = !open;
      chevron.setAttribute("aria-expanded", String(open));
      chevron.setAttribute("aria-label", (open ? "Hide" : "Show") + " methods for " + device.id);
      if (open) expanded.add(device.id);
      else expanded.delete(device.id);
      store.set("sessionStorage", EXPANDED_KEY, [...expanded]);
    }

    row.addEventListener("click", (event) => {
      if (event.target.closest("a")) return; // the Device control link navigates
      toggle();
    });
    return el("li", { class: "device" }, [row, detail]);
  }

  // ---- Lab PC card ----------------------------------------------------------

  function labPcCard(pc) {
    const meta = el("span", { class: "lab-pc-meta" }, [
      "IP ",
      el("code", { text: pc.address }),
      " · gRPC :" + pc.port + " · " + (pc.secure ? "TLS" : "plaintext"),
    ]);
    const head = el("header", { class: "lab-pc-head" }, [
      el("h2", { text: pc.id }),
      pc.online ? statusPill("Online", "ok") : statusPill("Offline", "bad"),
      meta,
      el("span", {
        class: "lab-pc-count",
        text: pc.online ? plural(pc.devices.length, "device") : "last seen " + ago(pc.last_seen),
      }),
    ]);
    const body = pc.devices.length
      ? el("ul", { class: "devices" }, pc.devices.map(deviceItem))
      : el("p", { class: "empty", text: "This Lab PC has not registered any devices." });
    return el("section", { class: "lab-pc" + (pc.online ? "" : " offline") }, [head, body]);
  }

  // ---- Rendering ------------------------------------------------------------

  function render(snapshot) {
    lastSnapshot = snapshot;
    const counts = snapshot.summary;
    summary.textContent =
      counts.online + " of " + plural(counts.lab_pcs, "Lab PC") + " online · " +
      plural(counts.devices, "device") + " · " + counts.available + " available · " +
      counts.leased + " leased";

    const central = snapshot.central;
    banner.hidden = central.ok;
    if (!central.ok) {
      banner.textContent =
        "Can't reach Central at " + central.url + " (" + central.error + "). " +
        "Showing the last known state.";
    }
    pulse.classList.toggle("stale", !central.ok);
    updated.textContent = "Updated " + new Date(snapshot.updated_at).toLocaleTimeString();

    // Rebuilding only when something changed keeps hover and focus steady.
    const signature = JSON.stringify([snapshot.lab_pcs, central.ok]);
    if (signature === lastRendered) return;
    lastRendered = signature;

    if (snapshot.lab_pcs.length === 0) {
      list.replaceChildren(
        el("section", { class: "lab-pc" }, [
          el("p", {
            class: "empty",
            text: central.ok
              ? "No Lab PCs have registered with Central yet. Start one with labnet-lab-pc."
              : "Waiting for Central…",
          }),
        ])
      );
      return;
    }
    list.replaceChildren(...snapshot.lab_pcs.map(labPcCard));
  }

  async function refresh() {
    let delay = 3000;
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const snapshot = await response.json();
      delay = Math.max(1000, snapshot.refresh_seconds * 1000);
      render(snapshot);
    } catch (error) {
      banner.hidden = false;
      banner.textContent = "Lost contact with the Control Center (" + error.message + "). Retrying…";
      pulse.classList.add("stale");
    }
    window.setTimeout(refresh, delay);
  }

  // Keep "x s ago" fresh between data changes.
  window.setInterval(() => {
    if (lastSnapshot) {
      lastRendered = "";
      render(lastSnapshot);
    }
  }, 15000);

  refresh();
})();
