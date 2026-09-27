"""
Получение новостей шоу-бизнеса напрямую из тех же RSS-источников,
что использует сайт podruzhki.online (см. import_rss.py на сайте) —
вместо поиска через GDELT.

Заменяет связку gdelt_search.py + filter_articles.py: результат этого
скрипта пишется сразу в news/filtered_articles.json — в том же формате,
который уже читает rewrite_and_post.py, поэтому сам rewrite_and_post.py
менять не нужно.

ВАЖНО: список лент и стоп-слова СОЗНАТЕЛЬНО продублированы из
import_rss.py сайта, а не импортированы оттуда — бот и сайт живут в
разных репозиториях. При изменении лент/ключевых слов на сайте не
забудьте перенести изменения и сюда вручную (или вынести оба списка в
общий JSON-файл, если захотите синхронизировать автоматически).

Требуемая зависимость: feedparser (добавьте в requirements.txt бота).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import feedparser

OUTPUT_FILE = Path("news/filtered_articles.json")

# Те же ленты, что использует сайт (см. import_rss.py). Держите этот
# список в синхроне вручную, если на сайте появятся новые источники.
RSS_FEEDS = [
    {
        "url": "https://www.starhit.ru/rss/",
        "source_name": "StarHit",
        "limit": 15,
    },
    {
        "url": "https://www.eg.ru/rss.xml",
        "source_name": "Экспресс газета",
        "limit": 15,
        "keywords": [
            "звезд", "знаменит", "селебрити", "светск",
            "актрис", "актер", "актёр", "певиц", "певец", "артист",
            "блогер", "шоу-бизнес", "шоубизнес", "шоу бизнес",
            "скандал", "развод", "роман", "слух", "измен",
            "папарац", "экс-муж", "экс-жена", "бывш",
            "беремен", "родила", "родил", "свадьб", "помолвк",
            "принц", "принцесс", "королев", "монарх",
        ],
    },
]

# Тот же общий стоп-список тяжёлых тем, что на сайте — действует на все
# ленты независимо от 'keywords' конкретной ленты.
EXCLUDE_KEYWORDS = [
    "суицид", "самоубийств", "покончил", "покончила",
    "повесил", "повесилась", "спрыгнул", "спрыгнула",
    "погиб", "погибла", "погибли",
    "умер", "умерла", "умерли", "скончал",
    "смерть", "трагически",
    "убил", "убила", "убийств", "зарезал", "застрелил",
    "изнасил", "насилие", "насильник",
    "разбился", "разбилась",
    "пропал без вести", "пропала без вести",
    "реанимац", "кома,", "в коме",
]

# Максимум статей суммарно по всем лентам, которые попадут в
# filtered_articles.json за один запуск. rewrite_and_post.py всё равно
# сам пропустит уже опубликованные (posted_news.json) и попробует не
# больше MAX_ATTEMPTS_PER_RUN кандидатов — этот лимит просто ограничивает
# размер файла и объём работы feedparser'а.
MAX_TOTAL_ARTICLES = 40


def find_text(entry) -> str:
    """Пробуем несколько возможных полей, где может быть текст новости —
    как на сайте, так что описание совпадает 1-в-1."""
    if entry.get("summary"):
        return entry.get("summary").strip()
    if entry.get("description"):
        return entry.get("description").strip()
    if "content" in entry and entry.content:
        value = entry.content[0].get("value", "")
        if value:
            return value.strip()
    if entry.get("subtitle"):
        return entry.get("subtitle").strip()
    return ""


def find_image_url(entry):
    if "media_content" in entry and entry.media_content:
        url = entry.media_content[0].get("url")
        if url:
            return url
    if "media_thumbnail" in entry and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url")
        if url:
            return url
    if "links" in entry:
        for link in entry.links:
            if link.get("type", "").startswith("image"):
                return link.get("href")
    if "enclosures" in entry:
        for enclosure in entry.enclosures:
            if enclosure.get("type", "").startswith("image"):
                return enclosure.get("href") or enclosure.get("url")
    return None


def matches_keywords(title, text, keywords) -> bool:
    haystack = f"{title} {text}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def is_excluded(title, text) -> bool:
    return matches_keywords(title, text, EXCLUDE_KEYWORDS)


def entry_date_iso(entry) -> str:
    """Достаёт дату публикации записи в ISO-формате; если не получилось —
    используем текущее время как запасной вариант (не критично, дата не
    используется для дедупликации — та идёт по url)."""
    for field in ("published_parsed", "updated_parsed"):
        value = entry.get(field)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def fetch_feed(feed_config: dict) -> list:
    url = feed_config["url"]
    source_name = feed_config["source_name"]
    limit = feed_config.get("limit", 15)
    keywords = feed_config.get("keywords")

    print(f"Читаю ленту: {source_name} ({url})")
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        print(f"  Ошибка запроса к ленте {source_name}: {e}")
        return []

    if feed.bozo and not feed.entries:
        print(f"  Лента не читается: {getattr(feed, 'bozo_exception', 'неизвестно')}")
        return []

    print(f"  Всего записей в ленте: {len(feed.entries)}")

    articles = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "").strip()
        source_url = entry.get("link", "")
        if not title or not source_url:
            continue

        text = find_text(entry)

        if is_excluded(title, text):
            continue
        if keywords and not matches_keywords(title, text, keywords):
            continue

        articles.append({
            "title": title,
            "source": source_name,
            "url": source_url,
            "date": entry_date_iso(entry),
            "text": text,
            "image_url": find_image_url(entry),
        })

    print(f"  Прошло фильтр: {len(articles)}")
    return articles


def main():
    all_articles = []
    seen_urls = set()

    for feed_config in RSS_FEEDS:
        for article in fetch_feed(feed_config):
            if article["url"] in seen_urls:
                continue
            seen_urls.add(article["url"])
            all_articles.append(article)

    # Свежие сверху — ленты и так отдают записи от новых к старым.
    all_articles = all_articles[:MAX_TOTAL_ARTICLES]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(all_articles, f, ensure_ascii=False, indent=2)

    print(f"Готово. Записано статей в {OUTPUT_FILE}: {len(all_articles)}")


if __name__ == "__main__":
    main()
