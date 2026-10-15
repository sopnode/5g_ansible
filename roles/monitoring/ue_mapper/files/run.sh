#!/bin/sh
# Periodically rebuild /www/metrics (ran_ue_info) and serve it with busybox httpd.
# Reads the gNB RRC stats from the metrics-parser container (same shared volume as the gNB),
# so nothing is executed on the gNB container nor on its isolated cores.
set -u
mkdir -p /www /tmp/uemap
: > /www/metrics
httpd -p "${PORT}" -h /www
while true; do
  kubectl -n "${NS}" exec "deploy/${RAN_DEPLOY}" -c "${RAN_CONTAINER}" -- cat "${RAN_FILE}" \
    > /tmp/uemap/ran.txt 2> /tmp/uemap/ran.err || cat /tmp/uemap/ran.err
  kubectl -n "${CORE_NS}" logs "deploy/${AMF_DEPLOY}" -c "${AMF_CONTAINER}" --tail="${AMF_TAIL}" \
    > /tmp/uemap/amf.log 2> /tmp/uemap/amf.err || cat /tmp/uemap/amf.err
  awk -f /uemap/join.awk /uemap/ue_names /tmp/uemap/amf.log /tmp/uemap/ran.txt > /www/metrics.tmp \
    && mv /www/metrics.tmp /www/metrics
  sleep "${PERIOD}"
done
