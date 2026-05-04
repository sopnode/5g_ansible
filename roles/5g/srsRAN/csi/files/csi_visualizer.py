#!/usr/bin/env python3
"""
CSI Logger Data Parser and Visualizer - v1.0.0.20
Supports:
  UL mode : binary /tmp/csi_data_0x<rnti>.bin
            → magnitude/phase per subcarrier/symbol/port
            Record format: 26 bytes = timestamp(8) + slot(4) + subcarrier(2)
                           + magnitude(4) + phase(4) + symbol(1) + port(1) + rnti(2)
  DL mode : CSV    /tmp/csi_dl_0x<rnti>.csv
            → CQI/RI timeline per UE

Usage:
  UL: python3 csi_visualizer.py csi_data_0x1234.bin [--slot N] [--prb N]
  DL: python3 csi_visualizer.py csi_dl_0x1234.csv --dl
  DL multi-UE: python3 csi_visualizer.py --dl-dir /tmp [--output /tmp/plots]
"""

import struct
import sys
import csv
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
import argparse
from collections import defaultdict


# ─────────────────────────────────────────────
# UL : Binary record parser
# ─────────────────────────────────────────────

class CSIRecord:
    """Single UL CSI measurement record (binary, 26 bytes)"""
    RECORD_SIZE = 26
    FORMAT = '<QIHffBBH'  # timestamp(8), slot(4), subcarrier(2),
                           # mag(4), phase(4), symbol(1), port(1), rnti(2)

    def __init__(self, data):
        if len(data) != self.RECORD_SIZE:
            raise ValueError(f"Invalid record size: {len(data)}")
        values = struct.unpack(self.FORMAT, data)
        self.timestamp_us      = values[0]
        self.slot_idx          = values[1]
        self.subcarrier_idx    = values[2]
        self.magnitude         = values[3]
        self.phase             = values[4]
        self.symbol_idx        = values[5]
        self.port_idx          = values[6]
        self.rnti              = values[7]
        self.prb_idx           = self.subcarrier_idx // 12
        self.subcarrier_in_prb = self.subcarrier_idx % 12

    def __repr__(self):
        return (f"CSIRecord(slot={self.slot_idx}, sub={self.subcarrier_idx}, "
                f"mag={self.magnitude:.3f}, phase={self.phase:.3f}, "
                f"rnti=0x{self.rnti:04x})")


class CSIParser:
    """Parse binary UL CSI file"""

    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.records  = []

    def parse(self):
        if not self.filepath.exists():
            print(f"ERROR: File not found: {self.filepath}")
            return False

        file_size        = self.filepath.stat().st_size
        expected_records = file_size // CSIRecord.RECORD_SIZE
        remainder        = file_size % CSIRecord.RECORD_SIZE
        print(f"[UL Parser] Reading {self.filepath}")
        print(f"[UL Parser] File size: {file_size} bytes | "
              f"Expected records: {expected_records} | "
              f"Remainder: {remainder} bytes")
        if remainder != 0:
            print(f"WARNING: File size not a multiple of {CSIRecord.RECORD_SIZE} bytes "
                  f"— possible corruption or wrong format")

        try:
            with open(self.filepath, 'rb') as f:
                while True:
                    data = f.read(CSIRecord.RECORD_SIZE)
                    if not data:
                        break
                    if len(data) < CSIRecord.RECORD_SIZE:
                        print(f"WARNING: Incomplete record ({len(data)} bytes) — skipped")
                        break
                    self.records.append(CSIRecord(data))
            print(f"[UL Parser] Parsed {len(self.records)} records")
            return True
        except Exception as e:
            print(f"ERROR: {e}")
            return False

    def first_slot(self):
        if not self.records:
            return 0
        return sorted(set(r.slot_idx for r in self.records))[0]

    def first_prb(self, slot_idx=None):
        if not self.records:
            return 0
        if slot_idx is not None:
            prbs = sorted(set(r.prb_idx for r in self.records
                              if r.slot_idx == slot_idx))
        else:
            prbs = sorted(set(r.prb_idx for r in self.records))
        return prbs[0] if prbs else 0

    def get_statistics(self):
        if not self.records:
            print("No UL records")
            return
        slots   = set(r.slot_idx   for r in self.records)
        prbs    = set(r.prb_idx    for r in self.records)
        symbols = set(r.symbol_idx for r in self.records)
        ports   = set(r.port_idx   for r in self.records)
        rntis   = set(r.rnti       for r in self.records)
        print("\n=== UL CSI Statistics ===")
        print(f"Total records : {len(self.records)}")
        print(f"RNTIs         : {[hex(r) for r in sorted(rntis)]}")
        print(f"Slots         : {len(slots)} (range: {min(slots)}-{max(slots)})")
        print(f"PRBs          : {len(prbs)} (range: {min(prbs)}-{max(prbs)})")
        print(f"Symbols       : {sorted(symbols)}")
        print(f"Ports         : {sorted(ports)}")
        print(f"Magnitude     : {min(r.magnitude for r in self.records):.3f} - "
              f"{max(r.magnitude for r in self.records):.3f}")
        print(f"Phase         : {min(r.phase for r in self.records):.3f} - "
              f"{max(r.phase for r in self.records):.3f}")


