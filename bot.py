import os
import time
import asyncio
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from hydrogram import Client, filters, idle
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- CONFIGURATION ---
API_ID        = int(os.environ.get("API_ID"))
API_HASH      = os.environ.get("API_HASH")
BOT_TOKEN     = os.environ.get("BOT_TOKEN")
DB_URL        = os.environ.get("DB_URL")
ADMIN         = int(os.environ.get("ADMIN"))
DEVELOPER_USR = os.environ.get("DEVELOPER_USR", "Unknown")
PORT          = int(os.environ.get("PORT", 8080))

# --- DATABASE SETUP ---
db_client     = AsyncIOMotorClient(DB_URL)
db            = db_client["SecureRenamePro_V3"]
user_data     = db["users"]
settings_data = db["settings"]

# NOTE: app is intentionally NOT created here.
# Creating hydrogram.Client at module level crashes on Python 3.10+ with:
#   RuntimeError: There is no current event loop in thread 'MainThread'
# It is created inside main() where the event loop is already running.

# ─────────────────────────────────────────────
# In-memory session store for /vth flow
# { user_id: {"videos": [paths...], "awaiting": "videos"} }
# ─────────────────────────────────────────────
vth_sessions: dict = {}

# --- WEB SERVER (keeps Render instance alive) ---
async def handle(request):
    return web.Response(text="Bot is Secure & Running! 🛡️")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", handle)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✅ Web Server started on port {PORT}")

# --- UTILS ---
async def is_bot_public():
    doc = await settings_data.find_one({"_id": "config"})
    return doc.get("public", False) if doc else False

def get_human_size(size_bytes: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"

# Module-level dict so progress_bar can track last edit time without instance state
_last_edit: dict = {}

async def progress_bar(current: int, total: int, status_msg, start_time: float):
    """Rate-limited progress updater — fires at most once every 5 seconds."""
    now  = time.time()
    diff = now - start_time
    if diff < 1:
        return
    msg_id = status_msg.id
    if (now - _last_edit.get(msg_id, 0)) < 5 and current != total:
        return
    _last_edit[msg_id] = now

    percentage = current * 100 / total
    speed      = current / diff if diff > 0 else 0
    eta        = (total - current) / speed if speed > 0 else 0
    filled     = int(percentage / 10)
    bar        = f"[{'■' * filled}{'□' * (10 - filled)}] {percentage:.1f}%"
    info = (
        f"\n\n🚀 **Speed:** {get_human_size(speed)}/s"
        f"\n📦 **Done:** {get_human_size(current)} of {get_human_size(total)}"
        f"\n⏳ **ETA:** {round(eta)}s"
    )
    try:
        await status_msg.edit(f"✨ **Processing...**\n\n{bar}{info}")
    except Exception:
        pass

# --- UI TEXT ---
START_TEXT = (
    "✨ **Welcome to Pro Rename Bot v3.1** ✨\n\n"
    "Hello **{name}**, I am a premium, high-speed file renamer.\n\n"
    "🛡️ **Current Security:** `{mode}`\n"
    "⚡ **Server Status:** `Online & High Speed`\n"
)

DETAILED_HELP = (
    "🚀 **USER GUIDE**\n━━━━━━━━━━━━━━━━━━\n\n"
    "**1️⃣ RENAME**\n"
    "Reply to any file with `/rename NewName.ext`\n\n"
    "**2️⃣ THUMBNAIL**\n"
    "Change video thumbnails with `/vth`:\n"
    "  • Send `/vth` to start a session\n"
    "  • Send up to **10 videos**\n"
    "  • Send a **photo** as the new thumbnail\n"
    "  • All videos are re-uploaded with the new thumb ✅\n"
    "  • Send `/vth_cancel` to abort at any time\n\n"
    "**3️⃣ CAPTION**\n"
    "Use `/set_caption Your text {filename}`\n\n"
    "**4️⃣ RESET CAPTION**\n"
    "Use `/del_caption` to restore default."
)

ABOUT_TEXT = (
    "💎 **Pro Rename Bot v3.1**\n\n"
    f"👨‍💻 **Developer:** @{DEVELOPER_USR}\n"
    "⚙️ **Engine:** Hydrogram + Motor\n"
    "🗄️ **Database:** MongoDB\n"
    "🚀 **Hosting:** Render\n\n"
    "Built for speed, security & reliability."
)

MAIN_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🛠 Help",    callback_data="help_msg"),
     InlineKeyboardButton("📝 Caption", callback_data="view_cap")],
    [InlineKeyboardButton("💎 About",   callback_data="about_msg")]
])

