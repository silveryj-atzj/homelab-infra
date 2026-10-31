#!/usr/bin/env python3
"""Gitea webhook + GitHub Actions 部署入口（仅内网，:9001）"""
import http.server
import json
import os
import subprocess

SECRET = "__SECRET__"
PORT = 9001
LOG = "/var/log/infra-hook.log"
INCOMING = "/tmp/incoming"
SCRIPTS = {
    "infra": "/opt/infra-hook/deploy.sh",
    "web-astro": "/opt/infra-hook/deploy-astro.sh",
    "web-wordpress": "/opt/infra-hook/deploy-wp.sh",
}
GH_SCRIPT = "/opt/infra-hook/deploy-github.sh"


def log(msg: str) -> None:
    with open(LOG, "a") as f:
        f.write(msg.rstrip() + "\n")


def run(script, *args):
    try:
        r = subprocess.run([script, *args], capture_output=True, text=True, timeout=1800)
        log(f"deploy rc={r.returncode}")
        log(r.stdout[-4000:])
        log(r.stderr[-2000:])
    except Exception as exc:  # noqa: BLE001
        log(f"deploy error: {exc}")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # ---- GitHub Actions 上传压缩包部署 ----
        if self.path.startswith(f"/sync/{SECRET}/"):
            name = self.path.rsplit("/", 1)[-1]
            if name not in ("astro", "wp"):
                self._json({"error": "bad target"}, 400)
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > 200 * 1024 * 1024:
                self._json({"error": "bad size"}, 413)
                return
            os.makedirs(INCOMING, exist_ok=True)
            dst = os.path.join(INCOMING, f"{name}.tar.gz")
            with open(dst, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            log(f"=== github sync target={name} bytes={length} ===")
            run(GH_SCRIPT, name)
            self._json({"ok": True, "target": name, "bytes": length})
            return

        # ---- Gitea webhook ----
        if self.path != f"/hook/{SECRET}":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        repo = ""
        try:
            repo = json.loads(body).get("repository", {}).get("name", "")
        except Exception:
            pass
        script = SCRIPTS.get(repo)
        log(f"=== hook repo={repo or '?'} ({len(body)} bytes) -> {script or 'IGNORED'} ===")
        if script:
            run(script)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    log("receiver starting")
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
