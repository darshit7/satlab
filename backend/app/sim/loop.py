"""The simulation tick: look at the whole fleet from the station and stream what is in view.

One ``SessionRuntime`` per live mission session. Each tick reads the sim clock and, from in-memory
data only (no I/O):

1. propagates every catalogued satellite at once (vectorized SGP4) and finds those above the
   horizon mask;
2. streams (above the mask ∩ session filter) + tracked + selected as a ``Snapshot``, each with
   its link budget when it has a published profile, plus the selection's subsystem health;
3. computes a sky arc (``pass_arc``) for satellites newly in view, within a per-tick budget, and a
   ground track for tracked/selected satellites — each recomputed only when it runs out;
4. advances the rising-soon sweep by one chunk and republishes ``upcoming`` when it changes;
5. steps the selection's comms and pass automation (``sim/scheduler.py``), which can turn the
   antenna (the selection) to a booked pass and slow a fast clock before its AOS.
"""

import asyncio
import contextlib
import logging
import math
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel
from sgp4.api import Satrec, SatrecArray

from app.config import Settings
from app.data.classify import classify
from app.models.ws import (
    MAX_RISING_WITHIN_MIN,
    AlarmMessage,
    ClockState,
    CommandStatusMessage,
    ErrorMessage,
    GroundTrackMessage,
    HealthState,
    LinkState,
    ObserverState,
    PacketMessage,
    PassArcMessage,
    Rag,
    SatelliteState,
    SatelliteType,
    SetFilter,
    Snapshot,
    UpcomingMessage,
    UpcomingPass,
)
from app.sim.clock import SimClock, wall_now
from app.sim.comms import (
    CommsConfig,
    CommsRuntime,
    ContactLook,
    FrameArchive,
    NoArchive,
    OutgoingCommand,
    Record,
)
from app.sim.fleet import Fleet
from app.sim.geometry import (
    Observation,
    Observer,
    geodetic,
    ground_track,
    observe,
    off_nadir_deg,
)
from app.sim.linkbudget import RAG_NAMES, RED, LinkTable, RagThresholds, strong_link_bands
from app.sim.passes import Arc, UpcomingSweep, pass_arcs
from app.sim.propagate import Elements, Epochs, epochs
from app.sim.scheduler import AutomationRecord, PassAutomation, ScheduledCommand
from app.sim.subsystems import HealthTracker

log = logging.getLogger(__name__)

# Recompute a ground track once the satellite has moved this fraction of an orbit past its centre.
_TRACK_REFRESH_ORBITS = 0.25
# Sky arcs cover the pass in progress (or the next one) within this window around the sim time.
_ARC_LOOKBACK_S = 30 * 60.0
_ARC_LOOKAHEAD_S = 60 * 60.0
# With no pass in the window, look again after this much sim time.
_ARC_RETRY_S = _ARC_LOOKAHEAD_S / 2
# Republish the rising-soon list this often (wall seconds) as the sim clock moves through it.
_UPCOMING_REPUBLISH_S = 5.0
_SLOW_TICK_LOG_INTERVAL_S = 60.0
# If the tick has already spent this share of its period (e.g. on a burst of new sky arcs), the
# rising-soon sweep waits for the next tick: sgp4 holds the GIL, so work here delays everything.
_SWEEP_BUDGET_SHARE = 0.3
# Strong-link bands are cached per profile and satellite radius rounded to this many metres.
_BAND_RADIUS_STEP_M = 1000.0
_BAND_CACHE_SIZE = 256


@dataclass(frozen=True)
class RuntimeConfig:
    tick_hz: float = 2.0
    ground_track_points: int = 240
    horizon_mask_deg: float = 5.0
    pass_search_step_s: float = 30.0
    pass_arc_points: int = 48
    max_arcs_per_tick: int = 100
    upcoming_window_min: float = 90.0
    upcoming_step_s: float = 60.0
    upcoming_chunk_size: int = 500
    upcoming_max_time_scale: float = 60.0
    upcoming_limit: int = 100
    link_green_margin_db: float = 6.0
    link_amber_margin_db: float = 0.0
    subsystem_lookback_h: float = 24.0
    subsystem_step_s: float = 60.0
    comms_max_time_scale: float = 60.0
    comms_max_packets_per_tick: int = 4
    schedule_lead_s: float = 60.0

    @classmethod
    def from_settings(cls, s: Settings) -> "RuntimeConfig":
        return cls(**{name: getattr(s, name) for name in cls.__dataclass_fields__})


