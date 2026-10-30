# gateway/buses.py
# Opens the two CAN buses. Live mode uses SocketCAN interfaces (can0 and can1).
# Demo mode needs no hardware. It tries Linux vcan interfaces first and falls back to an
# in memory bus from the python can package when vcan is not available (a Mac, a container without
# the vcan kernel module, or no permission to create the interfaces).
import logging
import os
import platform
import subprocess
import uuid
from dataclasses import dataclass
from typing import List, Optional

import can

from gateway.config import Config

log = logging.getLogger("gateway.buses")


@dataclass
class BusSet:
    oem: can.BusABC          # the gateway reads native BMS frames here
    inverter: can.BusABC     # the gateway writes translated frames here
    label: str               # socketcan, vcan or memory
    bms_tx: Optional[can.BusABC] = None            # demo only: the fake BMS sends here
    inverter_monitor: Optional[can.BusABC] = None  # demo only: stands in for the inverter

    def all_buses(self) -> List[can.BusABC]:
        return [b for b in (self.oem, self.inverter, self.bms_tx, self.inverter_monitor) if b is not None]

    def close(self) -> None:
        for b in self.all_buses():
            try:
                b.shutdown()
            except Exception as exc:  # closing must never raise
                log.debug("bus shutdown: %s", exc)


def open_live(cfg: Config) -> BusSet:
    oem = can.Bus(interface=cfg.can.interface, channel=cfg.can.oem_channel)
    try:
        inverter = can.Bus(interface=cfg.can.interface, channel=cfg.can.inverter_channel)
    except Exception:
        oem.shutdown()
        raise
    return BusSet(oem=oem, inverter=inverter, label=cfg.can.interface)


def _run(cmd: List[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_vcan(names: List[str]) -> bool:
    """Make sure the vcan interfaces exist and are up. Needs root or passwordless sudo to create them."""
    if platform.system() != "Linux":
        return False
    prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
    if not all(os.path.exists(f"/sys/class/net/{n}") for n in names):
        _run(prefix + ["modprobe", "vcan"])
        for n in names:
            if not os.path.exists(f"/sys/class/net/{n}"):
                _run(prefix + ["ip", "link", "add", "dev", n, "type", "vcan"])
    for n in names:
        _run(prefix + ["ip", "link", "set", "up", n])
    return all(os.path.exists(f"/sys/class/net/{n}") and _vcan_usable(n) for n in names)


def _vcan_usable(name: str) -> bool:
    try:
        can.Bus(interface="socketcan", channel=name).shutdown()
        return True
    except Exception:
        return False


def open_demo(cfg: Config, force_memory: bool = False) -> BusSet:
    names = [cfg.demo.vcan_oem, cfg.demo.vcan_inverter]
    if not force_memory and ensure_vcan(names):
        try:
            return BusSet(
                oem=can.Bus(interface="socketcan", channel=names[0]),
                inverter=can.Bus(interface="socketcan", channel=names[1]),
                bms_tx=can.Bus(interface="socketcan", channel=names[0]),
                inverter_monitor=can.Bus(interface="socketcan", channel=names[1]),
                label="vcan",
            )
        except Exception as exc:
            log.warning("vcan interfaces could not be opened (%s). Using the in memory bus.", exc)
    else:
        log.info("vcan is not available. Using the in memory bus.")
    tag = uuid.uuid4().hex[:8]
    oem_ch, inv_ch = f"demo_oem_{tag}", f"demo_inv_{tag}"
    return BusSet(
        oem=can.Bus(interface="virtual", channel=oem_ch),
        inverter=can.Bus(interface="virtual", channel=inv_ch),
        bms_tx=can.Bus(interface="virtual", channel=oem_ch),
        inverter_monitor=can.Bus(interface="virtual", channel=inv_ch),
        label="memory",
    )
