"""
Источник реальных статей для рубрики «Стиль и образ» (stil).

Берёт свежие статьи из RSS раздела «Мода» на peopletalk.ru. Полный текст
статьи уже лежит в самой RSS-ленте, в теге content:encoded (feedparser
отдаёт его как entry.content[0]['value']) — это чистый фрагмент текста
поста, БЕЗ обёртки страницы (рекламы, сайдбара, тег-навигации), поэтому
заходить на страницу не нужно.

Лента вперемешку публикует и разборы гардероба/трендов (нужны рубрике),
и подборки образов конкретных знаменитостей, итоги недель моды и т.п.
(светская хроника, не нужна) — вторые отфильтровываются по заголовку,
см. TITLE_BLACKLIST/TITLE_WHITELIST.

Фотогалереи в статьях (<figure class="wp-block-gallery">) убираются
целиком вместе с подписями вида "Фото: @account (Instagram*)" — эти
подписи не несут полезного текста, только кредит фото. Заодно это
убирает все упоминания Instagram, так что стоящую рядом по закону РФ
сноску про признание Meta экстремистской организацией тоже можно не
показывать: раз ни одного упоминания площадки не осталось, показывать
её было бы не нужно и вводило бы читателя в заблуждение.

ДЕДУПЛИКАЦИЯ БЕЗ ОТДЕЛЬНОГО ФАЙЛА — как в beauty_source.py: маркеры
ARTICLE_MARKER_PREFIX + url добавляются в posted_rubrics.json.

ИЗМЕНЕНИЯ: (1) стоп-маркеры ("Поделиться", "Комментарии" и т.п.) теперь
срабатывают только на короткие служебные строки или на строки, которые с
них начинаются, а не на любой абзац, где встретилось такое слово;
(2) белый список заголовков расширен; (3) в лог печатается причина
отсева каждой статьи.
"""
import re

import feedparser
from bs4 import BeautifulSoup

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
    r"стиль[ае]?\b|образ[а-я]* дня|базов[а-я]+ вещ|"
    r"осен|\bлук|сочетани|\bбаз[аыу]\b|модн|что надеть|с чем носить|"
    r"must.?have|маст.?хэв",
    re.IGNORECASE,
)

# Заглушки лоадера ("ленивая загрузка") вместо настоящей картинки —
# встречаются в src, пока data-src ещё не подгрузился в браузере.
PLACEHOLDER_IMAGE_MARKERS = ("clear-podcast", "clear-content", "clear-small", "clear.png")

STOP_MARKERS = ["Читайте также", "Поделиться", "Похожие статьи", "Комментарии"]


def _is_stop_line(text: str) -> bool:
    """Служебная строка в конце статьи: либо начинается со стоп-маркера
    ("Читайте также: ..."), либо это короткая строка, где маркер встречается
    (например, просто "Поделиться"). Обычный абзац, в котором случайно
    встретилось слово «комментарии», стоп-строкой НЕ считается."""
    lowered = text.lower()
    for marker in STOP_MARKERS:
        m = marker.lower()
        if lowered.startswith(m):
            return True
        if len(text) < 60 and m in lowered:
            return True
    return False


