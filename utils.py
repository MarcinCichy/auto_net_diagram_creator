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
def setup_logging(level_str: str = "INFO", log_to_file: bool = True,
                  log_file: str = "auto_diagram_app.log") -> None:
    """
    Konfiguruje logowanie do konsoli (z kolorami, jeśli colorlog jest dostępny)
    i opcjonalnie do pliku.
    Akceptuje poziom logowania jako string.
    """
    numeric_level = getattr(logging, level_str.upper(), logging.INFO)
    if not isinstance(numeric_level, int):
        logging.getLogger(__name__).warning(
            f"Nieprawidłowy poziom logowania: {level_str}. Używam INFO."
        )
        numeric_level = logging.INFO

    log_format_str = "%(asctime)s - %(levelname)-8s - [%(name)s:%(lineno)d] - %(message)s"
    log_datefmt_str = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout) # Użyj sys.stdout
    console_handler.setLevel(numeric_level)

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

    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    file_logging_configured_successfully = False
    if log_to_file:
        try:
            file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
            file_handler.setLevel(numeric_level)
            file_formatter = logging.Formatter(log_format_str, datefmt=log_datefmt_str)
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
            file_logging_configured_successfully = True
            logging.getLogger(__name__).info(f"Logowanie do pliku '{log_file}' zostało włączone i skonfigurowane (poziom: {level_str}).")
        except Exception as e:
            logging.getLogger(__name__).critical(f"Nie udało się skonfigurować logowania do pliku '{log_file}': {e}", exc_info=True)
            print(f"KRYTYCZNY BŁĄD: Nie udało się skonfigurować logowania do pliku '{log_file}': {e}", file=sys.stderr)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pysnmp").setLevel(logging.INFO)
    logging.getLogger("netmiko").setLevel(logging.INFO)
    if COLORLOG_AVAILABLE:
        logging.getLogger("colorlog").setLevel(logging.WARNING)

    if not COLORLOG_AVAILABLE:
        msg = "Moduł 'colorlog' nie jest zainstalowany. Logi konsolowe nie będą kolorowe."
        if log_to_file and file_logging_configured_successfully:
            logging.getLogger(__name__).warning(f"{msg} Logowanie do pliku '{log_file}' jest aktywne.")
        elif log_to_file: # Chcieliśmy, ale się nie udało
            logging.getLogger(__name__).warning(f"{msg} Logowanie do pliku '{log_file}' również nie powiodło się.")
        else: # Nie chcieliśmy logować do pliku
             logging.getLogger(__name__).warning(msg)
    elif log_to_file and not file_logging_configured_successfully:
        logging.getLogger(__name__).warning(f"Logowanie do pliku '{log_file}' nie powiodło się. Logi będą dostępne tylko w konsoli.")
    else:
        logging.getLogger(__name__).info(f"Logowanie skonfigurowane (Poziom: {level_str}, Kolory: {'Tak' if COLORLOG_AVAILABLE else 'Nie'}, Plik: {'Tak' if log_to_file and file_logging_configured_successfully else 'Nie'}).")


logger = logging.getLogger(__name__)


def find_device_in_list(identifier: Any, all_devices_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Wyszukuje urządzenie w liście z API po IP, hostname, sysName lub purpose
    (ignorując wielkość liter dla stringów).
    """
    if not identifier or not all_devices_list:
        return None

    identifier_str = str(identifier).strip()
    identifier_lower = identifier_str.lower()

    if not identifier_str:
        return None

    for d in all_devices_list:
        if str(d.get("ip", "")).strip() == identifier_str:
            logger.debug(
                f"Znaleziono urządzenie wg IP '{identifier_str}': {d.get('hostname') or d.get('sysName') or d.get('device_id')}")
            return d

    for d in all_devices_list:
        hostname_api = str(d.get("hostname", "")).strip()
        if hostname_api and hostname_api.lower() == identifier_lower:
            logger.debug(
                f"Znaleziono urządzenie wg hostname '{identifier_str}': {hostname_api} (ID: {d.get('device_id')})")
            return d

    for d in all_devices_list:
        sysname_api = str(d.get("sysName", "")).strip()
        if sysname_api and sysname_api.lower() == identifier_lower:
            logger.debug(
                f"Znaleziono urządzenie wg sysName '{identifier_str}': {d.get('hostname') or d.get('ip')} (ID: {d.get('device_id')})")
            return d

    for d in all_devices_list:
        purpose_api = str(d.get("purpose", "")).strip()
        if purpose_api and purpose_api.lower() == identifier_lower:
            logger.debug(
                f"Znaleziono urządzenie wg purpose '{identifier_str}': {d.get('hostname') or d.get('ip')} (ID: {d.get('device_id')})")
            return d

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

    if hostname and hostname_looks_like_ip:
        return hostname

    original_id_str = str(original_identifier).strip() if original_identifier else ""
    if original_id_str:
        return original_id_str

    dev_id = device_info_from_api.get('device_id')
    if dev_id is not None:
        return f"device_id_{str(dev_id).strip()}"

    sys_name_fallback = str(device_info_from_api.get('sysName', "")).strip()
    if sys_name_fallback:
        return sys_name_fallback

    logger.warning(f"Nie można było ustalić kanonicznego identyfikatora dla urządzenia: {device_info_from_api}")
    return None