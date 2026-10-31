# Runbook

Operational notes and the incidents that shaped this setup.

## Common operations

### Deploy a config change
```bash
git add -A && git commit -m "..." && git push
# Gitea webhook → infra-hook applies it; check the log:
tail -n 50 /var/log/infra-hook.log
```

### Rebuild a container
```bash
docker compose -f ai-stack/docker-compose.yml up -d --build
```

### Check service health
```bash
# internal, no ports needed
curl -s -o /dev/null -w '%{http_code}\n' https://<service>.example.ts.net/
```

### Reload Caddy after editing the Caddyfile
```bash
docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

---

## Incidents and lessons

### 1. Inference process exiting with 139
**Symptom** — Container restart loop, no CUDA device.
**Root cause** — A directory named `libnvidia-ptxjitcompiler.so.1` shadowed the real library file inside the container.
**Fix** — Remove the shadowing directories, push the real library from the host, re-create the symlinks.
**Lesson** — When a process dies with a signal, check that libraries are files, not directories.

### 2. WordPress admin infinite redirect
**Symptom** — `/wp-admin` returns 302 to itself forever.
**Root cause** — Cloudflare terminates TLS; the reverse proxy replaced `X-Forwarded-Proto` with `http`; WordPress (`force_ssl_admin`) kept redirecting to HTTPS.
**Fix** — In the Caddy reverse_proxy block: `header_up X-Forwarded-Proto https`; additionally a `CF-Visitor` fallback in `wp-config.php`.
**Lesson** — Behind a TLS-terminating edge, the origin's scheme detection is a config problem, not a WordPress bug.

### 3. Caddy routing silently not applied
**Symptom** — A newly added path matcher returned 404 or redirected unexpectedly.
**Root cause** — Directive ordering: a catch-all `handle`/`redir` ran before the intended matcher; nested `handle` blocks do not evaluate in the order they appear.
**Fix** — Use a single `handle` with explicit matchers (`rewrite @matcher`, `redir @other`) or `route` to preserve order.
**Lesson** — In Caddy, order is part of the logic.

### 4. Deployment left stale files
**Symptom** — A deleted source file was still being served after deploy.
**Root cause** — Sync copied files over the old tree without removing them; also `docker exec caddy rm -rf /srv/astro/*` had the `*` expanded by the host shell, so nothing was removed inside the container.
**Fix** — Clear the build directory before syncing; run globbing inside the container (`sh -c 'rm -rf ...'`).

### 5. Gitea webhooks not delivered to internal addresses
**Symptom** — Webhook exists, new pushes do nothing.
**Root cause** — Gitea's egress policy blocks private IP targets by default.
**Fix** — `GITEA__webhook__ALLOWED_HOST_LIST=private`.

### 6. GitHub runner never picking up jobs
**Symptom** — Workflows stay queued; runner container in a restart loop.
**Root cause** — Registration token is single-use and expires after one hour; the runner's entrypoint deregisters itself on exit, so a restart with the old token cannot register again.
**Fix** — `DISABLE_AUTOMATIC_DEREGISTRATION=true`, plus a systemd timer that re-registers the runner when it is offline.
**Lesson** — Treat self-hosted runners as stateful: protect their config or automate re-registration.

### 7. Windows `tar` crash on non-ASCII paths
**Symptom** — Archive creation returned exit code `0xC0000005` and produced a 0-byte file.
**Fix** — Build archives with Python's `zipfile`/`shutil` instead of the bundled `tar`.

### 8. Docker volume permissions broke WordPress plugin installs
**Symptom** — "Could not create directory" when installing a theme.
**Root cause** — `wp-content` was owned by root, PHP runs as `www-data`.
**Fix** — `chown -R 33:33 wp-content`; deployment scripts do the same after syncing.

---

## Useful one-liners

```bash
# what is listening where
ss -ltnp | grep -E ':(80|443|8000|8080)'

# which container owns a port
docker ps --format '{{.Names}}\t{{.Ports}}'

# tail logs of the deploy receiver
tail -f /var/log/infra-hook.log

# disk usage of docker
docker system df

# check tailnet service advertisement
tailscale serve status
```
