#!/bin/bash
# Rendered from infra/aws/user_data.tpl. Runs the on-instance deploy script
# (scripts/cloud/remote/remote-deploy.sh) on first boot. The script body is
# embedded verbatim so the instance never needs a git checkout.
set -euo pipefail

mkdir -p /opt/property-hunter

cat > /opt/property-hunter/remote-deploy.sh <<'REMOTE'
${remote_deploy_script}
REMOTE

chmod +x /opt/property-hunter/remote-deploy.sh

APP_IMAGE=${app_image} DATA_DIR=${data_dir} REGION=${region} \
  /opt/property-hunter/remote-deploy.sh
