import json
import time
import requests
from bs4 import BeautifulSoup

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
OUTPUT_FILE = "news/articles.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# Поисковые запросы GDELT.
# Термины на английском, sourcelang:russian —
# русскоязычные оригинальные источники.
QUERIES = [
    "celebrity sourcelang:russian",
    "singer sourcelang:russian",
    "actress sourcelang:russian",
    "actor sourcelang:russian",
    "musician sourcelang:russian",
    "television sourcelang:russian",
    "cinema sourcelang:russian",
]


def gdelt_search(query):
    """Получить новости из GDELT с защитой от 429."""

    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 10,
        "timespan": "24h",
        "sort": "datedesc",
    }

    for attempt in range(1, 4):
        print(f"  Запрос: {query}")
        print(f"  Попытка: {attempt}/3")

        try:
            response = requests.get(
                GDELT_URL,
                params=params,
                headers=HEADERS,
                timeout=60
            )

            print(f"  HTTP: {response.status_code}")

            if response.status_code == 429:
                if attempt < 3:
                    print("  GDELT ограничил частоту. Ждём 30 секунд...")
                    time.sleep(30)
                    continue

                print("  Не удалось получить ответ после 3 попыток.")
                return []

            response.raise_for_status()

            data = response.json()
            return data.get("articles", [])

        except requests.exceptions.Timeout:
            print("  Таймаут.")

            if attempt < 3:
                time.sleep(15)

        except requests.exceptions.RequestException as e:
            print(f"  Ошибка: {e}")

            if attempt < 3:
                time.sleep(15)

        except ValueError:
            print("  GDELT вернул некорректный JSON.")
            return []

    return []


def extract_text(url):
    """Скачать страницу и извлечь текст статьи."""

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Удаляем элементы, которые почти никогда
        # не являются текстом самой новости.
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

        for paragraph in soup.find_all("p"):
            text = paragraph.get_text(
                " ",
                strip=True
            )

            if len(text) >= 40:
                paragraphs.append(text)

        # Удаляем одинаковые абзацы.
        unique_paragraphs = []
        seen = set()

        for paragraph in paragraphs:
            if paragraph not in seen:
                seen.add(paragraph)
                unique_paragraphs.append(paragraph)

        return "\n".join(unique_paragraphs)

    except Exception as e:
        print(f"  Ошибка загрузки статьи: {e}")
        return ""


print("=" * 70)
print("GDELT — ПОИСК НОВОСТЕЙ ШОУ-БИЗНЕСА")
print("=" * 70)
print()

all_articles = {}

# ---------------------------------------------------------
# 1. Получаем кандидатов из GDELT
# ---------------------------------------------------------

for query in QUERIES:

    articles = gdelt_search(query)

    print(f"  Получено: {len(articles)}")
    print()

    for article in articles:

        url = article.get("url")

        if not url:
            continue

        # Дубликаты убираем сразу по URL.
        all_articles[url] = article

    # Небольшая пауза между запросами.
    time.sleep(5)


print("=" * 70)
print(f"УНИКАЛЬНЫХ КАНДИДАТОВ: {len(all_articles)}")
print("=" * 70)
print()


# ---------------------------------------------------------
# 2. Скачиваем полный текст
# ---------------------------------------------------------

result = []

articles_list = list(all_articles.values())

for number, article in enumerate(articles_list, 1):

    title = article.get("title", "")
    url = article.get("url", "")
    domain = article.get("domain", "")
    date = article.get("seendate", "")

    print(
        f"[{number}/{len(articles_list)}] "
        f"{title}"
    )

    print("Источник:", domain)

    if not url:
        print("Нет URL — пропускаем.")
        print()
        continue

    text = extract_text(url)

    print(
        "Текст:",
        len(text),
        "символов"
    )

    # Очень короткие страницы не отправляем дальше.
    if len(text) < 300:
        print(
            "Пропускаем — "
            "текста недостаточно."
        )
        print()
        continue

    result.append({
        "title": title,
        "source": domain,
        "url": url,
        "date": date,
        "text": text
    })

    print("Сохранено.")
    print()

    # Не долбим сайты слишком быстро.
    time.sleep(1)


# ---------------------------------------------------------
# 3. Сохраняем результат
# ---------------------------------------------------------

with open(
    OUTPUT_FILE,
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        result,
        file,
        ensure_ascii=False,
        indent=2
    )


print("=" * 70)
print("ГОТОВО")
print("=" * 70)
print("Уникальных кандидатов:", len(all_articles))
print("Статей с текстом:", len(result))
print("Файл:", OUTPUT_FILE)
print("=" * 70)
