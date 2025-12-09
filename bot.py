import os
from dotenv import load_dotenv
from typing import List
import logging
import asyncio
import subprocess
import tempfile

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)
from telegram.error import BadRequest

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def search_music_itunes(query: str) -> List[dict]:
    """Ищет треки в iTunes Search API."""
    url = "https://itunes.apple.com/search"
    params = {
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": 5,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    tracks = []
    for item in data.get("results", [])[:5]:
        tracks.append(
            {
                "title": item.get("trackName", "Без названия"),
                "artist": item.get("artistName", "Неизвестный исполнитель"),
                "link": item.get("trackViewUrl", ""),
            }
        )

    return tracks


def download_from_youtube_sync(artist: str, title: str) -> bytes:
    """Синхронная загрузка с YouTube через yt-dlp."""
    search_query = f"{artist} {title}"
    logger.info(f"🔍 Ищу на YouTube: {search_query}")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "%(title)s.%(ext)s")

        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "128",
            "-o",
            output_path,
            f"ytsearch:{search_query}",
        ]

        logger.info(f"⚡ Запускаю yt-dlp: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )

            logger.info(f"📊 yt-dlp return code: {result.returncode}")
            logger.info(f"📝 stdout: {result.stdout[:500]}")

            if result.returncode != 0:
                logger.error(f"❌ yt-dlp stderr: {result.stderr}")
                return None

            files = os.listdir(tmpdir)
            logger.info(f"📂 Файлы в tmpdir: {files}")

            mp3_files = [f for f in files if f.endswith(".mp3")]

            if not mp3_files:
                logger.error("❌ MP3 файлы не найдены")
                return None

            file_path = os.path.join(tmpdir, mp3_files[0])
            file_size = os.path.getsize(file_path)
            logger.info(f"✅ MP3 найден: {mp3_files[0]} ({file_size / 1024 / 1024:.2f} MB)")

            with open(file_path, "rb") as f:
                audio_data = f.read()

            logger.info(f"✅ Загружено в память: {len(audio_data) / 1024 / 1024:.2f} MB")
            return audio_data

        except subprocess.TimeoutExpired:
            logger.error("❌ Timeout yt-dlp (120 сек)")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка: {type(e).__name__}: {e}")
            return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Привет! Отправь мне название песни.\n\n"
        "🔍 Поиск с высокой релевантностью.\n\n"
        "🎶 Например: `linkin park numb` или `oxxxymiron город`\n\n"
        "⚡ Клик — скачиваю полную MP3!",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Напиши название песни.\n\n" "🔊 Клик — скачиваю полный MP3."
    )


def build_tracks_keyboard(tracks: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for i, t in enumerate(tracks):
        text = (
            f"{t['artist']} - {t['title']}"
            if t.get("artist")
            else t.get("title", "Трек")
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=text[:60], callback_data=f"dl_{i}"
                )
            ]
        )
    return InlineKeyboardMarkup(buttons)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    query = update.message.text.strip()
    if not query:
        return

    logger.info(f"🔍 Поиск: {query}")
    msg = await update.message.reply_text("🔍 Ищу треки...")

    try:
        tracks = await search_music_itunes(query)
    except Exception as e:
        logger.error(f"❌ iTunes ошибка: {e}")
        await msg.edit_text("❌ Ошибка поиска.")
        return

    if not tracks:
        await msg.edit_text("🔍 Ничего не найдено.")
        return

    context.user_data["tracks"] = tracks
    logger.info(f"✅ Найдено {len(tracks)} треков")

    text_lines = []
    for i, t in enumerate(tracks, start=1):
        line = f"{i}. {t.get('artist', 'Неизвестный')} — {t.get('title', 'Без названия')}"
        text_lines.append(line)

    text_lines.append("\n🔊 Клик на трек = скачиваю MP3")

    await msg.edit_text(
        "\n".join(text_lines), reply_markup=build_tracks_keyboard(tracks)
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    logger.info(f"🔘 Callback получен: {query.data}")

    try:
        await query.answer()
    except Exception as e:
        logger.error(f"❌ query.answer() ошибка: {e}")
        return

    try:
        track_index = int(query.data.split("_")[1])
    except (IndexError, ValueError) as e:
        logger.error(f"❌ Не могу парсить callback_data: {e}")
        await query.edit_message_text("❌ Ошибка обработки кнопки.")
        return

    tracks = context.user_data.get("tracks", [])

    if track_index >= len(tracks):
        logger.error(f"❌ Индекс {track_index} вне диапазона ({len(tracks)})")
        await query.edit_message_text("❌ Ошибка.")
        return

    track = tracks[track_index]
    logger.info(f"🎵 Скачиваю: {track['artist']} - {track['title']}")

    await query.edit_message_text(
        f"🎵 {track['artist']} - {track['title']}\n\n⚡ Грузу с YouTube...\n(может занять 1-3 минуты)"
    )

    try:
        logger.info("⏳ Ожидаю загрузку...")
        audio_data = await asyncio.to_thread(
            download_from_youtube_sync, track["artist"], track["title"]
        )

        if not audio_data:
            logger.error("❌ audio_data пуста")
            await query.edit_message_text(
                f"❌ Не нашел на YouTube.\n\n🔗 iTunes: {track['link']}"
            )
            return

        logger.info(f"📤 Отправляю в Telegram ({len(audio_data) / 1024 / 1024:.2f} MB)...")
        
        await query.message.reply_audio(
            audio=audio_data,
            title=track["title"],
            performer=track["artist"],
            caption=f"🎵 {track['artist']} - {track['title']}",
        )

        await query.edit_message_text(
            f"✅ {track['artist']} - {track['title']}\n✅ В чате!"
        )
        logger.info("✅ Успешно отправлено!")

    except BadRequest as e:
        logger.error(f"❌ Telegram ошибка: {e}")
        await query.edit_message_text(f"❌ Ошибка Telegram: {e}")
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка: {type(e).__name__}: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(handle_button, pattern=r"^dl_"))

    logger.info("🚀 Бот запущен!")
    application.run_polling()


if __name__ == "__main__":
    main()
