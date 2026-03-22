#!/usr/bin/env python3
"""
patch_nads.py <chart_dest>

Patches all NAD Helm templates under <chart_dest> to make the default
route conditional on gatewayIP being set. This is needed when the
masterIf (physical NIC) has no IP in the NAD subnet (e.g. Flannel overlay).

Before:
          "ipam": {
            "type": "static",
            "routes": [
              {
                "dst": "0.0.0.0/0",
                "gw": "{{ .Values.global.nXnetwork.gatewayIP }}"
              }
            ]
          }

After:
          "ipam": {
{{- if and .Values.global.nXnetwork.gatewayIP (ne .Values.global.nXnetwork.gatewayIP "") }}
            "type": "static",
            "routes": [
              {
                "dst": "0.0.0.0/0",
                "gw": "{{ .Values.global.nXnetwork.gatewayIP }}"
              }
            ]
{{- else }}
            "type": "static"
{{- end }}
          }
"""

import sys
import os
import re
import glob


def patch_nad_file(path):
    content = open(path).read()

    # Already patched
    if '{{- else }}' in content and 'gatewayIP' in content:
        print(f"  already patched: {path}")
        return False

    # Find the network variable name (e.g. n2network, n3network, n4network...)
    m = re.search(r'\.Values\.global\.(\w+network)\.gatewayIP', content)
    if not m:
        print(f"  skip (no gatewayIP reference): {path}")
        return False

    netvar = m.group(1)

    old = (
        f'            "type": "static",\n'
        f'            "routes": [\n'
        f'              {{\n'
        f'                "dst": "0.0.0.0/0",\n'
        f'                "gw": "{{{{ .Values.global.{netvar}.gatewayIP }}}}"\n'
        f'              }}\n'
        f'            ]'
    )

    new = (
        f'{{{{- if and .Values.global.{netvar}.gatewayIP '
        f'(ne .Values.global.{netvar}.gatewayIP "") }}}}\n'
        f'            "type": "static",\n'
        f'            "routes": [\n'
        f'              {{\n'
        f'                "dst": "0.0.0.0/0",\n'
        f'                "gw": "{{{{ .Values.global.{netvar}.gatewayIP }}}}"\n'
        f'              }}\n'
        f'            ]\n'
        f'{{{{- else }}}}\n'
        f'            "type": "static"\n'
        f'{{{{- end }}}}'
    )

    if old not in content:
        print(f"  pattern not found: {path}")
        return False

    open(path, 'w').write(content.replace(old, new))
    print(f"  patched: {path}")
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: patch_nads.py <chart_dest>")
        sys.exit(1)

    chart_dest = sys.argv[1]

    nad_files = glob.glob(
        os.path.join(chart_dest, 'charts/**/*-nad.yaml'),
        recursive=True
    )

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
