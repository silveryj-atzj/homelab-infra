#!/usr/bin/env python3
"""Cloudflare Workers AI -> OpenAI 兼容代理
给 Open WebUI 提供：
  GET  /v1/models                 模型列表（文本 + 图片）
  POST /v1/chat/completions       转发到 Workers AI OpenAI 兼容端点（支持流式）
  POST /v1/images/generations     flux 文生图，返回 b64_json
"""
import json
import os
import urllib.request
import urllib.error
import http.server

ACCOUNT = os.getenv("CF_ACCOUNT_ID", "")
TOKEN = os.getenv("CF_API_TOKEN", "")
CF_BASE = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}"
IMAGE_MODEL = os.getenv("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
PORT = int(os.getenv("PORT", "8080"))

TEXT_MODELS = [
    "@cf/openai/gpt-oss-120b",
    "@cf/meta/llama-3.1-8b-instruct-fp8",
    "@cf/meta/llama-3.2-3b-instruct",
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    "@cf/zai-org/glm-5.3-flash",
    "@cf/mistral/mistral-7b-instruct-v0.2-lora",
]
IMAGE_MODELS = [
    "@cf/black-forest-labs/flux-1-schnell",
    "@cf/black-forest-labs/flux-2-klein-4b",
]

UI_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CF Workers AI 图片生成</title>
<style>
  body{background:#0b0d10;color:#e6edf3;font-family:system-ui,-apple-system,"Segoe UI","Noto Sans SC",sans-serif;max-width:760px;margin:0 auto;padding:32px 20px}
  h1{font-size:20px;margin:0 0 6px}
  p.sub{color:#8b98a5;font-size:14px;margin:0 0 24px}
  textarea,input,select{width:100%;background:#14181d;border:1px solid #232a32;color:#e6edf3;border-radius:10px;padding:12px 14px;font:inherit;outline:none;box-sizing:border-box;margin-bottom:12px}
  textarea:focus,input:focus,select:focus{border-color:#6ea8fe}
  button{background:#6ea8fe;color:#08111c;border:0;border-radius:10px;padding:12px 20px;font-weight:600;cursor:pointer}
  button:disabled{opacity:.5;cursor:progress}
  #out{margin-top:24px;display:flex;flex-direction:column;gap:16px}
  #out img{width:100%;border-radius:12px;border:1px solid #232a32}
  .err{color:#ff8b8b;font-size:14px;white-space:pre-wrap}
</style></head><body>
<h1>Cloudflare Workers AI · 文生图</h1>
<p class="sub">模型：flux-1-schnell / flux-2-klein-4b（走 Cloudflare 免费额度）</p>
<textarea id="prompt" rows="3" placeholder="描述你想生成的画面，例如：一只橘猫坐在窗台上，水彩风格"></textarea>
<select id="model"></select>
<button id="go">生成图片</button>
<div id="out"></div>
<script>
const MODELS = ["@cf/black-forest-labs/flux-1-schnell", "@cf/black-forest-labs/flux-2-klein-4b"];
const sel = document.getElementById('model');
MODELS.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; sel.appendChild(o); });
const out = document.getElementById('out');
const go = document.getElementById('go');
go.onclick = async () => {
  const prompt = document.getElementById('prompt').value.trim();
  if (!prompt) return;
  go.disabled = true; out.innerHTML = '<p class="sub">生成中…（约 3-8 秒）</p>';
  try {
    const r = await fetch('/v1/images/generations', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt, model: sel.value})});
    const j = await r.json();
    if (!j.data) throw new Error(JSON.stringify(j));
    out.innerHTML = '';
    for (const item of j.data) {
      const img = document.createElement('img');
      img.src = 'data:image/png;base64,' + item.b64_json;
      out.appendChild(img);
    }
  } catch (e) { out.innerHTML = '<p class="err">失败：' + e.message + '</p>'; }
  go.disabled = false;
};
</script></body></html>"""



def cf_request(path, payload=None, method="POST"):
    url = f"{CF_BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {TOKEN}",
                                          "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=180)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip("/")
        if path in ("/health", ""):
            return self._send_json({"status": "ok"})
        if path == "/ui":
            return self._send_html(UI_HTML)
        if path == "/v1/models":
            models = [{"id": m, "object": "model", "created": 0, "owned_by": "cloudflare"} for m in TEXT_MODELS + IMAGE_MODELS]
            return self._send_json({"object": "list", "data": models})
        return self._send_json({"error": "not found"}, 404)

    def _send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._send_json({"error": "bad json"}, 400)

        path = self.path.rstrip("/")
        if path == "/v1/chat/completions":
            return self._chat(body)
        if path == "/v1/images/generations":
            return self._image(body)
        return self._send_json({"error": "not found"}, 404)

    # ---------------- chat ----------------
    def _chat(self, body):
        if not body.get("model"):
            body["model"] = TEXT_MODELS[0]
        try:
            resp = cf_request("/ai/v1/chat/completions", body)
        except urllib.error.HTTPError as e:
            return self._send_json({"error": e.read().decode(errors="ignore")}, e.code)
        except Exception as e:  # noqa: BLE001
            return self._send_json({"error": str(e)}, 500)

        ctype = resp.headers.get("Content-Type", "application/json")
        if "text/event-stream" in ctype:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            return

        data = resp.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------------- image ----------------
    def _image(self, body):
        prompt = body.get("prompt", "")
        model = body.get("model") or IMAGE_MODEL
        if model not in IMAGE_MODELS:
            model = IMAGE_MODEL
        try:
            resp = cf_request(f"/ai/run/{model}", {"prompt": prompt})
            data = resp.read()
        except urllib.error.HTTPError as e:
            return self._send_json({"error": e.read().decode(errors="ignore")}, e.code)
        except Exception as e:  # noqa: BLE001
            return self._send_json({"error": str(e)}, 500)
        import base64
        b64 = base64.b64encode(data).decode()
        return self._send_json({"created": int(__import__("time").time()),
                                "data": [{"b64_json": b64, "revised_prompt": prompt}]})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"cf-ai-proxy listening on {PORT}", flush=True)
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
