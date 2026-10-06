// A small Markdown renderer for Photo-Agent's answers. It builds DOM nodes
// (never innerHTML), so nothing in an answer can inject markup or scripts.
// Supported: paragraphs, headings, lists, quotes, tables, rules, fenced code
// (with a Copy button), `code`, **bold**, and http(s) links. An unclosed code
// fence renders as code, so a streaming answer looks right while it arrives.

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

  function render(markdown) {
    const fragment = document.createDocumentFragment();
    const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
    let paragraph = [];

    function flush() {
      if (paragraph.length) fragment.append(node("p", "", withBreaks(paragraph)));
      paragraph = [];
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = line.match(FENCE);
      if (fence) {
        flush();
        const body = [];
        for (i++; i < lines.length && !lines[i].trim().startsWith(fence[1]); i++) body.push(lines[i]);
        fragment.append(codeBlock(fence[2], body));
        continue;
      }
      if (!line.trim()) {
        flush();
        continue;
      }
      const heading = line.match(HEADING);
      if (heading) {
        flush();
        fragment.append(node("h" + Math.min(6, heading[1].length + 2), "md-heading", inline(heading[2])));
        continue;
      }
      if (RULE.test(line)) {
        flush();
        fragment.append(node("hr"));
        continue;
      }
      if (line.trim().startsWith("|") && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1])) {
        flush();
        const head = node("thead", "", [tableRow(line, "th")]);
        const body = node("tbody");
        for (i += 2; i < lines.length && lines[i].trim().startsWith("|"); i++) body.append(tableRow(lines[i], "td"));
        i--;
        fragment.append(node("div", "md-table", [node("table", "", [head, body])]));
        continue;
      }
      const item = line.match(LIST_ITEM);
      if (item) {
        flush();
        const ordered = /\d/.test(item[2]);
        const list = node(ordered ? "ol" : "ul");
        for (; i < lines.length; i++) {
          const next = lines[i].match(LIST_ITEM);
          if (next) {
            const entry = node("li", next[1].length >= 2 ? "md-nested" : "", inline(next[3]));
            list.append(entry);
          } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && list.lastChild) {
            list.lastChild.append(" ", ...inline(lines[i].trim())); // a wrapped item continues
          } else {
            break;
          }
        }
        i--;
        fragment.append(list);
        continue;
      }
      if (line.startsWith(">")) {
        flush();
        const quoted = [];
        for (; i < lines.length && lines[i].startsWith(">"); i++) quoted.push(lines[i].replace(/^>\s?/, ""));
        i--;
        fragment.append(node("blockquote", "", withBreaks(quoted)));
        continue;
      }
      paragraph.push(line);
    }
    flush();
    return fragment;
  }

  window.PQTMarkdown = { render };
})();
