import requests
import time

URL = "https://api.gdeltproject.org/api/v2/doc/doc"

query = """
(singer OR actress OR actor OR celebrity OR musician OR
"show business" OR entertainment OR television OR cinema)
sourcelang:russian
"""

params = {
    "query": query,
    "mode": "artlist",
    "format": "json",
    "maxrecords": 50,
    "timespan": "24h",
    "sort": "datedesc",
}

print("Запрашиваем GDELT...")
print("Ищем русскоязычные новости шоу-бизнеса за последние 24 часа.")
print()

try:
    response = requests.get(
        URL,
        params=params,
        timeout=60,
    )

    print("HTTP:", response.status_code)

    response.raise_for_status()

    data = response.json()
    articles = data.get("articles", [])

    print(f"Найдено новостей: {len(articles)}")
    print()

    for number, article in enumerate(articles, 1):
        print(f"#{number}")
        print("Дата:", article.get("seendate"))
        print("Заголовок:", article.get("title"))
        print("Источник:", article.get("domain"))
        print("URL:", article.get("url"))
        print("-" * 70)

except Exception as e:
    print("ОШИБКА:")
    print(e)
