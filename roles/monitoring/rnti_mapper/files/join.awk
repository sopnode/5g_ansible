# Join the RAN view (RNTI <-> RAN UE NGAP id) with the core view (RAN UE NGAP id <-> IMSI)
# and print Prometheus info metrics: ran_ue_info{rnti,ran_ue_id,imsi,ue_name} 1.
# Usage: awk -v ran=oai|ocudu -v core=oai|open5gs -v plmn=00101 -v dnn_snssai="internet=1 streaming=1-100000" \
#            -f join.awk ue_names core.log inventory.json sessions.log ran.txt
#   ue_names       "IMSI name" lines ('#' comments)
#   core.log       OAI AMF log (UEs' Information table) or Open5GS AMF log
#   inventory.json slice-awareness ue-mapper-api /inventory/ues (may be empty). Built from the
#                  N2/PFCP captures, it is exact per message and takes precedence over the log.
#   sessions.log   Open5GS UPF "UE F-SEID[UP:.. CP:..] APN[..] .. IPv4[..]" and SMF "UE SUPI[imsi-..] DNN[..] IPv4[..]"
#                  lines (may be empty): ran_session_info{snssai,seid,ue_ip,imsi,ue_name} 1, seid = UP SEID in
#                  decimal, as the slice_throughput label of the Open5GS KPI calculator
#   ran.txt        OAI nrRRC_stats.log, or OCUDU gNB log lines (cu/rrc/ngap at info level)
# The rnti label follows the gNB metrics: 4-digit hex for OAI, decimal for OCUDU (telegraf number(rnti)).
# Written for busybox awk (no gawk extensions).

function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }

# "0x1f" or "31" -> 31
function todec(s,   i, c, v, h) {
  s = tolower(trim(s))
  if (substr(s, 1, 2) != "0x") return s + 0
  h = "0123456789abcdef"; v = 0
  for (i = 3; i <= length(s); i++) { c = index(h, substr(s, i, 1)); if (c == 0) break; v = v * 16 + c - 1 }
  return v
}

function bind(id, im) { imsi_of[id] = im; delete amb[id] }

