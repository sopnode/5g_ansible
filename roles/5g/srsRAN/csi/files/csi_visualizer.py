#!/usr/bin/env python3
"""
CSI Logger Data Parser and Visualizer
Reads binary CSI data from srsRAN gNB and visualizes:
- Magnitude vs Subcarrier (per PRB)
- Phase vs Subcarrier (per PRB)
- Constellation (IQ plot)
- Timeline of CSI measurements
"""

import struct
import sys
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import argparse
from collections import defaultdict

class CSIRecord:
    """Single CSI measurement record"""
    RECORD_SIZE = 24  # bytes
    FORMAT = '<QIHffBB'  # timestamp, slot, subcarrier, mag, phase, symbol, port
    
    def __init__(self, data):
        if len(data) != self.RECORD_SIZE:
            raise ValueError(f"Invalid record size: {len(data)}")
        
        values = struct.unpack(self.FORMAT, data)
        self.timestamp_us = values[0]
        self.slot_idx = values[1]
        self.subcarrier_idx = values[2]
        self.magnitude = values[3]
        self.phase = values[4]
        self.symbol_idx = values[5]
        self.port_idx = values[6]
        
        # Derived
        self.prb_idx = self.subcarrier_idx // 12
        self.subcarrier_in_prb = self.subcarrier_idx % 12
    
    def __repr__(self):
        return (f"CSIRecord(slot={self.slot_idx}, subcarrier={self.subcarrier_idx}, "
                f"mag={self.magnitude:.3f}, phase={self.phase:.3f})")


class CSIParser:
    """Parse binary CSI file"""
    
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.records = []
    
    def parse(self):
        """Read and parse CSI file"""
        if not self.filepath.exists():
            print(f"ERROR: File not found: {self.filepath}")
            return False
        
        file_size = self.filepath.stat().st_size
        expected_records = file_size // CSIRecord.RECORD_SIZE
        
        print(f"[CSI Parser] Reading {self.filepath}")
        print(f"[CSI Parser] File size: {file_size} bytes")
        print(f"[CSI Parser] Expected records: {expected_records}")
        
        try:
            with open(self.filepath, 'rb') as f:
                while True:
                    data = f.read(CSIRecord.RECORD_SIZE)
                    if not data:
                        break
                    if len(data) < CSIRecord.RECORD_SIZE:
                        print(f"WARNING: Incomplete record at end (only {len(data)} bytes)")
                        break
                    
                    record = CSIRecord(data)
                    self.records.append(record)
            
            print(f"[CSI Parser] Successfully parsed {len(self.records)} records")
            return True
        
        except Exception as e:
            print(f"ERROR: Failed to parse file: {e}")
            return False
    
    def get_by_slot(self, slot_idx):
        """Get all records for a specific slot"""
        return [r for r in self.records if r.slot_idx == slot_idx]
    
    def get_by_prb(self, prb_idx):
        """Get all records for a specific PRB"""
        return [r for r in self.records if r.prb_idx == prb_idx]
    
    def get_statistics(self):
        """Print file statistics"""
        if not self.records:
            print("No records to analyze")
            return
        
        slots = set(r.slot_idx for r in self.records)
        prbs = set(r.prb_idx for r in self.records)
        symbols = set(r.symbol_idx for r in self.records)
        ports = set(r.port_idx for r in self.records)
        
        print("\n=== CSI Statistics ===")
        print(f"Total records: {len(self.records)}")
        print(f"Slots: {len(slots)} (range: {min(slots)}-{max(slots)})")
        print(f"PRBs: {len(prbs)} (range: {min(prbs)}-{max(prbs)})")
        print(f"Symbols: {len(symbols)} (range: {min(symbols)}-{max(symbols)})")
        print(f"Ports: {len(ports)} (range: {min(ports)}-{max(ports)})")
        print(f"Magnitude range: {min(r.magnitude for r in self.records):.3f} - {max(r.magnitude for r in self.records):.3f}")
        print(f"Phase range: {min(r.phase for r in self.records):.3f} - {max(r.phase for r in self.records):.3f}")


