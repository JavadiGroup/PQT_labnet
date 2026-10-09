// Pure helpers for experiments (no DOM): block sizes, ids, sweep points,
// summaries, automatic layout and undo history. Shared by the canvas, the
// experiments list and Photo-Agent's proposal cards.

(function () {
  "use strict";

  const GRID = 16;
  const SIZES = { instrument: [192, 80], note: [160, 96], optic: [64, 64] };
  const ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;

  function sizeOf(node) {
    if (node.kind === "instrument") return SIZES.instrument;
    if (node.kind === "note") return SIZES.note;
    return SIZES.optic;
  }

  function snap(value) {
    return Math.round(value / GRID) * GRID;
  }

  // A readable id that is free in `taken` (node ids, link ids, ...).
  function freeId(base, taken) {
    let stem = String(base || "item")
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .replace(/^[^A-Za-z]+/, "")
      .slice(0, 24)
      .replace(/-+$/, "");
    if (!stem) stem = "item";
    if (!taken.has(stem)) return stem;
    for (let n = 2; ; n++) {
      if (!taken.has(stem + "-" + n)) return stem + "-" + n;
    }
  }

  // ---- Numbers and sweeps ------------------------------------------------------

  function formatNumber(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
    return String(Number(value.toPrecision(10)));
  }

  // Exactly what the exported script steps through: index-based, ends exact.
  function sweepPoints(sweep) {
    const { start, stop, points } = sweep;
    return Array.from({ length: points }, (_, index) =>
      index === points - 1 ? stop : start + ((stop - start) * index) / (points - 1)
    );
  }

  function describeSweep(sweep, unit) {
    const values = sweepPoints(sweep);
    const suffix = unit && unit !== "1" ? " " + unit : "";
    const shown =
      values.length <= 4
        ? values.map(formatNumber).join(", ")
        : values.slice(0, 3).map(formatNumber).join(", ") + " … " + formatNumber(values[values.length - 1]);
    const step = formatNumber((sweep.stop - sweep.start) / (sweep.points - 1));
    return sweep.points + " points: " + shown + suffix + " · step " + step + suffix;
  }

  function totalPoints(doc) {
    const axes = new Map();
    for (const node of doc.nodes || []) {
      for (const setting of node.settings || []) {
        if (setting.sweep) axes.set(setting.sweep.axis || 0, setting.sweep.points);
      }
    }
    let total = 1;
    for (const points of axes.values()) total *= points;
    return total;
  }

  function summarize(doc) {
    const nodes = doc.nodes || [];
    const instruments = nodes.filter((node) => node.kind === "instrument");
    return {
      devices: instruments.length,
      optics: nodes.filter((node) => node.kind !== "instrument" && node.kind !== "note").length,
      links: (doc.links || []).length,
      sweeps: instruments.reduce((n, node) => n + (node.settings || []).filter((s) => s.sweep).length, 0),
      records: instruments.reduce((n, node) => n + (node.records || []).length, 0),
      points: totalPoints(doc),
    };
  }

  // ---- Automatic layout --------------------------------------------------------
  // Left to right along the links (beams and cables alike), one connected
  // group under another; unlinked blocks and notes on a shelf below. Loops
  // (a double-pass AOM) are broken only to decide the columns.

  function layout(nodes, links) {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const out = new Map(nodes.map((node) => [node.id, []]));
    const into = new Map(nodes.map((node) => [node.id, []]));
    for (const link of links) {
      if (!byId.has(link.source) || !byId.has(link.target) || link.source === link.target) continue;
      out.get(link.source).push(link.target);
      into.get(link.target).push(link.source);
    }
    const rank = (node) => (node.kind === "laser" ? 0 : node.kind === "instrument" ? 1 : 2);
    const order = [...nodes].sort((a, b) => rank(a) - rank(b) || (a.id < b.id ? -1 : 1));

    // Connected groups (ignoring direction).
    const group = new Map();
    let groups = 0;
    for (const node of order) {
      if (group.has(node.id) || (!out.get(node.id).length && !into.get(node.id).length)) continue;
      const stack = [node.id];
      group.set(node.id, groups);
      while (stack.length) {
        const id = stack.pop();
        for (const next of [...out.get(id), ...into.get(id)]) {
          if (!group.has(next)) {
            group.set(next, groups);
            stack.push(next);
          }
        }
      }
      groups++;
    }

    // Drop back edges (depth-first from the sources), then longest-path columns.
    const forward = new Map(nodes.map((node) => [node.id, []]));
    const state = new Map();
    function visit(id) {
      state.set(id, 1);
      for (const next of out.get(id)) {
        if (state.get(next) === 1) continue; // a loop: ignore for layering
        forward.get(id).push(next);
        if (!state.has(next)) visit(next);
      }
      state.set(id, 2);
    }
    for (const node of order) if (group.has(node.id) && !into.get(node.id).length && !state.has(node.id)) visit(node.id);
    for (const node of order) if (group.has(node.id) && !state.has(node.id)) visit(node.id);
    const column = new Map();
    const indegree = new Map(nodes.map((node) => [node.id, 0]));
    for (const targets of forward.values()) for (const t of targets) indegree.set(t, indegree.get(t) + 1);
    const queue = order.filter((node) => group.has(node.id) && indegree.get(node.id) === 0).map((node) => node.id);
    for (const id of queue) column.set(id, 0);
    while (queue.length) {
      const id = queue.shift();
      for (const next of forward.get(id)) {
        column.set(next, Math.max(column.get(next) || 0, column.get(id) + 1));
        indegree.set(next, indegree.get(next) - 1);
        if (indegree.get(next) === 0) queue.push(next);
      }
    }

    const positions = new Map();
    const columnWidth = 192 + 96;
    const rowHeight = 112;
    let top = 0;
    for (let g = 0; g < groups; g++) {
      const members = order.filter((node) => group.get(node.id) === g);
      const columns = [];
      for (const node of members) {
        const c = column.get(node.id) || 0;
        (columns[c] = columns[c] || []).push(node.id);
      }
      // Barycenter sweeps: order each column by its neighbours' rows.
      const row = new Map();
      columns.forEach((ids) => ids.forEach((id, index) => row.set(id, index)));
      for (let sweep = 0; sweep < 4; sweep++) {
        columns.forEach((ids, c) => {
          if (c === 0) return;
          const score = (id) => {
            const near = into.get(id).filter((other) => (column.get(other) || 0) < c);
            return near.length ? near.reduce((sum, other) => sum + row.get(other), 0) / near.length : row.get(id);
          };
          ids.sort((a, b) => score(a) - score(b));
          ids.forEach((id, index) => row.set(id, index));
        });
      }
      const tallest = Math.max(1, ...columns.map((ids) => (ids ? ids.length : 0)));
      columns.forEach((ids, c) => {
        if (!ids) return;
        const offset = ((tallest - ids.length) * rowHeight) / 2;
        ids.forEach((id, index) => positions.set(id, { x: c * columnWidth, y: top + offset + index * rowHeight }));
      });
      top += tallest * rowHeight + 64;
    }

    // The shelf: unconnected blocks, then notes.
    const shelf = order.filter((node) => !group.has(node.id) && node.kind !== "note");
    const notes = order.filter((node) => node.kind === "note");
    let x = 0;
    for (const node of [...shelf, ...notes]) {
      const [w] = sizeOf(node);
      positions.set(node.id, { x: x + w / 2, y: top + 48 });
      x += w + 48;
    }
    for (const position of positions.values()) {
      position.x = snap(position.x);
      position.y = snap(position.y);
    }
    return positions;
  }

  // ---- Undo history --------------------------------------------------------------
  // Snapshots (JSON strings) of the document before each change. Edits with
  // the same `coalesce` key within 1.2 s (typing, nudging) are one step.

  function createHistory(limit) {
    const max = limit || 100;
    let past = [];
    let future = [];
    return {
      record(label, before, coalesce) {
        const last = past[past.length - 1];
        const now = Date.now();
        if (coalesce && last && last.coalesce === coalesce && now - last.time < 1200) {
          last.time = now;
          future = [];
          return;
        }
        past.push({ label, json: before, coalesce: coalesce || null, time: now });
        if (past.length > max) past.shift();
        future = [];
      },
      undo(current) {
        const entry = past.pop();
        if (!entry) return null;
        future.push({ label: entry.label, json: current, coalesce: null, time: 0 });
        return entry;
      },
      redo(current) {
        const entry = future.pop();
        if (!entry) return null;
        past.push({ label: entry.label, json: current, coalesce: null, time: 0 });
        return entry;
      },
      // Undo only if the newest step is still `label` (a proposal card's Undo).
      latestLabel() {
        return past.length ? past[past.length - 1].label : null;
      },
      nextRedoLabel() {
        return future.length ? future[future.length - 1].label : null;
      },
      clear() {
        past = [];
        future = [];
      },
    };
  }

  window.LabNet = Object.assign(window.LabNet || {}, {
    experiments: {
      GRID,
      ID_PATTERN,
      createHistory,
      describeSweep,
      formatNumber,
      freeId,
      layout,
      sizeOf,
      snap,
      summarize,
      sweepPoints,
      totalPoints,
    },
  });
})();
