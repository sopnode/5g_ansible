# OAI CSI collection and live view

`csi_per_rb.csv` holds the SRS channel estimates written by the CSI logger of the
`oai-gnb-csi` / `oai-gnb-aw2s-csi` images (sources, format and visualizer:
[oai-csi-logging](https://github.com/turletti/oai-csi-logging)).

## Enabling the logger

`-e csi_logger_enabled=true` at deploy time selects the `-csi` gNB images and forces
`do_SRS: periodic` / `do_CSIRS: 1` (see `roles/5g/oai/setup`). The gNB runs on the RAN node
(`[ran_node]`) and the logger writes the hostPath file `/data/csi/csi_per_rb.csv` there; the
file is truncated when the gNB starts.

## Collection after a run

Everything runs on the RAN node; only the results are fetched to the controller. The file is
read once, in place (no copy), at the lowest CPU/IO priority, up to its size at start and to
its last complete line; the full `.csv.gz`, the per-window files and the summary are produced
in that single pass. The controller clock is never used for CSI timing.

Standalone:

```bash
ansible-playbook -i inventory/<name>/hosts.ini playbooks/collect_csi_oai.yml
```

Results: `results/csi-oai-<run_id>/` with `csi_per_rb.csv.gz` and `csi_collection.json`
(pod, image, `CSI_*` variables, gNB `Init: N_RB_DL ... ofdm_symbol_size ...` line, format
version, rows, flush markers, dropped rows).

Inside a generic experiment (`playbooks/run_experiment.yml`), add to the artifact profile:

```yaml
collect:
  csi:
    enabled: true
    fetch_full: true          # also fetch the whole file
    window_types: section     # default; comma list of timeline window levels
```

Every timeline event then also records the RAN node clock (`ran_epoch`), and the CSI log is
cut per window into `results/experiment-<run_id>/csi/by_window/<type>/<window>/csi_per_rb.csv.gz`.
Cutting is done per logger flush batch (~5 s): a batch goes to a window when its acquisition
interval overlaps the window, and the per-window files keep the JSON header and the markers
of their batches so the visualizer reads them directly.

## Live view (optional)

A streamer on the monitor node follows the file of the RAN node over ssh, and the visualizer
(v8.8, live mode) shows a sliding window of it. Nothing but `sshd` and `tail -F` (lowest
CPU/IO priority) runs on the RAN node; Streamlit and the parsing run on the monitor node.

```bash
ansible-playbook -i inventory/<name>/hosts.ini playbooks/csi_live.yml                        # start
ansible-playbook -i inventory/<name>/hosts.ini playbooks/csi_live.yml -e csi_live_action=status
ansible-playbook -i inventory/<name>/hosts.ini playbooks/csi_live.yml -e csi_live_action=stop
```

`start` prints the ssh tunnel command to run on your laptop, then open http://localhost:8501.

- The live host is the first `[monitor_node]` (or `-e csi_live_host=<node>`, never the RAN node).
  It must reach the RAN node with ssh (`csi_live_ran_address`, inventory address by default).
- `start` creates an ed25519 key on the live host and authorizes it on the RAN node with
  `restrict,command="/usr/local/bin/csi_live_source.sh"`: that key can only stream the CSI
  file. `stop` revokes the key, removes the script and stops both processes.
- The copy on the live host (`/root/csi_live/csi_per_rb.csv`) starts from "now", restarts after
  a reconnection or beyond `csi_live_max_mb`, and is removed at `stop`: it is not the
  reference data, which stays on the RAN node for the collection above.
- The viewer is downloaded from the oai-csi-logging repository (`csi_live_visualizer_ref`), or
  copied from the controller with `-e csi_live_visualizer_src=<path>`; its Python packages are
  installed in a venv on the live host (network access needed for pip).

## Variables (defaults/main.yml)

| Variable | Default | Meaning |
|---|---|---|
| `csi_ran_host` | first `[ran_node]` | Node running the gNB |
| `csi_output_dir`, `csi_file_name` | `/data/csi`, `csi_per_rb.csv` | File on the RAN node |
| `csi_k8s_namespace` | `{{ core }}` | Namespace of the gNB pod (metadata only) |
| `csi_pod_selector`, `csi_container` | oai-gnb / `gnb` (oai-du / `du` in cudu mode) | Pod and container (metadata only) |
| `csi_fetch_full` | `true` | Fetch the full file (gzip -1) |
| `csi_window_types` | `section` | Window levels used to cut the file |
| `csi_cpu_list` | `""` | Optional housekeeping CPUs for the RAN node side (never isolated CPUs) |
| `csi_cleanup_remote` | `true` | Remove the RAN node work directory |
| `csi_live_host` | first `[monitor_node]` | Host of the live streamer and viewer |
| `csi_live_port`, `csi_live_window_s`, `csi_live_refresh_s` | `8501`, `120`, `10` | Viewer |
| `csi_live_max_mb` | `1024` | Size cap of the live copy |

## Analysis

Copy the result directory from the controller to your laptop, `gunzip` the `.csv.gz` files
and open them with `oai-csi-logging/visualizer/streamlit_csi_visualizer_v8.8.py` (upload mode).
