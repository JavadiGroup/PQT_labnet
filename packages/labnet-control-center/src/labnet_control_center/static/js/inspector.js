// The inspector beside the canvas: edits whatever is selected.
//
// * an instrument: its label, then its settings (Off / Fixed / Sweep),
//   commands and readings to record, all generated from the device's contract
//   (methods, argument types, units) and the hints its driver publishes
//   (channels, ranges, choices);
// * a passive component or note: kind, label, notes and connections;
// * a link: type, label and direction;
// * nothing: the experiment's name, description and run options, and the
//   problems that would stop "Export script".
//
// Only valid values reach the experiment: a half-typed number stays in its
// field with a message. The panel is rebuilt only when the selection changes
// or the experiment changes elsewhere, so typing never loses focus.

(function () {
  "use strict";

  const { el, statusPill, deviceState, holderText } = window.LabNet;
  const X = window.LabNet.experiments;
  const FIELD_HINT = { float: "number", int: "whole number", bool: "true / false", str: "text" };

  function baseType(type) {
    return String(type).replace(" | None", "");
  }

  function niceDefault(low, high) {
    const middle = (low + high) / 2;
    return Number(middle.toPrecision(3));
  }

  function nextItemId(node, prefix) {
    const taken = new Set([...(node.settings || []), ...(node.records || [])].map((item) => item.id));
    for (let n = 1; ; n++) if (!taken.has(prefix + n)) return prefix + n;
  }

  function createInspector(host, options) {
    let selection = { nodes: [], links: [] };
    let problemsBox = null; // refreshed when a new report arrives
    let problemsFor = null; // node id, or null for the whole experiment
    let liveBox = null; // the instrument header's state, refreshed from the network
    let liveFor = null;
    let summaryBox = null; // the experiment panel's run summary, refreshed with each report
    let savedBox = null;

    // ---- Helpers ------------------------------------------------------------------

    function commitNode(nodeId, label, change, extra) {
      options.commit(
        label,
        (doc) => {
          const node = doc.nodes.find((item) => item.id === nodeId);
          if (node) change(node, doc);
        },
        Object.assign({ source: "inspector" }, extra || {})
      );
    }

    function section(title, note, children, { open = true, count } = {}) {
      const summary = el("summary", {}, [el("span", { text: title }), count ? el("span", { class: "count", text: count }) : ""]);
      return el("details", { class: "inspector-section", open }, [summary, note ? el("p", { class: "section-note", text: note }) : "", ...children]);
    }

    function textField(label, value, { multiline, maxlength, placeholder, onChange }) {
      const input = multiline
        ? el("textarea", { maxlength: String(maxlength), placeholder: placeholder || "", rows: "3" })
        : el("input", { type: "text", maxlength: String(maxlength), placeholder: placeholder || "" });
      input.value = value || "";
      input.addEventListener("input", () => onChange(input.value));
      return el("label", { class: "field" }, [el("span", { class: "field-label", text: label }), input]);
    }

    function numberField({ label, value, integer, min, max, required, placeholder, unit, hint, onValid }) {
      const input = el("input", { type: "text", inputmode: "decimal", autocomplete: "off", placeholder: placeholder || "" });
      input.value = value === undefined || value === null ? "" : X.formatNumber(value);
      const message = el("span", { class: "field-error", hidden: true });
      input.addEventListener("input", () => {
        const raw = input.value.trim();
        let problem = null;
        let number = null;
        if (raw === "") {
          if (required) problem = "enter a value";
        } else {
          number = Number(raw);
          if (!Number.isFinite(number)) problem = "not a number";
          else if (integer && !Number.isInteger(number)) problem = "enter a whole number";
          else if (min !== undefined && number < min) problem = "at least " + X.formatNumber(min);
          else if (max !== undefined && number > max) problem = "at most " + X.formatNumber(max);
        }
        input.setAttribute("aria-invalid", problem ? "true" : "false");
        message.textContent = problem ? "Not applied: " + problem + "." : "";
        message.hidden = !problem;
        if (!problem) onValid(raw === "" ? null : number);
      });
      return el("label", { class: "field compact" }, [
        el("span", { class: "field-label", text: label }),
        el("span", { class: "input-unit" }, [input, unit ? el("span", { class: "unit", text: unit }) : ""]),
        hint ? el("span", { class: "field-hint", text: hint }) : "",
        message,
      ]);
    }

    function selectField(label, value, choices, onChange) {
      const select = el("select", {}, choices.map(([choice, text]) => el("option", { value: choice, text })));
      select.value = value;
      select.addEventListener("change", () => onChange(select.value));
      return el("label", { class: "field compact" }, [el("span", { class: "field-label", text: label }), select]);
    }

    function unitOf(method, field) {
      return field === "value" && method.unit && method.unit !== "1" ? method.unit : "";
    }

    function limitsOf(hints, method, field) {
      return ((hints && hints.limits) || {})[method.name + "." + field] || null;
    }

    function limitsText(bounds, unit) {
      return bounds ? X.formatNumber(bounds[0]) + " to " + X.formatNumber(bounds[1]) + (unit ? " " + unit : "") : "";
    }

    // ---- An argument input, by its type --------------------------------------------

    function argInput(node, list, entry, method, field, hints, extra) {
      const type = baseType(field.type);
      const key = "arg:" + node.id + ":" + entry.id + ":" + field.name;
      const set = (value) =>
        commitNode(node.id, "Set " + method.name, (n) => {
          const item = n[list].find((candidate) => candidate.id === entry.id);
          if (!item) return;
          if (value === null) delete item.args[field.name];
          else item.args[field.name] = value;
        }, Object.assign({ coalesce: key }, extra || {}));
      const current = entry.args[field.name];
      if (type === "float" || type === "int") {
        const bounds = limitsOf(hints, method, field.name);
        const unit = unitOf(method, field.name);
        return numberField({
          label: field.name,
          value: current,
          integer: type === "int",
          required: !field.optional,
          unit,
          hint: limitsText(bounds, unit),
          onValid: set,
        });
      }
      if (type === "bool") {
        if (field.optional) {
          return selectField(field.name, current === undefined ? "" : String(current), [["", "Default"], ["true", "True"], ["false", "False"]],
            (value) => set(value === "" ? null : value === "true"));
        }
        const box = el("input", { type: "checkbox" });
        box.checked = current === true;
        box.addEventListener("change", () => set(box.checked));
        return el("label", { class: "field compact check" }, [box, el("span", { text: field.name })]);
      }
      if (type === "str") {
        const choices = ((hints && hints.choices) || {})[method.name + "." + field.name];
        if (choices) {
          const options = choices.map((choice) => [choice, choice]);
          if (current && !choices.includes(current)) options.unshift([current, current + " (not offered)"]);
          return selectField(field.name, current || choices[0], options, set);
        }
        return textField(field.name, current, {
          maxlength: 128,
          onChange: (value) => set(value === "" && field.optional ? null : value),
        });
      }
      return el("p", { class: "field-hint", text: field.name + " (" + field.type + ") can't be set from an experiment." });
    }

    function channelInput(node, list, entry, method, hints) {
      const channels = (hints && hints.channels) || [];
      const set = (value) =>
        commitNode(node.id, "Change channel", (n) => {
          const item = n[list].find((candidate) => candidate.id === entry.id);
          if (!item) return;
          if (value) item.args.channel = value;
          else delete item.args.channel;
        });
      if (channels.length) {
        const current = entry.args.channel || channels[0];
        const choices = channels.map((channel) => [channel, channel]);
        if (!channels.includes(current)) choices.unshift([current, current]);
        return selectField("channel", current, choices, set);
      }
      return textField("channel", entry.args.channel, { maxlength: 128, placeholder: "default", onChange: (value) => set(value.trim()) });
    }

    function freeChannel(node, list, method, hints) {
      const channels = (hints && hints.channels) || [];
      const used = new Set(node[list].filter((item) => item.method === method.name).map((item) => item.args.channel || channels[0]));
      return channels.find((channel) => !used.has(channel)) || null;
    }

    function defaultArg(method, field, hints) {
      const type = baseType(field.type);
      const choices = ((hints && hints.choices) || {})[method.name + "." + field.name];
      if (choices) return choices[0];
      if (type === "bool") return true;
      if (type === "str") return "";
      const bounds = limitsOf(hints, method, field.name);
      return bounds ? niceDefault(bounds[0], bounds[1]) : 0;
    }

    function nextAxis(doc, exceptId) {
      const axes = new Set();
      for (const node of doc.nodes) {
        for (const setting of node.settings || []) {
          if (setting.sweep && setting.id + node.id !== exceptId) axes.add(setting.sweep.axis || 0);
        }
      }
      return Math.min(axes.size, 3);
    }

    // ---- Settings and commands --------------------------------------------------------

    function setMode(node, method, entry, mode, hints) {
      const fields = method.fields.filter((field) => field.name !== "channel");
      const channelled = method.fields.some((field) => field.name === "channel");
      commitNode(node.id, mode === "off" ? "Turn off " + method.name : (mode === "sweep" ? "Sweep " : "Set ") + method.name, (n, doc) => {
        const index = entry ? n.settings.findIndex((item) => item.id === entry.id) : -1;
        if (mode === "off") {
          if (index >= 0) n.settings.splice(index, 1);
          return;
        }
        let item = index >= 0 ? n.settings[index] : null;
        if (!item) {
          item = { id: nextItemId(n, "s"), method: method.name, args: {} };
          if (channelled && hints && hints.channels) item.args.channel = freeChannel(n, "settings", method, hints) || hints.channels[0];
          n.settings.push(item);
        }
        if (mode === "fixed") {
          delete item.sweep;
          for (const field of fields) if (!field.optional && !(field.name in item.args)) item.args[field.name] = defaultArg(method, field, hints);
        } else {
          const swept = method.sweep_field;
          delete item.args[swept];
          const bounds = limitsOf(hints, method, swept);
          item.sweep = {
            field: swept,
            start: bounds ? bounds[0] : 0,
            stop: bounds ? bounds[1] : 1,
            points: 11,
            axis: nextAxis(doc, item.id + n.id),
          };
          for (const field of fields) {
            if (field.name !== swept && !field.optional && !(field.name in item.args)) item.args[field.name] = defaultArg(method, field, hints);
          }
        }
      }, { rerender: true });
    }

    function sweepInputs(node, entry, method) {
      const unit = unitOf(method, entry.sweep.field);
      const preview = el("p", { class: "sweep-preview", text: X.describeSweep(entry.sweep, unit) });
      const key = "sweep:" + node.id + ":" + entry.id;
      const set = (name) => (value) =>
        commitNode(node.id, "Change " + method.name + " sweep", (n) => {
          const item = n.settings.find((candidate) => candidate.id === entry.id);
          if (!item || !item.sweep) return;
          item.sweep[name] = value;
          preview.textContent = X.describeSweep(item.sweep, unit);
        }, { coalesce: key + ":" + name });
      return el("div", { class: "sweep" }, [
        el("div", { class: "sweep-row" }, [
          numberField({ label: "start", value: entry.sweep.start, required: true, unit, onValid: set("start") }),
          numberField({ label: "stop", value: entry.sweep.stop, required: true, unit, onValid: set("stop") }),
          numberField({ label: "points", value: entry.sweep.points, integer: true, required: true, min: 2, max: 1001, onValid: set("points") }),
          selectField("loop", String(entry.sweep.axis || 0), [["0", "1 (outer)"], ["1", "2"], ["2", "3"], ["3", "4 (inner)"]],
            (value) => commitNode(node.id, "Change loop", (n) => {
              const item = n.settings.find((candidate) => candidate.id === entry.id);
              if (item && item.sweep) item.sweep.axis = Number(value);
            }, { rerender: true })),
        ]),
        preview,
      ]);
    }

    function settingRow(node, method, entry, hints) {
      const fields = method.fields.filter((field) => field.name !== "channel");
      const channelled = method.fields.some((field) => field.name === "channel");
      const modes = [["off", "Off"], ["fixed", fields.length ? (method.kind === "command" ? "Set" : "Fixed") : "Run once"]];
      if (method.sweep_field) modes.push(["sweep", "Sweep"]);
      const mode = !entry ? "off" : entry.sweep ? "sweep" : "fixed";
      const select = el("select", { class: "mode", "aria-label": method.name }, modes.map(([value, text]) => el("option", { value, text })));
      select.value = mode;
      select.addEventListener("change", () => setMode(node, method, entry, select.value, hints));
      const row = el("div", { class: "entry-row" + (entry ? " on" : "") }, [
        el("div", { class: "entry-head" }, [
          el("span", { class: "method-name", text: method.name }),
          method.kind === "write" && method.unit && method.unit !== "1" ? el("span", { class: "method-unit", text: method.unit }) : "",
          select,
        ]),
      ]);
      if (!entry) return row;
      const inputs = [];
      if (channelled) inputs.push(channelInput(node, "settings", entry, method, hints));
      for (const field of fields) {
        if (entry.sweep && field.name === entry.sweep.field) continue;
        inputs.push(argInput(node, "settings", entry, method, field, hints));
      }
      if (inputs.length) row.append(el("div", { class: "entry-fields" }, inputs));
      if (entry.sweep) row.append(sweepInputs(node, entry, method));
      return row;
    }

    function methodBlock(node, method, hints) {
      const entries = node.settings.filter((item) => item.method === method.name);
      const block = el("div", { class: "method-block", "data-method": method.name });
      if (!entries.length) block.append(settingRow(node, method, null, hints));
      for (const entry of entries) block.append(settingRow(node, method, entry, hints));
      const channelled = method.fields.some((field) => field.name === "channel");
      if (channelled && entries.length && freeChannel(node, "settings", method, hints)) {
        const more = el("button", { class: "link-button", type: "button", text: "+ Another channel" });
        more.addEventListener("click", () => {
          const channel = freeChannel(node, "settings", method, hints);
          commitNode(node.id, "Set " + method.name, (n) => {
            const item = { id: nextItemId(n, "s"), method: method.name, args: { channel } };
            for (const field of method.fields) {
              if (field.name !== "channel" && !field.optional) item.args[field.name] = defaultArg(method, field, hints);
            }
            n.settings.push(item);
          }, { rerender: true });
        });
        block.append(more);
      }
      return block;
    }

    // ---- Readings to record -----------------------------------------------------------

    function recordRow(node, method, entry, hints, runDefaults) {
      const box = el("input", { type: "checkbox", "aria-label": "Record " + method.name });
      box.checked = Boolean(entry);
      box.addEventListener("change", () =>
        commitNode(node.id, (box.checked ? "Record " : "Stop recording ") + method.name, (n) => {
          if (box.checked) {
            const item = { id: nextItemId(n, "r"), method: method.name, args: {} };
            if (method.fields.some((field) => field.name === "channel") && hints && hints.channels) {
              item.args.channel = freeChannel(n, "records", method, hints) || hints.channels[0];
            }
            n.records.push(item);
          } else {
            const index = n.records.findIndex((item) => item.id === entry.id);
            if (index >= 0) n.records.splice(index, 1);
          }
        }, { rerender: true })
      );
      const row = el("div", { class: "entry-row record" + (entry ? " on" : "") }, [
        el("label", { class: "entry-head" }, [
          box,
          el("span", { class: "method-name", text: method.name }),
          method.unit && method.unit !== "1" ? el("span", { class: "method-unit", text: method.unit }) : "",
          method.kind === "stream" ? el("span", { class: "method-kind", text: "stream" }) : "",
        ]),
      ]);
      if (!entry) return row;
      const key = "record:" + node.id + ":" + entry.id;
      const set = (name) => (value) =>
        commitNode(node.id, "Change " + method.name, (n) => {
          const item = n.records.find((candidate) => candidate.id === entry.id);
          if (!item) return;
          if (value === null) delete item[name];
          else item[name] = value;
        }, { coalesce: key + ":" + name });
      const inputs = [];
      if (method.fields.some((field) => field.name === "channel")) inputs.push(channelInput(node, "records", entry, method, hints));
      for (const field of method.fields) {
        if (field.name !== "channel") inputs.push(argInput(node, "records", entry, method, field, hints));
      }
      inputs.push(numberField({
        label: "samples", value: entry.samples, integer: true, min: 1, max: 10000,
        placeholder: String(runDefaults.samples), onValid: set("samples"),
      }));
      if (method.kind === "stream") {
        inputs.push(numberField({
          label: "rate", value: entry.sample_rate_hz, min: 0.1, max: 1000, unit: "Hz",
          placeholder: X.formatNumber(runDefaults.sample_rate_hz), onValid: set("sample_rate_hz"),
        }));
      }
      row.append(el("div", { class: "entry-fields" }, inputs));
      return row;
    }

    function recordBlock(node, method, hints, runDefaults) {
      const entries = node.records.filter((item) => item.method === method.name);
      const block = el("div", { class: "method-block", "data-method": method.name });
      if (!entries.length) block.append(recordRow(node, method, null, hints, runDefaults));
      for (const entry of entries) block.append(recordRow(node, method, entry, hints, runDefaults));
      if (method.fields.some((field) => field.name === "channel") && entries.length && freeChannel(node, "records", method, hints)) {
        const more = el("button", { class: "link-button", type: "button", text: "+ Another channel" });
        more.addEventListener("click", () =>
          commitNode(node.id, "Record " + method.name, (n) => {
            n.records.push({ id: nextItemId(n, "r"), method: method.name, args: { channel: freeChannel(n, "records", method, hints) } });
          }, { rerender: true })
        );
        block.append(more);
      }
      return block;
    }

    // ---- Problems ------------------------------------------------------------------

    function summaryText(report) {
      if (!report) return "";
      return X.formatNumber(report.points) + (report.points === 1 ? " point" : " points") + " · about " +
        formatDuration(report.estimated_seconds) +
        (report.exportable ? " · ready to export"
          : " · " + report.errors.length + (report.errors.length === 1 ? " problem stops" : " problems stop") + " the export");
    }

    function fillProblems() {
      if (summaryBox) summaryBox.textContent = summaryText(options.report());
      if (savedBox) {
        const doc = options.doc();
        savedBox.textContent = "Created by " + (doc.created_by || "someone") + " · version " + doc.version;
      }
      if (!problemsBox) return;
      const report = options.report();
      const items = [];
      if (report) {
        const mine = (item) => problemsFor === null || item.node_id === problemsFor;
        for (const item of report.errors.filter(mine)) items.push(["error", item]);
        for (const item of report.warnings.filter(mine)) items.push(["warning", item]);
      }
      problemsBox.replaceChildren(
        ...items.map(([tone, item]) => {
          const line = el("li", { class: "problem " + tone }, [item.message]);
          if (problemsFor === null && item.node_id) {
            line.classList.add("clickable");
            line.title = "Show it on the diagram";
            line.addEventListener("click", () => options.select([item.node_id], []));
          }
          return line;
        })
      );
      problemsBox.hidden = !items.length;
    }

    function liveHeader(node) {
      const device = options.live(node.device_id);
      if (!device) return [statusPill(options.networkKnown() ? "Not on the network" : "Checking…", options.networkKnown() ? "bad" : "warn")];
      const state = deviceState(device);
      const holder = holderText(device);
      return [statusPill(state.label, state.tone), holder ? el("span", { class: "holder-note", text: holder }) : "",
        el("a", { href: device.control_url, text: "Device control" })];
    }

    // ---- Panels --------------------------------------------------------------------

    function instrumentPanel(doc, node) {
      summaryBox = savedBox = null;
      const device = options.live(node.device_id);
      const hints = (device && device.hints) || {};
      const methods = options.methodsFor(node);
      liveFor = node;
      liveBox = el("div", { class: "inspector-live" }, liveHeader(node));
      problemsBox = el("ul", { class: "problems", hidden: true });
      problemsFor = node.id;
      const header = el("header", { class: "inspector-head" }, [
        el("span", { class: "inspector-kind", text: options.typeLabel(node) }),
        el("h2", { class: "mono", text: node.device_id }),
        liveBox,
      ]);
      const parts = [
        header,
        problemsBox,
        textField("Label", node.label, {
          maxlength: 60,
          placeholder: node.device_id,
          onChange: (value) => commitNode(node.id, "Rename", (n) => (n.label = value.trim()), { coalesce: "label:" + node.id }),
        }),
      ];
      if (!methods) {
        parts.push(el("p", { class: "field-hint", text: "This Control Center has no contract for " + node.device_type + ", so its settings can't be edited here." }));
      } else {
        const runDefaults = doc.run || { samples: 10, sample_rate_hz: 20 };
        const writes = methods.filter((method) => method.kind === "write");
        const commands = methods.filter((method) => method.kind === "command");
        const reads = methods.filter((method) => method.kind === "read" || method.kind === "stream");
        const used = (list, names) => new Set(list.filter((item) => names.includes(item.method)).map((item) => item.method)).size;
        const filter = methods.length > 8 ? el("input", { type: "search", class: "method-filter", placeholder: "Filter " + methods.length + " methods" }) : null;
        if (filter) parts.push(filter);
        const blocks = [];
        if (writes.length) {
          const children = writes.map((method) => methodBlock(node, method, hints));
          blocks.push(...children);
          parts.push(section("Settings", "Applied before recording. A sweep steps through values.", children, {
            count: used(node.settings, writes.map((m) => m.name)) + " of " + writes.length + " on",
          }));
        }
        if (commands.length) {
          const children = commands.map((method) => methodBlock(node, method, hints));
          blocks.push(...children);
          const on = used(node.settings, commands.map((m) => m.name));
          parts.push(section("Commands", "Run once when the script starts, after the settings above it.", children, {
            open: on > 0 || commands.length <= 4,
            count: on + " of " + commands.length + " on",
          }));
        }
        if (reads.length) {
          const children = reads.map((method) => recordBlock(node, method, hints, runDefaults));
          blocks.push(...children);
          parts.push(section("Record", "Read at every point and saved to the CSV.", children, {
            count: node.records.length + " recorded",
          }));
        }
        if (filter) {
          filter.addEventListener("input", () => {
            const query = filter.value.trim().toLowerCase();
            for (const block of blocks) block.hidden = Boolean(query) && !block.dataset.method.includes(query);
          });
        }
        const known = new Set(methods.map((method) => method.name));
        const stray = [...node.settings, ...node.records].filter((item) => !known.has(item.method));
        for (const item of stray) {
          const remove = el("button", { class: "link-button", type: "button", text: "Remove" });
          remove.addEventListener("click", () =>
            commitNode(node.id, "Remove " + item.method, (n) => {
              n.settings = n.settings.filter((candidate) => candidate.id !== item.id);
              n.records = n.records.filter((candidate) => candidate.id !== item.id);
            }, { rerender: true })
          );
          parts.push(el("p", { class: "problem error" }, [item.method + " is not in this device's contract. ", remove]));
        }
      }
      parts.push(removeButton("Remove from diagram", { nodes: [node.id], links: [] }));
      const panel = el("div", { class: "inspector-panel" }, parts);
      fillProblems();
      return panel;
    }

    function connectionsList(doc, node) {
      const links = doc.links.filter((link) => link.source === node.id || link.target === node.id);
      const name = (id) => {
        const other = doc.nodes.find((item) => item.id === id);
        return other ? other.label || other.device_id || options.kindLabel(other.kind) : id;
      };
      const items = links.map((link) => {
        const outgoing = link.source === node.id;
        const show = el("button", { class: "link-button", type: "button", text: (outgoing ? "→ " : "← ") + name(outgoing ? link.target : link.source) });
        show.addEventListener("click", () => options.select([], [link.id]));
        return el("li", {}, [show, el("span", { class: "link-type " + link.type, text: link.type === "beam" ? "beam" : "signal" })]);
      });
      const others = doc.nodes.filter((item) => item.id !== node.id && item.kind !== "note");
      const target = el("select", { "aria-label": "Connect to" }, others.map((item) => el("option", { value: item.id, text: item.label || item.device_id || options.kindLabel(item.kind) })));
      const type = el("select", { "aria-label": "Link type" }, [el("option", { value: "beam", text: "Beam" }), el("option", { value: "signal", text: "Signal" })]);
      const add = el("button", { class: "button small", type: "button", text: "Connect" });
      add.addEventListener("click", () => {
        const other = doc.nodes.find((item) => item.id === target.value);
        if (other) options.connect(node, other, type.value);
      });
      return [
        el("h3", { class: "inspector-subhead", text: "Connections" }),
        items.length ? el("ul", { class: "connections" }, items) : el("p", { class: "field-hint", text: "Not connected yet. Drag from its round handle to another block, or:" }),
        others.length ? el("div", { class: "connect-row" }, [el("span", { text: "Connect to" }), target, type, add]) : "",
      ];
    }

    function opticPanel(doc, node) {
      summaryBox = savedBox = null;
      problemsBox = null;
      liveBox = null;
      const parts = [el("header", { class: "inspector-head" }, [el("span", { class: "inspector-kind", text: node.kind === "note" ? "Note" : "Passive component" }), el("h2", { text: node.label || options.kindLabel(node.kind) })])];
      if (node.kind !== "note") {
        parts.push(selectField("Kind", node.kind, options.optics.map((optic) => [optic.kind, optic.label]), (kind) =>
          commitNode(node.id, "Change kind", (n) => (n.kind = kind), { rerender: true })));
        parts.push(textField("Label", node.label, {
          maxlength: 60, placeholder: options.kindLabel(node.kind),
          onChange: (value) => commitNode(node.id, "Rename", (n) => (n.label = value.trim()), { coalesce: "label:" + node.id }),
        }));
      }
      parts.push(textField("Notes", node.notes, {
        multiline: true, maxlength: 2000, placeholder: node.kind === "note" ? "What to remember about this setup" : "e.g. 50:50 at 780 nm, AR coated",
        onChange: (value) => commitNode(node.id, "Edit notes", (n) => (n.notes = value), { coalesce: "notes:" + node.id }),
      }));
      if (node.kind !== "note") parts.push(...connectionsList(doc, node));
      parts.push(removeButton("Remove from diagram", { nodes: [node.id], links: [] }));
      return el("div", { class: "inspector-panel" }, parts);
    }

    function linkPanel(doc, link) {
      summaryBox = savedBox = null;
      problemsBox = null;
      liveBox = null;
      const name = (id) => {
        const node = doc.nodes.find((item) => item.id === id);
        return node ? node.label || node.device_id || options.kindLabel(node.kind) : id;
      };
      const ends = el("p", { class: "link-ends" }, [
        (() => { const b = el("button", { class: "link-button", type: "button", text: name(link.source) }); b.addEventListener("click", () => options.select([link.source], [])); return b; })(),
        " → ",
        (() => { const b = el("button", { class: "link-button", type: "button", text: name(link.target) }); b.addEventListener("click", () => options.select([link.target], [])); return b; })(),
      ]);
      const reverse = el("button", { class: "button small", type: "button", text: "Reverse direction" });
      reverse.addEventListener("click", () =>
        options.commit("Reverse link", (d) => {
          const item = d.links.find((candidate) => candidate.id === link.id);
          if (item) [item.source, item.target] = [item.target, item.source];
        }, { source: "inspector", rerender: true })
      );
      return el("div", { class: "inspector-panel" }, [
        el("header", { class: "inspector-head" }, [el("span", { class: "inspector-kind", text: "Link" }), el("h2", { text: link.type === "beam" ? "Beam" : "Signal cable" })]),
        ends,
        selectField("Type", link.type, [["beam", "Beam (light)"], ["signal", "Signal (cable)"]], (type) =>
          options.commit("Change link type", (d) => {
            const item = d.links.find((candidate) => candidate.id === link.id);
            if (item) item.type = type;
          }, { source: "inspector", rerender: true })),
        textField("Label", link.label, {
          maxlength: 40, placeholder: link.type === "signal" ? "e.g. CH1 → RF" : "e.g. 0th order",
          onChange: (value) => options.commit("Label link", (d) => {
            const item = d.links.find((candidate) => candidate.id === link.id);
            if (item) item.label = value.trim();
          }, { source: "inspector", coalesce: "link-label:" + link.id }),
        }),
        el("div", { class: "inspector-actions" }, [reverse]),
        removeButton("Remove link", { nodes: [], links: [link.id] }),
      ]);
    }

    function experimentPanel(doc) {
      problemsBox = el("ul", { class: "problems", hidden: true });
      problemsFor = null;
      liveBox = null;
      const run = doc.run;
      const setRun = (name) => (value) =>
        options.commit("Change run options", (d) => {
          if (value === null) delete d.run[name];
          else d.run[name] = value;
        }, { source: "inspector", coalesce: "run:" + name });
      const output = textField("Output file", run.output_name, {
        maxlength: 64,
        onChange: (value) => {
          if (/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value)) setRun("output_name")(value);
        },
      });
      const outline = doc.nodes.map((node) => {
        const button = el("button", { class: "link-button", type: "button", text: node.label || node.device_id || options.kindLabel(node.kind) });
        button.addEventListener("click", () => options.select([node.id], []));
        return el("li", {}, [button, el("span", { class: "outline-kind", text: node.kind === "instrument" ? options.typeLabel(node) : options.kindLabel(node.kind) })]);
      });
      const panel = el("div", { class: "inspector-panel" }, [
        el("header", { class: "inspector-head" }, [el("span", { class: "inspector-kind", text: "Experiment" }), el("h2", { text: doc.name })]),
        textField("Name", doc.name, {
          maxlength: 120,
          onChange: (value) => {
            if (value.trim()) options.commit("Rename experiment", (d) => (d.name = value.trim()), { source: "inspector", coalesce: "name" });
          },
        }),
        textField("Description", doc.description, {
          multiline: true, maxlength: 4000, placeholder: "What it measures, and why",
          onChange: (value) => options.commit("Edit description", (d) => (d.description = value), { source: "inspector", coalesce: "description" }),
        }),
        (savedBox = el("p", { class: "field-hint" })),
        section("Run", "Used by Export script.", [
          el("div", { class: "entry-fields" }, [
            numberField({ label: "settle time", value: run.settle_seconds, min: 0, max: 3600, unit: "s", required: true, onValid: setRun("settle_seconds") }),
            numberField({ label: "samples per reading", value: run.samples, integer: true, min: 1, max: 10000, required: true, onValid: setRun("samples") }),
            numberField({ label: "stream rate", value: run.sample_rate_hz, min: 0.1, max: 1000, unit: "Hz", required: true, onValid: setRun("sample_rate_hz") }),
          ]),
          output,
        ]),
        (summaryBox = el("p", { class: "run-summary" })),
        problemsBox,
        section("Components", null, outline.length ? [el("ul", { class: "outline" }, outline)] : [el("p", { class: "field-hint", text: "Nothing on the diagram yet. Drag devices and optics from the left" + (window.LabNet.features.photo_agent ? ", or ask Photo-Agent to set it up." : ".") })], { open: outline.length <= 12 }),
      ]);
      fillProblems();
      return panel;
    }

    function formatDuration(seconds) {
      if (seconds < 90) return Math.round(seconds) + " s";
      if (seconds < 5400) return Math.round(seconds / 60) + " min";
      return (seconds / 3600).toFixed(1) + " h";
    }

    function removeButton(text, selectionToRemove) {
      const button = el("button", { class: "button danger small remove", type: "button", text });
      button.addEventListener("click", () => options.remove(selectionToRemove));
      return el("div", { class: "inspector-actions" }, [button]);
    }

    function manyPanel(doc) {
      summaryBox = savedBox = null;
      problemsBox = null;
      liveBox = null;
      const n = selection.nodes.length;
      const l = selection.links.length;
      return el("div", { class: "inspector-panel" }, [
        el("header", { class: "inspector-head" }, [el("span", { class: "inspector-kind", text: "Selection" }),
          el("h2", { text: [n ? n + (n === 1 ? " block" : " blocks") : "", l ? l + (l === 1 ? " link" : " links") : ""].filter(Boolean).join(" and ") })]),
        el("p", { class: "field-hint", text: "Drag any of them to move them together. Shift-click adds or removes one." }),
        removeButton("Remove all", selection),
      ]);
    }

    // ---- Rendering ----------------------------------------------------------------

    function render() {
      const doc = options.doc();
      let panel;
      if (selection.nodes.length + selection.links.length > 1) panel = manyPanel(doc);
      else if (selection.nodes.length === 1) {
        const node = doc.nodes.find((item) => item.id === selection.nodes[0]);
        panel = !node ? experimentPanel(doc) : node.kind === "instrument" ? instrumentPanel(doc, node) : opticPanel(doc, node);
      } else if (selection.links.length === 1) {
        const link = doc.links.find((item) => item.id === selection.links[0]);
        panel = link ? linkPanel(doc, link) : experimentPanel(doc);
      } else panel = experimentPanel(doc);
      host.replaceChildren(panel);
    }

    return {
      show(next, { force } = {}) {
        const same = JSON.stringify(next) === JSON.stringify(selection);
        selection = { nodes: [...next.nodes], links: [...next.links] };
        if (!same || force) {
          const scroll = same ? host.scrollTop : 0;
          render();
          host.scrollTop = scroll;
        }
      },
      refreshProblems: fillProblems,
      refreshLive() {
        if (liveBox && liveFor) liveBox.replaceChildren(...liveHeader(liveFor));
      },
    };
  }

  window.LabNet.createInspector = createInspector;
})();
