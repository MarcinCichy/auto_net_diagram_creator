# read_config.py
import configparser
import logging
import sys
import os

logger = logging.getLogger(__name__)

CONFIG_FILE = 'config.ini'

# Inicjalizacja parsera z obsługą komentarzy w linii
AppConfig = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))

# Sprawdzenie, czy plik konfiguracyjny istnieje i jego odczyt
if not os.path.exists(CONFIG_FILE):
    msg = f"KRYTYCZNY BŁĄD: Plik konfiguracyjny '{CONFIG_FILE}' nie został znaleziony. Aplikacja nie może kontynuować."
    # Użyj print, ponieważ logger może jeszcze nie być skonfigurowany
    print(msg, file=sys.stderr)
    raise FileNotFoundError(msg)

try:
    files_read = AppConfig.read(CONFIG_FILE, encoding='utf-8')
    if not files_read:
        msg = f"KRYTYCZNY BŁĄD: Plik konfiguracyjny '{CONFIG_FILE}' jest pusty lub nie można go było odczytać."
        print(msg, file=sys.stderr)
        raise ValueError(msg)
    # Logowanie o sukcesie nastąpi w main_app.py po skonfigurowaniu loggera
except configparser.Error as e:
    msg = f"KRYTYCZNY BŁĄD: Błąd parsowania pliku konfiguracyjnego '{CONFIG_FILE}': {e}"
    print(msg, file=sys.stderr)
    raise ValueError(msg) from e