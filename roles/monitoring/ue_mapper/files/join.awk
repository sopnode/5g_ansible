# Join the RAN view (RNTI <-> RAN UE id) with the core view (RAN UE NGAP id <-> IMSI)
# and print Prometheus info metrics: ran_ue_info{rnti,ran_ue_id,imsi,ue_name} 1.
# Usage: awk -f join.awk ue_names amf.log ran.txt
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

# File 1: "IMSI name" lines, '#' comments allowed
FILENAME == ARGV[1] { if ($1 !~ /^#/ && NF >= 2) name[$1] = $2; next }

# File 2: OAI AMF log, "UEs' Information" table. Only the last table is kept.
FILENAME == ARGV[2] {
  if (index($0, "UEs' Information")) { split("", imsi_of); split("", reg); next }
  n = split($0, f, "|")
  if (n >= 9 && f[3] ~ /5GMM/) {
    id = todec(f[6]); st = trim(f[3])
    # a registered UE wins over a stale (deregistered) entry with the same RAN UE NGAP id
    if (!(id in imsi_of) || (st == "5GMM-REGISTERED" && !reg[id])) {
      imsi_of[id] = trim(f[4]); reg[id] = (st == "5GMM-REGISTERED")
    }
  }
  next
}

# File 3: OAI nrRRC_stats.log, "UE 0 CU UE ID 1 DU UE ID 19420 RNTI 4bdc random identity ...:"
/^UE [0-9]+ CU UE ID / {
  cu = ""; rnti = ""
  for (i = 1; i <= NF; i++) {
    if ($i == "CU" && $(i + 1) == "UE" && $(i + 2) == "ID") cu = $(i + 3)
    if ($i == "RNTI") rnti = tolower($(i + 1))
  }
  if (cu == "" || rnti == "") next
  im = ((cu + 0) in imsi_of) ? imsi_of[cu + 0] : "unknown"
  nm = (im in name) ? name[im] : im
  if (!hdr) { print "# HELP ran_ue_info RNTI to UE identity mapping (value is always 1)"; print "# TYPE ran_ue_info gauge"; hdr = 1 }
  printf "ran_ue_info{rnti=\"%s\",ran_ue_id=\"%s\",imsi=\"%s\",ue_name=\"%s\"} 1\n", rnti, cu, im, nm
}
END { if (!hdr) { print "# HELP ran_ue_info RNTI to UE identity mapping (value is always 1)"; print "# TYPE ran_ue_info gauge" } }
