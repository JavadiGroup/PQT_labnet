// The script box: a small editor, Run/Stop, and an output console. Scripts
// run on the Control Center in the restricted script language (see
// labnet_control_center/scripting.py) using the page's device lease.
//
//   const box = PQT.createScriptBox(container, { session, device, example });
//   box.onScript(event); box.onState(event);   // fed from the session events

(function () {
  "use strict";

  const { el, store } = window.PQT;
  const FUNCTIONS = "print, sleep, mean, stdev, lease(\"device-id\"), range, len, min, max, round, abs, sum";

  function signature(method) {
    // Show the call the way a script writes it: required arguments, then optional ones.
    const parameters = method.parameters.map((text) => text.split(":")[0] + (text.includes("=") ? "=…" : ""));
    return "device." + method.name + "(" + parameters.join(", ") + ")";
  }

  function createScriptBox(container, options) {
    const { session, device } = options;
    const storageKey = "pqt-script-" + device.type;
    const editor = el("textarea", {
      class: "script-editor",
      spellcheck: "false",
      rows: "10",
      "aria-label": "Script",
    });
    editor.value = store.get("localStorage", storageKey, options.example);
    const run = el("button", { class: "button primary", type: "button", text: "Run" });
    const stop = el("button", { class: "button", type: "button", text: "Stop", disabled: true });
    const status = el("span", { class: "panel-status" });
    const output = el("pre", { class: "script-output", "aria-live": "polite" });

    container.append(
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [
          el("h2", { text: "Script" }),
          status,
          el("div", { class: "spacer" }),
          run,
          stop,
        ]),
        el("p", { class: "panel-hint" }, [
          "Python-style commands run on the Control Center with this page's lease. Use ",
          el("code", { text: "device" }),
          " for " + device.id + "; also available: " + FUNCTIONS + ".",
        ]),
        el(
          "p",
          { class: "panel-hint methods-hint" },
          device.methods.map((method) => el("code", { text: signature(method) }))
        ),
        editor,
        output,
      ])
    );

    function append(text, kind) {
      const line = el("span", { class: "line " + (kind || "") , text: text + "\n" });
      output.append(line);
      output.scrollTop = output.scrollHeight;
    }

    editor.addEventListener("input", () => store.set("localStorage", storageKey, editor.value));
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Tab") {
        // Indent instead of leaving the editor.
        event.preventDefault();
        editor.setRangeText("    ", editor.selectionStart, editor.selectionEnd, "end");
      } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        run.click();
      }
    });

    run.addEventListener("click", async () => {
      output.replaceChildren();
      run.disabled = true;
      try {
        await session.runScript(editor.value);
      } catch (error) {
        append(error.message, "error");
        run.disabled = false;
      }
    });
    stop.addEventListener("click", () => session.stopScript().catch((error) => append(error.message, "error")));

    return {
      onScript(event) {
        if (event.kind === "started") {
          output.replaceChildren();
          append("Running…", "muted");
        } else if (event.kind === "output") {
          append(event.text);
        } else if (event.kind === "error") {
          append(event.text, "error");
        } else if (event.kind === "done") {
          append(event.text, "muted");
        }
      },
      onState(event) {
        run.disabled = event.script_running;
        stop.disabled = !event.script_running;
        status.textContent = event.script_running ? "Running" : "";
      },
    };
  }

  window.PQT.createScriptBox = createScriptBox;
})();
