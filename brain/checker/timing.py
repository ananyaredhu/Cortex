"""Check #2: time grounding-model candidates on this CPU. `python -m brain.checker.timing [--models a,b] [--pairs 40]`.

For each model: load time, milliseconds per (evidence, claim) pair, peak memory, and accuracy on a labeled set built
from the fixtures (a sentence of a document against its own document is "supported"; against another document
"unsupported"). Fixture text only; nothing leaves the machine except the model download. Paste the output into
ADR-003 and check #2. Models download into HF_HOME on first use: ask before running with a new one.
"""
import argparse
import json
import os
import re
import time

from brain.checker.layer2 import CrossEncoderScorer
from connectors.env import load_dotenv
from fixtures.loader import load

DEFAULT_MODELS = "cross-encoder/nli-deberta-v3-xsmall,cross-encoder/nli-deberta-v3-small"
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def labeled_pairs(limit: int) -> list[tuple[str, str, bool]]:
    """(premise, claim, supported). Claims are fixture sentences; premises are the sentence's own document or another."""
    docs = [d for d in load()["documents"] if len(d["body"]) > 80]
    out: list[tuple[str, str, bool]] = []
    for i, d in enumerate(docs):
        sentences = [s for s in SENTENCE.split(d["body"]) if len(s.split()) >= 5 and "CANARY" not in s]
        other = docs[(i + 1) % len(docs)]
        for s in sentences[:2]:
            out.append((d["body"], s, True))
            out.append((other["body"], s, False))
    return out[:limit]


def peak_memory_mb() -> float | None:
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().peak_wset / 1e6, 0)
    except (ImportError, AttributeError):
        return None


def time_model(name: str, pairs: list[tuple[str, str, bool]], *, threshold: float = 0.5) -> dict:
    scorer = CrossEncoderScorer(name)
    started = time.perf_counter()
    scorer.score([("warm up", "warm up")])
    load_s = round(time.perf_counter() - started, 1)
    started = time.perf_counter()
    scores = scorer.score([(p, c) for p, c, _ in pairs])
    per_pair_ms = round((time.perf_counter() - started) * 1000 / max(1, len(pairs)), 1)
    correct = sum((s >= threshold) == supported for s, (_, _, supported) in zip(scores, pairs, strict=True))
    supported_scores = [s for s, (_, _, ok) in zip(scores, pairs, strict=True) if ok]
    unsupported_scores = [s for s, (_, _, ok) in zip(scores, pairs, strict=True) if not ok]
    return {"model": name, "load_s": load_s, "ms_per_pair": per_pair_ms, "pairs": len(pairs),
            "accuracy_at_threshold": round(correct / len(pairs), 2),
            "mean_score_supported": round(sum(supported_scores) / max(1, len(supported_scores)), 3),
            "mean_score_unsupported": round(sum(unsupported_scores) / max(1, len(unsupported_scores)), 3),
            "peak_memory_mb_so_far": peak_memory_mb()}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m brain.checker.timing", description=__doc__.splitlines()[0])
    parser.add_argument("--models", default=DEFAULT_MODELS, help="comma-separated Hugging Face model names")
    parser.add_argument("--pairs", type=int, default=40)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()
    load_dotenv()
    pairs = labeled_pairs(args.pairs)
    report = {"cpu": os.cpu_count(), "pairs": len(pairs), "models": []}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        try:
            report["models"].append(time_model(name, pairs, threshold=args.threshold))
        except Exception as exc:                                   # noqa: BLE001 - report and continue
            report["models"].append({"model": name, "error": type(exc).__name__})
        print(json.dumps(report["models"][-1]), flush=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
