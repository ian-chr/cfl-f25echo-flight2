"""The shared "flight world": a single function from mission-time to
ground-truth physical state, which every virtual sensor samples from.

Design invariant: every virtual device (I2C register model or fake
Blinka driver) is *plug-and-play* -- it only ever calls
`sim.world.get_world().state_at(mission_t)` and reads the fields it
cares about. Adding, removing, or swapping a sensor never requires
touching this module, the flight profile, or any other device.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from sim.config import get_config


@dataclass
class FlightState:
    mission_t: float
    phase: str  # "ground_pre" | "ascent" | "descent" | "ground_post"

    lat_deg: float
    lon_deg: float
    alt_m: float                 # MSL geometric altitude
    vspeed_mps: float            # +up
    ground_speed_mps: float
    heading_deg: float

    pressure_hpa: float
    temp_c: float
    humidity_pct: float
    gas_ohms: float

    accel_body_mps2: Tuple[float, float, float]
    gyro_dps: Tuple[float, float, float]
    mag_ut: Tuple[float, float, float]

    sats_used: int
    sats_in_view: int
    fix_quality: int

    batt_voltage: float
    batt_current: float

    @property
    def alt_ft(self) -> float:
        return self.alt_m * 3.28084


class FlightProfile:
    """Abstract base: a pure function mission_t (seconds) -> FlightState."""

    def state_at(self, mission_t: float) -> FlightState:
        raise NotImplementedError

    def duration(self) -> Optional[float]:
        """Nominal flight duration in seconds, or None if open-ended."""
        return None


class World:
    """Binds a FlightProfile to the shared sim clock."""

    def __init__(self, profile: FlightProfile):
        self.profile = profile

    def mission_elapsed(self) -> float:
        return get_config().mission_elapsed()

    def now(self) -> FlightState:
        return self.profile.state_at(self.mission_elapsed())

    def state_at(self, mission_t: float) -> FlightState:
        return self.profile.state_at(mission_t)


_WORLD: Optional[World] = None


def _build_profile(name: str) -> FlightProfile:
    if name == "replay":
        from sim.profiles.replay import CsvReplayProfile
        cfg = get_config()
        return CsvReplayProfile(
            telem_csv=cfg.replay_telem_csv,
            gps_csv=cfg.replay_gps_csv,
            mag_csv=cfg.replay_mag_csv,
            loop=cfg.replay_loop,
        )
    if name == "synthetic":
        from sim.profiles.synthetic import SyntheticAscentProfile
        return SyntheticAscentProfile.from_config(get_config())
    raise ValueError(f"Unknown SIM_PROFILE={name!r} (expected 'synthetic' or 'replay')")


def get_world() -> World:
    global _WORLD
    if _WORLD is None:
        cfg = get_config()
        _WORLD = World(_build_profile(cfg.profile))
    return _WORLD


def reset_world() -> None:
    """Only used by tests / the CLI to force a rebuild after changing env vars."""
    global _WORLD
    _WORLD = None
