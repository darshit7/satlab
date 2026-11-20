# Simulation

The physics behind every number on screen, one model per page. Everything here runs on the server
in `backend/app/sim/`. These modules are pure functions where possible (testable without a database
or network), vectorized with numpy, and checked against references: skyfield for geometry and passes,
JPL ephemerides for the Sun, and each source's own worked link margins.

| Page | Question it answers | Real or modelled |
| ---- | ------------------- | ---------------- |
| [Orbits](orbits.md) | Where is every satellite now? | real: TLE + SGP4 |
| [Geometry](geometry.md) | Where is it in my sky, and over which point on Earth? | real |
| [Passes](passes.md) | When does it rise, peak and set? What rises soon? | real |
| [Link budget](link-budget.md) | How good is its downlink at my station? | real (published budgets) |
| [Subsystems](subsystems.md) | Is its battery, temperature, storage, pointing OK? | modelled, driven by real geometry |
| [Communications](comms.md) | What happens to a frame or a command in transit? | light time real; uplink simulated; bit errors modelled |
| [Pass automation](automation.md) | What happens to a command booked for a future pass? | simulated workflow |
