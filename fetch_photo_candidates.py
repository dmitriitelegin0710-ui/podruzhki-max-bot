"""
Скачивает фото-кандидатов под каждую рубрику из podruzhki-max-bot сразу из
трёх бесплатных стоковых сервисов со свободной лицензией — Pexels, Pixabay
и Unsplash — чтобы потом вручную отобрать из них лучшие штук 50 на тему и
уже их закоммитить в photos/<rubric_key>/ в репозитории бота.

Это НЕ поиск по интернету (Google Images и т.п.) — там нет гарантии
свободной лицензии, а сам скрапинг результатов поиска запрещён условиями
использования поисковиков. Три сервиса ниже официально предоставляют API
именно для скачивания свободных для использования фото.

--- Настройка ---
Перед запуском задайте ключи через переменные окружения (не вписывайте их
в сам файл):
    export PEXELS_API_KEY=...
    export PIXABAY_API_KEY=...
    export UNSPLASH_ACCESS_KEY=...
Если какого-то ключа нет — соответствующий сервис просто пропускается
(остальные два продолжат работать).

--- Установка зависимостей ---
    pip install requests --break-system-packages

--- Запуск ---
    python fetch_photo_candidates.py
Или только для одной рубрики:
    python fetch_photo_candidates.py --rubric goroskop
Задать своё число фото на тему (по умолчанию 50, из расчёта поровну между
сервисами, где ключ есть):
    python fetch_photo_candidates.py --per-topic 30

Результат — папки candidates/<rubric_key>/, откуда потом переносите
руками отобранные фото в photos/<rubric_key>/ в репозитории.
"""
import argparse
import hashlib
import os
import time
from pathlib import Path

import requests

OUTPUT_DIR = Path("candidates")

PEXELS_API_KEY = os.environ.get("xN4nYiZOUCV3r8m9pulH68xFUAqHzoOHiSNVwLsLBdillEpigA3NuZcr")
PIXABAY_API_KEY = os.environ.get("57280474-2c672521e96c8a1217f3c1b56")
UNSPLASH_ACCESS_KEY = os.environ.get("v97bPrtPBAM1s9WBqR1rfOoO-n6fiF9lj6086AzguEg")

# Английские поисковые запросы под каждую рубрику. Для рубрик с
# photo_keywords_by_weekday в rubrics.json (сейчас — только utro_privet)
# запросы объединены в один общий список: конкретный день недели тут не
# важен, вы всё равно потом сами раскидаете отобранные фото по дням.
RUBRIC_QUERIES = {
    "utro_privet": [
        "morning coffee woman cozy", "morning breakfast sunny window",
        "morning tea cup cozy", "woman smiling morning light",
        "cozy blanket coffee morning",
    ],
    "goroskop": [
        "zodiac astrology mystic", "night sky stars woman",
        "crystal ball mystic", "moon phases mystic aesthetic",
    ],
    "zozh": [
        "healthy lifestyle woman", "morning stretching yoga",
        "fresh vegetables healthy food", "woman drinking water glass",
    ],
    "recept": [
        "healthy breakfast food photography", "cooking kitchen top view",
        "fresh ingredients cooking", "homemade meal table",
    ],
    "layfhak": [
        "cozy home organization", "storage boxes tidy home",
        "cleaning home tips", "minimalist tidy apartment",
    ],
    "psy_otnosheniya": [
        "woman thinking relationship", "couple emotional conversation",
        "woman looking window thoughtful", "friends talking cafe",
    ],
    "test": [
        "woman thinking quiz", "woman notebook writing thoughtful",
        "puzzle question mind",
    ],
    "ezoterika": [
        "tarot cards reading", "palmistry hand reading",
        "candles mystic ritual", "numerology mystic symbols",
    ],
    "mama_rebenok": [
        "mother child cozy home", "mother child playing",
        "mother reading child book", "family cozy morning",
    ],
    "finansy": [
        "woman budget planning notebook", "woman calculator finance",
        "piggy bank savings", "woman laptop finance planning",
    ],
    "stil": [
        "fashion outfit woman style", "woman wardrobe clothes",
        "street style woman fashion", "woman fashion accessories",
    ],
    "krasota": [
        "skincare beauty routine woman", "woman face cream skincare",
        "haircare beauty routine", "spa relaxation beauty",
    ],
    "istoriya_zhenshiny": [
        "confident woman sunset silhouette", "strong woman portrait",
        "woman achievement success", "woman looking forward confident",
    ],
    "test_dnya": [
        "woman quiz test thinking cozy", "woman notebook thinking",
    ],
    "narodnaya_mudrost": [
        "folk wisdom rustic still life", "grandmother tradition cozy",
        "countryside rustic aesthetic",
    ],
    "vecherniy_ritual": [
        "evening relaxation candle tea", "cozy evening reading book",
        "bath relaxation evening", "woman meditation calm evening",
    ],
}

