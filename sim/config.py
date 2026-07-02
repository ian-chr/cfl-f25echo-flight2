"""Environment-driven simulator configuration.

Every knob here is read from an env var so that the harness process, the
telemetry app process, the radio process, and the ground-station process
(all separate `python` interpreters, mirroring the real systemd separation)
agree on the same simulated world without any IPC. The harness is the only
thing that *sets* these; everything else only reads them.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _s(name: str, default: str) -> str:
    return os.environ.get(name, default)


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_STATE_DIR = os.path.join(REPO_ROOT, "sim", "state")

# AF_UNIX socket paths are limited to ~104 bytes on macOS/BSD, and this
# repo can live several directories deep -- so sockets (control.sock,
# per-unit notify sockets) live under a short /tmp path keyed off a hash
# of the repo root, while ordinary files (rtc offset, radio rendezvous)
# stay under SIM_STATE_DIR where path length doesn't matter.
import hashlib as _hashlib  # noqa: E402

_REPO_HASH = _hashlib.md5(REPO_ROOT.encode()).hexdigest()[:10]
SIM_RUNTIME_DIR = os.environ.get("SIM_RUNTIME_DIR") or f"/tmp/echofsw-sim-{_REPO_HASH}"
CONTROL_SOCK_PATH = os.path.join(SIM_RUNTIME_DIR, "control.sock")


@dataclass
class SimConfig:
    # ---- clock ----
    epoch_unix: float = field(default_factory=lambda: _f("SIM_EPOCH_UNIX", time.time()))
    time_scale: float = field(default_factory=lambda: _f("SIM_TIME_SCALE", 1.0))

    # ---- flight profile selection ----
    profile: str = field(default_factory=lambda: _s("SIM_PROFILE", "synthetic"))
    seed: int = field(default_factory=lambda: _i("SIM_SEED", 495))

    # ---- synthetic profile knobs ----
    launch_lat: float = field(default_factory=lambda: _f("SIM_LAUNCH_LAT", 42.2808))   # Ann Arbor, MI
    launch_lon: float = field(default_factory=lambda: _f("SIM_LAUNCH_LON", -83.7430))
    launch_alt_m: float = field(default_factory=lambda: _f("SIM_LAUNCH_ALT_M", 250.0))
    ground_pre_s: float = field(default_factory=lambda: _f("SIM_GROUND_PRE_S", 60.0))
    ascent_rate_mps: float = field(default_factory=lambda: _f("SIM_ASCENT_RATE_MPS", 5.0))
    burst_alt_m: float = field(default_factory=lambda: _f("SIM_BURST_ALT_M", 30500.0))
    descent_terminal_mps: float = field(default_factory=lambda: _f("SIM_DESCENT_TERMINAL_MPS", -6.0))

    # ---- replay profile knobs ----
    replay_telem_csv: str = field(
        default_factory=lambda: _s("SIM_REPLAY_TELEM_CSV",
                                    os.path.join(REPO_ROOT, "data", "telemetry", "telemetry.csv")))
    replay_gps_csv: str = field(
        default_factory=lambda: _s("SIM_REPLAY_GPS_CSV",
                                    os.path.join(REPO_ROOT, "data", "gps", "gps_nmea_data.csv")))
    replay_mag_csv: str = field(
        default_factory=lambda: _s("SIM_REPLAY_MAG_CSV",
                                    os.path.join(REPO_ROOT, "data", "mag", "mag_data.csv")))
    replay_loop: bool = field(default_factory=lambda: _i("SIM_REPLAY_LOOP", 1) == 1)

    # ---- fault injection ----
    rtc_drift_sec: float = field(default_factory=lambda: _f("SIM_RTC_DRIFT_SEC", 0.0))
    radio_loss_pct: float = field(default_factory=lambda: _f("SIM_RADIO_LOSS_PCT", 0.0))
    radio_latency_ms: float = field(default_factory=lambda: _f("SIM_RADIO_LATENCY_MS", 15.0))

    # ---- radio virtual medium ----
    radio_medium_port: int = field(default_factory=lambda: _i("SIM_RADIO_PORT", 8765))

    def mission_elapsed(self) -> float:
        return max(0.0, (time.time() - self.epoch_unix) * self.time_scale)


_CONFIG: SimConfig | None = None


def get_config() -> SimConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = SimConfig()
    return _CONFIG


def _sim_env_vars(cfg: SimConfig) -> dict:
    """Every SIM_* var pinned to a specific, already-resolved config --
    the one source of truth for "how do I hand this config to another
    process," used both for direct subprocess launches and for the
    active-config file (see below)."""
    return {
        "SIM_EPOCH_UNIX": repr(cfg.epoch_unix),
        "SIM_TIME_SCALE": repr(cfg.time_scale),
        "SIM_PROFILE": cfg.profile,
        "SIM_SEED": str(cfg.seed),
        "SIM_LAUNCH_LAT": repr(cfg.launch_lat),
        "SIM_LAUNCH_LON": repr(cfg.launch_lon),
        "SIM_LAUNCH_ALT_M": repr(cfg.launch_alt_m),
        "SIM_GROUND_PRE_S": repr(cfg.ground_pre_s),
        "SIM_ASCENT_RATE_MPS": repr(cfg.ascent_rate_mps),
        "SIM_BURST_ALT_M": repr(cfg.burst_alt_m),
        "SIM_DESCENT_TERMINAL_MPS": repr(cfg.descent_terminal_mps),
        "SIM_REPLAY_TELEM_CSV": cfg.replay_telem_csv,
        "SIM_REPLAY_GPS_CSV": cfg.replay_gps_csv,
        "SIM_REPLAY_MAG_CSV": cfg.replay_mag_csv,
        "SIM_REPLAY_LOOP": "1" if cfg.replay_loop else "0",
        "SIM_RTC_DRIFT_SEC": repr(cfg.rtc_drift_sec),
        "SIM_RADIO_LOSS_PCT": repr(cfg.radio_loss_pct),
        "SIM_RADIO_LATENCY_MS": repr(cfg.radio_latency_ms),
        "SIM_RADIO_PORT": str(cfg.radio_medium_port),
        "SIM_RUNTIME_DIR": SIM_RUNTIME_DIR,
        "ECHO_SIM": "1",
    }


def env_for_subprocess() -> dict:
    """Full os.environ plus every SIM_* var pinned to the *current* config.

    Used by the harness/supervisor when it launches a child so the child's
    view of the world (epoch, scale, profile, seed, ...) is guaranteed
    identical even if it re-reads env vars independently.
    """
    env = dict(os.environ)
    env.update(_sim_env_vars(get_config()))
    return env


ACTIVE_CONFIG_PATH = os.path.join(SIM_STATE_DIR, "active_config.json")


def write_active_config_file() -> None:
    """Persists the resolved config so a *standalone* `sim.cli dashboard`,
    started later in a separate terminal against an already-running sim,
    can pick up the exact same mission clock (epoch/scale/profile/seed)
    without the operator having to re-type every flag or export env vars
    by hand."""
    import json
    os.makedirs(SIM_STATE_DIR, exist_ok=True)
    with open(ACTIVE_CONFIG_PATH, "w") as f:
        json.dump(_sim_env_vars(get_config()), f)


def load_active_config_into_env() -> bool:
    """Fills in SIM_* env vars from the last `write_active_config_file()`
    call, without overriding anything the caller already set explicitly
    (CLI flags/env still win). Returns True if a file was found."""
    import json
    if not os.path.exists(ACTIVE_CONFIG_PATH):
        return False
    try:
        with open(ACTIVE_CONFIG_PATH) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return False
    for k, v in saved.items():
        os.environ.setdefault(k, v)
    return True
