#!/usr/bin/env python3
"""Render the original and shortened-source peak RSS comparison as an SVG."""

import argparse
from html import escape
import json
import math
from pathlib import Path

from plot_rss import MIB, tick_spacing


MODES = (
    ("original_peak_rss_bytes", "Original parser", "#34577c"),
    ("reduced_peak_rss_bytes", "Shortened-file parser", "#754cbb"),
    ("pipeline_peak_child_rss_bytes", "Shortening + parsing", "#007e80"),
)
WIDTH, LEFT, RIGHT = 1280, 355, 1195
TOP, ROW = 162, 69


def draw(summary, output):
    end = TOP + ROW * len(summary)
    height = end + 78
    peak = max(row[key] / MIB for row in summary for key, _, _ in MODES)
    step = tick_spacing(peak / 8)
    axis_max = math.ceil(peak / step) * step

    def x(value):
        return LEFT + value / axis_max * (RIGHT - LEFT)

    def label(px, py, content, style="", anchor="start"):
        return (f'<text x="{px:.1f}" y="{py:.1f}" class="{style}" '
                f'text-anchor="{anchor}">{escape(content)}</text>')

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title description">',
        '<title id="title">Tree-sitter peak RSS before and after shortening generated tables</title>',
        '<desc id="description">Horizontal bars compare original parser peak resident memory, '
        'shortened-file parser peak, and the larger of the preprocessing and shortened-file parser '
        'peaks. Two unmodified files serve as controls.</desc>',
        '<style>text{font:14px Arial,Helvetica,sans-serif;fill:#25374a}'
        '.title{font-size:28px;font-weight:bold}.sub{fill:#506377}.file{font-weight:bold}'
        '.value{font-size:12px;font-weight:bold}.axis{font-weight:bold}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="white"/>',
        label(32, 48, "Tree-sitter peak RSS before and after shortening generated tables", "title"),
        label(32, 77, "Eight LLVM outliers · median of five sequential trials per file · system allocator", "sub"),
    ]
    for px, (_, name, color) in zip((32, 204, 404), MODES, strict=True):
        svg.append(f'<rect x="{px}" y="109" width="12" height="12" rx="2" fill="{color}"/>')
        svg.append(label(px + 20, 120, name))

    for i in range(round(axis_max / step) + 1):
        value = i * step
        px = x(value)
        svg.append(f'<path d="M{px:.1f},{TOP}V{end}" stroke="#dce4ec"/>')
        svg.append(label(px, end + 21, f"{value:g}", "sub", "middle"))
    for index, row in enumerate(summary):
        top = TOP + index * ROW
        path = Path(row["path"])
        svg.append(label(32, top + 26, path.name, "file"))
        parent = str(path.parent)
        if not row["tables"]:
            parent += " · unchanged"
        svg.append(label(32, top + 46, parent, "sub"))
        svg.append(f'<path d="M32,{top + ROW}H{RIGHT}" stroke="#e7edf3"/>')
        for j, (key, _, color) in enumerate(MODES if row["tables"] else MODES[:1]):
            rss = row[key] / MIB
            length = x(rss) - LEFT
            py = top + (9 + j * 18 if row["tables"] else 26)
            svg.append(f'<rect x="{LEFT}" y="{py}" width="{length:.2f}" height="12" '
                       f'rx="2" fill="{color}"/>')
            svg.append(label(x(rss) + 6, py + 10.5, f"{rss:.2f}", "value"))
    svg += [
        label((LEFT + RIGHT) / 2, end + 47, "Peak RSS (MiB)", "axis", "middle"),
        label(32, height - 12, "Shortening + parsing reports the larger of the two sequential child-process peaks.", "sub"),
        "</svg>",
    ]
    output.write_text("\n".join(svg) + "\n", encoding="utf-8")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("summary", type=Path)
    cli.add_argument("output", type=Path)
    args = cli.parse_args()
    draw(json.loads(args.summary.read_text()), args.output)
