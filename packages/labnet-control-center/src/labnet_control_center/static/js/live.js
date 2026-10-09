// Live network data shared by every page: the refresh loop, status pills,
// and the expandable device row used on both the Network and Devices pages.

(function () {
  "use strict";

  const { el, chevronIcon, store } = window.LabNet;
  const EXPANDED_KEY = "labnet-expanded-devices";
  const expanded = new Set(store.get("sessionStorage", EXPANDED_KEY, []));

  const DEVICE_STATES = {
    available: { label: "Available", tone: "ok" },
    leased: { label: "Leased", tone: "warn" },
    unavailable: { label: "Unavailable", tone: "bad" },
  };

  function statusPill(label, tone) {
    return el("span", { class: "status " + tone, text: label });
  }

  function deviceState(device) {
    return DEVICE_STATES[device.state] || DEVICE_STATES.unavailable;
  }

  // Whether the Lab PC's driver for the device is this Control Center's own.
  const DRIVER_FLAGS = {
    missing: { label: "No driver here", tone: "warn" },
    mismatch: { label: "Driver mismatch", tone: "bad" },
  };

  function driverFlag(device) {
    const driver = device.driver || {};
    const flag = DRIVER_FLAGS[driver.status];
    if (!flag) return [];
    return [el("span", { class: "driver-flag " + flag.tone, title: driver.detail || "", text: flag.label })];
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

  function minutesSince(epochSeconds) {
    if (!epochSeconds) return "";
    const minutes = Math.floor((Date.now() / 1000 - epochSeconds) / 60);
    return minutes < 1 ? "<1 min" : minutes < 90 ? minutes + " min" : (minutes / 60).toFixed(1) + " h";
  }

  // "ends in 12 min" for an Automation grant's end time (epoch seconds).
  function endsIn(epochSeconds) {
    const seconds = Math.max(0, Math.round(epochSeconds - Date.now() / 1000));
    if (seconds < 60) return seconds <= 0 ? "ending now" : "ends in <1 min";
    const minutes = Math.ceil(seconds / 60);
    return "ends in " + (minutes < 90 ? minutes + " min" : (minutes / 60).toFixed(1) + " h");
  }

  // Who holds a leased device, e.g. "Live in the Control Center · 12 min" or
  // "Automation: aom-study-k7q2mx/Task1 · ends in 14 min". Empty for devices nobody holds.
  function holderText(device) {
    const held = device.control_center;
    if (!held) return device.state === "leased" ? "Leased by another LabNet client" : "";
    if (held.automation) {
      const grant = held.automation;
      if (grant.status === "ending") return "Automation: " + grant.label + " · ending (safe state)";
      return "Automation: " + grant.label + " · " + endsIn(grant.ends_at);
    }
    let what;
    if (held.session !== device.id) what = "Used by a script on " + held.session;
    else if (held.streaming) what = "Live in the Control Center";
    else what = "Script running in the Control Center";
    const since = minutesSince(held.since);
    return since ? what + " · " + since : what;
  }

  // Frees everything a Control Center page holds on this device: stream and script.
  // Anyone viewing the Network or Devices page may do this, from any computer.
  async function releaseDevice(sessionId) {
    const response = await fetch("/api/devices/" + encodeURIComponent(sessionId) + "/release", {
      method: "POST",
    });
    if (!response.ok) {
      let detail = "HTTP " + response.status;
      try {
        detail = (await response.json()).detail || detail;
      } catch (_ignored) {}
      throw new Error(detail);
    }
  }

  // Ends an Automation grant now: its safe state is applied and the leases released.
  // Like Pause, open to anyone viewing the Network or Devices page.
  async function stopGrant(stopUrl) {
    const response = await fetch(stopUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) {
      let detail = "HTTP " + response.status;
      try {
        detail = (await response.json()).detail || detail;
      } catch (_ignored) {}
      throw new Error(detail);
    }
  }

  // Every device in the snapshot, each tagged with the Lab PC it belongs to.
  function allDevices(snapshot) {
    return snapshot.lab_pcs.flatMap((pc) => pc.devices.map((device) => ({ ...device, lab_pc: pc })));
  }

  // ---- Expandable device row ------------------------------------------------

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
    const driver = device.driver || {};
    const methods = device.known_contract
      ? el("ul", { class: "methods" }, device.methods.map(methodItem))
      : el("p", {
          text: driver.detail || "This Control Center has no driver for '" + device.type + "'.",
        });
    return el("div", { class: "device-detail", id: detailId, hidden: !expanded.has(device.id) }, [
      el("h3", { text: "Callable methods" }),
      methods,
      el("dl", { class: "facts" }, [
        el("div", {}, [el("dt", { text: "Status" }), el("dd", { text: device.detail })]),
        el("div", {}, [el("dt", { text: "Runtime" }), el("dd", { text: device.runtime_state })]),
        el("div", {}, [el("dt", { text: "Last report" }), el("dd", { text: ago(device.last_seen) })]),
        el("div", {}, [el("dt", { text: "Driver" }), el("dd", { text: driver.detail || "Not reported" })]),
      ]),
    ]);
  }

  // The strip under a held device's row: who holds it, and a Pause button.
  function holderStrip(device) {
    const held = device.control_center;
    if (!held) {
      if (device.state !== "leased") return null;
      return el("div", { class: "holder other" }, [
        el("span", { text: holderText(device) + ". Only that client can release it." }),
      ]);
    }
    const message = el("span", { class: "holder-text", text: holderText(device) });
    if (held.automation) return automationStrip(held, message);
    const pause = el("button", { class: "button small", type: "button", text: "Pause" });
    pause.title = "Stop " + (held.session === device.id ? "this device's" : held.session + "'s") +
      " live view and script, and release the lease";
    pause.addEventListener("click", async () => {
      pause.disabled = true;
      pause.textContent = "Pausing…";
      try {
        await releaseDevice(held.session);
        refreshNow();
      } catch (error) {
        message.textContent = "Could not pause: " + error.message;
        pause.disabled = false;
        pause.textContent = "Pause";
      }
    });
    return el("div", { class: "holder" }, [
      el("span", { class: "holder-dot", "aria-hidden": "true" }),
      message,
      pause,
      el("a", { href: held.page, text: "Open page" }),
    ]);
  }

  // The strip for a device an Automation grant holds: project/task, time left, Stop.
  function automationStrip(held, message) {
    const grant = held.automation;
    const stop = el("button", { class: "button small danger", type: "button", text: "Stop" });
    stop.title = "End this Automation experiment now: its safe state is applied and the devices released";
    stop.disabled = grant.status === "ending";
    stop.addEventListener("click", async () => {
      stop.disabled = true;
      stop.textContent = "Stopping…";
      try {
        await stopGrant(grant.stop_url);
        refreshNow();
      } catch (error) {
        message.textContent = "Could not stop: " + error.message;
        stop.disabled = false;
        stop.textContent = "Stop";
      }
    });
    return el("div", { class: "holder automation" }, [
      el("span", { class: "holder-dot", "aria-hidden": "true" }),
      message,
      stop,
      el("a", { href: held.page, text: "Open project" }),
    ]);
  }

  // options.labPc: also show which Lab PC the device is on (Devices page).
  function deviceItem(device, options) {
    const showLabPc = Boolean(options && options.labPc);
    const state = deviceState(device);
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
    const cells = [
      el("span", { class: "device-name", title: device.id, text: device.id }),
      statusPill(state.label, state.tone),
      el("span", { class: "device-type" }, ["Type: ", el("b", { text: device.type_label }), ...driverFlag(device)]),
    ];
    if (showLabPc) {
      cells.push(el("span", { class: "device-lab-pc" }, ["Lab PC: ", el("b", { text: device.lab_pc.id })]));
    }
    cells.push(el("a", { class: "device-control", href: device.control_url, text: "Device control" }), chevron);
    const row = el("div", { class: "device-row" + (showLabPc ? " with-lab-pc" : "") }, cells);

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
    const holder = holderStrip(device);
    return el("li", { class: "device" }, holder ? [row, holder, detail] : [row, detail]);
  }

  // ---- Refresh loop ---------------------------------------------------------
  // refreshNow() asks for a fresh picture at once (e.g. after a Pause).
  let refreshNow = () => {};

  // Polls /api/status and calls render(snapshot) whenever the data changes,
  // plus every 15 s so "x s ago" stays fresh. The page's banner, pulse and
  // "Updated" elements (ids banner, pulse, updated) are handled here.

  function watchNetwork(render) {
    const banner = document.getElementById("banner");
    const pulse = document.getElementById("pulse");
    const updated = document.getElementById("updated");
    let lastSignature = "";
    let lastSnapshot = null;

    function show(snapshot, force) {
      lastSnapshot = snapshot;
      const central = snapshot.central;
      if (banner) {
        banner.hidden = central.ok;
        if (!central.ok) {
          banner.textContent =
            "Can't reach Central at " + central.url + " (" + central.error + "). " +
            "Showing the last known state.";
        }
      }
      if (pulse) pulse.classList.toggle("stale", !central.ok);
      if (updated) updated.textContent = "Updated " + new Date(snapshot.updated_at).toLocaleTimeString();

      // Rebuilding only when something changed keeps hover and focus steady.
      const signature = JSON.stringify([snapshot.lab_pcs, central.ok]);
      if (!force && signature === lastSignature) return;
      lastSignature = signature;
      render(snapshot);
    }

    let timer = null;

    async function refresh() {
      window.clearTimeout(timer);
      let delay = 3000;
      try {
        const response = await fetch("/api/status", { cache: "no-store" });
        if (!response.ok) throw new Error("HTTP " + response.status);
        const snapshot = await response.json();
        delay = Math.max(1000, snapshot.refresh_seconds * 1000);
        show(snapshot, false);
      } catch (error) {
        if (banner) {
          banner.hidden = false;
          banner.textContent = "Lost contact with the Control Center (" + error.message + "). Retrying…";
        }
        if (pulse) pulse.classList.add("stale");
      }
      timer = window.setTimeout(refresh, delay);
    }
    refreshNow = refresh;

    window.setInterval(() => {
      if (lastSnapshot) show(lastSnapshot, true);
    }, 15000);
    refresh();
  }

  Object.assign(window.LabNet, {
    allDevices,
    ago,
    deviceItem,
    deviceState,
    holderText,
    plural,
    statusPill,
    watchNetwork,
  });
})();
