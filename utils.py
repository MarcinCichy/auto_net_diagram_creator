# --- utils.py ---

import re
import logging
from typing import Dict, List, Optional, Any

# Konfiguracja logowania (przykładowa, można dostosować)
def setup_logging(level=logging.INFO):
    """Konfiguruje podstawowe logowanie do konsoli."""
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # Wyłączenie zbyt gadatliwych loggerów (opcjonalne)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pysnmp").setLevel(logging.WARNING)
    # Można dodać FileHandler, jeśli logowanie do pliku jest potrzebne

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

    # Sprawdzenie po purpose (case-insensitive) - Dodano
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

    # 1. Purpose (jeśli istnieje i nie jest pusty)
    purpose = device_info_from_api.get('purpose')
    if purpose and purpose.strip():
        return purpose.strip()

    # 2. Hostname (jeśli istnieje i NIE wygląda jak IP)
    hostname = device_info_from_api.get('hostname')
    hostname_looks_like_ip = False
    if hostname:
        hostname_looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname))
        if not hostname_looks_like_ip:
            return hostname

    # 3. IP (jeśli istnieje)
    ip = device_info_from_api.get('ip')
    if ip:
        return ip

    # 4. Hostname (nawet jeśli wygląda jak IP, jako fallback)
    if hostname and hostname_looks_like_ip:
        return hostname

    # 5. Oryginalny identyfikator (jeśli podano)
    if original_identifier:
        return str(original_identifier)

    # 6. Device ID (ostateczność)
    dev_id = device_api_info.get('device_id')
    if dev_id is not None:
        return f"device_id_{dev_id}" # Dodaj prefix dla jasności

    # 7. Jeśli nic nie znaleziono (bardzo mało prawdopodobne)
    return None