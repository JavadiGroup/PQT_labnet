// Network page: one card per Lab PC with its devices.

(function () {
  "use strict";

  const { el, ago, deviceItem, plural, statusPill, watchNetwork } = window.PQT;
  const list = document.getElementById("lab-pcs");
  const summary = document.getElementById("summary");

  function labPcCard(pc) {
    const meta = el("span", { class: "lab-pc-meta" }, [
      "IP ",
      el("code", { text: pc.address }),
      " · gRPC :" + pc.port + " · " + (pc.secure ? "TLS" : "plaintext"),
    ]);
    const head = el("header", { class: "lab-pc-head" }, [
      el("h2", { text: pc.id }),
      pc.online ? statusPill("Online", "ok") : statusPill("Offline", "bad"),
      meta,
      el("span", {
        class: "lab-pc-count",
        text: pc.online ? plural(pc.devices.length, "device") : "last seen " + ago(pc.last_seen),
      }),
    ]);
    const body = pc.devices.length
      ? el("ul", { class: "devices" }, pc.devices.map((device) => deviceItem(device)))
      : el("p", { class: "empty", text: "This Lab PC has not registered any devices." });
    return el("section", { class: "lab-pc" + (pc.online ? "" : " offline") }, [head, body]);
  }

  watchNetwork((snapshot) => {
    const counts = snapshot.summary;
    summary.textContent =
      counts.online + " of " + plural(counts.lab_pcs, "Lab PC") + " online · " +
      plural(counts.devices, "device") + " · " + counts.available + " available · " +
      counts.leased + " leased";

    if (snapshot.lab_pcs.length === 0) {
      list.replaceChildren(
        el("section", { class: "lab-pc" }, [
          el("p", {
            class: "empty",
            text: snapshot.central.ok
              ? "No Lab PCs have registered with Central yet. Start one with labnet-lab-pc."
              : "Waiting for Central…",
          }),
        ])
      );
      return;
    }
    list.replaceChildren(...snapshot.lab_pcs.map(labPcCard));
  });
})();
