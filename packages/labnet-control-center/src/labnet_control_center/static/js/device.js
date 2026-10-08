// Device control page (/<device-id>): a live header, then the control panel
// for the device's type (static/js/panels/<type>.js, or panels/generic.js).

(function () {
  "use strict";

  const { el, allDevices, deviceState, holderText, statusPill, watchNetwork, connectSession } = window.PQT;
  const deviceId = decodeURIComponent(window.location.pathname.replace(/^\/+|\/+$/g, ""));
  const title = document.getElementById("device-title");
  const subtitle = document.getElementById("summary");
  const control = document.getElementById("control");
  const banner = document.getElementById("banner");
  let panelRequested = false;

  title.textContent = deviceId;
  document.title = deviceId + " · PQT Lab Control Center";

  function mountPanel(device) {
    panelRequested = true;
    const script = el("script", { src: "/static/js/panels/" + encodeURIComponent(device.panel) + ".js" });
    script.onload = () => {
      // Handlers are filled in once the panel exists; events only arrive
      // after this function returns, so none are missed.
      const handlers = {
        connection(ok) {
          if (!ok) {
            banner.hidden = false;
            banner.textContent = "Lost the live connection to the Control Center. Reconnecting…";
          } else if (banner.textContent.startsWith("Lost the live connection")) {
            banner.hidden = true;
          }
        },
      };
      const session = connectSession(deviceId, handlers);
      control.classList.remove("placeholder");
      control.replaceChildren();
      Object.assign(handlers, window.PQT.panels[device.panel](control, { device, session }));
    };
    script.onerror = () => {
      control.textContent = "Could not load the control panel for " + device.type_label + ".";
    };
    document.body.append(script);
  }

  watchNetwork((snapshot) => {
    const device = allDevices(snapshot).find((item) => item.id === deviceId);
    if (!device) {
      subtitle.replaceChildren(
        snapshot.central.ok
          ? "No device with this id is registered with Central."
          : "Waiting for Central…"
      );
      return;
    }
    const state = deviceState(device);
    const holder = holderText(device);
    subtitle.replaceChildren(
      statusPill(state.label, state.tone),
      ...(holder ? [el("span", { class: "subtle-sep", text: "·" }), holder] : []),
      el("span", { class: "subtle-sep", text: "·" }),
      device.type_label,
      el("span", { class: "subtle-sep", text: "·" }),
      "on " + device.lab_pc.id
    );
    if (!panelRequested && device.known_contract) mountPanel(device);
  });
})();
