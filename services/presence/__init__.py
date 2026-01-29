"""
Presence Service Package

Provides household presence tracking with manual toggle and Hubitat integration.
"""

from .presence_service import PresenceService, PresenceStatus

__all__ = ['PresenceService', 'PresenceStatus']