class CSIVisualizer:
    """Visualize CSI data"""
    
    def __init__(self, parser):
        self.parser = parser
    
    def plot_prb_magnitude(self, slot_idx, prb_idx, symbol_idx=0, port_idx=0):
        """Plot magnitude vs subcarrier for a specific PRB"""
        records = [r for r in self.parser.records 
                   if r.slot_idx == slot_idx and r.prb_idx == prb_idx 
                   and r.symbol_idx == symbol_idx and r.port_idx == port_idx]
        
        if not records:
            print(f"No data for slot={slot_idx}, prb={prb_idx}, symbol={symbol_idx}, port={port_idx}")
            return
        
        records.sort(key=lambda r: r.subcarrier_in_prb)
        
        subcarriers = [r.subcarrier_in_prb for r in records]
        magnitudes = [r.magnitude for r in records]
        
        plt.figure(figsize=(10, 4))
        plt.stem(subcarriers, magnitudes, basefmt=' ')
        plt.xlabel('Subcarrier Index (within PRB)')
        plt.ylabel('Magnitude')
        plt.title(f'CSI Magnitude - Slot {slot_idx}, PRB {prb_idx}')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        return plt.gcf()
    
    def plot_prb_phase(self, slot_idx, prb_idx, symbol_idx=0, port_idx=0):
        """Plot phase vs subcarrier for a specific PRB"""
        records = [r for r in self.parser.records 
                   if r.slot_idx == slot_idx and r.prb_idx == prb_idx 
                   and r.symbol_idx == symbol_idx and r.port_idx == port_idx]
        
        if not records:
            print(f"No data for slot={slot_idx}, prb={prb_idx}, symbol={symbol_idx}, port={port_idx}")
            return
        
        records.sort(key=lambda r: r.subcarrier_in_prb)
        
        subcarriers = [r.subcarrier_in_prb for r in records]
        phases = [r.phase for r in records]
        
        plt.figure(figsize=(10, 4))
        plt.stem(subcarriers, phases, basefmt=' ')
        plt.xlabel('Subcarrier Index (within PRB)')
        plt.ylabel('Phase (radians)')
        plt.title(f'CSI Phase - Slot {slot_idx}, PRB {prb_idx}')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        return plt.gcf()
    
    def plot_constellation(self, slot_idx, prb_idx, symbol_idx=0, port_idx=0):
        """Plot I/Q constellation"""
        records = [r for r in self.parser.records 
                   if r.slot_idx == slot_idx and r.prb_idx == prb_idx 
                   and r.symbol_idx == symbol_idx and r.port_idx == port_idx]
        
        if not records:
            print(f"No data for slot={slot_idx}, prb={prb_idx}")
            return
        
        # Convert to I/Q
        i_vals = [r.magnitude * np.cos(r.phase) for r in records]
        q_vals = [r.magnitude * np.sin(r.phase) for r in records]
        
        plt.figure(figsize=(8, 8))
        plt.scatter(i_vals, q_vals, alpha=0.6, s=30)
        plt.xlabel('I')
        plt.ylabel('Q')
        plt.title(f'CSI Constellation - Slot {slot_idx}, PRB {prb_idx}')
        plt.grid(True, alpha=0.3)
        plt.axis('equal')
        plt.tight_layout()
        return plt.gcf()
    
    def plot_prb_heatmap(self, slot_idx):
        """Heatmap of magnitude across all PRBs for a slot"""
        records_by_prb = defaultdict(list)
        
        for r in self.parser.records:
            if r.slot_idx == slot_idx:
                records_by_prb[r.prb_idx].append(r)
        
        if not records_by_prb:
            print(f"No data for slot {slot_idx}")
            return
        
        # Create matrix: PRB x Subcarrier
        prbs = sorted(records_by_prb.keys())
        data = np.zeros((len(prbs), 12))
        
        for i, prb in enumerate(prbs):
            for record in records_by_prb[prb]:
                data[i, record.subcarrier_in_prb] = record.magnitude
        
        plt.figure(figsize=(12, 6))
        im = plt.imshow(data, aspect='auto', cmap='viridis', origin='lower')
        plt.colorbar(im, label='Magnitude')
        plt.xlabel('Subcarrier Index (within PRB)')
        plt.ylabel('PRB Index')
        plt.title(f'CSI Magnitude Heatmap - Slot {slot_idx}')
        plt.tight_layout()
        return plt.gcf()
    
    def plot_timeline(self, prb_idx=0, symbol_idx=0, port_idx=0):
        """Plot magnitude vs time for a specific PRB"""
        records = [r for r in self.parser.records 
                   if r.prb_idx == prb_idx and r.symbol_idx == symbol_idx and r.port_idx == port_idx]
        
        if not records:
            print(f"No data for prb={prb_idx}")
            return
        
        records.sort(key=lambda r: r.timestamp_us)
        
        times = [(r.timestamp_us - records[0].timestamp_us) / 1000 for r in records]  # ms
        magnitudes = [r.magnitude for r in records]
        
        plt.figure(figsize=(14, 4))
        plt.plot(times, magnitudes, '.-', linewidth=0.5, markersize=3)
        plt.xlabel('Time (ms)')
        plt.ylabel('Magnitude')
        plt.title(f'CSI Magnitude Timeline - PRB {prb_idx}')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        return plt.gcf()


