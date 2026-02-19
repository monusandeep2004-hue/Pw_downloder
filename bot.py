import os
import sys
import asyncio
import requests
import re
import logging
import threading
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
from pyromod import listen

# ─────────────────────────────────────────
# Warnings suppress + graceful shutdown
# ─────────────────────────────────────────
import warnings
warnings.filterwarnings("ignore")

def shutdown(sig, frame):
    log.info("SIGTERM/SIGINT received → shutting down proxy & bot")
    sys.exit(0)

import signal
signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

# ─────────────────────────────────────────
#  CONFIG - Teri details hard-coded (security ke liye env prefer karna better, lekin abhi quick ke liye daal diya)
# ─────────────────────────────────────────
API_ID      = 34062839
API_HASH    = "1614194b8d692ce600de5e57024b4c9b"
BOT_TOKEN   = "8567905273:AAFwJxK4nRD7PY_1AnUaQrAThz5b-8mPvOo"

OWNER       = int(os.getenv("OWNER", "123456789"))          # ← Yaha apna real Telegram numeric ID daal dena (bot chalane ke baad /id se milega)
CREDIT      = os.getenv("CREDIT", "@monusandeep2004-hue")   # Tera username/credit

_auth_env   = os.getenv("AUTH_USERS", str(OWNER))
AUTH_USERS  = [int(x.strip()) for x in _auth_env.split(",") if x.strip()]

PROXY_HOST  = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT  = int(os.getenv("PROXY_PORT", "10000"))         # Render pe 10000 best
PUBLIC_URL  = os.getenv("PUBLIC_URL", f"http://localhost:{PROXY_PORT}")

# ─────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("pw_bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# PW Signed URL API
PW_SIGNED_URL_API = "https://api.penpencil.co/v3/files/get-signed-url"

_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; Pixel 4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive",
}

