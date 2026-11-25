#!/usr/bin/env python3
"""Render a standalone SVG from the benchmark CSVs."""

import argparse
import csv
from dataclasses import dataclass
from html import escape
import json
import math
from pathlib import Path
from statistics import median


MIB = 1024 ** 2
WIDTH, HEIGHT = 1240, 628
LEFT, RIGHT, TOP, BOTTOM = 93, 1202, 106, 535
MEDIAN_COLOR = "#b44d0e"
OVERLAY_LINES = (("#a630e0", 2.8), ("#007fe4", 2.1))


@dataclass
class PlotRun:
    start: float
    end: float
    label: str
    samples: list
    peaks: list


def tick_spacing(target):
    scale = 10 ** math.floor(math.log10(target))
    return next(n * scale for n in (1, 2, 5, 10) if n * scale >= target)


def time_ticks(start, end):
    step = tick_spacing((end - start) / 8)
    first = math.ceil(start / step)
    last = math.floor(end / step)
    return [n * step for n in range(first, last + 1)]


def read_points(directory, start, end):
    samples = []
    peaks = []
    with (directory / "rss.csv").open(newline="") as source:
        for row in csv.DictReader(source):
            elapsed = float(row["elapsed_s"])
            if row["process"] == "parser" and start <= elapsed <= end:
                samples.append((elapsed, int(row["rss_bytes"])))
    with (directory / "files.csv").open(newline="") as source:
        for row in csv.DictReader(source):
            elapsed = float(row["end_s"])
            peak = int(row["peak_rss_bytes"])
            if start <= elapsed <= end and peak:
                peaks.append((elapsed, peak))
    return samples, peaks


def render(directory, output=None, start=0.0, end=None, *, linear=False):
    summary = json.loads((directory / "summary.json").read_text())
    duration = summary["elapsed_s"]
    end = duration if end is None else end
    if not 0 <= start < end <= duration:
        raise ValueError(f"expected 0 <= start < end <= {duration:.9f} s")
    run = PlotRun(start, end, "", *read_points(directory, start, end))
    subtitle = f"{summary['files']:,} files · {summary['head'][:12]}"
    if "excluded_files" in summary:
        subtitle += (
            f" · {summary['excluded_files']} files excluded at "
            f"≥{summary['excluded_parser_peak_at_least_mib']:g} MiB parser peak RSS"
        )
    else:
        subtitle += f" · {summary['platform']} · one parser process at a time"
    if "replacement_files" in summary:
        subtitle += f" · {len(summary['replacement_files'])} shortened copies"
    draw([run], subtitle, output if output is not None else directory / "rss.svg",
         linear=linear)


