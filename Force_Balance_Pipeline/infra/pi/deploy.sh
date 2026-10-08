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
# missing. If force-probe is already running (a redeploy), it restarts it to pick up the new code and verifies the result; it never
# touches a password. On the very first deploy (never enabled) it does NOT start the service, since probe.env is still just the
# template at that point: fill it in yourself, then `sudo systemctl enable --now force-probe`. Safe to rerun with a new SHA.
set -euo pipefail

SHA="${1:-}"
if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: deploy.sh <full 40-character commit SHA>" >&2
  exit 1
fi
DEPLOY_START_EPOCH="$(date -u +%s)"    # a running process older than this did not pick up this deploy -- checked at the end
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

# Restart only if force-probe was already running -- not unconditionally. A unit that has never been
# enabled/started yet (first-ever deploy) has no MQTT credentials in /etc/force-probe/probe.env beyond
# the template just installed above; starting it now (restart on a never-started unit behaves like start)
# would crash-loop on missing credentials. A REDEPLOY of an already-running unit is the case this fixes --
# installing a new unit file and running daemon-reload changes what systemd WOULD run next time, but does
# nothing to an already-forked process already running the old ExecStart; only restart (or stop+start)
# replaces it. Found live, 2026-10-06: deploy.sh updated the unit file and ran daemon-reload successfully
# (`systemctl cat` showed the new ExecStart, NeedDaemonReload=no) but the running process was untouched,
# silently still on the previous commit's flags until a manual `sudo systemctl restart force-probe`.
WAS_ACTIVE=0
if systemctl is-active --quiet force-probe; then
  WAS_ACTIVE=1
  echo "force-probe is running; restarting it to pick up $SHA"
  $SUDO systemctl restart force-probe
fi

if [[ "$WAS_ACTIVE" -eq 1 ]]; then
  if ! systemctl is-active --quiet force-probe; then
    echo "force-probe did not come back up after restart" >&2
    systemctl status --no-pager force-probe >&2 || true
    exit 1
  fi
  START_TS="$(systemctl show -p ExecMainStartTimestamp --value force-probe)"
  MAIN_PID="$(systemctl show -p MainPID --value force-probe)"
  START_EPOCH="$(date -d "$START_TS" +%s)"
  if [[ "$START_EPOCH" -lt "$DEPLOY_START_EPOCH" ]]; then
    echo "force-probe's running process (PID $MAIN_PID, started $START_TS) predates this deploy -- it was not actually restarted" >&2
    exit 1
  fi
  CHECKED_OUT_SHA="$(git -C "$BASE/repo" rev-parse HEAD)"
  if [[ "$CHECKED_OUT_SHA" != "$SHA" ]]; then
    echo "checked-out SHA $CHECKED_OUT_SHA does not match requested $SHA" >&2
    exit 1
  fi
  echo "verified: force-probe running since $START_TS, SHA $CHECKED_OUT_SHA"
  ps -p "$MAIN_PID" -o pid,lstart,args
  echo "deployed $SHA."
else
  echo "deployed $SHA. force-probe is not running yet (not previously enabled) -- fill in probe.env, then: sudo systemctl enable --now force-probe"
fi
