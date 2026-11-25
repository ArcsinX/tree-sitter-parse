# Sequential Tree-sitter RSS benchmark

`benchmark.py` starts a fresh native C executable for each selected C or C++
file. The executable reads the file, creates a Tree-sitter syntax tree,
checks the root for syntax errors, frees the tree and exits. The Python
driver waits for and reaps that child before starting the next file.

## LLVM results

The run on LLVM commit `204998565305` processed 75,849 files in 221.7 seconds.
The largest parser peak was 344.47 MiB. Filtering out the eight files with
parser peak RSS of at least 45 MiB left 75,841 files and a maximum parser
peak of 39.25 MiB.

- [Full RSS timeline, linear axis (PNG)](results/rss-linear.png)
- [Filtered RSS timeline, linear axis (PNG)](results/below-45-mib/rss.png)
- [Excluded files, on-disk sizes, and peak RSS (CSV)](results/below-45-mib/excluded-files.csv)

## Build and run

Requirements are Python 3.10+, Git, CMake 3.18+, and a C compiler on macOS or
Linux. CMake uses checksum-verified Tree-sitter runtime and grammar archives
from the local Cargo cache when available, or downloads them from crates.io.
The benchmark and plotting script use the Python standard library.

```sh
cmake -S . -B build
cmake --build build --parallel 1
python3 benchmark.py --root ../llvm-project --out results-repeat
```

Choose a new output directory for each run. Defaults are `../llvm-project`,
`build/ts_parse`, and `results`, relative to these scripts. Use
`--limit 100 --out build/quick-run` for a short run.

To print an individual syntax tree:

```sh
build/ts_parse cpp ../llvm-project/llvm/lib/Support/CommandLine.cpp --tree
```

Exit code 0 means the tree has no syntax errors, 2 means the tree contains
recovery nodes or missing syntax, and 1 means parsing or reading the file
failed. Recovery is expected for some LLVM test inputs, macro-dependent code,
and source fragments. Parsing uses the original text and does not run a
preprocessor or resolve includes.

## File selection

The benchmark reads regular, non-symlink working-tree files from Git's tracked
path list. It includes source, tests, headers, modules, and common include
fragments. Local build outputs and untracked files are excluded. The recorded
Git commit identifies HEAD, and tracked local edits are included in the inputs.

Lowercase `.c` selects the C grammar. Ambiguous `.h` headers default to C++;
use `--header-language c` to select C for those headers. The other supported
suffixes, including `.C`, `.H`, `.inc` and `.def`, use C++.
See `CPP_EXTENSIONS` in `benchmark.py` for the full list.

## Reading the results

| File | Contents |
| --- | --- |
| `rss.svg` | One elapsed-time axis for both process types and a logarithmic RSS axis in MiB. Each child contributes its live samples and a lifetime peak at its exit time. Marks may overlap in the overview. |
| `rss.csv` | Timestamped RSS in bytes, PID, process type and file ID. |
| `files.csv` | File ID, PID, path, grammar, start and end on the same clock, exit status, OS-reported lifetime peak RSS in bytes, and live sample count. File IDs distinguish processes even when the OS reuses PIDs. |
| `summary.json` | Revision, timings, file counts, and maximum RSS. |

The clock starts after file discovery and stops before plotting. The driver
requests child RSS samples every 2 ms and samples its own RSS every 20 ms
during the parse loop by default. Scheduling and process lifetime affect sample
spacing. On macOS the driver reads RSS via `proc_pid_rusage` and on Linux it
reads `VmRSS` from procfs. It collects the kernel's lifetime high-water mark
with `wait4` for each child, including parses too short for a useful live
sample. A peak plotted at exit does not identify the instant it occurred.
Python's reported maximum is the maximum of its live samples.

To redraw a small time window after finding a file's start and end in
`files.csv`:

```sh
python3 plot_rss.py results --from-s 0 --to-s 0.1 --output build/rss-detail.svg
```

To reproduce the linear plots and the exclusion list from a fresh run:

```sh
python3 plot_rss.py results-repeat --linear --output results-repeat/rss-linear.svg
python3 filter_results.py results-repeat results-repeat-filtered --at-least-mib 45
python3 plot_rss.py results-repeat-filtered --linear --output results-repeat-filtered/rss.svg
```

The filter preserves the original elapsed times and Python RSS samples. It
reads excluded file sizes from the benchmark checkout. Use `--source-root`
if that checkout has moved.
