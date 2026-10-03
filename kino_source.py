"""
kino_source.py — подбор фильма/сериала для рубрики «Что посмотреть вечером».
Источник данных: https://kinopoiskapiunofficial.tech (нужен ключ X-API-KEY,
в GitHub Actions он передаётся через секрет KINOPOISK_API_KEY).

Как подбирается тайтл (см. pick_candidate):
  * чётные дни — фильм, нечётные — сериал;
  * происхождение чередуется: зарубежный / российский (см. ORIGIN_CYCLE);
  * жанры берутся из списка GENRES (по названиям, ID подбираются сами);
  * СНАЧАЛА новинки: идёт по ступеням TIERS — свежие тайтлы последних лет,
    затем более ранние, затем вся база. Внутри ступени список отсортирован
    по популярности (число голосов на Кинопоиске), берётся первый ещё не
    показанный тайтл;
  * показанные тайтлы записываются в used_movies.json и не повторяются;
  * ФИЛЬТР ДЛЯ ЖЕНСКОЙ АУДИТОРИИ: тайтл с жанром из GENRES_BLOCK (боевик,
    криминал, ужасы, аниме, мультфильм и т.д.) отбрасывается, даже если рядом
    указана «драма»; берутся тайтлы хотя бы с одним жанром из GENRES_ALLOW,
    а триллер — только в сочетании с драмой, мелодрамой или детективом.
    Отсеянные тайтлы с причиной печатаются в лог. См. is_suitable_genres().

Подключение в rubric_post_to_max.py (уже сделано):
    kino = kino_source.prepare_kino(now.date())
    topic_hint = kino_source.build_topic_hint(kino)
    ... после успешной публикации: kino_source.mark_used(kino["id"])

Проверка у себя:  python kino_source.py
    (выведет список жанров и покажет, что было бы выбрано, ничего не записывая)
"""
import json
import os
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

# ---------------- НАСТРОЙКИ ПОДБОРА ----------------

# Жанры — просто НАЗВАНИЯ, как на Кинопоиске (строчными буквами). ID подставляются
# автоматически из справочника API, поэтому сверять числа не нужно. Если названия
# нет в справочнике — оно пропускается с предупреждением в логе.
# Полный список доступных жанров выведет:  python kino_source.py
GENRES = ["мелодрама", "комедия", "драма", "детектив", "триллер", "семейный", "биография", "фэнтези"]

# ---- Фильтр для женской аудитории (применяется к жанрам КАЖДОГО тайтла) ----
# Поиск по жанру (GENRES) только находит кандидатов, а решает этот фильтр:
# у фильма «драма, криминал, боевик» поиск по «драме» его найдёт, а фильтр
# отбросит из-за «криминала» и «боевика».
#
# Тайтл ОТБРАСЫВАЕТСЯ, если у него есть хотя бы один из этих жанров.
GENRES_BLOCK = {
    "боевик", "криминал", "ужасы", "военный", "вестерн", "спорт", "фильм-нуар",
    "аниме", "мультфильм", "детский",
    "документальный", "реальное тв", "ток-шоу", "новости", "концерт", "музыка",
    "игра", "церемония", "короткометражка", "для взрослых",
}
# Тайтл ПОДХОДИТ, если у него есть хотя бы один из этих жанров
# (и нет ни одного из GENRES_BLOCK).
GENRES_ALLOW = {
    "мелодрама", "комедия", "драма", "детектив", "семейный",
    "биография", "мюзикл", "фэнтези",
}
# Триллер подходит только вместе с одним из этих жанров.
THRILLER_PARTNERS = {"драма", "мелодрама", "детектив"}

# Чередование происхождения по дням: "foreign" — зарубежные, "ru" — российские.
# Пропорция 50/50. Хотите чаще зарубежные — например ["foreign", "foreign", "ru"].
ORIGIN_CYCLE = ["foreign", "ru"]

# Ступени поиска — от новинок к остальным. Переходим к следующей ступени, только
# когда на текущей не осталось непоказанных тайтлов.
#   years_back — сколько лет назад считать «новинкой» (1 = этот и прошлый год;
#                None = без ограничения по году);
#   min_rating — минимальный рейтинг Кинопоиска на этой ступени
#                (у новинок оценок мало, поэтому планка ниже).
TIERS = [
    {"years_back": 1, "min_rating": 6.5},     # новинки
    {"years_back": 4, "min_rating": 6.8},     # недавние
    {"years_back": None, "min_rating": 7.0},  # вся база, лучшие
]

MAX_PAGES = 5  # сколько страниц списка (по 20 тайтлов) просматривать на ступени

# Страны, которые считаются «российскими» (для деления на ru / foreign).
RU_COUNTRIES = {"россия", "ссср"}


# ---------------- фильтр для женской аудитории ----------------
def _item_genres(item):
    """Названия жанров из элемента списка/деталей API (строчными)."""
    return {(g.get("genre") or "").strip().lower() for g in item.get("genres", []) or []}


