import json
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
OUTPUT_FILE = "news/articles.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

QUERIES = [
    '"шоу-бизнес" sourcelang:russian',
    "знаменитости sourcelang:russian",
    'звезды "шоу-бизнеса" sourcelang:russian',
]

# Таймаут и паузы между повторными попытками увеличены — GDELT DOC API
# нередко подвисает под нагрузкой (30 сек не всегда хватает) и иногда
# отдаёт HTTP 200 с пустым/битым телом вместо JSON. Логика ниже переживает
# оба этих случая и не молча возвращает [] после первого же сбоя.
REQUEST_TIMEOUT = 45
RETRY_BACKOFFS = [15, 30, 60]  # секунды паузы перед каждой следующей попыткой


def search_gdelt(query):
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 10,
        "timespan": "24h",
        "sort": "datedesc",
    }

    attempts = len(RETRY_BACKOFFS)

    for attempt in range(1, attempts + 1):
        print(f"GDELT: {query} — попытка {attempt}/{attempts}")

        try:
            response = requests.get(
                GDELT_URL,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            print("HTTP:", response.status_code)

            if response.status_code == 429:
                wait = RETRY_BACKOFFS[attempt - 1]
                print(f"429 — ждём {wait} секунд...")
                time.sleep(wait)
                continue

            response.raise_for_status()

            body = response.text.strip()
            if not body:
                print("Пустой ответ от GDELT (HTTP 200, но без тела).")
                if attempt < attempts:
                    wait = RETRY_BACKOFFS[attempt - 1]
                    print(f"Повтор через {wait} секунд...")
                    time.sleep(wait)
                continue

            return response.json().get("articles", [])

        except requests.exceptions.Timeout:
            print(f"Таймаут запроса (>{REQUEST_TIMEOUT} сек).")
            if attempt < attempts:
                wait = RETRY_BACKOFFS[attempt - 1]
                print(f"Повтор через {wait} секунд...")
                time.sleep(wait)

        except requests.exceptions.ConnectionError as e:
            print("Ошибка соединения:", e)
            if attempt < attempts:
                wait = RETRY_BACKOFFS[attempt - 1]
                print(f"Повтор через {wait} секунд...")
                time.sleep(wait)

        except json.JSONDecodeError:
            snippet = response.text[:200] if "response" in locals() else ""
            print(f"Не удалось распарсить JSON. Начало ответа: {snippet!r}")
            if attempt < attempts:
                wait = RETRY_BACKOFFS[attempt - 1]
                print(f"Повтор через {wait} секунд...")
                time.sleep(wait)

        except Exception as e:
            print("Ошибка:", e)
            if attempt < attempts:
                wait = RETRY_BACKOFFS[attempt - 1]
                print(f"Повтор через {wait} секунд...")
                time.sleep(wait)

    print(f"GDELT: '{query}' — не удалось получить данные после всех попыток.")
    return []


def extract_image(soup, base_url):
    """Пытается найти og:image / twitter:image статьи — реальное фото,
    относящееся к новости, а не общий стоковый снимок."""
    for prop in ["og:image", "og:image:secure_url", "twitter:image"]:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            return urljoin(base_url, tag["content"].strip())
    return None


def extract_text(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15
        )

        if response.status_code != 200:
            return "", None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        image_url = extract_image(soup, url)

        for tag in soup([
            "script",
            "style",
            "nav",
            "header",
            "footer",
            "aside",
            "form",
            "noscript",
            "svg"
        ]):
            tag.decompose()

        paragraphs = []

        for p in soup.find_all("p"):
            text = p.get_text(" ", strip=True)

            if len(text) >= 40:
                paragraphs.append(text)

        # Убираем дублирующиеся абзацы
        unique = []
        seen = set()

        for text in paragraphs:
            if text not in seen:
                seen.add(text)
                unique.append(text)

        return "\n".join(unique), image_url

    except Exception as e:
        print("Ошибка загрузки:", e)
        return "", None


print("=" * 60)
print("GDELT — БЫСТРЫЙ ТЕСТ ШОУ-БИЗНЕСА")
print("=" * 60)

all_articles = {}

for query in QUERIES:
    articles = search_gdelt(query)

    print("Получено:", len(articles))

    for article in articles:
        url = article.get("url")

        if url:
            all_articles[url] = article

    # Пауза между запросами GDELT
    time.sleep(5)


print()
print("Уникальных кандидатов:", len(all_articles))
print()

result = []

# Максимум 20 статей
articles = list(all_articles.values())[:20]

for number, article in enumerate(articles, 1):

    title = article.get("title", "")
    url = article.get("url", "")
    source = article.get("domain", "")
    date = article.get("seendate", "")

    print(f"[{number}/{len(articles)}] {title}")
    print("Источник:", source)

    text, image_url = extract_text(url)

    print("Текст:", len(text), "символов")
    print("Картинка:", image_url or "не найдена")

    if len(text) >= 300:
        result.append({
            "title": title,
            "source": source,
            "url": url,
            "date": date,
            "text": text,
            "image_url": image_url,
        })
        print("Сохранено.")
    else:
        print("Пропущено.")

    print()

    # Не загружаем сайты слишком быстро
    time.sleep(1)


with open(
    OUTPUT_FILE,
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        result,
        f,
        ensure_ascii=False,
        indent=2
    )


print("=" * 60)
print("ГОТОВО")
print("=" * 60)
print("Кандидатов:", len(all_articles))
print("Статей с текстом:", len(result))
print("Файл:", OUTPUT_FILE)
print("=" * 60)
