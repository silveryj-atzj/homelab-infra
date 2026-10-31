# Decisions (ADR)

Short records of the choices that shape this setup.

---

## ADR-1: Access internal services via Tailscale instead of exposed ports

**Context** — Dozens of services need remote access. Opening ports means firewall rules, remembering port numbers, and a wider attack surface.

**Decision** — Every node joins a tailnet. Internal access uses MagicDNS names and Tailscale Services (VIP, always 443). `tailscale serve` proxies to the local container port.

**Consequences**
- No ports to remember, no inbound firewall rules.
- Services are unreachable from the public internet by construction.
- Requires the Tailscale client on each device (acceptable for personal use).

---

## ADR-2: Public entry via Cloudflare Tunnel, not port forwarding

**Context** — Two websites and one webhook path must be publicly reachable.

**Decision** — `cloudflared` dials out to Cloudflare; the origin has no inbound exposure. Caddy routes by `Host` header.

**Consequences**
- No open ports on the router; TLS handled at the edge.
- Must forward the original scheme correctly: the origin sees plain HTTP, so Caddy sets `X-Forwarded-Proto: https` explicitly (otherwise WordPress redirect loops, see runbook).
- Only explicitly routed hostnames work; everything else returns 404.

---

## ADR-3: Gitea + webhook receiver as the deployment mechanism

**Context** — Configuration lives in git, but the server needs to apply it. A full CI system would be heavier than needed.

**Decision** — A small systemd service (`infra-hook`) listens on an internal port with a secret path. On push it pulls the repo and copies only the relevant files into the target LXC, then reloads that service.

**Consequences**
- Very small footprint, no scheduler or agents.
- Deploys are fast (seconds) and scoped to one service.
- Must handle idempotency carefully (`pct push` does not delete files; scripts are written accordingly).
- The receiver is internal-only and protected by a secret path.

---

## ADR-4: GitHub Actions self-hosted runners for GitHub-hosted repos

**Context** — Some repos (portfolio/WordPress source) also live on GitHub. GitHub's hosted runners cannot reach the internal network.

**Decision** — Register self-hosted runners on the server side. A workflow packages the repo and POSTs it to the internal receiver, which then deploys.

**Consequences**
- GitHub push → deployed without exposing anything.
- Registration tokens are one-time and short-lived, so the runner config must survive restarts (`DISABLE_AUTOMATIC_DEREGISTRATION=true`), and an external check re-registers when the runner is offline.

---

## ADR-5: Static Astro for the portfolio, WordPress for the community side

**Context** — Two very different needs: a fast, controlled portfolio and a place for informal posts/forum.

**Decision**
- Astro (static, git-driven) for the portfolio and technical writing.
- WordPress (dynamic, forum plugin) for casual posts, discussion and experiments.

**Consequences**
- Portfolio stays fast and cheap to host; content is Markdown in git.
- WordPress is where interactive features live; it is the only dynamic app exposed publicly.
- Two visual systems to keep roughly in sync.

---

## ADR-6: One shared embedding service for all AI frontends

**Context** — Open WebUI and SillyTavern both need embeddings for RAG. Running embeddings per frontend wastes RAM and duplicates indexes.

**Decision** — Run bge-m3 once as an OpenAI-compatible embeddings endpoint; every frontend points at it.

**Consequences**
- Consistent vectors and one model to update.
- Frontends must be configured with the same endpoint; the service becomes a shared dependency (acceptable, it is small and CPU-only).

---

## ADR-7: Local llama.cpp for routine inference, Workers AI as overflow

**Context** — The GPU is small (4 GB). A quantized 9B model fits, but is not always enough for heavier tasks.

**Decision** — llama.cpp on the GPU for daily use; a local proxy exposes Cloudflare Workers AI through the same OpenAI-compatible interface for overflow and image generation.

**Consequences**
- Predictable latency and no per-token cost for common cases.
- A second path for fallback; both speak the same protocol, so clients only switch the model name.
- Free-tier limits of Workers AI must be respected for long generations.

---

## ADR-8: Secrets only in environment/host files, never in git

**Context** — This repository is meant to be publishable.

**Decision** — Committed files contain placeholders only. Real values live in `.env` files or host-side files with restrictive permissions; this public repository is generated from the private one with a sanitizer.

**Consequences**
- Safe to open source; reviewers can see the real structure.
- Extra step when adding new secrets: template + sanitizer rule.
