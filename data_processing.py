# data_processing.py
from librenms_client import LibreNMSAPI # Poprawny import
import pprint # Do debugowania

def build_phys_mac_map(api: LibreNMSAPI):
    """
    Buduje globalną mapę MAC -> info o porcie (urządzenie, port, ifIndex itp.)
    używając danych z API LibreNMS.
    """
    phys = {}
    all_devices = api.get_devices() # Pobiera domyślne kolumny w tym device_id, hostname, ip
    if not all_devices:
        print("⚠ Nie udało się pobrać urządzeń z LibreNMS API do budowy mapy MAC.")
        return {}

    total_devices = len(all_devices)
    print(f"Budowanie mapy MAC: Pobieranie portów dla {total_devices} urządzeń...")
    processed_count = 0
    for d in all_devices:
        processed_count += 1
        dev_id = d.get("device_id")
        host = d.get("hostname", "")
        ip = d.get("ip", "") # Używamy 'ip'

        # Logowanie postępu co ~5%
        if processed_count % max(1, total_devices // 20) == 0 or processed_count == total_devices:
             print(f"  Mapowanie MAC: Przetworzono {processed_count}/{total_devices} urządzeń...")

        if not dev_id: continue

        try:
            ports = api.get_ports(str(dev_id), columns="port_id,ifPhysAddress,ifName,ifDescr,ifIndex")
            if ports is None:
                print(f"  ⚠ Mapowanie MAC: Błąd API podczas pobierania portów dla {host or ip} (ID: {dev_id}).")
                continue
            if not ports: continue

            for p in ports:
                mac = (p.get("ifPhysAddress") or "").lower().replace(":", "").replace("-", "").replace(".", "").strip()
                pid = p.get("port_id")
                if mac and len(mac) == 12 and pid:
                    phys[mac] = {
                        "device_id": dev_id,
                        "hostname": host,
                        "ip": ip,
                        "port_id": pid,
                        "ifName": p.get("ifName", ""),
                        "ifDescr": p.get("ifDescr", ""),
                        "ifIndex": p.get("ifIndex")
                    }
        except Exception as e:
            print(f"  ⚠ Mapowanie MAC: Błąd przetwarzania portów dla {host or ip} (ID: {dev_id}): {e}")

    print(f"✓ Zbudowano mapę fizycznych MAC adresów: {len(phys)} unikalnych wpisów.")
    return phys


def build_ifindex_to_name_map(api: LibreNMSAPI, device_id: str):
    """
    Buduje mapę ifIndex -> Nazwa Portu (ifName lub ifDescr) dla danego urządzenia.
    """
    idx2name = {}
    if not device_id: return {}
    try:
        ports = api.get_ports(device_id, columns="ifIndex,ifName,ifDescr")
        if ports:
            for p in ports:
                ifidx = p.get("ifIndex")
                if ifidx is not None:
                    port_name = p.get("ifName", "") or p.get("ifDescr", "")
                    try:
                        idx2name[int(ifidx)] = port_name
                    except ValueError:
                        idx2name[str(ifidx)] = port_name
    except Exception as e:
        print(f"⚠ Nie udało się zbudować mapy ifIndex->nazwa dla urządzenia ID {device_id}: {e}")
    return idx2name


# *** POPRAWIONA FUNKCJA DEDUPLIKACJI Z DEBUGOWANIEM ***
def deduplicate_connections(all_connections):
    """
    Usuwa zduplikowane połączenia z listy, preferując lepsze metody odkrycia.
    Dodano logowanie diagnostyczne.
    """
    if not all_connections: return []

    unique_conns_dict = {}
    method_preference = ["LLDP", "CDP", "CLI-LLDP", "CLI-CDP", "API-FDB", "SNMP-QBRIDGE", "SNMP-FDB", "SNMP-ARP"]

    print(f"  DEBUG Deduplicate: Otrzymano {len(all_connections)} połączeń do deduplikacji.") # Dodano debug print
    kept_count = 0
    discarded_incomplete = 0
    discarded_self = 0
    discarded_worse = 0

    for i, conn in enumerate(all_connections):
        # Sprawdź czy połączenie ma wymagane pola
        local_host = conn.get('local_device') # Używamy kluczy ze wzbogaconych danych
        local_if = conn.get('local_port')
        neighbor_host = conn.get('remote_device')
        neighbor_if = conn.get('remote_port')

        # Sprawdzenie kompletności danych
        if not all([local_host, local_if, neighbor_host, neighbor_if]):
            print(f"  DEBUG Deduplicate [{i}]: Pomijam niekompletne: {conn}") # Dodano debug print
            discarded_incomplete += 1
            continue # Pomiń jeśli brakuje kluczowych danych

        # Sprawdzenie połączenia do samego siebie - BARDZO WAŻNE
        # Porównuj po upewnieniu się, że oba identyfikatory istnieją
        if local_host and neighbor_host and str(local_host) == str(neighbor_host):
             print(f"  DEBUG Deduplicate [{i}]: Pomijam self-connection: {local_host}:{local_if} -> {neighbor_host}:{neighbor_if}")
             discarded_self += 1
             continue

        # Tworzymy unikalny klucz dla linku, niezależny od kierunku
        key_part1 = f"{local_host}:{local_if}"
        key_part2 = f"{neighbor_host}:{neighbor_if}"
        link_key = tuple(sorted((key_part1, key_part2)))

        existing_conn = unique_conns_dict.get(link_key)
        current_method_base = conn.get('discovery_method', '').split('(')[0]

        # --- Debug Print dla każdego połączenia ---
        # print(f"  DEBUG Deduplicate [{i}]: Key={link_key}, Method={current_method_base}, Conn={pprint.pformat(conn)}")
        # --------------------------------------

        if existing_conn:
            # Link już istnieje, zdecyduj czy obecny jest lepszy
            existing_method_base = existing_conn.get('discovery_method', '').split('(')[0]
            try: existing_pref = method_preference.index(existing_method_base)
            except ValueError: existing_pref = len(method_preference)
            try: current_pref = method_preference.index(current_method_base)
            except ValueError: current_pref = len(method_preference)

            # print(f"  DEBUG Deduplicate [{i}]: Existing found. Current Pref={current_pref} ('{current_method_base}'), Existing Pref={existing_pref} ('{existing_method_base}')")

            if current_pref < existing_pref: # Niższy indeks = lepsza metoda
                # print(f"  DEBUG Deduplicate [{i}]: Replacing existing with current (better method).")
                unique_conns_dict[link_key] = conn
                # Licznik 'kept_count' nie zmienia się, bo tylko podmieniamy wartość dla klucza
            elif current_pref == existing_pref and conn.get('vlan') is not None and existing_conn.get('vlan') is None:
                 # print(f"  DEBUG Deduplicate [{i}]: Replacing existing with current (same method, has VLAN).")
                 unique_conns_dict[link_key] = conn
                 # Licznik 'kept_count' nie zmienia się
            else:
                # print(f"  DEBUG Deduplicate [{i}]: Keeping existing (better or equal method). Discarding current.")
                discarded_worse += 1 # Bieżące połączenie jest odrzucane jako gorsze lub równe bez VLAN
        else:
            # Pierwszy raz widzimy ten link, dodajemy
            # print(f"  DEBUG Deduplicate [{i}]: Adding new link.")
            unique_conns_dict[link_key] = conn
            kept_count += 1 # Zwiększamy licznik tylko przy dodaniu nowego unikalnego linku

    # Podsumowanie działania deduplikacji
    print(f"  DEBUG Deduplicate: Zakończono. Początkowo: {len(all_connections)}, "
          f"Zachowano unikalnych linków: {len(unique_conns_dict)} (nowych: {kept_count}), "
          f"Odrzucono (niekompletne: {discarded_incomplete}, self: {discarded_self}, gorsze/duplikat: {discarded_worse})")

    # Zwróć tylko wartości ze słownika (unikalne połączenia)
    return list(unique_conns_dict.values())