def is_suitable_genres(genres):
    """(подходит?, причина) для набора жанров тайтла. Порядок проверок:
    1) любой жанр из GENRES_BLOCK -> отбросить;
    2) любой жанр из GENRES_ALLOW -> взять;
    3) триллер вместе с драмой/мелодрамой/детективом -> взять;
    4) иначе отбросить."""
    names = {g.strip().lower() for g in genres}
    if not names:
        return False, "жанры не указаны"
    blocked = names & GENRES_BLOCK
    if blocked:
        return False, "жанр не для канала: " + ", ".join(sorted(blocked))
    if names & GENRES_ALLOW:
        return True, ""
    if "триллер" in names and names & THRILLER_PARTNERS:
        return True, ""
    return False, "нет подходящего жанра (" + ", ".join(sorted(names)) + ")"


# ---------------- API ----------------
def _get(path, params=None, retries=3):
    for attempt in range(retries):
        r = requests.get(f"{API}{path}", headers=HEADERS, params=params, timeout=20)
        if r.status_code == 429:          # превышен лимит запросов
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Kinopoisk API: слишком много запросов (429)")


_FILTERS = None


def get_filters():
    """Справочник жанров и стран (запрашивается один раз за запуск)."""
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = _get("/v2.2/films/filters")
    return _FILTERS


def get_genres():
    return get_filters()["genres"]


def _genre_ids():
    """Названия из GENRES -> ID из справочника API."""
    by_name = {(g.get("genre") or "").strip().lower(): g["id"] for g in get_genres()}
    ids = []
    for name in GENRES:
        gid = by_name.get(name.strip().lower())
        if gid is None:
            print(f"Кино: жанр «{name}» не найден в справочнике API, пропускаю")
        else:
            ids.append(gid)
    if not ids:
        raise RuntimeError("Ни один жанр из GENRES не найден в справочнике API")
    return ids


def _country_id(name):
    for c in get_filters().get("countries", []):
        if (c.get("country") or "").strip().lower() == name.strip().lower():
            return c["id"]
    return None


# ---------------- учёт уже показанного ----------------
def _load_used():
    try:
        with open(USED_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_used(used):
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(used), f)


# ---------------- подбор ----------------
def _is_russian(item):
    names = {(c.get("country") or "").strip().lower() for c in item.get("countries", [])}
    return bool(names & RU_COUNTRIES)


def pick_candidate(kind, origin=None, skip=None):
    """Возвращает kinopoiskId непоказанного тайтла.

    kind:   'film' или 'series';
    origin: 'ru' (российские), 'foreign' (зарубежные) или None (любые);
    skip:   множество kinopoiskId, которые нужно пропустить (например,
            отсеянные проверкой жанров по подробным данным).

    Порядок: ступени TIERS сверху вниз (новинки -> ... -> вся база); внутри
    ступени — по убыванию популярности; берётся ПЕРВЫЙ непоказанный тайтл.
    Жанры чередуются."""
    used = _load_used()
    types = ["FILM"] if kind == "film" else ["TV_SERIES", "MINI_SERIES"]

    genre_ids = _genre_ids()
    shift = len(used) % len(genre_ids)
    genre_ids = genre_ids[shift:] + genre_ids[:shift]

    country_id = _country_id("Россия") if origin == "ru" else None
    this_year = dt.date.today().year
    rejected = 0

    for tier in TIERS:
        for page in range(1, MAX_PAGES + 1):
            for genre_id in genre_ids:
                for type_ in types:
                    params = {
                        "type": type_,
                        "order": "NUM_VOTE",          # самые популярные первыми
                        "ratingFrom": tier["min_rating"],
                        "ratingTo": 10,
                        "genres": genre_id,
                        "page": page,
                    }
                    if tier["years_back"] is not None:
                        params["yearFrom"] = this_year - tier["years_back"]
                    if country_id:
                        params["countries"] = country_id
                    items = _get("/v2.2/films", params).get("items", [])
                    for i in items:
                        if i["kinopoiskId"] in used:
                            continue
                        if skip and i["kinopoiskId"] in skip:
                            continue
                        if not (i.get("nameRu") and i.get("posterUrl")):
                            continue
                        # Фильтр для женской аудитории по жанрам из списка. Если
                        # API не вернул жанры в списке — проверка будет позже,
                        # по подробным данным (см. _pick_suitable).
                        item_genres = _item_genres(i)
                        if item_genres:
                            suitable, reason = is_suitable_genres(item_genres)
                            if not suitable:
                                rejected += 1
                                if rejected <= 15:
                                    print(f"Кино: отсеян «{i.get('nameRu')}» ({i.get('year')}) — {reason}")
                                continue
                        if origin == "ru" and not _is_russian(i):
                            continue
                        if origin == "foreign" and _is_russian(i):
                            continue
                        return i["kinopoiskId"]
    raise RuntimeError("Не нашлось непоказанных тайтлов — снизьте min_rating в TIERS "
                       "или увеличьте MAX_PAGES / добавьте жанры в GENRES")


