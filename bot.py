import os
from dotenv import load_dotenv
from typing import List
import logging

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
VK_TOKEN = os.getenv("VK_TOKEN", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def search_music_vk(query: str) -> List[dict]:
    """Ищет треки по текстовому запросу через VK Music."""
    if not VK_TOKEN:
        logger.warning("Токен VK не настроен, использую iTunes API")
        return await search_music_itunes(query)

    try:
        from vkpymusic import Service
        service = Service.parse_config()
        if not service:
            service = Service(token_path="vk_config.txt")
        
        tracks_raw = list(service.search_songs_by_text(query, count=5))
        tracks = []
        for t in tracks_raw:
            tracks.append({
                "title": t.title,
                "artist": t.artist,
                "link": f"https://vk.com/audio{t.owner_id}_{t.id}",
                "duration": t.duration
            })
        return tracks
    except Exception as e:
        logger.error(f"Ошибка VK API: {e}, переключаюсь на iTunes")
        return await search_music_itunes(query)


async def search_music_itunes(query: str) -> List[dict]:
    """Ищет треки через бесплатный iTunes Search API."""
    url = "https://itunes.apple.com/search"
    params = {
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": 5
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    tracks = []
    for item in data.get("results", [])[:5]:
        tracks.append({
            "title": item.get("trackName", "Без названия"),
            "artist": item.get("artistName", "Неизвестный исполнитель"),
            "link": item.get("trackViewUrl", ""),
            "preview": item.get("previewUrl", "")
        })

    return tracks


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Привет! Отправь мне текст, а я попробую найти подходящую музыку.\n\n"
        "🔍 Поиск выполняется через iTunes/Apple Music (\u0440аботает в России без VPN).\n\n"
        "🎶 Например: `linkin park numb` или `Oxxxymiron город под подошвой`",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Просто напиши, что ты хочешь найти:\n"
        "• Исполнителя или название песни\n"
        "• Описание настроения или жанр\n\n"
        "🌍 Поиск работает через iTunes API — доступно в России без VPN."
    )


def build_tracks_keyboard(tracks: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for t in tracks:
        text = f"{t['artist']} - {t['title']}" if t.get("artist") else t.get("title", "Трек")
        buttons.append([InlineKeyboardButton(text=text[:60], url=t["link"])])
    return InlineKeyboardMarkup(buttons)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    query = update.message.text.strip()
    if not query:
        return

    msg = await update.message.reply_text("🔍 Ищу треки...")

    try:
        tracks = await search_music_itunes(query)
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await msg.edit_text("❌ Произошла ошибка при запросе к музыкальному сервису, попробуй позже.")
        return

    if not tracks:
        await msg.edit_text("🔍 Ничего не нашлось. Попробуй сформулировать запрос по‑другому.")
        return

    text_lines = []
    for i, t in enumerate(tracks, start=1):
        line = f"{i}. {t.get('artist', 'Неизвестный исполнитель')} — {t.get('title', 'Без названия')}"
        text_lines.append(line)

    text_lines.append("\n👆 Нажми на кнопку, чтобы прослушать трек в Apple Music/iTunes.")

    await msg.edit_text(
        "\n".join(text_lines),
        reply_markup=build_tracks_keyboard(tracks),
    )


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Бот запущен!")
    application.run_polling()


if __name__ == "__main__":
    main()
