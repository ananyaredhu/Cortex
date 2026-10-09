"""The layered answer checker (ADR-003). Layer 1 is deterministic and always runs; layer 2 is a local grounding model."""
from .layer1 import CheckResult, check
from .layer2 import CrossEncoderScorer, FakeScorer, GroundingScorer, Layer2Result, check_layer2, scorer_from_env

__all__ = ["CheckResult", "CrossEncoderScorer", "FakeScorer", "GroundingScorer", "Layer2Result", "check", "check_layer2",
           "scorer_from_env"]
