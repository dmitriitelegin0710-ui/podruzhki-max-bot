import json
import time
import requests
from bs4 import BeautifulSoup

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
OUTPUT_FILE = "news/articles.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

QUERIES = [
    "celebrity sourcelang:russian",
    "entertainment sourcelang:russian",
]


def search_gdelt(query):
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 10,
        "timespan": "24h",
        "sort": "datedesc",
    }

    for attempt in range(1, 3):
        print(f"GDELT: {query} — попытка {attempt}")

        try:
            response = requests.get(
                GDELT_URL,
                params=params,
                headers=HEADERS,
                timeout=30
            )

            print("HTTP:", response.status_code)

            if response.status_code == 429:
                print("429 — ждём 45 секунд...")
                time.sleep(45)
                continue

            response.raise_for_status()

            return response.json().get("articles", [])

        except Exception as e:
            print("Ошибка:", e)

            if attempt < 2:
                time.sleep(15)

    return []


def extract_text(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15
        )

        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

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

        return "\n".join(unique)

    except Exception as e:
        print("Ошибка загрузки:", e)
        return ""


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

    text = extract_text(url)

    print("Текст:", len(text), "символов")

    if len(text) >= 300:
        result.append({
            "title": title,
            "source": source,
            "url": url,
            "date": date,
            "text": text
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
