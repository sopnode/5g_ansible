#!/usr/bin/env python3
"""
patch_nads.py <chart_dest>

Patches all NAD Helm templates under <chart_dest> to make the default
route conditional on gatewayIP being set. This is needed when the
masterIf (physical NIC) has no IP in the NAD subnet.

Before:
    "routes": [
      {
        "dst": "0.0.0.0/0",
        "gw": "{{ .Values.global.n2network.gatewayIP }}"
      }
    ]

After:
    {{- if .Values.global.n2network.gatewayIP }}
    "routes": [
      {
        "dst": "0.0.0.0/0",
        "gw": "{{ .Values.global.n2network.gatewayIP }}"
      }
    ],
    {{- end }}
"""

import sys
import os
import re
import glob

def patch_nad_file(path):
    with open(path, 'r') as f:
        content = f.read()

    # Already patched
    if '{{- if' in content and 'gatewayIP' in content and 'routes' in content:
        already = re.search(r'\{\{-\s*if\s+\.Values\.\S+\.gatewayIP\s*\}\}', content)
        if already:
            print(f"  already patched: {path}")
            return False

    # Find the network variable name (e.g. n2network, n3network, n4network...)
    m = re.search(r'\.Values\.global\.(\w+network)\.gatewayIP', content)
    if not m:
        print(f"  skip (no gatewayIP reference): {path}")
        return False

    netvar = m.group(1)

    # Pattern to match the routes block
    routes_pattern = re.compile(
        r'(\s*"ipam":\s*\{\s*\n'
        r'\s*"type":\s*"static",\s*\n)'
        r'(\s*"routes":\s*\[\s*\n'
        r'\s*\{\s*\n'
        r'\s*"dst":\s*"0\.0\.0\.0/0",\s*\n'
        r'\s*"gw":\s*"[^"]*"\s*\n'
        r'\s*\}\s*\n'
        r'\s*\]\s*\n)'
        r'(\s*\})',
        re.MULTILINE
    )

    def replacer(m):
        ipam_open = m.group(1)
        routes_block = m.group(2)
        ipam_close = m.group(3)
        # Detect indentation
        indent = re.match(r'(\s*)', routes_block).group(1)
        return (
            f"{ipam_open}"
            f"{{{{- if and .Values.global.{netvar}.gatewayIP (ne .Values.global.{netvar}.gatewayIP \"\") }}}}\n"
            f"{routes_block}"
            f"{{{{- end }}}}\n"
            f"{ipam_close}"
        )

    new_content, count = routes_pattern.subn(replacer, content)

    if count == 0:
        print(f"  pattern not matched: {path}")
        return False

    with open(path, 'w') as f:
        f.write(new_content)
    print(f"  patched: {path}")
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: patch_nads.py <chart_dest>")
        sys.exit(1)

    chart_dest = sys.argv[1]

    # Find all NAD templates in free5gc and ueransim charts
    patterns = [
        os.path.join(chart_dest, 'charts/free5gc/**/*-nad.yaml'),
        os.path.join(chart_dest, 'charts/ueransim/**/*-nad.yaml'),
    ]

    nad_files = []
    for pattern in patterns:
        nad_files.extend(glob.glob(pattern, recursive=True))

    if not nad_files:
        print(f"No NAD files found under {chart_dest}")
        sys.exit(0)

    patched = 0
    for f in sorted(nad_files):
        if patch_nad_file(f):
            patched += 1

    print(f"\nDone: {patched}/{len(nad_files)} files patched.")


if __name__ == '__main__':
    main()
