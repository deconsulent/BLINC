# gateway/config.py
# Loads gateway/config.toml into plain dataclasses. Unknown keys are an error, so a typo
# in a pin name cannot be silently ignored.
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.toml")


@dataclass(frozen=True)
class CanConfig:
    interface: str = "socketcan"
    oem_channel: str = "can0"
    inverter_channel: str = "can1"
    tx_period_ms: int = 1000


@dataclass(frozen=True)
class GpioConfig:
    precharge_relay_pin: int = 17
    main_relay_pin: int = 27
    enable_input_pin: int = 22
    iso_pulse_input_pin: int = -1
    relays_active_high: bool = True
    inputs_pull_up: bool = False
    reserved_pins: List[int] = field(default_factory=lambda: [7, 8, 9, 10, 11, 23, 25])


@dataclass(frozen=True)
class PackConfig:
    nominal_kwh: float = 40.0
    nominal_voltage: float = 350.0
    cells_series: int = 96
    c_rate: float = 0.20


@dataclass(frozen=True)
class BmsConfig:
    profile: str = "demo"
    timeout_ms: int = 2000
    iso_mask_grace_ms: int = 500


@dataclass(frozen=True)
class HttpConfig:
    host: str = "0.0.0.0"
    port: int = 8100


@dataclass(frozen=True)
class DemoConfig:
    vcan_oem: str = "vcan0"
    vcan_inverter: str = "vcan1"
    iso_period_s: float = 10.0
    iso_pulse_s: float = 1.2
    enable_period_s: float = 30.0
    enable_drop_s: float = 2.0
    imbalance_period_s: float = 60.0
    imbalance_len_s: float = 8.0


@dataclass(frozen=True)
class Config:
    can: CanConfig = field(default_factory=CanConfig)
    gpio: GpioConfig = field(default_factory=GpioConfig)
    pack: PackConfig = field(default_factory=PackConfig)
    bms: BmsConfig = field(default_factory=BmsConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    demo: DemoConfig = field(default_factory=DemoConfig)


def _section(cls, raw: dict, name: str):
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown key(s) in [{name}]: {', '.join(sorted(unknown))}")
    return cls(**raw)


def validate(cfg: Config) -> Config:
    g = cfg.gpio
    used = {"precharge_relay_pin": g.precharge_relay_pin, "main_relay_pin": g.main_relay_pin}
    if g.enable_input_pin >= 0:
        used["enable_input_pin"] = g.enable_input_pin
    if g.iso_pulse_input_pin >= 0:
        used["iso_pulse_input_pin"] = g.iso_pulse_input_pin
    for name, pin in used.items():
        if not 0 <= pin <= 27:
            raise ValueError(f"{name}={pin} is not a BCM pin between 0 and 27")
        if pin in g.reserved_pins:
            raise ValueError(f"{name}={pin} is reserved for the CAN HAT (reserved_pins)")
    if len(set(used.values())) != len(used):
        raise ValueError("gpio pins must all be different")
    if cfg.can.oem_channel == cfg.can.inverter_channel:
        raise ValueError("oem_channel and inverter_channel must be different")
    if cfg.pack.cells_series <= 0 or cfg.pack.nominal_voltage <= 0 or cfg.pack.nominal_kwh <= 0:
        raise ValueError("pack values must be positive")
    if not 0 < cfg.pack.c_rate <= 1.0:
        raise ValueError("pack.c_rate must be above 0 and at most 1.0")
    return cfg


def load_config(path: Optional[str] = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(p, "rb") as handle:
        raw = tomllib.load(handle)
    sections = {"can": CanConfig, "gpio": GpioConfig, "pack": PackConfig, "bms": BmsConfig, "http": HttpConfig, "demo": DemoConfig}
    unknown = set(raw) - set(sections)
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(sorted(unknown))}")
    built = {name: _section(cls, raw.get(name, {}), name) for name, cls in sections.items()}
    return validate(Config(**built))
