#!/usr/bin/env python3
"""Render a standalone SVG from the benchmark CSVs."""

import argparse
import csv
from html import escape
import json
import math
from pathlib import Path


MIB = 1024 ** 2
WIDTH, HEIGHT = 1240, 650
LEFT, RIGHT, TOP, BOTTOM = 93, 1202, 106, 535


def tick_spacing(target):
    scale = 10 ** math.floor(math.log10(target))
    return next(n * scale for n in (1, 2, 5, 10) if n * scale >= target)


def time_ticks(start, end):
    step = tick_spacing((end - start) / 8)
    first = math.ceil(start / step)
    last = math.floor(end / step)
    return [n * step for n in range(first, last + 1)]


def read_points(directory, start, end):
    python = []
    samples = []
    peaks = []
    with (directory / "rss.csv").open(newline="") as source:
        for row in csv.DictReader(source):
            elapsed = float(row["elapsed_s"])
            if start <= elapsed <= end:
                points = python if row["process"] == "python" else samples
                points.append((elapsed, int(row["rss_bytes"])))
    with (directory / "files.csv").open(newline="") as source:
        for row in csv.DictReader(source):
            elapsed = float(row["end_s"])
            peak = int(row["peak_rss_bytes"])
            if start <= elapsed <= end and peak:
                peaks.append((elapsed, peak))
    return python, samples, peaks


