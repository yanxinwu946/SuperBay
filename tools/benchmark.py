"""Reproducible warm-index benchmark; reports timings without performance assertions."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.models import Entry
from core.search import SearchIndex


def benchmark(count=10000, repeats=5):
    entries = [Entry(f"Tool-{i:05d}", Path(f"C:/Tools/Group-{i%20}/Tool-{i:05d}"),
                     "tools", f"Group {i%20}", True, desc="Workspace entry",
                     tags=["network" if i%2 else "analysis"]) for i in range(count)]
    notes = {str(entry.path): ("usage documentation quick reference " + str(i) + " ") * 24
             for i, entry in enumerate(entries)}
    index = SearchIndex()
    start = time.perf_counter()
    index.reset(entries, notes)
    result = {"entries": count, "repeats": repeats,
              "index_build_ms": round((time.perf_counter()-start)*1000, 3), "queries": {}}
    for query in ("tool-09999", "network tool", "reference", "t999", "不存在"):
        timings = []
        for _ in range(repeats):
            start = time.perf_counter()
            hits = index.ranked(query)
            hits.sort(key=lambda pair: (-pair[1], not pair[0].pinned, -pair[0].runs, pair[0].name.casefold()))
            timings.append((time.perf_counter()-start)*1000)
        result["queries"][query] = {"median_ms": round(statistics.median(timings), 3), "matches": len(hits)}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--entries", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.entries < 1 or args.repeats < 1:
        parser.error("entries and repeats must be positive")
    print(json.dumps(benchmark(args.entries, args.repeats), ensure_ascii=True, indent=2))
