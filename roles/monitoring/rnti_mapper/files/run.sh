#!/bin/sh
# Periodically rebuild /www/metrics (ran_ue_info) and serve it with busybox httpd.
# The RAN file is read from a sidecar sharing the gNB log volume (OAI metrics-parser,
# OCUDU gnb-logs), so nothing is executed in the gNB container nor on its isolated cores.
set -u
D=/tmp/rnti-mapper
mkdir -p /www "$D"
: > /www/metrics
httpd -p "${PORT}" -h /www
while true; do
  kubectl -n "${NS}" exec "deploy/${RAN_DEPLOY}" -c "${RAN_CONTAINER}" -- grep -E "${RAN_PATTERN}" "${RAN_FILE}" \
    > "$D/ran.txt" 2> "$D/ran.err" || cat "$D/ran.err"
  kubectl -n "${CORE_NS}" logs "deploy/${AMF_DEPLOY}" -c "${AMF_CONTAINER}" --tail="${AMF_TAIL}" \
    > "$D/core.log" 2> "$D/core.err" || cat "$D/core.err"
  # exact RAN UE NGAP id <-> IMSI from the slice-awareness captures, when deployed
  : > "$D/inventory.json"
  [ -n "${INVENTORY_URL}" ] && wget -q -T 5 -O "$D/inventory.json" "${INVENTORY_URL}" 2> /dev/null
  awk -v ran="${RAN_TYPE}" -v core="${CORE_TYPE}" -v plmn="${PLMN}" -f /rnti-mapper/join.awk /rnti-mapper/ue_names \
    "$D/core.log" "$D/inventory.json" "$D/ran.txt" > /www/metrics.tmp \
    && mv /www/metrics.tmp /www/metrics
  sleep "${PERIOD}"
done
