# Supplied V4 reference source

These four files are unchanged copies of the user-supplied 2026-08-28 exports:

- `SCH_LedGridWallHatV4_2026-08-28.json` — editable EDA Standard schematic
- `PCB_LedGridWallHatV4_2026-08-28.json` — editable EDA Standard PCB
- `Schematic_LedGridWallHatV4_2026-08-28.png` — schematic visual reference
- `Gerber_LedGridWallHatV4_LedGridWallHatV4_2026-08-28.zip` — fabrication output

The source exports use editor version 6.5.57. They are retained as evidence, not
as a released manufacturing design. The schematic and PCB disagree on receiver
SPI topology: the schematic uses separate `1IO*`/`2IO*` data nets, while the PCB
assigns both modules to shared `1IO11`, `1IO12`, and `1IO13` nets. See the
evidence report and machine audit before editing either source.
