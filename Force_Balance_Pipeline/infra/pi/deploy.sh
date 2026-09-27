#!/usr/bin/env bash
# Deploy the probe on the Raspberry Pi 3 (doc 04, Deployment on the Pi; doc 05, Phase 3 staging).
#
#   git clone --filter=blob:none --no-checkout <repo> /opt/force-probe/repo && cd /opt/force-probe/repo
#   git sparse-checkout set --cone Force_Balance_Pipeline/edge Force_Balance_Pipeline/warehouse/dbt/seeds Force_Balance_Pipeline/infra/pi
#   git checkout <full 40-character commit SHA>
#   Force_Balance_Pipeline/infra/pi/deploy.sh <full 40-character commit SHA>      # run from inside that clone (doc 05); infra/pi
#                                                                                  # is in the cone, so nothing is copied over separately
#
# It (re)clones the repository sparsely into /opt/force-probe/repo if that is not already a clone (only Force_Balance_Pipeline/edge,
# Force_Balance_Pipeline/warehouse/dbt/seeds and Force_Balance_Pipeline/infra/pi), checks out exactly that commit, builds the venv from
# edge/requirements.txt, creates the service user, installs the unit, and creates /etc/force-probe/probe.env from the template if it is
# missing. It does NOT start the service and never touches a password: fill in the env file yourself, then
# `sudo systemctl enable --now force-probe`. Safe to rerun with a new SHA.
set -euo pipefail

SHA="${1:-}"
if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: deploy.sh <full 40-character commit SHA>" >&2
  exit 1
fi
REPO_URL="${REPO_URL:-https://github.com/jivejong/JiveRepo.git}"
BASE=/opt/force-probe
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then SUDO="sudo"; fi

$SUDO apt-get install -y git python3-venv sqlite3 nftables >/dev/null

if ! id force-probe >/dev/null 2>&1; then
  $SUDO useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin force-probe
fi
$SUDO mkdir -p "$BASE"
$SUDO chown "$(id -un)":"$(id -gn)" "$BASE"

if [[ ! -d "$BASE/repo/.git" ]]; then
  git clone --filter=blob:none --no-checkout "$REPO_URL" "$BASE/repo"
fi
git -C "$BASE/repo" sparse-checkout set --cone Force_Balance_Pipeline/edge Force_Balance_Pipeline/warehouse/dbt/seeds Force_Balance_Pipeline/infra/pi
git -C "$BASE/repo" fetch origin
git -C "$BASE/repo" checkout --detach "$SHA"
if [[ "$(git -C "$BASE/repo" rev-parse HEAD)" != "$SHA" ]]; then
  echo "the checkout is not at $SHA" >&2
  exit 1
fi

python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install --quiet -r "$BASE/repo/Force_Balance_Pipeline/edge/requirements.txt"
echo "$SHA" > "$BASE/version"

$SUDO install -m 0644 "$HERE/force-probe.service" /etc/systemd/system/force-probe.service
$SUDO mkdir -p /etc/force-probe
if [[ ! -f /etc/force-probe/probe.env ]]; then
  $SUDO install -m 0600 -o root -g root "$HERE/probe.env.example" /etc/force-probe/probe.env
  echo "created /etc/force-probe/probe.env (mode 0600); fill it in with: sudoedit /etc/force-probe/probe.env"
fi
$SUDO systemctl daemon-reload

echo "deployed $SHA. Next: sudoedit /etc/force-probe/probe.env, then: sudo systemctl enable --now force-probe"
