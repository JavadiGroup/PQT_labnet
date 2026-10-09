// Fallback control panel for device types without their own panel file:
// the script box only, with an example built from the device's methods.

(function () {
  "use strict";

  const { el, createScriptBox } = window.LabNet;

  function example(device) {
    const read = device.methods.find((method) => method.kind === "read");
    const write = device.methods.find((method) => method.kind === "write");
    const lines = ["# `device` is " + device.id + ". Scripts share the page's lease."];
    if (write) lines.push("device." + write.name + "(1.0)");
    if (read) lines.push("print(device." + read.name + "())");
    if (!read && !write) lines.push("print(\"Connected to\", \"" + device.id + "\")");
    return lines.join("\n") + "\n";
  }

  function mount(container, context) {
    const { device, session } = context;
    container.append(
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [el("h2", { text: device.type_label })]),
        el("p", {
          class: "panel-hint",
          text:
            "This device type has no dedicated control panel yet. Add one as " +
            "static/js/panels/" + device.type + ".js. Until then, control it with a script.",
        }),
      ])
    );
    const scriptBox = createScriptBox(container, { session, device, example: example(device) });
    return {
      snapshot: (event) => scriptBox.onState(event),
      state: (event) => scriptBox.onState(event),
      script: (event) => scriptBox.onScript(event),
    };
  }

  window.LabNet.panels = window.LabNet.panels || {};
  window.LabNet.panels.generic = mount;
})();
