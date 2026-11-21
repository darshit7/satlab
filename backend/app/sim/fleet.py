"""The fleet: every catalogued satellite, ready for vectorized propagation.

An immutable bundle of parallel arrays plus one ``SatrecArray``, the link profiles aligned with its
rows and the satellites' packet templates. The catalog service builds a new one after each ingest
and swaps it in with a single assignment; the sim loop only ever reads it.
"""

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from sgp4.api import Satrec, SatrecArray

from app.models.ws import SATELLITE_TYPES, SatelliteType
from app.packets.table import CommsTable, Downlink
from app.packets.templates import CommandTemplate, TelemetryTemplate
from app.sim.linkbudget import LinkProfile, LinkTable
from app.sim.propagate import jd_to_datetime

_versions = itertools.count(1)
TYPE_CODE: dict[SatelliteType, int] = {t: i for i, t in enumerate(SATELLITE_TYPES)}


@dataclass(frozen=True)
class FleetRow:
    """One catalogued satellite as the fleet is built from it (a ``satellite`` row with a TLE)."""

    norad_id: int
    name: str
    type: SatelliteType
    is_sar_capable: bool
    line1: str
    line2: str


@dataclass(frozen=True, eq=False)
class Fleet:
    """The whole catalog as arrays, aligned by row, for vectorized propagation in the tick.

    Immutable: rebuilt from the DB after each ingest and swapped in with one assignment, so the
    loop never sees a half-built fleet. ``links`` and ``comms`` are aligned with the same rows.
    """

    version: int  #: changes whenever the catalog is rebuilt
    norad_ids: NDArray[np.int64]  #: per row
    names: tuple[str, ...]
    type_codes: NDArray[np.int8]
    sar: NDArray[np.bool_]
    tle_epochs: tuple[datetime, ...]
    satrecs: tuple[Satrec, ...]
    index: dict[int, int] = field(repr=False)  #: NORAD id → row
    array: SatrecArray | None = field(repr=False)  #: every row at once; None when empty
    links: LinkTable = field(repr=False)
    comms: CommsTable = field(repr=False, default_factory=CommsTable)

    @classmethod
    def build(
        cls,
        rows: Iterable[FleetRow],
        profiles: Iterable[LinkProfile] = (),
        templates: Iterable[tuple[int, int | None, TelemetryTemplate | CommandTemplate]] = (),
        downlinks: Mapping[int, Downlink] | None = None,
        *,
        generic_antenna_deg: float | None = None,
        no_radio: Iterable[int] = (),
    ) -> "Fleet":
        """Parse the rows' TLEs (skipping duplicates and sets SGP4 rejects) and align the link
        profiles and packet templates with the rows that remain.

        ``no_radio``: debris and rocket bodies, which the generic packet templates leave out.
        """
        kept: list[tuple[FleetRow, Satrec]] = []
        seen: set[int] = set()
        for row in rows:
            if row.norad_id in seen:
                continue
            try:
                satrec = Satrec.twoline2rv(row.line1, row.line2)
            except ValueError:
                continue
            if satrec.error or satrec.no_kozai <= 0:
                continue
            seen.add(row.norad_id)
            kept.append((row, satrec))
        satrecs = tuple(s for _, s in kept)
        index = {r.norad_id: i for i, (r, _) in enumerate(kept)}
        return cls(
            version=next(_versions),
            norad_ids=np.array([r.norad_id for r, _ in kept], dtype=np.int64),
            names=tuple(r.name for r, _ in kept),
            type_codes=np.array([TYPE_CODE[r.type] for r, _ in kept], dtype=np.int8),
            sar=np.array([r.is_sar_capable for r, _ in kept], dtype=np.bool_),
            tle_epochs=tuple(jd_to_datetime(s.jdsatepoch, s.jdsatepochF) for s in satrecs),
            satrecs=satrecs,
            index=index,
            array=SatrecArray(list(satrecs)) if satrecs else None,
            links=LinkTable.build(profiles, index, len(kept)),
            comms=CommsTable.build(
                templates, downlinks, generic_antenna_deg=generic_antenna_deg, no_radio=no_radio
            ),
        )

    @classmethod
    def empty(cls) -> "Fleet":
        """A fleet with no satellites (before the first build)."""
        return cls.build(())

    def __len__(self) -> int:
        return len(self.satrecs)

    def type_of(self, i: int) -> SatelliteType:
        """The type of row ``i``."""
        return SATELLITE_TYPES[int(self.type_codes[i])]

    def subset(self, rows: Sequence[int] | NDArray[np.intp]) -> SatrecArray:
        """A ``SatrecArray`` of just these rows (to propagate a few satellites)."""
        return SatrecArray([self.satrecs[int(i)] for i in rows])

    def type_mask(self, types: Iterable[SatelliteType]) -> NDArray[np.bool_]:
        """Per row: the satellite's type is one of ``types``."""
        codes = [TYPE_CODE[t] for t in types]
        return np.isin(self.type_codes, codes)

    def comms_mask(self) -> NDArray[np.bool_]:
        """Per row: the satellite has packet templates of its own (the "own formats" filter;
        every other satellite but debris and rocket bodies uses the generic set)."""
        return np.isin(self.norad_ids, np.fromiter(self.comms.by_norad, dtype=np.int64))