def draw(runs, subtitle, output, *, linear, title=None, overlay=False):
    start, end = runs[0].start, runs[-1].end
    comparison = len(runs) > 1 and not overlay
    if overlay:
        width, right = 1100, 1062
    else:
        width, right = (1650, 1612) if comparison else (WIDTH, RIGHT)
    rss_values = [rss for run in runs for series in (run.samples, run.peaks)
                  for _, rss in series]
    if not rss_values:
        raise ValueError("there are no RSS measurements in this time window")
    medians = [median(rss for _, rss in run.peaks) if run.peaks else None for run in runs]
    common_median = len(set(medians)) == 1
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
        return LEFT + (elapsed - start) * (right - LEFT) / (end - start)

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

    def peak_line(points, color, stroke_width):
        # Retain the largest observed peak in each common display bin.
        columns = {}
        for elapsed, rss in points:
            column = round((x(elapsed) - LEFT) / 2)
            columns[column] = max(rss, columns.get(column, 0))
        positions = " ".join(f"{min(LEFT + column * 2, right)},{y(rss):.2f}"
                             for column, rss in sorted(columns.items()))
        return (f'<polyline class="peak-line" points="{positions}" fill="none" '
                f'stroke="{color}" stroke-width="{stroke_width}" '
                'stroke-linecap="round" stroke-linejoin="round"/>')

    def label(px, py, content, cls="", anchor="start"):
        return (f'<text x="{px:.2f}" y="{py:.2f}" class="{cls}" '
                f'text-anchor="{anchor}">{escape(content)}</text>')

    axis_label = "RSS (MiB)" if linear else "RSS (MiB, log scale)"
    scale_description = "linear" if linear else "logarithmic"
    if title is None:
        title = "Tree-sitter RSS by allocator on LLVM" if comparison else "Tree-sitter RSS on LLVM"
    description = ("Original and shortened parser peaks aligned by source file, "
                   "showing the highest peak per display bin." if overlay else
                   "Live RSS and lifetime peak RSS for sequential parser processes.")
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{HEIGHT}" '
        f'viewBox="0 0 {width} {HEIGHT}" role="img" aria-labelledby="title description">',
        f'<title id="title">{escape(title)}</title>',
        f'<desc id="description">{description} '
        f'Each horizontal line shows the median parser peak for its run. RSS uses a {scale_description} axis.</desc>',
        '<style>text{font:14px Arial,Helvetica,sans-serif;fill:#223349}'
        '.title{font-size:27px;font-weight:680}.sub{fill:#4c5c70}'
        '.tick{font-size:12px;fill:#4c5c70}.axis{font-weight:600}</style>',
        f'<rect width="{width}" height="{HEIGHT}" fill="white"/>',
        label(LEFT, 38 if comparison else 44, title, "title"),
        label(LEFT, 62 if comparison else 70, subtitle, "sub"),
    ]
    if comparison:
        for index, (run, middle) in enumerate(zip(runs, medians, strict=True)):
            x1, x2 = x(run.start), x(run.end)
            if index % 2 == 1:
                svg.append(f'<rect x="{x1:.2f}" y="{TOP}" width="{x2 - x1:.2f}" '
                           f'height="{BOTTOM - TOP}" fill="#f3f8fb"/>')
            heading = run.label if middle is None else f"{run.label} · median {middle / MIB:.2f} MiB"
            svg.append(label((x1 + x2) / 2, TOP - 10, heading, "axis", "middle"))
    for tick in rss_ticks:
        py = y(tick * MIB)
        svg.append(gridline(LEFT, py, right, py))
        svg.append(label(LEFT - 11, py + 4, f"{tick:g}", "tick", "end"))
    for elapsed in time_ticks(start, end):
        px = x(elapsed)
        svg.append(gridline(px, TOP, px, BOTTOM))
        svg.append(label(px, BOTTOM + 21, f"{elapsed:g}", "tick", "middle"))
    time_label = "Original run elapsed seconds" if overlay else "Elapsed seconds"
    svg += [
        f'<rect x="{LEFT}" y="{TOP}" width="{right - LEFT}" height="{BOTTOM - TOP}" '
        'fill="none" stroke="#bcc9d6"/>',
        '<text class="axis" transform="translate(23 320) rotate(-90)" text-anchor="middle">'
        f'{axis_label}</text>',
        label((LEFT + right) / 2, BOTTOM + 43, time_label, "axis", "middle"),
    ]
    for index, run in enumerate(runs):
        if overlay:
            svg.append(peak_line(run.peaks, *OVERLAY_LINES[index]))
        else:
            svg.append(markers(run.samples, "#318799", 0.43, 1.8))
            svg.append(markers(run.peaks, "#7041b8", 0.66, 2.3))
    for index, (run, middle) in enumerate(zip(runs, medians, strict=True)):
        if middle is not None and (not overlay or middle not in medians[:index]):
            color = OVERLAY_LINES[index][0] if overlay and not common_median else MEDIAN_COLOR
            svg.append(
                f'<path class="median-line" d="M{x(run.start):.2f},{y(middle):.2f} '
                f'H{x(run.end):.2f}" stroke="{color}" stroke-width="2.4" '
                'stroke-dasharray="8 4" fill="none"/>'
            )
    for run in runs[1:] if comparison else ():
        px = x(run.start)
        svg.append(f'<path d="M{px:.2f},{TOP}V{BOTTOM}" stroke="#8294a8" '
                   'stroke-width="1.3" stroke-dasharray="5 4"/>')
    legend = (
        ((LEFT, OVERLAY_LINES[0][0], runs[0].label),
         (LEFT + 192, OVERLAY_LINES[1][0], runs[1].label))
        if overlay else
        ((LEFT, "#318799", "Parser RSS samples"),
         (LEFT + 192, "#7041b8", "Per-parser lifetime peak RSS"))
    )
    for px, color, name in legend:
        if overlay:
            svg.append(f'<path d="M{px},602h23" stroke="{color}" stroke-width="2.8"/>')
            svg.append(label(px + 31, 607, name))
        else:
            svg.append(f'<circle cx="{px + 4}" cy="602" r="4" fill="{color}"/>')
            svg.append(label(px + 16, 607, name))
    if (comparison or common_median) and any(value is not None for value in medians):
        px = LEFT + 425
        svg.append(f'<path d="M{px},602h23" stroke="{MEDIAN_COLOR}" '
                   'stroke-width="2.4" stroke-dasharray="8 4"/>')
        median_label = "Median parser peak RSS"
        if not comparison:
            median_label += f" · {medians[0] / MIB:.2f} MiB"
        svg.append(label(px + 31, 607, median_label))
    elif overlay:
        for index, middle in enumerate(medians):
            if middle is not None:
                px = LEFT + 425 + 192 * index
                color = OVERLAY_LINES[index][0]
                svg.append(f'<path d="M{px},602h23" stroke="{color}" '
                           'stroke-width="2.4" stroke-dasharray="8 4"/>')
                svg.append(label(px + 31, 607, f"{runs[index].label} median · {middle / MIB:.2f} MiB"))
    svg.append("</svg>")
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
