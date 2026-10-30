# gateway/gpio.py
# GPIO for the two relay outputs and the optional inputs, using gpiozero.
# On a Pi the lgpio backend is used (chip 0 on older boards, chip 4 on a Pi 5, chosen by
# gpiozero). Where lgpio or a GPIO chip is missing, for example on a Mac, a mock pin
# factory takes over so everything still runs. A mock pin switches nothing in the real world.
import logging
from typing import Callable, Optional

from gpiozero import Device, DigitalInputDevice, OutputDevice
from gpiozero.pins.mock import MockFactory

from gateway.config import GpioConfig

log = logging.getLogger("gateway.gpio")


def _lgpio_factory():
    from gpiozero.pins.lgpio import LGPIOFactory
    return LGPIOFactory()


def init_gpio(force_mock: bool = False, loader: Optional[Callable] = None) -> str:
    """Select the pin factory. Returns 'lgpio' or 'mock'."""
    if not force_mock:
        try:
            Device.pin_factory = (loader or _lgpio_factory)()
            return "lgpio"
        except Exception as exc:
            log.warning("lgpio is not usable (%s). Falling back to the mock pin factory.", exc)
    Device.pin_factory = MockFactory()
    return "mock"


class RelayBoard:
    """The precharge and main contactor relay outputs, plus optional inputs."""

    def __init__(self, cfg: GpioConfig):
        self.precharge = OutputDevice(cfg.precharge_relay_pin, active_high=cfg.relays_active_high, initial_value=False)
        self.main = OutputDevice(cfg.main_relay_pin, active_high=cfg.relays_active_high, initial_value=False)
        self.enable_input = (
            DigitalInputDevice(cfg.enable_input_pin, pull_up=cfg.inputs_pull_up) if cfg.enable_input_pin >= 0 else None
        )
        self.iso_input = (
            DigitalInputDevice(cfg.iso_pulse_input_pin, pull_up=cfg.inputs_pull_up)
            if cfg.iso_pulse_input_pin >= 0
            else None
        )

    def apply(self, precharge_on: bool, main_on: bool) -> None:
        """Set the relays. The two are never on together."""
        if precharge_on and main_on:
            raise ValueError("precharge and main relay must not be on together")
        if main_on:
            self.precharge.off()
            self.main.on()
        else:
            self.main.off()
            if precharge_on:
                self.precharge.on()
            else:
                self.precharge.off()

    def all_off(self) -> None:
        self.main.off()
        self.precharge.off()

    def close(self) -> None:
        self.all_off()
        for dev in (self.precharge, self.main, self.enable_input, self.iso_input):
            if dev is not None:
                dev.close()
