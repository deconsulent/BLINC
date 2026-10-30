# shared/can_frames.py
# The one implementation of the inverter side CAN frames. Used by the API simulation and
# by the Pi gateway. It only needs the standard library, so it runs on the Pi without
# the ML packages.
import struct
from typing import Dict

# Pylontech style frames, little endian, 8 bytes each.
# Layout checked against PYLON-LV-CAN.cpp in the Battery Emulator project.
# Differences from that file:
#  * Frames are padded to 8 bytes. The emulator sends shorter frames.
#  * 0x351 bytes 6 and 7 carry the discharge voltage limit, as in the Pylontech documents.
#  * 0x356 voltage is unsigned here. The low voltage spec uses int16, which stops at
#    327.67 V, and these packs run at 350 V and above.
#  * The protocol has no cell imbalance bit. An imbalance cutoff is reported as the
#    system error bit in 0x359 byte 1.
CAN_ID_LIMITS = 0x351
CAN_ID_SOC_SOH = 0x355
CAN_ID_MEASURE = 0x356
CAN_ID_ALARMS = 0x359
PROT2_SYSTEM_ERROR = 0x80
PACK_COUNT = 1


def _u16(value: float) -> int:
    return max(0, min(0xFFFF, int(round(value))))


def _s16(value: float) -> int:
    return max(-0x8000, min(0x7FFF, int(round(value))))


def build_frame_351(chg_v: float, chg_a: float, dis_a: float, dis_v: float) -> bytes:
    return struct.pack("<HHHH", _u16(chg_v * 10), _u16(chg_a * 10), _u16(dis_a * 10), _u16(dis_v * 10))


def build_frame_355(soc_pct: float, soh_pct: float) -> bytes:
    return struct.pack("<HH4x", _u16(soc_pct), _u16(soh_pct))


def build_frame_356(volt_v: float, current_a: float, temp_c: float) -> bytes:
    return struct.pack("<Hhh2x", _u16(volt_v * 100), _s16(current_a * 10), _s16(temp_c * 10))


def build_frame_359(prot1: int, prot2: int, alarm1: int, alarm2: int) -> bytes:
    return struct.pack("<BBBBB2sx", prot1, prot2, alarm1, alarm2, PACK_COUNT, b"PN")


def decode_frame(can_id: int, data: bytes) -> Dict:
    """Decode one of the four frames back to engineering units."""
    if len(data) != 8:
        raise ValueError("expected 8 bytes")
    if can_id == CAN_ID_LIMITS:
        cv, ca, da, dv = struct.unpack("<HHHH", data)
        return {"chg_v": cv / 10, "chg_a": ca / 10, "dis_a": da / 10, "dis_v": dv / 10}
    if can_id == CAN_ID_SOC_SOH:
        soc, soh = struct.unpack("<HH4x", data)
        return {"soc": soc, "soh": soh}
    if can_id == CAN_ID_MEASURE:
        volt, cur, temp = struct.unpack("<Hhh2x", data)
        return {"volt_v": volt / 100, "current_a": cur / 10, "temp_c": temp / 10}
    if can_id == CAN_ID_ALARMS:
        p1, p2, a1, a2, packs, tag = struct.unpack("<BBBBB2sx", data)
        return {"prot1": p1, "prot2": p2, "alarm1": a1, "alarm2": a2, "packs": packs, "tag": tag.decode("ascii")}
    raise ValueError(f"unknown CAN id {can_id:#x}")


def describe_frame(can_id: int, data: bytes) -> str:
    d = decode_frame(can_id, data)
    if can_id == CAN_ID_LIMITS:
        return f"V_chg_max={d['chg_v']:.1f} V, I_chg_max={d['chg_a']:.1f} A, I_dis_max={d['dis_a']:.1f} A, V_dis_min={d['dis_v']:.1f} V"
    if can_id == CAN_ID_SOC_SOH:
        return f"SOC={d['soc']} %, SOH={d['soh']} %"
    if can_id == CAN_ID_MEASURE:
        return f"V_pack={d['volt_v']:.2f} V, I={d['current_a']:.1f} A, T={d['temp_c']:.1f} C"
    return (
        f"prot1={d['prot1']:#04x}, prot2={d['prot2']:#04x}, alarm1={d['alarm1']:#04x}, "
        f"alarm2={d['alarm2']:#04x}, packs={d['packs']}, tag={d['tag']}"
    )


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)
