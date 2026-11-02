// Control panel for the Horiba spectrometer via LabSpec 6 (device type "horiba_spectrometer").
// Shipped by pqt_drivers; the Control Center serves it at /api/panels/horiba_spectrometer.js.
//
//   1. Spectrum: Live repeats one acquisition after another until Stop; Acquire takes
//      one. The plot zooms: drag across it, scroll, double-click to show everything.
//      Export CSV saves the spectrum on screen.
//   2. Settings: exposure, accumulations, center wavelength and grating, applied
//      together; Refresh reads them back with the acquisition time.
//   3. Script box.
//
// Each acquisition is two calls: acquire_spectrum (the counts), then
// get_spectrum_wavelengths (that spectrum's axis). Every call shares the page's
// lease, which the page keeps until Pause/Release, 5 minutes without a call, or the
// hold limit; Live stops when the lease goes.

(function () {
  "use strict";

  const { el, createPlot, createScriptBox } = window.LabNet;

  const EXAMPLE = [
    "# `device` is the spectrometer. Scripts share the page's lease.",
    "device.set_exposure(0.5)",
    "counts = device.acquire_spectrum()",
    "wavelengths = device.get_spectrum_wavelengths()",
    "peak = max(counts)",
    "for i in range(len(counts)):",
    "    if counts[i] == peak:",
    '        print("peak", round(wavelengths[i], 3), "nm,", peak, "counts")',
    "",
  ].join("\n");

  function limitsOf(device, key) {
    const limits = device.hints && device.hints.limits && device.hints.limits[key];
    return Array.isArray(limits) && limits.length === 2 ? limits : null;
  }

  function choicesOf(device, key) {
    const choices = device.hints && device.hints.choices && device.hints.choices[key];
    return Array.isArray(choices) ? choices.filter((item) => typeof item === "string") : [];
  }

  function numberField(label, id, options) {
    const input = el("input", {
      id,
      type: "number",
      min: options.min === undefined ? null : String(options.min),
      max: options.max === undefined ? null : String(options.max),
      step: String(options.step),
      placeholder: options.placeholder || null,
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

  function number(value, digits, unit) {
    return typeof value === "number" && isFinite(value) ? value.toFixed(digits) + (unit ? " " + unit : "") : "—";
  }

  function clock() {
    return new Date().toLocaleTimeString([], { hour12: false });
  }

  function mount(container, context) {
    const { device, session } = context;
    let state = { state: "idle", leased: false, script_running: false, calls_held: false };
    let live = false;
    let acquiring = false;
    let applying = false;
    let acquisitionSeconds = null; // from read_acquisition_time, or the settings on screen
    const centerRange = limitsOf(device, "set_center_wavelength.value");
    const gratings = choicesOf(device, "set_grating.grating");

    // ---- 1. Spectrum -------------------------------------------------------------
    const liveButton = el("button", { class: "button primary play", type: "button", text: "▶  Live" });
    const once = el("button", { class: "button", type: "button", text: "Acquire" });
    const stateLine = el("span", { class: "panel-status" });
    const peak = el("span", { class: "readout", text: "—" });
    const resetZoom = el("button", { class: "button", type: "button", text: "Show all" });
    const exportButton = el("button", { class: "button", type: "button", text: "Export CSV" });
    const release = el("button", { class: "button", type: "button", text: "Release spectrometer" });
    const canvas = el("canvas", { class: "chart", "aria-label": "Spectrum", style: "height: 420px; cursor: crosshair;" });

    // ---- 2. Settings ---------------------------------------------------------------
    const exposure = numberField("Exposure", "horiba-exposure", {
      min: 0, step: 0.01, placeholder: "s", help: "s per accumulation.",
    });
    const accumulations = numberField("Accumulations", "horiba-accumulations", {
      min: 1, step: 1, placeholder: "1", help: "spectra averaged into one.",
    });
    const center = numberField("Center wavelength", "horiba-center", {
      min: centerRange ? centerRange[0] : undefined,
      max: centerRange ? centerRange[1] : undefined,
      step: 0.01,
      placeholder: "nm",
      help: "nm" + (centerRange ? ", " + centerRange[0] + "–" + centerRange[1] : "") + ". Moves the grating.",
    });
    const grating = el("select", {
      id: "horiba-grating",
      style: "padding: 8px 10px; border: 1px solid var(--card-line); border-radius: 8px; background: #ffffff; font: inherit;",
    }, [el("option", { value: "", text: "(unchanged)" })].concat(gratings.map((name) => el("option", { value: name, text: name }))));
    const apply = el("button", { class: "button primary", type: "button", text: "Apply" });
    const refresh = el("button", { class: "button", type: "button", text: "Refresh" });
    const settingsNote = el("p", { class: "panel-hint" });

    container.append(
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [liveButton, once, stateLine, el("div", { class: "spacer" }), peak]),
        el("div", { class: "panel-toolbar" }, [
          el("span", { class: "panel-status", text: "Drag to zoom, scroll to zoom the wavelength, double-click to show all." }),
          el("div", { class: "spacer" }),
          release,
          resetZoom,
          exportButton,
        ]),
        canvas,
      ]),
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [el("h2", { text: "Settings" }), el("div", { class: "spacer" }), refresh]),
        el("div", { class: "fields" }, [
          exposure.element,
          accumulations.element,
          center.element,
          el("label", { class: "field", for: "horiba-grating" }, [
            el("span", { class: "field-label", text: "Grating" }),
            grating,
            el("span", { class: "field-help", text: gratings.length ? "as LabSpec names it." : "LabSpec reported no gratings." }),
          ]),
        ]),
        el("div", { class: "panel-toolbar" }, [apply]),
        settingsNote,
      ])
    );
    const plot = createPlot(canvas, {
      xLabel: "Wavelength (nm)",
      yLabel: "Counts",
      emptyText: "Press Live or Acquire to take a spectrum.",
    });
    const scriptBox = createScriptBox(container, { session, device, example: EXAMPLE });

    // ---- behaviour ----------------------------------------------------------

    function say(text, isError) {
      settingsNote.textContent = text;
      settingsNote.style.color = isError ? "var(--bad)" : "";
    }

    function status(text, tone) {
      stateLine.textContent = text;
      stateLine.className = "panel-status" + (tone ? " " + tone : "");
    }

    function render() {
      liveButton.textContent = live ? "❚❚  Stop" : "▶  Live";
      liveButton.classList.toggle("pausing", live);
      once.disabled = live || acquiring;
      apply.disabled = applying;
      refresh.disabled = applying;
      release.hidden = !(state.calls_held && !live && !acquiring);
    }

    // Long enough for the acquisition (with LabSpec's overhead), at most 10 minutes.
    function acquireTimeout() {
      let seconds = acquisitionSeconds;
      if (!(seconds > 0)) {
        const exposureS = Number(exposure.input.value) || 1;
        const count = Number(accumulations.input.value) || 1;
        seconds = exposureS * count;
      }
      return Math.min(600, Math.ceil(seconds * 1.5 + 30));
    }

    async function acquireOne() {
      acquiring = true;
      render();
      const started = performance.now();
      status((live ? "Live · " : "") + "acquiring…", live ? "live" : "");
      try {
        const counts = await session.call("acquire_spectrum", {}, { timeout_seconds: acquireTimeout() });
        const wavelengths = await session.call("get_spectrum_wavelengths");
        if (!Array.isArray(counts) || !Array.isArray(wavelengths) || counts.length !== wavelengths.length) {
          throw new Error("The spectrum and its wavelengths don't match; try again.");
        }
        plot.setData(wavelengths, counts);
        let best = -1;
        counts.forEach((value, index) => {
          if (value !== null && (best < 0 || value > counts[best])) best = index;
        });
        peak.textContent = best < 0 ? "—" : "peak " + number(wavelengths[best], 3, "nm") + " · " + Math.round(counts[best]);
        const took = ((performance.now() - started) / 1000).toFixed(1);
        status((live ? "Live · " : "") + "spectrum at " + clock() + " (" + took + " s)", live ? "live" : "");
      } finally {
        acquiring = false;
        render();
      }
    }

    async function runLive() {
      live = true;
      render();
      try {
        while (live) {
          await acquireOne();
        }
        status("Stopped at " + clock() + ".");
      } catch (error) {
        live = false;
        status(error.message, "error");
      } finally {
        live = false;
        render();
      }
    }

    async function readSettings() {
      const exposureS = await session.call("read_exposure");
      const count = await session.call("read_accumulations");
      const centerNm = await session.call("read_center_wavelength");
      const current = await session.call("get_grating");
      acquisitionSeconds = await session.call("read_acquisition_time");
      if (typeof exposureS === "number") exposure.input.value = exposureS;
      if (typeof count === "number") accumulations.input.value = count;
      if (typeof centerNm === "number") center.input.value = centerNm;
      if (gratings.includes(current)) grating.value = current;
      return { current };
    }

    function settingsText(current) {
      return "Exposure " + exposure.input.value + " s × " + accumulations.input.value + ", center " +
        center.input.value + " nm, grating " + current + "; one spectrum takes " + number(acquisitionSeconds, 1, "s") + ".";
    }

    async function settingsCommand(work) {
      if (applying) return;
      applying = true;
      render();
      try {
        await work();
      } catch (error) {
        say(error.message, true);
      } finally {
        applying = false;
        render();
      }
    }

    liveButton.addEventListener("click", () => {
      if (live) {
        live = false; // the loop ends after the acquisition in progress
        status("Stopping after this spectrum…");
        render();
      } else if (!acquiring) {
        runLive();
      }
    });

    once.addEventListener("click", async () => {
      if (acquiring) return;
      try {
        await acquireOne();
      } catch (error) {
        status(error.message, "error");
      }
    });

    apply.addEventListener("click", () => settingsCommand(async () => {
      const changes = [];
      const exposureS = exposure.input.value === "" ? null : Number(exposure.input.value);
      const count = accumulations.input.value === "" ? null : Number(accumulations.input.value);
      const centerNm = center.input.value === "" ? null : Number(center.input.value);
      if (exposureS !== null && !(exposureS > 0)) throw new Error("Exposure must be above 0 s.");
      if (count !== null && !(Number.isInteger(count) && count >= 1)) throw new Error("Accumulations must be a whole number, at least 1.");
      if (centerNm !== null && (!isFinite(centerNm) || (centerRange && (centerNm < centerRange[0] || centerNm > centerRange[1])))) {
        throw new Error("Center wavelength must be " + (centerRange ? centerRange[0] + "–" + centerRange[1] + " nm." : "a number of nm."));
      }
      say("Applying…" + (live ? " (between spectra)" : ""));
      if (exposureS !== null) changes.push(["set_exposure", { value: exposureS }]);
      if (count !== null) changes.push(["set_accumulations", { value: count }]);
      if (centerNm !== null) changes.push(["set_center_wavelength", { value: centerNm }, { timeout_seconds: 120 }]);
      if (grating.value) changes.push(["set_grating", { grating: grating.value }, { timeout_seconds: 120 }]);
      for (const [method, args, options] of changes) {
        await session.call(method, args, options);
      }
      const { current } = await readSettings();
      say("Applied at " + clock() + ". " + settingsText(current));
    }));

    refresh.addEventListener("click", () => settingsCommand(async () => {
      const { current } = await readSettings();
      say("Read at " + clock() + ". " + settingsText(current));
    }));

    resetZoom.addEventListener("click", () => plot.resetZoom());

    exportButton.addEventListener("click", () => {
      const { xs, ys } = plot.data();
      if (xs.length === 0) return;
      const rows = ["wavelength_nm,counts"].concat(xs.map((x, index) => x + "," + (ys[index] === null ? "" : ys[index])));
      const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..*/, "").replace("T", "-");
      const link = el("a", {
        href: URL.createObjectURL(new Blob([rows.join("\n") + "\n"], { type: "text/csv" })),
        download: device.id + "_spectrum_" + stamp + ".csv",
      });
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
    });

    release.addEventListener("click", async () => {
      try {
        await session.pause();
      } catch (error) {
        say(error.message, true);
      }
    });

    function onState(event) {
      const wasHeld = state.calls_held;
      state = event;
      if (live && wasHeld && !event.calls_held) {
        // Released (Pause on another page, the hold limit): Live ends with it.
        live = false;
        status(event.message || "The spectrometer was released.", "error");
      } else if (!live && !acquiring && event.message) {
        status(event.message, event.state === "error" ? "error" : "");
      }
      render();
      scriptBox.onState(event);
    }

    say("Refresh reads the settings (and holds the spectrometer for this page until Release, " +
      "or 5 minutes without a command).");
    status(state.leased ? "Spectrometer held by this page" : "Spectrometer free for others");
    render();

    return {
      snapshot: onState,
      state: onState,
      script: (event) => scriptBox.onScript(event),
    };
  }

  window.LabNet.panels = window.LabNet.panels || {};
  window.LabNet.panels.horiba_spectrometer = mount;
})();
