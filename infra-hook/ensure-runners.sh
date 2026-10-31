#!/bin/bash
# 保持 GitHub self-hosted runner 在线：掉了就用 classic PAT 重新注册
set -u
PAT=$(cat /opt/infra-hook/github-pat)
IMAGE=myoung34/github-runner:latest
LOG=/var/log/infra-hook.log
log() { echo "$(date -Is) [runners] $*" >> "$LOG"; }

check_and_heal() { # ct name repo labels workdir
  local CT=$1 NAME=$2 REPO=$3 LABELS=$4 WORKDIR=$5
  local API="https://api.github.com/repos/${REPO#https://github.com/}/actions/runners"
  local status
  status=$(curl -s -m 20 -H "Authorization: Bearer $PAT" "$API" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(next((r['status'] for r in d.get('runners',[]) if r['name']=='$NAME'),'missing'))" 2>/dev/null || echo error)
  if [ "$status" = "online" ]; then
    return 0
  fi
  log "$NAME status=$status -> re-registering"
  local TOKEN
  TOKEN=$(curl -s -m 20 -H "Authorization: Bearer $PAT" -X POST \
    "https://api.github.com/repos/${REPO#https://github.com/}/actions/runners/registration-token" \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('token',''))")
  if [ -z "$TOKEN" ]; then log "$NAME token empty"; return 1; fi
  pct exec $CT -- bash -lc "mkdir -p $WORKDIR"
  pct exec $CT -- docker rm -f "$NAME" >/dev/null 2>&1 || true
  pct exec $CT -- docker run -d --name "$NAME" --restart unless-stopped \
    -e REPO_URL="$REPO" -e RUNNER_TOKEN="$TOKEN" -e RUNNER_NAME="$NAME" -e RUNNER_SCOPE=repo \
    -e LABELS="$LABELS" -e RUN_AS_ROOT=true -e EPHEMERAL=false -e DISABLE_AUTOMATIC_DEREGISTRATION=true \
    -v /var/run/docker.sock:/var/run/docker.sock -v "$WORKDIR:/home/runner/work" \
    "$IMAGE" >/dev/null && log "$NAME restarted"
}

check_and_heal 600 runner-wp https://github.com/your-github-user/website-repo "self-hosted,wordpress" /opt/runner-wp
check_and_heal 602 astro-runner https://github.com/your-github-user/astro-site "self-hosted,astro" /opt/runner-astro
