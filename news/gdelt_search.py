import requests

URL = "https://api.gdeltproject.org/api/v2/doc/doc"

params = {
    "query": "(актриса OR певица OR актер OR звезда) sourcelang:russian",
    "mode": "artlist",
    "format": "json",
    "maxrecords": 20,
    "timespan": "24h",
    "sort": "datedesc",
}

response = requests.get(URL, params=params, timeout=30)
response.raise_for_status()

data = response.json()

articles = data.get("articles", [])

print(f"Найдено новостей: {len(articles)}")
print()

for article in articles:
    print(article.get("seendate"))
    print(article.get("title"))
    print(article.get("domain"))
    print(article.get("url"))
    print("-" * 60)
