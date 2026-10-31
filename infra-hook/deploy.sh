#!/bin/bash
# 由 webhook 触发：git pull -> 把配置推到各 LXC -> 重启/热重载
set -u
REPO=/srv/infra
CT_CADDY=501
CT_WEB=600
CT_HOMEPAGE=601
CT_AI=602
LOG_TAG="[infra-deploy]"

echo "$LOG_TAG start $(date -Is)"
cd "$REPO" || { echo "$LOG_TAG repo missing"; exit 1; }

echo "$LOG_TAG git pull"
git pull --ff-only 2>&1 | tail -n 3 || { echo "$LOG_TAG pull failed"; exit 1; }

push() { # push <vmid> <local> <dest>
  pct push "$1" "$2" "$3" && echo "$LOG_TAG pushed $2 -> CT$1:$3"
}

# --- caddy (CT501) ---
if push $CT_CADDY "$REPO/caddy/Caddyfile" /opt/caddy/Caddyfile; then
  pct exec $CT_CADDY -- bash -lc \
    'docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null && docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile' \
    && echo "$LOG_TAG caddy reloaded"
fi

# --- homepage (CT601) ---
changed_home=0
for f in services.yaml settings.yaml widgets.yaml proxmox.yaml docker.yaml; do
  [ -f "$REPO/homepage/$f" ] || continue
  push $CT_HOMEPAGE "$REPO/homepage/$f" "/opt/homepage/config/$f" && changed_home=1
done
if [ "$changed_home" = 1 ]; then
  pct exec $CT_HOMEPAGE -- bash -lc 'cd /opt/homepage && docker compose restart >/dev/null 2>&1' \
    && echo "$LOG_TAG homepage restarted"
fi

# --- lemp / wordpress (CT600) ---
if push $CT_WEB "$REPO/lemp/docker-compose.yml" /opt/lemp/docker-compose.yml; then
  push $CT_WEB "$REPO/lemp/Dockerfile-php" /opt/lemp/Dockerfile-php
  push $CT_WEB "$REPO/lemp/default.conf" /opt/lemp/nginx-conf/default.conf
  pct exec $CT_WEB -- bash -lc 'cd /opt/lemp && docker compose up -d >/dev/null 2>&1' \
    && echo "$LOG_TAG lemp updated"
fi

# --- ai-stack (CT602) ---
if push $CT_AI "$REPO/ai-stack/docker-compose.yml" /opt/ai-stack/docker-compose.yml; then
  push $CT_AI "$REPO/ai-stack/bot.py" /opt/ai-stack/tg-bot/bot.py
  push $CT_AI "$REPO/ai-stack/Dockerfile-tg-bot" /opt/ai-stack/tg-bot/Dockerfile
  if [ -f "$REPO/ai-stack/cf-ai-proxy/proxy.py" ]; then
    pct exec $CT_AI -- bash -lc 'mkdir -p /opt/ai-stack/cf-ai-proxy'
    push $CT_AI "$REPO/ai-stack/cf-ai-proxy/proxy.py" /opt/ai-stack/cf-ai-proxy/proxy.py
    push $CT_AI "$REPO/ai-stack/cf-ai-proxy/Dockerfile" /opt/ai-stack/cf-ai-proxy/Dockerfile
  fi
  if [ -f "$REPO/ai-stack/st-tg-bridge/server.js" ]; then
    pct exec $CT_AI -- bash -lc 'mkdir -p /opt/ai-stack/st-tg-bridge'
    for f in server.js messageFormatter.js package.json Dockerfile config.example.js; do
      [ -f "$REPO/ai-stack/st-tg-bridge/$f" ] && push $CT_AI "$REPO/ai-stack/st-tg-bridge/$f" "/opt/ai-stack/st-tg-bridge/$f"
    done
  fi
  pct exec $CT_AI -- bash -lc 'cd /opt/ai-stack && docker compose up -d --build >/dev/null 2>&1; docker compose build telegram-bot >/dev/null 2>&1; docker compose up -d telegram-bot >/dev/null 2>&1' \
    && echo "$LOG_TAG ai-stack updated"
fi

# --- gitea (CT600) ---
if push $CT_WEB "$REPO/gitea/docker-compose.yml" /opt/gitea/docker-compose.yml; then
  pct exec $CT_WEB -- bash -lc 'cd /opt/gitea && docker compose up -d >/dev/null 2>&1' \
    && echo "$LOG_TAG gitea updated"
fi

echo "$LOG_TAG done $(date -Is)"