FILENAME == ARGV[1] { if ($1 !~ /^#/ && NF >= 2) name[$1] = $2; next }

FILENAME == ARGV[2] {
  gsub(/\033\[[0-9;]*m/, "")          # strip ANSI colors (Open5GS)
  if (core == "open5gs") { o5gs_line(); next }
  # OAI AMF "UEs' Information" table. Only the last table is kept.
  if (index($0, "UEs' Information")) { split("", imsi_of); split("", reg); next }
  n = split($0, f, "|")
  if (n >= 9 && trim(f[2]) ~ /^[0-9]+$/ && f[3] ~ /5GMM/) {     # data rows only (not the header)
    id = todec(f[6]); st = trim(f[3])
    # a registered UE wins over a stale (deregistered) entry with the same RAN UE NGAP id
    if (!(id in imsi_of) || (st == "5GMM-REGISTERED" && !reg[id])) {
      imsi_of[id] = trim(f[4]); reg[id] = (st == "5GMM-REGISTERED")
    }
  }
  next
}

# Open5GS AMF (single event loop). The InitialUEMessage handler prints, in one event:
#   "    RAN_UE_NGAP_ID[n] AMF_UE_NGAP_ID[m] ..." then "[imsi-X] Known UE by ..." (exact)
#   or "Unknown UE by ...": the identity then comes in later events ("[suci-...] SUCI",
#   "[imsi-X] Registration complete") that carry no NGAP id. Such an identity is bound only
#   if a single attach is pending; with overlapping attaches the ids are marked ambiguous
#   (the slice-awareness inventory, when present, resolves them exactly).
function o5gs_line(   im, p, k) {
  if (index($0, "InitialUEMessage")) { want = 1; return }
  if (want && match($0, /RAN_UE_NGAP_ID\[[0-9]+\]/)) {
    cur = substr($0, RSTART + 15, RLENGTH - 16) + 0; want = 0; return
  }
  if (cur != "") {
    if (index($0, "Known UE by") && !index($0, "Unknown UE by") && match($0, /\[imsi-[0-9]+\]/)) {
      bind(cur, substr($0, RSTART + 6, RLENGTH - 7))
    } else { pend[cur] = 1; npend++ }
    cur = ""; return
  }
  if (npend == 0) return
  im = ""
  if (match($0, /\[suci-0-[0-9]+-[0-9]+-[0-9]+-0-[0-9]+-[0-9]+\]/)) {
    split(substr($0, RSTART + 1, RLENGTH - 2), p, "-")   # suci 0 mcc mnc routing scheme key msin
    im = p[3] p[4] p[8]
  } else if (index($0, "Registration complete") && match($0, /\[imsi-[0-9]+\]/))
    im = substr($0, RSTART + 6, RLENGTH - 7)
  if (im == "") return
  if (npend == 1) { for (k in pend) bind(k, im) }
  else { for (k in pend) { amb[k] = 1; delete imsi_of[k] } }
  split("", pend); npend = 0
}

# Open5GS UPF/SMF session lines; for a (DNN, UE IP) the last line wins (UE IPs are reused)
FILENAME == ARGV[4] {
  gsub(/\033\[[0-9;]*m/, "")
  if (match($0, /UE F-SEID\[UP:0x[0-9a-fA-F]+ CP:0x[0-9a-fA-F]+\] APN\[[^]]+\] PDN-Type\[[0-9]+\] IPv4\[[0-9.]+\]/)) {
    split(substr($0, RSTART, RLENGTH), q, /[][ :]+/)   # 1 UE 2 F-SEID 3 UP 4 0xU 5 CP 6 0xC 7 APN 8 dnn 9 PDN-Type 10 n 11 IPv4 12 ip
    k = q[8] SUBSEP q[12]; upseid[k] = todec(q[4])
    if (!(k in seen_s)) { seen_s[k] = 1; skeys[++nsk] = k }
  } else if (match($0, /UE SUPI\[imsi-[0-9]+\] DNN\[[^]]+\] IPv4\[[0-9.]+\]/)) {
    split(substr($0, RSTART, RLENGTH), q, /[][ ]+/)    # 1 UE 2 SUPI 3 imsi-X 4 DNN 5 dnn 6 IPv4 7 ip
    sub(/^imsi-/, "", q[3]); supi_ip[q[5] SUBSEP q[7]] = q[3]
  }
  next
}

# slice-awareness inventory: {"ues":[{"imsi":"...","ran_ue_id":"0",...},...]}
FILENAME == ARGV[3] {
  buf = buf $0
  next
}

function load_inventory(   n, i, o, id, im, ip) {
  n = split(buf, o, "}")
  for (i = 1; i <= n; i++) {
    id = ""; im = ""
    if (match(o[i], /"ran_ue_id": *"?[0-9]+/)) { id = substr(o[i], RSTART, RLENGTH); gsub(/[^0-9]/, "", id) }
    if (match(o[i], /"imsi": *"[0-9]+"/)) { im = substr(o[i], RSTART + 7, RLENGTH - 7); gsub(/[^0-9]/, "", im) }
    if (im != "" && match(o[i], /"ue_ip": *"[0-9.]+"/)) { ip = substr(o[i], RSTART, RLENGTH); sub(/^"ue_ip": *"/, "", ip); sub(/"$/, "", ip); if (length(im) <= 10 && plmn != "") inv_ip[ip] = plmn im; else inv_ip[ip] = im }
    if (id == "" || im == "") continue
    if (length(im) <= 10 && plmn != "") im = plmn im    # MSIN only (from the SUCI)
    bind(id + 0, im)
  }
  loaded = 1
}

function emit(rnti, id,   im, nm) {
  if (!loaded) load_inventory()
  if ((id + 0) in imsi_of) im = imsi_of[id + 0]
  else if ((id + 0) in amb) im = "ambiguous"
  else im = "unknown"
  nm = (im in name) ? name[im] : im
  if (!hdr) hdr_print()
  printf "ran_ue_info{rnti=\"%s\",ran_ue_id=\"%s\",imsi=\"%s\",ue_name=\"%s\"} 1\n", rnti, id, im, nm
}

# OAI nrRRC_stats.log, "UE 0 CU UE ID 1 DU UE ID 19420 RNTI 4bdc random identity ...:"
ran != "ocudu" && /^UE [0-9]+ CU UE ID / {
  cu = ""; rnti = ""
  for (i = 1; i <= NF; i++) {
    if ($i == "CU" && $(i + 1) == "UE" && $(i + 2) == "ID") cu = $(i + 3)
    if ($i == "RNTI") rnti = tolower($(i + 1))
  }
  if (cu != "" && rnti != "") emit(rnti, cu)
  next
}

# OCUDU gNB log, CU-CP index "ue=N" in both:
#   [CU-CP] ue=0 c-rnti=0x4601: UE created      [RRC] ue=0 c-rnti=0x4601: ...
#   [NGAP] Tx PDU ue=0 ran_ue=0: InitialUEMessage
# A "UE created" line starts a new context on that index (the index is reused).
ran == "ocudu" {
  if (match($0, /ue=[0-9]+ c-rnti=0x[0-9a-fA-F]+/)) {
    split(substr($0, RSTART, RLENGTH), q, /[= ]/)          # ue N c-rnti 0xHHHH
    if (index($0, "UE created") || !(q[2] in crnti)) { order[++nue] = q[2]; delete ranid[q[2]] }
    crnti[q[2]] = todec(q[4])
  } else if (match($0, /ue=[0-9]+ ran_ue=[0-9]+/)) {
    split(substr($0, RSTART, RLENGTH), q, /[= ]/)          # ue N ran_ue M
    ranid[q[2]] = q[4] + 0
  }
  next
}

END {
  if (ran == "ocudu")
    for (i = 1; i <= nue; i++) {
      u = order[i]
      if (done[u]++ || !(u in ranid)) continue     # one row per live index, NGAP id known
      emit(crnti[u], ranid[u])
    }
}

END {
  if (!loaded) load_inventory()
  n = split(dnn_snssai, m, " ")
  for (i = 1; i <= n; i++) if (split(m[i], kv, "=") == 2) snssai_of[kv[1]] = kv[2]
  for (i = 1; i <= nsk; i++) {
    k = skeys[i]; split(k, d, SUBSEP)                  # d[1] = DNN, d[2] = UE IP
    im = (k in supi_ip) ? supi_ip[k] : ((d[2] in inv_ip) ? inv_ip[d[2]] : "unknown")
    nm = (im in name) ? name[im] : im
    if (!shdr) { print "# HELP ran_session_info Open5GS UPF session (UP SEID) to UE identity mapping (value is always 1)"; print "# TYPE ran_session_info gauge"; shdr = 1 }
    printf "ran_session_info{snssai=\"%s\",seid=\"%s\",ue_ip=\"%s\",imsi=\"%s\",ue_name=\"%s\"} 1\n", ((d[1] in snssai_of) ? snssai_of[d[1]] : d[1]), upseid[k], d[2], im, nm
  }
}

function hdr_print() { print "# HELP ran_ue_info RNTI to UE identity mapping (value is always 1)"; print "# TYPE ran_ue_info gauge"; hdr = 1 }
END { if (!hdr) hdr_print() }
