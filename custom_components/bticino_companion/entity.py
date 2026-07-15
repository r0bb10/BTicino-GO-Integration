"""Shared Companion entity behavior."""

from __future__ import annotations


class CompanionAvailabilityMixin:
    """Make entity availability follow the push transport."""

    @property
    def available(self) -> bool:
        return self.coordinator.runtime.connected
