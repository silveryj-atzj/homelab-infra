#!/bin/bash
# WordPress 仓库部署（Gitea push 触发）：git pull -> 覆盖同步 wp-content/themes & plugins
set -u
REPO=/srv/web-wordpress
CT=600
TAG="[wp-deploy]"

echo "$TAG start $(date -Is)"
cd "$REPO" || { echo "$TAG repo missing"; exit 1; }
git pull --ff-only 2>&1 | tail -n 3 || { echo "$TAG pull failed"; exit 1; }

# 备份现有主题/插件（方便回滚）
pct exec $CT -- bash -lc 'cd /opt/lemp/html/wp-content && tar -czf /tmp/wp-content-backup-$(date +%Y%m%d-%H%M%S).tgz themes plugins 2>/dev/null || true'
pct exec $CT -- bash -lc 'ls -t /tmp/wp-content-backup-*.tgz 2>/dev/null | tail -n +6 | xargs -r rm -f'

changed=0
for d in themes plugins; do
  src="$REPO/wp-content/$d"
  [ -d "$src" ] || continue
  # 只有仓库里真的有内容才同步（避免空目录把线上主题/插件清空）
  if [ -z "$(find "$src" -type f ! -name '.gitkeep' -print -quit)" ]; then
    echo "$TAG $d: repo empty, skip"
    continue
  fi
  echo "$TAG sync $d"
  tar -C "$src" -cf - . | pct exec $CT -- bash -lc "mkdir -p /opt/lemp/html/wp-content/$d && tar -xf - -C /opt/lemp/html/wp-content/$d"
  changed=1
done

if [ "$changed" = 1 ]; then
  pct exec $CT -- bash -lc 'chown -R 33:33 /opt/lemp/html/wp-content/themes /opt/lemp/html/wp-content/plugins 2>/dev/null || true'
  pct exec $CT -- docker exec web_php php /var/www/html/.wp-cli.phar --path=/var/www/html --allow-root cache flush >/dev/null 2>&1 || true
  echo "$TAG synced + cache flushed"
else
  echo "$TAG nothing to sync"
fi

echo "$TAG done $(date -Is)"
