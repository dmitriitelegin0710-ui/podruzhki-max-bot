"""
Собственная база фото под rubrics.json.

Идея: фото хранятся прямо в репозитории (папка photos/), а раздаются по
обычному публичному URL raw.githubusercontent.com — поэтому НЕ нужно ни
отдельное облако, ни Telegram file_id: upload_media_and_get_token() в
rubric_post_to_max.py и так скачивает медиа по URL (requests.get), значит
подойдёт любой рабочий http(s)-адрес.

Это ДОПОЛНЕНИЕ, а не замена Pexels: если для рубрики своих фото ещё нет
(или в photo_bank.json для неё нет записи), fetch_and_upload_media в
rubric_post_to_max.py как и раньше идёт в Pexels. Ничего не ломается.

--- Структура photo_bank.json (в корне репозитория) ---
{
  "utro_privet": {
    "0": ["photos/utro_privet/mon_1.jpg", "photos/utro_privet/mon_2.jpg"],
    "1": ["photos/utro_privet/tue_1.jpg"],
    ...
    "6": [...]
  },
  "goroskop": {
    "*": ["photos/goroskop/1.jpg", "photos/goroskop/2.jpg", "photos/goroskop/3.jpg"]
  }
}

Ключи "0"-"6" — дни недели (0 = понедельник, как weekday_index в
rubric_post_to_max.py). Ключ "*" — общий пул фото на все дни недели сразу,
удобно для рубрик, где день недели не важен для темы фото (гороскоп,
эзотерика, истории успеха). Если для рубрики задан и "*", и конкретный
день — используется конкретный день, "*" в этом случае не проверяется.

--- Ротация, чтобы не повторять одно и то же фото ---
photo_bank_state.json (создаётся и обновляется автоматически, руками его
трогать не нужно) хранит для каждой пары "рубрика+день недели" список
недавно использованных фото. Пока в пуле есть неиспользованные недавно
варианты — берётся один из них; когда все использованы — ограничение
снимается и выбор идёт заново по всему пулу.
"""
import json
import os
import random

PHOTO_BANK_FILE = "photo_bank.json"
PHOTO_BANK_STATE_FILE = "photo_bank_state.json"

# Замените на свои username/repo, если они отличаются от указанных здесь.
# Можно также задать через переменную окружения GITHUB_RAW_BASE, ничего не
# меняя в коде — например в GitHub Actions secrets/vars.
GITHUB_RAW_BASE = os.environ.get(
    "GITHUB_RAW_BASE",
    "https://raw.githubusercontent.com/dmitriitelegin0710-ui/podruzhki-max-bot/main/",
)


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_own_photo(rubric_key: str, weekday_index: int):
    """Возвращает публичный URL фото из своей базы для этой рубрики/дня,
    стараясь не повторять недавно использованные фото.
    Возвращает None, если для рубрики в photo_bank.json нет записи или
    список фото пуст — тогда caller (rubric_post_to_max.py) идёт дальше
    по цепочке, в Pexels, как и раньше."""
    bank = _load_json(PHOTO_BANK_FILE, {})
    rubric_bank = bank.get(rubric_key, {})

    candidates = rubric_bank.get(str(weekday_index)) or rubric_bank.get("*")
    if not candidates:
        return None

    state = _load_json(PHOTO_BANK_STATE_FILE, {})
    state_key = f"{rubric_key}_{weekday_index}"
    used_recently = state.get(state_key, [])

    available = [c for c in candidates if c not in used_recently] or candidates
    chosen = random.choice(available)

    # "Не повторять последние N", где N — примерно половина пула, чтобы
    # фото реально успевало "отдохнуть" перед повтором, а не гонялось
    # туда-обратно между двумя вариантами при маленьком пуле.
    window = max(1, len(candidates) // 2)
    state[state_key] = (used_recently + [chosen])[-window:]
    _save_json(PHOTO_BANK_STATE_FILE, state)

    return GITHUB_RAW_BASE + chosen
