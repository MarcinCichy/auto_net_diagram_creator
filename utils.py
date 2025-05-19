# --- utils.py ---

import sys
import re
import logging
from typing import Dict, List, Optional, Any, Tuple

# Spróbuj zaimportować colorlog; jeśli nie ma, ustaw flagę i użyj standardowego formattera
try:
    from colorlog import ColoredFormatter

    COLORLOG_AVAILABLE = True
except ImportError:
    COLORLOG_AVAILABLE = False


# Konfiguracja logowania
def setup_logging(level: int = logging.INFO, log_to_file: bool = True,
                  log_file: str = "auto_diagram_app.log") -> None:  # Zmieniono domyślny poziom na INFO
    """
    Konfiguruje logowanie do konsoli (z kolorami, jeśli colorlog jest dostępny)
    i opcjonalnie do pliku.
    """
    log_format_str = "%(asctime)s - %(levelname)-8s - [%(name)s:%(lineno)d] - %(message)s"
    log_datefmt_str = "%Y-%m-%d %H:%M:%S"

    # Pobierz główny logger (root logger)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Usuń ewentualne istniejące handlery, aby uniknąć wielokrotnego dodawania
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # === Handler dla konsoli ===
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)  # Ustaw poziom dla tego konkretnego handlera

    if COLORLOG_AVAILABLE:
        console_formatter = ColoredFormatter(
            fmt="%(log_color)s" + log_format_str,
            datefmt=log_datefmt_str,
            reset=True,
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            },
            secondary_log_colors={},
            style='%'
        )
    else:
        console_formatter = logging.Formatter(log_format_str, datefmt=log_datefmt_str)
        # Ostrzeżenie o braku colorlog zostanie zalogowane później, po dodaniu handlerów

    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # === Opcjonalny handler dla pliku ===
    file_logging_configured_successfully = False
    if log_to_file:
        try:
            file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')  # Tryb 'a' (append)
            file_handler.setLevel(level)
            file_formatter = logging.Formatter(log_format_str, datefmt=log_datefmt_str)
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
            file_logging_configured_successfully = True
            # Logowanie informacji o konfiguracji pliku logów (teraz, gdy handlery są już ustawione)
            logging.getLogger(__name__).info(f"Logowanie do pliku '{log_file}' zostało włączone i skonfigurowane.")
        except Exception as e:
            # Jeśli konfiguracja pliku zawiedzie, zaloguj do konsoli (która powinna już działać)
            logging.getLogger(__name__).critical(f"Nie udało się skonfigurować logowania do pliku '{log_file}': {e}",
                                                 exc_info=True)
            # I wydrukuj na stderr jako ostateczność
            print(f"KRYTYCZNY BŁĄD: Nie udało się skonfigurować logowania do pliku '{log_file}': {e}", file=sys.stderr)

    # Wycisz zbyt gadatliwe loggery z zewnętrznych bibliotek
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # Dla pysnmp INFO może być przydatne do śledzenia problemów, ale można zmienić na WARNING
    logging.getLogger("pysnmp").setLevel(logging.INFO)  # Można dać WARNING jeśli za dużo logów
    logging.getLogger("netmiko").setLevel(logging.INFO)  # Podobnie
    if COLORLOG_AVAILABLE:
        logging.getLogger("colorlog").setLevel(logging.WARNING)

    # Logowanie końcowe o stanie konfiguracji
    if not COLORLOG_AVAILABLE:
        if file_logging_configured_successfully:
            logging.getLogger(__name__).warning(
                f"Moduł 'colorlog' nie jest zainstalowany. Logi konsolowe nie będą kolorowe, ale logowanie do pliku '{log_file}' jest aktywne.")
        else:
            logging.getLogger(__name__).warning(
                "Moduł 'colorlog' nie jest zainstalowany. Logi konsolowe nie będą kolorowe. Logowanie do pliku również nie zostało skonfigurowane lub zawiodło.")
    elif not file_logging_configured_successfully and log_to_file:  # Chcieliśmy logować do pliku, ale się nie udało
        logging.getLogger(__name__).warning(
            f"Logowanie do pliku '{log_file}' nie powiodło się. Logi będą dostępne tylko w konsoli.")


# Inicjalizacja loggera dla tego modułu (utils.py)
# Ten logger odziedziczy konfigurację z root_loggera ustawioną przez setup_logging().
# Komunikaty z tego loggera pojawią się dopiero po wywołaniu setup_logging().
logger = logging.getLogger(__name__)


