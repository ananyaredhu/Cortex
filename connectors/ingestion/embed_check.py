"""Time the local embedding model on this machine: load time, speed, vector size and memory.

    python -m connectors.ingestion.embed_check            # the fixture corpus, chunked as ingestion would
    python -m connectors.ingestion.embed_check --repeat 5

Runs entirely on this machine. The first run downloads the model (about 2.3 GB) into the Hugging Face cache;
set HF_HOME (in the shell or in .env) to choose where.
"""
import argparse
import sys
import time

from connectors.env import load_dotenv
from connectors.ingestion.chunking import chunk_body
from connectors.ingestion.embedding import DIM, Embedder, from_env
from fixtures.loader import load


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process, or None where it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return None
            return counters.PeakWorkingSetSize / 1_048_576
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1_048_576 if sys.platform == "darwin" else peak / 1024
    except Exception:
        return None


def measure(embedder: Embedder, texts: list[str], repeat: int = 1) -> dict:
    """Embed one text to load the model, then all of them `repeat` times."""
    started = time.perf_counter()
    first = embedder.embed(texts[:1])
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    vectors: list = []
    for _ in range(repeat):
        vectors = embedder.embed(texts)
    seconds = time.perf_counter() - started
    count = len(texts) * repeat
    dims = {len(v) for v in vectors if v is not None} | {len(v) for v in first if v is not None}
    return {"model": f"{embedder.model}@{embedder.version}", "chunks": count, "characters": sum(len(t) for t in texts) * repeat,
            "load_seconds": round(load_seconds, 1), "embed_seconds": round(seconds, 2),
            "seconds_per_chunk": round(seconds / count, 3) if count else None,
            "chunks_per_second": round(count / seconds, 2) if seconds else None,
            "dimensions": sorted(dims), "fits_schema": dims <= {DIM}, "peak_memory_mb": peak_memory_mb()}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.ingestion.embed_check", description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=1, help="embed the corpus this many times for a steadier number")
    args = parser.parse_args()
    load_dotenv()
    texts = [chunk for doc in load()["documents"] for chunk in chunk_body(doc["body"])]
    result = measure(from_env(), texts, args.repeat)
    peak = result.pop("peak_memory_mb")
    for key, value in result.items():
        print(f"{key}: {value}")
    print(f"peak_memory_mb: {round(peak) if peak else 'unknown'}")


if __name__ == "__main__":
    main()
