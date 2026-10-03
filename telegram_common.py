"""
Общие функции для публикации в Telegram-канал. Используется параллельно и
новостным скриптом news/rewrite_and_post.py, и рубричным
rubric_post_to_max.py.

Публикация в Telegram опциональна: если переменные окружения не заданы,
is_configured() возвращает False.

Медиа отправляется ФАЙЛОМ (multipart), а не ссылкой — иначе Telegram Bot API
сам пытается скачать его со своих серверов и часто падает на источниках без
привычного User-Agent (lenta.ru, Pexels и т.п.). Три способа передать медиа:
  photo_url   — ссылка: файл скачивается здесь и отправляется в Telegram
                (фото из своей базы, Pexels, фото из статьи, постер);
  photo_bytes — готовые байты картинки: используется для картинок,
                сгенерированных нейросетью (cf_image.py), у которых нет
                публичной ссылки;
  video_url   — ссылка на видео (отправляется как sendVideo).

Лёгкая офлайн-адаптация текста под Telegram БЕЗ вызовов GPT:
  generate_hashtags()             — подбирает хэштеги по ключевым словам,
                                     реально встретившимся в тексте (для новостей);
  adapt_text_for_telegram_local() — добавляет хэштеги в конец готового MAX-текста.
Хэштеги рубрик задаются по ключу рубрики в RUBRIC_HASHTAGS ниже. Тон и
структура текста не меняются — меняется только набор хэштегов в конце.

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


def _cut_to_limit(html_text: str, limit: int) -> str:
    """Обрезает текст до лимита Telegram по границе строки, а не посреди
    слова или HTML-тега (обрезанный тег вроде «<b» вызывает ошибку 400 и
    пост не выходит)."""
    if len(html_text) <= limit:
        return html_text
    cut = html_text[:limit]
    boundary = cut.rfind("\n")
    if boundary > limit // 2:
        cut = cut[:boundary]
    return cut.rstrip()


# --- Подбор хэштегов без ИИ ---

def _normalize_for_keywords(text: str) -> str:
    """Та же нормализация, что в news/filter_articles.py — приводим к
    нижнему регистру и заменяем ё→е, чтобы поиск ключевых слов был
    единообразным независимо от того, как слово написано в тексте."""
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


# Ключевое слово (подстрока, ищется без учёта регистра/ё) → хэштег.
# Порядок = приоритет при нескольких совпадениях. Список ориентирован на
# новости шоу-бизнеса (его использует новостной скрипт) — расширяйте под
# свои темы при необходимости.
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
    "utro_privet": ["#доброеутро", "#утро"],
    "goroskop": ["#гороскоп", "#астрология"],
    "recept": ["#рецепты", "#готовимдома"],
    "zozh": ["#здоровье", "#зож"],
    "manikur": ["#маникюр", "#красота"],
    "psy_otnosheniya": ["#психология", "#отношения"],
    "kino_serial": ["#кино", "#чтопосмотреть"],
    "ezoterika": ["#эзотерика", "#таро"],
    "mama_rebenok": ["#мамаиребенок", "#воспитание"],
    "finansy": ["#финансы", "#деньги"],
    "stil": ["#стиль", "#мода"],
    "krasota": ["#красота", "#уход"],
    "istoriya_zhenshiny": ["#сильныеженщины", "#вдохновение"],
    "narodnaya_mudrost": ["#народнаямудрость", "#приметы"],
    "test_dnya": ["#тестдня", "#психология"],
    "semeinye_istorii": ["#историиизжизни", "#семья"],
    "vecherniy_ritual": ["#вечернийритуал", "#релакс"],
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
    используем). Тон, факты и структура абзацев НЕ меняются."""
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


def _image_filename(data: bytes) -> str:
    """Имя файла по содержимому: нейросеть может вернуть PNG, остальные
    источники — JPEG/WebP."""
    if data[:4] == b"\x89PNG":
        return "image.png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image.webp"
    return "image.jpg"


def send_message(
    text: str,
    photo_url: str = None,
    video_url: str = None,
    photo_bytes: bytes = None,
) -> requests.Response:
    """Отправляет пост. Медиа — одно из: photo_bytes (готовая картинка, например
    сгенерированная), photo_url, video_url. Если медиа не удалось получить,
    уходит просто текст."""
    if not is_configured():
        raise RuntimeError("Telegram не настроен: нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")

    html_text = format_for_telegram(text)

    is_video = bool(video_url) and not photo_url and not photo_bytes
    media_bytes = photo_bytes
    if not media_bytes:
        media_url = photo_url or video_url
        media_bytes = _download_bytes(media_url) if media_url else None

    if media_bytes:
        endpoint = "sendVideo" if is_video else "sendPhoto"
        field_name = "video" if is_video else "photo"
        filename = "video.mp4" if is_video else _image_filename(media_bytes)
        files = {field_name: (filename, media_bytes)}

        if len(html_text) > TELEGRAM_CAPTION_LIMIT:
            # Подпись к медиа ограничена 1024 символами: длинный пост
            # отправляется как картинка, а затем отдельным сообщением — текст.
            media_resp = requests.post(
                f"{API_BASE}/{endpoint}",
                data={"chat_id": CHAT_ID},
                files=files,
                timeout=120,
            )
            if media_resp.status_code != 200:
                print(f"Telegram: медиа не отправилось — {media_resp.status_code} {media_resp.text[:200]}")
            return requests.post(
                f"{API_BASE}/sendMessage",
                json={
                    "chat_id": CHAT_ID,
                    "text": _cut_to_limit(html_text, TELEGRAM_MESSAGE_LIMIT),
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
            "text": _cut_to_limit(html_text, TELEGRAM_MESSAGE_LIMIT),
            "parse_mode": "HTML",
        },
        timeout=30,
    )
