// Control panel for a Sacher laser's tuning motor (device type "sacher_laser").
// Shipped by pqt_drivers; the Control Center serves it at /api/panels/sacher_laser.js.
//
//   1. Wavelength: live and commanded wavelength, state and velocity (Refresh), and
//      a live graph of the motor's wavelength (Play/Pause).
//   2. Move: a target inside the Lab PC's limits (the site's 757-782 nm for sacher-vis),
//      and the velocity. A move blocks until the motor stops; there is no Stop.
//   3. Rebase: tell the laser what the wavemeter reads. It asks the person to check
//      the wavemeter is reading this laser, warns again if the value is more than
//      2 nm from the live wavelength, and refuses values outside the limits (the Lab
//      PC refuses them too).
//   4. Script box.
//
// "Live" wavelength is the motor position through the calibration, not a
// measurement: the wavemeter is the measurement, and Rebase ties the two together.
// Every button except Play calls one method (session.call). The page then holds
// the laser until Pause, or 5 minutes without a command.

(function () {
  "use strict";

  const { el, createChart, createScriptBox } = window.LabNet;

  // Warn before a rebase that moves the axis further than this (nm).
  const REBASE_WARNING_NM = 2;

  const EXAMPLE = [
    "# `device` is this laser. Scripts share the page's lease.",
    "# Moves block until the motor stops.",
    "for target in [765.0, 766.0, 767.0]:",
    "    device.move_to_wavelength_nm(target)",
    '    print(target, "->", round(device.read_live_wavelength(), 4), "nm")',
    "",
    "# Never rebase from a script: only a person who has checked the wavemeter may.",
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
      value: options.value === undefined ? null : String(options.value),
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

  function reading(label) {
    const value = el("span", { text: "—", style: "font-family: var(--mono); font-size: 15px;" });
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

  function rangeText(range) {
    return range ? range[0] + "–" + range[1] + " nm" : "the laser's range";
  }

  function mount(container, context) {
    const { device, session } = context;
    let state = { state: "idle", leased: false, script_running: false, calls_held: false };
    let busy = false;
    let live = null; // the last live wavelength read, nm
    let velocity = null; // nm/s
    const moveRange = limitsOf(device, "move_to_wavelength_nm.wavelength_nm");
    const rebaseRange = limitsOf(device, "rebase_position_axis.measured_wavelength_nm");
    const velocityRange = limitsOf(device, "set_velocity.value");

    // ---- 1. Wavelength ---------------------------------------------------------
    const playButton = el("button", { class: "button primary play", type: "button" });
    const stateLine = el("span", { class: "panel-status" });
    const readout = el("span", { class: "readout", text: "—" });
    const canvas = el("canvas", { class: "chart", "aria-label": "Live wavelength" });
    const refresh = el("button", { class: "button", type: "button", text: "Refresh" });
    const release = el("button", { class: "button", type: "button", text: "Release laser" });
    const readings = {
      live: reading("Live wavelength"),
      commanded: reading("Commanded wavelength"),
      state: reading("State"),
      velocity: reading("Velocity"),
    };

    // ---- 2. Move -----------------------------------------------------------------
    const target = numberField("Target wavelength", "sacher-target", {
      min: moveRange ? moveRange[0] : undefined,
      max: moveRange ? moveRange[1] : undefined,
      step: 0.001,
      placeholder: moveRange ? String((moveRange[0] + moveRange[1]) / 2) : "nm",
      help: "nm, " + rangeText(moveRange) + ". The move blocks until the motor stops.",
    });
    const move = el("button", { class: "button primary", type: "button", text: "Move" });
    const speed = numberField("Velocity", "sacher-velocity", {
      min: velocityRange ? velocityRange[0] : undefined,
      max: velocityRange ? velocityRange[1] : undefined,
      step: 0.1,
      placeholder: "nm/s",
      help: "nm/s" + (velocityRange ? ", " + velocityRange[0] + "–" + velocityRange[1] : "") + ".",
    });
    const setSpeed = el("button", { class: "button", type: "button", text: "Set velocity" });
    const moveNote = el("p", { class: "panel-hint" });

    // ---- 3. Rebase ---------------------------------------------------------------
    const measured = numberField("Wavemeter reading", "sacher-measured", {
      min: rebaseRange ? rebaseRange[0] : undefined,
      max: rebaseRange ? rebaseRange[1] : undefined,
      step: 0.0001,
      placeholder: "nm",
      help: "nm, what the wavemeter reads for this laser now (" + rangeText(rebaseRange) + ").",
    });
    const rebase = el("button", { class: "button danger", type: "button", text: "Rebase…" });
    const rebaseNote = el("p", { class: "panel-hint" });

    const commandButtons = [refresh, move, setSpeed, rebase];

    container.append(
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [playButton, stateLine, el("div", { class: "spacer" }), readout]),
        canvas,
        el("div", { class: "panel-toolbar" }, [el("div", { class: "spacer" }), release, refresh]),
        el("div", { class: "fields" }, Object.values(readings).map((item) => item.element)),
      ]),
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [el("h2", { text: "Move" })]),
        el("div", { class: "fields" }, [target.element, speed.element]),
        el("div", { class: "panel-toolbar" }, [move, setSpeed]),
        moveNote,
      ]),
      el("section", { class: "panel-card" }, [
        el("header", { class: "panel-head" }, [el("h2", { text: "Rebase to the wavemeter" })]),
        el("p", {
          class: "panel-hint",
          text:
            "Tells the laser that it is at the wavelength the wavemeter reads, so later moves land where " +
            "they should. Only rebase when the wavemeter is reading this laser.",
        }),
        el("div", { class: "fields" }, [measured.element]),
        el("div", { class: "panel-toolbar" }, [rebase]),
        rebaseNote,
      ])
    );
    const chart = createChart(canvas, { unit: "nm" });
    chart.setWindow(60);
    const scriptBox = createScriptBox(container, { session, device, example: EXAMPLE });

    // ---- behaviour ----------------------------------------------------------

    function say(note, text, isError) {
      note.textContent = text;
      note.style.color = isError ? "var(--bad)" : "";
    }

    async function command(button, note, work) {
      if (busy) return;
      busy = true;
      commandButtons.forEach((item) => (item.disabled = true));
      const label = button.textContent;
      button.textContent = label.replace(/…$/, "") + "…";
      try {
        await work();
      } catch (error) {
        say(note, error.message, true);
      } finally {
        button.textContent = label;
        busy = false;
        commandButtons.forEach((item) => (item.disabled = false));
      }
    }

    async function readLive() {
      const value = await session.call("read_live_wavelength");
      if (typeof value === "number" && isFinite(value)) live = value;
      readings.live.set(number(value, 4, "nm"));
      return value;
    }

    async function readAll() {
      await readLive();
      readings.commanded.set(number(await session.call("read_commanded_wavelength"), 4, "nm"));
      readings.state.set(String(await session.call("get_state")));
      velocity = await session.call("read_velocity");
      readings.velocity.set(number(velocity, 2, "nm/s"));
      if (typeof velocity === "number" && isFinite(velocity) && !speed.input.value) speed.input.value = velocity;
    }

    function inside(value, range) {
      return !range || (value >= range[0] && value <= range[1]);
    }

    // Long enough for the slowest move across the whole range, at most 10 minutes.
    function moveTimeout(to) {
      const span = live !== null ? Math.abs(to - live) : moveRange ? moveRange[1] - moveRange[0] : 30;
      const slowest = velocity || (velocityRange ? velocityRange[0] : 0.1);
      return Math.min(600, Math.ceil(span / slowest * 1.5 + 30));
    }

    function render() {
      const streaming = state.state === "streaming";
      const starting = state.state === "starting";
      playButton.textContent = streaming ? "❚❚  Pause" : starting ? "Starting…" : "▶  Play";
      playButton.classList.toggle("pausing", streaming);
      playButton.disabled = starting;
      release.hidden = !(state.calls_held && !streaming);
      if (streaming) {
        stateLine.textContent = "Live · 2 Hz";
        stateLine.className = "panel-status live";
      } else {
        stateLine.textContent = state.message ||
          (state.leased ? "Laser held by this page" : "Paused · laser free for others");
        stateLine.className = "panel-status" + (state.state === "error" ? " error" : "");
      }
    }

    function onState(event) {
      state = event;
      render();
      scriptBox.onState(event);
    }

    playButton.addEventListener("click", async () => {
      try {
        if (state.state === "streaming") {
          await session.pause();
        } else {
          playButton.disabled = true;
          await session.play({ settings: [], stream: { method: "stream_live_wavelength", sample_rate_hz: 2, channel: null } });
        }
      } catch (error) {
        stateLine.textContent = error.message;
        stateLine.className = "panel-status error";
        playButton.disabled = false;
      }
    });

    refresh.addEventListener("click", () => command(refresh, moveNote, async () => {
      await readAll();
      say(moveNote, "Read at " + new Date().toLocaleTimeString([], { hour12: false }) + ".");
    }));

    move.addEventListener("click", () => command(move, moveNote, async () => {
      const to = Number(target.input.value);
      if (target.input.value === "" || !isFinite(to)) throw new Error("Enter a target wavelength in nm.");
      if (!inside(to, moveRange)) throw new Error(to + " nm is outside " + rangeText(moveRange) + ".");
      say(moveNote, "Moving to " + to + " nm… (the page waits until the motor stops)");
      await session.call("move_to_wavelength_nm", { wavelength_nm: to }, { timeout_seconds: moveTimeout(to) });
      await readAll();
      say(moveNote, "Moved. Live wavelength " + number(live, 4, "nm") + ".");
    }));

    setSpeed.addEventListener("click", () => command(setSpeed, moveNote, async () => {
      const value = Number(speed.input.value);
      if (speed.input.value === "" || !isFinite(value) || !inside(value, velocityRange)) {
        throw new Error("Velocity must be " + (velocityRange ? velocityRange[0] + "–" + velocityRange[1] + " " : "") + "nm/s.");
      }
      velocity = await session.call("set_velocity", { value });
      readings.velocity.set(number(velocity, 2, "nm/s"));
      say(moveNote, "Velocity set to " + number(velocity, 2, "nm/s") + ".");
    }));

    rebase.addEventListener("click", () => command(rebase, rebaseNote, async () => {
      const value = Number(measured.input.value);
      if (measured.input.value === "" || !isFinite(value)) throw new Error("Enter the wavemeter's reading in nm.");
      if (!inside(value, rebaseRange)) {
        throw new Error("Refused: " + value + " nm is outside " + rangeText(rebaseRange) + ".");
      }
      const checked = window.confirm(
        "Before rebasing " + device.id + ", check the connections:\n\n" +
        "• the wavemeter's fiber is connected to THIS laser, and no other light reaches it;\n" +
        "• the wavemeter shows a steady reading (not under- or overexposed).\n\n" +
        "Rebase " + device.id + " to " + value + " nm?"
      );
      if (!checked) {
        say(rebaseNote, "Rebase cancelled.");
        return;
      }
      const current = await readLive();
      if (typeof current === "number" && Math.abs(value - current) > REBASE_WARNING_NM) {
        const sure = window.confirm(
          value + " nm is " + Math.abs(value - current).toFixed(3) + " nm from the laser's live wavelength (" +
          current.toFixed(4) + " nm). That is more than " + REBASE_WARNING_NM + " nm: the wavemeter may be " +
          "reading another laser, or the reading may be wrong.\n\nRebase anyway?"
        );
        if (!sure) {
          say(rebaseNote, "Rebase cancelled.");
          return;
        }
      }
      const after = await session.call("rebase_position_axis", { measured_wavelength_nm: value });
      if (typeof after === "number" && isFinite(after)) live = after;
      readings.live.set(number(after, 4, "nm"));
      readings.commanded.set(number(await session.call("read_commanded_wavelength"), 4, "nm"));
      say(rebaseNote, "Rebased: the live wavelength is now " + number(after, 4, "nm") + ".");
    }));

    release.addEventListener("click", async () => {
      try {
        await session.pause();
      } catch (error) {
        say(moveNote, error.message, true);
      }
    });

    say(moveNote, "Refresh reads the laser (and holds it for this page until Pause, or 5 minutes without a command).");
    render();

    return {
      snapshot(event) {
        chart.setPoints(event.samples);
        const value = chart.latest();
        readout.textContent = value === null ? "—" : value.toFixed(4) + " nm";
        onState(event);
      },
      state: onState,
      reset() {
        chart.clear();
        readout.textContent = "—";
      },
      samples(event) {
        chart.addPoints(event.points);
        const value = chart.latest();
        readout.textContent = value === null ? "—" : value.toFixed(4) + " nm";
      },
      script: (event) => scriptBox.onScript(event),
    };
  }

  window.LabNet.panels = window.LabNet.panels || {};
  window.LabNet.panels.sacher_laser = mount;
})();
