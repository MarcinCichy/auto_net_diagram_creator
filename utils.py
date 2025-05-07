# --- utils.py ---

import re
import logging
from typing import Dict, List, Optional, Any, Tuple # Dodano Tuple dla spójności z innymi plikami

# Spróbuj zaimportować colorlog; jeśli nie ma, ustaw flagę i użyj standardowego formattera
try:
    from colorlog import ColoredFormatter
    COLORLOG_AVAILABLE = True
except ImportError:
    COLORLOG_AVAILABLE = False
    # Standardowy logging.Formatter zostanie użyty jako fallback

# Konfiguracja logowania (ZMODYFIKOWANA)
def setup_logging(level: int = logging.INFO, log_to_file: bool = False, log_file: str = "app.log") -> None:
    """
    Konfiguruje logowanie do konsoli (z kolorami, jeśli colorlog jest dostępny)
    i opcjonalnie do pliku.
    """
    # Definicja formatu i daty dla wszystkich handlerów
    log_format_str = "%(asctime)s - %(levelname)-8s - [%(filename)s:%(lineno)d] - %(message)s"
    log_datefmt_str = "%Y-%m-%d %H:%M:%S"

    # Pobierz główny logger (root logger)
    # Wszystkie inne loggery stworzone przez logging.getLogger(__name__) odziedziczą jego konfigurację
    root_logger = logging.getLogger()
    root_logger.setLevel(level)  # Ustaw minimalny poziom logowania dla głównego loggera

    # Usuń ewentualne istniejące handlery, aby uniknąć wielokrotnego dodawania
    # i duplikacji logów, jeśli setup_logging() zostałoby wywołane więcej niż raz.
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # === Handler dla konsoli ===
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level) # Ustaw poziom dla tego konkretnego handlera

    if COLORLOG_AVAILABLE:
        # Jeśli biblioteka colorlog jest dostępna, użyj ColoredFormatter
        console_formatter = ColoredFormatter(
            fmt="%(log_color)s" + log_format_str,  # colorlog używa specjalnego znacznika %(log_color)s
            datefmt=log_datefmt_str,
            reset=True,  # Automatycznie resetuje kolor po każdej wiadomości
            log_colors={
                'DEBUG':    'cyan',
                'INFO':     'green',
                'WARNING':  'yellow',
                'ERROR':    'red',
                'CRITICAL': 'red,bg_white', # Można użyć 'bold_red' jeśli terminal wspiera standardowe style ANSI dla bold
            },
            secondary_log_colors={}, # Można zdefiniować kolory dla innych części logu, np. nazwy loggera
            style='%' # Styl formatowania (jak w standardowym logging)
        )
    else:
        # Jeśli colorlog nie jest dostępny, użyj standardowego Formattera
        console_formatter = logging.Formatter(log_format_str, datefmt=log_datefmt_str)
        # Zaloguj ostrzeżenie używając standardowego mechanizmu, bo nasz logger może jeszcze nie działać
        logging.warning("Moduł 'colorlog' nie jest zainstalowany. Logi konsolowe nie będą kolorowe.")

    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # === Opcjonalny handler dla pliku (zawsze bez kolorów) ===
    if log_to_file:
        try:
            file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
            file_handler.setLevel(level)
            file_formatter = logging.Formatter(log_format_str, datefmt=log_datefmt_str)
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
            # Logujemy informację o zapisie do pliku używając już skonfigurowanego loggera
            logging.getLogger(__name__).info(f"Logowanie do pliku '{log_file}' włączone.")
        except Exception as e:
            logging.getLogger(__name__).error(f"Nie udało się skonfigurować logowania do pliku '{log_file}': {e}")


    # Wycisz zbyt gadatliwe loggery z zewnętrznych bibliotek (opcjonalnie)
    # Ustawia im wyższy poziom logowania, niż domyślny (np. DEBUG/INFO),
    # żeby nie zaśmiecały konsoli/pliku mniej istotnymi wiadomościami.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pysnmp").setLevel(logging.WARNING)
    logging.getLogger("netmiko").setLevel(logging.WARNING)
    if COLORLOG_AVAILABLE:
        logging.getLogger("colorlog").setLevel(logging.WARNING) # Sam colorlog może logować info o braku colorama

# Inicjalizacja loggera dla tego modułu (utils.py)
# Jest to dobra praktyka, aby każdy moduł miał swój własny logger.
# Ten logger odziedziczy konfigurację z root_loggera ustawioną przez setup_logging().
logger = logging.getLogger(__name__)


def find_device_in_list(identifier: Any, all_devices_list: List[Dict]) -> Optional[Dict]:
    """
    Wyszukuje urządzenie w liście z API po IP, hostname lub sysName
    (ignorując wielkość liter dla stringów).
    """
    if not identifier or not all_devices_list:
        return None

    identifier_str = str(identifier) # Dla spójnego porównywania

    # Sprawdzenie po IP
    for d in all_devices_list:
        if d.get("ip") == identifier_str:
            logger.debug(f"Znaleziono urządzenie wg IP '{identifier_str}': {d.get('hostname') or d.get('device_id')}")
            return d

    # Sprawdzenie po hostname (case-insensitive)
    identifier_lower = identifier_str.lower()
    for d in all_devices_list:
        hostname_api = d.get("hostname")
        if hostname_api and hostname_api.lower() == identifier_lower:
            logger.debug(f"Znaleziono urządzenie wg hostname '{identifier_str}': {d.get('hostname')} (ID: {d.get('device_id')})")
            return d

    # Sprawdzenie po sysName (case-insensitive)
    for d in all_devices_list:
        sysname_api = d.get("sysName")
        if sysname_api and sysname_api.lower() == identifier_lower:
            logger.debug(f"Znaleziono urządzenie wg sysName '{identifier_str}': {d.get('hostname') or d.get('ip')} (ID: {d.get('device_id')})")
            return d

    # Sprawdzenie po purpose (case-insensitive)
    for d in all_devices_list:
        purpose_api = d.get("purpose")
        if purpose_api and purpose_api.lower() == identifier_lower:
             logger.debug(f"Znaleziono urządzenie wg purpose '{identifier_str}': {d.get('hostname') or d.get('ip')} (ID: {d.get('device_id')})")
             return d

    logger.debug(f"Nie znaleziono urządzenia dla identyfikatora '{identifier_str}' w dostarczonej liście.")
    return None


def get_canonical_identifier(device_info_from_api: Optional[Dict], original_identifier: Any = None) -> Optional[str]:
    """
    Zwraca preferowany (kanoniczny) identyfikator dla urządzenia.
    Preferencje: purpose > hostname (jeśli nie IP) > IP > hostname (jeśli IP) > original_identifier > device_id.
    """
    if not device_info_from_api:
        return str(original_identifier) if original_identifier else None

    purpose = device_info_from_api.get('purpose')
    if purpose and purpose.strip():
        return purpose.strip()

    hostname = device_info_from_api.get('hostname')
    hostname_looks_like_ip = False
    if hostname:
        hostname_looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname))
        if not hostname_looks_like_ip:
            return hostname

    ip_addr = device_info_from_api.get('ip') # Zmieniono nazwę zmiennej z 'ip'
    if ip_addr:
        return ip_addr

    if hostname and hostname_looks_like_ip:
        return hostname

    if original_identifier:
        return str(original_identifier)

    dev_id = device_info_from_api.get('device_id')
    if dev_id is not None:
        return f"device_id_{dev_id}"

    return None