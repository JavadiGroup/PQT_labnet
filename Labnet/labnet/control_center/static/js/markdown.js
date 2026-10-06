// A small Markdown renderer for Photo-Agent's answers. It builds DOM nodes
// (never innerHTML), so nothing in an answer can inject markup or scripts.
// Supported: paragraphs, headings, lists, quotes, tables, rules, fenced code
// (with a Copy button), `code`, **bold**, and http(s) links. An unclosed code
// fence renders as code, so a streaming answer looks right while it arrives.
// register(language, fn) renders a fenced block another way (e.g. a card).

(function () {
  "use strict";

  function node(tag, className, children) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    for (const child of children || []) element.append(child);
    return element;
  }

  const INLINE = /`([^`\n]+)`|\*\*([^*\n]+?)\*\*|\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g;

  function inline(text) {
    const parts = [];
    let last = 0;
    for (const match of text.matchAll(INLINE)) {
      if (match.index > last) parts.push(text.slice(last, match.index));
      if (match[1] !== undefined) {
        parts.push(node("code", "md-inline-code", [match[1]]));
      } else if (match[2] !== undefined) {
        parts.push(node("strong", "", inline(match[2])));
      } else {
        const link = node("a", "", [match[3]]);
        link.href = match[4];
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        parts.push(link);
      }
      last = match.index + match[0].length;
    }
    if (last < text.length) parts.push(text.slice(last));
    return parts;
  }

  function withBreaks(lines) {
    const parts = [];
    lines.forEach((line, index) => {
      if (index > 0) parts.push(document.createElement("br"));
      parts.push(...inline(line));
    });
    return parts;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_error) {
      // Clipboard API needs HTTPS or localhost; fall back to a hidden textarea.
      const area = node("textarea", "md-copy-buffer", [text]);
      document.body.append(area);
      area.select();
      const copied = document.execCommand("copy");
      area.remove();
      return copied;
    }
  }

  function codeBlock(language, lines) {
    const source = lines.join("\n");
    const copy = node("button", "md-copy", ["Copy"]);
    copy.type = "button";
    copy.addEventListener("click", async () => {
      copy.textContent = (await copyText(source)) ? "Copied" : "Copy failed";
      window.setTimeout(() => (copy.textContent = "Copy"), 1500);
    });
    return node("div", "md-code", [
      node("div", "md-code-bar", [node("span", "", [language || "code"]), copy]),
      node("pre", "", [node("code", "", [source])]),
    ]);
  }

  function tableRow(line, cellTag) {
    const cells = line.trim().replace(/^\||\|$/g, "").split("|");
    return node("tr", "", cells.map((cell) => node(cellTag, "", inline(cell.trim()))));
  }

  const FENCE = /^\s*(```|~~~)\s*([\w+#.-]*)/;
  const HEADING = /^(#{1,6})\s+(.*)$/;
  const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  // Renderers for fenced blocks by language, e.g. "pqt-experiment" cards.
  // fn(source, {closed}) returns a Node; closed is false while the fence is
  // still streaming in. A renderer that throws falls back to a code block.
  const renderers = Object.create(null);

  function register(language, renderer) {
    renderers[language] = renderer;
  }

  function fencedBlock(language, lines, closed) {
    const renderer = renderers[language];
    if (renderer) {
      try {
        const rendered = renderer(lines.join("\n"), { closed });
        if (rendered instanceof Node) return rendered;
      } catch (error) {
        console.error("Rendering a " + language + " block failed:", error);
      }
    }
    return codeBlock(language, lines);
  }

  // Splits Markdown into top-level blocks. Each has a key (its kind and exact
  // source) and a build() that makes its element, so renderInto() can keep
  // blocks whose source hasn't changed.
  function parse(markdown) {
    const blocks = [];
    const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
    let paragraph = [];

    function add(kind, source, build) {
      blocks.push({ key: kind + "\u0000" + source, build });
    }

    function flush() {
      if (paragraph.length) {
        const text = paragraph;
        add("p", text.join("\n"), () => node("p", "", withBreaks(text)));
      }
      paragraph = [];
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = line.match(FENCE);
      if (fence) {
        flush();
        const body = [];
        for (i++; i < lines.length && !lines[i].trim().startsWith(fence[1]); i++) body.push(lines[i]);
        const closed = i < lines.length;
        add("fence:" + (closed ? "closed" : "open"), line + "\n" + body.join("\n"), () =>
          fencedBlock(fence[2], body, closed)
        );
        continue;
      }
      if (!line.trim()) {
        flush();
        continue;
      }
      const heading = line.match(HEADING);
      if (heading) {
        flush();
        add("h", line, () => node("h" + Math.min(6, heading[1].length + 2), "md-heading", inline(heading[2])));
        continue;
      }
      if (RULE.test(line)) {
        flush();
        add("hr", line, () => node("hr"));
        continue;
      }
      if (line.trim().startsWith("|") && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1])) {
        flush();
        const rows = [];
        for (i += 2; i < lines.length && lines[i].trim().startsWith("|"); i++) rows.push(lines[i]);
        i--;
        add("table", line + "\n" + rows.join("\n"), () =>
          node("div", "md-table", [
            node("table", "", [
              node("thead", "", [tableRow(line, "th")]),
              node("tbody", "", rows.map((row) => tableRow(row, "td"))),
            ]),
          ])
        );
        continue;
      }
      const item = line.match(LIST_ITEM);
      if (item) {
        flush();
        const ordered = /\d/.test(item[2]);
        const source = [];
        for (; i < lines.length; i++) {
          if (lines[i].match(LIST_ITEM) || (lines[i].trim() && /^\s{2,}/.test(lines[i]) && source.length)) {
            source.push(lines[i]);
          } else {
            break;
          }
        }
        i--;
        add(ordered ? "ol" : "ul", source.join("\n"), () => {
          const list = node(ordered ? "ol" : "ul");
          for (const entry of source) {
            const next = entry.match(LIST_ITEM);
            if (next) list.append(node("li", next[1].length >= 2 ? "md-nested" : "", inline(next[3])));
            else list.lastChild.append(" ", ...inline(entry.trim())); // a wrapped item continues
          }
          return list;
        });
        continue;
      }
      if (line.startsWith(">")) {
        flush();
        const quoted = [];
        for (; i < lines.length && lines[i].startsWith(">"); i++) quoted.push(lines[i].replace(/^>\s?/, ""));
        i--;
        add("quote", quoted.join("\n"), () => node("blockquote", "", withBreaks(quoted)));
        continue;
      }
      paragraph.push(line);
    }
    flush();
    return blocks;
  }

  function render(markdown) {
    const fragment = document.createDocumentFragment();
    for (const block of parse(markdown)) fragment.append(block.build());
    return fragment;
  }

  // Updates `container` to show `markdown`, keeping every leading block whose
  // source is unchanged. A streaming answer only grows at the end, so buttons
  // and open details in earlier blocks stay put (and stay clickable).
  const blockKeys = new WeakMap();

  function renderInto(container, markdown) {
    const blocks = parse(markdown);
    const existing = [...container.children];
    let kept = 0;
    while (kept < blocks.length && kept < existing.length && blockKeys.get(existing[kept]) === blocks[kept].key) {
      kept++;
    }
    for (let index = existing.length - 1; index >= kept; index--) existing[index].remove();
    for (const block of blocks.slice(kept)) {
      let element = block.build();
      if (!(element instanceof Element)) element = node("div", "", [element]);
      blockKeys.set(element, block.key);
      container.append(element);
    }
  }

  window.PQTMarkdown = { codeBlock, copyText, register, render, renderInto };
})();
