"""Parametric high-altitude-balloon flight profile.

Produces a physically-plausible (not aerospace-grade) ground_pre -> ascent
-> burst -> descent -> ground_post trajectory and derives every other
quantity (pressure, temperature, humidity, IMU, mag, GPS, battery) from
that single altitude/time timeline. Every method is a pure function of
`mission_t`, so any process can ask "what does the world look like at
time T" independently and get the same answer -- there is no hidden
integrator state to keep in sync across sensors or across processes.
"""
from __future__ import annotations

import math

from sim.world import FlightProfile, FlightState

G = 9.80665
KM_PER_DEG_LAT = 111.32


def _det_noise(t: float, salt: int) -> float:
    """Deterministic pseudo-random value in [-1, 1], stable for a given (t, salt)."""
    h = hash((round(t, 3), salt)) & 0xFFFFFFFF
    return (h / 0xFFFFFFFF) * 2.0 - 1.0


def _smooth_noise(t: float, salt: int, period: float = 1.0) -> float:
    """A slowly-varying noise signal: blends a few sine harmonics with a
    coarse deterministic jitter so consecutive samples look continuous
    instead of independently random."""
    base = (
        0.6 * math.sin(t / period * 1.0 + salt)
        + 0.3 * math.sin(t / period * 2.7 + salt * 1.7)
        + 0.1 * math.sin(t / period * 5.3 + salt * 0.3)
    )
    jitter = 0.15 * _det_noise(t, salt)
    return base + jitter


def _std_atmosphere_temp_c(alt_m: float) -> float:
    """Rough standard-atmosphere temperature profile good enough for a
    telemetry sanity check (not a real atmospheric model)."""
    if alt_m <= 11000:
        return 15.0 - 6.5 * (alt_m / 1000.0)
    if alt_m <= 20000:
        return -56.5
    # mild stratospheric warming above the tropopause
    return -56.5 + 1.0 * ((alt_m - 20000.0) / 1000.0)


def _pressure_hpa_from_alt(alt_m: float) -> float:
    """Exact inverse of the barometric formula app.py itself uses to derive
    baro_alt from pressure, so the FSW's own apogee-detection math sees a
    self-consistent world."""
    alt_m = min(alt_m, 44300.0)
    return 1013.25 * (1.0 - alt_m / 44330.0) ** (1.0 / 0.1903)


