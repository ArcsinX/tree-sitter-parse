#!/usr/bin/env python3
"""Exclude parser processes by peak RSS while keeping the original timeline."""

import argparse
import csv
import json
import math
from pathlib import Path


MIB = 1024 ** 2


def filter_results(source, output, threshold_mib, source_root):
    summary = json.loads((source / "summary.json").read_text())
    root = source_root if source_root is not None else Path(summary["root"])
    threshold_bytes = threshold_mib * MIB

    with (source / "files.csv").open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        excluded, kept = [], []
        for row in reader:
            rows = excluded if int(row["peak_rss_bytes"]) >= threshold_bytes else kept
            rows.append(row)
    if not kept:
        raise ValueError("the threshold excludes every parser process")

    excluded.sort(key=lambda row: -int(row["peak_rss_bytes"]))
    excluded_ids = {row["file_id"] for row in excluded}
    excluded_details = [
        (row["file_id"], row["path"], (root / row["path"]).stat().st_size, row["peak_rss_bytes"])
        for row in excluded
    ]

    output.mkdir(parents=True)
    with (output / "excluded-files.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("file_id", "path", "file_size_bytes", "peak_rss_bytes"))
        writer.writerows(excluded_details)
    with (output / "files.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(kept)
    with (source / "rss.csv").open(newline="") as original, (
        output / "rss.csv"
    ).open("w", newline="") as destination:
        reader = csv.DictReader(original)
        writer = csv.DictWriter(destination, reader.fieldnames)
        writer.writeheader()
        writer.writerows(row for row in reader if row["file_id"] not in excluded_ids)

    largest = max(kept, key=lambda row: int(row["peak_rss_bytes"]))
    summary.update({
        "files": len(kept),
        "recovered": sum(row["exit_code"] == "2" for row in kept),
        "failed": sum(row["exit_code"] not in {"0", "2"} for row in kept),
        "clean": sum(row["exit_code"] == "0" for row in kept),
        "max_parser_peak_rss_bytes": int(largest["peak_rss_bytes"]),
        "max_parser_peak_path": largest["path"],
        "excluded_parser_peak_at_least_mib": threshold_mib,
        "excluded_files": len(excluded),
    })
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Excluded {len(excluded):,} files at ≥{threshold_mib:g} MiB. Kept {len(kept):,}.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path, help="benchmark output directory")
    cli.add_argument("output", type=Path, help="new directory for the filtered results")
    cli.add_argument("--at-least-mib", type=float, default=45.0)
    cli.add_argument("--source-root", type=Path, help="checkout used to read source file sizes")
    args = cli.parse_args()
    if not math.isfinite(args.at_least_mib) or args.at_least_mib <= 0:
        cli.error("the RSS threshold must be finite and positive")
    try:
        filter_results(args.source, args.output, args.at_least_mib, args.source_root)
    except (OSError, ValueError) as error:
        cli.error(str(error))
