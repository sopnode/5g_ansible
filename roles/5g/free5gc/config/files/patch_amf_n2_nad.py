import sys, re

repo = sys.argv[1]
path = f"{repo}/charts/free5gc/charts/free5gc-amf/templates/amf-n2-nad.yaml"

with open(path) as f:
    content = f.read()

old = '          "type": {{ .Values.global.n2network.type | quote }},'
new = '''          "type": {{ .Values.global.n2network.type | quote }},
{{- if eq .Values.global.n2network.type "ovs" }}
          "bridge": {{ .Values.global.n2network.masterIf | quote }},
{{- end }}'''

content = content.replace(old, new)

with open(path, 'w') as f:
    f.write(content)

print("Patched amf-n2-nad.yaml")
