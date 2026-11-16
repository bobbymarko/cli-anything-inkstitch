#!/bin/bash
# Deploy the stitch editor (http://stitch, inkstitch-artifact.service on :8791).
#
# The service imports this package from the -live checkout through its PYTHONPATH, which
# overrides the dev checkout's editable install. Advancing the dev checkout deploys NOTHING
# (2026-11-13 and again 11-15: PRs #41-#48 were reported live while the service ran b704ccd).
# This advances -live, restarts, and prints DEPLOYED only when the running server is on it.
#
#   scripts/deploy_live.sh            # origin/main
#   scripts/deploy_live.sh <rev>
set -euo pipefail
LIVE=$HOME/code/ext/cli-anything-inkstitch-live
PY=$HOME/code/ext/cli-anything-inkstitch/.venv/bin/python
REV=${1:-origin/main}

systemctl --user cat inkstitch-artifact.service | grep -q "PYTHONPATH=.*cli-anything-inkstitch-live/src" \
  || { echo "FAIL: inkstitch-artifact.service PYTHONPATH no longer points at $LIVE/src: fix this script"; exit 1; }
git -C "$LIVE" fetch -q origin
git -C "$LIVE" checkout -q --detach "$REV"
want=$(git -C "$LIVE" rev-parse --short HEAD)

systemctl --user restart inkstitch-artifact.service
sleep 4
systemctl --user restart inkstitch-artifact-bridge.service
sleep 2
systemctl --user is-active --quiet inkstitch-artifact.service inkstitch-artifact-bridge.service \
  || { echo "FAIL: a service is not active"; exit 1; }

pid=$(systemctl --user show -p MainPID --value inkstitch-artifact.service)
tr '\0' '\n' < "/proc/$pid/environ" | grep -qx "PYTHONPATH=$LIVE/src" \
  || { echo "FAIL: running server (pid $pid) is not importing from $LIVE/src"; exit 1; }
got=$(cd / && PYTHONPATH=$LIVE/src "$PY" -c "import os, cli_anything_inkstitch as m; print(os.path.dirname(m.__file__))")
[[ "$got" == "$LIVE/src/"* ]] || { echo "FAIL: imports resolve to $got"; exit 1; }
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8791/)
[ "$code" = 200 ] || { echo "FAIL: editor answered HTTP $code"; exit 1; }
echo "DEPLOYED $want: server pid $pid imports $LIVE/src, HTTP 200"
