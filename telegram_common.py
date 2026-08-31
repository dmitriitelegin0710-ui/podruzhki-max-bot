"""
Общие функции для публикации в Telegram-канал. Используется параллельно и
новостным скриптом news/rewrite_and_post.py, и рубричным
rubric_post_to_max.py — чтобы В Telegram уходил полный дубль всех постов,
которые публикуются в MAX (и новости, и рубрики), с тем же текстом и тем
же медиа.

Публикация в Telegram опциональна: если переменные окружения не заданы,
is_configured() возвращает False и вызывающий скрипт просто пропускает
публикацию туда, ничего не ломая в публикации в MAX.

Фото/видео отправляются файлом (multipart), а не ссылкой — Telegram Bot
API сам пытается скачать медиа по URL со своих серверов и часто падает
ошибкой "Bad Request: failed to get HTTP URL content", если источник
(lenta.ru, Pexels и т.п.) блокирует запросы без привычного браузеру
User-Agent. Поэтому медиа скачивается самим скриптом и отправляется как
файл — так серверу Telegram ничего самому скачивать не нужно.

Требуемые переменные окружения (GitHub Secrets):
  TELEGRAM_BOT_TOKEN — токен бота, подключённого как автопост в канал
  TELEGRAM_CHAT_ID   — id канала/чата (например, @your_channel или -100...)
"""
import os
import re

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None

TELEGRAM_CAPTION_LIMIT = 1024   # макс. длина подписи к фото/видео
TELEGRAM_MESSAGE_LIMIT = 4096   # макс. длина текстового сообщения

# Нужен, т.к. некоторые источники (в т.ч. Pexels) блокируют запросы без
# него — та же логика, что уже используется в max_common.py.
DOWNLOAD_HEADERS = {"User-Agent": "Mozilla/5.0"}


def is_configured() -> bool:
    """True, если заданы оба секрета и публикацию в Telegram можно пробовать."""
    return bool(BOT_TOKEN) and bool(CHAT_ID)


def format_for_telegram(text: str) -> str:
    """Конвертирует MAX-разметку (**жирный**, _курсив_, ++подчёркивание++)
    в HTML, понятный Telegram Bot API (parse_mode=HTML). Telegram не умеет
    ++подчёркивание++ как отдельный markdown-синтаксис — переводим его в
    <u>. Сначала экранируются HTML-спецсимволы, которые могли случайно
    оказаться в сгенерированном тексте, — иначе, например, случайный
    символ "<" сломает разметку сообщения."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\+\+(.+?)\+\+", r"<u>\1</u>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    return text


def _download_bytes(url: str):
    """Скачивает медиа самим скриптом (с User-Agent браузера), чтобы не
    просить Telegram скачивать его со своих серверов. Возвращает None при
    любой ошибке — тогда вызывающий код отправит пост без медиа вместо
    того, чтобы упасть целиком."""
    try:
        resp = requests.get(url, timeout=60, headers=DOWNLOAD_HEADERS)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"Telegram: не удалось скачать медиа по URL ({e}), отправлю без него")
        return None


def send_message(text: str, photo_url: str = None, video_url: str = None) -> requests.Response:
    """Публикует пост в Telegram-канал.
      photo_url — если передан, скачивает фото и публикует его файлом с подписью.
      video_url — если передан (и photo_url не передан), то же самое, но видео.
    Если текст длиннее лимита подписи — медиа уходит без подписи, а полный
    текст отдельным сообщением следом, чтобы ничего не обрезалось молча.
    Если медиа не удалось скачать — пост уходит обычным текстовым
    сообщением."""
    if not is_configured():
        raise RuntimeError("Telegram не настроен: нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")

    html_text = format_for_telegram(text)

    media_url = photo_url or video_url
    is_video = bool(video_url) and not photo_url
    media_bytes = _download_bytes(media_url) if media_url else None

    if media_bytes:
        endpoint = "sendVideo" if is_video else "sendPhoto"
        field_name = "video" if is_video else "photo"
        filename = "video.mp4" if is_video else "image.jpg"
        files = {field_name: (filename, media_bytes)}

        if len(html_text) > TELEGRAM_CAPTION_LIMIT:
            requests.post(
                f"{API_BASE}/{endpoint}",
                data={"chat_id": CHAT_ID},
                files=files,
                timeout=120,
            )
            return requests.post(
                f"{API_BASE}/sendMessage",
                json={
                    "chat_id": CHAT_ID,
                    "text": html_text[:TELEGRAM_MESSAGE_LIMIT],
                    "parse_mode": "HTML",
                },
                timeout=30,
            )

        return requests.post(
            f"{API_BASE}/{endpoint}",
            data={
                "chat_id": CHAT_ID,
                "caption": html_text,
                "parse_mode": "HTML",
            },
            files=files,
            timeout=120,
        )

    return requests.post(
        f"{API_BASE}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": html_text[:TELEGRAM_MESSAGE_LIMIT],
            "parse_mode": "HTML",
        },
        timeout=30,
    )
