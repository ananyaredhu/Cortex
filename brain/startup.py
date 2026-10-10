"""Start-up checks: warn in development, refuse unsafe settings in production (BRAIN_ENV=production).

A forgotten environment variable should stop a deployment, not quietly weaken it. Everything that decides whether a
setting is unsafe lives in `Settings.production_problems` and `Settings.startup_warnings` (brain/config.py).
"""
import logging

from brain.config import Settings

log = logging.getLogger(__name__)


class UnsafeConfiguration(RuntimeError):
    """The settings are not safe for BRAIN_ENV=production."""


def check_startup(settings: Settings, *, fixture: bool = False) -> list[str]:
    """Log and return the warnings. In production, raise `UnsafeConfiguration` listing every problem instead."""
    if settings.production:
        problems = settings.production_problems()
        if fixture:
            problems.insert(0, "BRAIN_RUNTIME=fixture serves invented data with demo controls; it is not for production")
        if problems:
            raise UnsafeConfiguration("Refusing to start with BRAIN_ENV=production:\n- " + "\n- ".join(problems))
    warnings = [] if fixture else settings.startup_warnings()
    for warning in warnings:
        log.warning("%s", warning)
    return warnings
