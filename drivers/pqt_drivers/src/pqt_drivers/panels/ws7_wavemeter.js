// Control panel for the HighFinesse WS7 wavemeter (device type "ws7_wavemeter").
// Shipped by pqt_drivers; the Control Center serves it at /api/panels/ws7_wavemeter.js.
//
//   1. Live wavelength: Play/Pause, time window, latest value, Export CSV. A reading
//      the WS7 can't make (no light, over- or underexposed) is a gap and "no signal".
//   2. Wavemeter: exposure, auto exposure, measurement start/stop, and Refresh for
//      the frequency, temperature and measurement state.
//   3. Script box.
//
// Every button except Play calls one method (session.call). The page then holds the
// WS7 until Pause, or 5 minutes without a command, so it is free for others again.

(function () {
  "use strict";

  const { el, createChart, createScriptBox } = window.LabNet;

  const WINDOWS = [
    ["10 s", 10],
    ["30 s", 30],
    ["1 min", 60],
    ["5 min", 300],
    ["All", null],
  ];

  const EXAMPLE = [
    "# `device` is the WS7. Scripts share the page's lease.",
    "readings = []",
    "for i in range(20):",
    "    readings.append(device.read_wavelength())",
    "    sleep(0.1)",
    'print("mean", round(mean(readings), 6), "nm")',
    'print("stdev", round(stdev(readings) * 1e6, 2), "fm")',
    "",
  ].join("\n");

  function limitsOf(device, key) {
    const limits = device.hints && device.hints.limits && device.hints.limits[key];
    return Array.isArray(limits) && limits.length === 2 ? limits : null;
  }

  function numberField(label, id, options) {
    const input = el("input", {
      id,
      type: "number",
      min: options.min === undefined ? null : String(options.min),
      max: options.max === undefined ? null : String(options.max),
      step: String(options.step),
      value: String(options.value),
    });
    return {
      input,
      element: el("label", { class: "field", for: id }, [
        el("span", { class: "field-label", text: label }),
        input,
        el("span", { class: "field-help", text: options.help }),
      ]),
    };
  }

  function reading(label) {
    const value = el("span", { class: "readout-value", text: "—", style: "font-family: var(--mono); font-size: 15px;" });
    return {
      element: el("div", { class: "field" }, [el("span", { class: "field-label", text: label }), value]),
      set(text) {
        value.textContent = text;
      },
    };
  }

  function number(value, digits, unit) {
    return typeof value === "number" && isFinite(value) ? value.toFixed(digits) + (unit ? " " + unit : "") : "—";
  }

  function mount(container, context) {
    const { device, session } = context;
    let state = { state: "idle", leased: false, script_running: false, calls_held: false };
    let windowSeconds = 30;
    let heldTimer = null;
    let busy = false;
    const exposureLimits = limitsOf(device, "set_exposure.value");

    // ---- 1. Live wavelength --------------------------------------------------
    const playButton = el("button", { class: "button primary play", type: "button" });
    const stateLine = el("span", { class: "panel-status" });
    const readout = el("span", { class: "readout", text: "—" });
    const exportButton = el("button", { class: "button", type: "button", text: "Export CSV" });
    const windowButtons = WINDOWS.map(([label, seconds]) => {
      const button = el("button", {
        class: "segment" + (seconds === windowSeconds ? " active" : ""),
        type: "button",
        text: label,
      });
      button.addEventListener("click", () => {
        windowSeconds = seconds;
        chart.setWindow(seconds);
        windowButtons.forEach((item) => item.classList.toggle("active", item === button));
      });
      return button;
    });
    const canvas = el("canvas", { class: "chart", "aria-label": "Live wavelength" });
    const rate = numberField("Sample rate", "ws7-rate", {
      min: 0.1, max: 20, step: 0.1, value: 2,
      help: "readings per second while playing (0.1–20).",
    });

    // ---- 2. Wavemeter --------------------------------------------------------
    const exposure = numberField("Exposure", "ws7-exposure", {
      min: exposureLimits ? exposureLimits[0] : 1,
      max: exposureLimits ? exposureLimits[1] : undefined,
      step: 1, value: 10,
      help: "ms" + (exposureLimits ? " (" + exposureLimits[0] + "–" + exposureLimits[1] + ")" : "") +
        ". Turn auto exposure off first: the WS7 refuses a fixed exposure while it is on.",
    });
    const setExposure = el("button", { class: "button", type: "button", text: "Set exposure" });
    const autoOn = el("button", { class: "button", type: "button", text: "Auto exposure on" });
    const autoOff = el("button", { class: "button", type: "button", text: "Auto exposure off" });
    const start = el("button", { class: "button", type: "button", text: "Start measuring" });
    const stop = el("button", { class: "button", type: "button", text: "Stop measuring" });
    const refresh = el("button", { class: "button", type: "button", text: "Refresh" });
    const release = el("button", { class: "button", type: "button", text: "Release WS7" });
    const note = el("p", { class: "panel-hint" });
    const readings = {
      wavelength: reading("Wavelength"),
      frequency: reading("Frequency"),
      temperature: reading("Temperature"),
      exposure: reading("Exposure"),
      auto: reading("Auto exposure"),
      measuring: reading("Measurement"),
    };
    const commandButtons = [setExposure, autoOn, autoOff, start, stop, refresh];

    container.append(
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [playButton, stateLine, el("div", { class: "spacer" }), readout]),
        el("div", { class: "panel-toolbar" }, [
          el("div", { class: "segments", role: "group", "aria-label": "Time window" }, windowButtons),
          el("div", { class: "spacer" }),
          exportButton,
        ]),
        canvas,
        el("div", { class: "fields" }, [rate.element]),
      ]),
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [
          el("h2", { text: "Wavemeter" }),
          el("div", { class: "spacer" }),
          release,
          refresh,
        ]),
        el("div", { class: "fields" }, Object.values(readings).map((item) => item.element)),
        el("div", { class: "fields" }, [exposure.element]),
        el("div", { class: "panel-toolbar" }, [setExposure, autoOn, autoOff, el("div", { class: "spacer" }), start, stop]),
        note,
      ])
    );
    const chart = createChart(canvas, { unit: "nm" });
    chart.setWindow(windowSeconds);
    const scriptBox = createScriptBox(container, { session, device, example: EXAMPLE });

    // ---- behaviour ----------------------------------------------------------

    function say(text, isError) {
      note.textContent = text;
      note.style.color = isError ? "var(--bad)" : "";
    }

    function latestText() {
      const value = chart.latest();
      if (value === null) return chart.points().length ? "no signal" : "—";
      return value.toFixed(6) + " nm";
    }

    async function command(button, work) {
      if (busy) return;
      busy = true;
      commandButtons.forEach((item) => (item.disabled = true));
      const label = button.textContent;
      button.textContent = label + "…";
      try {
        await work();
      } catch (error) {
        say(error.message, true);
      } finally {
        button.textContent = label;
        busy = false;
        commandButtons.forEach((item) => (item.disabled = false));
      }
    }

    async function readAll() {
      const wavelength = await session.call("read_wavelength");
      readings.wavelength.set(number(wavelength, 6, "nm"));
      readings.frequency.set(number(await session.call("read_frequency"), 6, "THz"));
      readings.temperature.set(number(await session.call("read_temperature"), 2, "°C"));
      const ms = await session.call("read_exposure");
      readings.exposure.set(number(ms, 0, "ms"));
      if (typeof ms === "number" && isFinite(ms)) exposure.input.value = ms;
      const auto = await session.call("get_auto_exposure");
      readings.auto.set(auto ? "on" : "off");
      readings.measuring.set(String(await session.call("get_measurement_state")));
    }

    function heldFor() {
      if (!state.started_at) return "";
      const seconds = Math.max(0, Math.round(Date.now() / 1000 - state.started_at));
      return " · " + Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0") +
        " of " + Math.round(state.max_hold_seconds / 60) + " min";
    }

    function render() {
      const streaming = state.state === "streaming";
      const starting = state.state === "starting";
      playButton.textContent = streaming ? "❚❚  Pause" : starting ? "Starting…" : "▶  Play";
      playButton.classList.toggle("pausing", streaming);
      playButton.disabled = starting;
      rate.input.disabled = streaming || starting;
      release.hidden = !(state.calls_held && !streaming);
      if (streaming) {
        const play = state.play && state.play.stream;
        stateLine.textContent = "Live" + (play ? " · " + play.sample_rate_hz + " Hz" : "") + heldFor();
        stateLine.className = "panel-status live";
      } else {
        stateLine.textContent = state.message ||
          (state.leased ? "WS7 held by this page" : "Paused · WS7 free for others");
        stateLine.className = "panel-status" + (state.state === "error" ? " error" : "");
      }
    }

    function onState(event) {
      state = event;
      if (event.state === "streaming" && event.play && event.play.stream) rate.input.value = event.play.stream.sample_rate_hz;
      window.clearInterval(heldTimer);
      if (event.state === "streaming") heldTimer = window.setInterval(render, 1000);
      render();
      scriptBox.onState(event);
    }

    playButton.addEventListener("click", async () => {
      if (state.state === "streaming") {
        try {
          await session.pause();
        } catch (error) {
          stateLine.textContent = error.message;
        }
        return;
      }
      const hz = Number(rate.input.value);
      if (!(hz >= 0.1 && hz <= 20)) {
        stateLine.textContent = "The sample rate must be 0.1–20 readings per second.";
        stateLine.className = "panel-status error";
        return;
      }
      playButton.disabled = true;
      try {
        await session.play({ settings: [], stream: { method: "stream_wavelength", sample_rate_hz: hz, channel: null } });
      } catch (error) {
        stateLine.textContent = error.message;
        stateLine.className = "panel-status error";
        playButton.disabled = false;
      }
    });

    refresh.addEventListener("click", () => command(refresh, async () => {
      await readAll();
      say("Read at " + new Date().toLocaleTimeString([], { hour12: false }) + ".");
    }));

    setExposure.addEventListener("click", () => command(setExposure, async () => {
      const ms = Number(exposure.input.value);
      if (!Number.isInteger(ms) || (exposureLimits && (ms < exposureLimits[0] || ms > exposureLimits[1]))) {
        throw new Error("Exposure must be a whole number of ms" +
          (exposureLimits ? " from " + exposureLimits[0] + " to " + exposureLimits[1] : "") + ".");
      }
      const value = await session.call("set_exposure", { value: ms });
      readings.exposure.set(number(value, 0, "ms"));
      say("Exposure set to " + number(value, 0, "ms") + ".");
    }));

    for (const [button, enabled] of [[autoOn, true], [autoOff, false]]) {
      button.addEventListener("click", () => command(button, async () => {
        await session.call("set_auto_exposure", { enabled });
        readings.auto.set(enabled ? "on" : "off");
        say("Auto exposure " + (enabled ? "on." : "off."));
      }));
    }

    for (const [button, method] of [[start, "start_measurement"], [stop, "stop_measurement"]]) {
      button.addEventListener("click", () => command(button, async () => {
        await session.call(method);
        const measuring = String(await session.call("get_measurement_state"));
        readings.measuring.set(measuring);
        say("Measurement: " + measuring + ".");
      }));
    }

    release.addEventListener("click", async () => {
      try {
        await session.pause();
      } catch (error) {
        say(error.message, true);
      }
    });

    exportButton.addEventListener("click", () => {
      const points = chart.points();
      if (points.length === 0) return;
      const rows = ["time_utc,unix_seconds,wavelength_nm"].concat(
        points.map(([time, value]) =>
          new Date(time * 1000).toISOString() + "," + time.toFixed(6) + "," + (value === null ? "" : value))
      );
      const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..*/, "").replace("T", "-");
      const link = el("a", {
        href: URL.createObjectURL(new Blob([rows.join("\n") + "\n"], { type: "text/csv" })),
        download: device.id + "_" + stamp + ".csv",
      });
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
    });

    say("Refresh reads the WS7 (and holds it for this page until Pause, or 5 minutes without a command).");
    render();

    return {
      snapshot(event) {
        chart.setPoints(event.samples);
        readout.textContent = latestText();
        onState(event);
      },
      state: onState,
      reset() {
        chart.clear(); // a new Play starts a fresh graph
        readout.textContent = "—";
      },
      samples(event) {
        chart.addPoints(event.points);
        readout.textContent = latestText();
      },
      script: (event) => scriptBox.onScript(event),
    };
  }

  window.LabNet.panels = window.LabNet.panels || {};
  window.LabNet.panels.ws7_wavemeter = mount;
})();
