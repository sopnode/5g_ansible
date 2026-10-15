#!/bin/sh
# Periodically rebuild /www/metrics (ran_ue_info) and serve it with busybox httpd.
# The RAN file is read with a niced grep: from a sidecar sharing the gNB log volume when there is
# one (OAI metrics-parser, OCUDU gnb-logs for /var/log/gnb.log), else in the gNB container (OCUDU n300/n320,
# /tmp/gnb.log).
set -u
D=/tmp/rnti-mapper
mkdir -p /www "$D"
: > /www/metrics
httpd -p "${PORT}" -h /www
while true; do
  # RAN_SOURCES: "container:file ...", the first existing file wins (grep: 0 match, 1 no match, 2 error).
  # The grep runs at nice 19; it is a few ms every PERIOD on a file of a few hundred kB.
  : > "$D/ran.txt"
  for src in ${RAN_SOURCES}; do
    kubectl -n "${NS}" exec "deploy/${RAN_DEPLOY}" -c "${src%%:*}" -- \
      sh -c 'nice -n 19 true 2> /dev/null && exec nice -n 19 grep -E "$0" "$1"; exec grep -E "$0" "$1"' \
      "${RAN_PATTERN}" "${src#*:}" > "$D/ran.txt" 2> "$D/ran.err"
    [ $? -le 1 ] && break
  done
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
