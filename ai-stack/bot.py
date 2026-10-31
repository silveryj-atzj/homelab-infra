"""Telegram 常驻 bot：走 SillyTavern（角色卡 + ST 模型配置），支持模型自己调用生图工具。

流程：Telegram 消息 -> 读 ST 角色卡构造 messages -> ST /api/backends/chat-completions/generate
     -> 若模型返回 tool_calls(generate_image) -> 调 cf-ai-proxy 生成图片 -> 发图 -> 把结果喂回模型继续
"""
import base64
import json
import logging
import os
import struct
import urllib.request
from pathlib import Path

import requests
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger("tg-st-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ST_BASE = os.getenv("ST_BASE", "http://sillytavern:8000").rstrip("/")
ST_HOST = os.getenv("ST_HOST", "ai-sillytavern.example.ts.net")
ST_MODEL = os.getenv("ST_MODEL", "bonsai-9b")
ST_REVERSE_PROXY = os.getenv("ST_REVERSE_PROXY", "http://llama-server:8080/v1")
IMAGE_API = os.getenv("IMAGE_API", "http://cf-ai-proxy:8080/v1/images/generations")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
CHAR_DIR = Path(os.getenv("CHAR_DIR", "/st-data/default-user/characters"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "700"))
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "${WEBHOOK_SECRET}")
MAX_HISTORY = 20
MAX_TOOL_ROUNDS = 2

DATA_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "根据文字描述生成一张图片并直接发给用户。只要用户想看图/画画/生成图像，就调用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "英文或中文的详细画面描述"},
                },
                "required": ["prompt"],
            },
        },
    }
]


# ---------------------------------------------------------------- 角色卡
def _png_text_chunks(raw: bytes):
    out = {}
    i = 8
    while i + 8 <= len(raw):
        length = struct.unpack(">I", raw[i : i + 4])[0]
        typ = raw[i + 4 : i + 8]
        data = raw[i + 8 : i + 8 + length]
        if typ == b"tEXt":
            key, _, val = data.partition(b"\x00")
            out[key.decode("latin1")] = val
        i += 12 + length
    return out


def _card_from_png(path: Path):
    try:
        chunks = _png_text_chunks(path.read_bytes())
        for key in ("ccv3", "chara"):
            if key in chunks:
                data = json.loads(base64.b64decode(chunks[key]))
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
                return data
    except Exception as exc:  # noqa: BLE001
        log.warning("parse card failed %s: %s", path, exc)
    return None


def _card_from_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("data", data) if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def load_characters():
    chars = []
    for p in sorted(CHAR_DIR.glob("*.png")):
        card = _card_from_png(p)
        if card and card.get("name"):
            chars.append({"file": p.stem, "card": card})
    for p in sorted(CHAR_DIR.glob("*.json")):
        card = _card_from_json(p)
        if card and card.get("name"):
            chars.append({"file": p.stem, "card": card})
    return chars


