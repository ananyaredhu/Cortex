"""The layered answer checker (ADR-003). Layer 1 is deterministic and always runs."""
from .layer1 import CheckResult, check

__all__ = ["CheckResult", "check"]
