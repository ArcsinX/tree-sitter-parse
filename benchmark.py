#!/usr/bin/env python3
"""Run one native Tree-sitter process per file and measure its RSS."""

import argparse
import csv
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
CPP_EXTENSIONS = set(
    ".C .H .cc .cp .cpp .cxx .c++ .hh .hp .hpp .hxx .h++ .ipp .inl .tcc "
    ".tpp .ixx .cppm .ccm .cxxm .c++m .mpp .inc .def".split()
)


class MacRusage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time", "system_time", "package_idle_wakeups", "interrupt_wakeups",
            "pageins", "wired_size", "resident_size", "physical_footprint",
            "start_time", "exit_time",
        )
    ]


def rss_reader():
    if sys.platform == "darwin":
        proc = ctypes.CDLL("/usr/lib/libSystem.B.dylib").proc_pid_rusage
        proc.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.POINTER(MacRusage))
        proc.restype = ctypes.c_int
        info = MacRusage()

        def read(pid):
            if proc(pid, 0, ctypes.byref(info)) == 0 and info.resident_size:
                return info.resident_size
            return None

        return read

    if sys.platform == "linux":
        def read(pid):
            try:
                with open(f"/proc/{pid}/status", "rb") as status:
                    for line in status:
                        if line.startswith(b"VmRSS:"):
                            return int(line.split()[1]) * 1024
            except (FileNotFoundError, ProcessLookupError):
                pass
            return None

        return read

    raise SystemExit("RSS sampling requires macOS or Linux")


def tracked_sources(root, header_language):
    tracked = subprocess.check_output(["git", "-C", root, "ls-files", "-z", "--cached"])
    files = []
    seen = set()
    for path in tracked.split(b"\0"):
        if not path or path in seen:
            continue
        seen.add(path)
        name = os.fsdecode(path)
        suffix = os.path.splitext(name)[1]
        if suffix == ".c":
            language = "c"
        elif suffix.lower() == ".h" and suffix != ".H":
            language = header_language
        elif suffix in CPP_EXTENSIONS or suffix.lower() in CPP_EXTENSIONS:
            language = "cpp"
        else:
            continue
        source = root / name
        if not source.is_symlink() and source.is_file():
            files.append((name, language))
    return files


class Timeline:
    def __init__(self, writer, read_rss, interval):
        self.writer = writer
        self.read_rss = read_rss
        self.interval = interval
        self.start = time.perf_counter()
        self.parent = os.getpid()
        self.parent_peak = 0
        self.next_parent_sample = 0

    def elapsed(self):
        return time.perf_counter() - self.start

    def sample(self, pid, process, file_id):
        rss = self.read_rss(pid)
        now = self.elapsed()
        if rss is not None:
            self.writer.writerow((f"{now:.9f}", pid, process, file_id, rss))
        return now, rss or 0

    def sample_parent(self, force=False):
        if force or self.elapsed() >= self.next_parent_sample:
            now, rss = self.sample(self.parent, "python", "")
            self.parent_peak = max(self.parent_peak, rss)
            self.next_parent_sample = now + max(self.interval, 0.020)


def parse_file(binary, root, file_id, name, language, timeline):
    # The worker keeps stdout open until exit. Polling this pipe wakes the parent
    # as soon as a short parse finishes, without waiting out the sample interval.
    read_fd, write_fd = os.pipe()
    pid = None
    start = timeline.elapsed()
    try:
        pid = os.posix_spawn(
            binary, [binary, language, str(root / name)], os.environ,
            file_actions=[(os.POSIX_SPAWN_DUP2, write_fd, 1)],
        )
    finally:
        os.close(write_fd)
        if pid is None:
            os.close(read_fd)

    sample_count = 0
    try:
        while True:
            timeline.sample_parent()
            _, rss = timeline.sample(pid, "parser", file_id)
            sample_count += bool(rss)
            if select.select([read_fd], [], [], timeline.interval)[0]:
                break
        _, status, usage = os.wait4(pid, 0)
    except BaseException:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        raise
    finally:
        os.close(read_fd)
    end = timeline.elapsed()
    exit_code = os.waitstatus_to_exitcode(status)
    peak = usage.ru_maxrss
    if sys.platform == "linux":
        peak *= 1024
    return (file_id, pid, name, language, f"{start:.9f}", f"{end:.9f}",
            exit_code, peak, sample_count)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE.parent / "llvm-project")
    parser.add_argument("--binary", type=Path, default=HERE / "build" / "ts_parse")
    parser.add_argument("--out", type=Path, default=HERE / "results",
                        help="a new directory for the samples, per-file results and graph")
    parser.add_argument("--interval-ms", type=float, default=2.0)
    parser.add_argument("--header-language", choices=("c", "cpp"), default="cpp")
    parser.add_argument("--limit", type=int, help="only process the first N matched files")
    args = parser.parse_args()
    if not 0 < args.interval_ms < float("inf") or (args.limit is not None and args.limit < 1):
        parser.error("sample interval and any file limit must be positive")

    root = args.root.resolve()
    binary = str(args.binary.resolve())
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        parser.error(f"native parser is missing or is not executable: {binary}")
    read_rss = rss_reader()
    if read_rss(os.getpid()) is None:
        parser.error("could not read the Python process RSS")
    revision = subprocess.check_output(
        ["git", "-C", root, "rev-parse", "HEAD"], text=True
    ).strip()
    files = tracked_sources(root, args.header_language)[:args.limit]
    if not files:
        parser.error(f"no matching Git-tracked files in {root}")
    args.out.mkdir(parents=True)

    print(f"Parsing {len(files):,} files from {root}, one process at a time", flush=True)
    recovered = failures = 0
    highest = None
    with (args.out / "rss.csv").open("w", newline="") as samples, (
        args.out / "files.csv"
    ).open("w", newline="") as results:
        sample_writer = csv.writer(samples)
        sample_writer.writerow(("elapsed_s", "pid", "process", "file_id", "rss_bytes"))
        result_writer = csv.writer(results)
        result_writer.writerow(("file_id", "pid", "path", "language", "start_s", "end_s",
                                "exit_code", "peak_rss_bytes", "live_sample_count"))
        timeline = Timeline(sample_writer, read_rss, args.interval_ms / 1000)
        timeline.sample_parent(force=True)
        for file_id, (name, language) in enumerate(files, 1):
            result = parse_file(binary, root, file_id, name, language, timeline)
            result_writer.writerow(result)
            exit_code, peak, _ = result[-3:]
            if highest is None or peak > highest[-2]:
                highest = result
            if exit_code == 2:
                recovered += 1
            elif exit_code != 0:
                failures += 1
                print(f"{name}: parser exited {exit_code}", file=sys.stderr)
            if file_id % 1000 == 0:
                samples.flush()
                results.flush()
                print(f"{file_id:,}/{len(files):,}  {timeline.elapsed():.1f}s", flush=True)
        timeline.sample_parent(force=True)
        elapsed = timeline.elapsed()

    summary = {
        "root": str(root), "head": revision, "platform": sys.platform,
        "header_language": args.header_language, "parser_interval_ms": args.interval_ms,
        "python_interval_ms": max(20, args.interval_ms),
        "files": len(files), "recovered": recovered, "failed": failures,
        "clean": len(files) - recovered - failures, "elapsed_s": elapsed,
        "python_peak_sampled_rss_bytes": timeline.parent_peak,
        "max_parser_peak_rss_bytes": highest[-2], "max_parser_peak_path": highest[2],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)

    from plot_rss import render
    render(args.out)
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