def build_system_prompt(card) -> str:
    parts = []
    if card.get("system_prompt"):
        parts.append(card["system_prompt"])
    parts.append(
        f"你正在扮演角色「{card.get('name', '助手')}」。请始终以该角色的身份、语气和设定回复，不要跳出角色。"
    )
    for label, key in (("角色描述", "description"), ("性格", "personality"), ("场景", "scenario")):
        if card.get(key):
            parts.append(f"{label}：{card[key]}")
    if card.get("mes_example"):
        parts.append(f"对话示例：\n{card['mes_example']}")
    parts.append(
        "当用户想看图片、要你画画、要你生成图像时，调用 generate_image 工具生成图片；"
        "生成后简短评论一句即可。不要声称自己无法生成图片。"
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 状态
def state_path(chat_id: int) -> Path:
    return DATA_DIR / f"chat_{chat_id}.json"


def load_state(chat_id: int):
    p = state_path(chat_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"char": None, "messages": []}


def save_state(chat_id: int, state):
    state_path(chat_id).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def get_char(name=None):
    chars = load_characters()
    if not chars:
        return None, []
    if not name:
        return chars[0], chars
    for c in chars:
        if c["file"] == name or c["card"].get("name") == name:
            return c, chars
    return chars[0], chars


# ---------------------------------------------------------------- ST 调用
class STClient:
    def __init__(self):
        self.session = requests.Session()
        self.token = None

    def _refresh_token(self):
        r = self.session.get(f"{ST_BASE}/csrf-token", headers={"Host": ST_HOST}, timeout=15)
        self.token = r.json().get("token", "")
        return self.token

    def generate(self, messages, tools=None):
        if not self.token:
            self._refresh_token()
        payload = {
            "chat_completion_source": "openai",
            "model": ST_MODEL,
            "reverse_proxy": ST_REVERSE_PROXY,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.9,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Host": ST_HOST, "Content-Type": "application/json"}
        if self.token:
            headers["X-CSRF-Token"] = self.token
        url = f"{ST_BASE}/api/backends/chat-completions/generate"
        r = self.session.post(url, json=payload, headers=headers, timeout=180)
        if r.status_code == 403:  # token 过期，刷新重试一次
            self._refresh_token()
            headers["X-CSRF-Token"] = self.token
            r = self.session.post(url, json=payload, headers=headers, timeout=180)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("choices"):
            return data["choices"][0]["message"]
        return {"role": "assistant", "content": data.get("content", "") if isinstance(data, dict) else str(data)}


st = STClient()


def generate_image(prompt: str) -> bytes:
    body = json.dumps({"prompt": prompt, "model": IMAGE_MODEL}).encode()
    req = urllib.request.Request(
        IMAGE_API, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return base64.b64decode(data["data"][0]["b64_json"])


# ---------------------------------------------------------------- 命令
HELP = (
    "可用命令：\n"
    "/char 当前角色\n"
    "/chars 角色列表\n"
    "/switch <序号或名字> 切换角色\n"
    "/reset 清空对话\n"
    "/help 帮助\n\n"
    "直接发消息即可聊天；想要图就直接说「画一只猫」之类，模型会调用生图工具。"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已连接 SillyTavern 角色扮演后端。\n" + HELP)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def cmd_chars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chars = load_characters()
    if not chars:
        await update.message.reply_text("没找到角色卡。")
        return
    lines = [f"{i + 1}. {c['card'].get('name', c['file'])}" for i, c in enumerate(chars)]
    await update.message.reply_text("角色列表：\n" + "\n".join(lines) + "\n\n用 /switch 序号 切换")


async def cmd_char(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state = load_state(update.effective_chat.id)
    char, _ = get_char(state.get("char"))
    if not char:
        await update.message.reply_text("没找到角色卡。")
        return
    await update.message.reply_text(f"当前角色：{char['card'].get('name')}")


async def cmd_switch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    arg = " ".join(ctx.args).strip()
    chars = load_characters()
    if not arg or not chars:
        await update.message.reply_text("用法：/switch <序号或名字>")
        return
    chosen = None
    if arg.isdigit() and 1 <= int(arg) <= len(chars):
        chosen = chars[int(arg) - 1]
    else:
        for c in chars:
            if arg in (c["file"], c["card"].get("name")):
                chosen = c
                break
    if not chosen:
        await update.message.reply_text("没找到这个角色，先看 /chars")
        return
    state = load_state(update.effective_chat.id)
    state["char"] = chosen["file"]
    state["messages"] = []
    save_state(update.effective_chat.id, state)
    await update.message.reply_text(f"已切换到「{chosen['card'].get('name')}」，对话已清空。")


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state = load_state(update.effective_chat.id)
    state["messages"] = []
    save_state(update.effective_chat.id, state)
    await update.message.reply_text("对话已清空。")


# ---------------------------------------------------------------- 聊天
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    if not text:
        return

    # 群聊：只响应 @机器人 或回复机器人的消息；私聊：直接响应
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        me = await ctx.bot.get_me()
        username = (me.username or "").lower()
        entities = update.message.entities or []
        mentioned = False
        for ent in entities:
            if ent.type == "mention":
                frag = text[ent.offset : ent.offset + ent.length].lower()
                if username and frag == f"@{username}":
                    mentioned = True
                    break
            elif ent.type == "text_mention" and getattr(ent, "user", None) and ent.user.id == me.id:
                mentioned = True
                break
        if not mentioned and username and f"@{username}" in text.lower():
            mentioned = True
        replied = False
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            replied = update.message.reply_to_message.from_user.id == me.id
        if not (mentioned or replied):
            return
        if username:
            text = re.sub(rf"@{re.escape(username)}\b\s*", "", text, flags=re.IGNORECASE).strip()
        if not text:
            return
    state = load_state(chat_id)
    char, _ = get_char(state.get("char"))
    if not char:
        await update.message.reply_text("没找到角色卡，无法开始。")
        return
    if not state.get("char"):
        state["char"] = char["file"]

    history = state.get("messages", [])
    history.append({"role": "user", "content": text})

    await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)

    system_prompt = build_system_prompt(char["card"])
    final_text = ""
    try:
        for _round in range(MAX_TOOL_ROUNDS + 1):
            messages = [{"role": "system", "content": system_prompt}] + history[-MAX_HISTORY:]
            msg = st.generate(messages, tools=IMAGE_TOOLS)
            content = (msg.get("content") or "").strip()
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                final_text = content or "（模型没有返回内容）"
                history.append({"role": "assistant", "content": final_text})
                break

            history.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function", {})
                if fn.get("name") != "generate_image":
                    history.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": "unsupported tool"})
                    continue
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:  # noqa: BLE001
                    args = {}
                prompt = args.get("prompt") or text
                await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
                try:
                    image = generate_image(prompt)
                    await update.message.reply_photo(photo=image, caption=f"🎨 {prompt[:200]}")
                    history.append(
                        {"role": "tool", "tool_call_id": call.get("id", ""), "content": "图片已生成并发送给用户。"}
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("image gen failed: %s", exc)
                    await update.message.reply_text(f"生图失败：{exc}")
                    history.append(
                        {"role": "tool", "tool_call_id": call.get("id", ""), "content": f"image generation failed: {exc}"}
                    )
            final_text = content
        else:
            final_text = final_text or "（工具调用次数超限）"
    except Exception as exc:  # noqa: BLE001
        log.exception("chat failed")
        await update.message.reply_text(f"出错了：{exc}")
        return

    if final_text:
        await update.message.reply_text(final_text)

    state["messages"] = history[-MAX_HISTORY * 2 :]
    state["char"] = char["file"]
    save_state(chat_id, state)


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("chars", cmd_chars))
    app.add_handler(CommandHandler("char", cmd_char))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    if WEBHOOK_BASE:
        url = f"{WEBHOOK_BASE}/tg/{WEBHOOK_SECRET}"
        log.info("starting webhook on port %s, url %s", WEBHOOK_PORT, url)
        app.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path=f"/tg/{WEBHOOK_SECRET}",
            webhook_url=url,
            secret_token=WEBHOOK_SECRET,
        )
    else:
        log.info("starting polling")
        app.run_polling()


if __name__ == "__main__":
    main()
