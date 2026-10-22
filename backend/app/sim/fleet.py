"""The fleet: every catalogued satellite, ready for vectorized propagation.

An immutable bundle of parallel arrays plus one ``SatrecArray``, the link profiles aligned with its
rows and the satellites' packet templates. The catalog service builds a new one after each ingest
and swaps it in with a single assignment; the sim loop only ever reads it.
"""

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from sgp4.api import Satrec, SatrecArray

from app.models.ws import SATELLITE_TYPES, SatelliteType
from app.packets.table import CommsTable
from app.packets.templates import CommandTemplate, TelemetryTemplate
from app.sim.linkbudget import LinkProfile, LinkTable
from app.sim.propagate import jd_to_datetime

_versions = itertools.count(1)
TYPE_CODE: dict[SatelliteType, int] = {t: i for i, t in enumerate(SATELLITE_TYPES)}


@dataclass(frozen=True)
class FleetRow:
    norad_id: int
    name: str
    type: SatelliteType
    is_sar_capable: bool
    line1: str
    line2: str


@dataclass(frozen=True, eq=False)
class Fleet:
    version: int  # changes whenever the catalog is rebuilt
    norad_ids: NDArray[np.int64]
    names: tuple[str, ...]
    type_codes: NDArray[np.int8]
    sar: NDArray[np.bool_]
    tle_epochs: tuple[datetime, ...]
    satrecs: tuple[Satrec, ...]
    index: dict[int, int] = field(repr=False)
    array: SatrecArray | None = field(repr=False)
    links: LinkTable = field(repr=False)
    comms: CommsTable = field(repr=False, default_factory=CommsTable)

    @classmethod
    def build(
        cls,
        rows: Iterable[FleetRow],
        profiles: Iterable[LinkProfile] = (),
        templates: Iterable[tuple[int, int, TelemetryTemplate | CommandTemplate]] = (),
    ) -> "Fleet":
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
            comms=CommsTable.build(templates),
        )

    @classmethod
    def empty(cls) -> "Fleet":
        return cls.build(())

    def __len__(self) -> int:
        return len(self.satrecs)

    def type_of(self, i: int) -> SatelliteType:
        return SATELLITE_TYPES[int(self.type_codes[i])]

    def subset(self, rows: Sequence[int] | NDArray[np.intp]) -> SatrecArray:
        return SatrecArray([self.satrecs[int(i)] for i in rows])

    def type_mask(self, types: Iterable[SatelliteType]) -> NDArray[np.bool_]:
        codes = [TYPE_CODE[t] for t in types]
        return np.isin(self.type_codes, codes)