# per_page у Pexels/Pixabay max 80, у Unsplash max 30 — берём с запасом,
# чтобы после дедупликации на тему хватило.
RESULTS_PER_QUERY = 15


def slugify_query(query: str) -> str:
    return query.replace(" ", "_").replace(",", "")


def file_hash_name(source: str, url: str, ext: str = "jpg") -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"{source}_{digest}.{ext}"


def download(url: str, dest: Path) -> bool:
    if dest.exists():
        return True
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return True
    except Exception as e:
        print(f"    ошибка скачивания {url} — {e}")
        return False


def fetch_pexels(query: str, count: int) -> list:
    if not PEXELS_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": count, "orientation": "portrait"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=20,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos") or []
        return [p["src"]["large"] for p in photos]
    except Exception as e:
        print(f"  Pexels: ошибка запроса '{query}' — {e}")
        return []


def fetch_pixabay(query: str, count: int) -> list:
    if not PIXABAY_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": PIXABAY_API_KEY,
                "q": query,
                "image_type": "photo",
                "orientation": "vertical",
                "per_page": max(count, 3),  # у Pixabay минимум 3
            },
            timeout=20,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits") or []
        return [h["largeImageURL"] for h in hits[:count]]
    except Exception as e:
        print(f"  Pixabay: ошибка запроса '{query}' — {e}")
        return []


def fetch_unsplash(query: str, count: int) -> list:
    if not UNSPLASH_ACCESS_KEY:
        return []
    try:
        resp = requests.get(
            "https://api.unsplash.com/search/photos",
            params={
                "query": query,
                "per_page": min(count, 30),
                "orientation": "portrait",
            },
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            timeout=20,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        return [r["urls"]["regular"] for r in results]
    except Exception as e:
        print(f"  Unsplash: ошибка запроса '{query}' — {e}")
        return []


def process_rubric(rubric_key: str, queries: list, per_topic: int) -> None:
    rubric_dir = OUTPUT_DIR / rubric_key
    rubric_dir.mkdir(parents=True, exist_ok=True)

    active_sources = [
        name for name, key in (
            ("pexels", PEXELS_API_KEY),
            ("pixabay", PIXABAY_API_KEY),
            ("unsplash", UNSPLASH_ACCESS_KEY),
        ) if key
    ]
    if not active_sources:
        print("Ни одного API-ключа не задано — нечего скачивать. "
              "Задайте хотя бы один из PEXELS_API_KEY / PIXABAY_API_KEY / UNSPLASH_ACCESS_KEY.")
        return

    per_query_per_source = max(2, per_topic // (len(queries) * len(active_sources)) + 1)

    print(f"\n=== Рубрика: {rubric_key} (источники: {', '.join(active_sources)}) ===")

    downloaded = 0
    for query in queries:
        if downloaded >= per_topic:
            break
        print(f"  Запрос: '{query}'")

        urls = []
        if "pexels" in active_sources:
            urls += [("pexels", u) for u in fetch_pexels(query, per_query_per_source)]
            time.sleep(0.3)
        if "pixabay" in active_sources:
            urls += [("pixabay", u) for u in fetch_pixabay(query, per_query_per_source)]
            time.sleep(0.3)
        if "unsplash" in active_sources:
            urls += [("unsplash", u) for u in fetch_unsplash(query, per_query_per_source)]
            time.sleep(0.3)

        for source, url in urls:
            if downloaded >= per_topic:
                break
            filename = file_hash_name(source, url)
            dest = rubric_dir / filename
            if download(url, dest):
                downloaded += 1
                print(f"    [{downloaded}/{per_topic}] {filename}")

    print(f"  Итого скачано для {rubric_key}: {downloaded}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", help="Обработать только одну рубрику (её key)")
    parser.add_argument("--per-topic", type=int, default=50, help="Сколько фото скачать на тему (по умолчанию 50)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    if args.rubric:
        queries = RUBRIC_QUERIES.get(args.rubric)
        if not queries:
            print(f"Рубрика '{args.rubric}' не найдена в RUBRIC_QUERIES. "
                  f"Доступные: {', '.join(RUBRIC_QUERIES.keys())}")
            return
        process_rubric(args.rubric, queries, args.per_topic)
    else:
        for rubric_key, queries in RUBRIC_QUERIES.items():
            process_rubric(rubric_key, queries, args.per_topic)

    print(f"\nГотово. Кандидаты лежат в папке '{OUTPUT_DIR}/'. "
          "Просмотрите их и перенесите лучшие в photos/<rubric_key>/ в репозитории бота.")


if __name__ == "__main__":
    main()
