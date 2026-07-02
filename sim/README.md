# Echo FSW Simulator

Runs the **real** flight software — `telem_deps/app.py`, `radio/unified_radio.py`,
`watchdog/watchdog.py`, `telem_deps/sensors/camera_worker.py` — unmodified, on a
laptop, with every hardware touchpoint (I2C sensors, the Blinka/SPI radio, the
Pi camera stack, and systemd) replaced by a plug-and-play virtual device driven
by one shared, physically-plausible flight timeline. No Pi, no I2C bus, no
camera, no systemd required.

```
python -m venv .venv-sim && .venv-sim/bin/pip install -r sim/requirements.txt
.venv-sim/bin/python -m sim.cli run --time-scale 60 --ground-station --dashboard
```
Then in another terminal: `.venv-sim/bin/python -m sim.cli status`.

## Why it's architected this way

The FSW talks to hardware through three different channels, so the simulator
meets each one where it lives instead of forcing a single strategy everywhere:

| Channel | Real thing | Sim seam |
|---|---|---|
| `import board/busio/smbus/...` | Blinka + I2C kernel driver | `sim/fake_modules/*.py` shadow the real packages on `sys.path` |
| `subprocess.run(["systemctl"/"rpicam-still"/"hwclock"/"sudo", ...])` | systemd, libcamera, RTC utils | `sim/fakebin/*` executables shadow them on `PATH` |
| Hardcoded `/home/f25-echo2/echo_fsw/...` path constants | Pi filesystem layout | `sim/patch_paths.py` reassigns the already-imported modules' globals |

**None of this required editing FSW source.** Every one of these is a plain
module-level name that the owning code re-reads at call time rather than
capturing into a closure at import time — Python lets you swap what an
already-imported module's global points to, or what a bare `import X`
resolves to via `sys.path` order, without touching `X`'s caller at all. The
one exception (`sim/devices/host_stats.py`) is discussed in "Known
limitations" below.

## Layout

```
sim/
  world.py              FlightState + FlightProfile ABC + World (mission clock)
  config.py              All SIM_* env vars, one dataclass, shared by every process
  profiles/
    synthetic.py         Parametric ascent/burst/descent/landing HAB profile
    replay.py            Replays data/telemetry+gps+mag CSVs (real or bench-test)
  devices/
    i2c_virtual_bus.py    Address-keyed registry of virtual I2C peripherals
    tmp102_device.py, rtc_device.py, gps_device.py, pdu_device.py
    radio_medium.py        Loopback multi-process "RF ether" (UDP, optional loss/latency)
    host_stats.py           /proc, /sys fallback shim (only engages off-Linux)
  fake_modules/           board, busio, digitalio, smbus, smbus2,
                          adafruit_icm20x, adafruit_bme680, adafruit_rfm9x, rm3100
  fakebin/                systemctl, sudo, hwclock, rpicam-still, rpicam-vid
  supervisor.py           FakeSystemd: Restart=on-failure + WatchdogSec, in userspace
  harness.py              Registers the 5 real units against the fake systemd
  patch_paths.py          Redirects hardcoded Pi paths into sim/state/fsw_home
  run_telem.py/run_radio.py/run_watchdog.py   Thin launchers (HAL setup + main())
  groundstation.py        Real RAP/CAP/ACK/DAP protocol client + image reassembly
  dashboard.py            Live mission dashboard: stdlib HTTP+SSE server, tails FSW's own CSVs
  dashboard_static/        index.html / dashboard.js / dashboard.css (Leaflet + Chart.js + Three.js via CDN)
  cli.py                  Operator entrypoint (run/status/crash/reboot/dashboard/...)
```

## The flight world

`sim/world.py` defines `FlightState` (altitude, vspeed, lat/lon, pressure,
temp, humidity, IMU accel/gyro, mag field, GPS fix, battery V/I) and
`FlightProfile`, a pure function `mission_t -> FlightState`. Every virtual
sensor calls `get_world().now()` and reads the fields it needs — nothing
about adding, removing, or swapping a sensor touches the profile.

Two profiles, selectable with `--profile`:
- **synthetic** (default): a scripted ground_pre → ascent → burst → descent →
  ground_post HAB trajectory. Pressure is the *exact* inverse of the
  barometric formula `app.py` itself uses for apogee detection, so the FSW's
  own baro-based descent trigger sees a self-consistent world.
- **replay**: replays `data/telemetry/telemetry.csv` (+ gps/mag CSVs) with
  linear interpolation and forward-fill for blanks. Auto-detects CSVs that
  were appended to without ever getting a header line (as the ones shipped
  in this repo are) and falls back to the known `CORE_HEADER` column order.

