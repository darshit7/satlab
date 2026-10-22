"""Every satellite's packet templates, as the sim reads them: built with the fleet after each
catalog ingest and swapped in with it (immutable)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.packets.templates import CommandTemplate, TelemetryTemplate


@dataclass(frozen=True)
class SatelliteTemplates:
    norad_id: int
    # (packet_template.id, template)
    telemetry: tuple[tuple[int, TelemetryTemplate], ...]
    commands: tuple[tuple[int, CommandTemplate], ...]

    def telemetry_named(self, name: str) -> tuple[int, TelemetryTemplate] | None:
        return next(((i, t) for i, t in self.telemetry if t.name == name), None)

    def command_named(self, name: str) -> tuple[int, CommandTemplate] | None:
        return next(((i, c) for i, c in self.commands if c.name == name), None)

    @property
    def periodic(self) -> tuple[tuple[int, TelemetryTemplate], ...]:
        """Templates the satellite sends on its own, on a cadence."""
        return tuple((i, t) for i, t in self.telemetry if t.interval_s is not None)

    @property
    def replayed(self) -> tuple[TelemetryTemplate, ...]:
        """Templates whose frames come from the SatNOGS archive."""
        return tuple(t for _, t in self.telemetry if t.downlink == "satnogs")


@dataclass(frozen=True, eq=False)
class CommsTable:
    by_norad: Mapping[int, SatelliteTemplates] = field(default_factory=dict)

    @classmethod
    def build(
        cls, rows: Iterable[tuple[int, int, TelemetryTemplate | CommandTemplate]]
    ) -> "CommsTable":
        """From (template id, NORAD ID, template) rows."""
        telemetry: dict[int, list[tuple[int, TelemetryTemplate]]] = {}
        commands: dict[int, list[tuple[int, CommandTemplate]]] = {}
        for template_id, norad_id, template in rows:
            telemetry.setdefault(norad_id, [])
            commands.setdefault(norad_id, [])
            if isinstance(template, TelemetryTemplate):
                telemetry[norad_id].append((template_id, template))
            else:
                commands[norad_id].append((template_id, template))
        return cls(
            {
                n: SatelliteTemplates(n, tuple(telemetry[n]), tuple(commands[n]))
                for n in telemetry
                if telemetry[n]  # a satellite with no telemetry template has no comms
            }
        )

    def __len__(self) -> int:
        return len(self.by_norad)

    @property
    def replayed_ids(self) -> list[int]:
        """Satellites whose downlink comes (at least partly) from SatNOGS."""
        return sorted(n for n, s in self.by_norad.items() if s.replayed)
