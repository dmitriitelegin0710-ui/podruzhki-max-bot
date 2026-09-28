"""
kino_source.py — подбор фильма/сериала для рубрики «Что посмотреть вечером».
Источник данных: https://kinopoiskapiunofficial.tech (нужен ключ X-API-KEY).

Подключение в rubric_post_to_max.py:
    from kino_source import build_kino_post
    data = build_kino_post()          # dict с фактами, промптом, фото, трейлером
    text = yandexgpt(data["prompt"])  # ваша функция вызова YandexGPT
    # отправить text + data["photo_path"] (или data["video_path"]) в MAX
"""
import json
import os
import random
import subprocess
import time
import datetime as dt

import requests

API = "https://kinopoiskapiunofficial.tech/api"
HEADERS = {"X-API-KEY": os.environ.get("KINOPOISK_API_KEY", ""),
           "Content-Type": "application/json"}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USED_FILE = os.path.join(BASE_DIR, "used_movies.json")
MEDIA_DIR = os.path.join(BASE_DIR, "kino_media")

# ID жанров. ОБЯЗАТЕЛЬНО сверьте с ответом get_genres() — см. ниже.
GENRES = {"мелодрама": 4, "драма": 2}
MIN_RATING = 6.8
MIN_YEAR = 2000


def _get(path, params=None, retries=3):
    for attempt in range(retries):
        r = requests.get(f"{API}{path}", headers=HEADERS, params=params, timeout=20)
        if r.status_code == 429:          # превышен лимит запросов
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Kinopoisk API: слишком много запросов (429)")


def get_genres():
    """Разово вызовите и напечатайте, чтобы проверить ID жанров."""
    return _get("/v2.2/films/filters")["genres"]


# ---------- учёт уже показанного ----------
def _load_used():
    try:
        with open(USED_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_used(used):
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(used), f)


# ---------- подбор ----------
def pick_candidate(kind):
    """kind: 'film' или 'series'. Возвращает kinopoiskId непоказанного тайтла."""
    used = _load_used()
    type_ = "FILM" if kind == "film" else "TV_SERIES"
    for _ in range(8):  # несколько попыток на разных страницах/жанрах
        params = {
            "type": type_,
            "order": "RATING",
            "ratingFrom": MIN_RATING,
            "ratingTo": 10,
            "yearFrom": MIN_YEAR,
            "genres": random.choice(list(GENRES.values())),
            "page": random.randint(1, 5),
        }
        items = _get("/v2.2/films", params).get("items", [])
        fresh = [i for i in items
                 if i["kinopoiskId"] not in used and i.get("nameRu") and i.get("posterUrl")]
        if fresh:
            return random.choice(fresh)["kinopoiskId"]
    raise RuntimeError("Не нашлось непоказанных фильмов — расширьте фильтры")


# ---------- данные ----------
def get_details(film_id):
    d = _get(f"/v2.2/films/{film_id}")
    facts = {
        "id": film_id,
        "name": d.get("nameRu") or d.get("nameOriginal"),
        "year": d.get("year"),
        "type": d.get("type"),  # FILM / TV_SERIES / MINI_SERIES ...
        "rating": d.get("ratingKinopoisk"),
        "genres": [g["genre"] for g in d.get("genres", [])],
        "countries": [c["country"] for c in d.get("countries", [])],
        "length": d.get("filmLength"),
        "description": d.get("description") or d.get("shortDescription") or "",
        "poster": d.get("posterUrl"),
        "url": d.get("webUrl"),
        "seasons": None,
        "episodes": None,
    }
    if facts["type"] in ("TV_SERIES", "MINI_SERIES"):
        try:
            s = _get(f"/v2.2/films/{film_id}/seasons")
            facts["seasons"] = s.get("total")
            facts["episodes"] = sum(len(i.get("episodes", [])) for i in s.get("items", []))
        except requests.RequestException:
            pass
    return facts


def get_trailer_url(film_id):
    """API отдаёт только ССЫЛКИ на трейлеры (чаще YouTube), не файлы."""
    try:
        items = _get(f"/v2.2/films/{film_id}/videos").get("items", [])
    except requests.RequestException:
        return None
    for v in items:
        if v.get("site") == "YOUTUBE" and "рейлер" in (v.get("name") or ""):
            return v["url"]
    return next((v["url"] for v in items if v.get("site") == "YOUTUBE"), None)


def get_stills(film_id, limit=4):
    try:
        items = _get(f"/v2.2/films/{film_id}/images",
                     {"type": "STILL", "page": 1}).get("items", [])
    except requests.RequestException:
        return []
    return [i["imageUrl"] for i in items[:limit]]


def _download(url, name):
    os.makedirs(MEDIA_DIR, exist_ok=True)
    path = os.path.join(MEDIA_DIR, name)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return path


