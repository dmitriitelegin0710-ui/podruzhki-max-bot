"""
Банк вручную проверенных видео для рубрики «Маникюр дня» (manikur).

В отличие от fetch_pexels_video (случайный поиск по ключевым словам),
здесь используются конкретные ID роликов с Pexels, отобранные вручную —
каждый заранее проверен: на видео виден маникюр крупным планом, а не
процесс нанесения лака, флакон или что-то не по теме.

Ротация детерминирована по дате (та же схема, что и в MANIKUR_CAPTIONS
в rubric_post_to_max.py): в течение одного дня при повторных запусках
выбирается один и тот же ролик, на следующий день — следующий по кругу.

Как добавить новый ролик:
  1. Открыть страницу видео на pexels.com, посмотреть, что реально видно
     на превью — должен быть виден маникюр, а не процесс/флакон/руки без
     ногтей.
  2. Взять числовой ID из адреса страницы (число в конце URL).
  3. Добавить его в список нужного сезона в VIDEO_BANK ниже.

Требуется PEXELS_API_KEY (тот же секрет, что и для fetch_pexels_video).
"""
import os

import requests

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
PEXELS_VIDEO_BY_ID_URL = "https://api.pexels.com/videos/videos/{video_id}"

# ID роликов, проверенных вручную (см. инструкцию выше). Пополняется по
# мере того, как находятся новые подходящие видео.
VIDEO_BANK = {
    "осень": [8981632, 35049542],
    "зима": [35049541],
    "весна": [7689582],
    "лето": [6767643, 7338505],
}


def _pick_file_url(video_files: list):
    """Та же логика выбора файла, что и в fetch_pexels_video: сначала
    вертикальные ролики, среди них — самый лёгкий по битрейту (sd), если
    вертикальных нет — берётся любой найденный."""
    if not video_files:
        return None
    vertical_files = [f for f in video_files if f.get("height", 0) > f.get("width", 0)]
    candidates = vertical_files or video_files
    sd_candidates = [f for f in candidates if f.get("quality") == "sd"] or candidates
    chosen = min(sd_candidates, key=lambda f: f.get("width") or 9999)
    return chosen.get("link")


def get_manikur_video(season: str, target_date):
    """Возвращает прямую ссылку на видеофайл для рубрики «Маникюр дня»
    на конкретную дату, или None, если для сезона банк пуст, ключ не
    настроен или запрос к Pexels не удался (тогда вызывающий код сам
    переключится на случайный поиск fetch_pexels_video, см.
    fetch_and_upload_media)."""
    video_ids = VIDEO_BANK.get(season, [])
    if not video_ids or not PEXELS_API_KEY:
        return None

    video_id = video_ids[target_date.toordinal() % len(video_ids)]

    try:
        resp = requests.get(
            PEXELS_VIDEO_BY_ID_URL.format(video_id=video_id),
            headers={"Authorization": PEXELS_API_KEY},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Свой банк видео маникюра: ошибка запроса ролика {video_id} — {e}")
        return None

    url = _pick_file_url(data.get("video_files", []))
    if not url:
        print(f"Свой банк видео маникюра: у ролика {video_id} нет пригодных video_files")
        return None

    print(f"Свой банк видео маникюра: выбран ролик {video_id} ({season}) — {url}")
    return url