BACK_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("⬅️ Back", callback_data="back")]
])

# ─────────────────────────────────────────────
# HANDLER FUNCTIONS  (plain async functions — decorators applied in main())
# ─────────────────────────────────────────────

async def send_start_msg(message, is_callback=False):
    is_pub    = await is_bot_public()
    mode_text = "🔓 Public" if is_pub else "🔒 Private"
    user = message.from_user if not is_callback else message.chat
    name = getattr(user, "first_name", "User")
    text = START_TEXT.format(name=name, mode=mode_text)
    if is_callback:
        await message.edit_text(text, reply_markup=MAIN_KB)
    else:
        await message.reply_text(text, reply_markup=MAIN_KB)


async def start_cmd(client, message):
    await send_start_msg(message)


async def cb_handler(client, cb):
    if cb.data == "help_msg":
        await cb.message.edit_text(DETAILED_HELP, reply_markup=BACK_KB)
    elif cb.data == "about_msg":
        await cb.message.edit_text(ABOUT_TEXT, reply_markup=BACK_KB)
    elif cb.data == "view_cap":
        u   = await user_data.find_one({"_id": cb.from_user.id}) or {}
        cap = u.get("caption", "{filename}  _(default)_")
        await cb.message.edit_text(
            f"📝 **Your Current Caption:**\n\n`{cap}`",
            reply_markup=BACK_KB,
        )
    elif cb.data == "back":
        await send_start_msg(cb.message, is_callback=True)
    await cb.answer()


async def rename_handler(client, message):
    if not await is_bot_public() and message.from_user.id != ADMIN:
        return await message.reply("🔒 This bot is private.")

    reply = message.reply_to_message
    if not (reply.document or reply.video or reply.audio):
        return await message.reply("⚠️ Please reply to a file (document / video / audio).")

    parts = message.text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply("⚠️ Usage: `/rename NewFileName.ext`", quote=True)

    new_name = parts[1].strip()
    status   = await message.reply("📥 Downloading...")
    start_t  = time.time()
    path     = None
    try:
        path = await client.download_media(
            reply,
            file_name=new_name,
            progress=progress_bar,
            progress_args=(status, start_t),
        )
        u_data  = await user_data.find_one({"_id": message.from_user.id}) or {}
        caption = u_data.get("caption", "{filename}").replace("{filename}", new_name)
        await status.edit("📤 Uploading...")
        await client.send_document(
            message.chat.id, path, caption=caption,
            progress=progress_bar, progress_args=(status, time.time()),
        )
        await status.delete()
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")
    finally:
        if path and os.path.exists(path):
            os.remove(path)


async def set_caption_cmd(client, message):
    parts = message.text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply(
            "⚠️ Usage: `/set_caption Your caption here {filename}`", quote=True
        )
    new_cap = parts[1].strip()
    await user_data.update_one(
        {"_id": message.from_user.id}, {"$set": {"caption": new_cap}}, upsert=True
    )
    await message.reply(f"✅ Caption saved:\n`{new_cap}`")


async def del_caption_cmd(client, message):
    await user_data.update_one(
        {"_id": message.from_user.id}, {"$unset": {"caption": ""}}, upsert=True
    )
    await message.reply("✅ Caption reset to default.")


# ── /vth handlers ──────────────────────────────────────────────────────────────

async def vth_start(client, message):
    if not await is_bot_public() and message.from_user.id != ADMIN:
        return await message.reply("🔒 This bot is private.")
    uid = message.from_user.id
    old = vth_sessions.pop(uid, None)
    if old:
        for p in old.get("videos", []):
            if os.path.exists(p): os.remove(p)
    vth_sessions[uid] = {"videos": [], "awaiting": "videos"}
    await message.reply(
        "🎬 **Video Thumbnail Mode — Started!**\n\n"
        "📹 Send me up to **10 videos** now.\n"
        "🖼 When ready, send the **thumbnail photo** to apply it to all of them.\n\n"
        "❌ Send /vth_cancel to abort."
    )


