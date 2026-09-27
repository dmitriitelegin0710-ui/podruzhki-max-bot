"""
Изолированная проверка photo_bank.py — НЕ публикует ничего в MAX/Telegram,
только проверяет подключение к бакету Yandex Object Storage и печатает,
что происходит для каждой рубрики из rubrics.json: есть подключение и есть
фото / есть подключение но фото ещё нет / подключения нет вовсе.

Запуск: python test_photo_bank.py
"""
import json

import photo_bank

with open("rubrics.json", encoding="utf-8") as f:
    rubrics = json.load(f)["rubrics"]

print("=" * 60)
print("ПРОВЕРКА СВОЕЙ БАЗЫ ФОТО (Yandex Object Storage)")
print("=" * 60)

for rubric in rubrics:
    key = rubric["key"]
    if rubric.get("media_type") == "video":
        print(f"{key}: пропускаю — рубрика видео, свою базу фото не использует")
        continue

    url = photo_bank.get_own_photo(key, weekday_index=0)
    if url:
        print(f"{key}: ЕСТЬ ФОТО -> {url[:90]}...")
    else:
        print(f"{key}: фото не получено (см. сообщение выше от photo_bank)")
    print("-" * 60)

print("Готово.")
