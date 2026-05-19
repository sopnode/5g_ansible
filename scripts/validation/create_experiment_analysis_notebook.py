#!/usr/bin/env python3
"""Create an automatic supervisor-facing experiment analysis notebook.

The notebook is intentionally broad: it loads every available artifact in a run
directory and produces both metric inventory plots and paper-candidate figures.
It is meant for first-pass experiment review, not as the final camera-ready
plot source.
"""

from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path


def md(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": textwrap.dedent(source).strip().splitlines(True),
    }


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": textwrap.dedent(source).strip().splitlines(True),
    }


def build_notebook(kind: str, title: str) -> dict:
    cells = [
        md(
            f"""
            # {title}

            This notebook is generated automatically from the experiment result
            directory. It tries to show **everything useful first**, then a
            smaller set of candidate plots for the paper: time-series, CDF,
            tail/CCDF, box plots, measurement-quality plots, throughput context,
            radio context, overhead, and pcap validation when available.

            Experiment type: `{kind}`.
            """
        ),
        code(
            """
            from pathlib import Path
            import json
            import shutil
            import subprocess
            import sys
            import tarfile
            import re
            import math
            from collections import defaultdict

            import numpy as np
            import pandas as pd
            import matplotlib.pyplot as plt

            RESULTS_DIR = Path(".").resolve()
            FIG_DIR = RESULTS_DIR / "notebook_figures"
            FIG_DIR.mkdir(exist_ok=True)

            IMSI_TO_UE = {
                "001010000000006": "qhat01",
                "001010000000007": "qhat02",
                "001010000000008": "qhat03",
                "001010000000010": "qhat21",
                "001010000000011": "qhat22",
            }
            IMSI_SUFFIX_TO_UE = {
                imsi[-10:]: ue
                for imsi, ue in IMSI_TO_UE.items()
            }
            UE_IP_TO_UE = {
                "12.1.0.1": "qhat01",
                "12.1.0.2": "qhat02",
                "12.1.0.3": "qhat03",
            }
            UE_ORDER = ["qhat01", "qhat02", "qhat03", "qhat21", "qhat22", "unknown"]
            UE_COLORS = {
                "qhat01": "#1F77B4",
                "qhat02": "#D62728",
                "qhat03": "#2CA02C",
                "qhat21": "#9467BD",
                "qhat22": "#8C564B",
                "unknown": "#7F7F7F",
            }

            plt.rcParams.update({
                "figure.figsize": (11, 5),
                "figure.dpi": 120,
                "savefig.dpi": 220,
                "axes.grid": True,
                "grid.alpha": 0.25,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "legend.frameon": False,
            })

            def savefig(name):
                plt.tight_layout()
                plt.savefig(FIG_DIR / f"{name}.png", bbox_inches="tight")

            def read_json(path):
                path = Path(path)
                return json.loads(path.read_text()) if path.exists() else {}

            def read_text(path):
                path = Path(path)
                return path.read_text(errors="replace") if path.exists() else ""

            def first_existing(*names):
                for name in names:
                    matches = sorted(RESULTS_DIR.glob(name))
                    if matches:
                        return matches[0]
                return None

            def normalize_imsi(value):
                text = str(value).strip()
                if not text or text.lower() in {"nan", "none", "null"}:
                    return ""
                if re.fullmatch(r"\\d+\\.0+", text):
                    text = text.split(".", 1)[0]
                digits = re.sub(r"\\D", "", text)
                if not digits:
                    return ""
                normalized = digits.zfill(15)
                if normalized in IMSI_TO_UE:
                    return normalized
                for suffix in IMSI_SUFFIX_TO_UE:
                    if digits.endswith(suffix) or normalized.endswith(suffix):
                        return "00101" + suffix
                return normalized

            def ue_from_labels(imsi, ue_ip):
                normalized = normalize_imsi(imsi)
                if normalized in IMSI_TO_UE:
                    return IMSI_TO_UE[normalized]
                return UE_IP_TO_UE.get(str(ue_ip).strip(), "unknown")

            metadata = {}
            for name in ["experiment_metadata.json", "validation_metadata.json", "prometheus_export_summary.json"]:
                path = RESULTS_DIR / name
                if path.exists():
                    metadata[name] = read_json(path)
            if (RESULTS_DIR / "run_metadata.yml").exists():
                metadata["run_metadata.yml"] = read_text(RESULTS_DIR / "run_metadata.yml")

            print("Results directory:", RESULTS_DIR)
            print("Metadata files:", list(metadata))
            display(metadata)
            """
        ),
        md(
            """
            ## Experiment Timeline

            Use this table to correlate every plot with the exact scenario,
            step, direction, UE, and `iperf -P` interval that produced it.
            """
        ),
        code(
            """
            timeline_path = RESULTS_DIR / "timeline_summary.csv"
            if timeline_path.exists():
                timeline = pd.read_csv(timeline_path)
                for col in ["start_epoch", "end_epoch", "duration_s", "parallel_streams"]:
                    if col in timeline.columns:
                        timeline[col] = pd.to_numeric(timeline[col], errors="coerce")
                timeline["start_time"] = pd.to_datetime(timeline["start_epoch"], unit="s", utc=True, errors="coerce")
                timeline["end_time"] = pd.to_datetime(timeline["end_epoch"], unit="s", utc=True, errors="coerce")
                display(timeline)

                iperf_timeline = timeline[timeline["level"].eq("iperf_task")].copy()
                if not iperf_timeline.empty:
                    cols = [
                        "scenario",
                        "step",
                        "ue",
                        "direction",
                        "parallel_streams",
                        "start_local",
                        "end_local",
                        "duration_s",
                    ]
                    sort_cols = [c for c in ["start_epoch", "scenario", "step", "ue"] if c in iperf_timeline.columns]
                    iperf_display = iperf_timeline.sort_values(sort_cols) if sort_cols else iperf_timeline
                    display(
                        iperf_display[[c for c in cols if c in iperf_display.columns]]
                    )
            else:
                timeline = pd.DataFrame()
                print("No timeline_summary.csv found.")

            scenario_windows = timeline[timeline["level"].eq("scenario")].copy() if not timeline.empty else pd.DataFrame()
            task_windows = timeline[timeline["level"].eq("iperf_task")].copy() if not timeline.empty else pd.DataFrame()

            def timeline_label(row):
                label = str(row.get("scenario", "")).strip() or str(row.get("name", "")).strip()
                if row.get("step", "") and row.get("level", "") != "scenario":
                    label += f" / {row.get('step')}"
                if row.get("direction", ""):
                    label += f" / {row.get('direction')}"
                if row.get("ue", ""):
                    label += f" / {row.get('ue')}"
                return label or str(row.get("window_id", "window"))

            def annotate_scenario_windows(ax, xmin=None, xmax=None, windows=None):
                if windows is None:
                    windows = scenario_windows
                if windows is None or windows.empty:
                    return
                colors = ["#dbeafe", "#dcfce7", "#fee2e2", "#fef3c7", "#ede9fe", "#e0f2fe"]
                xmin = pd.to_datetime(xmin, utc=True) if xmin is not None else None
                xmax = pd.to_datetime(xmax, utc=True) if xmax is not None else None
                for idx, row in windows.sort_values("start_time").reset_index(drop=True).iterrows():
                    start = row.get("start_time")
                    end = row.get("end_time")
                    if pd.isna(start) or pd.isna(end):
                        continue
                    if xmin is not None and end < xmin:
                        continue
                    if xmax is not None and start > xmax:
                        continue
                    ax.axvspan(start, end, color=colors[idx % len(colors)], alpha=0.18, zorder=0)
                    mid = start + (end - start) / 2
                    ax.text(
                        mid,
                        1.01,
                        timeline_label(row),
                        transform=ax.get_xaxis_transform(),
                        ha="center",
                        va="bottom",
                        fontsize=6.5,
                        bbox={"boxstyle": "round,pad=0.18", "fc": "white", "ec": colors[idx % len(colors)], "alpha": 0.86},
                        clip_on=False,
                    )
            """
        ),
        md(
            """
            ## Load iperf Artifacts

            This section reads `iperf_*.json` files either directly from the
            directory tree or from fetched `qhatXX.tgz` archives.
            """
        ),
        code(
            r"""
            IPERF_RE = re.compile(
                r"scenario=(?P<scenario>[^/]+)/step=(?P<step>[^/]+)/direction=(?P<direction>[^/]+)/iperf_(?P<ue>[^/.]+)\.json"
            )

            def parse_iperf_payload(payload, source_name):
                match = IPERF_RE.search(source_name)
                info = match.groupdict() if match else {
                    "scenario": "unknown",
                    "step": "unknown",
                    "direction": "unknown",
                    "ue": Path(source_name).stem.replace("iperf_", ""),
                }

                end = payload.get("end", {}) if isinstance(payload, dict) else {}
                start = payload.get("start", {}) if isinstance(payload, dict) else {}
                ts = (start.get("timestamp", {}) or {}).get("timesecs")

                def section_value(section_name, key):
                    section = end.get(section_name, {})
                    return section.get(key) if isinstance(section, dict) else None

                bps_candidates = [
                    section_value("sum_received", "bits_per_second"),
                    section_value("sum_sent", "bits_per_second"),
                    section_value("sum", "bits_per_second"),
                ]
                bps = next((x for x in bps_candidates if x is not None), np.nan)
                retransmits = section_value("sum_sent", "retransmits")
                jitter = section_value("sum", "jitter_ms")
                lost_percent = section_value("sum", "lost_percent")

                server = payload.get("server_output_json") or {}
                if isinstance(server, str):
                    try:
                        server = json.loads(server)
                    except json.JSONDecodeError:
                        server = {}
                server_end = server.get("end", {}) if isinstance(server, dict) else {}
                server_retx = None
                if isinstance(server_end, dict):
                    server_retx = (server_end.get("sum_sent") or {}).get("retransmits")

                row = dict(info)
                row.update({
                    "source": source_name,
                    "start_time": pd.to_datetime(ts, unit="s", utc=True, errors="coerce"),
                    "throughput_mbps": float(bps) / 1e6 if bps is not None else np.nan,
                    "client_retransmits": retransmits,
                    "server_retransmits": server_retx,
                    "jitter_ms": jitter,
                    "lost_percent": lost_percent,
                    "error": payload.get("error", "") if isinstance(payload, dict) else "",
                })
                return row

            def iter_iperf_payloads(results_dir):
                for path in results_dir.rglob("iperf_*.json"):
                    try:
                        yield json.loads(path.read_text()), str(path.relative_to(results_dir))
                    except json.JSONDecodeError:
                        print(f"Could not parse {path}")

                for archive in results_dir.glob("*.tgz"):
                    try:
                        with tarfile.open(archive) as tf:
                            for member in tf.getmembers():
                                if not member.name.endswith(".json") or "iperf_" not in member.name:
                                    continue
                                fp = tf.extractfile(member)
                                if fp is None:
                                    continue
                                try:
                                    yield json.loads(fp.read().decode()), member.name
                                except json.JSONDecodeError:
                                    print(f"Could not parse {archive}:{member.name}")
                    except tarfile.TarError:
                        print(f"Could not open archive {archive}")

            iperf = pd.DataFrame(parse_iperf_payload(payload, source) for payload, source in iter_iperf_payloads(RESULTS_DIR))
            if iperf.empty:
                print("No iperf JSON logs found.")
            else:
                iperf = iperf.sort_values(["scenario", "step", "direction", "ue"])
                display(iperf)

                summary = iperf.groupby(["scenario", "step", "direction", "ue"], dropna=False).agg(
                    throughput_mbps=("throughput_mbps", "mean"),
                    client_retransmits=("client_retransmits", "sum"),
                    server_retransmits=("server_retransmits", "sum"),
                    runs=("source", "count"),
                ).reset_index()
                display(summary)
            """
        ),
        code(
            """
            if not iperf.empty:
                plot_df = iperf.copy()
                plot_df["case"] = plot_df["scenario"] + "\\n" + plot_df["step"] + "\\n" + plot_df["direction"]
                pivot = plot_df.pivot_table(index="case", columns="ue", values="throughput_mbps", aggfunc="mean")
                ax = pivot.plot(kind="bar", width=0.85, figsize=(max(12, len(pivot) * 0.6), 5))
                ax.set_ylabel("Throughput (Mb/s)")
                ax.set_xlabel("")
                ax.set_title("iperf throughput by scenario, step, direction, and UE")
                plt.xticks(rotation=45, ha="right")
                savefig("iperf_throughput_by_case")

                retx_cols = [c for c in ["client_retransmits", "server_retransmits"] if c in iperf.columns]
                if retx_cols:
                    retx = plot_df.groupby("case")[retx_cols].sum()
                    ax = retx.plot(kind="bar", stacked=False, figsize=(max(12, len(retx) * 0.55), 4))
                    ax.set_ylabel("Retransmits")
                    ax.set_title("TCP retransmissions by case")
                    plt.xticks(rotation=45, ha="right")
                    savefig("iperf_retransmits_by_case")
            """
        ),
        md(
            """
            ## ICMP Ping Artifacts

            ICMP validation uses UE-visible `ping` RTT as an external sanity
            signal. It should move with the probe ICMP latency, especially
            under controlled delay, but exact equality is not expected because
            the probes observe N3 rather than the UE socket.
            """
        ),
        code(
            """
            ping_path = RESULTS_DIR / "ping_rtt.csv"
            if ping_path.exists():
                ping = pd.read_csv(ping_path)
                ping["rtt_ms"] = pd.to_numeric(ping["rtt_ms"], errors="coerce")
                ping["time_utc"] = pd.to_datetime(ping["time_utc"], utc=True, errors="coerce")
                display(ping.head())

                ping_summary = ping.groupby(["scenario", "step", "direction", "ue"], dropna=False).agg(
                    count=("rtt_ms", "count"),
                    mean_ms=("rtt_ms", "mean"),
                    p50_ms=("rtt_ms", "median"),
                    p95_ms=("rtt_ms", lambda s: s.quantile(0.95)),
                    max_ms=("rtt_ms", "max"),
                ).reset_index()
                display(ping_summary)

                fig, ax = plt.subplots(figsize=(11, 5))
                for (step, ue), group in ping.dropna(subset=["rtt_ms"]).groupby(["step", "ue"]):
                    values = np.sort(group["rtt_ms"].to_numpy())
                    if len(values) == 0:
                        continue
                    y = np.arange(1, len(values) + 1) / len(values)
                    ax.plot(values, y, label=f"{step} / {ue}")
                ax.set_xlabel("ping RTT (ms)")
                ax.set_ylabel("CDF")
                ax.set_title("UE-visible ICMP ping RTT")
                ax.legend()
                savefig("icmp_ping_rtt_cdf")
            else:
                ping = pd.DataFrame()
                print("No ping_rtt.csv found.")
            """
        ),
        md(
            """
            ## Load Prometheus Export

            This section loads `prometheus_timeseries.csv`,
            `prometheus_timeseries_1s.csv`, or any `prometheus*.csv(.gz)` file found
            in the run directory.
            """
        ),
        code(
            """
            prom_path = first_existing(
                "prometheus_timeseries_1s.csv",
                "prometheus_timeseries.csv",
                "prometheus_timeseries.csv.gz",
                "prometheus*.csv",
                "prometheus*.csv.gz",
            )
            prom_label_cols = [
                "query_name", "__name__", "imsi", "ue_ip", "slice", "probe_role",
                "mode", "direction", "reason", "teid", "ue_rnti", "rnti", "round",
                "instance", "pod", "namespace", "container", "job", "stream",
                "metric_json",
            ]

            def prepare_prometheus_df(df):
                if df.empty:
                    return df
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True, errors="coerce")
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
                for col in prom_label_cols:
                    if col not in df.columns:
                        df[col] = ""
                    df[col] = df[col].fillna("").astype(str)
                df["imsi_normalized"] = df["imsi"].map(normalize_imsi)
                df["ue"] = [
                    ue_from_labels(imsi, ue_ip)
                    for imsi, ue_ip in zip(df["imsi"], df["ue_ip"])
                ]
                df["logical_direction"] = df["direction"]
                df.loc[df["logical_direction"].eq("") & df["mode"].eq("core"), "logical_direction"] = "uplink"
                df.loc[df["logical_direction"].eq("") & df["mode"].eq("ran"), "logical_direction"] = "downlink"
                df["series"] = (
                    df[["ue", "ue_ip", "imsi_normalized", "slice", "probe_role", "mode", "logical_direction", "container", "pod", "instance", "reason"]]
                    .astype(str)
                    .agg(" ".join, axis=1)
                    .str.replace(r"\\s+", " ", regex=True)
                    .str.strip()
                )
                return df

            def read_prometheus_export(path):
                dtype = {col: "string" for col in prom_label_cols}
                try:
                    df = pd.read_csv(path, dtype=dtype)
                except pd.errors.ParserError as exc:
                    print(f"Warning: {Path(path).name} is not clean CSV: {exc}")
                    print("Retrying with the slower Python CSV parser and skipping malformed lines.")
                    print("For paper figures, regenerate prometheus_timeseries.csv if this happened after a disk-quota failure.")
                    df = pd.read_csv(path, dtype=dtype, engine="python", on_bad_lines="skip")
                return prepare_prometheus_df(df)

            if prom_path is None:
                prom = pd.DataFrame()
                print("No Prometheus CSV found.")
            else:
                print("Prometheus CSV:", prom_path.name)
                prom = read_prometheus_export(prom_path)

                query_summary = prom.groupby("query_name").agg(
                    rows=("value", "size"),
                    series=("series", "nunique"),
                    first=("timestamp", "min"),
                    last=("timestamp", "max"),
                    mean=("value", "mean"),
                    p95=("value", lambda s: s.dropna().quantile(0.95) if s.notna().any() else np.nan),
                ).sort_values(["rows", "series"], ascending=False)
                display(query_summary)
            """
        ),
        md(
            """
            ## Metric Inventory: Show Everything

            The next cell plots every `query_name` present in the Prometheus CSV.
            It caps each metric at the most populated series so the notebook
            remains readable.
            """
        ),
        code(
            """
            def plot_query(query_name, max_series=12, smooth=1, ax=None):
                if prom.empty:
                    print("No Prometheus data.")
                    return None
                df = prom[prom["query_name"] == query_name].dropna(subset=["value"]).copy()
                if df.empty:
                    print(f"No data for {query_name}")
                    return None
                series_order = df.groupby("series")["value"].count().sort_values(ascending=False).head(max_series).index
                df = df[df["series"].isin(series_order)].sort_values("timestamp")
                if ax is None:
                    _, ax = plt.subplots(figsize=(11, 4))
                for label, group in df.groupby("series", sort=False):
                    y = group["value"].rolling(smooth, min_periods=1).mean() if smooth > 1 else group["value"]
                    ax.plot(group["timestamp"], y, label=label or query_name, linewidth=1.5)
                ax.set_title(query_name)
                ax.set_ylabel(query_name)
                ax.set_xlabel("")
                ax.legend(fontsize=7, loc="best")
                annotate_scenario_windows(ax, df["timestamp"].min(), df["timestamp"].max())
                return ax

            if not prom.empty:
                for query_name in sorted(prom["query_name"].dropna().unique()):
                    plot_query(query_name, max_series=10, smooth=3)
                    savefig(f"all_metric_{re.sub(r'[^A-Za-z0-9_.-]+', '_', query_name)}")
                    plt.show()
            """
        ),
        md(
            """
            ## Paper Candidate: Direct Latency Time Series

            Prefer `direct_mean_ms_5s` if present; otherwise fall back to older
            mean/latest query names.
            """
        ),
        code(
            """
            def choose_query(*names, regex=None):
                if prom.empty:
                    return None
                available = set(prom["query_name"].dropna().unique())
                for name in names:
                    if name in available:
                        return name
                if regex:
                    matches = sorted(q for q in available if re.search(regex, q))
                    return matches[0] if matches else None
                return None

            latency_query = choose_query(
                "direct_mean_ms_5s",
                "direct_rtt_mean_ms",
                "direct_latest_ms",
                regex=r"direct.*(mean|latest).*ms",
            )
            p50_query = choose_query(
                "direct_median_1s_ms_by_kind",
                "direct_median_1s_ms",
                "direct_p50_ms_5s_by_kind",
                "direct_p50_ms_5s",
                regex=r"direct.*(median|p50).*ms",
            )
            p95_query = choose_query("direct_p95_ms_5s", "direct_rtt_p95_ms", regex=r"direct.*p95.*ms")
            p99_query = choose_query("direct_p99_ms_5s", regex=r"direct.*p99.*ms")

            print("Selected latency query:", latency_query)
            print("Selected quantile queries:", p50_query, p95_query, p99_query)

            def plot_latency_grid(query_name, title, smooth=5):
                if not query_name:
                    print("No latency query available.")
                    return
                df = prom[prom["query_name"] == query_name].dropna(subset=["value"]).copy()
                df = df[df["ue"].isin(UE_ORDER)]
                if df.empty:
                    print(f"No data for {query_name}")
                    return
                panels = [
                    ("downlink", "gnb", "Downlink, gNB"),
                    ("downlink", "upf", "Downlink, UPF"),
                    ("uplink", "gnb", "Uplink, gNB"),
                    ("uplink", "upf", "Uplink, UPF"),
                ]
                fig, axes = plt.subplots(2, 2, figsize=(13, 7), sharex=True)
                for ax, (direction, probe, subtitle) in zip(axes.flat, panels):
                    subset = df[(df["logical_direction"] == direction) & (df["probe_role"] == probe)]
                    for ue in [u for u in UE_ORDER if u in set(subset["ue"])]:
                        group = subset[subset["ue"] == ue].sort_values("timestamp")
                        y = group["value"].rolling(smooth, min_periods=1).mean()
                        ax.plot(group["timestamp"], y, label=ue, color=UE_COLORS.get(ue), linewidth=1.8)
                    ax.set_title(subtitle)
                    ax.set_ylabel("Latency (ms)")
                    ax.legend(fontsize=8)
                    annotate_scenario_windows(ax, subset["timestamp"].min() if not subset.empty else None, subset["timestamp"].max() if not subset.empty else None)
                fig.suptitle(title)
                savefig("candidate_direct_latency_timeseries")
                plt.show()

            plot_latency_grid(latency_query, "Direct latency over time")
            """
        ),
        md(
            """
            ## Per-Scenario Deep Dive

            The cells below use `by_window/scenario/<scenario>/prometheus_timeseries.csv(.gz)`
            so each experiment can be inspected without the rest of the campaign
            mixed in. These are the figures to use when you want to ask: *what
            happened inside this one scenario?*
            """
        ),
        code(
            """
            def choose_query_in_df(df, *names, regex=None):
                if df.empty:
                    return None
                available = set(df["query_name"].dropna().unique())
                for name in names:
                    if name in available:
                        return name
                if regex:
                    matches = sorted(q for q in available if re.search(regex, q))
                    return matches[0] if matches else None
                return None

            def plot_scenario_query(ax, df, query_name, title, ylabel, max_series=8, smooth=3):
                if not query_name:
                    ax.text(0.5, 0.5, "No matching query", ha="center", va="center", transform=ax.transAxes)
                    ax.set_title(title)
                    return
                subset = df[df["query_name"].eq(query_name)].dropna(subset=["timestamp", "value"]).copy()
                if subset.empty:
                    ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
                    ax.set_title(title)
                    return
                keep = subset.groupby("series")["value"].count().sort_values(ascending=False).head(max_series).index
                subset = subset[subset["series"].isin(keep)].sort_values("timestamp")
                for label, group in subset.groupby("series", sort=False):
                    y = group["value"].rolling(smooth, min_periods=1, center=True).mean()
                    color = UE_COLORS.get(group["ue"].iloc[0], None)
                    ax.plot(group["timestamp"], y, label=label[:72], color=color, linewidth=1.5)
                ax.set_title(f"{title}\\n{query_name}", fontsize=9)
                ax.set_ylabel(ylabel)
                ax.legend(fontsize=6)

            def plot_scenario_deep_dive(csv_path):
                scenario_name = csv_path.parent.name
                scenario_df = read_prometheus_export(csv_path)
                if scenario_df.empty:
                    print(f"No data for {scenario_name}")
                    return
                metadata_path = csv_path.parent / "window_metadata.json"
                metadata = read_json(metadata_path).get("window", {}) if metadata_path.exists() else {}
                print("\\n" + "=" * 96)
                print("Scenario:", scenario_name)
                if metadata:
                    print("Window:", metadata.get("start_local"), "->", metadata.get("end_local"), f"({metadata.get('duration_s')} s)")
                print("=" * 96)
                display(
                    scenario_df.groupby("query_name").agg(
                        rows=("value", "size"),
                        series=("series", "nunique"),
                        first=("timestamp", "min"),
                        last=("timestamp", "max"),
                        mean=("value", "mean"),
                        p95=("value", lambda s: s.dropna().quantile(0.95) if s.notna().any() else np.nan),
                    ).sort_values(["rows", "series"], ascending=False).head(30)
                )

                panels = [
                    (
                        "Direct mean latency",
                        choose_query_in_df(scenario_df, "direct_mean_ms_5s", "direct_rtt_mean_ms", regex=r"direct.*mean.*ms"),
                        "Latency (ms)",
                    ),
                    (
                        "Direct p95 latency",
                        choose_query_in_df(scenario_df, "direct_p95_ms_5s", "direct_rtt_p95_ms", regex=r"direct.*p95.*ms"),
                        "Latency (ms)",
                    ),
                    (
                        "Latency sample rate",
                        choose_query_in_df(scenario_df, "direct_event_rate_hz_5s", regex=r"direct.*event.*rate"),
                        "Samples/s",
                    ),
                    (
                        "Same-packet pair rate",
                        choose_query_in_df(scenario_df, "same_packet_pair_rate_hz_5s", "same_packet_pair_rate_hz", regex=r"same_packet.*pair.*rate"),
                        "Pairs/s",
                    ),
                    (
                        "MAC / throughput",
                        choose_query_in_df(scenario_df, "mac_throughput_total_bps", "mac_throughput_per_rnti_bps", regex=r"(mac|throughput)"),
                        "bit/s",
                    ),
                    (
                        "Radio quality",
                        choose_query_in_df(scenario_df, regex=r"(mcs|bler|harq|snr|sinr|rsrp|rsrq|prach)"),
                        "value",
                    ),
                ]

                fig, axes = plt.subplots(3, 2, figsize=(14, 9), sharex=True)
                for ax, (title, query, ylabel) in zip(axes.flat, panels):
                    plot_scenario_query(ax, scenario_df, query, title, ylabel)
                fig.suptitle(f"Scenario deep dive: {scenario_name}", fontsize=14, fontweight="bold")
                fig.autofmt_xdate()
                savefig(f"scenario_deep_dive_{re.sub(r'[^A-Za-z0-9_.-]+', '_', scenario_name)}")
                plt.show()

            scenario_root = RESULTS_DIR / "by_window" / "scenario"
            scenario_csvs = sorted(
                list(scenario_root.glob("*/prometheus_timeseries.csv"))
                + list(scenario_root.glob("*/prometheus_timeseries.csv.gz"))
            )
            print(f"Scenario metric windows found: {len(scenario_csvs)}")
            for csv_path in scenario_csvs:
                plot_scenario_deep_dive(csv_path)
            """
        ),
        md(
            """
            ## Paper Candidate: Latency CDF, Tail CCDF, and Box Plots
            """
        ),
        code(
            """
            def ecdf(values):
                values = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
                values = values[np.isfinite(values)]
                values = values[values >= 0]
                values.sort()
                if len(values) == 0:
                    return values, values
                y = np.arange(1, len(values) + 1) / len(values)
                return values, y

            def eccdf(values):
                x, y = ecdf(values)
                return x, 1 - y + (1 / len(y) if len(y) else 0)

            def latency_distribution_plots(query_name):
                if not query_name:
                    print("No latency query available.")
                    return
                df = prom[prom["query_name"] == query_name].dropna(subset=["value"]).copy()
                df = df[df["ue"].isin(UE_ORDER)]
                panels = [
                    ("downlink", "gnb", "Downlink, gNB"),
                    ("downlink", "upf", "Downlink, UPF"),
                    ("uplink", "gnb", "Uplink, gNB"),
                    ("uplink", "upf", "Uplink, UPF"),
                ]

                fig, axes = plt.subplots(2, 2, figsize=(13, 7))
                for ax, (direction, probe, subtitle) in zip(axes.flat, panels):
                    subset = df[(df["logical_direction"] == direction) & (df["probe_role"] == probe)]
                    for ue in [u for u in UE_ORDER if u in set(subset["ue"])]:
                        x, y = ecdf(subset.loc[subset["ue"] == ue, "value"])
                        if len(x):
                            ax.plot(x, y, label=ue, color=UE_COLORS.get(ue), linewidth=1.8)
                    ax.set_title(subtitle)
                    ax.set_xlabel("Latency (ms)")
                    ax.set_ylabel("CDF")
                    ax.legend(fontsize=8)
                fig.suptitle("Direct latency CDF")
                savefig("candidate_direct_latency_cdf")
                plt.show()

                fig, axes = plt.subplots(2, 2, figsize=(13, 7))
                for ax, (direction, probe, subtitle) in zip(axes.flat, panels):
                    subset = df[(df["logical_direction"] == direction) & (df["probe_role"] == probe)]
                    for ue in [u for u in UE_ORDER if u in set(subset["ue"])]:
                        x, y = eccdf(subset.loc[subset["ue"] == ue, "value"])
                        if len(x):
                            ax.semilogy(x, y, label=ue, color=UE_COLORS.get(ue), linewidth=1.8)
                    ax.set_title(subtitle)
                    ax.set_xlabel("Latency (ms)")
                    ax.set_ylabel("CCDF")
                    ax.legend(fontsize=8)
                fig.suptitle("Direct latency tail CCDF")
                savefig("candidate_direct_latency_tail_ccdf")
                plt.show()

                box_df = df[df["ue"].isin([u for u in UE_ORDER if u != "unknown"])].copy()
                box_df["case"] = box_df["ue"] + "\\n" + box_df["logical_direction"] + "\\n" + box_df["probe_role"]
                order = sorted(box_df["case"].unique())
                data = [box_df.loc[box_df["case"] == case, "value"].dropna() for case in order]
                plt.figure(figsize=(max(11, len(order) * 0.55), 4.5))
                plt.boxplot(data, labels=order, showfliers=False)
                plt.ylabel("Latency (ms)")
                plt.title("Direct latency box plot (outliers hidden)")
                plt.xticks(rotation=45, ha="right")
                savefig("candidate_direct_latency_boxplot")
                plt.show()

            latency_distribution_plots(latency_query)
            """
        ),
        md(
            """
            ## Paper Candidate: Latency Decomposition Gap

            `UPF - gNB` gives a first view of where additional delay appears.
            """
        ),
        code(
            """
            def plot_latency_gap(query_name):
                if not query_name:
                    print("No latency query available.")
                    return
                df = prom[prom["query_name"] == query_name].dropna(subset=["value"]).copy()
                df = df[df["probe_role"].isin(["gnb", "upf"]) & df["ue"].isin(UE_ORDER)]
                if df.empty:
                    print("No gNB/UPF latency data.")
                    return
                fig, axes = plt.subplots(1, 2, figsize=(13, 4), sharex=True)
                for ax, direction in zip(axes, ["downlink", "uplink"]):
                    for ue in [u for u in UE_ORDER if u in set(df["ue"])]:
                        subset = df[(df["logical_direction"] == direction) & (df["ue"] == ue)]
                        pivot = subset.pivot_table(index="timestamp", columns="probe_role", values="value", aggfunc="mean")
                        if "upf" not in pivot or "gnb" not in pivot:
                            continue
                        gap = (pivot["upf"] - pivot["gnb"]).dropna()
                        ax.plot(gap.index, gap.rolling(5, min_periods=1).mean(), label=ue, color=UE_COLORS.get(ue))
                    ax.axhline(0, color="black", linewidth=0.8)
                    ax.set_title(f"{direction.capitalize()} UPF-gNB gap")
                    ax.set_ylabel("Latency gap (ms)")
                    ax.legend(fontsize=8)
                    annotate_scenario_windows(ax, df["timestamp"].min(), df["timestamp"].max())
                savefig("candidate_latency_gap_timeseries")
                plt.show()

            plot_latency_gap(latency_query)
            """
        ),
        md(
            """
            ## Paper Candidate: Direct Quantiles Over Time
            """
        ),
        code(
            """
            quantile_queries = [q for q in [p50_query, p95_query, p99_query] if q]
            if quantile_queries:
                fig, axes = plt.subplots(len(quantile_queries), 1, figsize=(12, 3.2 * len(quantile_queries)), sharex=True)
                if len(quantile_queries) == 1:
                    axes = [axes]
                for ax, q in zip(axes, quantile_queries):
                    df = prom[prom["query_name"] == q].dropna(subset=["value"])
                    for (ue, direction, probe), group in df.groupby(["ue", "logical_direction", "probe_role"]):
                        if ue == "unknown":
                            continue
                        label = f"{ue} {direction} {probe}"
                        group = group.sort_values("timestamp")
                        ax.plot(group["timestamp"], group["value"].rolling(5, min_periods=1).mean(), label=label)
                    ax.set_title(q)
                    ax.set_ylabel("Latency (ms)")
                    ax.legend(fontsize=7, ncol=2)
                    annotate_scenario_windows(ax, df["timestamp"].min() if not df.empty else None, df["timestamp"].max() if not df.empty else None)
                savefig("candidate_direct_latency_quantiles")
                plt.show()
            else:
                print("No direct quantile queries found.")
            """
        ),
        md(
            """
            ## Paper Candidate: Same-Packet Validation and Measurement Quality
            """
        ),
        code(
            """
            same_queries = {
                "upf": choose_query("same_packet_upf_rtt_mean_ms_5s", "same_packet_upf_rtt_mean_ms", regex=r"same_packet.*upf.*mean.*ms"),
                "gnb": choose_query("same_packet_gnb_rtt_mean_ms_5s", "same_packet_gnb_rtt_mean_ms", regex=r"same_packet.*gnb.*mean.*ms"),
                "gap": choose_query("same_packet_gap_mean_ms_5s", "same_packet_gap_mean_ms", regex=r"same_packet.*gap.*mean.*ms"),
                "pair_rate": choose_query("same_packet_pair_rate_hz_5s", "same_packet_pair_rate_hz", regex=r"same_packet.*pair.*rate"),
                "rejections": choose_query("same_packet_rejections_hz_5s", "same_packet_rejected_hz_5s", regex=r"same_packet.*reject"),
            }
            print(same_queries)

            for label, query_name in same_queries.items():
                if query_name:
                    plot_query(query_name, max_series=12, smooth=5)
                    savefig(f"candidate_same_packet_{label}")
                    plt.show()

            if same_queries["upf"] and same_queries["gnb"]:
                fig, axes = plt.subplots(1, 2, figsize=(13, 4))
                for ax, direction in zip(axes, ["downlink", "uplink"]):
                    for key, linestyle in [("gnb", "-"), ("upf", "--")]:
                        q = same_queries[key]
                        df = prom[(prom["query_name"] == q) & (prom["logical_direction"] == direction)]
                        for ue in [u for u in UE_ORDER if u in set(df["ue"])]:
                            if ue == "unknown":
                                continue
                            x, y = ecdf(df.loc[df["ue"] == ue, "value"])
                            if len(x):
                                ax.plot(x, y, color=UE_COLORS.get(ue), linestyle=linestyle, label=f"{ue} {key}")
                    ax.set_title(f"{direction.capitalize()} same-packet RTT CDF")
                    ax.set_xlabel("RTT (ms)")
                    ax.set_ylabel("CDF")
                    ax.legend(fontsize=7)
                savefig("candidate_same_packet_rtt_cdf")
                plt.show()
            """
        ),
        md(
            """
            ## Paper Candidate: Throughput, Radio Context, and Overhead
            """
        ),
        code(
            """
            def plot_matching_queries(pattern, title, max_series=12):
                if prom.empty:
                    return
                matches = sorted(q for q in prom["query_name"].dropna().unique() if re.search(pattern, q, re.I))
                print(title, matches)
                for q in matches:
                    plot_query(q, max_series=max_series, smooth=5)
                    savefig(f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', title.lower())}_{q}")
                    plt.show()

            plot_matching_queries(r"(throughput|mac)", "Throughput / MAC")
            plot_matching_queries(r"(mcs|bler|harq|snr|sinr|rsrp|rsrq|cqi|prach|rf_)", "Radio context")
            plot_matching_queries(r"(cpu|memory|container)", "Monitoring overhead")
            plot_matching_queries(r"(lost|reject|pair_rate)", "Measurement quality")

            bpftool_path = RESULTS_DIR / "bpftool_overhead_summary.csv"
            if bpftool_path.exists():
                bpfo = pd.read_csv(bpftool_path)
                for col in ["run_cnt_delta", "run_time_ns_delta", "ns_per_run"]:
                    if col in bpfo.columns:
                        bpfo[col] = pd.to_numeric(bpfo[col], errors="coerce")
                if bpfo.empty:
                    print("bpftool summary exists but contains no paired samples.")
                else:
                    display(bpfo.sort_values("ns_per_run", ascending=False).head(30))
                    plot_df = bpfo.dropna(subset=["ns_per_run"]).sort_values("ns_per_run", ascending=False).head(12)
                    if not plot_df.empty:
                        plot_df["program"] = (
                            plot_df["prog_name"].fillna("").astype(str)
                            + "\\n"
                            + plot_df["prog_type"].fillna("").astype(str)
                            + " id="
                            + plot_df["prog_id"].astype(str)
                        )
                        ax = plot_df.plot.barh(x="program", y="ns_per_run", legend=False, figsize=(8, 5))
                        ax.invert_yaxis()
                        ax.set_xlabel("ns per BPF program run")
                        ax.set_ylabel("")
                        ax.set_title("BPF packet-path overhead from bpftool")
                        savefig("bpftool_packet_path_overhead")
                        plt.show()
            """
        ),
        code(
            """
            if latency_query and not prom.empty:
                latency = prom[prom["query_name"] == latency_query].copy()
                throughput_q = choose_query("mac_throughput_total_bps", regex=r"(mac|throughput).*total|throughput")
                if throughput_q:
                    th = prom[prom["query_name"] == throughput_q].copy()
                    # Join by nearest second after rounding. This is a coarse diagnostic only.
                    latency["t"] = latency["timestamp"].dt.floor("s")
                    th["t"] = th["timestamp"].dt.floor("s")
                    lat_agg = latency.groupby("t")["value"].mean().reset_index(name="latency_ms")
                    th_agg = th.groupby("t")["value"].sum().reset_index(name="throughput_bps")
                    merged = pd.merge(lat_agg, th_agg, on="t", how="inner")
                    if not merged.empty:
                        plt.figure(figsize=(7, 5))
                        plt.scatter(merged["throughput_bps"] / 1e6, merged["latency_ms"], s=12, alpha=0.45)
                        plt.xlabel("Throughput / MAC activity (Mb/s)")
                        plt.ylabel("Mean latency (ms)")
                        plt.title("Latency vs throughput diagnostic")
                        savefig("candidate_latency_vs_throughput")
                        plt.show()
                    else:
                        print("No overlapping latency/throughput timestamps.")
                else:
                    print("No throughput query found.")
            """
        ),
        md(
            """
            ## Pcap RTT Validation, if Available
            """
        ),
        code(
            """
            pcap_path = RESULTS_DIR / "pcap_tcp_rtt.csv"
            if pcap_path.exists():
                try:
                    has_pcap_samples = sum(1 for _ in pcap_path.open("r", encoding="utf-8", errors="ignore")) > 1
                except OSError:
                    has_pcap_samples = False
            else:
                has_pcap_samples = False

            def find_pcap_extractor():
                for root in [RESULTS_DIR, *RESULTS_DIR.parents]:
                    candidate = root / "scripts" / "validation" / "pcap_tcp_rtt_extract.py"
                    if candidate.exists():
                        return candidate
                return None

            if not has_pcap_samples:
                pcap_dir = RESULTS_DIR / "pcaps"
                pcap_files = list(pcap_dir.glob("*.pcap")) + list(pcap_dir.glob("*.pcapng")) + list(pcap_dir.glob("*.pcap.gz")) + list(pcap_dir.glob("*.pcapng.gz"))
                extractor = find_pcap_extractor()
                tshark = shutil.which("tshark")
                if not pcap_files:
                    print("No pcap files found under pcaps/.")
                elif not tshark:
                    print("pcap_tcp_rtt.csv is missing or empty, but tshark is not available in this notebook environment.")
                    print("Install Wireshark/tshark locally, then rerun this cell.")
                elif not extractor:
                    print("Could not find scripts/validation/pcap_tcp_rtt_extract.py from this result directory.")
                else:
                    print(f"Reconstructing pcap RTT locally using {tshark}...")
                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(extractor),
                            "--pcap-dir",
                            str(pcap_dir),
                            "--out",
                            str(pcap_path),
                        ],
                        text=True,
                        capture_output=True,
                    )
                    if proc.stdout:
                        print(proc.stdout[-4000:])
                    if proc.stderr:
                        print(proc.stderr[-4000:])
                    has_pcap_samples = proc.returncode == 0 and pcap_path.exists() and sum(1 for _ in pcap_path.open("r", encoding="utf-8", errors="ignore")) > 1

            if has_pcap_samples:
                pcap = pd.read_csv(pcap_path)
                pcap["timestamp"] = pd.to_datetime(pcap["timestamp"], unit="s", utc=True, errors="coerce")
                pcap["rtt_ms"] = pd.to_numeric(pcap["rtt_ms"], errors="coerce")
                display(pcap.describe(include="all"))
                x, y = ecdf(pcap["rtt_ms"])
                if len(x):
                    plt.figure(figsize=(7, 4))
                    plt.plot(x, y)
                    plt.xlabel("RTT (ms)")
                    plt.ylabel("CDF")
                    plt.title("Pcap-reconstructed TCP ACK RTT CDF")
                    savefig("pcap_tcp_rtt_cdf")
                    plt.show()
            else:
                pcap = pd.DataFrame()
                print("No pcap RTT samples available yet.")
            """
        ),
        md(
            """
            ## Supervisor Notes

            Fill this section during discussion.

            - Which scenarios show clear latency changes?
            - Are latency spikes aligned with throughput, radio metrics, or measurement-quality drops?
            - Is the same-packet signal dense enough for the claim?
            - Do we need another experiment phase, another UE mix, or raw pcap/histogram export?
            """
        ),
    ]

    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "pygments_lexer": "ipython3",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--kind", default="experiment", choices=["experiment", "validation", "scenario"])
    parser.add_argument("--title", default="")
    parser.add_argument("--output-name", default="experiment_analysis.ipynb")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    title = args.title or f"{args.kind.title()} Analysis"
    notebook = build_notebook(args.kind, title)
    out = results_dir / args.output_name
    out.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
