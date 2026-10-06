// Devices page: every device on the network in one list, whichever Lab PC
// it is attached to.

(function () {
  "use strict";

  const { el, allDevices, deviceItem, plural, watchNetwork } = window.PQT;
  const list = document.getElementById("devices");
  const count = document.getElementById("device-count");
  const summary = document.getElementById("summary");

  watchNetwork((snapshot) => {
    const devices = allDevices(snapshot).sort((a, b) => a.id.localeCompare(b.id));
    const counts = snapshot.summary;
    const unavailable = counts.devices - counts.available - counts.leased;
    summary.textContent =
      plural(counts.devices, "device") + " · " + counts.available + " available · " +
      counts.leased + " leased · " + unavailable + " unavailable";
    count.textContent = "across " + plural(counts.lab_pcs, "Lab PC");

    if (devices.length === 0) {
      list.replaceChildren(
        el("li", {
          class: "empty",
          text: snapshot.central.ok
            ? "No devices are registered with Central yet."
            : "Waiting for Central…",
        })
      );
      return;
    }
    list.replaceChildren(...devices.map((device) => deviceItem(device, { labPc: true })));
  });
})();
