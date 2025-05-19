# discovery.py
import os
import sys  # Usunięte, jeśli nie jest potrzebne do manipulacji ścieżkami
import logging
from typing import Callable, List, Dict, Any, Optional, Tuple

from librenms_client import LibreNMSAPI

try:
    import snmp_utils

    SNMP_UTILS_AVAILABLE = True
except ImportError:
    SNMP_UTILS_AVAILABLE = False
    logging.getLogger(__name__).warning(  # Użyj loggera przed zdefiniowaniem lokalnego
        "Moduł snmp_utils.py nie został znaleziony lub nie można go zaimportować. "
        "Funkcje SNMP nie będą działać. Upewnij się, że pysnmp jest zainstalowane i snmp_utils.py jest w PYTHONPATH."
    )


    # Definicja klasy zastępczej (stub)
    class snmp_utils:  # type: ignore
        @staticmethod
        def snmp_get_lldp_neighbors(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[int, str, str]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_lldp_neighbors({h}, ***)")
            return None

        @staticmethod
        def snmp_get_cdp_neighbors(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[int, str, str]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_cdp_neighbors({h}, ***)")
            return None

        @staticmethod
        def snmp_get_bridge_baseport_ifindex(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            Dict[int, int]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_bridge_baseport_ifindex({h}, ***)")
            return None

        @staticmethod
        def snmp_get_fdb_entries(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[List[Tuple[str, int]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_fdb_entries({h}, ***)")
            return None

        @staticmethod
        def snmp_get_qbridge_fdb(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[str, int, int]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_qbridge_fdb({h}, ***)")
            return None

        @staticmethod
        def snmp_get_arp_entries(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[str, str, int]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_arp_entries({h}, ***)")
            return None

import cli_utils  # Załóżmy, że jest poprawnie zaimportowany
import pprint  # Do debugowania, ale lepiej używać logger.debug z pprint.pformat

logger = logging.getLogger(__name__)


def _format_connection(local_host: Any, local_if: Any, neighbor_host: Any, neighbor_if: Any, vlan: Any, via: Any) -> \
Dict[str, Any]:
    """Pomocnicza funkcja do tworzenia spójnego formatu słownika połączenia."""
    conn = {
        "local_host": str(local_host).strip() if local_host is not None else None,
        "local_if": str(local_if).strip() if local_if is not None else None,
        "neighbor_host": str(neighbor_host).strip() if neighbor_host is not None else None,
        "neighbor_if": str(neighbor_if).strip() if neighbor_if is not None else None,
        "vlan": vlan,  # vlan może być None lub int/str
        "via": str(via).strip() if via is not None else None,
    }
    # Usuń klucze z wartością None, aby nie zaśmiecać JSONa, chyba że None jest znaczące
    # return {k: v for k, v in conn.items() if v is not None} # To może być zbyt agresywne
    return conn


def _try_snmp_operation(
        host: str,
        communities: Optional[List[str]],
        snmp_func: Callable,
        operation_desc: str,
        *args: Any  # Dodatkowe argumenty dla snmp_func
) -> Optional[Any]:
    """
    Wykonuje daną operację SNMP, iterując po community.
    Zwraca wynik pierwszej udanej operacji lub None, jeśli wszystkie zawiodą.
    """
    if not SNMP_UTILS_AVAILABLE:
        logger.debug(f"  SNMP ({operation_desc}): Moduł snmp_utils niedostępny, pomijam operację dla {host}.")
        return None
    if not communities:
        logger.info(f"  SNMP ({operation_desc}): Brak community do próby dla {host}.")
        return None

    for i, community_str in enumerate(communities):
        if not community_str:
            continue
        logger.info(f"  SNMP ({operation_desc}): Próba dla {host} z community #{i + 1} ('{community_str}')...")
        try:
            # Przekazanie timeout i retries, jeśli funkcja snmp_utils je akceptuje.
            # Można to zrobić bardziej generycznie z inspect.signature, ale dla uproszczenia:
            # Załóżmy, że funkcje w snmp_utils mają domyślne timeout/retries.
            # Jeśli snmp_func wymaga dodatkowych argumentów (np. phys_map), są one w *args.
            if args:
                result = snmp_func(host, community_str, *args)  # timeout i retries są w snmp_utils
            else:
                result = snmp_func(host, community_str)

            if result is not None:
                logger.info(f"    ✓ SNMP ({operation_desc}): Odpowiedź z community #{i + 1} dla {host}.")
                if isinstance(result, (list, dict)):  # Sprawdź, czy to kolekcja, aby zalogować rozmiar
                    logger.debug(f"    SNMP ({operation_desc}): Otrzymano {len(result)} elementów.")
                return result
            else:
                logger.info(
                    f"    ⓘ SNMP ({operation_desc}): Brak odpowiedzi/błąd (funkcja zwróciła None) z community #{i + 1} dla {host}.")
        except Exception as e:
            logger.error(
                f"    ⚠ SNMP ({operation_desc}): Niespodziewany błąd podczas wywołania {snmp_func.__name__} z community '{community_str}' dla {host}: {e}",
                exc_info=True)
            # Kontynuuj z następnym community

    logger.warning(
        f"  ⓘ SNMP ({operation_desc}): Nie udało się uzyskać danych dla {host} po próbie wszystkich community.")
    return None


def find_via_lldp_cdp_snmp(target_device: Dict[str, Any], communities_to_try: Optional[List[str]],
                           idx2name: Dict[int, str]) -> List[Dict[str, Any]]:
    host = target_device.get("hostname") or target_device.get("ip")
    if not host: return []
    logger.info(f"⟶ SNMP: Próba odkrycia sąsiadów LLDP/CDP dla {host}...")
    conns: List[Dict[str, Any]] = []

    lldp_data = _try_snmp_operation(host, communities_to_try, snmp_utils.snmp_get_lldp_neighbors, "LLDP Neighbors")
    if isinstance(lldp_data, list):
        logger.info(f"  SNMP LLDP: Przetwarzanie {len(lldp_data)} sąsiadów dla {host}.")
        for ifidx, sysname, portid in lldp_data:
            local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
            conns.append(_format_connection(host, local_if_name, sysname, portid, None, "LLDP"))

    cdp_data = _try_snmp_operation(host, communities_to_try, snmp_utils.snmp_get_cdp_neighbors, "CDP Neighbors")
    if isinstance(cdp_data, list):
        logger.info(f"  SNMP CDP: Przetwarzanie {len(cdp_data)} sąsiadów dla {host}.")
        for ifidx, dev_id, portid in cdp_data:
            local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
            cleaned_dev_id = dev_id.split('.')[0] if '.' in dev_id else dev_id
            conns.append(_format_connection(host, local_if_name, cleaned_dev_id, portid, None, "CDP"))
    return conns


def find_via_snmp_fdb(phys_map: Dict, target_device: Dict[str, Any], communities_to_try: Optional[List[str]],
                      idx2name: Dict[int, str]) -> List[Dict[str, Any]]:
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    logger.info(f"⟶ SNMP: Próba odkrycia przez FDB (Bridge-MIB) dla {host}...")
    conns: List[Dict[str, Any]] = []

    base2if = _try_snmp_operation(host, communities_to_try, snmp_utils.snmp_get_bridge_baseport_ifindex,
                                  "BasePortIfIndex (FDB)")
    if not isinstance(base2if, dict):
        return []  # Logowanie błędu jest już w _try_snmp_operation

    fdb_entries = _try_snmp_operation(host, communities_to_try, snmp_utils.snmp_get_fdb_entries, "FDB Entries")
    if not isinstance(fdb_entries, list) or not fdb_entries:
        if isinstance(fdb_entries, list):  # Pusta lista, ale nie błąd
            logger.info(f"  SNMP FDB: Brak wpisów FDB przez SNMP dla {host}.")
        return []  # Logowanie błędu (None) jest już w _try_snmp_operation

    logger.info(
        f"  SNMP FDB: Przetwarzanie {len(fdb_entries)} wpisów FDB dla {host} (mapa BasePort->ifIndex: {len(base2if)} wpisów).")
    for mac, base_port in fdb_entries:
        neighbor_info = phys_map.get(mac)
        if neighbor_info and str(neighbor_info.get('device_id')) != str(dev_id):  # Porównaj jako stringi dla pewności
            ifidx = base2if.get(base_port)
            if ifidx is not None:  # Upewnij się, że ifidx istnieje
                local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
                neighbor_host_ident = neighbor_info.get("hostname") or neighbor_info.get(
                    "ip") or f"ID:{neighbor_info.get('device_id')}"
                neighbor_if_ident = neighbor_info.get("ifName") or neighbor_info.get(
                    "ifDescr") or f"PortID:{neighbor_info.get('port_id')}"
                conns.append(
                    _format_connection(host, local_if_name, neighbor_host_ident, neighbor_if_ident, None, "SNMP-FDB"))
    return conns


def find_via_qbridge_snmp(phys_map: Dict, target_device: Dict[str, Any], communities_to_try: Optional[List[str]],
                          idx2name: Dict[int, str]) -> List[Dict[str, Any]]:
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    logger.info(f"⟶ SNMP: Próba odkrycia przez FDB (Q-Bridge-MIB) dla {host}...")
    conns: List[Dict[str, Any]] = []

    base2if = _try_snmp_operation(host, communities_to_try, snmp_utils.snmp_get_bridge_baseport_ifindex,
                                  "BasePortIfIndex (Q-Bridge)")
    if not isinstance(base2if, dict):
        return []

    qbridge_fdb_entries = _try_snmp_operation(host, communities_to_try, snmp_utils.snmp_get_qbridge_fdb,
                                              "Q-Bridge FDB Entries")
    if not isinstance(qbridge_fdb_entries, list) or not qbridge_fdb_entries:
        if isinstance(qbridge_fdb_entries, list):
            logger.info(f"  SNMP Q-Bridge: Brak wpisów Q-Bridge FDB dla {host}.")
        return []

    logger.info(
        f"  SNMP Q-Bridge: Przetwarzanie {len(qbridge_fdb_entries)} wpisów Q-Bridge FDB dla {host} (mapa BasePort->ifIndex: {len(base2if)} wpisów).")
    for mac, vlan, base_port in qbridge_fdb_entries:
        neighbor_info = phys_map.get(mac)
        if neighbor_info and str(neighbor_info.get('device_id')) != str(dev_id):
            ifidx = base2if.get(base_port)
            if ifidx is not None:
                local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
                neighbor_host_ident = neighbor_info.get("hostname") or neighbor_info.get(
                    "ip") or f"ID:{neighbor_info.get('device_id')}"
                neighbor_if_ident = neighbor_info.get("ifName") or neighbor_info.get(
                    "ifDescr") or f"PortID:{neighbor_info.get('port_id')}"
                conns.append(_format_connection(host, local_if_name, neighbor_host_ident, neighbor_if_ident, vlan,
                                                "SNMP-QBRIDGE"))
    return conns


def find_via_arp_snmp(phys_map: Dict, target_device: Dict[str, Any], communities_to_try: Optional[List[str]],
                      idx2name: Dict[int, str]) -> List[Dict[str, Any]]:
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    logger.info(f"⟶ SNMP: Próba odkrycia przez ARP dla {host}...")
    conns: List[Dict[str, Any]] = []

    arp_entries = _try_snmp_operation(host, communities_to_try, snmp_utils.snmp_get_arp_entries, "ARP Entries")
    if not isinstance(arp_entries, list) or not arp_entries:
        if isinstance(arp_entries, list):
            logger.info(f"  SNMP ARP: Brak wpisów ARP dla {host}.")
        return []

    logger.info(f"  SNMP ARP: Przetwarzanie {len(arp_entries)} wpisów ARP dla {host}.")
    for ipaddr, mac, ifidx_arp in arp_entries:  # Zmieniono ifidx na ifidx_arp dla jasności
        neighbor_info = phys_map.get(mac)
        if neighbor_info and str(neighbor_info.get('device_id')) != str(dev_id):
            local_if_name = idx2name.get(ifidx_arp, f"ifIndex {ifidx_arp}")  # Użyj ifidx_arp
            # Dla ARP, 'neighbor_host' to urządzenie, którego MAC znaleźliśmy. Jego IP to 'ipaddr'.
            neighbor_host_ident = neighbor_info.get("hostname") or neighbor_info.get("ip",
                                                                                     ipaddr)  # Użyj ipaddr jako fallback dla IP sąsiada
            # 'neighbor_if' to interfejs tego sąsiada.
            neighbor_if_ident = neighbor_info.get("ifName") or neighbor_info.get("ifDescr") or f"MAC:{mac}"
            via = f"SNMP-ARP({ipaddr})"  # IP address z wpisu ARP
            conns.append(_format_connection(host, local_if_name, neighbor_host_ident, neighbor_if_ident, None, via))
    return conns


def find_via_api_fdb(api: LibreNMSAPI, phys_map: Dict, target_device: Dict[str, Any]) -> List[Dict[str, Any]]:
    dev_id = target_device.get("device_id")
    host_identifier = target_device.get("hostname") or target_device.get("ip") or f"ID:{dev_id}"  # Lepsza nazwa
    if not dev_id:
        logger.warning(f"API-FDB: Brak device_id dla urządzenia '{host_identifier}'. Pomijam.")
        return []
    logger.info(f"⟶ API-FDB: Próba odkrycia dla {host_identifier}")
    conns: List[Dict[str, Any]] = []
    try:
        ports = api.get_ports(str(dev_id))  # get_ports zwraca listę lub pustą listę, obsługa None jest w kliencie API
        if not ports:
            logger.info(f"  API-FDB: Brak portów lub błąd pobierania portów dla {host_identifier} (ID: {dev_id}).")
            return []

        fdb_entries_found_on_any_port = False
        for p in ports:
            port_id = p.get("port_id")
            local_if_name = p.get("ifName", "") or p.get("ifDescr", "") or f"PortID:{port_id}"
            if not port_id:
                logger.debug(f"  API-FDB: Pomijam port bez port_id na {host_identifier}: {p}")
                continue

            fdb_entries = api.get_port_fdb(str(dev_id), str(port_id))  # get_port_fdb zwraca listę lub pustą listę
            if not fdb_entries:
                # logger.debug(f"  API-FDB: Brak wpisów FDB lub błąd pobierania dla portu {local_if_name} (ID:{port_id}) na {host_identifier}.")
                continue  # Przejdź do następnego portu

            fdb_entries_found_on_any_port = True
            logger.debug(
                f"  API-FDB: Przetwarzanie {len(fdb_entries)} wpisów FDB dla portu {local_if_name} na {host_identifier}.")
            for entry in fdb_entries:
                mac = (entry.get("mac_address") or "").lower().replace(":", "").replace("-", "").replace(".",
                                                                                                         "").strip()
                if len(mac) != 12:
                    logger.debug(
                        f"  API-FDB: Pominęto nieprawidłowy MAC '{mac}' na porcie {local_if_name} urządzenia {host_identifier}.")
                    continue

                neighbor_info = phys_map.get(mac)
                if neighbor_info and str(neighbor_info.get('device_id')) != str(dev_id):
                    neighbor_host_ident = neighbor_info.get("hostname") or neighbor_info.get(
                        "ip") or f"ID:{neighbor_info.get('device_id')}"
                    neighbor_if_ident = neighbor_info.get("ifName") or neighbor_info.get(
                        "ifDescr") or f"PortID:{neighbor_info.get('port_id')}"
                    vlan = entry.get("vlan_id") or entry.get("vlanid")  # Obsługa obu kluczy
                    conns.append(
                        _format_connection(host_identifier, local_if_name, neighbor_host_ident, neighbor_if_ident, vlan,
                                           "API-FDB"))

        if not fdb_entries_found_on_any_port:
            logger.info(f"  API-FDB: Nie znaleziono żadnych użytecznych wpisów FDB przez API dla {host_identifier}.")

    except Exception as e:
        logger.error(f"  API-FDB: Ogólny błąd podczas przetwarzania {host_identifier}: {e}", exc_info=True)
    return conns


def find_via_cli(host: str, username: str, password: str) -> List[Dict[str, Any]]:
    """Odkrywanie przez CLI (opakowanie na cli_utils)."""
    if not username or not password:
        # Logowanie o braku poświadczeń powinno być w miejscu, które decyduje o wywołaniu tej funkcji (np. NetworkDiscoverer)
        logger.debug(f"CLI: Pomijam próbę dla {host} z powodu braku pełnych poświadczeń (użytkownik lub hasło puste).")
        return []
    # Zakładamy, że cli_get_neighbors_enhanced już używa loggera po refaktoryzacji
    return cli_utils.cli_get_neighbors_enhanced(host, username, password)