def find_device_in_list(identifier: Any, all_devices_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Wyszukuje urządzenie w liście z API po IP, hostname, sysName lub purpose
    (ignorując wielkość liter dla stringów).
    """
    if not identifier or not all_devices_list:
        return None

    identifier_str = str(identifier).strip()  # Upewnij się, że to string i bez białych znaków
    identifier_lower = identifier_str.lower()

    if not identifier_str:  # Jeśli po strip() jest pusty
        return None

    # 1. Sprawdzenie po IP (dokładne dopasowanie)
    for d in all_devices_list:
        if str(d.get("ip", "")).strip() == identifier_str:
            logger.debug(
                f"Znaleziono urządzenie wg IP '{identifier_str}': {d.get('hostname') or d.get('sysName') or d.get('device_id')}")
            return d

    # 2. Sprawdzenie po hostname (case-insensitive)
    for d in all_devices_list:
        hostname_api = str(d.get("hostname", "")).strip()
        if hostname_api and hostname_api.lower() == identifier_lower:
            logger.debug(
                f"Znaleziono urządzenie wg hostname '{identifier_str}': {hostname_api} (ID: {d.get('device_id')})")
            return d

    # 3. Sprawdzenie po sysName (case-insensitive)
    for d in all_devices_list:
        sysname_api = str(d.get("sysName", "")).strip()
        if sysname_api and sysname_api.lower() == identifier_lower:
            logger.debug(
                f"Znaleziono urządzenie wg sysName '{identifier_str}': {d.get('hostname') or d.get('ip')} (ID: {d.get('device_id')})")
            return d

    # 4. Sprawdzenie po purpose (case-insensitive)
    for d in all_devices_list:
        purpose_api = str(d.get("purpose", "")).strip()
        if purpose_api and purpose_api.lower() == identifier_lower:
            logger.debug(
                f"Znaleziono urządzenie wg purpose '{identifier_str}': {d.get('hostname') or d.get('ip')} (ID: {d.get('device_id')})")
            return d

    # 5. Jeśli identyfikator jest numeryczny, spróbuj dopasować do device_id
    if identifier_str.isdigit():
        dev_id_to_find = int(identifier_str)
        for d in all_devices_list:
            if d.get("device_id") == dev_id_to_find:
                logger.debug(
                    f"Znaleziono urządzenie wg device_id '{dev_id_to_find}': {d.get('hostname') or d.get('sysName')}")
                return d

    logger.debug(f"Nie znaleziono urządzenia dla identyfikatora '{identifier_str}' w dostarczonej liście.")
    return None


def get_canonical_identifier(device_info_from_api: Optional[Dict[str, Any]], original_identifier: Any = None) -> \
Optional[str]:
    """
    Zwraca preferowany (kanoniczny) identyfikator dla urządzenia.
    Preferencje: purpose > hostname (jeśli nie IP) > IP > hostname (jeśli IP) > original_identifier > device_id.
    Zawsze zwraca string lub None. Usuwa białe znaki z początku/końca.
    """
    if not device_info_from_api:
        return str(original_identifier).strip() if original_identifier and str(original_identifier).strip() else None

    purpose = str(device_info_from_api.get('purpose', "")).strip()
    if purpose:
        return purpose

    hostname = str(device_info_from_api.get('hostname', "")).strip()
    hostname_looks_like_ip = False
    if hostname:
        hostname_looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname))
        if not hostname_looks_like_ip:
            return hostname

    ip_addr = str(device_info_from_api.get('ip', "")).strip()
    if ip_addr:
        return ip_addr

    if hostname and hostname_looks_like_ip:  # hostname został już sprawdzony, więc jeśli tu jesteśmy, to jest to IP
        return hostname  # Zwróć hostname, które wygląda jak IP, jeśli nie ma właściwego pola IP

    original_id_str = str(original_identifier).strip() if original_identifier else ""
    if original_id_str:
        return original_id_str

    dev_id = device_info_from_api.get('device_id')
    if dev_id is not None:
        return f"device_id_{str(dev_id).strip()}"

    # Jako ostateczność, jeśli nic innego nie jest dostępne
    sys_name_fallback = str(device_info_from_api.get('sysName', "")).strip()
    if sys_name_fallback:
        return sys_name_fallback

    logger.warning(f"Nie można było ustalić kanonicznego identyfikatora dla urządzenia: {device_info_from_api}")
    return None  # Jeśli absolutnie nic nie można ustalić