#!/usr/bin/env python3
"""Read-only benchmark helper for Django FileBasedCache in a temp directory.

Run with the project's virtualenv and production-parity Django version:
    python verification/benchmark_filebased_cache.py --sizes 0 100 1000 10000 20000 --max-entries 20000

All files are created under tempfile.TemporaryDirectory and removed on exit.
This does not read or touch the project's configured cache directories.
"""
import argparse
import statistics
import tempfile
import time
from pathlib import Path

from django.core.cache.backends.filebased import FileBasedCache


def benchmark(size: int, repeats: int, max_entries: int) -> tuple[float, float]:
    if size > max_entries:
        raise ValueError("size cannot exceed max_entries")
    with tempfile.TemporaryDirectory(prefix="django-fbc-bench-") as directory:
        cache = FileBasedCache(
            directory,
            {
                "TIMEOUT": 60,
                "OPTIONS": {"MAX_ENTRIES": max_entries, "CULL_FREQUENCY": 8},
            },
        )
        # FileBasedCache._list_cache_files() counts .djcache files. Dummy files
        # are sufficient to measure directory enumeration/cull bookkeeping;
        # no project cache values are read.
        for index in range(size):
            (Path(directory) / f"seed-{index:08d}.djcache").touch()

        samples = []
        for index in range(repeats):
            started = time.perf_counter()
            cache.set(f"probe-{index}", {"ok": True}, timeout=60)
            samples.append(time.perf_counter() - started)
        return statistics.median(samples), max(samples)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=[0, 100, 500, 1000, 2500])
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-entries", type=int, default=20000)
    args = parser.parse_args()

    print("entries,max_entries,median_set_ms,max_set_ms")
    for size in args.sizes:
        median, maximum = benchmark(size, args.repeats, args.max_entries)
        print(f"{size},{args.max_entries},{median * 1000:.3f},{maximum * 1000:.3f}")


if __name__ == "__main__":
    main()
