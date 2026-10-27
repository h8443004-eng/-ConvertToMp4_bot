import asyncio
import logging
import os
import tempfile
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("video-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
MAX_DOWNLOAD = 20 * 1024 * 1024  # Bot API limit for getFile (without local Bot API server)
MAX_UPLOAD = 50 * 1024 * 1024    # Bot API limit for sending files


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Send me a video (MKV, AVI, MOV, WEBM, GIF, etc.) and I'll convert it to MP4.\n"
        "Limit: 20 MB per file."
    )


async def run_ffmpeg(src: Path, dst: Path) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # H.264 needs even dimensions
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    return proc.returncode, err.decode(errors="ignore")[-500:]


async def convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    media = msg.video or msg.animation or msg.video_note or msg.document
    if media is None:
        return
    if msg.document and not (msg.document.mime_type or "").startswith("video/"):
        await msg.reply_text("Please send a video file.")
        return
    if media.file_size and media.file_size > MAX_DOWNLOAD:
        await msg.reply_text("❌ File is larger than 20 MB (Telegram Bot API limit).")
        return

    status = await msg.reply_text("⏳ Converting to MP4...")
    await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_VIDEO)

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input"
        dst = Path(tmp) / "output.mp4"
        try:
            tg_file = await media.get_file()
            await tg_file.download_to_drive(src)

            code, err = await run_ffmpeg(src, dst)
            if code != 0 or not dst.exists():
                log.error("ffmpeg failed: %s", err)
                await status.edit_text("❌ Conversion failed. Is this a valid video?")
                return
            if dst.stat().st_size > MAX_UPLOAD:
                await status.edit_text("❌ Converted file is over 50 MB, can't send it.")
                return

            with dst.open("rb") as f:
                await msg.reply_video(video=f, filename="converted.mp4", supports_streaming=True)
            await status.delete()
        except Exception:
            log.exception("Error")
            await status.edit_text("❌ Something went wrong. Try again.")


def main():
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.ANIMATION | filters.VIDEO_NOTE | filters.Document.VIDEO, convert))
    log.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
