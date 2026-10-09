// The palette beside the canvas: the network's devices and the passive
// components. Drag an item onto the canvas, or click it (or press Enter) to
// add it in the middle of the view. A device already on the diagram can't be
// added twice; clicking it shows its block.

(function () {
  "use strict";

  const { el, deviceState } = window.LabNet;
  const { iconSvg } = window.LabNet.optics;

  function createPalette(host, options) {
    // options: preview(entry, x, y) -> bool, endPreview(), drop(entry, x, y),
    // add(entry), placed(deviceId) -> nodeId | null, reveal(nodeId)
    const deviceList = el("ul", { class: "palette-list", "aria-label": "Devices" });
    const opticList = el("ul", { class: "palette-list", "aria-label": "Components" });
    host.append(
      el("section", { class: "palette-section" }, [el("h3", { text: "Devices" }), deviceList]),
      el("section", { class: "palette-section" }, [el("h3", { text: "Optics and notes" }), opticList]),
      el("p", { class: "palette-hint", text: "Drag onto the canvas, or click to add." })
    );

    let devices = [];
    let dragging = null;
    let heldDevices = null; // network updates wait until a drag ends

    function item(entry) {
      const placedId = entry.device ? options.placed(entry.device.id) : null;
      const children = [entry.device ? iconSvg("instrument", 20) : iconSvg(entry.kind, 20)];
      if (entry.device) {
        const state = deviceState(entry.device);
        children.push(
          el("span", { class: "palette-text" }, [
            el("span", { class: "palette-name", text: entry.device.id }),
            el("span", {
              class: "palette-sub",
              text: placedId ? "On the diagram" : entry.device.type_label,
            }),
          ]),
          el("span", { class: "palette-dot " + state.tone, title: state.label })
        );
      } else {
        children.push(el("span", { class: "palette-text" }, [el("span", { class: "palette-name", text: entry.label })]));
      }
      const li = el(
        "li",
        {
          class: "palette-item" + (placedId ? " placed" : ""),
          tabindex: "0",
          role: "button",
          "aria-label": placedId ? "Show " + entry.label + " on the diagram" : "Add " + entry.label,
        },
        children
      );
      li.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        host.setPointerCapture(event.pointerId); // the palette root outlives re-renders
        dragging = { entry, start: { x: event.clientX, y: event.clientY }, moved: false, ghost: null };
      });
      li.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate(entry);
        }
      });
      return li;
    }

    function activate(entry) {
      const placedId = entry.device ? options.placed(entry.device.id) : null;
      if (placedId) options.reveal(placedId);
      else options.add(entry);
    }

    host.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      if (!dragging.moved && Math.hypot(event.clientX - dragging.start.x, event.clientY - dragging.start.y) < 4) return;
      if (!dragging.moved) {
        if (dragging.entry.device && options.placed(dragging.entry.device.id)) return; // already on it
        dragging.moved = true;
        dragging.ghost = el("div", { class: "palette-ghost", text: dragging.entry.label });
        document.body.append(dragging.ghost);
      }
      const over = options.preview(dragging.entry, event.clientX, event.clientY);
      dragging.ghost.hidden = Boolean(over);
      dragging.ghost.style.transform = "translate(" + (event.clientX + 12) + "px, " + (event.clientY + 10) + "px)";
    });

    function finish(event, cancelled) {
      if (!dragging) return;
      const current = dragging;
      dragging = null;
      if (current.ghost) current.ghost.remove();
      options.endPreview();
      if (!cancelled) {
        if (!current.moved) activate(current.entry);
        else options.drop(current.entry, event.clientX, event.clientY);
      }
      if (heldDevices) {
        const next = heldDevices;
        heldDevices = null;
        setDevices(next);
      }
    }

    host.addEventListener("pointerup", (event) => finish(event, false));
    host.addEventListener("pointercancel", (event) => finish(event, true));

    function setDevices(next) {
      if (dragging) {
        heldDevices = next;
        return;
      }
      devices = next;
      render();
    }

    let optics = [];

    function setOptics(next) {
      optics = next;
      render();
    }

    function render() {
      if (dragging) return;
      const sorted = [...devices].sort((a, b) => a.type_label.localeCompare(b.type_label) || a.id.localeCompare(b.id));
      deviceList.replaceChildren(
        ...(sorted.length
          ? sorted.map((device) => item({ device, label: device.id }))
          : [el("li", { class: "palette-empty", text: "Waiting for the network…" })])
      );
      opticList.replaceChildren(
        ...optics.map((optic) => item({ kind: optic.kind, label: optic.label })),
        item({ kind: "note", label: "Note" })
      );
    }

    return { setDevices, setOptics, refresh: render };
  }

  window.LabNet.createPalette = createPalette;
})();