def main():
    parser = argparse.ArgumentParser(description='CSI Data Visualizer')
    parser.add_argument('csi_file', help='Path to CSI data file')
    parser.add_argument('--slot', type=int, default=0, help='Slot index to visualize')
    parser.add_argument('--prb', type=int, default=0, help='PRB index to visualize')
    parser.add_argument('--symbol', type=int, default=0, help='Symbol index')
    parser.add_argument('--port', type=int, default=0, help='Port index')
    parser.add_argument('--output', help='Output directory for plots (instead of displaying)')
    parser.add_argument('--stats', action='store_true', help='Show statistics only')
    
    args = parser.parse_args()
    
    # Parse file
    csi_parser = CSIParser(args.csi_file)
    if not csi_parser.parse():
        sys.exit(1)
    
    # Show statistics
    csi_parser.get_statistics()
    
    if args.stats:
        return
    
    # Create visualizer
    visualizer = CSIVisualizer(csi_parser)
    
    if args.output:
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"\n[Visualizer] Saving plots to {output_dir}")
        
        fig = visualizer.plot_prb_magnitude(args.slot, args.prb, args.symbol, args.port)
        if fig:
            fig.savefig(output_dir / f'magnitude_slot{args.slot}_prb{args.prb}.png', dpi=100)
        
        fig = visualizer.plot_prb_phase(args.slot, args.prb, args.symbol, args.port)
        if fig:
            fig.savefig(output_dir / f'phase_slot{args.slot}_prb{args.prb}.png', dpi=100)
        
        fig = visualizer.plot_constellation(args.slot, args.prb, args.symbol, args.port)
        if fig:
            fig.savefig(output_dir / f'constellation_slot{args.slot}_prb{args.prb}.png', dpi=100)
        
        fig = visualizer.plot_prb_heatmap(args.slot)
        if fig:
            fig.savefig(output_dir / f'heatmap_slot{args.slot}.png', dpi=100)
        
        fig = visualizer.plot_timeline(args.prb, args.symbol, args.port)
        if fig:
            fig.savefig(output_dir / f'timeline_prb{args.prb}.png', dpi=100)
        
        print("[Visualizer] Done!")
    else:
        print("\n[Visualizer] Displaying plots (close window to continue)...\n")
        
        visualizer.plot_prb_magnitude(args.slot, args.prb, args.symbol, args.port)
        plt.show()
        
        visualizer.plot_prb_phase(args.slot, args.prb, args.symbol, args.port)
        plt.show()
        
        visualizer.plot_constellation(args.slot, args.prb, args.symbol, args.port)
        plt.show()
        
        visualizer.plot_prb_heatmap(args.slot)
        plt.show()
        
        visualizer.plot_timeline(args.prb, args.symbol, args.port)
        plt.show()


if __name__ == '__main__':
    main()