def _kind_and_origin(target_date):
    """Чётные дни — фильм, нечётные — сериал; происхождение чередуется через
    каждые два дня, так что за 4 дня выходят все комбинации:
    фильм/сериал x зарубежное/российское."""
    n = target_date.toordinal()
    kind = "film" if n % 2 == 0 else "series"
    origin = ORIGIN_CYCLE[(n // 2) % len(ORIGIN_CYCLE)]
    return kind, origin


# ---------------- данные ----------------
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


# ---------------- короткое видео-тизер из кадров (нужен ffmpeg на сервере) ----------------
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


# ---------------- промпт для YandexGPT ----------------
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
        "Закончи пост после третьего пункта. НЕ задавай читательницам вопросов в конце, "
        "не спрашивай, смотрели ли они, и не проси делиться впечатлениями.\n"
        "Тон тёплый, с абзацами и эмодзи, до 900 знаков."
    )


# ---------------- лёгкий вариант для rubric_post_to_max.py (GitHub Actions) ----------------
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
        "настроения подходит. "
        "Закончи пост после третьего пункта: НЕ задавай читательницам вопросов в конце, "
        "не спрашивай, смотрели ли они, и не проси делиться впечатлениями."
    )


def _pick_suitable(kind, origin, attempts=5):
    """Выбирает тайтл и перепроверяет жанры по ПОДРОБНЫМ данным (жанры в
    списке могут быть неполными). Если не подошёл — берёт следующий."""
    skip = set()
    for _ in range(attempts):
        film_id = pick_candidate(kind, origin, skip)
        facts = get_details(film_id)
        suitable, reason = is_suitable_genres(facts["genres"])
        if suitable:
            return film_id, facts
        print(f"Кино: по подробным данным отсеян «{facts['name']}» ({facts['year']}) — {reason}")
        skip.add(film_id)
    raise RuntimeError("Не нашлось тайтла с подходящими жанрами за несколько попыток")


def prepare_kino(target_date):
    """Выбирает непоказанный тайтл и возвращает факты (+ poster, trailer).
    В used_movies.json НЕ записывает — вызовите mark_used() после успешной публикации."""
    kind, origin = _kind_and_origin(target_date)
    try:
        film_id, facts = _pick_suitable(kind, origin)
    except RuntimeError:
        # например, закончились российские — берём любые, лишь бы пост вышел
        print(f"Кино: для {kind}/{origin} ничего не нашлось, беру без учёта происхождения")
        film_id, facts = _pick_suitable(kind, None)
    print(f"Кино: выбран «{facts['name']}» ({facts['year']}), жанры: {', '.join(facts['genres'])}")
    facts["trailer"] = get_trailer_url(film_id)
    return facts


def mark_used(film_id):
    used = _load_used()
    used.add(film_id)
    _save_used(used)


# ---------------- полный вариант с локальным видео-тизером (не используется в GitHub Actions) ----------------
def build_kino_post(kind=None, make_video=True):
    d_kind, origin = _kind_and_origin(dt.date.today())
    kind = kind or d_kind
    film_id = pick_candidate(kind, origin)
    facts = get_details(film_id)

    photo_path = _download(facts["poster"], f"{film_id}_poster.jpg")
    trailer = get_trailer_url(film_id)

    video_path = None
    if make_video:
        stills = [_download(u, f"{film_id}_still{i}.jpg")
                  for i, u in enumerate(get_stills(film_id))]
        video_path = make_teaser_video(stills, os.path.join(MEDIA_DIR, f"{film_id}_teaser.mp4"))

    mark_used(film_id)

    return {
        "facts": facts,
        "prompt": build_prompt(facts),
        "photo_path": photo_path,
        "video_path": video_path,     # None, если ffmpeg недоступен
        "trailer_url": trailer,       # добавьте ссылкой в конец поста
    }


if __name__ == "__main__":
    # Проверка: доступные жанры и пробный подбор (ничего не записывается).
    print("Доступные жанры на Кинопоиске:")
    print(", ".join(g["genre"] for g in get_genres()))
    print("\nВаши GENRES (поиск):", GENRES)
    print("Не подходят (GENRES_BLOCK):", ", ".join(sorted(GENRES_BLOCK)))
    print("Подходят (GENRES_ALLOW):", ", ".join(sorted(GENRES_ALLOW)), "+ триллер вместе с драмой/мелодрамой/детективом")
    print("\nПробный подбор (в used_movies.json не пишется):")
    for k in ("film", "series"):
        for o in ("foreign", "ru"):
            try:
                f = get_details(pick_candidate(k, o))
                print(f"  {k:6} / {o:7} -> {f['name']} ({f['year']}), "
                      f"рейтинг {f['rating']}, {', '.join(f['countries'])}")
            except Exception as e:
                print(f"  {k:6} / {o:7} -> ошибка: {e}")
