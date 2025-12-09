import os
from dotenv import load_dotenv
from typing import List
import logging

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
from telegram.error import BadRequest

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
VK_TOKEN = os.getenv("VK_TOKEN", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
        preview_url = item.get("previewUrl", "")
        tracks.append({
            "title": item.get("trackName", "Без названия"),
            "artist": item.get("artistName", "Неизвестный исполнитель"),
            "link": item.get("trackViewUrl", ""),
            "preview": preview_url,
            "has_preview": bool(preview_url)
        })

    return tracks


async def download_preview(preview_url: str) -> bytes:
    """Загружает 30-секундный попревью трека на основе iTunes API."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(preview_url)
        resp.raise_for_status()
        return resp.content


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Привет! Отправь мне текст, а я попробую найти подходящую музыку.\n\n"
        "🔍 Поиск выполняется через iTunes/Apple Music (работает в России без VPN).\n\n"
        "🎶 Например: `linkin park numb` или `Oxxxymiron город под подошвой`\n\n"
        "✨ Кликни на песню, и я отправлю 30-секундный попревью!",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Просто напиши, что ты хочешь найти:\n"
        "• Исполнителя или название песни\n"
        "• Описание настроения или жанр\n\n"
        "🌍 Поиск работает через iTunes API — доступно в России без VPN.\n\n"
        "🔗 Нажми на песню, я отправлю попревью (30 секунд) в Telegram."
    )


def build_tracks_keyboard(tracks: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for i, t in enumerate(tracks):
        text = f"{t['artist']} - {t['title']}" if t.get("artist") else t.get("title", "Трек")
        preview_indicator = "🔊" if t.get("has_preview") else "🔗"
        button_text = f"{preview_indicator} {text[:55]}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"preview_{i}")])
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

    # Сохрани треки в context для Каллбэка
    context.user_data["tracks"] = tracks

    text_lines = []
    for i, t in enumerate(tracks, start=1):
        line = f"{i}. {t.get('artist', 'Неизвестный исполнитель')} — {t.get('title', 'Без названия')}"
        if t.get("has_preview"):
            line += " [🔊 есть попревью]"
        text_lines.append(line)

    text_lines.append("\n🔊 Кликни для скачивания 30-секундного попревью (AAC-формат).")
    text_lines.append("🔗 Либо кликни для открытия в Apple Music/iTunes.")

    await msg.edit_text(
        "\n".join(text_lines),
        reply_markup=build_tracks_keyboard(tracks),
    )


async def handle_preview_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    # Получи индекс трека из callback_data
    track_index = int(query.data.split("_")[1])
    tracks = context.user_data.get("tracks", [])

    if track_index >= len(tracks):
        await query.edit_message_text("❌ Ошибка при загружке.")
        return

    track = tracks[track_index]

    # Проверь есть ли попревью
    if not track.get("preview"):
        await query.edit_message_text(
            f"❌ {track['artist']} - {track['title']}\n\n"
            "Попревью не доступна. Открыть в Apple Music: " + track["link"]
        )
        return

    # Начни показ статуса
    await query.edit_message_text(
        f"🔊 {track['artist']} - {track['title']}\n\nЗагружаю попревью..."
    )

    try:
        # Загружай попревью
        audio_data = await download_preview(track["preview"])
        file_name = f"{track['artist']} - {track['title']}.aac".replace("/", "").replace("\\", "")

        # Отправь в чат
        await query.message.reply_audio(
            audio=audio_data,
            title=track["title"],
            performer=track["artist"],
            caption=f"🎵 30-секундный попревью от iTunes\n\n🔗 Открыть полный трек: {track['link']}"
        )

        # Обнови сообщение
        await query.edit_message_text(
            f"✔️ {track['artist']} - {track['title']}\n\
Попревью отправлена в чат!\n\n🔗 Apple Music: {track['link']}"
        )
    except BadRequest as e:
        logger.error(f"Ошибка Telegram: {e}")
        await query.edit_message_text(
            f"❌ Не удалось отправить аудио.\n\n🔗 Открыть в Apple Music: {track['link']}"
        )
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await query.edit_message_text(
            f"❌ Ошибка при загружке попревью.\n\n🔗 Открыть в Apple Music: {track['link']}"
        )


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(handle_preview_button, pattern=r"^preview_\d+$"))

    logger.info("🚀 Бот запущен!")
    application.run_polling()


if __name__ == "__main__":
    main()