The **mission clock** is decoupled from the FSW's own real-time loop cadence:
`SAMPLE_PERIOD_SECONDS` etc. still sleep in real wall-clock seconds exactly
as on hardware, but `mission_t = (time.time() - epoch) * SIM_TIME_SCALE`, so
`--time-scale 60` rehearses an hours-long flight in minutes without touching
any FSW timing code. The epoch is a fixed real Unix timestamp (not
`time.monotonic()` since process start), so mission time keeps advancing
correctly across a watchdog-triggered restart or a simulated reboot — a
real Pi reboot mid-flight wouldn't reset the balloon's actual altitude, and
neither does this.

## Virtual sensors (all plug-and-play)

Two tiers, matched to how the real code talks to each chip:

- **I2C register level** (`sim/devices/i2c_virtual_bus.py` + one file per
  chip): TMP102 x2, RV-8803 RTC, u-blox NMEA-over-I2C GPS, ADS7828 PDU ADC
  x2. These encode physical values into the *exact* raw byte format the real
  driver code decodes (two's-complement 12-bit temps, BCD RTC registers,
  real NMEA sentences with valid checksums, ADC counts that round-trip
  through `eddy_pdu.py`'s current divider/shunt math). Verified by running
  the actual `telem_deps/sensors/*.py` decode functions against them.
- **Driver object level** (`sim/fake_modules/adafruit_icm20x.py`,
  `adafruit_bme680.py`, `rm3100.py`): fake classes exposing the same
  `.acceleration`/`.gyro`/`.magnetic`/`.temperature`/`.pressure`/... surface
  the real Adafruit drivers do.

Add a sensor by registering one more device in
`i2c_virtual_bus._install_default_devices` (or a new fake driver module);
remove one by deleting its registration line. Nothing else changes.

## Radio

`sim/devices/radio_medium.py` is a loopback UDP "RF ether": every simulated
radio node (the flight radio process, any number of ground stations) drops a
small rendezvous file and unicasts to every other known node, so multiple
independent Python processes can hear each other exactly like they would
over real LoRa — including configurable packet loss (`--radio-loss`) and
latency. `sim/fake_modules/adafruit_rfm9x.py` is the seam `unified_radio.py`
already talks to (`.send()`/`.receive()`/`.tx_power`).

`sim/groundstation.py` is a *real* ground station, not a mock of one: it
builds an actual CAP packet with `lora_gnu_radio/CFLTXRX/encode.encode_rap`
and the real `radio/protocol.fletcher16` checksum, decodes beacons/ACKs/DAP
chunks with the real `decode_rap`, and reassembles downlinked images to
`sim/state/gs_downloads/`. This exercises `unified_radio.py`'s actual
CAP/ACK/DAP handling code end to end.

## systemd, without systemd

`sim/supervisor.py`'s `FakeSystemd` plays systemd's role for real:
`Restart=on-failure`, `WatchdogSec=` (kills a unit that stops petting its
notify socket, exactly like real `Type=notify`), `systemctl
is-active/is-failed/restart/start/stop/reboot/get-default/list-units`. It's a
plain userspace process supervisor — `sim/fakebin/systemctl` forwards the
FSW's real `subprocess.run(["systemctl", ...])` calls to it over a local
control socket, so it can never touch the actual host, and
`watchdog/watchdog.py` (itself one of the supervised units, exactly like on
the real Pi) layers its own restart/escalation logic on top, unmodified.

`echo-incr-boot.service`'s entire job — bump `boot_count.txt` before
`echo-telem` starts — is replicated directly in `sim/harness.py` /
`sim/supervisor.py`'s reboot handler rather than shelling out to the real
`incr_boot.sh`, because that script has its target path hardcoded into a
bash variable with no Python-reachable seam. `apogee-video.service` is
registered as a real one-shot unit (`enabled_at_boot=False`, so a
simulated reboot doesn't restart it) whose `ExecStart` is the fake
`rpicam-vid` — `systemctl start apogee-video.service` really does block
the caller for the (time-scaled) capture duration, faithfully reproducing
the camera-lock race with the photo cycle noted in `CHANGES.md`.

## Live mission dashboard

`sim/dashboard.py` is a stdlib-only HTTP+SSE server (no new dependencies)
serving `sim/dashboard_static/`'s browser page over **two independent SSE
feeds**, kept deliberately separate:

- **`/stream`** tails the *same CSV files `echo-telem.service` is
  writing* — it doesn't read the flight world directly, so what you see is
  exactly what the FSW actually telemetered, the way a real ground station
  would see it, at its real ~5s sample cadence. It joins each telemetry row
  to the GPS row from the same loop iteration (`app.py` writes both from
  one shared `ts` per iteration, so they match on `UnixTime`/`timestamp`
  exactly). Drives:
  - **Ground track**: a Leaflet map with the live lat/lon polyline, a
    "follow" toggle that keeps the current position on-screen as it moves
    (there's no fixed flight area — the balloon can drift tens of km, so
    the view has to actually track it, not just zoom in once at launch),
    and a "fit track" button to zoom out to the whole path. Falls back to
    nothing plotted until GPS gets a fix, exactly like a real ops room.
  - **Altitude / vertical speed chart** (Chart.js): altitude prefers GPS,
    and falls back to the same barometric-inverse formula `app.py` itself
    uses for its apogee trigger whenever there's no fix yet.
  - **Live gauges**: battery V/I, BME280 temp/pressure/humidity, CPU
    temp/load, sat count, boot count, packet count.
- **`/attitude_stream`** samples `sim.world.get_world()` directly at 20 Hz —
  a ground-truth IMU probe, like a bench-test scope, *not* something the
  FSW downlinked (that stays on the ~5s cadence above). This is what drives
  the **3D attitude cube** (Three.js) smoothly instead of snapping once per
  telemetry sample and holding still in between. Requires the dashboard
  process to share the exact same mission clock (epoch/scale/profile/seed)
  as the running sim, since `FlightState` is a pure function of mission
  time — `sim.cli run --dashboard` wires this up automatically via
  `env_for_subprocess()`, and a standalone `sim.cli dashboard` (started
  later, in another terminal) picks it up from
  `sim/state/active_config.json`, written once at `run` startup.

Both feeds compute roll/pitch from the accelerometer (quasi-static
assumption) and yaw from a tilt-compensated magnetometer reading — a
**dashboard-side estimate**, clearly labeled in the UI. The real FSW never
computes attitude; it only logs raw accel/gyro/mag, so this is
presentation, not a claim about onboard state.

```
sim.cli run --dashboard                     # start the flight stack + dashboard together
sim.cli dashboard                           # or point it at an already-running sim
sim.cli run --dashboard --no-open-browser --dashboard-port 9000
```

Requires internet access in the browser (Leaflet/Chart.js/Three.js load
from CDN — no bundler, no build step, single static HTML page).

## CLI

```
sim.cli run [--time-scale N] [--profile synthetic|replay] [--seed N]
            [--rtc-drift SEC] [--radio-loss PCT] [--ground-pre SEC]
            [--fsw-home PATH] [--ground-station]
            [--dashboard] [--dashboard-port N] [--no-open-browser]
sim.cli status                     # unit states + pids for a running sim
sim.cli crash <unit>               # SIGKILL a unit's process -- watch it restart
sim.cli reboot                     # simulate a full Pi reboot (never touches the real host)
sim.cli start apogee-video.service # trigger the one-shot on demand
sim.cli stop <unit>
sim.cli groundstation [--no-auto-request]
sim.cli dashboard [--fsw-home PATH] [--port N] [--no-open-browser]
```

`--rtc-drift 90` starts the virtual RTC 90 seconds off real UTC, so you can
watch `app.py`'s GPS→RTC drift-sync path (`sudo hwclock --set --date ...`,
faked by `sim/fakebin/hwclock`) actually fire.

## Known limitations

- **CPU/mem/disk host stats** (`telem_deps/sensors/cpu.py`, `memory.py`) read
  `/proc/stat`, `/proc/meminfo`, and `/sys/class/thermal/.../temp` directly,
  as literal path strings with no module attribute to patch. `sim/devices/
  host_stats.py` narrowly wraps `builtins.open` to serve fake contents for
  *only* those three exact paths, and *only* when the real file doesn't
  exist — so on an actual Linux box (or the Pi) it's inert and the real
  files are used untouched. This is the one seam that isn't a pure
  `sys.path`/`PATH`/attribute swap, because none was available.
- **Video/photo content** is synthetic (a JPEG with mission time/altitude
  burned in via PIL; a placeholder-byte "video" file of plausible size) —
  nothing in the FSW parses image/video *content*, only paths and sizes.
- The radio medium models packet loss/latency, not real LoRa airtime,
  spreading factor, or RF propagation — packets that would take seconds of
  real airtime at SF/BW settings are effectively instant here.
- The muon detector tooling (`import_csmcwtch_data.py`, `data/cwmuon/`) is
  out of scope — it isn't wired into the telemetry loop and wasn't asked for.
- `lora_gnu_radio/CFLTXRX/telemetry_beacon.py` has its own PDU
  divider-ratio constants that predate the fixes in `eddy_pdu.py`
  (`CHANGES.md` §4) and no longer quite match; the sim's ground-station
  beacon decode mirrors `telemetry_beacon.py`'s own forward math (so it's a
  correct round-trip through *that* module) but this is a pre-existing
  latent inconsistency between two files, not something the simulator
  introduced or corrected.
