#!/usr/bin/env bash
# remote-deploy.sh — runs ON the EC2 instance (Amazon Linux 2023, arm64).
#
# Idempotent. Installs Docker, mounts the retained EBS volume by filesystem
# label/UUID, writes the app .env from SSM Parameter Store, pulls the deployed
# image from ECR, and starts the scheduler + ui containers via compose.
#
# Invoked from cloud-init user-data (first boot) and via SSM send-command for
# redeploys/rollback (scripts/cloud/deploy.sh). Also embedded verbatim in
# infra/aws/user_data.tpl (passed as a templatefile value).
#
# Env vars:
#   APP_IMAGE   required  Full ECR image URI to run
#   DATA_DIR    default   /opt/property-hunter/data   (EBS mount + compose volume)
#   REGION      default   us-east-1
#   COMPOSE_DIR default   /opt/property-hunter        (compose file + .env live here)

set -euo pipefail

APP_IMAGE="${APP_IMAGE:?APP_IMAGE is required}"
DATA_DIR="${DATA_DIR:-/opt/property-hunter/data}"
REGION="${REGION:-us-east-1}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/property-hunter}"
VOLUME_LABEL="ph-data"   # ext4 labels are limited to 16 chars

log() { printf '[remote-deploy] %s\n' "$*"; }

mkdir -p "$DATA_DIR" "$COMPOSE_DIR"

# --- AWS CLI (AL2023 ships it; ensure anyway) --------------------------------
if ! command -v aws >/dev/null 2>&1; then
  log "installing AWS CLI"
  dnf install -y aws-cli >/dev/null
fi

# --- Docker + compose plugin -------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "installing Docker"
  dnf install -y docker >/dev/null
fi
if ! docker compose version >/dev/null 2>&1; then
  log "installing compose plugin"
  if ! dnf install -y docker-compose-plugin >/dev/null 2>&1; then
    log "dnf package unavailable; downloading compose v2 binary"
    mkdir -p /usr/local/libexec/docker/cli-plugins
    case "$(uname -m)" in
      aarch64) comp_arch=aarch64 ;;
      x86_64)  comp_arch=x86_64 ;;
      *)       log "ERROR: unsupported arch $(uname -m)"; exit 1 ;;
    esac
    curl -fsSL -o /usr/local/libexec/docker/cli-plugins/docker-compose \
      "https://github.com/docker/compose/releases/download/v2.24.6/docker-compose-linux-${comp_arch}"
    chmod +x /usr/local/libexec/docker/cli-plugins/docker-compose
  fi
fi
systemctl enable --now docker >/dev/null 2>&1 || true
for _ in $(seq 1 30); do
  docker info >/dev/null 2>&1 && break
  sleep 2
done
docker info >/dev/null 2>&1 || { log "ERROR: docker daemon not ready"; exit 1; }
log "docker ready: $(docker --version)"

# --- Retained EBS volume: format once, mount by label/UUID (T026) ------------
if blkid -l -t "LABEL=$VOLUME_LABEL" >/dev/null 2>&1; then
  log "volume already formatted ($VOLUME_LABEL)"
else
  log "searching for an unformatted disk to adopt"
  disk="$(lsblk -dn -o NAME,TYPE | awk '$2=="disk"{print "/dev/"$1}' | while read -r d; do
    if [ -b "$d" ] && ! blkid "$d" >/dev/null 2>&1; then
      printf '%s\n' "$d"
      break
    fi
  done)"
  if [ -n "$disk" ]; then
    log "formatting $disk as ext4 (label=$VOLUME_LABEL)"
    mkfs.ext4 -L "$VOLUME_LABEL" "$disk" >/dev/null
  else
    log "WARNING: no unformatted disk found; continuing without the data volume"
  fi
fi

uuid="$(blkid -l -t "LABEL=$VOLUME_LABEL" -s UUID -o value 2>/dev/null || true)"
if [ -n "$uuid" ]; then
  if ! grep -q "$uuid" /etc/fstab; then
    log "adding $DATA_DIR to /etc/fstab (UUID=$uuid)"
    printf 'UUID=%s %s ext4 defaults,noatime,nofail 0 2\n' "$uuid" "$DATA_DIR" >> /etc/fstab
  fi
  if ! mountpoint -q "$DATA_DIR"; then
    log "mounting $DATA_DIR"
    mount "$DATA_DIR"
  fi
  log "data volume mounted at $DATA_DIR (UUID=$uuid)"
else
  log "WARNING: data volume not found; data will NOT persist this cycle"
fi

# --- App .env from SSM Parameter Store (research §6) -------------------------
log "fetching secrets from /property-hunter/*"
aws ssm get-parameters-by-path --path /property-hunter --recursive --with-decryption \
  --region "$REGION" --output json > /tmp/ph-params.json
python3 - "$COMPOSE_DIR/.env" <<'PY'
import json, sys
out = sys.argv[1]
data = json.load(open("/tmp/ph-params.json"))["Parameters"]
lines = {}
for p in data:
    key = p["Name"].rsplit("/", 1)[-1]
    if key:
        lines[key] = p["Value"]
# The image mounts /app/data as the data volume; force the DB there so env_file
# values never point the container at a host-relative path.
lines["DB_PATH"] = "/app/data/property_hunter.db"
with open(out, "w") as f:
    for k in sorted(lines):
        v = lines[k]
        if "\n" in v:
            v = '"' + v.replace("\n", "\\n") + '"'
        f.write(f"{k}={v}\n")
print(f"wrote {len(lines)} env keys to {out}")
PY

# --- ECR auth + pull ---------------------------------------------------------
registry="$(printf '%s' "$APP_IMAGE" | sed -E 's#^([^/]+)/.*#\1#')"
log "authenticating to $registry"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry" >/dev/null
log "pulling $APP_IMAGE"
docker pull "$APP_IMAGE" >/dev/null

# --- Compose file + Caddyfile ship inside the image; extract fresh each deploy --
cid="$(docker create "$APP_IMAGE")"
docker cp "$cid":/app/docker-compose.cloud.yml "$COMPOSE_DIR/docker-compose.cloud.yml"
docker cp "$cid":/app/Caddyfile "$COMPOSE_DIR/Caddyfile"
docker rm "$cid" >/dev/null

# --- Start the app -----------------------------------------------------------
log "starting scheduler + ui + caddy via compose"
cd "$COMPOSE_DIR"
APP_IMAGE="$APP_IMAGE" DATA_DIR="$DATA_DIR" docker compose -f docker-compose.cloud.yml up -d

# --- Health check ------------------------------------------------------------
log "waiting for the dashboard health check (http://127.0.0.1:9000/)"
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null http://127.0.0.1:9000/; then
    log "dashboard healthy at http://127.0.0.1:9000/"
    exit 0
  fi
  sleep 2
done
log "ERROR: dashboard did not become healthy"
docker compose -f "$COMPOSE_DIR/docker-compose.cloud.yml" ps >&2 || true
exit 1