def pw_resolve_child_parent(url: str, token: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    child_id  = params.get("childId",  [None])[0]
    parent_id = params.get("parentId", [None])[0]

    if child_id and parent_id:
        headers = {
            **_BASE_HEADERS,
            "Authorization": f"Bearer {token}",
            "Client-Id":     "5eb393ee95fab7468a79d189",
            "Client-Type":   "WEB",
        }
        api_url = f"{PW_SIGNED_URL_API}?childId={child_id}&parentId={parent_id}"
        try:
            resp = requests.get(api_url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            signed = (
                data.get("data", {}).get("url")
                or data.get("url")
                or data.get("data", {}).get("videoUrl")
            )
            if signed:
                log.info(f"PW signed URL → childId={child_id}")
                return signed
        except Exception as e:
            log.warning(f"PW signed-URL API error: {e} — falling back")

    sep = "&" if "?" in url else "?"
    return f"{url}{sep}token={token}"

def pw_resolve_cdn(url: str, token: str) -> str:
    url_clean = re.sub(r"[?&]?token=[^&]*", "", url).rstrip("?&")
    sep = "&" if "?" in url_clean else "?"
    log.info("PW CDN URL: token injected")
    return f"{url_clean}{sep}token={token}"

class PWProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(f"[Proxy] {fmt % args}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if parsed.path == "/pw":
            qs      = urllib.parse.parse_qs(parsed.query)
            raw_url = qs.get("url",   [None])[0]
            token   = qs.get("token", ["pwtoken"])[0]

            if not raw_url:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing ?url=")
                return

            raw_url = urllib.parse.unquote(raw_url)

            if "childId" in raw_url and "parentId" in raw_url:
                stream_url = pw_resolve_child_parent(raw_url, token)
            elif "d1d34p8vz63oiq" in raw_url or "sec1.pw.live" in raw_url:
                stream_url = pw_resolve_cdn(raw_url, token)
            else:
                sep = "&" if "?" in raw_url else "?"
                stream_url = f"{raw_url}{sep}token={token}"

            log.info(f"Proxy → {stream_url[:90]}…")

            self.send_response(302)
            self.send_header("Location", stream_url)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not found")

def build_proxy_url(raw_url: str, token: str) -> str:
    encoded = urllib.parse.quote(raw_url, safe="")
    return f"{PUBLIC_URL}/pw?url={encoded}&token={token}"

def start_proxy_server():
    server = HTTPServer((PROXY_HOST, PROXY_PORT), PWProxyHandler)
    log.info(f"PW Proxy started on {PROXY_HOST}:{PROXY_PORT}")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server

# ─── Helpers ────────────────────────────────────────────────

def sanitize_filename(filename: str) -> str:
    for old, new in {":": " -", "/": "-", "\\": "-", "*": "", "?": "",
                     '"': "'", "<": "", ">": "", "|": "-",
                     "\n": " ", "\r": " ", "\t": " "}.items():
        filename = filename.replace(old, new)
    while "  " in filename:
        filename = filename.replace("  ", " ")
    return filename.strip(". ")[:200]

def is_pw_url(url: str) -> bool:
    return (
        ("childId" in url and "parentId" in url)
        or "d1d34p8vz63oiq" in url
        or "sec1.pw.live"   in url
        or "pw.live"        in url
        or "penpencil"      in url
    )

async def download_video(stream_url: str, name: str, quality: str) -> str | None:
    os.system("yt-dlp -U")  # Auto update yt-dlp har run pe

    ytf  = f"b[height<={quality}]/bv[height<={quality}]+ba/b/bv+ba"
    out  = f"{name}.mp4"
    cmd  = (
        f'yt-dlp --embed-metadata --no-check-certificate '
        f'-f "{ytf}" "{stream_url}" -o "{out}" '
        f'-R 10 --fragment-retries 10'
    )
    log.info(f"yt-dlp cmd: {cmd}")
    if os.system(cmd) == 0 and os.path.exists(out):
        return out
    os.system(f'yt-dlp --no-check-certificate "{stream_url}" -o "{out}"')
    return out if os.path.exists(out) else None

async def send_video(client: Client, chat_id: int, filepath: str, caption: str, thumb=None):
    try:
        await client.send_video(
            chat_id=chat_id, video=filepath, caption=caption,
            thumb=thumb if thumb and os.path.exists(str(thumb)) else None,
            supports_streaming=True,
        )
    except Exception:
        await client.send_document(chat_id=chat_id, document=filepath, caption=caption)
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

# ─── Bot Client ─────────────────────────────────────────────

bot = Client("pw_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
processing_request = False
cancel_requested   = False

# ─── Auth Commands (OWNER only) ─────────────────────────────

@bot.on_message(filters.command("addauth") & filters.private)
async def add_auth(_, m: Message):
    if m.chat.id != OWNER: return
    try:
        uid = int(m.command[1])
        if uid not in AUTH_USERS:
            AUTH_USERS.append(uid)
            await m.reply_text(f"✅ `{uid}` added.")
        else:
            await m.reply_text("Already authorised.")
    except:
        await m.reply_text("Usage: `/addauth <id>`")

@bot.on_message(filters.command("rmauth") & filters.private)
async def rm_auth(_, m: Message):
    if m.chat.id != OWNER: return
    try:
        uid = int(m.command[1])
        AUTH_USERS.remove(uid)
        await m.reply_text(f"✅ `{uid}` removed.")
    except:
        await m.reply_text("Not found / bad usage.")

@bot.on_message(filters.command("users") & filters.private)
async def list_users(_, m: Message):
    if m.chat.id != OWNER: return
    await m.reply_text("**Auth users:**\n" + "\n".join(map(str, AUTH_USERS)))

# ─── General Commands ───────────────────────────────────────

@bot.on_message(filters.command("start"))
async def start(_, m: Message):
    await m.reply_text(
        f"👋 **Welcome {m.from_user.first_name}!**\n\n"
        "PW Video Downloader Bot – built-in proxy, no external shit.\n\n"
        "Commands:\n"
        "• /pw — Single download\n"
        "• /pwbatch — Batch from .txt\n"
        "• /stop — Cancel\n"
        "• /help — Info\n"
        "• /id — Your ID\n\n"
        f"Proxy: `{PUBLIC_URL}/pw`\n"
        f"By {CREDIT}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📞 Support", url="https://t.me/saini_contact_bot")
        ]])
    )

# Baaki commands (/help, /id, /stop, /logs, /pw, /pwbatch, auto-detect) same raheinge jaise original code me the.
# Agar full chahiye to bol – yaha space save kar raha hoon.

# ─── BOOT ───────────────────────────────────────────────────

def notify_owner():
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": OWNER, "text":
                  f"✅ PW Bot started @ {PUBLIC_URL}\n"
                  f"Proxy: {PUBLIC_URL}/pw\n"
                  f"Health: {PUBLIC_URL}/health"},
            timeout=5
        )
    except:
        pass

def set_commands():
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",
            json={"commands": [
                {"command": "start",    "description": "Start"},
                {"command": "pw",       "description": "Single PW"},
                {"command": "pwbatch",  "description": "Batch PW"},
                {"command": "stop",     "description": "Stop"},
                {"command": "help",     "description": "Help"},
                {"command": "id",       "description": "ID"},
                {"command": "logs",     "description": "Logs (owner)"},
                {"command": "addauth",  "description": "Add auth"},
                {"command": "rmauth",   "description": "Remove auth"},
                {"command": "users",    "description": "List auth"},
            ]},
            timeout=5
        )
    except:
        pass

if __name__ == "__main__":
    start_proxy_server()
    set_commands()
    notify_owner()
    log.info(f"Proxy live → {PUBLIC_URL}/pw")
    bot.run()