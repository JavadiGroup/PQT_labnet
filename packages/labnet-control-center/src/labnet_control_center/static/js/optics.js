// Line icons for diagram components, on a 24-unit grid. The same paths draw
// the palette's icons (in HTML) and the blocks on the canvas (inside SVG).

(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const PATHS = {
    laser: ["M2 8h12v8H2z", "M14 12h8", "M19 9l3 3-3 3", "M5 11h3"],
    mirror: ["M5 19L19 5", "M8 21l-3-3", "M12 17l-3-3", "M16 13l-3-3", "M20 9l-3-3"],
    beamsplitter: ["M5 5h14v14H5z", "M5 19L19 5"],
    pbs: ["M5 5h14v14H5z", "M5 19L19 5", "M9 5v4", "M15 19v-4"],
    lens: ["M12 2c4 4 4 16 0 20c-4-4-4-16 0-20z"],
    filter: ["M9 2h6v20H9z", "M9 7h6", "M9 12h6", "M9 17h6"],
    waveplate: ["M10 2h4v20h-4z", "M3 7l4 5-4 5", "M21 7l-4 5 4 5"],
    fiber: ["M2 12c3 0 3-6 6-6s3 12 6 12 3-6 6-6", "M20 12h2"],
    isolator: ["M3 7h18v10H3z", "M7 12h9", "M13 9l3 3-3 3"],
    iris: ["M12 3a9 9 0 1 0 0.01 0z", "M12 9a3 3 0 1 0 0.01 0z"],
    sample: ["M7 3h10v18H7z", "M7 8h10", "M10 13h4"],
    beam_dump: ["M3 5h11v14H3z", "M14 8l6 4-6 4", "M6 9l5 6", "M6 15l5-6"],
    photodetector: ["M5 13a7 7 0 0 1 14 0v5H5z", "M9 18v3", "M15 18v3", "M12 6V3"],
    note: ["M5 3h10l4 4v14H5z", "M15 3v4h4", "M8 12h8", "M8 16h6"],
    instrument: ["M3 6h18v12H3z", "M7 10h4", "M7 14h10", "M16 9.5h1"],
  };

  // Paths for `kind`, scaled into a box `size` units wide centered at (0, 0).
  function iconGroup(kind, size) {
    const group = document.createElementNS(NS, "g");
    const scale = size / 24;
    group.setAttribute("transform", "translate(" + -size / 2 + " " + -size / 2 + ") scale(" + scale + ")");
    group.setAttribute("class", "icon");
    for (const d of PATHS[kind] || PATHS.note) {
      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", d);
      group.append(path);
    }
    return group;
  }

  // A standalone <svg> icon for HTML (palette, cards).
  function iconSvg(kind, size) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", String(size || 20));
    svg.setAttribute("height", String(size || 20));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "optic-icon");
    for (const d of PATHS[kind] || PATHS.note) {
      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", d);
      svg.append(path);
    }
    return svg;
  }

  window.PQT = Object.assign(window.PQT || {}, { optics: { iconGroup, iconSvg, kinds: Object.keys(PATHS) } });
})();
