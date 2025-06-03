# --- utils.py ---

import sys
import re
import logging
from typing import Dict, List, Optional, Any

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
        # Użyj print, bo logger może jeszcze nie być w pełni skonfigurowany
        print(f"OSTRZEŻENIE: Nieprawidłowy poziom logowania: {level_str}. Używam INFO.")
        numeric_level = logging.INFO

    log_format_str = "%(asctime)s - %(levelname)-8s - [%(name)s:%(lineno)d] - %(message)s"
    log_datefmt_str = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    # Usuń istniejące handlery, aby uniknąć duplikowania logów przy wielokrotnym wywołaniu
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()  # Zamknij handler przed usunięciem

    root_logger.setLevel(numeric_level)  # Ustaw poziom dla root loggera

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)  # Ustaw poziom dla handlera konsoli

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
            file_handler.setLevel(numeric_level)  # Ustaw poziom dla handlera pliku
            file_formatter = logging.Formatter(log_format_str, datefmt=log_datefmt_str)
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
            file_logging_configured_successfully = True
            # Logowanie o sukcesie konfiguracji pliku jest teraz bardziej niezawodne
        except Exception as e:
            # Użyj print, bo logger plikowy mógł się nie powieść
            print(f"KRYTYCZNY BŁĄD: Nie udało się skonfigurować logowania do pliku '{log_file}': {e}", file=sys.stderr)
            # Nie loguj tutaj przez logger, bo może to spowodować pętlę, jeśli handler plikowy jest problemem

    # Informacje o konfiguracji logowania po próbie ustawienia wszystkich handlerów
    if file_logging_configured_successfully:
        logging.getLogger(__name__).info(
            f"Logowanie do pliku '{log_file}' zostało włączone i skonfigurowane (poziom: {level_str}).")

    # Ustawianie poziomów dla bibliotek zewnętrznych
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pysnmp").setLevel(logging.INFO)  # Można zmienić na WARNING, jeśli logi pysnmp są zbyt gadatliwe
    logging.getLogger("netmiko").setLevel(logging.INFO)  # Podobnie, można zmienić na WARNING
    if COLORLOG_AVAILABLE:
        logging.getLogger("colorlog").setLevel(logging.WARNING)

    # Finalny komunikat o statusie logowania
    final_status_parts = [f"Logowanie skonfigurowane (Poziom: {level_str}"]
    final_status_parts.append(f"Kolory konsoli: {'Tak' if COLORLOG_AVAILABLE else 'Nie'}")
    if log_to_file:
        final_status_parts.append(
            f"Plik: {'Tak' if file_logging_configured_successfully else f'NIE ({log_file} - błąd)'}")
    else:
        final_status_parts.append("Plik: Nie")
    logging.getLogger(__name__).info(", ".join(final_status_parts) + ").")


logger_utils = logging.getLogger(__name__)  # Logger specyficzny dla tego modułu


