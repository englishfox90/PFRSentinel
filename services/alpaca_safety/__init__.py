"""ASCOM Alpaca SafetyMonitor served from Sentinel's confirmed roof verdict or its imaging-conditions grade."""
from .feed import AlpacaSafetyFeed, CONFIG_KEY
from .service import AlpacaSafetyService, alpaca_safety_service
from .state import SafetyState, safety_state
from .verdict_source import IMAGING_CONDITIONS, ROOF, SafetyVerdict, read_source

__all__ = [
    'AlpacaSafetyFeed', 'AlpacaSafetyService', 'CONFIG_KEY', 'IMAGING_CONDITIONS', 'ROOF',
    'SafetyState', 'SafetyVerdict', 'alpaca_safety_service', 'read_source', 'safety_state',
]