class Subscriber:
    """Latest-value mailbox for one client.

    Snapshots and the rising-soon list coalesce; ground tracks and pass arcs coalesce per
    satellite but are never dropped; errors queue; packets, command statuses and alarms queue in
    order, up to ``MAX_EVENTS`` (a client that stops reading loses the oldest, not the server's
    memory).
    """

    MAX_EVENTS = 200

    def __init__(self) -> None:
        self._snapshot: Snapshot | None = None
        self._upcoming: UpcomingMessage | None = None
        self._tracks: dict[int, GroundTrackMessage] = {}
        self._arcs: dict[int, PassArcMessage] = {}
        self._errors: list[ErrorMessage] = []
        self._events: deque[PacketMessage | CommandStatusMessage | AlarmMessage] = deque(
            maxlen=self.MAX_EVENTS
        )
        self._ready = asyncio.Event()

    def offer(self, message: BaseModel) -> None:
        match message:
            case Snapshot():
                self._snapshot = message
            case UpcomingMessage():
                self._upcoming = message
            case GroundTrackMessage():
                self._tracks[message.norad_id] = message
            case PassArcMessage():
                self._arcs[message.norad_id] = message
            case PacketMessage() | CommandStatusMessage() | AlarmMessage():
                self._events.append(message)
            case ErrorMessage():
                self._errors.append(message)
        self._ready.set()

    async def drain(self) -> list[BaseModel]:
        await self._ready.wait()
        self._ready.clear()
        # geometry first, so the snapshot that uses it never arrives ahead of it
        batch: list[BaseModel] = [
            *self._errors,
            *self._tracks.values(),
            *self._arcs.values(),
            *self._events,
        ]
        if self._upcoming is not None:
            batch.append(self._upcoming)
        if self._snapshot is not None:
            batch.append(self._snapshot)
        self._snapshot, self._upcoming = None, None
        self._tracks, self._arcs, self._errors = {}, {}, []
        self._events.clear()
        return batch


@dataclass
class _TrackState:
    elements: Elements
    center: datetime
    message: GroundTrackMessage


@dataclass
class _ArcState:
    epoch_key: tuple[float, float]  # the TLE it was computed from
    links_version: int  # and the link profiles that coloured it
    computed_at: datetime
    valid_until: datetime
    message: PassArcMessage | None


@dataclass
class _Target:
    """A satellite to stream this tick: a fleet row, or elements outside the fleet."""

    norad_id: int
    satrec: Satrec
    row: int | None = None
    elements: Elements | None = None
    pinned: bool = False  # tracked or selected


@dataclass(frozen=True)
class _Look:
    """Where the selected satellite is this tick, as the health model and comms need it."""

    satrec: Satrec
    kind: SatelliteType
    elevation_deg: float
    above_mask: bool
    range_m: float
    off_nadir_deg: float
    link: LinkState | None


def _epoch_key(satrec: Satrec) -> tuple[float, float]:
    return (satrec.jdsatepoch, satrec.jdsatepochF)


