# data_processing.py
import logging
import pprint  # Do debugowania z loggerem
from typing import List, Dict, Any, Optional, Tuple  # Dodano Optional dla spójności

from librenms_client import LibreNMSAPI  # Załóżmy, że jest w PYTHONPATH

logger = logging.getLogger(__name__)


def build_phys_mac_map(api: LibreNMSAPI) -> Dict[str, Dict[str, Any]]:
    """
    Buduje globalną mapę MAC -> info o porcie (urządzenie, port, ifIndex itp.)
    używając danych z API LibreNMS.
    """
    phys_mac_map: Dict[str, Dict[str, Any]] = {}
    logger.info("Rozpoczynanie budowy globalnej mapy MAC adresów...")
    all_devices = api.get_devices()  # Pobiera domyślne kolumny w tym device_id, hostname, ip, sysName, purpose
    if not all_devices:
        logger.warning("Nie udało się pobrać urządzeń z LibreNMS API do budowy mapy MAC. Zwracam pustą mapę.")
        return {}

    total_devices = len(all_devices)
    logger.info(f"Budowanie mapy MAC: Pobieranie portów dla {total_devices} urządzeń...")
    processed_count = 0
    skipped_devices_no_id = 0

    for d in all_devices:
        processed_count += 1
        dev_id = d.get("device_id")
        # Użyjemy canonical_identifier dla lepszego logowania i spójności
        # Potrzebujemy utils, więc zaimportujmy go, lub przekażmy funkcję
        # Na razie dla prostoty:
        host_repr = d.get("hostname") or d.get("ip") or d.get("sysName") or f"ID_API:{dev_id}"

        if (processed_count % max(1, total_devices // 20) == 0) or (processed_count == total_devices):
            logger.info(f"  Mapowanie MAC: Przetworzono {processed_count}/{total_devices} urządzeń...")

        if not dev_id:
            logger.debug(f"  Mapowanie MAC: Pomijam urządzenie bez device_id: {d}")
            skipped_devices_no_id += 1
            continue

        try:
            # Pobierz tylko niezbędne kolumny
            ports = api.get_ports(str(dev_id), columns="port_id,ifPhysAddress,ifName,ifDescr,ifIndex")
            if ports is None:  # api.get_ports zwraca [] lub None w przypadku błędu krytycznego
                logger.warning(
                    f"  Mapowanie MAC: Błąd API lub brak odpowiedzi podczas pobierania portów dla {host_repr} (ID: {dev_id}).")
                continue
            if not ports:
                logger.debug(f"  Mapowanie MAC: Brak portów dla {host_repr} (ID: {dev_id}).")
                continue

            for p in ports:
                mac_raw = p.get("ifPhysAddress")
                if not mac_raw: continue  # Pomiń porty bez adresu MAC

                mac = str(mac_raw).lower().replace(":", "").replace("-", "").replace(".", "").strip()
                port_id_val = p.get("port_id")  # Zmieniono nazwę zmiennej z pid

                if mac and len(mac) == 12 and port_id_val is not None:
                    if mac in phys_mac_map:
                        # Loguj jeśli MAC jest już zmapowany, może to wskazywać na duplikat MAC w sieci
                        # lub na urządzenie, które ma ten sam MAC na wielu portach (rzadkie dla fizycznych)
                        logger.debug(
                            f"  Mapowanie MAC: MAC {mac} już istnieje w mapie (poprzednio: {phys_mac_map[mac].get('hostname')}:{phys_mac_map[mac].get('ifName')}). "
                            f"Obecnie mapowany na: {host_repr}:{p.get('ifName', '')}. Nadpisuję (lub nie, w zależności od polityki).")
                        # Obecnie nadpisuje, można dodać logikę wyboru "lepszego" wpisu

                    phys_mac_map[mac] = {
                        "device_id": dev_id,
                        "hostname": d.get("hostname"),  # Użyj hostname z danych urządzenia
                        "ip": d.get("ip"),  # Użyj ip z danych urządzenia
                        "sysname": d.get("sysName"),  # Dodaj sysName
                        "purpose": d.get("purpose"),  # Dodaj purpose
                        "port_id": port_id_val,
                        "ifName": p.get("ifName", ""),
                        "ifDescr": p.get("ifDescr", ""),
                        "ifIndex": p.get("ifIndex")  # Może być None
                    }
        except Exception as e:
            logger.error(f"  Mapowanie MAC: Błąd przetwarzania portów dla {host_repr} (ID: {dev_id}): {e}",
                         exc_info=True)

    if skipped_devices_no_id > 0:
        logger.warning(f"Mapowanie MAC: Pominięto {skipped_devices_no_id} urządzeń z powodu braku device_id.")
    logger.info(f"✓ Zakończono budowę mapy fizycznych MAC adresów: {len(phys_mac_map)} unikalnych wpisów.")
    return phys_mac_map


def build_ifindex_to_name_map(api: LibreNMSAPI, device_id: str, device_repr_for_log: Optional[str] = None) -> Dict[
    int, str]:
    """
    Buduje mapę ifIndex -> Nazwa Portu (ifName lub ifDescr) dla danego urządzenia.
    Kluczem jest int(ifIndex).
    """
    idx_to_name_map: Dict[int, str] = {}
    if not device_id:
        logger.warning("build_ifindex_to_name_map: device_id jest pusty. Zwracam pustą mapę.")
        return {}

    device_log_name = device_repr_for_log or f"urządzenia ID {device_id}"
    logger.debug(f"Budowanie mapy ifIndex->nazwa dla {device_log_name}...")

    try:
        ports = api.get_ports(device_id, columns="ifIndex,ifName,ifDescr")  # Pobierz tylko potrzebne kolumny
        if ports is None:
            logger.warning(
                f"Nie udało się pobrać portów dla mapy ifIndex->nazwa dla {device_log_name} (API zwróciło None).")
            return {}
        if not ports:
            logger.debug(f"Brak portów dla mapy ifIndex->nazwa dla {device_log_name}.")
            return {}

        for p in ports:
            ifindex_val = p.get("ifIndex")
            if ifindex_val is not None:  # Musi być ifIndex
                # Użyj ifName jeśli dostępne, w przeciwnym razie ifDescr. Jeśli oba puste, użyj placeholder.
                port_name = str(p.get("ifName", "")).strip() or \
                            str(p.get("ifDescr", "")).strip() or \
                            f"ifIndex_{ifindex_val}"  # Placeholder
                try:
                    idx_to_name_map[int(ifindex_val)] = port_name
                except ValueError:
                    logger.warning(
                        f"Nie można przekonwertować ifIndex '{ifindex_val}' na int dla portu '{port_name}' na {device_log_name}. Pomijam ten port w mapie.")
            else:
                logger.debug(
                    f"Port {p.get('ifName') or p.get('port_id')} na {device_log_name} nie ma ifIndex. Pomijam w mapie ifIndex->nazwa.")

    except Exception as e:
        logger.error(f"Nie udało się zbudować mapy ifIndex->nazwa dla {device_log_name}: {e}", exc_info=True)

    logger.debug(f"Zbudowano mapę ifIndex->nazwa dla {device_log_name} z {len(idx_to_name_map)} wpisami.")
    return idx_to_name_map


def deduplicate_connections(all_connections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Usuwa zduplikowane połączenia z listy, preferując lepsze metody odkrycia.
    Używa kluczy z wzbogaconych danych (np. local_device, local_port).
    """
    if not all_connections:
        logger.info("Deduplikacja: Brak połączeń do przetworzenia.")
        return []

    unique_conns_dict: Dict[Tuple[str, str], Dict[str, Any]] = {}
    # Kolejność preferencji metod (od najlepszej do najgorszej)
    method_preference = ["LLDP", "CDP", "CLI-LLDP", "CLI-CDP", "API-FDB", "SNMP-QBRIDGE", "SNMP-FDB", "SNMP-ARP"]

    num_initial_conns = len(all_connections)
    logger.info(f"Deduplikacja: Rozpoczynam dla {num_initial_conns} połączeń...")

    discarded_incomplete = 0
    discarded_self_conn = 0
    discarded_worse_method_or_duplicate = 0
    kept_new_unique_link = 0
    updated_existing_link = 0

    for i, conn in enumerate(all_connections):
        logger.debug(f"  Deduplicate [{i + 1}/{num_initial_conns}]: Przetwarzanie połączenia: {pprint.pformat(conn)}")

        local_host = conn.get('local_device')
        local_if = conn.get('local_port')
        neighbor_host = conn.get('remote_device')
        neighbor_if = conn.get('remote_port')

        if not all([local_host, local_if, neighbor_host, neighbor_if]):
            logger.debug(
                f"  Deduplicate [{i + 1}]: Pomijam niekompletne połączenie (brak jednego z: local_device, local_port, remote_device, remote_port).")
            discarded_incomplete += 1
            continue

        # Upewnij się, że identyfikatory są stringami do porównań i tworzenia klucza
        local_host_str = str(local_host)
        local_if_str = str(local_if)
        neighbor_host_str = str(neighbor_host)
        neighbor_if_str = str(neighbor_if)

        if local_host_str == neighbor_host_str:  # Połączenie do samego siebie
            # Można dodać bardziej rygorystyczne sprawdzanie, np. czy również porty są te same,
            # ale zwykle połączenie między dwoma różnymi portami na tym samym urządzeniu jest prawidłowe (np. patch panel).
            # Tutaj zakładamy, że jeśli hosty są te same, to jest to pętla, którą chcemy usunąć.
            # Jeśli jednak chcemy zachować połączenia w ramach tego samego urządzenia (np. między modułami stacka),
            # ta logika musiałaby być bardziej złożona.
            # Na razie: jeśli nazwy urządzeń są te same, uznajemy za self-connection.
            logger.debug(
                f"  Deduplicate [{i + 1}]: Pomijam self-connection: {local_host_str}:{local_if_str} -> {neighbor_host_str}:{neighbor_if_str}")
            discarded_self_conn += 1
            continue

        # Tworzymy unikalny klucz dla linku, niezależny od kierunku
        # Klucz składa się z posortowanych par (urządzenie:port)
        key_part1 = f"{local_host_str}:{local_if_str}"
        key_part2 = f"{neighbor_host_str}:{neighbor_if_str}"
        # Sortowanie krotek (stringów) zapewnia, że (A:1 -> B:2) i (B:2 -> A:1) dadzą ten sam klucz
        link_key = tuple(sorted((key_part1.lower(), key_part2.lower())))  # Użyj lowercase dla klucza

        existing_conn_for_link = unique_conns_dict.get(link_key)
        current_method = str(conn.get('discovery_method', '')).split('(')[
            0].upper()  # Weź bazową metodę, np. "LLDP" z "LLDP(snmp)"

        if existing_conn_for_link:
            existing_method = str(existing_conn_for_link.get('discovery_method', '')).split('(')[0].upper()

            try:
                current_pref_idx = method_preference.index(current_method)
            except ValueError:  # Jeśli metoda nie jest na liście preferencji, traktuj ją jako najgorszą
                current_pref_idx = len(method_preference)

            try:
                existing_pref_idx = method_preference.index(existing_method)
            except ValueError:
                existing_pref_idx = len(method_preference)

            logger.debug(
                f"  Deduplicate [{i + 1}]: Link {link_key} już istnieje. Obecna metoda: '{current_method}' (pref: {current_pref_idx}), "
                f"Istniejąca metoda: '{existing_method}' (pref: {existing_pref_idx}).")

            # Obecna metoda jest lepsza (niższy indeks = lepsza)
            if current_pref_idx < existing_pref_idx:
                logger.debug(f"    -> Zastępuję istniejące połączenie nowym (lepsza metoda).")
                unique_conns_dict[link_key] = conn
                updated_existing_link += 1
            # Metody są takie same, ale obecne połączenie ma VLAN, a istniejące nie
            elif current_pref_idx == existing_pref_idx and conn.get('vlan') is not None and existing_conn_for_link.get(
                    'vlan') is None:
                logger.debug(f"    -> Zastępuję istniejące połączenie nowym (ta sama metoda, ale obecne ma VLAN).")
                unique_conns_dict[link_key] = conn
                updated_existing_link += 1
            # Istniejące połączenie jest lepsze lub równe (i albo ma VLAN, albo oba nie mają/mają)
            else:
                logger.debug(f"    -> Zachowuję istniejące połączenie (lepsza lub równa metoda/VLAN). Odrzucam obecne.")
                discarded_worse_method_or_duplicate += 1
        else:
            # Pierwszy raz widzimy ten link
            logger.debug(f"  Deduplicate [{i + 1}]: Dodaję nowy unikalny link {link_key} z metodą '{current_method}'.")
            unique_conns_dict[link_key] = conn
            kept_new_unique_link += 1

    final_unique_connections = list(unique_conns_dict.values())
    logger.info(f"Deduplikacja zakończona. Początkowo: {num_initial_conns} połączeń.")
    logger.info(
        f"  Zachowano unikalnych linków: {len(final_unique_connections)} (Nowych: {kept_new_unique_link}, Zaktualizowanych: {updated_existing_link}).")
    logger.info(
        f"  Odrzucono: Niekompletne: {discarded_incomplete}, Self-connect: {discarded_self_conn}, Gorsza metoda/Duplikat: {discarded_worse_method_or_duplicate}.")

    return final_unique_connections