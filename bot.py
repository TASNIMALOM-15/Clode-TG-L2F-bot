import asyncio
import html
import logging
import mimetypes
import os
import re
import time
from urllib.parse import quote

# --- Python 3.10+ / 3.14 compatibility -------------------------------------
# Pyrogram's sync helper calls asyncio.get_event_loop() at import time.
# On newer Python versions this raises RuntimeError if no loop exists.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from pyrogram import Client, filters, idle  # noqa: E402
from pyrogram.handlers import MessageHandler  # noqa: E402
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("stream-bot")
logging.getLogger("pyrogram").setLevel(logging.WARNING)


# --- Configuration ----------------------------------------------------------
def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def format_base_url(raw: str, port: int) -> str:
    """Normalize SERVER_URL / FQDN: strip protocol, path, slashes; enforce https."""
    url = (raw or "").strip().strip('"').strip("'")
    if not url:
        return f"http://localhost:{port}"
    url = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url)
    url = url.split("/")[0].strip()
    if not url:
        return f"http://localhost:{port}"
    if url.startswith(("localhost", "127.0.0.1", "0.0.0.0")):
        return f"http://{url}"
    return f"https://{url}"


def normalize_channel_id(raw: str) -> int:
    value = int(raw.strip())
    if value > 0:  # user forgot the -100 prefix
        value = int(f"-100{value}")
    return value


API_ID = int(require_env("API_ID"))
API_HASH = require_env("API_HASH")
BOT_TOKEN = require_env("BOT_TOKEN")
BIN_CHANNEL = normalize_channel_id(require_env("BIN_CHANNEL"))
PORT = int(os.environ.get("PORT", "8080"))
BASE_URL = format_base_url(
    os.environ.get("SERVER_URL")
    or os.environ.get("FQDN")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or "",
    PORT,
)
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", "300"))
ALLOWED_USERS = {
    int(x) for x in os.environ.get("ALLOWED_USERS", "").replace(" ", "").split(",") if x
}

CHUNK_SIZE = 1024 * 1024  # Pyrogram stream_media yields 1 MiB chunks
CACHE_TTL = 600
CACHE_MAX = 500

tg: Client = None  # created inside main() so it binds to the running loop
_cache: dict = {}


# --- Helpers ----------------------------------------------------------------
def human_size(size: int) -> str:
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def get_media(message):
    return message.video or message.document or message.audio


def build_info(msg) -> dict | None:
    media = get_media(msg)
    if not media:
        return None
    mime = getattr(media, "mime_type", None) or "application/octet-stream"
    name = getattr(media, "file_name", None)
    if not name:
        ext = mimetypes.guess_extension(mime) or ""
        name = f"file_{msg.id}{ext}"
    return {
        "ts": time.time(),
        "message": msg,
        "name": name,
        "mime": mime,
        "size": int(getattr(media, "file_size", 0) or 0),
        "hash": media.file_unique_id[:6],
    }


async def get_file_info(msg_id: int) -> dict | None:
    now = time.time()
    cached = _cache.get(msg_id)
    if cached and now - cached["ts"] < CACHE_TTL:
        return cached
    try:
        msg = await tg.get_messages(BIN_CHANNEL, msg_id)
    except Exception as exc:
        log.warning("get_messages failed for %s: %s", msg_id, exc)
        return None
    if not msg or msg.empty:
        return None
    info = build_info(msg)
    if not info:
        return None
    if len(_cache) >= CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1]["ts"])[: CACHE_MAX // 5]
        for key, _ in oldest:
            _cache.pop(key, None)
    _cache[msg_id] = info
    return info


# --- Telegram handlers ------------------------------------------------------
async def start_handler(client: Client, message):
    await message.reply_text(
        "👋 হ্যালো!\n\n"
        "আমাকে যেকোনো Video, Document বা Audio ফাইল পাঠান। "
        "আমি আপনাকে Direct Download ও Watch Online লিংক দেব।"
    )


async def file_handler(client: Client, message):
    user = message.from_user
    if ALLOWED_USERS and (not user or user.id not in ALLOWED_USERS):
        await message.reply_text("⛔ আপনি এই বট ব্যবহার করার অনুমতি পাননি।")
        return

    status = await message.reply_text("⏳ ফাইল প্রসেস হচ্ছে...", quote=True)
    try:
        log_msg = await message.copy(BIN_CHANNEL)
    except Exception as exc:
        log.exception("Copy to BIN_CHANNEL failed")
        await status.edit_text(
            "❌ ফাইল BIN_CHANNEL এ পাঠানো যায়নি।\n"
            "বটকে চ্যানেলের Admin বানিয়েছেন কি না এবং BIN_CHANNEL ঠিক আছে কি না দেখুন।\n"
            f"Error: {exc}"
        )
        return

    info = build_info(log_msg)
    if not info:
        await status.edit_text("❌ এই ফাইল টাইপ সাপোর্ট করা হয় না।")
        return
    _cache[log_msg.id] = info

    try:
        who = f"{user.first_name} (ID: {user.id})" if user else "Unknown"
        await log_msg.reply_text(f"👤 Requested by: {who}", quote=True)
    except Exception:
        pass

    file_hash = info["hash"]
    dl_link = f"{BASE_URL}/dl/{log_msg.id}/{file_hash}"
    watch_link = f"{BASE_URL}/watch/{log_msg.id}/{file_hash}"

    text = (
        f"📁 **{info['name']}**\n"
        f"📦 Size: `{human_size(info['size'])}`\n"
        f"🎞 Type: `{info['mime']}`"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬇️ Direct Download", url=dl_link),
                InlineKeyboardButton("▶️ Watch Online", url=watch_link),
            ]
        ]
    )
    try:
        await status.edit_text(text, reply_markup=keyboard)
    except Exception:
        # Telegram rejects non-public URLs (e.g. localhost) as buttons.
        await status.edit_text(
            f"{text}\n\n⬇️ Download: {dl_link}\n▶️ Watch: {watch_link}",
            disable_web_page_preview=True,
        )


