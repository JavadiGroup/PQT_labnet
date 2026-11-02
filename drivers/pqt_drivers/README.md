# pqt-drivers

LabNet drivers for the PQT lab's instruments. Each one is a thin wrapper
around a [PQT_devices](https://github.com/JavadiGroup/PQT_devices) driver:
the instrument logic, range checks and safe shutdown stay in PQT_devices, and
this package maps a `.proto` contract onto them. It pins one exact
`pqt-devices` version; the sync from PQT_devices updates the wrappers, the
tests and the pin together.

| Device type | Facade / driver | Wraps | Served | Not served |
| --- | --- | --- | --- | --- |
| `ws7_wavemeter` | `Ws7Wavemeter` / `PhysicalWs7Wavemeter` | `WS7Wavemeter` | wavelength (single and streamed), frequency, temperature, exposure, auto exposure, measurement start/stop/state | switcher mode, channel selection |
| `sacher_laser` | `SacherLaser` / `PhysicalSacherLaser` | `SacherLaser` | move to a wavelength, live and commanded wavelength (live also streamed), rebase to a wavemeter reading, velocity, controller state | `jog_steps`, `calibration_mode`, `reference_wavelength_nm`, `stop` |
| `horiba_spectrometer` | `HoribaSpectrometer` / `PhysicalHoribaSpectrometer` | `HoribaSpectrometer` | spectra (counts and their wavelengths), exposure, accumulations, center wavelength, grating, acquisition time | binning, laser selection |

## Behaviour to know

- **Long calls.** A Sacher move and a Horiba acquisition block until they
  finish, and calls to one device run one at a time. Give such a call a
  timeout longer than the operation (`timeout_seconds=...`); the client
  default is 10 s. For the same reason the Sacher's `stop` isn't served: it
  would only run after the move it was meant to stop.
- **WS7 without signal.** `read_wavelength` fails; the stream instead sends a
  bad-quality sample (NaN, `NON_FINITE`) and keeps going.
- **Sacher limits.** The driver publishes its calibration range for moves and
  rebases; each Lab PC's inventory narrows it with `site_limits`. A rebase
  rewrites the motor's calibrated position, so do it only from a wavemeter
  reading of this laser.
- **Horiba spectra.** `acquire_spectrum()` returns the counts;
  `get_spectrum_wavelengths()` returns the wavelengths of that same spectrum.
  Exposure and accumulations are the values last set (LabSpec can't report
  them). Grating names are published as choices at connect.

## Device pages

The Control Center's page for each device (`https://<control center>/<device id>`)
comes from `src/pqt_drivers/panels/<device_type>.js`, registered under the
entry-point group `labnet.control_center.panels`. It needs a Control Center
that serves driver panels; an older one shows its generic page (script box
only).

- **WS7** (`ws7`): live wavelength graph (Play), with gaps where the WS7 has no
  signal; exposure, auto exposure, measurement start/stop; Refresh reads the
  frequency, temperature and state.
- **Sacher** (`sacher-vis`): live and commanded wavelength, a live graph, Move to a
  target inside the Lab PC's limits, velocity. **Rebase** asks the person to check
  that the wavemeter is reading this laser, asks again if the value is more than
  2 nm from the live wavelength, and refuses values outside the limits (as the
  Lab PC does).
- **Horiba** (`horiba-spec-vis`): Live repeats one acquisition after another, or
  Acquire takes one; the spectrum plot zooms (drag, scroll, double-click). The
  settings (exposure, accumulations, center wavelength, grating) apply together.

Opening a page leases nothing. A button that talks to the device leases it for
the page until Pause/Release, 5 minutes without a command, or the hold limit
(30 min). `tests/test_panels.py` checks that every method a page calls is in its
contract with the arguments it passes.

## Use

A Lab PC creates the drivers in its inventory (`sites/pqt/inventories/`):

```python
from pqt_drivers import PhysicalWs7Wavemeter

def create_devices():
    return [PhysicalWs7Wavemeter("ws7")]
```

A client leases a device through Central and calls the facade:

```python
from labnet import LabClient
from pqt_drivers import HoribaSpectrometer

central = LabClient.from_env()
spectrometer = HoribaSpectrometer(client=central, device_id="horiba-spec-vis")
try:
    spectrometer.set_exposure(0.5)
    counts = spectrometer.acquire_spectrum(timeout_seconds=30)
    wavelengths = spectrometer.get_spectrum_wavelengths()
finally:
    spectrometer.close()
    central.close()
```

## Develop

```
pip install -e ".[dev]"
labnet driver build        # protos -> src/pqt_drivers/_generated/ (after every proto change)
pytest                     # offline: stand-ins for the PQT_devices drivers, and a real in-process Lab PC
```

- `protos/pqt_drivers/v1/*.proto`: the network contracts. Keep deployed ones
  backward compatible (never renumber or retype a field).
- `src/pqt_drivers/<family>/physical.py`: the wrappers. Pass calls through;
  checks and logic belong in PQT_devices.
- `src/pqt_drivers/_generated/`: built by `labnet driver build`. Commit it;
  never edit it. `.gitattributes` keeps it LF so `--check` works on Windows.
