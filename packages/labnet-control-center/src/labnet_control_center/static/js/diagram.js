// The setup-diagram editor: an SVG canvas with pan and zoom, blocks
// (instruments, passive optics, notes) and links (beams and signal cables).
// It never changes the experiment itself: every edit goes through
// options.commit(label, mutate, opts), and the page re-renders it from the
// new document. Rendering is keyed by id, so live updates only touch what
// changed and a drag never loses its element.

(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const XHTML = "http://www.w3.org/1999/xhtml";
  const { sizeOf, snap, GRID } = window.LabNet.experiments;
  const { iconGroup } = window.LabNet.optics;
  const MIN_ZOOM = 0.25;
  const MAX_ZOOM = 3;

  function svg(tag, attributes, children) {
    const node = document.createElementNS(NS, tag);
    for (const [name, value] of Object.entries(attributes || {})) {
      if (value !== null && value !== undefined && value !== false) node.setAttribute(name, String(value));
    }
    for (const child of children || []) if (child) node.append(child);
    return node;
  }

  function text(x, y, content, className, anchor) {
    return svg("text", { x, y, class: className, "text-anchor": anchor || "start" }, [content]);
  }

  function truncate(value, max) {
    return value.length > max ? value.slice(0, max - 1) + "…" : value;
  }

  function createDiagram(host, options) {
    // ---- Structure ----------------------------------------------------------------
    const minorGrid = svg("pattern", { id: "dg-minor", width: GRID, height: GRID, patternUnits: "userSpaceOnUse" }, [
      svg("circle", { cx: 0, cy: 0, r: 1.1, class: "grid-dot" }),
    ]);
    const majorGrid = svg("pattern", { id: "dg-major", width: GRID * 5, height: GRID * 5, patternUnits: "userSpaceOnUse" }, [
      svg("path", { d: "M" + GRID * 5 + " 0H0V" + GRID * 5, class: "grid-line", fill: "none" }),
    ]);
    const marker = (id) =>
      svg("marker", {
        id,
        viewBox: "0 0 10 10",
        refX: 9,
        refY: 5,
        markerWidth: 11,
        markerHeight: 11,
        markerUnits: "userSpaceOnUse",
        orient: "auto",
      }, [svg("path", { d: "M0 0L10 5L0 10z" })]);
    const defs = svg("defs", {}, [minorGrid, majorGrid, marker("dg-arrow-beam"), marker("dg-arrow-signal")]);
    const minorRect = svg("rect", { class: "grid-minor", width: "100%", height: "100%", fill: "url(#dg-minor)" });
    const majorRect = svg("rect", { width: "100%", height: "100%", fill: "url(#dg-major)" });
    const linksLayer = svg("g", { class: "links" });
    const nodesLayer = svg("g", { class: "nodes" });
    const preview = svg("path", { class: "connect-preview", hidden: true });
    const rubber = svg("rect", { class: "rubber-band", hidden: true });
    const ghost = svg("g", { class: "drop-ghost", hidden: true });
    const viewport = svg("g", { class: "viewport" }, [linksLayer, nodesLayer, preview, ghost]);
    const root = svg("svg", {
      class: "diagram",
      tabindex: 0,
      role: "application",
      "aria-label": "Experiment diagram. Drag blocks to move them, drag a block's round handle onto another block to connect them.",
    }, [defs, minorRect, majorRect, viewport, rubber]);
    host.append(root);

    let doc = { nodes: [], links: [] };
    let view = { k: 1, tx: 0, ty: 0 };
    const selectedNodes = new Set();
    const selectedLinks = new Set();
    const nodeEls = new Map(); // id -> {g, signature}
    const linkEls = new Map();
    let dragPositions = null; // id -> {x, y} while blocks are being dragged
    let gesture = null;
    let spaceHeld = false;
    let frame = 0;

    // ---- Coordinates ----------------------------------------------------------------

    function screenToWorld(clientX, clientY) {
      const rect = root.getBoundingClientRect();
      return { x: (clientX - rect.left - view.tx) / view.k, y: (clientY - rect.top - view.ty) / view.k };
    }

    function worldToScreen(x, y) {
      const rect = root.getBoundingClientRect();
      return { x: rect.left + view.tx + x * view.k, y: rect.top + view.ty + y * view.k };
    }

    function contains(clientX, clientY) {
      const rect = root.getBoundingClientRect();
      return clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
    }

    function applyView(notify) {
      viewport.setAttribute("transform", "translate(" + view.tx + " " + view.ty + ") scale(" + view.k + ")");
      const transform = "translate(" + view.tx + " " + view.ty + ") scale(" + view.k + ")";
      minorGrid.setAttribute("patternTransform", transform);
      majorGrid.setAttribute("patternTransform", transform);
      minorRect.toggleAttribute("hidden", GRID * view.k < 8);
      if (notify !== false && options.onView) options.onView({ ...view });
    }

    function zoomAt(factor, clientX, clientY) {
      const rect = root.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.k * factor));
      view = { k, tx: px - ((px - view.tx) * k) / view.k, ty: py - ((py - view.ty) * k) / view.k };
      applyView();
    }

    function zoomBy(factor) {
      const rect = root.getBoundingClientRect();
      zoomAt(factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
    }

    function bounds(nodes) {
      let box = null;
      for (const node of nodes) {
        if (node.x == null) continue;
        const [w, h] = sizeOf(node);
        const below = node.kind === "instrument" || node.kind === "note" ? 0 : 26; // optic labels
        const part = { left: node.x - w / 2, right: node.x + w / 2, top: node.y - h / 2, bottom: node.y + h / 2 + below };
        box = box
          ? {
              left: Math.min(box.left, part.left),
              right: Math.max(box.right, part.right),
              top: Math.min(box.top, part.top),
              bottom: Math.max(box.bottom, part.bottom),
            }
          : part;
      }
      return box;
    }

    function fit() {
      const rect = root.getBoundingClientRect();
      const box = bounds(doc.nodes);
      if (!box || !rect.width) {
        view = { k: 1, tx: rect.width / 2, ty: rect.height / 2 };
      } else {
        const width = box.right - box.left + 96;
        const height = box.bottom - box.top + 96;
        const k = Math.min(1.25, Math.max(MIN_ZOOM, Math.min(rect.width / width, rect.height / height)));
        view = {
          k,
          tx: rect.width / 2 - ((box.left + box.right) / 2) * k,
          ty: rect.height / 2 - ((box.top + box.bottom) / 2) * k,
        };
      }
      applyView();
    }

    function centerWorld() {
      const rect = root.getBoundingClientRect();
      return screenToWorld(rect.left + rect.width / 2, rect.top + rect.height / 2);
    }

    // The nearest free grid spot to `position` for a block of `kind`.
    function freeSpot(position, kind) {
      const [w, h] = sizeOf({ kind });
      const overlaps = (x, y) =>
        doc.nodes.some((node) => {
          if (node.x == null) return false;
          const [nw, nh] = sizeOf(node);
          return Math.abs(node.x - x) < (nw + w) / 2 + 16 && Math.abs(node.y - y) < (nh + h) / 2 + 16;
        });
      const start = { x: snap(position.x), y: snap(position.y) };
      for (let ring = 0; ring < 30; ring++) {
        for (let dx = -ring; dx <= ring; dx++) {
          for (let dy = -ring; dy <= ring; dy++) {
            if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
            const x = start.x + dx * 64;
            const y = start.y + dy * 48;
            if (!overlaps(x, y)) return { x, y };
          }
        }
      }
      return start;
    }

    // ---- Rendering ------------------------------------------------------------------

    function positionOf(node) {
      return (dragPositions && dragPositions.get(node.id)) || node;
    }

    function handle(x, y, id) {
      return svg("circle", { class: "handle", cx: x, cy: y, r: 7, "data-handle": id });
    }

    function buildInstrument(node, w, h, info) {
      const parts = [
        svg("rect", { class: "node-box", x: -w / 2, y: -h / 2, width: w, height: h, rx: 10 }),
        text(-w / 2 + 14, -h / 2 + 23, truncate(info.title, 21), "node-title"),
        text(-w / 2 + 14, -h / 2 + 41, truncate(info.subtitle, 30), "node-subtitle"),
        svg("circle", { class: "state-dot " + info.state, cx: w / 2 - 15, cy: -h / 2 + 17, r: 5 }),
      ];
      let x = -w / 2 + 14;
      for (const badge of info.badges) {
        const width = badge.text.length * 6.4 + 12;
        parts.push(
          svg("g", { class: "badge " + badge.tone, transform: "translate(" + x + " " + (h / 2 - 25) + ")" }, [
            svg("rect", { width, height: 16, rx: 4 }),
            text(width / 2, 11.5, badge.text, "badge-text", "middle"),
          ])
        );
        x += width + 6;
      }
      parts.push(handle(w / 2, 0, node.id));
      return parts;
    }

    function buildOptic(node, w, h) {
      return [
        svg("rect", { class: "node-box", x: -w / 2, y: -h / 2, width: w, height: h, rx: 14 }),
        iconGroup(node.kind, 36),
        text(0, h / 2 + 18, truncate(node.label || options.kindLabel(node.kind), 22), "node-label", "middle"),
        handle(w / 2, 0, node.id),
      ];
    }

    function buildNote(node, w, h) {
      const body = document.createElementNS(XHTML, "div");
      body.className = "note-text";
      body.textContent = node.notes || node.label || "Note";
      return [
        svg("rect", { class: "node-box", x: -w / 2, y: -h / 2, width: w, height: h, rx: 6 }),
        svg("foreignObject", { x: -w / 2 + 8, y: -h / 2 + 6, width: w - 16, height: h - 12 }, [body]),
      ];
    }

    function renderNode(node) {
      let entry = nodeEls.get(node.id);
      if (!entry) {
        entry = { g: svg("g", { "data-node": node.id }), signature: null };
        nodeEls.set(node.id, entry);
        nodesLayer.append(entry.g);
      }
      const position = positionOf(node);
      entry.g.setAttribute("transform", "translate(" + position.x + " " + position.y + ")");
      const info = node.kind === "instrument" ? options.describe(node) : null;
      const selected = selectedNodes.has(node.id);
      const signature = JSON.stringify([node.kind, node.label, node.notes, info, selected]);
      if (signature === entry.signature) return;
      entry.signature = signature;
      const [w, h] = sizeOf(node);
      const kind = node.kind === "instrument" || node.kind === "note" ? node.kind : "optic";
      const classes = ["node", kind];
      if (selected) classes.push("selected");
      if (info && info.missing) classes.push("missing");
      if (info && info.errors) classes.push("has-errors");
      entry.g.setAttribute("class", classes.join(" "));
      entry.g.replaceChildren(
        ...(node.kind === "instrument" ? buildInstrument(node, w, h, info) : node.kind === "note" ? buildNote(node, w, h) : buildOptic(node, w, h))
      );
      entry.g.append(svg("title", {}, [node.label || node.device_id || options.kindLabel(node.kind)]));
    }

    // Where a link leaves `node` toward `point`: the edge of its block.
    function anchor(node, point) {
      const [w, h] = sizeOf(node);
      const p = positionOf(node);
      const dx = point.x - p.x;
      const dy = point.y - p.y;
      if (!dx && !dy) return { x: p.x, y: p.y };
      const t = Math.min((w / 2 + 5) / Math.abs(dx || 1e-9), (h / 2 + 5) / Math.abs(dy || 1e-9));
      return { x: p.x + dx * t, y: p.y + dy * t };
    }

    function linkGeometry(link, byId) {
      const source = byId.get(link.source);
      const target = byId.get(link.target);
      if (!source || !target) return null;
      let a = anchor(source, positionOf(target));
      let b = anchor(target, positionOf(source));
      // A beam and a cable between the same two blocks sit side by side.
      const twin = doc.links.some(
        (other) =>
          other !== link && other.type !== link.type &&
          ((other.source === link.source && other.target === link.target) ||
            (other.source === link.target && other.target === link.source))
      );
      if (twin) {
        const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const shift = link.type === "beam" ? -6 : 6;
        const nx = (-(b.y - a.y) / length) * shift;
        const ny = ((b.x - a.x) / length) * shift;
        a = { x: a.x + nx, y: a.y + ny };
        b = { x: b.x + nx, y: b.y + ny };
      }
      return { a, b };
    }

    function renderLinks() {
      const byId = new Map(doc.nodes.filter((node) => node.x != null).map((node) => [node.id, node]));
      const seen = new Set();
      for (const link of doc.links) {
        const geometry = linkGeometry(link, byId);
        if (!geometry) continue;
        seen.add(link.id);
        let entry = linkEls.get(link.id);
        if (!entry) {
          const hit = svg("path", { class: "link-hit" });
          const glow = svg("path", { class: "link-glow" });
          const line = svg("path", { class: "link-line" });
          const label = svg("text", { class: "link-label", "text-anchor": "middle" });
          const g = svg("g", { "data-link": link.id }, [hit, glow, line, label]);
          linksLayer.append(g);
          entry = { g, hit, glow, line, label };
          linkEls.set(link.id, entry);
        }
        const { a, b } = geometry;
        const d = "M" + a.x + " " + a.y + "L" + b.x + " " + b.y;
        entry.hit.setAttribute("d", d);
        entry.glow.setAttribute("d", d);
        entry.line.setAttribute("d", d);
        entry.line.setAttribute("marker-end", "url(#dg-arrow-" + link.type + ")");
        entry.g.setAttribute("class", "link " + link.type + (selectedLinks.has(link.id) ? " selected" : ""));
        entry.label.setAttribute("x", (a.x + b.x) / 2);
        entry.label.setAttribute("y", (a.y + b.y) / 2 - 8);
        entry.label.textContent = link.label || "";
      }
      for (const [id, entry] of linkEls) {
        if (!seen.has(id)) {
          entry.g.remove();
          linkEls.delete(id);
        }
      }
    }

    function render() {
      const seen = new Set();
      for (const node of doc.nodes) {
        if (node.x == null) continue;
        seen.add(node.id);
        renderNode(node);
      }
      for (const [id, entry] of nodeEls) {
        if (!seen.has(id)) {
          entry.g.remove();
          nodeEls.delete(id);
        }
      }
      renderLinks();
    }

    function renderSoon() {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        render();
      });
    }

    // ---- Selection ----------------------------------------------------------------

    function selection() {
      return { nodes: [...selectedNodes], links: [...selectedLinks] };
    }

    function setSelection(nodes, links, notify) {
      selectedNodes.clear();
      selectedLinks.clear();
      for (const id of nodes || []) selectedNodes.add(id);
      for (const id of links || []) selectedLinks.add(id);
      render();
      if (notify && options.onSelect) options.onSelect(selection());
    }

    function reveal(id) {
      const node = doc.nodes.find((item) => item.id === id);
      if (!node || node.x == null) return;
      const rect = root.getBoundingClientRect();
      const screen = { x: view.tx + node.x * view.k, y: view.ty + node.y * view.k };
      if (screen.x < 60 || screen.y < 60 || screen.x > rect.width - 60 || screen.y > rect.height - 60) {
        view = { ...view, tx: rect.width / 2 - node.x * view.k, ty: rect.height / 2 - node.y * view.k };
        applyView();
      }
    }

    function nodeAt(world, except) {
      for (let index = doc.nodes.length - 1; index >= 0; index--) {
        const node = doc.nodes[index];
        if (node.x == null || node.id === except) continue;
        const [w, h] = sizeOf(node);
        if (Math.abs(world.x - node.x) <= w / 2 + 6 && Math.abs(world.y - node.y) <= h / 2 + 6) return node;
      }
      return null;
    }

    // ---- Gestures -------------------------------------------------------------------

    function endGesture() {
      gesture = null;
      dragPositions = null;
      preview.setAttribute("hidden", "");
      rubber.setAttribute("hidden", "");
      for (const entry of nodeEls.values()) entry.g.classList.remove("connect-target");
      root.classList.remove("panning", "connecting");
      render();
    }

    root.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 && event.button !== 1) return;
      root.focus({ preventScroll: true });
      const start = { x: event.clientX, y: event.clientY };
      const world = screenToWorld(event.clientX, event.clientY);
      const target = event.target.closest("[data-handle],[data-node],[data-link]");
      if (event.button === 1 || spaceHeld) {
        gesture = { type: "pan", start, view: { ...view }, moved: true };
      } else if (target && target.dataset.handle) {
        gesture = { type: "connect", source: target.dataset.handle };
        root.classList.add("connecting");
      } else if (target && target.dataset.node) {
        const id = target.dataset.node;
        if (event.shiftKey || event.ctrlKey || event.metaKey) {
          if (selectedNodes.has(id)) selectedNodes.delete(id);
          else selectedNodes.add(id);
          setSelection([...selectedNodes], [], true);
        } else if (!selectedNodes.has(id)) {
          setSelection([id], [], true);
        }
        const origins = new Map();
        for (const node of doc.nodes) if (selectedNodes.has(node.id)) origins.set(node.id, { x: node.x, y: node.y });
        gesture = { type: "press-node", id, start, world, origins };
      } else if (target && target.dataset.link) {
        setSelection([], [target.dataset.link], true);
        return;
      } else if (event.shiftKey) {
        gesture = { type: "rubber", world };
      } else {
        gesture = { type: "press-empty", start, view: { ...view } };
      }
      root.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    root.addEventListener("pointermove", (event) => {
      if (!gesture) return;
      const world = screenToWorld(event.clientX, event.clientY);
      const moved = Math.hypot(event.clientX - (gesture.start ? gesture.start.x : 0), event.clientY - (gesture.start ? gesture.start.y : 0));
      if (gesture.type === "press-node" && moved > 3) {
        gesture.type = "drag";
        dragPositions = new Map();
      }
      if (gesture.type === "press-empty" && moved > 3) {
        gesture.type = "pan";
        root.classList.add("panning");
      }
      if (gesture.type === "drag") {
        const dx = world.x - gesture.world.x;
        const dy = world.y - gesture.world.y;
        for (const [id, origin] of gesture.origins) {
          const x = origin.x + dx;
          const y = origin.y + dy;
          dragPositions.set(id, event.altKey ? { x, y } : { x: snap(x), y: snap(y) });
        }
        renderSoon();
      } else if (gesture.type === "pan") {
        view = { ...gesture.view, tx: gesture.view.tx + event.clientX - gesture.start.x, ty: gesture.view.ty + event.clientY - gesture.start.y };
        applyView();
      } else if (gesture.type === "connect") {
        const source = doc.nodes.find((node) => node.id === gesture.source);
        const candidate = nodeAt(world, gesture.source);
        const valid = candidate && candidate.kind !== "note" && source;
        const from = anchor(source, valid ? candidate : world);
        const to = valid ? anchor(candidate, source) : world;
        const kind = valid ? options.linkKind(source, candidate, event.altKey) : "beam";
        preview.setAttribute("d", "M" + from.x + " " + from.y + "L" + to.x + " " + to.y);
        preview.setAttribute("class", "connect-preview " + kind);
        preview.removeAttribute("hidden");
        for (const [id, entry] of nodeEls) entry.g.classList.toggle("connect-target", Boolean(valid && id === candidate.id));
      } else if (gesture.type === "rubber") {
        const a = worldToScreen(gesture.world.x, gesture.world.y);
        const rect = root.getBoundingClientRect();
        const x = Math.min(a.x, event.clientX) - rect.left;
        const y = Math.min(a.y, event.clientY) - rect.top;
        rubber.setAttribute("x", x);
        rubber.setAttribute("y", y);
        rubber.setAttribute("width", Math.abs(event.clientX - a.x));
        rubber.setAttribute("height", Math.abs(event.clientY - a.y));
        rubber.removeAttribute("hidden");
        gesture.end = world;
      }
    });

    root.addEventListener("pointerup", (event) => {
      if (!gesture) return;
      const current = gesture;
      const world = screenToWorld(event.clientX, event.clientY);
      if (current.type === "drag" && dragPositions && dragPositions.size) {
        const moves = new Map(dragPositions);
        const changed = [...moves].some(([id, p]) => {
          const origin = current.origins.get(id);
          return origin.x !== p.x || origin.y !== p.y;
        });
        endGesture();
        if (changed) {
          options.commit(moves.size === 1 ? "Move" : "Move " + moves.size + " blocks", (d) => {
            for (const node of d.nodes) {
              const p = moves.get(node.id);
              if (p) {
                node.x = p.x;
                node.y = p.y;
              }
            }
          });
        }
        return;
      }
      if (current.type === "press-empty") setSelection([], [], true);
      if (current.type === "connect") {
        const source = doc.nodes.find((node) => node.id === current.source);
        const target = nodeAt(world, current.source);
        endGesture();
        if (source && target && target.kind !== "note" && source.kind !== "note") {
          options.connect(source, target, options.linkKind(source, target, event.altKey));
        }
        return;
      }
      if (current.type === "rubber" && current.end) {
        const left = Math.min(current.world.x, current.end.x);
        const right = Math.max(current.world.x, current.end.x);
        const top = Math.min(current.world.y, current.end.y);
        const bottom = Math.max(current.world.y, current.end.y);
        const inside = doc.nodes.filter((node) => {
          if (node.x == null) return false;
          const [w, h] = sizeOf(node);
          return node.x + w / 2 >= left && node.x - w / 2 <= right && node.y + h / 2 >= top && node.y - h / 2 <= bottom;
        });
        endGesture();
        setSelection(inside.map((node) => node.id), [], true);
        return;
      }
      endGesture();
    });

    for (const type of ["pointercancel", "lostpointercapture"]) {
      root.addEventListener(type, () => {
        if (gesture) endGesture();
      });
    }
    window.addEventListener("blur", () => {
      spaceHeld = false;
      if (gesture) endGesture();
    });

    root.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        zoomAt(Math.exp(-event.deltaY * 0.0015), event.clientX, event.clientY);
      },
      { passive: false }
    );

    const editable = (target) => target && target.closest && target.closest("input, textarea, select, [contenteditable]");
    window.addEventListener("keydown", (event) => {
      if (event.code === "Space" && !editable(event.target) && !event.repeat) {
        spaceHeld = true;
        root.classList.add("pan-ready");
        if (document.activeElement === root) event.preventDefault();
      }
    });
    window.addEventListener("keyup", (event) => {
      if (event.code === "Space") {
        spaceHeld = false;
        root.classList.remove("pan-ready");
      }
    });

    // Keep the middle of the view still when the canvas changes size (e.g.
    // the Photo-Agent sidebar collapses).
    let lastSize = null;
    new ResizeObserver(() => {
      const rect = root.getBoundingClientRect();
      if (lastSize && rect.width && lastSize.width) {
        view = { ...view, tx: view.tx + (rect.width - lastSize.width) / 2, ty: view.ty + (rect.height - lastSize.height) / 2 };
        applyView();
      }
      lastSize = { width: rect.width, height: rect.height };
    }).observe(root);

    // ---- Drop preview (from the palette) --------------------------------------------

    function previewDrop(kind, clientX, clientY) {
      if (!contains(clientX, clientY)) {
        ghost.setAttribute("hidden", "");
        return null;
      }
      const world = screenToWorld(clientX, clientY);
      const position = { x: snap(world.x), y: snap(world.y) };
      const [w, h] = sizeOf({ kind });
      ghost.replaceChildren(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 10 }));
      ghost.setAttribute("transform", "translate(" + position.x + " " + position.y + ")");
      ghost.removeAttribute("hidden");
      return position;
    }

    function endPreview() {
      ghost.setAttribute("hidden", "");
    }

    applyView(false); // the default view isn't worth remembering

    return {
      element: root,
      setDocument(next) {
        doc = next;
        for (const id of [...selectedNodes]) if (!doc.nodes.some((node) => node.id === id)) selectedNodes.delete(id);
        for (const id of [...selectedLinks]) if (!doc.links.some((link) => link.id === id)) selectedLinks.delete(id);
        render();
      },
      refresh: render,
      select(nodes, links, { reveal: show } = {}) {
        setSelection(nodes, links, false);
        if (show && nodes && nodes.length) reveal(nodes[0]);
      },
      selection,
      fit,
      zoomBy,
      centerWorld,
      freeSpot,
      screenToWorld,
      worldToScreen,
      contains,
      previewDrop,
      endPreview,
      view: () => ({ ...view }),
      setView(next) {
        view = { ...next };
        applyView();
      },
      focus: () => root.focus({ preventScroll: true }),
      busy: () => Boolean(gesture),
    };
  }

  window.LabNet.createDiagram = createDiagram;
})();