def _parse_article_html(raw_html: str):
    """Возвращает (text, image_url) из HTML content:encoded статьи.
    Фрагмент из RSS — это сам текст поста без обёртки страницы, поэтому
    парсим его целиком, без поиска отдельного контейнера."""
    soup = BeautifulSoup(raw_html, "html.parser")

    # Настоящую картинку берём ДО того, как уберём все <figure> — иначе
    # она уйдёт вместе с ними.
    image_url = None
    first_img = soup.find("img")
    if first_img:
        candidate = first_img.get("data-src") or first_img.get("src")
        if candidate and not any(marker in candidate for marker in PLACEHOLDER_IMAGE_MARKERS):
            image_url = candidate

    # Одиночные фото и фотогалереи убираем целиком — их подписи это
    # только кредит вида "Фото: @account (Instagram*)", без полезного
    # текста (см. пояснение в шапке файла).
    for fig in soup.find_all("figure"):
        fig.decompose()

    for tag in soup.find_all(["script", "style", "nav", "aside", "form", "ins"]):
        tag.decompose()

    paragraphs = []
    for el in soup.find_all(["p", "h2", "h3", "li"]):
        text = el.get_text(" ", strip=True)
        if not text or len(text) < 3:
            continue
        if "instagram" in text.lower():
            continue
        if _is_stop_line(text):
            break
        if el.name in ("h2", "h3"):
            paragraphs.append(f"**{text}**")
        elif el.name == "li":
            paragraphs.append(f"• {text}")
        else:
            paragraphs.append(text)

    return "\n\n".join(paragraphs), image_url


def _norm_url(url: str) -> str:
    """Приводит ссылку к единому виду (без http/https, www, параметров,
    якоря и хвостового слэша), чтобы одна и та же статья не считалась
    новой из-за мелкого отличия в написании ссылки."""
    url = url.strip().lower()
    url = re.sub(r"^https?://(www\.)?", "", url)
    url = url.split("#")[0].split("?")[0]
    return url.rstrip("/")


def _image_from_entry(entry):
    """Запасные места, где RSS может хранить картинку статьи, если в самом
    тексте её нет или там только заглушка."""
    for key in ("media_content", "media_thumbnail"):
        items = entry.get(key) or []
        if items and items[0].get("url"):
            return items[0]["url"]
    for link in entry.get("links", []) or []:
        if str(link.get("type", "")).startswith("image") and link.get("href"):
            return link["href"]
    for enc in entry.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image") and enc.get("href"):
            return enc["href"]
    return None


def find_new_article(already_used: set):
    try:
        feed = feedparser.parse(FEED_URL, request_headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        print(f"Стиль: ошибка чтения ленты {FEED_URL} — {e}")
        return None

    entries = feed.entries[:15]
    status = getattr(feed, "status", "нет")
    print(f"Стиль: лента прочитана (HTTP {status}), записей: {len(entries)}")
    if not entries:
        print(f"Стиль: лента пуста или недоступна: {getattr(feed, 'bozo_exception', '')}")
        return None

    used_norm = {
        _norm_url(s[len(ARTICLE_MARKER_PREFIX):])
        for s in already_used
        if s.startswith(ARTICLE_MARKER_PREFIX)
    }
    print(f"Стиль: в журнале уже отмечено статей: {len(used_norm)}")

    for entry in entries:
        url = entry.get("link")
        title = entry.get("title", "").strip()
        if not url or not title:
            continue
        print(f"Стиль: кандидат «{title}»")
        if _norm_url(url) in used_norm:
            print("  отсеян: уже публиковалась")
            continue
        if TITLE_BLACKLIST.search(title):
            print("  отсеян: чёрный список")
            continue
        if not TITLE_WHITELIST.search(title):
            print("  отсеян: нет в белом списке")
            continue

        raw_html = ""
        if entry.get("content"):
            raw_html = entry.content[0].get("value", "")
        if not raw_html:
            raw_html = entry.get("description", "")

        text, image_url = _parse_article_html(raw_html)

        if len(text) < MIN_TEXT_CHARS:
            print(f"  отсеян: текст {len(text)} симв., короче {MIN_TEXT_CHARS} ({url})")
            continue

        if not image_url:
            image_url = _image_from_entry(entry)
            print(f"  картинка из текста не найдена, из полей RSS: {image_url}")
        else:
            print(f"  картинка из текста статьи: {image_url}")

        print(f"  ВЫБРАНА: {url} ({len(text)} симв.)")
        return {"url": url, "title": title, "text": text, "image_url": image_url}

    print("Стиль: подходящих новых статей не нашлось")
    return None