# ─────────────────────────────────────────────
# DL : CSV record parser
# ─────────────────────────────────────────────

class DLCSIRecord:
    """Single DL CSI report record (CSV row)"""

    def __init__(self, row):
        self.timestamp_us = int(row['timestamp_us'])
        self.slot_idx     = int(row['slot_idx'])
        self.rnti         = row['rnti']
        self.cqi          = int(row['cqi'])
        self.ri           = int(row['ri'])
        self.pmi_present  = int(row['pmi_present']) == 1


class DLCSIParser:
    """Parse DL CSI CSV file"""

    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.records  = []
        self.rnti     = None

    def parse(self):
        if not self.filepath.exists():
            print(f"ERROR: File not found: {self.filepath}")
            return False

        print(f"[DL Parser] Reading {self.filepath}")
        try:
            with open(self.filepath, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rec = DLCSIRecord(row)
                    self.records.append(rec)
                    if self.rnti is None:
                        self.rnti = rec.rnti
            print(f"[DL Parser] Parsed {len(self.records)} records for RNTI {self.rnti}")
            return True
        except Exception as e:
            print(f"ERROR: {e}")
            return False

    def get_statistics(self):
        if not self.records:
            print("No DL records")
            return
        cqis  = [r.cqi for r in self.records]
        ris   = [r.ri  for r in self.records]
        slots = [r.slot_idx for r in self.records]
        duration_s = (self.records[-1].timestamp_us - self.records[0].timestamp_us) / 1e6

        print(f"\n=== DL CSI Statistics — RNTI {self.rnti} ===")
        print(f"Total records : {len(self.records)}")
        print(f"Duration      : {duration_s:.2f} s")
        print(f"Slots         : {min(slots)} → {max(slots)}")
        print(f"CQI           : min={min(cqis)} max={max(cqis)} mean={np.mean(cqis):.2f}")
        print(f"RI            : min={min(ris)}  max={max(ris)}  mean={np.mean(ris):.2f}")
        print(f"PMI present   : {sum(r.pmi_present for r in self.records)}/{len(self.records)}")


# ─────────────────────────────────────────────
# UL Visualizer
# ─────────────────────────────────────────────

class CSIVisualizer:
    """Visualize UL CSI data"""

    def __init__(self, parser):
        self.parser = parser

    def plot_prb_magnitude(self, slot_idx, prb_idx, symbol_idx=None, port_idx=None):
        records = [r for r in self.parser.records
                   if r.slot_idx == slot_idx and r.prb_idx == prb_idx]
        if symbol_idx is not None:
            records = [r for r in records if r.symbol_idx == symbol_idx]
        if port_idx is not None:
            records = [r for r in records if r.port_idx == port_idx]
        if not records:
            print(f"No data for slot={slot_idx}, prb={prb_idx}")
            return None
        records.sort(key=lambda r: r.subcarrier_in_prb)
        rnti = f"0x{records[0].rnti:04x}"
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.stem([r.subcarrier_in_prb for r in records],
                [r.magnitude for r in records], basefmt=' ')
        ax.set_xlabel('Subcarrier Index (within PRB)')
        ax.set_ylabel('Magnitude')
        ax.set_title(f'UL CSI Magnitude — Slot {slot_idx}, PRB {prb_idx}, UE {rnti}')
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        return fig

    def plot_prb_phase(self, slot_idx, prb_idx, symbol_idx=None, port_idx=None):
        records = [r for r in self.parser.records
                   if r.slot_idx == slot_idx and r.prb_idx == prb_idx]
        if symbol_idx is not None:
            records = [r for r in records if r.symbol_idx == symbol_idx]
        if port_idx is not None:
            records = [r for r in records if r.port_idx == port_idx]
        if not records:
            print(f"No data for slot={slot_idx}, prb={prb_idx}")
            return None
        records.sort(key=lambda r: r.subcarrier_in_prb)
        rnti = f"0x{records[0].rnti:04x}"
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.stem([r.subcarrier_in_prb for r in records],
                [r.phase for r in records], basefmt=' ')
        ax.set_xlabel('Subcarrier Index (within PRB)')
        ax.set_ylabel('Phase (radians)')
        ax.set_title(f'UL CSI Phase — Slot {slot_idx}, PRB {prb_idx}, UE {rnti}')
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        return fig

    def plot_constellation(self, slot_idx, prb_idx, symbol_idx=None, port_idx=None):
        records = [r for r in self.parser.records
                   if r.slot_idx == slot_idx and r.prb_idx == prb_idx]
        if symbol_idx is not None:
            records = [r for r in records if r.symbol_idx == symbol_idx]
        if port_idx is not None:
            records = [r for r in records if r.port_idx == port_idx]
        if not records:
            print(f"No data for slot={slot_idx}, prb={prb_idx}")
            return None
        rnti = f"0x{records[0].rnti:04x}"
        i_vals = [r.magnitude * np.cos(r.phase) for r in records]
        q_vals = [r.magnitude * np.sin(r.phase) for r in records]
        fig, ax = plt.subplots(figsize=(8, 8))
        ax.scatter(i_vals, q_vals, alpha=0.6, s=30)
        ax.set_xlabel('I')
        ax.set_ylabel('Q')
        ax.set_title(f'UL CSI Constellation — Slot {slot_idx}, PRB {prb_idx}, UE {rnti}')
        ax.grid(True, alpha=0.3)
        ax.axis('equal')
        plt.tight_layout()
        return fig

    def plot_prb_heatmap(self, slot_idx):
        records_by_prb = defaultdict(list)
        for r in self.parser.records:
            if r.slot_idx == slot_idx:
                records_by_prb[r.prb_idx].append(r)
        if not records_by_prb:
            print(f"No data for slot {slot_idx}")
            return None
        prbs   = sorted(records_by_prb.keys())
        data   = np.zeros((len(prbs), 12))
        counts = np.zeros((len(prbs), 12))
        for i, prb in enumerate(prbs):
            for rec in records_by_prb[prb]:
                data[i, rec.subcarrier_in_prb]   += rec.magnitude
                counts[i, rec.subcarrier_in_prb] += 1
        with np.errstate(invalid='ignore'):
            data = np.where(counts > 0, data / counts, 0)
        rnti = f"0x{self.parser.records[0].rnti:04x}"
        fig, ax = plt.subplots(figsize=(12, 6))
        im = ax.imshow(data, aspect='auto', cmap='viridis', origin='lower')
        plt.colorbar(im, ax=ax, label='Magnitude (avg)')
        ax.set_xlabel('Subcarrier Index (within PRB)')
        ax.set_ylabel('PRB Index')
        ax.set_title(f'UL CSI Magnitude Heatmap — Slot {slot_idx}, UE {rnti}')
        plt.tight_layout()
        return fig

    def plot_timeline(self, prb_idx=0, symbol_idx=None, port_idx=None):
        records = [r for r in self.parser.records if r.prb_idx == prb_idx]
        if symbol_idx is not None:
            records = [r for r in records if r.symbol_idx == symbol_idx]
        if port_idx is not None:
            records = [r for r in records if r.port_idx == port_idx]
        if not records:
            print(f"No data for prb={prb_idx}")
            return None
        records.sort(key=lambda r: r.timestamp_us)
        times = [(r.timestamp_us - records[0].timestamp_us) / 1000 for r in records]
        rnti = f"0x{records[0].rnti:04x}"
        fig, ax = plt.subplots(figsize=(14, 4))
        ax.plot(times, [r.magnitude for r in records], '.-', linewidth=0.5, markersize=3)
        ax.set_xlabel('Time (ms)')
        ax.set_ylabel('Magnitude')
        ax.set_title(f'UL CSI Magnitude Timeline — PRB {prb_idx}, UE {rnti}')
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        return fig


# ─────────────────────────────────────────────
# DL Visualizer
# ─────────────────────────────────────────────

class DLCSIVisualizer:
    """Visualize DL CSI report data (CQI/RI timeline per UE)"""

    def __init__(self, parser):
        self.parser = parser

    def _time_axis(self):
        t0 = self.parser.records[0].timestamp_us
        return [(r.timestamp_us - t0) / 1e6 for r in self.parser.records]

    def plot_cqi_timeline(self):
        if not self.parser.records:
            return None
        times = self._time_axis()
        cqis  = [r.cqi for r in self.parser.records]
        fig, ax = plt.subplots(figsize=(14, 4))
        ax.plot(times, cqis, '.-', linewidth=0.8, markersize=4, color='steelblue')
        ax.axhline(y=np.mean(cqis), color='red', linestyle='--',
                   linewidth=1, label=f'Mean CQI = {np.mean(cqis):.2f}')
        ax.set_xlabel('Time (s)')
        ax.set_ylabel('CQI (0-15)')
        ax.set_ylim(-0.5, 15.5)
        ax.set_yticks(range(0, 16))
        ax.set_title(f'DL CQI Timeline — UE {self.parser.rnti}')
        ax.grid(True, alpha=0.3)
        ax.legend()
        plt.tight_layout()
        return fig

    def plot_ri_timeline(self):
        if not self.parser.records:
            return None
        times = self._time_axis()
        ris   = [r.ri for r in self.parser.records]
        fig, ax = plt.subplots(figsize=(14, 3))
        ax.step(times, ris, where='post', linewidth=1.2, color='darkorange')
        ax.set_xlabel('Time (s)')
        ax.set_ylabel('Rank Indicator')
        ax.set_ylim(0, max(ris) + 1)
        ax.set_title(f'DL Rank Indicator Timeline — UE {self.parser.rnti}')
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        return fig

    def plot_cqi_histogram(self):
        if not self.parser.records:
            return None
        cqis = [r.cqi for r in self.parser.records]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(cqis, bins=range(0, 17), align='left', rwidth=0.8,
                color='steelblue', edgecolor='black')
        ax.set_xlabel('CQI')
        ax.set_ylabel('Count')
        ax.set_xticks(range(0, 16))
        ax.set_title(f'DL CQI Distribution — UE {self.parser.rnti} ({len(cqis)} samples)')
        ax.grid(True, alpha=0.3, axis='y')
        plt.tight_layout()
        return fig

    def plot_cqi_ri_combined(self):
        if not self.parser.records:
            return None
        times = self._time_axis()
        cqis  = [r.cqi for r in self.parser.records]
        ris   = [r.ri  for r in self.parser.records]
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
        ax1.plot(times, cqis, '.-', linewidth=0.8, markersize=3, color='steelblue')
        ax1.axhline(y=np.mean(cqis), color='red', linestyle='--', linewidth=1,
                    label=f'mean={np.mean(cqis):.2f}')
        ax1.set_ylabel('CQI (0-15)')
        ax1.set_ylim(-0.5, 15.5)
        ax1.set_yticks(range(0, 16, 2))
        ax1.set_title(f'DL Channel Quality — UE {self.parser.rnti}')
        ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=9)
        ax2.step(times, ris, where='post', linewidth=1.2, color='darkorange')
        ax2.set_ylabel('RI')
        ax2.set_xlabel('Time (s)')
        ax2.set_ylim(0, max(ris) + 1)
        ax2.grid(True, alpha=0.3)
        plt.tight_layout()
        return fig


