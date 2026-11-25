#!/usr/bin/env python3
"""Measure the highest-RSS LLVM files before and after shortening generated tables."""

import argparse
import csv
import json
import os
from pathlib import Path
import signal
from statistics import median
import sys
from tempfile import TemporaryDirectory, TemporaryFile
import time


HERE = Path(__file__).resolve().parent
CASES = {
    "compiler-rt/test/builtins/Unit/udivmodti4_test.c": ("c", ("--array", "tests")),
    "libc/src/__support/ryu_long_double_constants.h":
        ("cpp", ("--array", "POW10_SPLIT", "--array", "POW10_SPLIT_2")),
    "llvm/lib/Support/UnicodeNameToCodepointGenerated.cpp":
        ("cpp", ("--array", "UnicodeNameToCodepointIndex_")),
    "polly/lib/External/isl/include/isl/typed_cpp.h": ("cpp", ()),
    "llvm/lib/Target/X86/X86ISelLowering.cpp": ("cpp", ()),
    "libcxxabi/test/DemangleTestCases.inc": ("cpp", ("--string-pairs",)),
    "llvm/include/llvm/Testing/Demangle/DemangleTestCases.inc": ("cpp", ("--string-pairs",)),
    "compiler-rt/test/builtins/Unit/ppc/floatditf_test.h": ("cpp", ("--array", "tests")),
}
PARSER_FIELDS = ("wall_s", "parse_s", "peak_rss_bytes")


def measure(argv, accepted_codes):
    with TemporaryFile() as stream:
        start = time.perf_counter()
        pid = os.posix_spawn(argv[0], argv, os.environ,
                             file_actions=[(os.POSIX_SPAWN_DUP2, stream.fileno(), 1)])
        try:
            _, status, usage = os.wait4(pid, 0)
        except BaseException:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
            raise
        elapsed = time.perf_counter() - start
        exit_code = os.waitstatus_to_exitcode(status)
        if exit_code not in accepted_codes:
            raise ValueError(f"{argv[0]} exited with status {exit_code} for {argv[2]}")
        peak = usage.ru_maxrss * (1024 if sys.platform == "linux" else 1)
        stream.seek(0)
        data = json.load(stream)
    return {"wall_s": elapsed, "peak_rss_bytes": peak, "exit_code": exit_code, **data}


def parse(binary, grammar, path):
    return measure([binary, grammar, str(path), "--stats"], {0, 2})


def benchmark(root, binary, output, trials):
    output.mkdir(parents=True)
    all_trials = []
    summary = []
    with TemporaryDirectory(prefix="tree-sitter-payloads-") as temp:
        for relative, (grammar, flags) in CASES.items():
            original = root / relative
            altered = Path(temp) / original.name
            case_trials = []
            tables = []
            for index in range(trials):
                if index % 2 == 0:
                    before = parse(binary, grammar, original)
                if flags:
                    preparation = measure(
                        [sys.executable, str(HERE / "preprocess_payloads.py"),
                         str(original), str(altered), *flags], {0}
                    )
                    tables = preparation["edits"]
                else:
                    preparation = {"wall_s": 0, "peak_rss_bytes": 0,
                                   "output_bytes": original.stat().st_size}
                after = parse(binary, grammar, altered if flags else original)
                if flags:
                    altered.unlink()
                if index % 2 == 1:
                    before = parse(binary, grammar, original)
                case_trials.append({
                    "path": relative,
                    "trial": index + 1,
                    "source_bytes": original.stat().st_size,
                    "reduced_bytes": preparation["output_bytes"],
                    **{f"original_{key}": before[key]
                       for key in (*PARSER_FIELDS, "nodes", "exit_code")},
                    **{f"reduced_{key}": after[key]
                       for key in (*PARSER_FIELDS, "nodes", "exit_code")},
                    "preprocess_wall_s": preparation["wall_s"],
                    "preprocess_peak_rss_bytes": preparation["peak_rss_bytes"],
                    "pipeline_wall_s": preparation["wall_s"] + after["wall_s"],
                    "pipeline_peak_child_rss_bytes": max(preparation["peak_rss_bytes"],
                                                         after["peak_rss_bytes"]),
                })
            all_trials.extend(case_trials)
            medians = {key: median(row[key] for row in case_trials) for key in case_trials[0]
                       if key not in {"path", "trial", "original_exit_code", "reduced_exit_code"}}
            summary.append({
                "path": relative,
                **medians,
                "original_exit_codes": sorted({row["original_exit_code"] for row in case_trials}),
                "reduced_exit_codes": sorted({row["reduced_exit_code"] for row in case_trials}),
                "tables": tables,
            })
            print(f"{relative}: {medians['original_peak_rss_bytes'] / 2**20:.2f} → "
                  f"{medians['reduced_peak_rss_bytes'] / 2**20:.2f} MiB", flush=True)
    with (output / "trials.csv").open("w", newline="") as destination:
        writer = csv.DictWriter(destination, all_trials[0].keys())
        writer.writeheader()
        writer.writerows(all_trials)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--root", type=Path, default=HERE.parent / "llvm-project")
    cli.add_argument("--binary", type=Path, default=HERE / "build" / "ts_parse")
    cli.add_argument("--out", type=Path, default=HERE / "results" / "preprocessing")
    cli.add_argument("--trials", type=int, default=5)
    args = cli.parse_args()
    if args.trials < 1:
        cli.error("trials must be positive")
    try:
        benchmark(args.root.resolve(), str(args.binary.resolve()), args.out, args.trials)
    except (OSError, ValueError) as error:
        cli.error(str(error))
