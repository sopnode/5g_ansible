# OAI CSI Logging - Visualization Role

**Prerequisites:** OAI must be built with CSI logging support (Docker image: `oai-gnb-csi:2026.w15`)

This role provides Python visualization tools for CSI measurements logged by OAI.

## Usage

Basic visualization:
csi_visualizer_oai.py /data/csi/csi_per_rb.csv

Real-time monitoring:
csi_visualizer_oai.py /data/csi/csi_per_rb.csv --realtime

Save plots to directory:
csi_visualizer_oai.py /data/csi/csi_per_rb.csv --output /tmp/plots

Statistics only:
csi_visualizer_oai.py /data/csi/csi_per_rb.csv --stats

Convenience alias:
csi_viz /data/csi/csi_per_rb.csv --realtime

## Output Format

CSV file at `/data/csi/csi_per_rb.csv`:

frame,slot,rb,subcarrier,real,imag
316,3,131,0,2486,-4454
316,3,131,1,2434,-4500

Where: real = I component, imag = Q component (c16_t signed 16-bit integers)

## Plots Generated

- RB magnitude distribution
- Magnitude heatmap (RB x Time)
- Constellation diagram (I-Q)
- Magnitude timeline
- Phase timeline

## Dependencies

Automatically installed by this role:
- python3-numpy
- python3-matplotlib

## Deployment

Enable in your Ansible playbook:

ansible-playbook playbooks/deploy.yml -e oai_csi_logging_enabled=true -e ran=oai

## Notes

- CSI logging must be enabled at OAI build time (included in oai-gnb-csi Docker image)
- Output directory /data/csi must have write permissions
- CSV grows ~1-2MB per minute depending on bandwidth