# ---------- короткое видео-тизер из кадров (нужен ffmpeg на сервере) ----------
def make_teaser_video(image_paths, out_path, sec_per_image=3):
    """Слайд-шоу из кадров фильма 1280x720 (это ваше видео, а не скачанный трейлер)."""
    if len(image_paths) < 2:
        return None
    cmd = ["ffmpeg", "-y"]
    for p in image_paths:
        cmd += ["-loop", "1", "-t", str(sec_per_image), "-i", p]
    n = len(image_paths)
    filt = "".join(
        f"[{i}:v]scale=1280:720:force_original_aspect_ratio=decrease,"
        f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25[v{i}];"
        for i in range(n))
    filt += "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[out]"
    cmd += ["-filter_complex", filt, "-map", "[out]", "-pix_fmt", "yuv420p", out_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return out_path
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


# ---------- промпт для YandexGPT ----------
def build_prompt(f):
    kind = "сериал" if f["type"] in ("TV_SERIES", "MINI_SERIES") else "фильм"
    fmt = kind
    if f["seasons"]:
        fmt += f", сезонов: {f['seasons']}, серий: {f['episodes']}"
    elif f["length"]:
        fmt += f", {f['length']} мин."
    facts = (
        f"Название: {f['name']}\nГод: {f['year']}\nФормат: {fmt}\n"
        f"Жанры: {', '.join(f['genres'])}\nСтрана: {', '.join(f['countries'])}\n"
        f"Рейтинг Кинопоиска: {f['rating']}\nОписание: {f['description']}"
    )
    return (
        "Ты ведёшь женский канал. Напиши пост-анонс по ФАКТАМ ниже. "
        "Используй только эти факты: не выдумывай актёров, сюжетные повороты и цифры.\n\n"
        f"{facts}\n\n"
        "Структура поста:\n"
        "1) Название, год, жанр и формат.\n"
        "2) Краткий сюжет в 2-3 предложениях без спойлеров.\n"
        "3) Абзац «Совет: почему стоит посмотреть» — 2-3 причины и для какого настроения подходит.\n"
        "4) «⚠️ Мини-спойлер»: только то, что прямо следует из описания; "
        "если в описании нечего раскрывать, вместо спойлера задай интригующий вопрос о сюжете.\n"
        "5) В конце вопрос читательницам: смотрели ли, какие впечатления.\n"
        "Тон тёплый, с абзацами и эмодзи, до 900 знаков."
    )


# ---------- лёгкий вариант для rubric_post_to_max.py (GitHub Actions) ----------
def build_topic_hint(f):
    """Текст для поля topic_hint рубрики: факты + структура поста.
    Общие правила стиля и длину добавляет сам generate_text()."""
    kind = "сериал" if f["type"] in ("TV_SERIES", "MINI_SERIES") else "фильм"
    fmt = kind
    if f.get("seasons"):
        fmt += f", сезонов: {f['seasons']}, серий: {f['episodes']}"
    elif f.get("length"):
        fmt += f", {f['length']} мин."
    return (
        "Напиши пост-анонс к фильму или сериалу. Используй ТОЛЬКО факты ниже: "
        "не выдумывай актёров, сюжетные повороты, цифры и награды.\n"
        f"Название: {f['name']}\nГод: {f['year']}\nФормат: {fmt}\n"
        f"Жанры: {', '.join(f['genres'])}\nСтрана: {', '.join(f['countries'])}\n"
        f"Рейтинг Кинопоиска: {f['rating']}\nОписание: {f['description']}\n"
        "Структура: 1) название, год, жанр, формат; 2) краткий сюжет в 2-3 предложениях "
        "без спойлеров; 3) «Совет: почему стоит посмотреть» — 2-3 причины и для какого "
        "настроения подходит; 4) «⚠️ Мини-спойлер» — только то, что прямо следует из "
        "описания, а если раскрывать нечего, задай интригующий вопрос о сюжете; "
        "5) в конце вопрос читательницам: смотрели ли, какие впечатления."
    )


def prepare_kino(target_date):
    """Выбирает непоказанный тайтл и возвращает факты (+ poster, trailer).
    В used_movies.json НЕ записывает — вызовите mark_used() после успешной публикации."""
    kind = "film" if target_date.day % 2 == 0 else "series"
    film_id = pick_candidate(kind)
    facts = get_details(film_id)
    facts["trailer"] = get_trailer_url(film_id)
    return facts


def mark_used(film_id):
    used = _load_used()
    used.add(film_id)
    _save_used(used)


# ---------- главная функция (полный вариант с локальным видео-тизером) ----------
def build_kino_post(kind=None, make_video=True):
    if kind is None:  # чётные дни — фильм, нечётные — сериал
        kind = "film" if dt.date.today().day % 2 == 0 else "series"
    film_id = pick_candidate(kind)
    facts = get_details(film_id)

    photo_path = _download(facts["poster"], f"{film_id}_poster.jpg")
    trailer = get_trailer_url(film_id)

    video_path = None
    if make_video:
        stills = [_download(u, f"{film_id}_still{i}.jpg")
                  for i, u in enumerate(get_stills(film_id))]
        video_path = make_teaser_video(stills, os.path.join(MEDIA_DIR, f"{film_id}_teaser.mp4"))

    used = _load_used()
    used.add(film_id)
    _save_used(used)

    return {
        "facts": facts,
        "prompt": build_prompt(facts),
        "photo_path": photo_path,
        "video_path": video_path,     # None, если ffmpeg недоступен
        "trailer_url": trailer,       # добавьте ссылкой в конец поста
    }


if __name__ == "__main__":
    print(json.dumps(get_genres(), ensure_ascii=False, indent=1))  # проверка ID жанров
