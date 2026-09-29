"""
Источник реальных статей для рубрики «Стиль и образ» (stil).

Берёт свежие статьи из RSS раздела «Мода» на peopletalk.ru. В отличие
от beauty_source.py, здесь заходить на страницу статьи не нужно: полный
текст уже лежит в самой RSS-ленте, в теге content:encoded (feedparser
отдаёт его как entry.content[0]['value']).

Лента вперемешку публикует и разборы гардероба/трендов (нужны рубрике),
и подборки образов конкретных знаменитостей, итоги недель моды и т.п.
(светская хроника, не нужна) — вторые отфильтровываются по заголовку,
см. TITLE_BLACKLIST/TITLE_WHITELIST.

ДЕДУПЛИКАЦИЯ БЕЗ ОТДЕЛЬНОГО ФАЙЛА — как в beauty_source.py: маркеры
ARTICLE_MARKER_PREFIX + url добавляются в posted_rubrics.json.
"""
import re

import feedparser

FEED_URL = "https://peopletalk.ru/category/fashion/feed/"

ARTICLE_MARKER_PREFIX = "article_used:stil:"

MIN_TEXT_CHARS = 400

# Подборки образов знаменитостей, итоги недель моды, светская хроника —
# не разбор гардероба для читательницы, исключаем по заголовку.
TITLE_BLACKLIST = re.compile(
    r"лучших образов|итоги недели моды|неделя моды в|показ[а-я]*\s|"
    r"красн[а-я]+ дорожк|появил[а-я]+ в образе|вышл[а-я]+ в свет",
    re.IGNORECASE,
)

# Разборы советов/трендов/гардероба — то, что реально нужно рубрике.
# Заголовок должен пройти И блэклист (не быть про звёзд), И один из
# этих признаков (быть советом/разбором тренда).
TITLE_WHITELIST = re.compile(
    r"совет стилиста|тренд|гардероб|как носить|как сочетать|капсул|"
    r"стиль[ае]?\b|образ[а-я]* дня|базов[а-я]+ вещ",
    re.IGNORECASE,
)


def _extract_image(html: str):
    """У peopletalk.ru картинки грузятся лениво: в src — заглушка
    (clear-podcast.png), реальный адрес — в data-src. Берём его."""
    match = re.search(r'data-src="([^"]+\.(?:jpg|jpeg|png|webp))"', html or "", re.IGNORECASE)
    return match.group(1) if match else None


def _clean_text(html: str) -> str:
    """HTML из content:encoded -> обычный текст с MAX-разметкой
    (**подзаголовки**, • для списков), без картинок и служебных блоков.

    Галереи и подписи к фото убираются вместе с дисклеймером про
    запрет Instagram в РФ — он нужен только при упоминании самой
    площадки, а мы эти подписи не публикуем."""
    html = re.sub(r"<figure.*?</figure>", "", html, flags=re.DOTALL)
    html = re.sub(r"<hr\s*/?>.*?Instagram запрещен.*?</p>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"<p>\s*Запись <a.*?</p>", "", html, flags=re.DOTALL)  # автоприписка WordPress

    html = re.sub(r"<h([23])[^>]*>(.*?)</h\1>", r"\n\n**\2**\n\n", html, flags=re.DOTALL)
    html = re.sub(r"<li[^>]*>(.*?)</li>", r"• \1\n", html, flags=re.DOTALL)
    html = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n\n", html, flags=re.DOTALL)
    html = re.sub(r"<[^>]+>", "", html)  # остальные теги (b, em, span и т.п.)
    html = html.replace("&nbsp;", " ")
    html = re.sub(r"[ \t]{2,}", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def find_new_article(already_used: set):
    try:
        feed = feedparser.parse(FEED_URL, request_headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        print(f"Стиль: ошибка чтения ленты {FEED_URL} — {e}")
        return None

    for entry in feed.entries[:15]:
        url = entry.get("link")
        title = entry.get("title", "").strip()
        if not url or not title:
            continue
        if ARTICLE_MARKER_PREFIX + url in already_used:
            continue
        if TITLE_BLACKLIST.search(title):
            continue
        if not TITLE_WHITELIST.search(title):
            continue

        raw_html = ""
        if entry.get("content"):
            raw_html = entry.content[0].get("value", "")
        if not raw_html:
            raw_html = entry.get("description", "")

        image_url = _extract_image(raw_html)
        text = _clean_text(raw_html)

        if len(text) < MIN_TEXT_CHARS:
            print(f"Стиль: текст статьи {url} короче {MIN_TEXT_CHARS} символов, пропускаю")
            continue

        return {"url": url, "title": title, "text": text, "image_url": image_url}

    return None
