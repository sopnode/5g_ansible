#!/usr/bin/env python3
"""CSI log collection helper, run on the RAN node (Python 3 standard library only).

  csi_tools.py collect --src /data/csi/csi_per_rb.csv --out-dir DIR [--full]
                       [--windows-json W --window-types section --padding-seconds 0]

Reads the source file ONCE, without copying it first, up to its size at start and up to the
last complete line (the gNB may be writing it), and in that single pass:
  - writes DIR/csi_per_rb.csv.gz (--full, gzip level 1)
  - writes DIR/by_window/<level>/<window>/csi_per_rb.csv.gz for each window of W whose
    RAN node clock interval (ran_start_epoch, ran_end_epoch) overlaps logged data,
    plus DIR/by_window/csi_split_summary.json
  - prints a JSON summary (format, rows, markers, dropped rows, time span)

Timestamp semantics (CSI logger v3.1, also true for v3): a "# TIMESTAMP: t_k" marker is
written at flush time, followed by the rows buffered since the previous flush, so the rows
after marker k were acquired in (t_{k-1}, t_k]. Splitting is done at batch granularity
(one batch = rows between two markers, ~5 s): a batch goes to a window when its acquisition
interval overlaps the window. Each output file keeps the JSON header, the column line and the
markers of its batches, so it is a valid CSV for the Streamlit visualizer.
"""

import argparse
import calendar
import gzip
import json
import os
import re
import sys
import time

FLUSH_PERIOD_S = 5.0
CHUNK = 8 << 20
FILE_NAME = "csi_per_rb.csv"
# Any line that is not a data row (data rows start with the frame number digit)
NON_ROW = re.compile(rb"^(?:[^0-9\n][^\n]*)?\n", re.M)


def parse_marker(line):
    # "# TIMESTAMP: YYYY-mm-dd HH:MM:SS" -> epoch; v3.1 writes UTC, v3 wrote pod local time
    try:
        return calendar.timegm(time.strptime(line[len(b"# TIMESTAMP:"):].strip().decode(),
                                             "%Y-%m-%d %H:%M:%S"))
    except (ValueError, UnicodeDecodeError):
        return None


def safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_.=-]+", "_", str(value).strip())
    return value.strip("_") or "window"


def window_subdir(w):
    # Same layout as scripts/artifacts/split_prometheus_by_timeline.py
    level = w.get("level", "window")
    if level == "scenario":
        return os.path.join("scenario", safe_name(w.get("scenario", "scenario")))
    if level == "step":
        return os.path.join("step", safe_name("__".join([w.get("scenario", ""), w.get("step", "")])))
    if level == "direction":
        return os.path.join("direction", safe_name("__".join([w.get("scenario", ""), w.get("step", ""),
                                                              w.get("direction", "")])))
    return os.path.join(level, safe_name(w.get("window_id", "window")))


def load_windows(path, window_types, padding):
    with open(path) as f:
        data = json.load(f)
    levels = {x.strip() for x in window_types.split(",") if x.strip()}
    windows, skipped = [], []
    for w in data.get("windows", []):
        if w.get("level") not in levels:
            continue
        try:
            s, e = float(w["ran_start_epoch"]), float(w["ran_end_epoch"])
        except (KeyError, TypeError, ValueError):
            skipped.append({"window_id": w.get("window_id"), "reason": "no ran_start_epoch/ran_end_epoch"})
            continue
        windows.append({"w": w, "start": s - padding, "end": e + padding, "ran_start_epoch": s,
                        "ran_end_epoch": e, "batches": 0, "rows": 0,
                        "first_batch_start": None, "last_batch_end": None})
    return windows, skipped


class Collector:
    def __init__(self, windows, split_dir):
        self.windows, self.split_dir = windows, split_dir
        self.json_hdr = self.col_hdr = None
        self.markers, self.batch_rows = [], []
        self.rows_before_first = self.dropped = 0
        self.prev_t = None
        self.interval = None          # acquisition interval of the current batch
        self.marker_line = None
        self.targets = []             # indices of the windows receiving the current batch
        self.started = set()          # windows that already got the marker of the current batch
        self.handles = {}

    def feed(self, block):
        """block: complete lines only."""
        pos = 0
        for m in NON_ROW.finditer(block):
            if m.start() > pos:
                self._rows(block[pos:m.start()])
            self._line(m.group(0))
            pos = m.end()
        if pos < len(block):
            self._rows(block[pos:])

    def _line(self, line):
        if line.startswith(b"# TIMESTAMP:"):
            t = parse_marker(line)
            self.markers.append(t)
            self.batch_rows.append(0)
            if t is None:
                self.interval, self.targets = None, []
            else:
                start = self.prev_t if self.prev_t is not None else t - FLUSH_PERIOD_S
                self.interval = (start, t)
                self.prev_t = t
                self.targets = [i for i, w in enumerate(self.windows) if t > w["start"] and start < w["end"]]
            self.marker_line = line
            self.started = set()
        elif line.startswith(b"# {"):
            if self.json_hdr is None:
                self.json_hdr = line
        elif line.startswith(b"# DROPPED:"):
            try:
                self.dropped += int(line.split(b":", 1)[1])
            except ValueError:
                pass
        elif line.startswith(b"frame") and self.col_hdr is None:
            self.col_hdr = line

    def _rows(self, seg):
        n = seg.count(b"\n")
        if not self.markers:
            self.rows_before_first += n
            return
        self.batch_rows[-1] += n
        for i in self.targets:
            h = self.handles.get(i)
            if h is None:
                w = self.windows[i]
                path = os.path.join(self.split_dir, window_subdir(w["w"]), FILE_NAME + ".gz")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                h = self.handles[i] = gzip.open(path, "wb", compresslevel=1)
                w["path"] = path
                for hdr in (self.json_hdr, self.col_hdr):
                    if hdr:
                        h.write(hdr)
            if i not in self.started:
                self.started.add(i)
                w = self.windows[i]
                h.write(self.marker_line)
                w["batches"] += 1
                if w["first_batch_start"] is None:
                    w["first_batch_start"] = self.interval[0]
                w["last_batch_end"] = self.interval[1]
            h.write(seg)
            self.windows[i]["rows"] += n

    def close(self):
        for h in self.handles.values():
            h.close()


