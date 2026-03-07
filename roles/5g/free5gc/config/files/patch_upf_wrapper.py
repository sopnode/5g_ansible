#!/usr/bin/env python3
import sys, os
repo = sys.argv[1]
files = [
    f"{repo}/charts/free5gc/charts/free5gc-upf/templates/psaupf1/psaupf1-configmap.yaml",
    f"{repo}/charts/free5gc/charts/free5gc-upf/templates/psaupf2/psaupf2-configmap.yaml",
]
old = '    iptables -t nat -A POSTROUTING -s {{ $.Values.global.uesubnet }} -o n6 -j MASQUERADE  # route traffic comming from the UE SUBNET to the interface N6\n    echo "1200 n6if" >> /etc/iproute2/rt_tables # create a routing table for the interface N6\n    ip rule add from {{ $.Values.global.uesubnet }} table n6if   # use the created ip table to route the traffic comming from the UE SUBNET\n    ip route add default via {{ $.Values.global.n6network.gatewayIP }} dev n6 table n6if  # add a default route in the created table so that all UEs will use this gateway for external communications (target IP not in the Data Network attached to the interface N6) and then the Data Network will manage to route the traffic'
new = '    iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE\n    ip route del default via {{ $.Values.global.n6network.gatewayIP }} dev n6 2>/dev/null || true\n    ip route | grep "^default" | grep -v "eth0" | while read -r gw via ip dev iface rest; do ip route del default via $ip dev $iface 2>/dev/null || true; done'
for f in files:
    if not os.path.exists(f):
        print(f"SKIP (not found): {f}")
        continue
    content = open(f).read()
    if old in content:
        open(f, 'w').write(content.replace(old, new))
        print(f"PATCHED: {f}")
    elif new in content:
        print(f"ALREADY PATCHED: {f}")
    else:
        print(f"NO MATCH: {f}")