def find_device_in_list(identifier: Any, all_devices_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Wyszukuje urządzenie w liście z API po IP, hostname, sysName lub purpose
    (ignorując wielkość liter dla stringów).
    """
    if not identifier or not all_devices_list:
        return None

    identifier_str = str(identifier).strip()
    identifier_lower = identifier_str.lower()

    if not identifier_str:  # Pusty identyfikator po strip()
        return None

    # Kolejność sprawdzania może mieć znaczenie, jeśli identyfikatory mogą być niejednoznaczne
    # 1. IP (najbardziej jednoznaczny, jeśli dostępny)
    for d in all_devices_list:
        # Sprawdź, czy 'ip' istnieje i nie jest None przed strip()
        ip_api = d.get("ip")
        if ip_api is not None and str(ip_api).strip() == identifier_str:
            logger_utils.debug(
                f"Znaleziono urządzenie wg IP '{identifier_str}': {d.get('hostname') or d.get('sysName') or d.get('device_id')}")
            return d

    # 2. Hostname (jeśli nie jest adresem IP)
    for d in all_devices_list:
        hostname_api_raw = d.get("hostname")
        if hostname_api_raw is not None:
            hostname_api = str(hostname_api_raw).strip()
            if hostname_api and hostname_api.lower() == identifier_lower and not re.match(r'^\d{1,3}(\.\d{1,3}){3}$',
                                                                                          hostname_api):
                logger_utils.debug(
                    f"Znaleziono urządzenie wg hostname '{identifier_str}': {hostname_api} (ID: {d.get('device_id')})")
                return d

    # 3. sysName
    for d in all_devices_list:
        sysname_api_raw = d.get("sysName")
        if sysname_api_raw is not None:
            sysname_api = str(sysname_api_raw).strip()
            if sysname_api and sysname_api.lower() == identifier_lower:
                logger_utils.debug(
                    f"Znaleziono urządzenie wg sysName '{identifier_str}': {d.get('hostname') or d.get('ip')} (ID: {d.get('device_id')})")
                return d

    # 4. Purpose
    for d in all_devices_list:
        purpose_api_raw = d.get("purpose")
        if purpose_api_raw is not None:
            purpose_api = str(purpose_api_raw).strip()
            if purpose_api and purpose_api.lower() == identifier_lower:
                logger_utils.debug(
                    f"Znaleziono urządzenie wg purpose '{identifier_str}': {d.get('hostname') or d.get('ip')} (ID: {d.get('device_id')})")
                return d

    # 5. Hostname (jeśli jest adresem IP - fallback, jeśli pierwotne wyszukiwanie po IP zawiodło np. z powodu formatu)
    for d in all_devices_list:
        hostname_api_raw = d.get("hostname")
        if hostname_api_raw is not None:
            hostname_api = str(hostname_api_raw).strip()
            if hostname_api and hostname_api.lower() == identifier_lower and re.match(r'^\d{1,3}(\.\d{1,3}){3}$',
                                                                                      hostname_api):
                logger_utils.debug(
                    f"Znaleziono urządzenie wg hostname (będącego IP) '{identifier_str}': {hostname_api} (ID: {d.get('device_id')})")
                return d

    # 6. Device ID (jeśli identyfikator jest numeryczny)
    if identifier_str.isdigit():
        try:
            dev_id_to_find = int(identifier_str)
            for d in all_devices_list:
                if d.get("device_id") == dev_id_to_find:
                    logger_utils.debug(
                        f"Znaleziono urządzenie wg device_id '{dev_id_to_find}': {d.get('hostname') or d.get('sysName')}")
                    return d
        except ValueError:
            pass  # Ignoruj, jeśli nie można przekonwertować na int

    logger_utils.debug(f"Nie znaleziono urządzenia dla identyfikatora '{identifier_str}' w dostarczonej liście.")
    return None


def normalize_interface_name(if_name: str, replacements: Dict[str, str]) -> str:
    """
    Normalizuje nazwę interfejsu na podstawie słownika zamienników.
    Dłuższe dopasowania mają priorytet. Niewrażliwe na wielkość liter.
    Przykład: "GigabitEthernet0/1" z {"GigabitEthernet": "Gi"} staje się "Gi0/1".
    """
    if_name_stripped = if_name.strip()
    # Sortuj wg długości klucza malejąco, aby np. "TenGigabitEthernet" było sprawdzane przed "GigabitEthernet"
    # lub "GigabitEthernet" przed "Eth"
    for long, short in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        # Użyj re.escape dla 'long', aby uniknąć problemów ze znakami specjalnymi w nazwach interfejsów, jeśli są używane jako regex
        # Jednak tutaj robimy proste startswith, więc escape nie jest absolutnie konieczny, ale dobra praktyka przy budowaniu regexów.
        # Dla startswith, lepiej po prostu porównywać.
        if if_name_stripped.lower().startswith(long.lower()):  # Porównanie niewrażliwe na wielkość liter
            # Zamień tylko prefiks, zachowaj resztę
            return short + if_name_stripped[len(long):]
    return if_name_stripped  # Zwróć oczyszczoną nazwę, jeśli nie znaleziono zamiennika


def get_canonical_identifier(device_info_from_api: Optional[Dict[str, Any]], original_identifier: Any = None) -> \
Optional[str]:
    """
    Zwraca preferowany (kanoniczny) identyfikator dla urządzenia.
    Preferencje: purpose > hostname (jeśli nie IP) > IP > hostname (jeśli IP) > sysName > original_identifier > device_id.
    Zawsze zwraca string lub None. Usuwa białe znaki z początku/końca.
    """
    if not device_info_from_api:
        return str(original_identifier).strip() if original_identifier and str(original_identifier).strip() and str(
            original_identifier).strip().lower() != "none" else None

    # Lista potencjalnych identyfikatorów w kolejności preferencji
    potential_ids = []

    purpose = str(device_info_from_api.get('purpose', "")).strip()
    if purpose: potential_ids.append(purpose)

    hostname = str(device_info_from_api.get('hostname', "")).strip()
    is_hostname_ip = bool(hostname and re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', hostname))

    if hostname and not is_hostname_ip:
        potential_ids.append(hostname)

    ip_addr = str(device_info_from_api.get('ip', "")).strip()
    if ip_addr: potential_ids.append(ip_addr)

    if hostname and is_hostname_ip and hostname not in potential_ids:  # Jeśli hostname to IP i nie został jeszcze dodany
        potential_ids.append(hostname)

    sys_name = str(device_info_from_api.get('sysName', "")).strip()
    if sys_name and sys_name not in potential_ids: potential_ids.append(sys_name)

    # Dodaj original_identifier, jeśli jest sensowny i jeszcze go nie ma
    original_id_str = str(original_identifier).strip() if original_identifier else ""
    if original_id_str and original_id_str.lower() != "none" and original_id_str not in potential_ids:
        potential_ids.append(original_id_str)

    # Sprawdź pierwszeństwo dla niepustych wartości
    for pid in potential_ids:
        if pid:  # Pierwszy niepusty identyfikator z listy preferencji
            return pid

    # Jeśli wszystko inne zawiedzie, użyj device_id
    dev_id_val = device_info_from_api.get('device_id')
    if dev_id_val is not None:
        return f"device_id_{str(dev_id_val).strip()}"

    logger_utils.warning(
        f"Nie można było ustalić kanonicznego identyfikatora dla urządzenia: {device_info_from_api}, oryginalny identyfikator: {original_identifier}. Zwracam None.")
    return None
