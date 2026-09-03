# -*- coding: utf-8 -*-
"""
Точка входа для Passenger (используется хостингом reg.ru).

ВАЖНО: этот файл содержит секретные ключи в открытом виде — НЕ коммитьте
заполненную версию в открытый GitHub-репозиторий. Держите шаблон (как
сейчас, с плейсхолдерами) в репозитории, а реальные значения проставляйте
только в копии на самом сервере (через FTP/SSH), либо добавьте
passenger_wsgi.py в .gitignore после первого деплоя.

ЧТО ПОПРАВИТЬ:
1. sys.path.insert(0, "...") — путь до этой папки на сервере. Посмотреть
   можно командой `pwd`, зайдя по SSH в папку scan_backend. Обычно похоже на:
   /var/www/u0000006/data/www/xn--d1aeghrfjy.online/scan_backend
2. Если создавали отдельное виртуальное окружение (venv) — раскомментируйте
   и поправьте INTERP.
3. Заполните значения os.environ[...] ниже реальными ключами.
"""

import os
import sys

# --- 1. Путь к проекту -------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))

# --- 2. (опционально) виртуальное окружение -----------------------------
# INTERP = os.path.expanduser("/var/www/u0000006/data/scanenv/bin/python")
# if sys.executable != INTERP:
#     os.execl(INTERP, INTERP, *sys.argv)

# --- 3. Секреты — заполните реальными значениями на сервере -------------
os.environ["MAX_BOT_TOKEN"] = "ВАШ_ТОКЕН_БОТА"
os.environ["YANDEX_VISION_API_KEY"] = "ВАШ_КЛЮЧ_YANDEX_VISION"
os.environ["YOOKASSA_SHOP_ID"] = "ВАШ_SHOP_ID"
os.environ["YOOKASSA_SECRET_KEY"] = "ВАШ_СЕКРЕТНЫЙ_КЛЮЧ_ЮKASSA"
os.environ["RETURN_URL"] = "https://xn--d1aeghrfjy.online/max/scan/"

from server import app as application  # noqa: E402  (импорт после setup — так и должно быть)