# ─────────────────────────────────────────────
# Multi-UE DL overview
# ─────────────────────────────────────────────

def plot_dl_multi_ue(dl_dir, output_dir=None):
    dl_files = sorted(Path(dl_dir).glob('csi_dl_0x*.csv'))
    if not dl_files:
        print(f"No DL CSI files found in {dl_dir}")
        return None

    print(f"[Multi-UE] Found {len(dl_files)} DL CSI files")
    fig, axes = plt.subplots(len(dl_files), 1,
                             figsize=(14, 3 * len(dl_files)),
                             sharex=False)
    if len(dl_files) == 1:
        axes = [axes]

    for ax, filepath in zip(axes, dl_files):
        parser = DLCSIParser(filepath)
        if not parser.parse() or not parser.records:
            continue
        t0    = parser.records[0].timestamp_us
        times = [(r.timestamp_us - t0) / 1e6 for r in parser.records]
        cqis  = [r.cqi for r in parser.records]
        ax.plot(times, cqis, '.-', linewidth=0.8, markersize=3)
        ax.axhline(y=np.mean(cqis), color='red', linestyle='--', linewidth=1,
                   label=f'mean={np.mean(cqis):.2f}')
        ax.set_ylabel('CQI')
        ax.set_ylim(-0.5, 15.5)
        ax.set_title(f'UE {parser.rnti}')
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)

    axes[-1].set_xlabel('Time (s)')
    plt.suptitle('DL CQI Timeline — All UEs', fontsize=13, fontweight='bold')
    plt.tight_layout()

    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        fig.savefig(Path(output_dir) / 'dl_cqi_all_ues.png', dpi=100)
        print(f"[Multi-UE] Saved dl_cqi_all_ues.png")
    else:
        plt.show()
    return fig


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='CSI Data Visualizer v1.0.0.20 — UL binary (26B) + DL CSV',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # UL - auto slot/prb detection
  python3 csi_visualizer.py csi_data_0x1234.bin

  # UL - specific slot and prb
  python3 csi_visualizer.py csi_data_0x1234.bin --slot 503 --prb 5

  # DL single UE
  python3 csi_visualizer.py csi_dl_0x1234.csv --dl

  # DL multi-UE (all files in /tmp)
  python3 csi_visualizer.py --dl-dir /tmp --output /tmp/plots

  # Stats only
  python3 csi_visualizer.py csi_data_0x1234.bin --stats
        """)

    parser.add_argument('csi_file', nargs='?',  help='Path to CSI file (UL .bin or DL .csv)')
    parser.add_argument('--dl',     action='store_true', help='DL mode: parse CSV file')
    parser.add_argument('--dl-dir', help='DL multi-UE mode: directory with csi_dl_0x*.csv')
    parser.add_argument('--slot',   type=int, default=None,
                        help='[UL] Slot index (default: auto = first available)')
    parser.add_argument('--prb',    type=int, default=None,
                        help='[UL] PRB index (default: auto = first available)')
    parser.add_argument('--symbol', type=int, default=None, help='[UL] Symbol index')
    parser.add_argument('--port',   type=int, default=None, help='[UL] Port index')
    parser.add_argument('--output', help='Output directory for PNG files')
    parser.add_argument('--stats',  action='store_true', help='Show statistics only')

    args = parser.parse_args()

    # ── DL multi-UE mode ──────────────────────────────────────
    if args.dl_dir:
        plot_dl_multi_ue(args.dl_dir, args.output)
        return

    if not args.csi_file:
        parser.print_help()
        sys.exit(1)

    # ── DL single-UE mode ─────────────────────────────────────
    if args.dl:
        dl_parser = DLCSIParser(args.csi_file)
        if not dl_parser.parse():
            sys.exit(1)
        dl_parser.get_statistics()
        if args.stats:
            return

        viz = DLCSIVisualizer(dl_parser)

        if args.output:
            out = Path(args.output)
            out.mkdir(parents=True, exist_ok=True)
            rnti = dl_parser.rnti.replace('0x', '')
            for name, fig in [
                (f'dl_cqi_ri_{rnti}.png',   viz.plot_cqi_ri_combined()),
                (f'dl_cqi_{rnti}.png',      viz.plot_cqi_timeline()),
                (f'dl_ri_{rnti}.png',       viz.plot_ri_timeline()),
                (f'dl_cqi_hist_{rnti}.png', viz.plot_cqi_histogram()),
            ]:
                if fig:
                    fig.savefig(out / name, dpi=100)
                    plt.close(fig)
            print(f"[Visualizer] Saved DL plots to {out}")
        else:
            viz.plot_cqi_ri_combined()
            plt.show()
            viz.plot_cqi_histogram()
            plt.show()
        return

    # ── UL mode ───────────────────────────────────────────────
    ul_parser = CSIParser(args.csi_file)
    if not ul_parser.parse():
        sys.exit(1)
    ul_parser.get_statistics()
    if args.stats:
        return

    # Auto-detect slot and PRB
    if args.slot is None:
        slots = sorted(set(r.slot_idx for r in ul_parser.records))
        args.slot = slots[0] if slots else 0
        print(f"[Visualizer] Auto-selected slot={args.slot}")

    if args.prb is None:
        prbs = sorted(set(r.prb_idx for r in ul_parser.records
                          if r.slot_idx == args.slot))
        args.prb = prbs[0] if prbs else 0
        print(f"[Visualizer] Auto-selected prb={args.prb}")

    viz = CSIVisualizer(ul_parser)

    # Derive RNTI string for filenames
    rntis = set(r.rnti for r in ul_parser.records)
    rnti_str = f"{ul_parser.records[0].rnti:04x}"

    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        for name, fig in [
            (f'ul_magnitude_slot{args.slot}_prb{args.prb}_{rnti_str}.png',
             viz.plot_prb_magnitude(args.slot, args.prb, args.symbol, args.port)),
            (f'ul_phase_slot{args.slot}_prb{args.prb}_{rnti_str}.png',
             viz.plot_prb_phase(args.slot, args.prb, args.symbol, args.port)),
            (f'ul_constellation_slot{args.slot}_prb{args.prb}_{rnti_str}.png',
             viz.plot_constellation(args.slot, args.prb, args.symbol, args.port)),
            (f'ul_heatmap_slot{args.slot}_{rnti_str}.png',
             viz.plot_prb_heatmap(args.slot)),
            (f'ul_timeline_prb{args.prb}_{rnti_str}.png',
             viz.plot_timeline(args.prb, args.symbol, args.port)),
        ]:
            if fig:
                fig.savefig(out / name, dpi=100)
                plt.close(fig)
        print(f"[Visualizer] Saved UL plots to {out}")
    else:
        viz.plot_prb_magnitude(args.slot, args.prb, args.symbol, args.port)
        plt.show()
        viz.plot_prb_phase(args.slot, args.prb, args.symbol, args.port)
        plt.show()
        viz.plot_constellation(args.slot, args.prb, args.symbol, args.port)
        plt.show()
        viz.plot_prb_heatmap(args.slot)
        plt.show()
        viz.plot_timeline(args.prb, args.symbol, args.port)
        plt.show()


if __name__ == '__main__':
    main()
