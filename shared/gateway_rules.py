# shared/gateway_rules.py
# The one implementation of the gateway rules: precharge hold, current derating,
# isolation test handling and protection cutoffs. It is a pure function of its inputs,
# so the API simulation and the Pi gateway give the same answer for the same inputs
# and tests need no hardware.
from dataclasses import dataclass
from typing import Tuple

from shared import can_frames as cf

# Project design value. Not taken from the Battery Emulator source.
PRECHARGE_HOLD_MS = 1500

# Spread between the highest and lowest cell above which charge and discharge are cut
IMBALANCE_CUTOFF_MV = 100.0

STATE_DISABLED = "DISABLED"
STATE_PRECHARGE = "PRECHARGE"
STATE_RUN = "RUN"

ALARM_NONE = "NONE"
ALARM_IMBALANCE = "CELL_IMBALANCE_CUTOFF (reported as system error bit 0x80 in 0x359 byte 1)"
ALARM_ISOLATION = "ISOLATION_FAULT (reported as system error bit 0x80 in 0x359 byte 1)"
ALARM_BMS_TIMEOUT = "BMS_TIMEOUT (reported as system error bit 0x80 in 0x359 byte 1)"


@dataclass(frozen=True)
class PackParams:
    nominal_kwh: float
    nominal_voltage: float
    cells_series: int
    c_rate: float = 0.20


@dataclass(frozen=True)
class GatewayInputs:
    soc_pct: float
    soh_pct: float
    temp_c: float
    pack_voltage_v: float
    delta_mv: float
    current_a: float = 0.0
    enable_high: bool = True
    ms_since_enable: int = 0
    iso_pulse_active: bool = False
    iso_fault_reported: bool = False
    imbalance_fault: bool = False
    bms_stale: bool = False


@dataclass(frozen=True)
class GatewayOutputs:
    state: str
    precharge_relay: bool
    main_relay: bool
    tx_enabled: bool
    max_charge_a: float
    max_discharge_a: float
    max_charge_v: float
    min_discharge_v: float
    protection_alarm: str
    prot2: int
    iso_masked: bool
    frames: Tuple[Tuple[int, bytes], ...]


def evaluate(pack: PackParams, inp: GatewayInputs) -> GatewayOutputs:
    if not inp.enable_high:
        state = STATE_DISABLED
    elif inp.ms_since_enable < PRECHARGE_HOLD_MS:
        state = STATE_PRECHARGE
    else:
        state = STATE_RUN
    tx_enabled = state == STATE_RUN

    # Current limits: 0.2C by default, halved outside 5 to 38 C, charge cut to a quarter
    # above 88 percent SOC, discharge cut to a quarter below 18 percent SOC
    if tx_enabled:
        base_a = round((pack.nominal_kwh * 1000.0 / pack.nominal_voltage) * pack.c_rate, 1)
        temp_derate = 0.5 if inp.temp_c > 38.0 or inp.temp_c < 5.0 else 1.0
        max_charge_a = round(base_a * temp_derate * (0.25 if inp.soc_pct > 88.0 else 1.0), 1)
        max_discharge_a = round(base_a * temp_derate * (0.25 if inp.soc_pct < 18.0 else 1.0), 1)
    else:
        max_charge_a = max_discharge_a = 0.0

    # The inverter isolation test and the BMS isolation message collide. While the test
    # pulse runs, the BMS isolation flag is masked. Outside a pulse it is a real fault.
    iso_masked = inp.iso_pulse_active
    iso_fault_active = inp.iso_fault_reported and not inp.iso_pulse_active

    prot2 = 0
    if inp.imbalance_fault or inp.delta_mv > IMBALANCE_CUTOFF_MV:
        alarm = ALARM_IMBALANCE
    elif iso_fault_active:
        alarm = ALARM_ISOLATION
    elif inp.bms_stale:
        alarm = ALARM_BMS_TIMEOUT
    else:
        alarm = ALARM_NONE
    if alarm != ALARM_NONE:
        prot2 |= cf.PROT2_SYSTEM_ERROR
        max_charge_a = max_discharge_a = 0.0

    max_charge_v = round(pack.cells_series * 4.12, 1)
    min_discharge_v = round(pack.cells_series * 3.20, 1)

    frames = (
        (cf.CAN_ID_LIMITS, cf.build_frame_351(max_charge_v, max_charge_a, max_discharge_a, min_discharge_v)),
        (cf.CAN_ID_SOC_SOH, cf.build_frame_355(inp.soc_pct, inp.soh_pct)),
        (cf.CAN_ID_MEASURE, cf.build_frame_356(inp.pack_voltage_v, inp.current_a, inp.temp_c)),
        (cf.CAN_ID_ALARMS, cf.build_frame_359(0, prot2, 0, 0)),
    )
    return GatewayOutputs(
        state=state,
        precharge_relay=state == STATE_PRECHARGE,
        main_relay=state == STATE_RUN,
        tx_enabled=tx_enabled,
        max_charge_a=max_charge_a,
        max_discharge_a=max_discharge_a,
        max_charge_v=max_charge_v,
        min_discharge_v=min_discharge_v,
        protection_alarm=alarm,
        prot2=prot2,
        iso_masked=iso_masked,
        frames=frames,
    )
