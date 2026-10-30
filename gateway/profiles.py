# gateway/profiles.py
# Decoders for the native EV BMS frames that arrive on the OEM side CAN bus.
#
# Only the demo profile exists. Its layout is made up for this project and does NOT match
# any real vehicle. A real vehicle needs its own profile, written from that vehicle's
# CAN documentation or from the Battery Emulator source. To add one, write a class with
# the same decode method and register it in PROFILES.
import struct
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class BmsSnapshot:
    pack_voltage_v: float = 0.0
    current_a: float = 0.0
    soc_pct: float = 0.0
    soh_pct: float = 0.0
    temp_c: float = 0.0
    cell_max_mv: float = 0.0
    cell_min_mv: float = 0.0
    iso_fault: bool = False
    have_pack: bool = False
    have_cells: bool = False

    @property
    def complete(self) -> bool:
        return self.have_pack and self.have_cells

    @property
    def delta_mv(self) -> float:
        return max(0.0, self.cell_max_mv - self.cell_min_mv)


class DemoProfile:
    """Synthetic layout, little endian, 8 bytes.

    0x700 pack:  voltage 0.1 V (u16), current 0.1 A (i16), SOC 0.1 percent (u16),
                 SOH percent (u8), flags (u8, bit 0 is the BMS isolation fault)
    0x701 cells: highest cell mV (u16), lowest cell mV (u16), temperature 0.1 C (i16), 2 pad bytes
    """

    name = "demo"
    ID_PACK = 0x700
    ID_CELLS = 0x701
    FLAG_ISO_FAULT = 0x01

    @staticmethod
    def encode_pack(pack_voltage_v: float, current_a: float, soc_pct: float, soh_pct: float, iso_fault: bool) -> bytes:
        flags = DemoProfile.FLAG_ISO_FAULT if iso_fault else 0
        return struct.pack(
            "<HhHBB",
            max(0, min(0xFFFF, round(pack_voltage_v * 10))),
            max(-0x8000, min(0x7FFF, round(current_a * 10))),
            max(0, min(0xFFFF, round(soc_pct * 10))),
            max(0, min(255, round(soh_pct))),
            flags,
        )

    @staticmethod
    def encode_cells(cell_max_mv: float, cell_min_mv: float, temp_c: float) -> bytes:
        return struct.pack(
            "<HHh2x",
            max(0, min(0xFFFF, round(cell_max_mv))),
            max(0, min(0xFFFF, round(cell_min_mv))),
            max(-0x8000, min(0x7FFF, round(temp_c * 10))),
        )

    def decode(self, can_id: int, data: bytes, snap: BmsSnapshot) -> Optional[str]:
        """Update the snapshot from one frame. Returns a readable summary, or None if the frame is not ours."""
        if len(data) != 8:
            return None
        if can_id == self.ID_PACK:
            volt, cur, soc, soh, flags = struct.unpack("<HhHBB", data)
            snap.pack_voltage_v = volt / 10
            snap.current_a = cur / 10
            snap.soc_pct = soc / 10
            snap.soh_pct = float(soh)
            snap.iso_fault = bool(flags & self.FLAG_ISO_FAULT)
            snap.have_pack = True
            return (
                f"V_pack={snap.pack_voltage_v:.1f} V, I={snap.current_a:.1f} A, SOC={snap.soc_pct:.1f} %, "
                f"SOH={snap.soh_pct:.0f} %, BMS isolation fault={'yes' if snap.iso_fault else 'no'}"
            )
        if can_id == self.ID_CELLS:
            cmax, cmin, temp = struct.unpack("<HHh2x", data)
            snap.cell_max_mv = float(cmax)
            snap.cell_min_mv = float(cmin)
            snap.temp_c = temp / 10
            snap.have_cells = True
            return f"V_cell_max={cmax} mV, V_cell_min={cmin} mV, T={snap.temp_c:.1f} C"
        return None


PROFILES: Dict[str, type] = {DemoProfile.name: DemoProfile}


def get_profile(name: str):
    try:
        return PROFILES[name]()
    except KeyError:
        raise ValueError(f"unknown BMS profile '{name}', available: {', '.join(sorted(PROFILES))}") from None
