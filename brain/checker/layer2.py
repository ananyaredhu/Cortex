"""Layer 2: a small grounding model, run locally, checks each claim against the evidence it cites (ADR-003).

Claim-versus-document verification with a cross-encoder from a different family than the generator: a MiniCheck
model, or a natural-language-inference model where "entailment" stands for "supported". The premise is the cited
evidence (only authorized text, already in the packet), the hypothesis is the claim. A claim whose best support
score is under the threshold is dropped; if none survive, the answer abstains.

`CHECKER_MODEL` names the model (`none` disables the layer). Timings per candidate: `python -m brain.checker.timing`.
"""
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

SUPPORTED_LABELS = ("entail", "support", "label_1", "1", "consistent")


class GroundingScorer(Protocol):
    model: str

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        """P(claim supported by premise) for each (premise, claim) pair, in order."""


@dataclass
class Layer2Result:
    claims: list[dict]
    removed_claims: int
    scores: list[int] = field(default_factory=list)        # best support score per input claim, in permille (ints survive jsonb)
    model: str = "none"
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.skipped:
            return "skipped"
        return "fail" if self.removed_claims else "pass"


class FakeScorer:
    """Supported when the claim's words mostly appear in the premise. Tests and the fixture runtime."""
    model = "fake-overlap"

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        out = []
        for premise, claim in pairs:
            words = set(re.findall(r"[a-z0-9]+", claim.lower())) - {"the", "a", "an", "is", "was", "and", "of", "to", "in"}
            have = set(re.findall(r"[a-z0-9]+", premise.lower()))
            out.append(len(words & have) / len(words) if words else 0.0)
        return out


class CrossEncoderScorer:
    """Any sequence-classification cross-encoder on the Hugging Face hub. The "supported" label is found by name
    (entailment, supported, LABEL_1); the model loads on first use into `HF_HOME`."""

    def __init__(self, model_name: str, *, max_length: int = 512, batch_size: int = 8) -> None:
        self.model = model_name
        self._max_length = max_length
        self._batch = batch_size
        self._encoder = None
        self._index: int | None = None

    def _load(self) -> None:
        from sentence_transformers import CrossEncoder
        self._encoder = CrossEncoder(self.model, max_length=self._max_length, device="cpu")
        labels = getattr(self._encoder.model.config, "id2label", None) or {}
        names = {int(k): str(v).lower() for k, v in labels.items()}
        for idx, name in sorted(names.items()):
            if any(tag in name for tag in SUPPORTED_LABELS):
                self._index = idx
                break
        if self._index is None:
            self._index = 1 if len(names) == 2 else 0

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        if self._encoder is None:
            self._load()
        import numpy as np
        logits = np.asarray(self._encoder.predict(pairs, batch_size=self._batch, apply_softmax=False, show_progress_bar=False))
        if logits.ndim == 1:                                   # single-logit models: a sigmoid score already
            return [float(x) if 0.0 <= x <= 1.0 else float(1 / (1 + np.exp(-x))) for x in logits]
        logits = logits - logits.max(axis=1, keepdims=True)
        probs = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
        return [float(p[self._index]) for p in probs]


def scorer_from_env(name: str | None = None) -> GroundingScorer | None:
    name = (name if name is not None else os.environ.get("CHECKER_MODEL") or "none").strip()
    if name.lower() in ("", "none", "off"):
        return None
    if name.lower() == "fake":
        return FakeScorer()
    return CrossEncoderScorer(name)


def check_layer2(claims: list[dict], evidence_text: dict[str, str], scorer: GroundingScorer | None, *,
                 threshold: float = 0.5, max_premise_chars: int = 2400) -> Layer2Result:
    """Keep the claims the model finds supported by at least one of their cited documents."""
    if scorer is None or not claims:
        return Layer2Result(list(claims), 0, [], "none", skipped=True)
    pairs: list[tuple[str, str]] = []
    owners: list[int] = []
    for i, claim in enumerate(claims):
        for doc_id in claim.get("citations", []):
            premise = evidence_text.get(doc_id, "")
            if premise:
                pairs.append((premise[:max_premise_chars], claim["text"]))
                owners.append(i)
    raw = scorer.score(pairs)
    best = [0.0] * len(claims)
    for i, s in zip(owners, raw, strict=True):
        best[i] = max(best[i], s)
    kept = [c for c, s in zip(claims, best, strict=True) if s >= threshold]
    return Layer2Result(kept, len(claims) - len(kept), [int(round(s * 1000)) for s in best], scorer.model)


def rebuild_answer(answer: str, kept: Iterable[dict], removed: int) -> str:
    kept = list(kept)
    if not kept:
        return ""
    if not removed:
        return answer
    return "Here is what I found:\n" + "\n".join(f"- {c['text']}" for c in kept)