# --- Web server -------------------------------------------------------------
PLAYER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/plyr@3.7.8/dist/plyr.css">
<style>
  :root { --bg:#0f1115; --card:#181b22; --text:#e8eaf0; --accent:#4f8cff; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  .wrap { max-width:960px; margin:0 auto; padding:16px; }
  h1 { font-size:1.05rem; word-break:break-word; margin:8px 0 14px; }
  .card { background:var(--card); border-radius:12px; padding:12px; }
  video, audio { width:100%; border-radius:8px; background:#000; }
  .meta { opacity:.7; font-size:.85rem; margin:10px 0; }
  .btn { display:inline-block; margin-top:10px; padding:11px 18px; background:var(--accent);
         color:#fff; text-decoration:none; border-radius:8px; font-weight:600; }
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <div class="card">
    __PLAYER__
    <div class="meta">Size: __SIZE__</div>
    <a class="btn" href="__DL__">⬇️ Download</a>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/plyr@3.7.8/dist/plyr.polyfilled.min.js"></script>
<script>
  var el = document.getElementById('player');
  if (el) {
    // [পরিবর্তন ১]: "new Plyr" এর আগে "var player =" যুক্ত করা হয়েছে
    var player = new Plyr(el, {
      controls: ['play-large','play','progress','current-time','duration','mute','volume','settings','pip','fullscreen'],
      settings: ['speed'],
      
      // [পরিবর্তন ২]: selected-কে 3 করা হয়েছে এবং options-এ 2.5, 3, 3.5, 4 যোগ করা হয়েছে
      speed: { selected: 3, options: [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4] },
      
      keyboard: { focused: true, global: true }
    });
    
    // [পরিবর্তন ৩]: ডিফল্ট ৩x স্পিড নিশ্চিত করতে নিচের ৪টি নতুন লাইন যোগ করা হয়েছে
    player.on('ready', function() {
      player.speed = 3;
    });
  }
</script>
</body>
</html>
"""


async def index_handler(request: web.Request) -> web.Response:
    return web.Response(text="✅ Bot is running.", content_type="text/plain")


async def watch_handler(request: web.Request) -> web.Response:
    try:
        msg_id = int(request.match_info["msg_id"])
    except ValueError:
        raise web.HTTPNotFound()
    file_hash = request.match_info["hash"]
    info = await get_file_info(msg_id)
    if not info or info["hash"] != file_hash:
        raise web.HTTPNotFound(text="File not found")

    stream_url = f"/stream/{msg_id}/{file_hash}"
    dl_url = f"/dl/{msg_id}/{file_hash}"
    mime = info["mime"]

    if mime.startswith("video/"):
        player = f'<video id="player" controls playsinline preload="metadata"><source src="{stream_url}"></video>'
    elif mime.startswith("audio/"):
        player = f'<audio id="player" controls preload="metadata"><source src="{stream_url}" type="{html.escape(mime)}"></audio>'
    else:
        player = "<p>এই ফাইল ব্রাউজারে প্লে করা যাবে না। নিচের বাটন থেকে ডাউনলোড করুন।</p>"

    page = (
        PLAYER_HTML.replace("__TITLE__", html.escape(info["name"]))
        .replace("__PLAYER__", player)
        .replace("__SIZE__", human_size(info["size"]))
        .replace("__DL__", dl_url)
    )
    return web.Response(text=page, content_type="text/html")


def parse_range(header: str | None, size: int):
    """Return (start, end) inclusive, or None if invalid."""
    if not header:
        return 0, size - 1
    match = re.match(r"^bytes=(\d*)-(\d*)$", header.strip())
    if not match:
        return 0, size - 1
    start_s, end_s = match.groups()
    if start_s == "" and end_s == "":
        return 0, size - 1
    if start_s == "":  # suffix range: last N bytes
        length = int(end_s)
        start = max(size - length, 0)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end


async def stream_file(request: web.Request, download: bool) -> web.StreamResponse:
    try:
        msg_id = int(request.match_info["msg_id"])
    except ValueError:
        raise web.HTTPNotFound()
    file_hash = request.match_info["hash"]
    info = await get_file_info(msg_id)
    if not info or info["hash"] != file_hash:
        raise web.HTTPNotFound(text="File not found")

    size = info["size"]
    if size <= 0:
        raise web.HTTPNotFound(text="Empty file")

    range_header = request.headers.get("Range")
    rng = parse_range(range_header, size)
    if rng is None:
        return web.Response(
            status=416, headers={"Content-Range": f"bytes */{size}"}
        )
    start, end = rng
    length = end - start + 1

    disposition = "attachment" if download else "inline"
    headers = {
        "Content-Type": info["mime"] if not download else "application/octet-stream",
        "Content-Length": str(length),
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(info['name'])}",
        "Cache-Control": "no-store",
    }
    status = 200
    if range_header:
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    response = web.StreamResponse(status=status, headers=headers)
    await response.prepare(request)
    if request.method == "HEAD":
        return response

    first_chunk = start // CHUNK_SIZE
    cut = start % CHUNK_SIZE
    remaining = length
    generator = tg.stream_media(info["message"], offset=first_chunk)
    try:
        async for chunk in generator:
            if cut:
                chunk = chunk[cut:]
                cut = 0
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            await response.write(chunk)
            remaining -= len(chunk)
            if remaining <= 0:
                break
    except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.warning("Streaming error for %s: %s", msg_id, exc)
    finally:
        try:
            await generator.aclose()
        except Exception:
            pass
    try:
        await response.write_eof()
    except Exception:
        pass
    return response


async def stream_handler(request: web.Request):
    return await stream_file(request, download=False)


async def download_handler(request: web.Request):
    return await stream_file(request, download=True)


def create_web_app() -> web.Application:
    app = web.Application(client_max_size=1024**2)
    app.router.add_get("/", index_handler)
    app.router.add_get("/watch/{msg_id}/{hash}", watch_handler)
    app.router.add_get("/stream/{msg_id}/{hash}", stream_handler)
    app.router.add_get("/dl/{msg_id}/{hash}", download_handler)
    return app


async def keep_alive():
    """Ping our own public URL so Render's free instance sleeps less often."""
    if BASE_URL.startswith("http://localhost"):
        return
    await asyncio.sleep(60)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(BASE_URL + "/", timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    log.info("Keep-alive ping: %s", resp.status)
            except Exception as exc:
                log.warning("Keep-alive failed: %s", exc)
            await asyncio.sleep(PING_INTERVAL)


# --- Runner -----------------------------------------------------------------
async def main():
    global tg

    # 1) Start web server first so Render detects the open port quickly.
    runner = web.AppRunner(create_web_app())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web server listening on 0.0.0.0:%s", PORT)
    log.info("Public base URL: %s", BASE_URL)

    # 2) Start Pyrogram bot inside the running loop.
    tg = Client(
        "stream_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
        workers=8,
    )
    tg.add_handler(MessageHandler(start_handler, filters.command("start") & filters.private))
    tg.add_handler(
        MessageHandler(
            file_handler,
            filters.private & (filters.video | filters.document | filters.audio),
        )
    )
    await tg.start()
    me = await tg.get_me()
    log.info("Bot started as @%s", me.username)

    # Verify access to the log channel early with a clear error.
    try:
        await tg.get_chat(BIN_CHANNEL)
    except Exception as exc:
        log.error("Cannot access BIN_CHANNEL (%s): %s", BIN_CHANNEL, exc)

    keepalive_task = asyncio.create_task(keep_alive())
    try:
        await idle()
    finally:
        keepalive_task.cancel()
        await tg.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
