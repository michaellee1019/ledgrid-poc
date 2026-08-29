# Primary design references

The component values and layout rules in this proposal were checked against
manufacturer or platform-owner documentation. Re-check document revisions at
source-capture time.

- [Raspberry Pi hardware and SPI documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html) — SPI0/SPI1 GPIO and physical-pin assignments.
- [Raspberry Pi HAT+ specification](https://datasheets.raspberrypi.com/hat/hat-plus-specification.pdf) — board mechanics, mounting, header, and clearance guidance.
- [Espressif ESP32-S3 hardware design guidelines](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32s3/) — supply capability, decoupling, `EN`, USB, RF/antenna, and PCB guidance.
- [ESP32-S3-WROOM-1/-1U datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf) — pinout, land pattern, RF keepout, and electrical limits.
- [TI SN74HCT125 datasheet](https://www.ti.com/lit/gpn/SN74HCT125) — HCT input threshold, bypassing, output-enable behavior, and package data.
- [Diodes AP2112 datasheet](https://www.diodes.com/datasheet/download/AP2112.pdf) — V4 regulator current, dropout, thermal resistance, stability, and capacitor requirements.
- [TI TPS62162 product data](https://www.ti.com/product/TPS62162) — proposed fixed-3.3 V, 1 A buck topology and reference layout.
- [TI TPD4E05U06 product data](https://www.ti.com/product/TPD4E05U06/part-details/TPD4E05U06DQAR) — optional four-channel LED-cable ESD candidate.
- [TI TPD2EUSB30 product data](https://www.ti.com/product/TPD2EUSB30) — two-channel native-USB ESD candidate.
- [3M 303 series connector drawing](https://multimedia.3m.com/mws/mediawebserver?mwsId=66666UuZjcFSLXTt4Xf_Lxs6EVuQEcuZgVs6EVs6E666666--) — proposed keyed 34-position LED data/ground header family.