class SessionRuntime:
    def __init__(
        self,
        session_id: int,
        observer: ObserverState,
        clock: SimClock,
        elements_lookup: Callable[[int], Elements | None],
        fleet_source: Callable[[], Fleet],
        config: RuntimeConfig,
        archive: FrameArchive | None = None,
        sink: Callable[[Record | AutomationRecord], None] = lambda record: None,
    ) -> None:
        self.session_id = session_id
        self.observer = observer
        self.clock = clock
        self.config = config
        self._lookup = elements_lookup
        self._fleet = fleet_source
        self._mask_deg = config.horizon_mask_deg
        self._tick_period_s = 1.0 / config.tick_hz
        self._station = Observer(observer.lat_deg, observer.lon_deg, observer.alt_m)
        self._thresholds = RagThresholds(config.link_green_margin_db, config.link_amber_margin_db)
        self._health = HealthTracker(
            self._station,
            self._mask_deg,
            self._thresholds,
            lookback_s=config.subsystem_lookback_h * 3600,
            step_s=config.subsystem_step_s,
        )
        self._bands: dict[tuple[int, int, float], list[tuple[float, float]]] = {}
        self._selected_look: _Look | None = None
        self.tracked: list[int] = []
        self.selected: int | None = None
        self.filter = SetFilter()
        self.tick = 0
        self._tracks: dict[int, _TrackState] = {}
        self._arcs: dict[int, _ArcState] = {}
        self._failed: set[int] = set()
        self._subscribers: set[Subscriber] = set()
        self._task: asyncio.Task[None] | None = None
        self._allowed: tuple[int, SetFilter, NDArray[np.bool_]] | None = None
        self._sweep = UpcomingSweep(
            self._station,
            self._mask_deg,
            window_s=config.upcoming_window_min * 60,
            step_s=config.upcoming_step_s,
            chunk_size=config.upcoming_chunk_size,
            max_within_s=MAX_RISING_WITHIN_MIN * 60,
        )
        self._upcoming: UpcomingMessage | None = None
        self._upcoming_dirty = True
        self._upcoming_published_at = -math.inf
        self._slow_logged_at = -math.inf
        self.comms = CommsRuntime(
            session_id,
            CommsConfig(
                max_time_scale=config.comms_max_time_scale,
                max_packets_per_tick=config.comms_max_packets_per_tick,
            ),
            archive or NoArchive(),
            sink,
        )
        self.automation = PassAutomation(
            session_id,
            sink,
            lead_s=config.schedule_lead_s,
            max_time_scale=config.comms_max_time_scale,
        )

    # ----- subscribers -----

    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        for state in self._tracks.values():
            sub.offer(state.message)
        for arc in self._arcs.values():
            if arc.message is not None:
                sub.offer(arc.message)
        if self._upcoming is not None:
            sub.offer(self._upcoming)
        for packet in self.comms.recent:  # the packet log, for a client that (re)connects
            sub.offer(packet)
        for message in [*self.automation.pending_statuses(), *self.automation.alarms]:
            sub.offer(message)
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _publish(self, message: BaseModel) -> None:
        for sub in self._subscribers:
            sub.offer(message)

    def publish_error(self, message: str) -> None:
        self._publish(ErrorMessage(message=message))

    @property
    def station(self) -> Observer:
        return self._station

    def submit_command(self, command: OutgoingCommand) -> None:
        """Queue a stored, validated command for the satellite in contact."""
        self._publish(self.comms.submit(command))

    def schedule_command(self, command: ScheduledCommand) -> None:
        """Book a stored, validated command for its (server-predicted) pass."""
        self._publish(self.automation.book(command))

    def cancel_command(self, command_id: int) -> bool:
        message = self.automation.cancel(command_id, self.clock.now())
        if message is not None:
            self._publish(message)
        return message is not None

    def ack_alarm(self, alarm_id: int) -> bool:
        message = self.automation.ack(alarm_id)
        if message is not None:
            self._publish(message)
        return message is not None

    # ----- control -----

    @property
    def pinned(self) -> list[int]:
        """Always streamed, whatever the filter: tracked satellites plus the selection."""
        ids = list(self.tracked)
        if self.selected is not None and self.selected not in ids:
            ids.append(self.selected)
        return ids

    def _forget_unpinned_tracks(self) -> None:
        keep = set(self.pinned)
        self._tracks = {k: v for k, v in self._tracks.items() if k in keep}
        self._failed &= keep

    def set_tracked(self, norad_ids: list[int]) -> None:
        self.tracked = list(dict.fromkeys(norad_ids))
        self._forget_unpinned_tracks()

    def set_selected(self, norad_id: int | None) -> None:
        """The operator's selection: away from a booked pass in progress, it takes the antenna."""
        for message in self.automation.operator_selected(norad_id, self.clock.now()):
            self._publish(message)
        self._select(norad_id)

    def _select(self, norad_id: int | None) -> None:
        self.selected = norad_id
        self._forget_unpinned_tracks()

    def set_filter(self, message: SetFilter) -> None:
        self.filter = message
        self._upcoming_dirty = True

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"sim-session-{self.session_id}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while True:
            try:
                self.step()
            except Exception:
                log.exception("session %d: tick failed", self.session_id)
            next_at += self._tick_period_s
            delay = next_at - loop.time()
            if delay < 0:  # fell behind: don't try to catch up with a burst of ticks
                next_at, delay = loop.time(), 0.0
            await asyncio.sleep(delay)

    # ----- the tick -----

    def step(self) -> Snapshot:
        started = time.perf_counter()
        # 0. Pass automation: slow a fast clock before a booked pass, turn the antenna to it.
        sim_time = self._automate_clock(self.clock.now())
        antenna, messages = self.automation.prepare(sim_time)
        for message in messages:
            self._publish(message)
        if antenna is not None and antenna != self.selected:
            self._select(antenna)
        fleet = self._fleet()
        now_epoch = epochs(sim_time)

        # 1. The whole fleet, from the station.
        above = np.zeros(len(fleet), dtype=bool)
        fleet_look: Observation | None = None
        if fleet.array is not None:
            fleet_look = observe(fleet.array, now_epoch, self._station)
            with np.errstate(invalid="ignore"):
                above = fleet_look.ok[:, 0] & (fleet_look.elevation_deg[:, 0] >= self._mask_deg)
        min_elev = max(self._mask_deg, self.filter.min_elevation_deg)
        shown = above & self._allowed_rows(fleet)
        if fleet_look is not None:
            with np.errstate(invalid="ignore"):
                shown &= fleet_look.elevation_deg[:, 0] >= min_elev
            if self.filter.link_ok_only:
                shown &= self._link_ok_rows(fleet, fleet_look)

        # 2. What to stream.
        targets = self._targets(fleet, np.flatnonzero(shown))
        states = self._states(targets, fleet, fleet_look, now_epoch)

        # 3. Geometry the views draw along.
        streamed = {s.norad_id for s in states}
        self._update_arcs(
            targets,
            fleet.links,
            sim_time,
            streamed,
            {s.norad_id for s in states if s.above_mask},
        )
        for target in targets:
            if target.pinned and target.norad_id in streamed:
                elements = target.elements or self._lookup(target.norad_id)
                if elements is not None:
                    self._maybe_update_track(elements, sim_time)

        # 4. Rising soon.
        busy = time.perf_counter() - started > _SWEEP_BUDGET_SHARE * self._tick_period_s
        self._update_upcoming(fleet, sim_time, above, advance=not busy)

        # 5. The selection's systems, the link with it, and the check at a booked pass's AOS.
        health = self._selected_health(fleet.links, sim_time)
        for message in self.comms.step(
            sim_time,
            self._contact_look(),
            health,
            fleet.comms,
            time_scale=self.clock.time_scale,
            paused=self.clock.paused,
        ):
            self._publish(message)
        messages, released = self.automation.step(sim_time, health, self.comms.contact)
        for message in messages:
            self._publish(message)
        for command in released:  # sent on the next tick in contact
            self._publish(self.comms.submit(command))

        snapshot = Snapshot(
            session_id=self.session_id,
            tick=self.tick,
            sim_time=sim_time,
            wall_time=wall_now(),
            clock=ClockState(time_scale=self.clock.time_scale, paused=self.clock.paused),
            observer=self.observer,
            visible_count=int(above.sum()),
            satellites=states,
            health=health,
            contact=self.comms.contact,
            automation=self.automation.state(sim_time),
        )
        self.tick += 1
        self._publish(snapshot)
        self._log_if_slow(time.perf_counter() - started, len(fleet))
        return snapshot

    def _automate_clock(self, sim_time: datetime) -> datetime:
        catch = self.automation.clock_catch(
            sim_time,
            time_scale=self.clock.time_scale,
            paused=self.clock.paused,
            tick_s=self._tick_period_s,
        )
        if catch is None:
            return sim_time
        if catch.jump_to is not None:
            self.clock.jump_to(catch.jump_to)
        self.clock.set_time_scale(1.0)
        return self.clock.now()

    def _allowed_rows(self, fleet: Fleet) -> NDArray[np.bool_]:
        cached = self._allowed
        if cached is not None and cached[0] == fleet.version and cached[1] is self.filter:
            return cached[2]
        allowed = fleet.type_mask(self.filter.types)
        if self.filter.sar_only:
            allowed &= fleet.sar
        if self.filter.link_ok_only:
            allowed &= fleet.links.has_profile
        if self.filter.comms_only:
            allowed &= fleet.comms_mask()
        self._allowed = (fleet.version, self.filter, allowed)
        return allowed

    def _link_ok_rows(self, fleet: Fleet, look: Observation) -> NDArray[np.bool_]:
        """Per fleet row: has a link profile and, now, a margin at or above the amber threshold."""
        ok = np.zeros(len(fleet), dtype=bool)
        links = fleet.links
        rows = links.rows
        if len(rows):
            ev = links.evaluate(
                links.profile_of_row[rows],
                range_m=look.range_m[rows, 0],
                range_rate_m_s=look.range_rate_m_s[rows, 0],
                elevation_deg=look.elevation_deg[rows, 0],
                off_nadir_deg=off_nadir_deg(look.r_itrf_m[rows, 0], self._station.itrf_m),
                mask_deg=self._mask_deg,
                thresholds=self._thresholds,
            )
            ok[rows] = ev.rag != RED
        return ok

    def _targets(self, fleet: Fleet, shown_rows: Iterable[int]) -> list[_Target]:
        targets: dict[int, _Target] = {}
        for norad_id in self.pinned:
            row = fleet.index.get(norad_id)
            if row is not None:
                targets[norad_id] = _Target(norad_id, fleet.satrecs[row], row=row, pinned=True)
            elif (elements := self._lookup(norad_id)) is not None:
                # resolved (e.g. from a remote search) but not in the fleet yet
                targets[norad_id] = _Target(
                    norad_id, elements.satellite.model, elements=elements, pinned=True
                )
        for row in shown_rows:
            norad_id = int(fleet.norad_ids[row])
            if norad_id not in targets:
                targets[norad_id] = _Target(norad_id, fleet.satrecs[int(row)], row=int(row))
        return list(targets.values())

    def _states(
        self,
        targets: list[_Target],
        fleet: Fleet,
        fleet_look: Observation | None,
        now_epoch: Epochs,
    ) -> list[SatelliteState]:
        in_fleet = [t for t in targets if t.row is not None]
        extras = [t for t in targets if t.row is None]
        looks: list[tuple[_Target, Observation, int]] = []
        if fleet_look is not None:
            looks += [(t, fleet_look, t.row) for t in in_fleet if t.row is not None]
        if extras:
            extra_look = observe(SatrecArray([t.satrec for t in extras]), now_epoch, self._station)
            looks += [(t, extra_look, i) for i, t in enumerate(extras)]
        self._selected_look = None
        if not looks:
            return []

        r_itrf = np.stack([obs.r_itrf_m[i, 0] for _, obs, i in looks])
        lat, lon, alt = geodetic(r_itrf)
        links = self._link_states(fleet.links, looks, r_itrf)

        states: list[SatelliteState] = []
        for k, (target, obs, i) in enumerate(looks):
            values = (
                float(obs.azimuth_deg[i, 0]),
                float(obs.elevation_deg[i, 0]),
                float(obs.range_m[i, 0]),
                float(obs.range_rate_m_s[i, 0]),
                float(lat[k]),
                float(lon[k]),
                float(alt[k]),
            )
            if not all(math.isfinite(v) for v in values):
                if target.pinned:
                    self._report_failure(target)
                continue
            self._failed.discard(target.norad_id)
            az, el, rng, rate, la, lo, h = values
            if target.row is not None:
                name = fleet.names[target.row]
                kind = fleet.type_of(target.row)
                sar = bool(fleet.sar[target.row])
                tle_epoch = fleet.tle_epochs[target.row]
            else:
                assert target.elements is not None
                name, tle_epoch = target.elements.name, target.elements.epoch
                c = classify(name, target.norad_id)
                kind, sar = c.type, c.is_sar_capable
            states.append(
                SatelliteState(
                    norad_id=target.norad_id,
                    name=name,
                    type=kind,
                    is_sar_capable=sar,
                    tle_epoch=tle_epoch,
                    lat_deg=round(la, 5),
                    lon_deg=round(lo, 5),
                    alt_m=round(h, 1),
                    azimuth_deg=round(az, 4),
                    elevation_deg=round(el, 4),
                    range_m=round(rng, 1),
                    range_rate_m_s=round(rate, 3),
                    above_mask=el >= self._mask_deg,
                    link=links.get(k),
                )
            )
            if target.norad_id == self.selected:
                off_nadir = off_nadir_deg(r_itrf[k], self._station.itrf_m)
                self._selected_look = _Look(
                    target.satrec,
                    kind,
                    el,
                    el >= self._mask_deg,
                    rng,
                    float(off_nadir),
                    links.get(k),
                )
        return states

    def _link_states(
        self,
        links: LinkTable,
        looks: list[tuple[_Target, Observation, int]],
        r_itrf: NDArray[np.float64],
    ) -> dict[int, LinkState]:
        """Link state per entry of ``looks`` that has a profile (keyed by position in ``looks``)."""
        with_profile = [
            (k, links.by_norad[t.norad_id])
            for k, (t, _, _) in enumerate(looks)
            if t.norad_id in links.by_norad
        ]
        if not with_profile:
            return {}
        ks = [k for k, _ in with_profile]
        ev = links.evaluate(
            [p for _, p in with_profile],
            range_m=np.array([looks[k][1].range_m[looks[k][2], 0] for k in ks]),
            range_rate_m_s=np.array([looks[k][1].range_rate_m_s[looks[k][2], 0] for k in ks]),
            elevation_deg=np.array([looks[k][1].elevation_deg[looks[k][2], 0] for k in ks]),
            off_nadir_deg=off_nadir_deg(r_itrf[ks], self._station.itrf_m),
            mask_deg=self._mask_deg,
            thresholds=self._thresholds,
        )
        station_radius = float(np.linalg.norm(self._station.itrf_m))
        result: dict[int, LinkState] = {}
        for j, (k, p) in enumerate(with_profile):
            profile = links.profiles[p]
            margin, ebn0, doppler = ev.margin_db[j], ev.ebn0_db[j], ev.doppler_hz[j]
            result[k] = LinkState(
                frequency_hz=profile.frequency_hz,
                data_rate_bps=profile.data_rate_bps,
                ebn0_db=round(float(ebn0), 2) if math.isfinite(ebn0) else None,
                margin_db=round(float(margin), 2) if math.isfinite(margin) else None,
                doppler_hz=round(float(doppler), 1) if math.isfinite(doppler) else 0.0,
                rag=RAG_NAMES[int(ev.rag[j])],  # type: ignore[arg-type]
                strong_link_elev_bands_deg=self._strong_link_bands(
                    links, p, float(np.linalg.norm(r_itrf[k])), station_radius
                ),
            )
        return result

    def _strong_link_bands(
        self, links: LinkTable, profile: int, sat_radius_m: float, station_radius_m: float
    ) -> list[tuple[float, float]]:
        key = (links.version, profile, round(sat_radius_m / _BAND_RADIUS_STEP_M))
        bands = self._bands.get(key)
        if bands is None:
            if len(self._bands) >= _BAND_CACHE_SIZE:
                self._bands.clear()
            bands = strong_link_bands(
                links.profiles[profile],
                key[2] * _BAND_RADIUS_STEP_M,
                station_radius_m,
                self._mask_deg,
                self._thresholds,
            )
            self._bands[key] = bands
        return bands

    def _contact_look(self) -> ContactLook | None:
        look = self._selected_look
        if look is None or self.selected is None:
            return None
        return ContactLook(
            norad_id=self.selected,
            elevation_deg=look.elevation_deg,
            above_mask=look.above_mask,
            range_m=look.range_m,
            margin_db=look.link.margin_db if look.link is not None else None,
            has_link=look.link is not None,
        )

    def _selected_health(self, links: LinkTable, sim_time: datetime) -> HealthState | None:
        look = self._selected_look
        if look is None or self.selected is None:
            return None
        try:
            return self._health.state(
                norad_id=self.selected,
                satrec=look.satrec,
                kind=look.kind,
                links=links,
                sim_time=sim_time,
                elevation_deg=look.elevation_deg,
                off_nadir=look.off_nadir_deg,
                link=look.link,
            )
        except Exception:
            log.exception("session %d: health of %s failed", self.session_id, self.selected)
            return None

    def _report_failure(self, target: _Target) -> None:
        if target.norad_id not in self._failed:
            self._failed.add(target.norad_id)
            self.publish_error(
                f"SGP4 could not propagate NORAD {target.norad_id} at this time; "
                f"its elements may be decayed or invalid."
            )

    # ----- sky arcs -----

    def _update_arcs(
        self,
        targets: list[_Target],
        links: LinkTable,
        sim_time: datetime,
        streamed: set[int],
        in_view: set[int],
    ) -> None:
        # Arcs follow what is in view, plus pinned satellites (their next pass, if below).
        wanted = {
            t.norad_id: t
            for t in targets
            if t.norad_id in streamed and (t.pinned or t.norad_id in in_view)
        }
        self._arcs = {n: a for n, a in self._arcs.items() if n in wanted}

        stale = [
            t
            for t in sorted(wanted.values(), key=lambda t: not t.pinned)  # pinned first
            if not self._arc_valid(self._arcs.get(t.norad_id), t.satrec, links, sim_time)
        ][: self.config.max_arcs_per_tick]
        if not stale:
            return
        arcs = pass_arcs(
            [t.satrec for t in stale],
            self._station,
            sim_time,
            self._mask_deg,
            lookback_s=_ARC_LOOKBACK_S,
            lookahead_s=_ARC_LOOKAHEAD_S,
            step_s=self.config.pass_search_step_s,
            n_points=self.config.pass_arc_points,
        )
        for target, arc in zip(stale, arcs, strict=True):
            message = None
            if arc is not None:
                message = PassArcMessage(
                    norad_id=target.norad_id,
                    aos_time=arc.aos,
                    los_time=arc.los,
                    max_elev_deg=round(arc.max_elev_deg, 3),
                    start_time=arc.start,
                    end_time=arc.end,
                    points=arc.points,
                    rag=self._arc_rag(links, target.norad_id, arc),
                )
                self._publish(message)
            self._arcs[target.norad_id] = _ArcState(
                epoch_key=_epoch_key(target.satrec),
                links_version=links.version,
                computed_at=sim_time,
                valid_until=arc.end
                if arc is not None
                else sim_time + timedelta(seconds=_ARC_RETRY_S),
                message=message,
            )

    def _arc_rag(self, links: LinkTable, norad_id: int, arc: Arc) -> list[Rag] | None:
        """The link state at each arc point: the same evaluation as the snapshot's."""
        profile = links.by_norad.get(norad_id)
        if profile is None or not arc.points:
            return None
        ev = links.evaluate(
            np.full(len(arc.points), profile),
            range_m=arc.range_m,
            range_rate_m_s=arc.range_rate_m_s,
            elevation_deg=np.array([el for _, el in arc.points]),
            off_nadir_deg=arc.off_nadir_deg,
            mask_deg=self._mask_deg,
            thresholds=self._thresholds,
        )
        return [RAG_NAMES[int(r)] for r in ev.rag]  # type: ignore[misc]

    @staticmethod
    def _arc_valid(
        state: _ArcState | None, satrec: Satrec, links: LinkTable, sim_time: datetime
    ) -> bool:
        return (
            state is not None
            and state.epoch_key == _epoch_key(satrec)
            and state.links_version == links.version
            and state.computed_at <= sim_time + timedelta(seconds=1)  # no backwards jump
            and sim_time <= state.valid_until
        )

    # ----- ground tracks -----

    def _maybe_update_track(self, elements: Elements, sim_time: datetime) -> None:
        state = self._tracks.get(elements.norad_id)
        if state is not None and state.elements is elements:
            offset = sim_time - state.center
            limit = timedelta(seconds=elements.period_s * _TRACK_REFRESH_ORBITS)
            if timedelta(0) <= offset <= limit:
                return
        track = ground_track(
            elements, sim_time, self.config.ground_track_points, self._station, self._mask_deg
        )
        message = GroundTrackMessage(
            norad_id=elements.norad_id,
            start_time=track.start,
            end_time=track.end,
            period_s=elements.period_s,
            points=track.points,
            alt_m=track.alt_m,
            above_mask=track.above_mask,
        )
        self._tracks[elements.norad_id] = _TrackState(elements, sim_time, message)
        self._publish(message)

    # ----- rising soon -----

    def _update_upcoming(
        self, fleet: Fleet, sim_time: datetime, above: NDArray[np.bool_], *, advance: bool
    ) -> None:
        too_fast = (
            not self.clock.paused and self.clock.time_scale > self.config.upcoming_max_time_scale
        )
        completed = False
        if advance and not too_fast:
            sim_s_per_tick = (
                0.0 if self.clock.paused else self.clock.time_scale * self._tick_period_s
            )
            completed = self._sweep.advance(fleet, sim_time, sim_s_per_tick)
        status = (
            "paused"
            if too_fast
            else "ready"
            if self._sweep.valid_for(fleet, sim_time)
            else "computing"
        )
        due = time.monotonic() - self._upcoming_published_at >= _UPCOMING_REPUBLISH_S
        previous = self._upcoming
        if not (
            completed
            or self._upcoming_dirty
            or previous is None
            or previous.status != status
            or (status == "ready" and due)
        ):
            return

        passes: list[UpcomingPass] = []
        total = 0
        result = self._sweep.result
        if status == "ready" and result is not None:
            rises, total = self._sweep.query(
                sim_time,
                allowed_rows=self._allowed_rows(fleet),
                exclude_rows=above,
                within_s=self.filter.rising_within_min * 60,
                min_max_elev_deg=max(self._mask_deg, self.filter.min_elevation_deg),
                limit=self.config.upcoming_limit,
            )
            passes = [
                UpcomingPass(
                    norad_id=int(fleet.norad_ids[r.row]),
                    name=fleet.names[r.row],
                    type=fleet.type_of(r.row),
                    is_sar_capable=bool(fleet.sar[r.row]),
                    aos_time=r.aos,
                    aos_az_deg=r.aos_az_deg,
                    max_elev_deg=r.max_elev_deg,
                )
                for r in rises
            ]
        window_start = result.start if status == "ready" and result is not None else None
        message = UpcomingMessage(
            status=status,
            window_start=window_start,
            window_end=(
                window_start + timedelta(minutes=self.config.upcoming_window_min)
                if window_start is not None
                else None
            ),
            total=total,
            passes=passes,
        )
        self._upcoming = message
        self._upcoming_dirty = False
        self._upcoming_published_at = time.monotonic()
        self._publish(message)

    def _log_if_slow(self, elapsed_s: float, fleet_size: int) -> None:
        if elapsed_s > 0.5 * self._tick_period_s:
            now = time.monotonic()
            if now - self._slow_logged_at >= _SLOW_TICK_LOG_INTERVAL_S:
                self._slow_logged_at = now
                log.warning(
                    "session %d: tick took %.0f ms (fleet %d, period %.0f ms)",
                    self.session_id,
                    elapsed_s * 1000,
                    fleet_size,
                    self._tick_period_s * 1000,
                )
