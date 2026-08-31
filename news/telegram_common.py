"""
Общие функции для публикации в Telegram-канал — используется параллельно
с MAX новостным скриптом news/rewrite_and_post.py. Публикация в Telegram
опциональна: если переменные окружения не заданы, is_configured()
возвращает False и rewrite_and_post.py просто пропускает публикацию туда,
ничего не ломая.

Требуемые переменные окружения (GitHub Secrets):
  TELEGRAM_BOT_TOKEN — токен бота, который уже подключён как автопост в канал
  TELEGRAM_CHAT_ID   — id канала/чата (например, @your_channel или -100...)
"""
import os
import re

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None

# Лимиты Telegram Bot API.
TELEGRAM_CAPTION_LIMIT = 1024   # макс. длина подписи к фото
TELEGRAM_MESSAGE_LIMIT = 4096   # макс. длина текстового сообщения


def is_configured() -> bool:
    """True, если заданы оба секрета и публикацию в Telegram можно пробовать."""
    return bool(BOT_TOKEN) and bool(CHAT_ID)


def format_for_telegram(text: str) -> str:
    """Конвертирует markdown-разметку, которую использует MAX
    (**жирный**, _курсив_), в HTML, понятный Telegram Bot API
    (parse_mode=HTML). Сначала экранируются HTML-спецсимволы, которые
    могли случайно оказаться в сгенерированном тексте, — иначе, например,
    случайный символ "<" сломает разметку сообщения."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    return text


def send_message(text: str, photo_url: str = None) -> requests.Response:
    """Публикует пост в Telegram-канал. Если передан photo_url — публикует
    как фото с подписью (как в MAX); Telegram принимает прямую ссылку на
    изображение без предварительной загрузки, в отличие от MAX. Если текст
    длиннее лимита подписи к фото — фото уходит без подписи, а полный текст
    отдельным сообщением следом, чтобы ничего не обрезалось молча."""
    if not is_configured():
        raise RuntimeError("Telegram не настроен: нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")

    html_text = format_for_telegram(text)

    if photo_url:
        if len(html_text) > TELEGRAM_CAPTION_LIMIT:
            requests.post(
                f"{API_BASE}/sendPhoto",
                json={"chat_id": CHAT_ID, "photo": photo_url},
                timeout=30,
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
            f"{API_BASE}/sendPhoto",
            json={
                "chat_id": CHAT_ID,
                "photo": photo_url,
                "caption": html_text,
                "parse_mode": "HTML",
            },
            timeout=30,
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