def render(directory, output=None, start=0.0, end=None, *, linear=False):
    summary = json.loads((directory / "summary.json").read_text())
    duration = summary["elapsed_s"]
    end = duration if end is None else end
    if not 0 <= start < end <= duration:
        raise ValueError(f"expected 0 <= start < end <= {duration:.9f} s")
    python, samples, peaks = read_points(directory, start, end)
    rss_values = [rss for series in (python, samples, peaks) for _, rss in series]
    if not rss_values:
        raise ValueError("there are no RSS measurements in this time window")
    if linear:
        low = 0
        tick_step = tick_spacing(max(rss_values) / MIB / 8)
        tick_count = math.ceil(max(rss_values) / MIB / tick_step)
        high = tick_count * tick_step
        rss_ticks = [index * tick_step for index in range(tick_count + 1)]
        coordinate = float
    else:
        low = math.floor(math.log2(min(rss_values) / MIB))
        high = max(low + 1, math.ceil(math.log2(max(rss_values) / MIB)))
        tick_step = max(1, math.ceil((high - low) / 12))
        exponents = sorted(set(range(low, high + 1, tick_step)) | {high})
        rss_ticks = [2.0 ** exponent for exponent in exponents]
        coordinate = math.log2

    def x(elapsed):
        return LEFT + (elapsed - start) * (RIGHT - LEFT) / (end - start)

    def y(rss_bytes):
        return BOTTOM - (coordinate(rss_bytes / MIB) - low) * (BOTTOM - TOP) / (high - low)

    def gridline(x1, y1, x2, y2):
        return f'<path d="M{x1:.2f},{y1:.2f} L{x2:.2f},{y2:.2f}" stroke="#dbe3eb"/>'

    def markers(points, color, opacity, size):
        # Coincident points at the SVG's display resolution share a marker.
        pixels = dict.fromkeys((round(x(t) * 2) / 2, round(y(rss) * 2) / 2)
                               for t, rss in points)
        commands = [f"M{px:g},{py:g}h.01" for px, py in pixels]
        paths = []
        for offset in range(0, len(commands), 512):
            path = "".join(commands[offset:offset + 512])
            paths.append(f'<path d="{path}"/>')
        return (f'<g fill="none" stroke="{color}" opacity="{opacity}" '
                f'stroke-width="{size}" stroke-linecap="round">\n'
                + "\n".join(paths) + "\n</g>")

    def label(px, py, content, cls="", anchor="start"):
        return (f'<text x="{px:.2f}" y="{py:.2f}" class="{cls}" '
                f'text-anchor="{anchor}">{escape(content)}</text>')

    axis_label = "RSS (MiB)" if linear else "RSS (MiB, log scale)"
    scale_description = "linear" if linear else "logarithmic"
    subtitle = f"{summary['files']:,} files · {duration:.1f} s · {summary['head'][:12]}"
    if "excluded_files" in summary:
        subtitle += (
            f" · {summary['excluded_files']} files excluded at "
            f"≥{summary['excluded_parser_peak_at_least_mib']:g} MiB parser peak RSS"
        )
    else:
        subtitle += f" · {summary['platform']} · one parser process at a time"
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">',
        '<title id="title">Tree-sitter RSS on LLVM</title>',
        '<desc id="description">RSS for the Python process and sequential native parser '
        f'processes on a shared elapsed-time axis. RSS uses a {scale_description} axis.</desc>',
        '<style>text{font:14px Arial,Helvetica,sans-serif;fill:#223349}'
        '.title{font-size:27px;font-weight:680}.sub{fill:#4c5c70}'
        '.tick{font-size:12px;fill:#4c5c70}.axis{font-weight:600}</style>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="white"/>',
        label(LEFT, 44, "Tree-sitter RSS on LLVM", "title"),
        label(LEFT, 70, subtitle, "sub"),
    ]
    for tick in rss_ticks:
        py = y(tick * MIB)
        svg.append(gridline(LEFT, py, RIGHT, py))
        svg.append(label(LEFT - 11, py + 4, f"{tick:g}", "tick", "end"))
    for elapsed in time_ticks(start, end):
        px = x(elapsed)
        svg.append(gridline(px, TOP, px, BOTTOM))
        svg.append(label(px, BOTTOM + 21, f"{elapsed:g}", "tick", "middle"))
    svg += [
        f'<rect x="{LEFT}" y="{TOP}" width="{RIGHT - LEFT}" height="{BOTTOM - TOP}" '
        'fill="none" stroke="#bcc9d6"/>',
        '<text class="axis" transform="translate(23 320) rotate(-90)" text-anchor="middle">'
        f'{axis_label}</text>',
        label((LEFT + RIGHT) / 2, BOTTOM + 43, "Elapsed seconds", "axis", "middle"),
        markers(samples, "#318799", 0.43, 1.8),
        markers(peaks, "#7041b8", 0.66, 2.3),
    ]
    if python:
        path = " ".join(f"{x(t):.2f},{y(rss):.2f}" for t, rss in python)
        svg += [
            f'<polyline points="{path}" fill="none" stroke="#bd510d" stroke-width="2"/>',
            markers(python, "#bd510d", 1, 2),
        ]
    for px, color, name in (
        (LEFT, "#bd510d", "Python RSS"),
        (LEFT + 182, "#318799", "Parser RSS samples"),
        (LEFT + 411, "#7041b8", "Per-parser peak RSS at exit"),
    ):
        svg.append(f'<circle cx="{px + 4}" cy="602" r="4" fill="{color}"/>')
        svg.append(label(px + 16, 607, name))
    svg.append(label(
        LEFT, 632,
        f"Requested sample intervals  ·  parser {summary['parser_interval_ms']:g} ms"
        f"  ·  Python {summary['python_interval_ms']:g} ms  ·  Peak times within each process are unknown",
        "sub",
    ))
    svg.append("</svg>")
    output = output if output is not None else directory / "rss.svg"
    output.write_text("\n".join(svg) + "\n", encoding="utf-8")
    print(f"Wrote {output}", flush=True)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--from-s", type=float, default=0.0)
    cli.add_argument("--to-s", type=float)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--linear", action="store_true", help="show RSS on a linear axis")
    options = cli.parse_args()
    if (options.from_s or options.to_s is not None or options.linear) and options.output is None:
        cli.error("a time window or linear axis requires --output")
    try:
        render(options.directory, options.output, options.from_s, options.to_s,
               linear=options.linear)
    except ValueError as error:
        cli.error(str(error))
