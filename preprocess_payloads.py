#!/usr/bin/env python3
"""Shorten selected generated C/C++ tables for structural parsing experiments."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re


MIN_TABLE_BYTES = 64 * 1024
IDENTIFIER = rb"[A-Za-z_][A-Za-z_0-9]*"
ARRAY_DECL = re.compile(
    rb"(?m)^[ \t]*(?:" + IDENTIFIER + rb"[ \t]+)+(?P<name>" + IDENTIFIER +
    rb")[ \t]*(?:\[[ \t]*[0-9]*[ \t]*\][ \t]*)+=[ \t\r\n]*(?P<open>\{)"
)
STRING = rb'"[^"\\]*(?:\\[\s\S][^"\\]*)*"'
LINE_COMMENT = rb"//[^\r\n]*"
BLOCK_COMMENT = rb"/\*[\s\S]*?\*/"
TRIVIA = re.compile(rb"(?:\s+|" + LINE_COMMENT + rb"|" + BLOCK_COMMENT + rb")*")
TOKEN = re.compile(
    rb"(?P<ignored>" + LINE_COMMENT + rb"|/\*[\s\S]*?(?:\*/|\Z)"
    rb'|\b(?:u8|[uUL])?R"(?P<delim>[^\s()\\]{0,16})\([\s\S]*?(?:\)(?P=delim)"|\Z)'
    rb"|\b[0-9][A-Za-z0-9_.]*'[A-Za-z0-9_']+|" + STRING +
    rb"|\x27[^\x27\\]*(?:\\[\s\S][^\x27\\]*)*\x27)"
    rb"|(?P<punct>[{}\[\](),;#]|%:)"
)
STRING_PAIR = re.compile(rb"\{\s*(?:" + STRING + rb"\s*)+,\s*(?:" + STRING + rb"\s*)+\},")
CLOSER = {b"{": b"}", b"(": b")", b"[": b"]"}
LINE_ENDING = re.compile(rb"\r\n|\n|\r")
ROW = rb"\s*\{[0-9A-Za-z_ \t.,()<>|+\-]+\}"
BRACED_ROW = re.compile(ROW + rb"[ \t]*,")
FINAL_ROW = re.compile(ROW + rb"\s*")
FLAT_NUMBERS = re.compile(rb"[0-9A-Fa-fxX, \t\r\n]+")


@dataclass
class Edit:
    table: str
    line: int
    entries_before: int
    start: int
    end: int
    padding: bytes

    def metrics(self):
        return {
            "table": self.table,
            "line": self.line,
            "entries_before": self.entries_before,
            "removed_bytes": self.end - self.start - len(self.padding),
        }


def line_padding(source, start, end):
    if source.find(b"\r", start, end) < 0:
        return b"\n" * source.count(b"\n", start, end)
    return b"".join(match[0] for match in LINE_ENDING.finditer(source, start, end))


def generated_table_tail(source, opening, name, min_bytes):
    end = source.find(b"};", opening + 1)
    if end - opening < min_bytes:
        return None
    body = opening + 1
    if FLAT_NUMBERS.fullmatch(source, body, end):
        start = source.find(b",", body, end) + 1
        count = source.count(b",", body, end) + (source[body:end].rstrip()[-1:] != b",")
    else:
        cursor = body
        start = count = 0
        while row := BRACED_ROW.match(source, cursor, end):
            cursor = row.end()
            if count == 0:
                start = cursor
            count += 1
        if FINAL_ROW.fullmatch(source, cursor, end):
            count += 1
        elif source[cursor:end].strip():
            return None
    if count < 2:
        return None
    return Edit(name, source.count(b"\n", 0, opening) + 1, count, start, end,
                line_padding(source, start, end))


def initializer_tail(source, tokens, opening, name, min_bytes):
    stack = [b"}"]
    first_comma = last_comma = None
    commas = 0
    for token in tokens:
        punct = token["punct"]
        if punct is None:
            continue
        if punct in CLOSER:
            stack.append(CLOSER[punct])
        elif punct == stack[-1]:
            stack.pop()
            if not stack:
                closing = token.start()
                end = TRIVIA.match(source, token.end()).end()
                if first_comma is None or closing - opening < min_bytes or source[end:end + 1] != b";":
                    return None
                count = commas + (TRIVIA.match(source, last_comma + 1).end() != closing)
                if count < 2:
                    return None
                start = first_comma + 1
                return Edit(name, source.count(b"\n", 0, opening) + 1, count, start, closing,
                            line_padding(source, start, closing))
        elif punct in (b"}", b"]", b")", b"#", b"%:", b";"):
            raise ValueError(f"{name}: unexpected token in initializer {punct!r}")
        elif punct == b"," and len(stack) == 1:
            if first_comma is None:
                first_comma = token.start()
            last_comma = token.start()
            commas += 1
    raise ValueError(f"{name}: unterminated initializer")


def array_edits(source, names, min_bytes):
    edits = []
    cursor = 0
    for match in ARRAY_DECL.finditer(source):
        name = match["name"].decode("ascii")
        opening = match.start("open")
        if name not in names or opening < cursor:
            continue
        tokens = TOKEN.finditer(source, cursor)
        for token in tokens:
            if token.end() <= opening:
                continue
            if token.start() != opening:
                break
            edit = generated_table_tail(source, opening, name, min_bytes)
            if edit is None:
                edit = initializer_tail(source, tokens, opening, name, min_bytes)
            if edit is not None:
                edits.append(edit)
                cursor = edit.end + 1
            break
    if {edit.table for edit in edits} != set(names) or len(edits) != len(names):
        raise ValueError("expected one eligible initializer per selected array")
    return edits


def pair_fragment_edits(source, min_bytes):
    cursor = 0
    count = 0
    first_end = last_end = 0
    first_start = TRIVIA.match(source).end()
    while True:
        cursor = TRIVIA.match(source, cursor).end()
        if cursor == len(source):
            break
        row = STRING_PAIR.match(source, cursor)
        if row is None:
            line = source.count(b"\n", 0, cursor) + 1
            raise ValueError(f"expected a brace-enclosed string pair at line {line}")
        cursor = last_end = row.end()
        count += 1
        if count == 1:
            first_end = last_end
    if count < 2 or last_end - first_start < min_bytes:
        raise ValueError("string-pair fragment is below the table threshold")
    return [Edit("string-pair rows", source.count(b"\n", 0, first_start) + 1,
                 count, first_end, last_end, line_padding(source, first_end, last_end))]


def shorten(source, arrays=(), *, string_pairs=False, min_bytes=MIN_TABLE_BYTES):
    if re.search(rb"\\\r?\n|\?\?/", source):
        raise ValueError("line splices and trigraph escapes are outside the supported input")
    edits = pair_fragment_edits(source, min_bytes) if string_pairs else array_edits(
        source, arrays, min_bytes
    )
    chunks = []
    cursor = 0
    for edit in edits:
        chunks.extend((source[cursor:edit.start], edit.padding))
        cursor = edit.end
    chunks.append(source[cursor:])
    return b"".join(chunks), edits


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("output", type=Path)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--array", action="append", help="array identifier to shorten, repeatable")
    mode.add_argument("--string-pairs", action="store_true", help="verify and shorten a string-pair include fragment")
    cli.add_argument("--min-bytes", type=int, default=MIN_TABLE_BYTES)
    args = cli.parse_args()
    if args.source.resolve() == args.output.resolve():
        cli.error("use a separate output file")
    if args.min_bytes <= 0 or len(set(args.array or [])) != len(args.array or []):
        cli.error("the byte threshold must be positive and array names unique")
    try:
        source = args.source.read_bytes()
        result, edits = shorten(source, args.array or (), string_pairs=args.string_pairs,
                                min_bytes=args.min_bytes)
        with args.output.open("xb") as output:
            output.write(result)
    except (OSError, ValueError) as error:
        cli.error(str(error))
    print(json.dumps({"source_bytes": len(source), "output_bytes": len(result),
                      "edits": [edit.metrics() for edit in edits]}))
