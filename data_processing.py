# data_processing.py
from librenms_client import LibreNMSAPI # Poprawny import

def build_phys_mac_map(api: LibreNMSAPI):
    """
    Buduje globalną mapę MAC -> info o porcie (urządzenie, port, ifIndex itp.)
    używając danych z API LibreNMS.
    """
    phys = {}
    all_devices = api.get_devices()
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
        ip = d.get("ip", "")
        # Logowanie postępu
        if processed_count % max(1, total_devices // 20) == 0 or processed_count == total_devices:
             print(f"  Mapowanie MAC: Przetworzono {processed_count}/{total_devices} urządzeń...")

        if not dev_id:
            continue

        try:
            # Pobieramy tylko potrzebne pola portów
            ports = api.get_ports(str(dev_id), columns="port_id,ifPhysAddress,ifName,ifDescr,ifIndex")
            if ports is None: # Obsługa błędu API
                 print(f"  ⚠ Mapowanie MAC: Błąd API podczas pobierania portów dla {host or ip} (ID: {dev_id}).")
                 continue
            if not ports:
                continue

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
                    except ValueError: # ifIndex może nie być liczbą? Mało prawdopodobne
                         idx2name[str(ifidx)] = port_name
    except Exception as e:
        print(f"⚠ Nie udało się zbudować mapy ifIndex->nazwa dla urządzenia ID {device_id}: {e}")
    return idx2name

def deduplicate_connections(all_connections):
    """
    Usuwa zduplikowane połączenia z listy, preferując lepsze metody odkrycia.
    """
    if not all_connections: return []

    unique_conns_dict = {}
    method_preference = ["LLDP", "CDP", "CLI-LLDP", "CLI-CDP", "API-FDB", "SNMP-QBRIDGE", "SNMP-FDB", "SNMP-ARP"]

    for conn in all_connections:
        # Sprawdź czy połączenie ma wymagane pola
        local_host = conn.get('local_host')
        local_if = conn.get('local_if')
        neighbor_host = conn.get('neighbor_host')
        neighbor_if = conn.get('neighbor_if')
        if not all([local_host, local_if, neighbor_host, neighbor_if]):
             # print(f"DEBUG: Pomijam niekompletne połączenie: {conn}")
             continue # Pomiń jeśli brakuje kluczowych danych

        key_part1 = f"{local_host}:{local_if}"
        key_part2 = f"{neighbor_host}:{neighbor_if}"
        link_key = tuple(sorted((key_part1, key_part2)))

        existing_conn = unique_conns_dict.get(link_key)
        current_method_base = conn.get('via', '').split('(')[0]

        if existing_conn:
            existing_method_base = existing_conn.get('via', '').split('(')[0]
            try: existing_pref = method_preference.index(existing_method_base)
            except ValueError: existing_pref = len(method_preference)
            try: current_pref = method_preference.index(current_method_base)
            except ValueError: current_pref = len(method_preference)

            if current_pref < existing_pref:
                unique_conns_dict[link_key] = conn
            # Dodatkowa logika: Jeśli ta sama metoda, weź wpis z VLANem jeśli drugi go nie ma
            elif current_pref == existing_pref and conn.get('vlan') is not None and existing_conn.get('vlan') is None:
                 unique_conns_dict[link_key] = conn
        else:
            unique_conns_dict[link_key] = conn

    return list(unique_conns_dict.values())