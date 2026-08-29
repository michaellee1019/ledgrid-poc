# LED Grid Wall HAT hardware package

This directory contains the supplied V4 editable EDA Standard JSON, manufacturing
evidence, and the Rev5 electrical/layout proposal derived from
`docs/plan/hardware-iteration.md`.

> **Release status:** Rev5 is a non-fabrication design proposal. Do not order,
> assemble, or connect it to hardware. The V4 editable exports reveal a critical
> schematic-to-PCB SPI discrepancy. A reviewed Rev5 source revision, ERC, DRC,
> final routed-length extraction, mechanical approval, and fresh manufacturing
> outputs are still required.

## Contents

- [`rev4/reference/`](rev4/reference/) — unchanged input EDA Standard schematic
  and PCB JSON, Gerber archive, and schematic PNG.
- [`rev4/evidence/`](rev4/evidence/) — reproducible fabrication audit, rendered
  V4 copper/silkscreen plots, and the verification report.
- [`rev5/proposal/`](rev5/proposal/) — the proposed architecture, exact bus and
  connector mappings, candidate BOM, fabrication requirements, and release
  gates.

The proposal makes the required architectural choice: receiver A uses SPI0 and
receiver B uses SPI1, with point-to-point buses and no breakout branches. It
also replaces the thermally marginal linear regulators, specifies a four-layer
return-path-controlled stackup, adds configurable source damping and test
points, pairs every LED data output with ground, and defines USB and antenna
layout requirements.

No live hardware was queried, powered, flashed, or otherwise operated while
producing this package.
