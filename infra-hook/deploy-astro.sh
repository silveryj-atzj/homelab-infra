#!/bin/bash
# Astro 博客自动部署：git pull -> 清空构建目录 -> 复制源码 -> CT602 node 容器构建 -> dist 推到 Caddy
set -u
REPO=/srv/web-astro
BUILD=/opt/astro-build
TAG="[astro-deploy]"

echo "$TAG start $(date -Is)"
cd "$REPO" || { echo "$TAG repo missing"; exit 1; }
git pull --ff-only 2>&1 | tail -n 3 || { echo "$TAG pull failed"; exit 1; }

# 关键：先清空，避免已删除的文件残留在构建目录里
echo "$TAG clean + sync source -> CT602:$BUILD"
pct exec 602 -- bash -lc "rm -rf $BUILD && mkdir -p $BUILD"
tar -C "$REPO" --exclude=node_modules --exclude=dist --exclude=.git -cf - . \
  | pct exec 602 -- tar -xf - -C "$BUILD"

echo "$TAG build in node container"
pct exec 602 -- bash -lc "mkdir -p /opt/astro-npm-cache && docker run --rm -v $BUILD:/app -w /app -v /opt/astro-npm-cache:/root/.npm node:20-alpine sh -c 'npm ci --prefer-offline --no-audit --no-fund && npm run build'" 2>&1 | tail -n 20

pct exec 602 -- bash -lc "test -f $BUILD/dist/index.html" || { echo "$TAG build produced no dist, abort"; exit 1; }

echo "$TAG push dist -> Caddy"
pct exec 501 -- bash -lc 'rm -rf /opt/caddy/astro && mkdir -p /opt/caddy/astro'
pct exec 602 -- tar -cf - -C "$BUILD/dist" . | pct exec 501 -- tar -xf - -C /opt/caddy/astro
pct exec 501 -- bash -lc "docker exec caddy sh -c 'rm -rf /srv/astro/*' && docker cp /opt/caddy/astro/. caddy:/srv/astro/"

echo "$TAG done $(date -Is)"
