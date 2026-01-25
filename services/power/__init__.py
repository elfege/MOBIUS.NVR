"""
Power Management Services

This module provides power control functionality for cameras via smart home
integrations (Hubitat smart plugs, UniFi POE switches, etc.).

Classes:
    HubitatPowerService: Power cycling via Hubitat smart plugs
    UnifiPoePowerService: Power cycling via UniFi POE switches

Author: NVR System
Date: January 24, 2026
"""

from .hubitat_power_service import HubitatPowerService
from .unifi_poe_service import UnifiPoePowerService

__all__ = ['HubitatPowerService', 'UnifiPoePowerService']
