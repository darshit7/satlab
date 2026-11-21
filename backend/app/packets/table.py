"""Every satellite's packet templates, as the sim reads them: built with the fleet after each
catalog ingest and swapped in with it (immutable). Satellites without templates of their own share
one generic set (debris and rocket bodies excepted)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.packets.templates import CommandTemplate, SourceRef, TelemetryTemplate


@dataclass(frozen=True)
class Downlink:
    """A satellite's downlink frequency from the seed, with its sources."""

    frequency_hz: float
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class SatelliteTemplates:
    """One satellite's templates, each paired with its ``packet_template`` row id."""

    norad_id: int
    #: (packet_template.id, template)
    telemetry: tuple[tuple[int, TelemetryTemplate], ...]
    commands: tuple[tuple[int, CommandTemplate], ...]
    downlink: Downlink | None = None  #: seeded; satellites with a link profile use its frequency
    #: The shared generic set (the satellite has no templates of its own), not its own formats.
    generic: bool = False

    def telemetry_named(self, name: str) -> tuple[int, TelemetryTemplate] | None:
        """The telemetry template called ``name``, with its row id, if the satellite has it."""
        return next(((i, t) for i, t in self.telemetry if t.name == name), None)

    def command_named(self, name: str) -> tuple[int, CommandTemplate] | None:
        """The command template called ``name``, with its row id, if the satellite has it."""
        return next(((i, c) for i, c in self.commands if c.name == name), None)

    @property
    def periodic(self) -> tuple[tuple[int, TelemetryTemplate], ...]:
        """Templates the satellite sends on its own, on a cadence."""
        return tuple((i, t) for i, t in self.telemetry if t.interval_s is not None)

    @property
    def replayed(self) -> tuple[TelemetryTemplate, ...]:
        """Templates whose frames come from the SatNOGS archive."""
        return tuple(t for _, t in self.telemetry if t.downlink == "satnogs")


@dataclass(frozen=True)
class GenericTemplates:
    """The generic set: stored once, used by every satellite without templates of its own except
    debris and rocket bodies."""

    #: (packet_template.id, template), as in ``SatelliteTemplates``
    telemetry: tuple[tuple[int, TelemetryTemplate], ...]
    commands: tuple[tuple[int, CommandTemplate], ...]
    #: The assumed TT&C antenna's coverage off nadir (seed); None: the subsystem model's default.
    antenna_half_angle_deg: float | None = None


@dataclass(frozen=True, eq=False)
class CommsTable:
    """Every satellite's templates by NORAD id, and the generic set for the rest: what the comms
    runtime can talk to."""

    #: Satellites with templates of their own (the seed's list): the "own formats" ones.
    by_norad: Mapping[int, SatelliteTemplates] = field(default_factory=dict)
    generic: GenericTemplates | None = None  #: None: only ``by_norad`` can be contacted
    no_radio: frozenset[int] = frozenset()  #: debris and rocket bodies: no generic set for them

    @classmethod
    def build(
        cls,
        rows: Iterable[tuple[int, int | None, TelemetryTemplate | CommandTemplate]],
        downlinks: Mapping[int, Downlink] | None = None,
        *,
        generic_antenna_deg: float | None = None,
        no_radio: Iterable[int] = (),
    ) -> "CommsTable":
        """From (template id, NORAD ID, template) rows (NORAD ID None for the generic set), the
        seeded downlinks by NORAD ID, the generic set's antenna and the objects without a radio."""
        telemetry: dict[int | None, list[tuple[int, TelemetryTemplate]]] = {}
        commands: dict[int | None, list[tuple[int, CommandTemplate]]] = {}
        for template_id, norad_id, template in rows:
            telemetry.setdefault(norad_id, [])
            commands.setdefault(norad_id, [])
            if isinstance(template, TelemetryTemplate):
                telemetry[norad_id].append((template_id, template))
            else:
                commands[norad_id].append((template_id, template))
        # A set with no telemetry template has no comms.
        generic_telemetry = telemetry.pop(None, [])
        generic_commands = commands.pop(None, [])
        return cls(
            {
                n: SatelliteTemplates(
                    n, tuple(telemetry[n]), tuple(commands[n]), (downlinks or {}).get(n)
                )
                for n in telemetry
                if n is not None and telemetry[n]
            },
            GenericTemplates(tuple(generic_telemetry), tuple(generic_commands), generic_antenna_deg)
            if generic_telemetry
            else None,
            frozenset(no_radio),
        )

    def __len__(self) -> int:
        return len(self.by_norad)

    def for_norad(self, norad_id: int) -> SatelliteTemplates | None:
        """The templates comms uses for this satellite: its own, else the generic set (None for
        debris and rocket bodies, or without a generic set)."""
        own = self.by_norad.get(norad_id)
        if own is not None:
            return own
        if self.generic is None or norad_id in self.no_radio:
            return None
        return SatelliteTemplates(
            norad_id, self.generic.telemetry, self.generic.commands, generic=True
        )

    def antenna_half_angle_deg(self, norad_id: int) -> float | None:
        """The antenna coverage the generic set assumes, for a satellite that uses it."""
        if self.generic is None or norad_id in self.by_norad or norad_id in self.no_radio:
            return None
        return self.generic.antenna_half_angle_deg

    @property
    def replayed_ids(self) -> list[int]:
        """Satellites whose downlink comes (at least partly) from SatNOGS."""
        return sorted(n for n, s in self.by_norad.items() if s.replayed)
