"""Layer 1: every cited source exists in the packet and was allowed; the local denied-set scan is clean.

A claim that cites nothing, or cites something outside the allowed evidence, is removed. When claims were removed
the answer is rebuilt from the surviving ones, so no unsupported sentence survives in the prose. A leak-scan hit
withholds the whole answer: the generator produced denied content, which must not reach the asker.
"""
from dataclasses import dataclass, field

from brain.gateway.models import Generated
from brain.policy.leakscan import DeniedDoc, scan


@dataclass
class CheckResult:
    answer: str
    claims: list[dict]
    removed_claims: int
    leak_hits: list[str] = field(default_factory=list)   # denied doc_ids; hashed before logging
    abstained: bool = False

    @property
    def status(self) -> dict:
        return {"deterministic": "fail" if self.removed_claims else "pass",
                "leak_scan": "hit" if self.leak_hits else "clean"}

    @property
    def grounding(self) -> dict | None:
        total = len(self.claims) + self.removed_claims
        if total == 0:
            return None
        return {"score": round(len(self.claims) / total, 2), "removed_claims": self.removed_claims}


def check(generated: Generated, allowed_doc_ids: set[str], denied: list[DeniedDoc]) -> CheckResult:
    kept = [c for c in generated.claims if c.get("citations") and set(c["citations"]) <= allowed_doc_ids]
    removed = len(generated.claims) - len(kept)
    answer = generated.answer
    if removed or not kept:
        answer = ("Here is what I found:\n" + "\n".join(f"- {c['text']}" for c in kept)) if kept else ""
    hits = scan(answer + " " + " ".join(c["text"] for c in kept), denied) if (answer or kept) else []
    if hits:
        return CheckResult("", [], removed + len(kept), hits, abstained=True)
    return CheckResult(answer, kept, removed, [], abstained=generated.abstained or not kept)