async def vth_cancel(client, message):
    uid     = message.from_user.id
    session = vth_sessions.pop(uid, None)
    if session:
        for p in session.get("videos", []):
            if os.path.exists(p): os.remove(p)
    await message.reply("❌ Thumbnail session cancelled.")


async def vth_collect_videos(client, message):
    uid     = message.from_user.id
    session = vth_sessions.get(uid)
    if not session or session["awaiting"] != "videos":
        return

    media = message.video
    if not media and message.document:
        mime = getattr(message.document, "mime_type", "") or ""
        if "video" in mime:
            media = message.document
    if not media:
        return

    if len(session["videos"]) >= 10:
        return await message.reply(
            "⚠️ Maximum **10 videos** already queued.\n"
            "📸 Now send the **thumbnail image** to apply."
        )

    status = await message.reply(f"📥 Downloading video {len(session['videos']) + 1}/10...")
    try:
        path = await client.download_media(
            message, progress=progress_bar, progress_args=(status, time.time())
        )
        session["videos"].append(path)
        count = len(session["videos"])
        await status.edit(
            f"✅ **Video {count} queued.**\n\n"
            + ("Send more videos or s" if count < 10 else "S")
            + f"end the **📸 thumbnail photo** to process all {count} video(s)."
        )
    except Exception as e:
        await status.edit(f"❌ Download failed: `{e}`")


async def vth_apply_thumbnail(client, message):
    uid     = message.from_user.id
    session = vth_sessions.get(uid)
    if not session:
        return

    videos = session.get("videos", [])
    if not videos:
        return await message.reply(
            "⚠️ No videos queued yet.\nSend videos first, then the thumbnail photo."
        )

    vth_sessions.pop(uid, None)  # close session before processing
    thumb_path = None
    status     = await message.reply("🖼 Downloading thumbnail...")

    try:
        thumb_path    = await client.download_media(message.photo)
        total         = len(videos)
        success, fail = 0, 0

        for i, vid_path in enumerate(videos, 1):
            try:
                await status.edit(f"📤 Uploading video {i}/{total} with new thumbnail...")
                await client.send_video(
                    message.chat.id, vid_path,
                    thumb=thumb_path,
                    caption=f"✅ **{i}/{total}** — thumbnail applied",
                    progress=progress_bar, progress_args=(status, time.time()),
                )
                success += 1
            except Exception as e:
                fail += 1
                await message.reply(f"❌ Video {i} failed: `{e}`")
            finally:
                if os.path.exists(vid_path): os.remove(vid_path)

        result = f"🎉 **All done!**\n\n✅ Success: **{success}/{total}**"
        if fail:
            result += f"\n❌ Failed: **{fail}**"
        await status.edit(result)

    except Exception as e:
        await status.edit(f"❌ Fatal error: `{e}`")
        for p in videos:
            if os.path.exists(p): os.remove(p)
    finally:
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)


# ─────────────────────────────────────────────
# MAIN  — Client created here, inside the running event loop
# ─────────────────────────────────────────────

async def main():
    # ✅ FIX: Client is instantiated here so asyncio's event loop already exists.
    #    This avoids the "no current event loop in thread 'MainThread'" crash.
    bot = Client("rename_bot_pro", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

    # Register all handlers programmatically (no module-level @app.on_* decorators)
    bot.on_message(filters.command("start"))(start_cmd)
    bot.on_callback_query(filters.regex("^(help_msg|back|view_cap|about_msg)$"))(cb_handler)
    bot.on_message(filters.command("rename") & filters.reply)(rename_handler)
    bot.on_message(filters.command("set_caption"))(set_caption_cmd)
    bot.on_message(filters.command("del_caption"))(del_caption_cmd)
    bot.on_message(filters.command("vth"))(vth_start)
    bot.on_message(filters.command("vth_cancel"))(vth_cancel)
    bot.on_message(filters.video | filters.document)(vth_collect_videos)
    bot.on_message(filters.photo)(vth_apply_thumbnail)

    await start_web_server()
    await bot.start()
    print("🤖 Bot is Online!")
    await idle()
    await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
