#!/bin/sh
# Periodically rebuild /www/metrics (ran_ue_info, ran_session_info) and serve it with busybox httpd.
#
# RAN side: the gNB pod (or the CU / CU-CP pod in split mode, where the RRC runs) is found with the
# first RAN_SELECTORS label selector matching a running pod; its containers are listed at run time,
# the RAN_SIDECARS ones (sharing the gNB log volume: OAI metrics-parser, OCUDU gnb-logs) first, and
# the first readable file of RAN_FILES is grepped (nice 19, a few ms every PERIOD). The winning
# pod/container/file is tried first on the next cycles.
set -u
D=/tmp/rnti-mapper
mkdir -p /www "$D"
: > /www/metrics
httpd -p "${PORT}" -h /www
last=""
# $1 pod, $2 container, $3 file: 0 = file read (matches or not), else not readable / no such container
read_ran() {
  kubectl -n "${NS}" exec "$1" -c "$2" -- sh -c '[ -r "$1" ] || exit 3
    if nice -n 19 true 2> /dev/null; then nice -n 19 grep -E "$0" "$1"; else grep -E "$0" "$1"; fi; exit 0' \
    "${RAN_PATTERN}" "$3" > "$D/ran.txt" 2>> "$D/ran.err"
}
while true; do
  : > "$D/ran.txt"; : > "$D/ran.err"
  pod=""
  for sel in ${RAN_SELECTORS}; do
    pod=$(kubectl -n "${NS}" get pod -l "$sel" --field-selector=status.phase=Running -o name 2> /dev/null | head -1)
    [ -n "$pod" ] && break
  done
  ok=0
  if [ -z "$pod" ]; then
    echo "no running gNB/CU pod for: ${RAN_SELECTORS} (namespace ${NS})"
  else
    set -- ${last}
    if [ $# -eq 3 ] && [ "$1" = "$pod" ] && read_ran "$1" "$2" "$3"; then
      ok=1
    else
      ctrs=$(kubectl -n "${NS}" get "$pod" -o jsonpath='{.spec.containers[*].name}' 2>> "$D/ran.err")
      ordered=""
      for c in $ctrs; do case " ${RAN_SIDECARS} " in *" $c "*) ordered="$c $ordered";; *) ordered="$ordered $c";; esac; done
      for c in $ordered; do
        for f in ${RAN_FILES}; do
          if read_ran "$pod" "$c" "$f"; then ok=1; last="$pod $c $f"; echo "RAN source: $pod $c $f"; break 2; fi
        done
      done
    fi
    [ $ok = 1 ] || { echo "no readable RAN file (${RAN_FILES}) in $pod"; cat "$D/ran.err"; }
  fi
  kubectl -n "${CORE_NS}" logs "deploy/${AMF_DEPLOY}" -c "${AMF_CONTAINER}" --tail="${AMF_TAIL}" \
    > "$D/core.log" 2> "$D/core.err" || cat "$D/core.err"
  # Open5GS: UPF "UE F-SEID[...]" and SMF "UE SUPI[...]" session lines (UP SEID <-> UE IP <-> IMSI)
  : > "$D/sessions.log"
  if [ "${CORE_TYPE}" = open5gs ]; then
    for d in $(kubectl -n "${CORE_NS}" get deploy -o name 2> /dev/null | grep -E 'upf|smf'); do
      kubectl -n "${CORE_NS}" logs "$d" --all-containers 2> /dev/null | grep -E 'UE F-SEID\[|UE SUPI\[' >> "$D/sessions.log"
    done
  fi
  # exact RAN UE NGAP id <-> IMSI from the slice-awareness captures, when deployed
  : > "$D/inventory.json"
  [ -n "${INVENTORY_URL}" ] && wget -q -T 5 -O "$D/inventory.json" "${INVENTORY_URL}" 2> /dev/null
  awk -v ran="${RAN_TYPE}" -v core="${CORE_TYPE}" -v plmn="${PLMN}" -v dnn_snssai="${DNN_SNSSAI}" \
    -f /rnti-mapper/join.awk /rnti-mapper/ue_names \
    "$D/core.log" "$D/inventory.json" "$D/sessions.log" "$D/ran.txt" > /www/metrics.tmp \
    && mv /www/metrics.tmp /www/metrics
  sleep "${PERIOD}"
done
