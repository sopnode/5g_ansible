#!/usr/bin/env python3
"""Create a ready-to-run Jupyter notebook for latency validation plots."""

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


def build_notebook() -> dict:
    cells = [
        md(
            """
            # Latency Validation Analysis

            This notebook analyzes one validation run for **On-Demand Slice-Aware
            Latency Decomposition in Cloud-Native 5G Systems**.

            Treat the direct BPF TCP ACK RTT and the same-packet pairer as
            candidate signals. The goal is to compare density, stability,
            agreement with pcap reconstruction, and decomposition value before
            deciding which one deserves the main paper plot.
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

            import numpy as np
            import pandas as pd
            import matplotlib.pyplot as plt

            RESULTS_DIR = Path(".").resolve()
            META_PATH = RESULTS_DIR / "validation_metadata.json"
            PROM_CSV = next(
                (
                    path
                    for path in [
                        RESULTS_DIR / "prometheus_timeseries.csv",
                        RESULTS_DIR / "prometheus_timeseries.csv.gz",
                        RESULTS_DIR / "prometheus_timeseries_1s.csv",
                        RESULTS_DIR / "prometheus_timeseries_1s.csv.gz",
                    ]
                    if path.exists()
                ),
                RESULTS_DIR / "prometheus_timeseries.csv",
            )
            PCAP_RTT_CSV = RESULTS_DIR / "pcap_tcp_rtt.csv"
            PING_RTT_CSV = RESULTS_DIR / "ping_rtt.csv"
            BPFTOOL_CSV = RESULTS_DIR / "bpftool_overhead_summary.csv"

            plt.rcParams.update({
                "figure.figsize": (11, 5),
                "axes.grid": True,
                "grid.alpha": 0.25,
                "axes.spines.top": False,
                "axes.spines.right": False,
            })

            def read_json(path):
                return json.loads(Path(path).read_text()) if Path(path).exists() else {}

            meta = read_json(META_PATH)
            print(json.dumps(meta, indent=2)[:4000])
            """
        ),
        md(
            """
            ## Load iperf Results

            The playbook stores per-UE iperf JSON logs in the result directory.
            If the logs are still packed as `qhatXX.tgz`, this cell reads them
            directly from the archive.
            """
        ),
        code(
            """
            IPERF_RE = re.compile(
                r"scenario=(?P<scenario>[^/]+)/step=(?P<step>[^/]+)/direction=(?P<direction>[^/]+)/iperf_(?P<ue>[^/.]+)\\.json"
            )

            def parse_iperf_payload(payload, source_name):
                match = IPERF_RE.search(source_name)
                if not match:
                    return None

                end = payload.get("end", {})
                start = payload.get("start", {})
                ts = start.get("timestamp", {}).get("timesecs")

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

                row = match.groupdict()
                row.update({
                    "source": source_name,
                    "start_time": pd.to_datetime(ts, unit="s", utc=True, errors="coerce"),
                    "throughput_mbps": float(bps) / 1e6 if bps is not None else np.nan,
                    "client_retransmits": retransmits,
                    "server_retransmits": server_retx,
                    "error": payload.get("error", ""),
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

            iperf_rows = []
            for payload, source in iter_iperf_payloads(RESULTS_DIR):
                row = parse_iperf_payload(payload, source)
                if row:
                    iperf_rows.append(row)

            iperf = pd.DataFrame(iperf_rows)
            if not iperf.empty:
                iperf = iperf.sort_values(["scenario", "step", "direction", "ue"])
            iperf
            """
        ),
        code(
            """
            if iperf.empty:
                print("No iperf logs found.")
            else:
                display_cols = [
                    "scenario", "step", "direction", "ue",
                    "throughput_mbps", "client_retransmits", "server_retransmits",
                    "start_time",
                ]
                display(iperf[display_cols])

                plot_df = iperf.copy()
                plot_df["case"] = plot_df["scenario"] + "\\n" + plot_df["step"] + "\\n" + plot_df["direction"]
                ax = plot_df.pivot_table(
                    index="case",
                    columns="ue",
                    values="throughput_mbps",
                    aggfunc="mean",
                ).plot(kind="bar", width=0.82)
                ax.set_ylabel("iperf throughput (Mb/s)")
                ax.set_xlabel("")
                ax.set_title("TCP offered/achieved throughput by validation case")
                plt.xticks(rotation=45, ha="right")
                plt.tight_layout()
            """
        ),
        md(
            """
            ## Load ICMP Ping RTT

            `ping_rtt.csv` contains UE-visible ICMP RTT samples parsed from
            `ping` output. Compare this with probe ICMP latency as a sanity
            check for trend and distribution agreement, not exact equality:
            the probe observes packets at N3 while `ping` measures the full
            UE-to-server round trip.
            """
        ),
        code(
            """
            if PING_RTT_CSV.exists():
                ping = pd.read_csv(PING_RTT_CSV)
                ping["rtt_ms"] = pd.to_numeric(ping["rtt_ms"], errors="coerce")
                ping["time_utc"] = pd.to_datetime(ping["time_utc"], utc=True, errors="coerce")
                display(ping.head())

                ping_summary = (
                    ping.groupby(["scenario", "step", "direction", "ue"], dropna=False)["rtt_ms"]
                    .agg(["count", "mean", "median", lambda s: s.quantile(0.95), "max"])
                    .rename(columns={"<lambda_0>": "p95"})
                    .reset_index()
                )
                display(ping_summary)

                fig, ax = plt.subplots()
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
                plt.tight_layout()
            else:
                ping = pd.DataFrame()
                print("No ping_rtt.csv found.")
            """
        ),
        md(
            """
            ## Load Prometheus Export

            This table contains direct BPF latency, same-packet pairer metrics,
            lost event counters, cAdvisor overhead metrics, and discovered RF
            metrics such as MCS, BLER, HARQ, CQI, RSRP, RSRQ, SINR, or SNR when
            those metric names exist in Prometheus.
            """
        ),
        code(
            """
            if PROM_CSV.exists():
                prom = pd.read_csv(PROM_CSV)
                prom["timestamp"] = pd.to_datetime(prom["timestamp"], unit="s", utc=True, errors="coerce")
                prom["value"] = pd.to_numeric(prom["value"], errors="coerce")
                for col in ["ue_ip", "imsi", "slice", "probe_role", "mode", "direction", "instance", "pod", "container"]:
                    if col not in prom.columns:
                        prom[col] = ""
                prom["series"] = (
                    prom[["ue_ip", "imsi", "slice", "probe_role", "mode", "direction", "instance"]]
                    .fillna("")
                    .astype(str)
                    .agg(" ".join, axis=1)
                    .str.replace(r"\\s+", " ", regex=True)
                    .str.strip()
                )
                print(prom.groupby("query_name").size().sort_values(ascending=False).to_string())
            else:
                prom = pd.DataFrame()
                print("No Prometheus export found.")
            """
        ),
        code(
            """
            def plot_query(query_name, title=None, ylabel=None, max_series=10):
                if prom.empty:
                    print("No Prometheus data.")
                    return
                df = prom[prom["query_name"] == query_name].dropna(subset=["value"])
                if df.empty:
                    print(f"No data for {query_name}")
                    return
                series_order = df.groupby("series")["value"].count().sort_values(ascending=False).head(max_series).index
                df = df[df["series"].isin(series_order)]
                ax = None
                for label, group in df.groupby("series"):
                    group = group.sort_values("timestamp")
                    ax = group.plot(x="timestamp", y="value", ax=ax, label=label or query_name)
                ax.set_title(title or query_name)
                ax.set_ylabel(ylabel or query_name)
                ax.set_xlabel("")
                plt.legend(fontsize=8)
                plt.tight_layout()

            plot_query("direct_rtt_mean_ms", "Direct BPF TCP ACK RTT mean", "RTT (ms)")
            """
        ),
        code(
            """
            plot_query("direct_rtt_p95_ms", "Direct BPF TCP ACK RTT p95", "RTT (ms)")
            """
        ),
        md(
            """
            ## Candidate Signal Density

            This is the first decision plot: if the pairer has enough samples and
            tracks the same changes as the direct BPF RTT, it can be promoted to
            a main result. If it is sparse or bursty, use it as validation and
            decomposition evidence.
            """
        ),
        code(
            """
            if prom.empty:
                print("No Prometheus data.")
            else:
                density = prom[prom["query_name"].isin(["direct_event_rate_hz", "same_packet_pair_rate_hz"])].copy()
                if density.empty:
                    print("No density metrics found.")
                else:
                    density["signal"] = density["query_name"].map({
                        "direct_event_rate_hz": "direct BPF RTT samples/s",
                        "same_packet_pair_rate_hz": "same-packet pairs/s",
                    })
                    ax = None
                    for (signal, series), group in density.groupby(["signal", "series"]):
                        group = group.sort_values("timestamp")
                        label = f"{signal} | {series}"[:120]
                        ax = group.plot(x="timestamp", y="value", ax=ax, label=label)
                    ax.set_title("Sample density: direct BPF vs same-packet pairer")
                    ax.set_ylabel("samples/s")
                    ax.set_xlabel("")
                    plt.legend(fontsize=7)
                    plt.tight_layout()
            """
        ),
        md(
            """
            ## Same-Packet Decomposition

            The gap metric is the paper's decomposition candidate. Interpret its
            sign using your implementation convention: downlink gap is UPF RTT
            minus gNB RTT; uplink gap is gNB RTT minus UPF RTT.
            """
        ),
        code(
            """
            plot_query("same_packet_gap_mean_ms", "Same-packet RTT gap", "gap (ms)")
            """
        ),
        code(
            """
            plot_query("same_packet_upf_rtt_mean_ms", "Same-packet UPF local RTT", "RTT (ms)")
            plot_query("same_packet_gnb_rtt_mean_ms", "Same-packet gNB local RTT", "RTT (ms)")
            """
        ),
        md(
            """
            ## Pcap Reconstruction Check

            This is the ground-truth sanity check. It reconstructs TCP ACK RTT
            from captured GTP-U packets and compares the distribution shape to
            the BPF-exported latency metrics.
            """
        ),
        code(
            """
            if PCAP_RTT_CSV.exists():
                try:
                    has_samples = sum(1 for _ in PCAP_RTT_CSV.open("r", encoding="utf-8", errors="ignore")) > 1
                except OSError:
                    has_samples = False
            else:
                has_samples = False

            def find_pcap_extractor():
                for root in [RESULTS_DIR, *RESULTS_DIR.parents]:
                    candidate = root / "scripts" / "validation" / "pcap_tcp_rtt_extract.py"
                    if candidate.exists():
                        return candidate
                return None

            if not has_samples:
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
                            str(PCAP_RTT_CSV),
                        ],
                        text=True,
                        capture_output=True,
                    )
                    if proc.stdout:
                        print(proc.stdout[-4000:])
                    if proc.stderr:
                        print(proc.stderr[-4000:])
                    has_samples = proc.returncode == 0 and PCAP_RTT_CSV.exists() and sum(1 for _ in PCAP_RTT_CSV.open("r", encoding="utf-8", errors="ignore")) > 1

            if has_samples:
                pcap = pd.read_csv(PCAP_RTT_CSV)
                pcap["timestamp"] = pd.to_datetime(pcap["timestamp"], unit="s", utc=True, errors="coerce")
                pcap["rtt_ms"] = pd.to_numeric(pcap["rtt_ms"], errors="coerce")
                display(pcap["rtt_ms"].describe(percentiles=[0.5, 0.9, 0.95, 0.99]))

                vals = pcap["rtt_ms"].dropna().sort_values().to_numpy()
                y = np.arange(1, len(vals) + 1) / len(vals)
                plt.plot(vals, y, label="pcap reconstructed RTT")
                plt.xlabel("RTT (ms)")
                plt.ylabel("CDF")
                plt.title("Pcap reconstructed TCP ACK RTT CDF")
                plt.legend()
                plt.tight_layout()
            else:
                print("No pcap RTT samples available yet.")
            """
        ),
        md(
            """
            ## RF and RAN Metrics

            These discovered metrics are not assumed to have fixed names. The
            exporter pulls Prometheus metric names containing radio terms such
            as MCS, BLER, HARQ, CQI, RSRP, RSRQ, SINR, or SNR.
            """
        ),
        code(
            """
            if prom.empty:
                print("No Prometheus data.")
            else:
                rf = prom[prom["query_name"].str.startswith("rf_", na=False)].dropna(subset=["value"])
                if rf.empty:
                    print("No discovered RF/RAN metrics found.")
                else:
                    top = rf.groupby("query_name")["value"].count().sort_values(ascending=False).head(8).index
                    for q in top:
                        df = rf[rf["query_name"] == q].sort_values("timestamp")
                        pivot = df.pivot_table(index="timestamp", columns="series", values="value", aggfunc="mean")
                        ax = pivot.iloc[:, :6].plot(title=q, legend=False)
                        ax.set_xlabel("")
                        ax.set_ylabel("value")
                        plt.tight_layout()
                        plt.show()
            """
        ),
        md(
            """
            ## BPF Packet-Path Overhead

            When available, this table is computed from before/after
            `bpftool prog show -j` snapshots taken inside the running
            `ebpf-latency-probe` container. The key value is
            `delta(run_time_ns) / delta(run_cnt)`.
            """
        ),
        code(
            """
            if BPFTOOL_CSV.exists():
                bpfo = pd.read_csv(BPFTOOL_CSV)
                for col in ["run_cnt_delta", "run_time_ns_delta", "ns_per_run"]:
                    if col in bpfo.columns:
                        bpfo[col] = pd.to_numeric(bpfo[col], errors="coerce")
                if bpfo.empty:
                    print("bpftool summary exists but contains no paired samples.")
                else:
                    cols = [
                        "scenario", "step", "direction", "pod", "container",
                        "prog_id", "prog_name", "prog_type",
                        "run_cnt_delta", "run_time_ns_delta", "ns_per_run",
                    ]
                    display(bpfo[[c for c in cols if c in bpfo.columns]].sort_values("ns_per_run", ascending=False))
                    plot_df = bpfo.dropna(subset=["ns_per_run"]).copy()
                    if not plot_df.empty:
                        plot_df["program"] = (
                            plot_df["prog_name"].fillna("").astype(str)
                            + "\\n"
                            + plot_df["prog_type"].fillna("").astype(str)
                            + " id="
                            + plot_df["prog_id"].astype(str)
                        )
                        ax = (
                            plot_df.sort_values("ns_per_run", ascending=False)
                            .head(12)
                            .plot.barh(x="program", y="ns_per_run", legend=False)
                        )
                        ax.invert_yaxis()
                        ax.set_xlabel("ns per BPF program run")
                        ax.set_ylabel("")
                        ax.set_title("BPF packet-path overhead from bpftool")
                        plt.tight_layout()
            else:
                print("No bpftool_overhead_summary.csv found.")
            """
        ),
        md(
            """
            ## Decision Summary Tables
            """
        ),
        code(
            """
            if not prom.empty:
                key_queries = [
                    "direct_rtt_mean_ms",
                    "direct_rtt_p95_ms",
                    "direct_event_rate_hz",
                    "same_packet_pair_rate_hz",
                    "same_packet_gap_mean_ms",
                    "lost_observation_events_hz",
                ]
                summary = (
                    prom[prom["query_name"].isin(key_queries)]
                    .dropna(subset=["value"])
                    .groupby(["query_name", "mode", "direction", "slice", "probe_role"])["value"]
                    .agg(["count", "mean", "median", lambda s: s.quantile(0.95)])
                    .rename(columns={"<lambda_0>": "p95"})
                    .reset_index()
                    .sort_values(["query_name", "mode", "direction", "slice"])
                )
                display(summary)

            if not iperf.empty:
                iperf_summary = (
                    iperf.groupby(["scenario", "step", "direction", "ue"])
                    .agg(
                        throughput_mbps=("throughput_mbps", "mean"),
                        client_retransmits=("client_retransmits", "sum"),
                        server_retransmits=("server_retransmits", "sum"),
                    )
                    .reset_index()
                )
                display(iperf_summary)
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
    parser.add_argument("--out")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else results_dir / "latency_validation_analysis.ipynb"
    out.write_text(json.dumps(build_notebook(), indent=2), encoding="utf-8")
    print(f"wrote notebook to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
