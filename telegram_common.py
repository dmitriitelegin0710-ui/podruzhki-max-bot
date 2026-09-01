"""
Общие функции для публикации в Telegram-канал. Используется параллельно и
новостным скриптом news/rewrite_and_post.py, и рубричным
rubric_post_to_max.py.

Публикация в Telegram опциональна: если переменные окружения не заданы,
is_configured() возвращает False.

Фото/видео отправляются файлом (multipart), а не ссылкой — иначе Telegram
Bot API сам пытается скачать медиа со своих серверов и часто падает на
источниках без привычного User-Agent (lenta.ru, Pexels и т.п.).

--- НОВОЕ: лёгкая офлайн-адаптация текста под Telegram, БЕЗ вызовов GPT ---
Чтобы посты в MAX и Telegram не были абсолютно идентичны, но при этом не
тратить токены YandexGPT на вторую генерацию, здесь есть два простых
инструмента чисто на regex/поиске подстрок:
  generate_hashtags()          — подбирает 2-3 хэштега по ключевым словам,
                                  которые реально встретились в готовом
                                  тексте поста (без обращения к ИИ);
  adapt_text_for_telegram_local() — добавляет эти хэштеги в конец уже
                                  готового MAX-текста.
Это честная, но простая эвристика: тон и структура текста НЕ меняются,
меняется только набор хэштегов в конце — этого достаточно, чтобы посты не
были 1-в-1 одинаковыми на двух площадках, и ничего не стоит по токенам.

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

TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_LIMIT = 4096

DOWNLOAD_HEADERS = {"User-Agent": "Mozilla/5.0"}


def is_configured() -> bool:
    return bool(BOT_TOKEN) and bool(CHAT_ID)


def format_for_telegram(text: str) -> str:
    """MAX-разметка (**жирный**, _курсив_, ++подчёркивание++) → HTML для
    Telegram Bot API (parse_mode=HTML)."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\+\+(.+?)\+\+", r"<u>\1</u>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    return text


# --- Подбор хэштегов без ИИ ---

def _normalize_for_keywords(text: str) -> str:
    """Та же нормализация, что в news/filter_articles.py — приводим к
    нижнему регистру и заменяем ё→е, чтобы поиск ключевых слов был
    единообразным независимо от того, как слово написано в тексте."""
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


# Ключевое слово (подстрока, ищется без учёта регистра/ё) → хэштег.
# Порядок = приоритет при нескольких совпадениях. Список ориентирован на
# новости шоу-бизнеса — расширяйте под свои темы при необходимости.
NEWS_HASHTAG_KEYWORDS = [
    ("скандал", "#скандал"),
    ("сплетн", "#сплетни"),
    ("развод", "#развод"),
    ("свадьб", "#свадьба"),
    ("беремен", "#беременность"),
    ("измен", "#измена"),
    ("роман", "#отношения"),
    ("концерт", "#концерт"),
    ("гастрол", "#концерт"),
    ("сериал", "#сериал"),
    ("фильм", "#кино"),
    ("кино", "#кино"),
    ("премьер", "#премьера"),
    ("блогер", "#блогеры"),
    ("тикток", "#тикток"),
    ("инстаграм", "#инстаграм"),
    ("премия", "#премия"),
    ("оскар", "#оскар"),
]
DEFAULT_NEWS_HASHTAGS = ["#шоубиз", "#звезды"]

# Хэштеги по ключу рубрики (rubric["key"] из rubrics.json). Если рубрики в
# этом словаре нет — используется DEFAULT_RUBRIC_HASHTAGS. Дополняйте по
# мере появления новых рубрик.
RUBRIC_HASHTAGS = {
    "test_dnya": ["#тестдня", "#психология"],
    "istoriya_zhenshiny": ["#сильныеженщины", "#вдохновение"],
    "ezoterika": ["#эзотерика", "#таро"],
    "utro_privet": ["#доброеутро"],
}
DEFAULT_RUBRIC_HASHTAGS = ["#подружки"]


def generate_hashtags(text: str, keyword_map, default_tags, max_tags: int = 3) -> list:
    """Подбирает хэштеги ПО ГОТОВОМУ ТЕКСТУ поста без каких-либо обращений
    к ИИ — ищет ключевые слова из keyword_map (список пар
    (подстрока, хэштег), порядок = приоритет) внутри текста. Если ничего
    не нашлось — возвращает default_tags. Не дублирует хэштеги."""
    normalized = _normalize_for_keywords(text)
    found = []
    for keyword, hashtag in keyword_map:
        if keyword in normalized and hashtag not in found:
            found.append(hashtag)
        if len(found) >= max_tags:
            break
    return found or list(default_tags)


def adapt_text_for_telegram_local(max_text: str, hashtags: list) -> str:
    """Лёгкая офлайн-адаптация уже готового MAX-поста под Telegram — БЕЗ
    повторного обращения к YandexGPT. Добавляет хэштеги в конце поста
    (обычная телеграм-практика, которую в MAX-версии намеренно не
    используем). Тон, факты и структура абзацев НЕ меняются — это не
    полноценная адаптация, а дешёвый способ хоть немного отличать площадки
    без второго вызова GPT."""
    text = max_text.rstrip()
    if hashtags:
        text += "\n\n" + " ".join(hashtags)
    return text


# --- Отправка ---

def _download_bytes(url: str):
    try:
        resp = requests.get(url, timeout=60, headers=DOWNLOAD_HEADERS)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"Telegram: не удалось скачать медиа по URL ({e}), отправлю без него")
        return None


def send_message(text: str, photo_url: str = None, video_url: str = None) -> requests.Response:
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
