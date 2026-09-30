"""
Источник реальных статей для рубрики «Красота и уход» (krasota).

Берёт свежие статьи из RSS-лент трёх разделов сайта beautyinsider.ru:
уход за лицом, волосами и телом. Текст публикуется ДОСЛОВНО, без
обработки YandexGPT.

ВАЖНО: RSS beautyinsider.ru отдаёт только обрезанный анонс статьи
(description заканчивается на «[…]»), поэтому полный текст
дополнительно забирается прямо со страницы статьи по её ссылке из
ленты (см. _extract_full_text). Если это по какой-то причине не
получилось (сайт изменил вёрстку, страница недоступна и т.п.) —
статья пропускается, вызывающий код сам возьмёт следующую или
переключится на старый сценарий с YandexGPT.

Файл position rubric_post_to_max.py должен положить рядом, в корень
репозитория — см. requirements.txt: нужны feedparser и beautifulsoup4
(оба уже указаны).

ДЕДУПЛИКАЦИЯ БЕЗ ОТДЕЛЬНОГО ФАЙЛА: уже опубликованные статьи
помечаются строками вида "article_used:krasota:<url>", которые
rubric_post_to_max.py добавляет в тот же posted_rubrics.json, что и
так пишется после каждой публикации (там это просто дополнительные
элементы множества, наравне с датой-ключами рубрик).

ИЗМЕНЕНИЯ: (1) стоп-маркеры ("Поделиться", "Комментарии" и т.п.) теперь
срабатывают только на короткие служебные строки или на строки, которые с
них начинаются, а не на любой абзац, где встретилось такое слово —
раньше из-за этого статьи обрезались и отсеивались как «короче 400
символов»; (2) в лог печатается статус каждой ленты, каждого кандидата и
причина отсева.
"""
import re

import feedparser
import requests
from bs4 import BeautifulSoup

FEEDS = [
    "https://www.beautyinsider.ru/category/facial-care/feed/",
    "https://www.beautyinsider.ru/category/hair/feed/",
    "https://www.beautyinsider.ru/category/body-care/feed/",
]

ARTICLE_MARKER_PREFIX = "article_used:krasota:"

# Если со страницы вытащилось меньше символов — считаем экстракцию
# неудачной (значит, вёрстка страницы не совпала с ожидаемой) и
# пропускаем статью, а не публикуем огрызок.
MIN_TEXT_CHARS = 400

# Тесты и видеообзоры плохо ложатся в формат текстового поста без адаптации.
TITLE_BLACKLIST = re.compile(r"\bтест\b|видеообзор|видеоинструкция", re.IGNORECASE)

# На странице статьи это обычно начало блока с "читайте также",
# комментариями и т.п. — как только встретили такую служебную строку,
# дальше не собираем.
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


def _extract_image_from_description(description: str):
    """В RSS beautyinsider.ru картинка статьи зашита прямо в <description>
    как <img src="https://static.beautyinsider.ru/...">, без ленивой
    загрузки — берём её напрямую, без похода на страницу."""
    match = re.search(r'src="([^"]+\.(?:jpg|jpeg|png|webp))"', description or "", re.IGNORECASE)
    return match.group(1) if match else None


def _extract_full_text(article_url: str) -> str:
    """Лучшее из возможного для типовой вёрстки WordPress: ищем контейнер
    статьи, вырезаем служебные элементы, собираем параграфы/подзаголовки/
    списки в MAX-разметку.

    Если после первого реального запуска окажется, что beautyinsider.ru
    использует другие названия классов — эту функцию нужно будет
    поправить под конкретную вёрстку (пришлите пример HTML страницы
    статьи, и я подгоню селекторы)."""
    resp = requests.get(article_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Подтверждено реальным HTML страницы статьи: контейнер — именно
    # div.single-content (WordPress-тема beautyinsider2021).
    container = soup.find("div", class_="single-content")
    if not container:
        print(f"  на странице нет div.single-content (вёрстка изменилась?) {article_url}")
        return ""

    # Виджет "Содержание" (bi-table-of-contents) заполняется JS-ом уже в
    # браузере — в сыром HTML это просто иконка-заглушка без текста.
    # Без удаления получился бы висячий подзаголовок "О чём это мы тут?"
    # без содержимого.
    toc = container.find("div", class_="bi-table-of-contents")
    if toc:
        toc.decompose()

    for tag in container.find_all(["script", "style", "aside", "nav", "form"]):
        tag.decompose()

    paragraphs = []
    for el in container.find_all(["p", "h2", "h3", "li"]):
        text = el.get_text(" ", strip=True)
        if not text or len(text) < 3:
            continue
        if _is_stop_line(text):
            break
        if el.name in ("h2", "h3"):
            paragraphs.append(f"**{text}**")
        elif el.name == "li":
            paragraphs.append(f"• {text}")
        elif el.find_parent("blockquote"):
            # Цитаты-врезки ("«Вау, это точно мои волосы?»") оформляем курсивом.
            paragraphs.append(f"_{text}_")
        else:
            paragraphs.append(text)

    return "\n\n".join(paragraphs)


def find_new_article(already_used: set):
    """already_used — множество строк из posted_rubrics.json (среди них
    маркеры ARTICLE_MARKER_PREFIX + url уже опубликованных статей).
    Возвращает dict {url, title, text, image_url} или None, если новых
    подходящих статей не нашлось ни в одной из трёх лент."""
    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})
        except Exception as e:
            print(f"Красота: ошибка чтения ленты {feed_url} — {e}")
            continue

        entries = feed.entries[:10]
        status = getattr(feed, "status", "нет")
        print(f"Красота: лента {feed_url} (HTTP {status}), записей: {len(entries)}")
        if not entries:
            print(f"  лента пуста или недоступна: {getattr(feed, 'bozo_exception', '')}")
            continue

        for entry in entries:
            url = entry.get("link")
            title = entry.get("title", "").strip()
            if not url or not title:
                continue
            print(f"Красота: кандидат «{title}»")
            if ARTICLE_MARKER_PREFIX + url in already_used:
                print("  отсеян: уже публиковалась")
                continue
            if TITLE_BLACKLIST.search(title):
                print("  отсеян: чёрный список")
                continue

            try:
                text = _extract_full_text(url)
            except Exception as e:
                print(f"  отсеян: не удалось получить текст статьи {url} — {e}")
                continue

            if len(text) < MIN_TEXT_CHARS:
                print(f"  отсеян: текст {len(text)} симв., короче {MIN_TEXT_CHARS} ({url})")
                continue

            image_url = _extract_image_from_description(entry.get("description", ""))
            print(f"  ВЫБРАНА: {url} ({len(text)} симв.)")
            return {"url": url, "title": title, "text": text, "image_url": image_url}

    print("Красота: подходящих новых статей не нашлось")
    return None
