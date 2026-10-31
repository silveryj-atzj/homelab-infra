#!/bin/bash
# GitHub Actions runner 上传的压缩包部署
# 用法: deploy-github.sh <astro|wp>
set -u
NAME="${1:-}"
TAG="[gh-deploy:$NAME]"
CT_ASTRO=602
CT_WEB=600
INCOMING=/tmp/incoming

echo "$TAG start $(date -Is)"

case "$NAME" in
  astro)
    SRC=/srv/gh-astro
    rm -rf "$SRC" && mkdir -p "$SRC"
    tar -xzf "$INCOMING/astro.tar.gz" -C "$SRC" || { echo "$TAG extract failed"; exit 1; }
    echo "$TAG build astro"
    pct exec $CT_ASTRO -- bash -lc "rm -rf /opt/astro-build && mkdir -p /opt/astro-build"
    tar -C "$SRC" --exclude=node_modules --exclude=dist -cf - . | pct exec $CT_ASTRO -- tar -xf - -C /opt/astro-build
    pct exec $CT_ASTRO -- bash -lc "mkdir -p /opt/astro-npm-cache && docker run --rm -v /opt/astro-build:/app -w /app -v /opt/astro-npm-cache:/root/.npm node:20-alpine sh -c 'npm ci --prefer-offline --no-audit --no-fund && npm run build'" 2>&1 | tail -n 12
    pct exec $CT_ASTRO -- bash -lc 'test -f /opt/astro-build/dist/index.html' || { echo "$TAG build failed"; exit 1; }
    pct exec 501 -- bash -lc 'rm -rf /opt/caddy/astro && mkdir -p /opt/caddy/astro'
    pct exec $CT_ASTRO -- tar -cf - -C /opt/astro-build/dist . | pct exec 501 -- tar -xf - -C /opt/caddy/astro
    pct exec 501 -- bash -lc "docker exec caddy sh -c 'rm -rf /srv/astro/*' && docker cp /opt/caddy/astro/. caddy:/srv/astro/"
    echo "$TAG astro deployed"
    ;;
  wp)
    SRC=/srv/gh-wp
    rm -rf "$SRC" && mkdir -p "$SRC"
    tar -xzf "$INCOMING/wp.tar.gz" -C "$SRC" || { echo "$TAG extract failed"; exit 1; }
    changed=0
    for d in themes plugins; do
      s="$SRC/wp-content/$d"
      [ -d "$s" ] || continue
      if [ -z "$(find "$s" -type f ! -name '.gitkeep' -print -quit)" ]; then
        echo "$TAG $d empty, skip"; continue
      fi
      echo "$TAG sync $d"
      tar -C "$s" -cf - . | pct exec $CT_WEB -- bash -lc "mkdir -p /opt/lemp/html/wp-content/$d && tar -xf - -C /opt/lemp/html/wp-content/$d"
      changed=1
    done
    if [ "$changed" = 1 ]; then
      pct exec $CT_WEB -- bash -lc 'chown -R 33:33 /opt/lemp/html/wp-content/themes /opt/lemp/html/wp-content/plugins 2>/dev/null || true'
      pct exec $CT_WEB -- docker exec web_php php /var/www/html/.wp-cli.phar --path=/var/www/html --allow-root cache flush >/dev/null 2>&1 || true
      echo "$TAG wp deployed"
    else
      echo "$TAG nothing to deploy"
    fi
    ;;
  *)
    echo "$TAG unknown target"
    exit 1
    ;;
esac

echo "$TAG done $(date -Is)"
