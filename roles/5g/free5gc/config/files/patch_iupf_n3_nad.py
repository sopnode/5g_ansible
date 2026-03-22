# roles/5g/free5gc/config/files/patch_iupf_n3_nad.py
import sys
repo = sys.argv[1]
path = f"{repo}/charts/free5gc/charts/free5gc-upf/templates/upf-n3-nad.yaml"
with open(path) as f:
    content = f.read()

old = '          "type": {{ .Values.global.n3network.type | quote }},\n          "capabilities": { "ips": true },\n          "master": {{ .Values.global.n3network.masterIf | quote }},'

new = '''          "type": {{ .Values.global.n3network.type | quote }},
          "capabilities": { "ips": true },
{{- if eq .Values.global.n3network.type "ovs" }}
          "bridge": {{ .Values.global.n3network.masterIf | quote }},
          "mtu": 1400,
{{- else }}
          "master": {{ .Values.global.n3network.masterIf | quote }},
{{- end }}'''

if old not in content:
    print(f"ERROR: pattern not found in {path}")
    sys.exit(1)

content = content.replace(old, new)

# Also remove the ipvlan/macvlan mode lines since ovs doesn't use "mode"
old2 = ('{{- if eq .Values.global.n3network.type "ipvlan" }}\n'
        '          "mode": "l2",\n'
        '{{- end }}\n')
content = content.replace(old2, (
    '{{- if eq .Values.global.n3network.type "ipvlan" }}\n'
    '          "mode": "l2",\n'
    '{{- end }}\n'
    '{{- if eq .Values.global.n3network.type "ovs" }}\n'
    '{{- end }}\n'
))

with open(path, 'w') as f:
    f.write(content)
print("Patched upf-n3-nad.yaml for OVS support")
