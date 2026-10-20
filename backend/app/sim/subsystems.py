"""Subsystem health of one satellite: power, thermal, storage, attitude (modelled) and RF (real).

The models are generic (``app/data/seed/subsystem_models.json``); what drives them is real
geometry: whether the satellite is in the Earth's shadow (``sim/sun.py``), whether it is in contact
with this station with a usable margin (the same ``LinkTable.evaluate`` as everything else), and
where the station sits relative to its nadir.

Deterministic in sim time: the state at ``t`` is integrated from a fixed initial state over
``[t - lookback, t]`` on a grid of absolute instants (multiples of the step since the Unix epoch),
recomputed on every call. The same ``t`` therefore always gives the same values however the clock
got there (a jump, a replay), and the dynamics forget their initial state long before the window
ends: the thermal lag decays, a sunlit stretch recharges the battery, and the buffer saturates
without a contact or empties during one.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Self

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field, model_validator
from sgp4.api import Satrec, SatrecArray

from app.data.seed import SEED_DIR
from app.models.ws import (
    AttitudeHealth,
    HealthState,
    Illumination,
    LinkState,
    PowerHealth,
    Rag,
    RfHealth,
    SatelliteType,
    StorageHealth,
    ThermalHealth,
)
from app.sim.geometry import Observer, off_nadir_deg, teme_to_itrf, topocentric
from app.sim.linkbudget import RED, LinkTable, RagThresholds
from app.sim.propagate import epochs, epochs_unix, sgp4_teme
from app.sim.sun import EARTH_RADIUS_KM, sun_position_km, sunlit_fraction

MU_EARTH_KM3_S2 = 398_600.4418
_SUNLIT, _ECLIPSE = 0.999, 0.001  # sunlit fraction boundaries of "penumbra"

Floats = NDArray[np.float64]


# ---------- parameters (seed JSON) ----------


class PowerModel(BaseModel):
    design_dod_pct: float = Field(gt=0, le=100)  # at the end of the orbit's longest eclipse
    recharge_factor: float = Field(gt=0, le=10)


class ThermalModel(BaseModel):
    sunlit_eq_c: float = Field(ge=-150, le=150)
    eclipse_eq_c: float = Field(ge=-150, le=150)
    tau_s: float = Field(gt=0, le=1e6)
    band_min_c: float = Field(ge=-150, le=150)
    band_max_c: float = Field(ge=-150, le=150)

    @model_validator(mode="after")
    def _band(self) -> Self:
        if self.band_min_c >= self.band_max_c:
            raise ValueError("thermal band_min_c must be below band_max_c")
        return self


class StorageModel(BaseModel):
    fill_hours: float = Field(gt=0, le=1000)
    capacity_downlink_s: float = Field(gt=0, le=1e6)


class AttitudeModel(BaseModel):
    half_beamwidth_deg: float = Field(gt=0, le=90)


class TypeModel(BaseModel):
    power: PowerModel
    thermal: ThermalModel
    storage: StorageModel
    attitude: AttitudeModel


class _PowerThresholds(BaseModel):
    green_at_or_above: float = Field(ge=0, le=100)
    amber_at_or_above: float = Field(ge=0, le=100)


class _StorageThresholds(BaseModel):
    green_at_or_below: float = Field(ge=0, le=100)
    amber_at_or_below: float = Field(ge=0, le=100)


class Thresholds(BaseModel):
    power_soc_pct: _PowerThresholds
    storage_fill_pct: _StorageThresholds
    thermal_amber_beyond_band_c: float = Field(ge=0, le=100)
    attitude_amber_beyond_beam_deg: float = Field(ge=0, le=90)


class SubsystemModels(BaseModel):
    version: int = Field(ge=1)
    thresholds: Thresholds
    default: TypeModel
    by_type: dict[SatelliteType, TypeModel]

    def for_type(self, kind: SatelliteType) -> TypeModel:
        return self.by_type.get(kind, self.default)


SUBSYSTEM_MODELS_FILE = SEED_DIR / "subsystem_models.json"


@lru_cache
def subsystem_models(path: Path = SUBSYSTEM_MODELS_FILE) -> SubsystemModels:
    """Parse the seed; per-type entries override the default section by section, field by field."""

    class _File(BaseModel):
        version: int
        thresholds: Thresholds
        default: TypeModel
        types: dict[SatelliteType, dict[str, dict[str, float]]] = Field(default_factory=dict)

    raw = _File.model_validate_json(path.read_bytes())
    base = raw.default.model_dump()
    by_type = {
        kind: TypeModel.model_validate(
            {section: {**base[section], **override.get(section, {})} for section in base}
        )
        for kind, override in raw.types.items()
    }
    return SubsystemModels(
        version=raw.version, thresholds=raw.thresholds, default=raw.default, by_type=by_type
    )


# ---------- the integration (pure) ----------


@dataclass(frozen=True)
class Integrated:
    soc_pct: float
    temperature_c: float
    fill_pct: float | None


def battery_rates(model: PowerModel, period_s: float) -> tuple[float, float]:
    """(charge in full sun, load) in %/h, for a battery sized for this orbit's longest eclipse.

    The longest eclipse (β = 0, cylindrical shadow) lasts asin(R⊕ / a) / π of the orbit; the load
    drains ``design_dod_pct`` over it, and full sun recharges ``recharge_factor`` times faster than
    the rest of the orbit would need.
    """
    n_rad_s = 2 * math.pi / period_s
    a_km = (MU_EARTH_KM3_S2 / n_rad_s**2) ** (1 / 3)
    shadow = math.asin(min(EARTH_RADIUS_KM / a_km, 1.0)) / math.pi
    period_h = period_s / 3600
    eclipse_h = max(shadow * period_h, 1e-3)
    sun_h = max(period_h - eclipse_h, 1e-3)
    load = model.design_dod_pct / eclipse_h
    charge = load + model.recharge_factor * model.design_dod_pct / sun_h
    return charge, load


def integrate(
    model: TypeModel,
    sunlit: Floats,
    downlink: NDArray[np.bool_] | None,
    *,
    step_s: float,
    tail_s: float,
    period_s: float,
) -> Integrated:
    """Run the models over grid samples (each held for ``step_s``; the last for ``tail_s``).

    Starts from a full battery, the first sample's equilibrium temperature and a full buffer.
    """
    charge, load = battery_rates(model.power, period_s)
    th = model.thermal
    fill_rate = 100.0 / (model.storage.fill_hours * 3600)  # %/s
    drain_rate = 100.0 / model.storage.capacity_downlink_s  # %/s while downlinking

    def t_eq(f: float) -> float:
        return th.eclipse_eq_c + f * (th.sunlit_eq_c - th.eclipse_eq_c)

    n = len(sunlit)
    f_values = sunlit.tolist()
    dl_values = downlink.tolist() if downlink is not None else None
    soc = 100.0
    temp = t_eq(f_values[0])
    fill = 100.0
    decay_step = math.exp(-step_s / th.tau_s)
    for i in range(n):
        dt = step_s if i < n - 1 else tail_s
        decay = decay_step if i < n - 1 else math.exp(-dt / th.tau_s)
        f = f_values[i]
        soc = min(100.0, max(0.0, soc + (f * charge - load) * dt / 3600))
        eq = t_eq(f)
        temp = eq + (temp - eq) * decay
        if dl_values is not None:
            rate = fill_rate - drain_rate if dl_values[i] else fill_rate
            fill = min(100.0, max(0.0, fill + rate * dt))
    return Integrated(soc, temp, fill if downlink is not None else None)


# ---------- RAG ----------


def power_rag(soc_pct: float, t: Thresholds) -> Rag:
    p = t.power_soc_pct
    return (
        "green"
        if soc_pct >= p.green_at_or_above
        else ("amber" if soc_pct >= p.amber_at_or_above else "red")
    )


def thermal_rag(temp_c: float, model: ThermalModel, t: Thresholds) -> Rag:
    outside = max(model.band_min_c - temp_c, temp_c - model.band_max_c, 0.0)
    if outside == 0:
        return "green"
    return "amber" if outside <= t.thermal_amber_beyond_band_c else "red"


def storage_rag(fill_pct: float, t: Thresholds) -> Rag:
    s = t.storage_fill_pct
    return (
        "green"
        if fill_pct <= s.green_at_or_below
        else ("amber" if fill_pct <= s.amber_at_or_below else "red")
    )


def attitude_rag(off_nadir: float, half_beamwidth_deg: float, t: Thresholds) -> Rag:
    beyond = off_nadir - half_beamwidth_deg
    if beyond <= 0:
        return "green"
    return "amber" if beyond <= t.attitude_amber_beyond_beam_deg else "red"


def rollup(rags: list[Rag | None]) -> Rag | None:
    """Red if any is red; otherwise unknown if any has no data; otherwise the worse of the rest."""
    if "red" in rags:
        return "red"
    if None in rags:
        return None
    return "amber" if "amber" in rags else "green"


def illumination(fraction: float) -> Illumination:
    return "sunlit" if fraction >= _SUNLIT else "eclipse" if fraction <= _ECLIPSE else "penumbra"


# ---------- per-session tracker ----------


@dataclass(frozen=True)
class _Inputs:
    key: tuple[object, ...]  # satellite, its elements and the link profiles
    k_end: int  # last grid index (instant k_end * step_s)
    sunlit: Floats
    downlink: NDArray[np.bool_] | None


class HealthTracker:
    """Health of the selected satellite, for one session's station.

    Grid inputs are cached and, as the window slides forward, only the new instants are computed;
    each instant is evaluated on its own absolute time, so the values don't depend on the path.
    """

    def __init__(
        self,
        station: Observer,
        mask_deg: float,
        thresholds: RagThresholds,
        *,
        lookback_s: float,
        step_s: float,
        models: SubsystemModels | None = None,
    ) -> None:
        self.station = station
        self.mask_deg = mask_deg
        self.thresholds = thresholds
        self.step_s = step_s
        self.n_steps = max(2, round(lookback_s / step_s))
        self.models = models or subsystem_models()
        self._inputs: _Inputs | None = None

    def _compute(
        self, norad_id: int, satrec: Satrec, links: LinkTable, ks: Floats
    ) -> tuple[Floats, NDArray[np.bool_] | None]:
        """(sunlit fraction, downlink possible) at the grid instants ``ks * step_s``."""
        when = epochs_unix(ks * self.step_s)
        r_teme, v_teme, ok = sgp4_teme(SatrecArray([satrec]), when)
        sunlit = np.where(ok[0], sunlit_fraction(r_teme[0], sun_position_km(when)), 1.0)
        profile = links.by_norad.get(norad_id)
        if profile is None:
            return sunlit, None
        r, v = teme_to_itrf(r_teme[0], v_teme[0], when)
        _, el, rng, rate = topocentric(r, v, self.station)
        ev = links.evaluate(
            np.full(len(ks), profile),
            range_m=rng,
            range_rate_m_s=rate,
            elevation_deg=el,
            off_nadir_deg=off_nadir_deg(r, self.station.itrf_m),
            mask_deg=self.mask_deg,
            thresholds=self.thresholds,
        )
        return sunlit, ok[0] & (ev.rag != RED)

    def _grid_inputs(self, norad_id: int, satrec: Satrec, links: LinkTable, k_end: int) -> _Inputs:
        key = (norad_id, satrec.jdsatepoch, satrec.jdsatepochF, links.version)
        cached = self._inputs
        if cached is not None and cached.key == key and cached.k_end == k_end:
            return cached
        n = self.n_steps
        if cached is not None and cached.key == key and 0 < k_end - cached.k_end < n:
            ks = np.arange(cached.k_end + 1, k_end + 1, dtype=np.float64)
            sunlit, downlink = self._compute(norad_id, satrec, links, ks)
            sunlit = np.concatenate((cached.sunlit, sunlit))[-n:]
            if downlink is not None and cached.downlink is not None:
                downlink = np.concatenate((cached.downlink, downlink))[-n:]
        else:
            ks = np.arange(k_end - n + 1, k_end + 1, dtype=np.float64)
            sunlit, downlink = self._compute(norad_id, satrec, links, ks)
        self._inputs = _Inputs(key, k_end, sunlit, downlink)
        return self._inputs

    def state(
        self,
        *,
        norad_id: int,
        satrec: Satrec,
        kind: SatelliteType,
        links: LinkTable,
        sim_time: datetime,
        elevation_deg: float,
        off_nadir: float,
        link: LinkState | None,
    ) -> HealthState | None:
        if satrec.no_kozai <= 0:
            return None
        t = sim_time.timestamp()
        k_end = math.floor(t / self.step_s)
        inputs = self._grid_inputs(norad_id, satrec, links, k_end)
        model = self.models.for_type(kind)
        thresholds = self.models.thresholds
        core = integrate(
            model,
            inputs.sunlit,
            inputs.downlink,
            step_s=self.step_s,
            tail_s=t - k_end * self.step_s,
            period_s=2 * math.pi / satrec.no_kozai * 60.0,
        )

        # Exact, instantaneous geometry.
        now = epochs(sim_time)
        r_now, _, ok_now = sgp4_teme(SatrecArray([satrec]), now)
        fraction = (
            float(sunlit_fraction(r_now[0, 0], sun_position_km(now)[0])) if ok_now[0, 0] else 1.0
        )
        profile = links.by_norad.get(norad_id)
        beam = (
            links.profiles[profile].coverage_half_angle_deg if profile is not None else None
        ) or model.attitude.half_beamwidth_deg
        in_view = elevation_deg >= self.mask_deg and math.isfinite(off_nadir)
        downlinking = link is not None and link.rag != "red"

        power = PowerHealth(
            rag=power_rag(core.soc_pct, thresholds),
            soc_pct=round(core.soc_pct, 1),
            sunlit_fraction=round(fraction, 3),
            illumination=illumination(fraction),
        )
        thermal = ThermalHealth(
            rag=thermal_rag(core.temperature_c, model.thermal, thresholds),
            temperature_c=round(core.temperature_c, 1),
            band_min_c=model.thermal.band_min_c,
            band_max_c=model.thermal.band_max_c,
        )
        if core.fill_pct is None:
            storage = StorageHealth(rag=None, fill_pct=None, activity=None)
        else:
            storage = StorageHealth(
                rag=storage_rag(core.fill_pct, thresholds),
                fill_pct=round(core.fill_pct, 1),
                activity="downlinking"
                if downlinking
                else "full"
                if core.fill_pct >= 99.95
                else "filling",
            )
        attitude = AttitudeHealth(
            rag=attitude_rag(off_nadir, beam, thresholds) if in_view else None,
            off_nadir_deg=round(off_nadir, 2) if in_view else None,
            half_beamwidth_deg=beam,
        )
        rf = RfHealth(
            rag=link.rag if link is not None else None,
            margin_db=link.margin_db if link is not None else None,
        )
        return HealthState(
            norad_id=norad_id,
            model_version=self.models.version,
            rollup=rollup([power.rag, thermal.rag, storage.rag, attitude.rag, rf.rag]),
            power=power,
            thermal=thermal,
            storage=storage,
            attitude=attitude,
            rf=rf,
        )
