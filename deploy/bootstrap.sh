#!/usr/bin/env bash
# Installs Docker on Ubuntu (if missing) and starts or updates the production stack.
# Safe to re-run: each run pulls the latest image and recreates only what changed.
#
# Usage, on the server, from a copy of this deploy/ directory:
#   cp .env.prod.example .env.prod   # then fill it in
#   sudo ./bootstrap.sh
# The Lightsail firewall must allow inbound TCP 80 and 443 (and UDP 443 for HTTP/3).
set -euo pipefail

cd "$(dirname "$0")"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo ./bootstrap.sh)." >&2
  exit 1
fi
if [[ ! -f .env.prod ]]; then
  echo "Missing .env.prod: copy .env.prod.example and fill it in." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

# .env.prod is not valid shell (e.g. CORS_ORIGINS holds JSON), so read single keys instead
# of sourcing it.
env_get() {
  grep -E "^$1=" .env.prod | tail -n 1 | cut -d= -f2- || true
}

ghcr_user=$(env_get GHCR_USER)
ghcr_token=$(env_get GHCR_TOKEN)
if [[ -n "$ghcr_user" && -n "$ghcr_token" ]]; then
  echo "$ghcr_token" | docker login ghcr.io -u "$ghcr_user" --password-stdin
fi

compose=(docker compose --env-file .env.prod -f docker-compose.prod.yml)
"${compose[@]}" pull
"${compose[@]}" up -d --remove-orphans
docker image prune -f

"${compose[@]}" ps