class SyntheticAscentProfile(FlightProfile):
    def __init__(
        self,
        seed: int = 495,
        launch_lat: float = 42.2808,
        launch_lon: float = -83.7430,
        launch_alt_m: float = 250.0,
        ground_pre_s: float = 60.0,
        ascent_rate_mps: float = 5.0,
        burst_alt_m: float = 30500.0,
        descent_terminal_mps: float = -6.0,
    ):
        self.seed = seed
        self.launch_lat = launch_lat
        self.launch_lon = launch_lon
        self.launch_alt_m = launch_alt_m
        self.ground_pre_s = ground_pre_s
        self.ascent_rate_mps = ascent_rate_mps
        self.burst_alt_m = burst_alt_m
        self.descent_terminal_mps = descent_terminal_mps

        self.ascent_end_s = ground_pre_s + (burst_alt_m - launch_alt_m) / ascent_rate_mps
        # freefall relaxes to parachute terminal velocity with this time constant
        self._freefall_initial_mps = -45.0
        self._freefall_tau_s = 20.0

    @classmethod
    def from_config(cls, cfg) -> "SyntheticAscentProfile":
        return cls(
            seed=cfg.seed,
            launch_lat=cfg.launch_lat,
            launch_lon=cfg.launch_lon,
            launch_alt_m=cfg.launch_alt_m,
            ground_pre_s=cfg.ground_pre_s,
            ascent_rate_mps=cfg.ascent_rate_mps,
            burst_alt_m=cfg.burst_alt_m,
            descent_terminal_mps=cfg.descent_terminal_mps,
        )

    # ------------------------------------------------------------------
    # Altitude / vertical speed / phase timeline
    # ------------------------------------------------------------------
    def _alt_vspeed_phase(self, t: float):
        if t < self.ground_pre_s:
            return self.launch_alt_m + 0.3 * _det_noise(t, 1), 0.0, "ground_pre"

        if t < self.ascent_end_s:
            dt = t - self.ground_pre_s
            alt = self.launch_alt_m + self.ascent_rate_mps * dt
            return alt, self.ascent_rate_mps, "ascent"

        # descent: exponential relaxation from freefall to parachute terminal velocity
        dt = t - self.ascent_end_s
        v_term = self.descent_terminal_mps
        v0 = self._freefall_initial_mps
        tau = self._freefall_tau_s
        decay = math.exp(-dt / tau)
        vspeed = v_term + (v0 - v_term) * decay
        alt = (
            self.burst_alt_m
            + v_term * dt
            + (v0 - v_term) * tau * (1.0 - decay)
        )

        if alt <= self.launch_alt_m:
            return self.launch_alt_m, 0.0, "ground_post"
        return alt, vspeed, "descent"

    # ------------------------------------------------------------------
    # Horizontal drift (smooth, deterministic, no wind-field integration)
    # ------------------------------------------------------------------
    def _horizontal_offset_km(self, t: float):
        th = t / 3600.0
        dx = 6.0 * th + 1.5 * math.sin(t / 900.0 + self.seed)
        dy = 3.0 * th + 1.0 * math.sin(t / 1200.0 + self.seed * 0.5)
        ddx_dt = 6.0 / 3600.0 + 1.5 * math.cos(t / 900.0 + self.seed) / 900.0
        ddy_dt = 3.0 / 3600.0 + 1.0 * math.cos(t / 1200.0 + self.seed * 0.5) / 1200.0
        return dx, dy, ddx_dt, ddy_dt

    # ------------------------------------------------------------------
    def state_at(self, t: float) -> FlightState:
        t = max(0.0, t)
        alt_m, vspeed, phase = self._alt_vspeed_phase(t)

        temp_c = _std_atmosphere_temp_c(alt_m) + 0.5 * _det_noise(t, 2)
        pressure_hpa = _pressure_hpa_from_alt(alt_m) * (1.0 + 0.0005 * _det_noise(t, 3))
        humidity_pct = max(1.0, 55.0 * math.exp(-alt_m / 3000.0) + 2.0 * _det_noise(t, 4))
        gas_ohms = 5000.0 + 40000.0 * (1.0 - math.exp(-alt_m / 8000.0)) + 500 * _det_noise(t, 5)

        dx_km, dy_km, ddx_dt, ddy_dt = self._horizontal_offset_km(t)
        lat_deg = self.launch_lat + dy_km / KM_PER_DEG_LAT
        km_per_deg_lon = KM_PER_DEG_LAT * max(0.2, math.cos(math.radians(self.launch_lat)))
        lon_deg = self.launch_lon + dx_km / km_per_deg_lon
        ground_speed_mps = math.hypot(ddx_dt, ddy_dt) * 1000.0
        heading_deg = math.degrees(math.atan2(ddx_dt, ddy_dt)) % 360.0

        # --- IMU: near 1g at rest, extra vibration during ascent, a burst
        # shock + tumble right after burst, settling under the parachute ---
        vib = 0.05
        gyro_amp = 1.0
        if phase == "ascent":
            vib = 0.15
            gyro_amp = 3.0
        elif phase == "descent":
            dt_since_burst = t - self.ascent_end_s
            shock = 8.0 * math.exp(-dt_since_burst / 3.0) if dt_since_burst >= 0 else 0.0
            vib = 0.3 + shock * 0.05
            gyro_amp = 40.0 * math.exp(-dt_since_burst / 8.0) + 2.0
        else:
            shock = 0.0

        ax = vib * _smooth_noise(t, 11, period=0.7)
        ay = vib * _smooth_noise(t, 12, period=0.9)
        az = G + vib * _smooth_noise(t, 13, period=0.5)
        if phase == "descent" and (t - self.ascent_end_s) < 2.0:
            az += 6.0 * math.exp(-(t - self.ascent_end_s))  # burst shock spike
        accel_body_mps2 = (ax, ay, az)

        gx = gyro_amp * _smooth_noise(t, 21, period=1.3)
        gy = gyro_amp * _smooth_noise(t, 22, period=1.1)
        gz = gyro_amp * _smooth_noise(t, 23, period=1.7)
        gyro_dps = (gx, gy, gz)

        # --- Magnetometer: ~Earth field magnitude, slowly rotating with heading ---
        b_total = 50.0
        incl_rad = math.radians(65.0)
        hdg_rad = math.radians(heading_deg)
        mag_x = b_total * math.cos(incl_rad) * math.cos(hdg_rad) + 1.5 * _det_noise(t, 31)
        mag_y = b_total * math.cos(incl_rad) * math.sin(hdg_rad) + 1.5 * _det_noise(t, 32)
        mag_z = -b_total * math.sin(incl_rad) + 1.5 * _det_noise(t, 33)
        mag_ut = (mag_x, mag_y, mag_z)

        # --- GPS fix: no fix for the first few seconds on the pad ---
        if phase == "ground_pre" and t < 8.0:
            sats_used, sats_in_view, fix_quality = 0, 3, 0
        else:
            sats_used = 9 + int(round(_det_noise(t, 41)))
            sats_in_view = sats_used + 3
            fix_quality = 1

        # --- Battery: slow linear drain over a nominal ~3 hour mission ---
        drain_frac = min(1.0, t / (3 * 3600.0))
        batt_voltage = 8.2 - 1.2 * drain_frac + 0.02 * _det_noise(t, 51)
        batt_current = 0.9 + 0.1 * _det_noise(t, 52)

        return FlightState(
            mission_t=t,
            phase=phase,
            lat_deg=lat_deg,
            lon_deg=lon_deg,
            alt_m=alt_m,
            vspeed_mps=vspeed,
            ground_speed_mps=ground_speed_mps,
            heading_deg=heading_deg,
            pressure_hpa=pressure_hpa,
            temp_c=temp_c,
            humidity_pct=humidity_pct,
            gas_ohms=gas_ohms,
            accel_body_mps2=accel_body_mps2,
            gyro_dps=gyro_dps,
            mag_ut=mag_ut,
            sats_used=sats_used,
            sats_in_view=sats_in_view,
            fix_quality=fix_quality,
            batt_voltage=batt_voltage,
            batt_current=batt_current,
        )

    def duration(self):
        return None  # holds at ground_post forever once landed