def cmd_collect(args):
    windows, skipped = ([], [])
    if args.windows_json:
        windows, skipped = load_windows(args.windows_json, args.window_types, args.padding_seconds)
    os.makedirs(args.out_dir, exist_ok=True)
    split_dir = os.path.join(args.out_dir, "by_window")
    col = Collector(windows, split_dir)
    full = gzip.open(os.path.join(args.out_dir, FILE_NAME + ".gz"), "wb", compresslevel=1) if args.full else None

    size = os.path.getsize(args.src)       # snapshot: data appended after this point is ignored
    remaining, carry, shrunk = size, b"", False
    with open(args.src, "rb") as f:
        while remaining > 0:
            buf = f.read(min(CHUNK, remaining))
            if not buf:                     # file truncated while reading (gNB restart)
                shrunk = True
                break
            remaining -= len(buf)
            data = carry + buf
            cut = data.rfind(b"\n") + 1
            block, carry = data[:cut], data[cut:]
            if block:
                if full:
                    full.write(block)
                col.feed(block)
    if full:
        full.close()
    col.close()

    meta = {}
    if col.json_hdr:
        try:
            meta = json.loads(col.json_hdr[2:].decode())
        except ValueError:
            meta = {"_error": "unparsable JSON header"}
    valid = [m for m in col.markers if m is not None]
    gaps = [b - a for a, b in zip(valid, valid[1:])]
    summary = {
        "file": args.src,
        "source_bytes_at_start": size,
        "incomplete_last_line_bytes": len(carry),
        "file_shrank_while_reading": shrunk,
        "format_version": meta.get("format_version", "3 (pre-3.1)" if col.json_hdr else "unknown"),
        "columns": meta.get("columns") or (col.col_hdr.decode().strip().split(",") if col.col_hdr else []),
        "rows": sum(col.batch_rows) + col.rows_before_first,
        "rows_before_first_marker": col.rows_before_first,
        "markers": len(col.markers),
        "unparsable_markers": len(col.markers) - len(valid),
        "dropped_rows": col.dropped,
        "first_marker_epoch": valid[0] if valid else None,
        "last_marker_epoch": valid[-1] if valid else None,
        "max_marker_gap_s": max(gaps) if gaps else None,
        "metadata": meta,
    }

    if args.windows_json:
        os.makedirs(split_dir, exist_ok=True)
        out = []
        for w in windows:
            out.append({"window_id": w["w"].get("window_id"), "level": w["w"].get("level"),
                        "path": os.path.relpath(w["path"], args.out_dir) if "path" in w else None,
                        "ran_start_epoch": w["ran_start_epoch"], "ran_end_epoch": w["ran_end_epoch"],
                        "batches": w["batches"], "rows": w["rows"],
                        "first_batch_start": w["first_batch_start"], "last_batch_end": w["last_batch_end"]})
        with open(os.path.join(split_dir, "csi_split_summary.json"), "w") as f:
            json.dump({"csv": args.src, "window_types": args.window_types,
                       "padding_seconds": args.padding_seconds, "granularity": "flush batch (~5 s)",
                       "windows": out, "skipped": skipped}, f, indent=2)
        summary["windows_written"] = len(col.handles)
        summary["windows_empty"] = len(windows) - len(col.handles)
        summary["windows_skipped"] = len(skipped)

    print(json.dumps(summary, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("collect")
    c.add_argument("--src", required=True)
    c.add_argument("--out-dir", required=True)
    c.add_argument("--full", action="store_true", help="write the whole file, gzip -1")
    c.add_argument("--windows-json", default="")
    c.add_argument("--window-types", default="section")
    c.add_argument("--padding-seconds", type=float, default=0.0)
    a = p.parse_args()
    if a.cmd != "collect":
        p.print_help()
        return 2
    return cmd_collect(a)


if __name__ == "__main__":
    sys.exit(main())
