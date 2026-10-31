# Architecture

## Layers

1. **Edge** — Cloudflare terminates TLS, routes by hostname, provides Access (identity-aware proxy) and Workers AI.
2. **Tunnel** — `cloudflared` runs inside the gateway LXC and dials out to Cloudflare. No inbound ports are opened on the router.
3. **Reverse proxy** — Caddy routes by `Host` header to the right backend and injects `X-Forwarded-*` headers.
4. **Tailnet** — every node joins Tailscale. Internal access uses MagicDNS names and Tailscale Services VIPs (always port 443), so services are never exposed by port numbers.
5. **Workloads** — Docker containers inside LXC containers. Each LXC owns one concern (web, ai, dashboard, storage, gateway).
6. **Delivery** — Gitea is the source of truth; webhooks drive deployments. GitHub Actions runners cover repos that live on GitHub.

## Request flows

### Public website
```
browser → Cloudflare (TLS) → cloudflared → Caddy:80 → nginx/PHP (WordPress)
```

### Blog (static)
```
browser → Cloudflare → cloudflared → Caddy:80 → file_server (/srv/astro)
```

### Internal service (no ports)
```
device with Tailscale → MagicDNS name or Service VIP → tailscale serve → container port
```

### Deployment
```
git push → Gitea webhook → infra-hook :9001 → git pull → pct push → reload/restart
```

### GitHub-hosted repo
```
git push → GitHub Actions (self-hosted runner) → tar + POST → infra-hook → build/deploy
```

### AI request
```
client (Open WebUI / SillyTavern / Telegram bot)
  → OpenAI-compatible endpoint
  → llama.cpp server (GPU) or cf-ai-proxy → Workers AI
  → embeddings via bge-m3 service for RAG
```

## Container inventory

| LXC | Container | Purpose |
|---|---|---|
| gateway | caddy | public entry, routing |
| gateway | cloudflared | tunnel client |
| gateway | portainer | docker UI |
| web | nginx | HTTP server |
| web | php-fpm | WordPress runtime |
| web | mariadb | database |
| web | redis | object cache |
| web | phpmyadmin | DB admin |
| web | gitea | git hosting |
| web | portainer | docker UI |
| homepage | homepage | dashboard |
| homepage | portainer | docker UI |
| ai | llama-server | 9B model inference |
| ai | llama-embed | bge-m3 embeddings |
| ai | open-webui | chat / RAG frontend |
| ai | sillytavern | roleplay frontend |
| ai | searxng | metasearch |
| ai | cf-ai-proxy | Workers AI gateway |
| ai | telegram-bot | chat bot bridge |
| ai | portainer | docker UI |

## Address map (example placeholders)

| Service | Internal address |
|---|---|
| Caddy | `http://10.0.0.20:80` |
| Gitea | `http://10.0.0.30:3001` |
| phpMyAdmin | `http://10.0.0.30:8080` |
| llama.cpp | `http://10.0.0.40:8080` |
| embeddings | `http://10.0.0.40:8090` |
| Open WebUI | `http://10.0.0.40:3000` |
| SillyTavern | `http://10.0.0.40:8001` |
| SearXNG | `http://10.0.0.40:8888` |
| Homepage | `http://10.0.0.31:3000` |
| Samba | `smb://10.0.0.21` |
