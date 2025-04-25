# discovery.py
from librenms_client import LibreNMSAPI
try:
    import snmp_utils
except ImportError:
    print("OSTRZEŻENIE: Moduł snmp_utils.py nie został znaleziony. Funkcje SNMP nie będą działać.")
    class snmp_utils: # Stub class
        @staticmethod
        def snmp_get_lldp_neighbors(h, c): print(f"  SNMP stub: snmp_get_lldp_neighbors({h}, ***)"); return None
        @staticmethod
        def snmp_get_cdp_neighbors(h, c): print(f"  SNMP stub: snmp_get_cdp_neighbors({h}, ***)"); return None
        @staticmethod
        def snmp_get_bridge_baseport_ifindex(h, c): print(f"  SNMP stub: snmp_get_bridge_baseport_ifindex({h}, ***)"); return None
        @staticmethod
        def snmp_get_fdb_entries(h, c): print(f"  SNMP stub: snmp_get_fdb_entries({h}, ***)"); return None
        @staticmethod
        def snmp_get_qbridge_fdb(h, c): print(f"  SNMP stub: snmp_get_qbridge_fdb({h}, ***)"); return None
        @staticmethod
        def snmp_get_arp_entries(h, c): print(f"  SNMP stub: snmp_get_arp_entries({h}, ***)"); return None
import cli_utils
import pprint

def _format_connection(local_host, local_if, neighbor_host, neighbor_if, vlan, via):
    """Pomocnicza funkcja do tworzenia spójnego formatu słownika połączenia."""
    return {
        "local_host": str(local_host).strip() if local_host else None,
        "local_if": str(local_if).strip() if local_if else None,
        "neighbor_host": str(neighbor_host).strip() if neighbor_host else None,
        "neighbor_if": str(neighbor_if).strip() if neighbor_if else None,
        "vlan": vlan,
        "via": str(via).strip() if via else None,
    }

# *** ZMODYFIKOWANA FUNKCJA - Iteracja po liście community ***
def find_via_lldp_cdp_snmp(target_device, communities_to_try, idx2name):
    """
    Odkrywanie przez LLDP/CDP używając SNMP. Iteruje po liście community.
    """
    if not communities_to_try: return []
    host = target_device.get("hostname") or target_device.get("ip")
    if not host: return []
    print(f"⟶ SNMP LLDP/CDP dla {host}...")

    conns = []
    # Upewnij się, że communities_to_try jest listą
    communities = communities_to_try if isinstance(communities_to_try, list) else [communities_to_try]
    lldp_results = None # Zmienna do przechowania wyników LLDP
    cdp_results = None  # Zmienna do przechowania wyników CDP

    for i, community in enumerate(communities):
        if not community: continue
        print(f"  Próba SNMP z community #{i+1} ('{community}')...")

        # LLDP (próbuj tylko jeśli jeszcze nie mamy wyników)
        if lldp_results is None:
            print(f"    DEBUG discovery: Wywołanie snmp_utils.snmp_get_lldp_neighbors(host='{host}', community='{community}')")
            current_lldp = snmp_utils.snmp_get_lldp_neighbors(host, community)
            if current_lldp is not None: # Otrzymano odpowiedź (nawet pustą)
                print(f"    ✓ LLDP SNMP: Odpowiedź z community #{i+1}.")
                lldp_results = current_lldp # Zapisz wynik i przerwij próby LLDP
            else:
                print(f"    ⓘ LLDP SNMP: Brak odpowiedzi/błąd z community #{i+1}.")

        # CDP (próbuj tylko jeśli jeszcze nie mamy wyników)
        if cdp_results is None:
            print(f"    DEBUG discovery: Wywołanie snmp_utils.snmp_get_cdp_neighbors(host='{host}', community='{community}')")
            current_cdp = snmp_utils.snmp_get_cdp_neighbors(host, community)
            if current_cdp is not None:
                print(f"    ✓ CDP SNMP: Odpowiedź z community #{i+1}.")
                cdp_results = current_cdp # Zapisz wynik i przerwij próby CDP
            else:
                print(f"    ⓘ CDP SNMP: Brak odpowiedzi/błąd z community #{i+1}.")

        # Jeśli mamy już wyniki dla obu, nie ma sensu próbować dalej
        if lldp_results is not None and cdp_results is not None:
            print("  Mamy wyniki dla LLDP i CDP, przerywam próby z innymi community.")
            break

    # Przetwarzanie zapisanych wyników
    if lldp_results:
        print(f"    Przetwarzanie {len(lldp_results)} sąsiadów LLDP.")
        for ifidx, sysname, portid in lldp_results:
            local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
            conns.append(_format_connection(host, local_if_name, sysname, portid, None, "LLDP"))
    elif lldp_results is None: # Jeśli None, oznacza że były błędy dla wszystkich community
         print(f"  ⓘ LLDP SNMP: Brak odpowiedzi lub błąd dla wszystkich community.")

    if cdp_results:
        print(f"    Przetwarzanie {len(cdp_results)} sąsiadów CDP.")
        for ifidx, dev_id, portid in cdp_results:
            local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
            cleaned_dev_id = dev_id.split('.')[0]
            conns.append(_format_connection(host, local_if_name, cleaned_dev_id, portid, None, "CDP"))
    elif cdp_results is None:
         print(f"  ⓘ CDP SNMP: Brak odpowiedzi lub błąd dla wszystkich community.")

    return conns

# Funkcja find_via_api_fdb (bez zmian)
def find_via_api_fdb(api: LibreNMSAPI, phys_map, target_device):
    """Odkrywanie przez API FDB LibreNMS."""
    dev_id = target_device.get("device_id")
    host = target_device.get("hostname") or target_device.get("ip", f"ID:{dev_id}")
    if not dev_id: return []
    print(f"⟶ API-FDB dla {host}")
    conns = []
    try:
        ports = api.get_ports(str(dev_id))
        if ports is None: print(f"  ⚠ API-FDB: Błąd pobierania portów dla {host}"); return []
        if not ports: print(f"  ⓘ API-FDB: Brak portów dla {host}"); return []
        fdb_checked = False
        for p in ports:
            pid = p.get("port_id")
            local_if = p.get("ifName", "") or p.get("ifDescr", f"PortID:{pid}")
            if not pid: continue
            fdb_entries = api.get_port_fdb(str(dev_id), str(pid))
            if fdb_entries is None: print(f"  ⚠ API-FDB: Błąd pobierania FDB dla portu {local_if} (ID:{pid}) na {host}"); continue
            if not fdb_entries: continue
            fdb_checked = True
            for entry in fdb_entries:
                mac = (entry.get("mac_address") or "").lower().replace(":", "").replace("-", "").replace(".", "").strip()
                if len(mac) != 12: continue
                neighbor_info = phys_map.get(mac)
                if neighbor_info and neighbor_info.get('device_id') != dev_id:
                    neighbor_host = neighbor_info.get("hostname") or neighbor_info.get("ip", f"ID:{neighbor_info.get('device_id')}")
                    neighbor_if = neighbor_info.get("ifName") or neighbor_info.get("ifDescr", f"PortID:{neighbor_info.get('port_id')}")
                    vlan = entry.get("vlan_id") or entry.get("vlanid")
                    conns.append(_format_connection(host, local_if, neighbor_host, neighbor_if, vlan, "API-FDB"))
        if not fdb_checked: print(f"  ⓘ API-FDB: Nie znaleziono żadnych wpisów FDB przez API dla {host}")
    except Exception as e: print(f"  ⚠ API-FDB: Ogólny błąd dla {host}: {e}")
    return conns

# *** ZMODYFIKOWANA FUNKCJA - Iteracja po liście community ***
def find_via_snmp_fdb(phys_map, target_device, communities_to_try, idx2name):
    """
    Odkrywanie przez SNMP FDB (Bridge-MIB). Iteruje po liście community.
    """
    if not communities_to_try: return []
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    print(f"⟶ SNMP FDB (Bridge-MIB) dla {host}...")

    communities = communities_to_try if isinstance(communities_to_try, list) else [communities_to_try]
    base2if = None
    fdb_entries = None

    for i, community in enumerate(communities):
        if not community: continue
        print(f"  Próba SNMP z community #{i+1} ('{community}')...")
        if base2if is None:
            print(f"    DEBUG discovery: Wywołanie snmp_utils.snmp_get_bridge_baseport_ifindex(host='{host}', community='{community}')")
            current_base2if = snmp_utils.snmp_get_bridge_baseport_ifindex(host, community)
            if current_base2if is not None: base2if = current_base2if; print(f"    ✓ BasePort->ifIndex: Odpowiedź z community #{i+1}.")
            else: print(f"    ⓘ BasePort->ifIndex: Brak odpowiedzi/błąd z community #{i+1}.")
        if fdb_entries is None:
            print(f"    DEBUG discovery: Wywołanie snmp_utils.snmp_get_fdb_entries(host='{host}', community='{community}')")
            current_fdb = snmp_utils.snmp_get_fdb_entries(host, community)
            if current_fdb is not None: fdb_entries = current_fdb; print(f"    ✓ FDB Entries: Odpowiedź z community #{i+1}.")
            else: print(f"    ⓘ FDB Entries: Brak odpowiedzi/błąd z community #{i+1}.")
        if base2if is not None and fdb_entries is not None: print("  Mamy wyniki dla BasePort i FDB, przerywam próby."); break

    if base2if is None: print(f"  ⓘ SNMP FDB: Nie udało się pobrać mapy BasePort->ifIndex."); return []
    if fdb_entries is None: print(f"  ⓘ SNMP FDB: Nie udało się pobrać wpisów FDB."); return []
    if not fdb_entries: print(f"  ⓘ SNMP FDB: Brak wpisów FDB przez SNMP dla {host}"); return []

    conns = []
    for mac, base_port in fdb_entries:
        neighbor_info = phys_map.get(mac)
        if neighbor_info and neighbor_info.get('device_id') != dev_id:
            ifidx = base2if.get(base_port)
            if ifidx:
                local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
                neighbor_host = neighbor_info.get("hostname") or neighbor_info.get("ip", f"ID:{neighbor_info.get('device_id')}")
                neighbor_if = neighbor_info.get("ifName") or neighbor_info.get("ifDescr", f"PortID:{neighbor_info.get('port_id')}")
                conns.append(_format_connection(host, local_if_name, neighbor_host, neighbor_if, None, "SNMP-FDB"))
    return conns

# *** ZMODYFIKOWANA FUNKCJA - Iteracja po liście community ***
def find_via_qbridge_snmp(phys_map, target_device, communities_to_try, idx2name):
    """
    Odkrywanie przez SNMP FDB (Q-Bridge-MIB). Iteruje po liście community.
    """
    if not communities_to_try: return []
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    print(f"⟶ SNMP FDB (Q-Bridge-MIB) dla {host}...")

    communities = communities_to_try if isinstance(communities_to_try, list) else [communities_to_try]
    base2if = None
    qbridge_fdb_entries = None

    for i, community in enumerate(communities):
        if not community: continue
        print(f"  Próba SNMP z community #{i+1} ('{community}')...")
        if base2if is None:
            print(f"    DEBUG discovery: Wywołanie snmp_utils.snmp_get_bridge_baseport_ifindex(host='{host}', community='{community}')")
            current_base2if = snmp_utils.snmp_get_bridge_baseport_ifindex(host, community)
            if current_base2if is not None: base2if = current_base2if; print(f"    ✓ BasePort->ifIndex: Odpowiedź z community #{i+1}.")
            else: print(f"    ⓘ BasePort->ifIndex: Brak odpowiedzi/błąd z community #{i+1}.")
        if qbridge_fdb_entries is None:
            print(f"    DEBUG discovery: Wywołanie snmp_utils.snmp_get_qbridge_fdb(host='{host}', community='{community}')")
            current_qfdb = snmp_utils.snmp_get_qbridge_fdb(host, community)
            if current_qfdb is not None: qbridge_fdb_entries = current_qfdb; print(f"    ✓ Q-Bridge FDB: Odpowiedź z community #{i+1}.")
            else: print(f"    ⓘ Q-Bridge FDB: Brak odpowiedzi/błąd z community #{i+1}.")
        if base2if is not None and qbridge_fdb_entries is not None: print("  Mamy wyniki dla BasePort i Q-FDB, przerywam próby."); break

    if base2if is None: print(f"  ⓘ SNMP Q-BRIDGE: Nie udało się pobrać mapy BasePort->ifIndex."); return []
    if qbridge_fdb_entries is None: print(f"  ⓘ SNMP Q-BRIDGE: Nie udało się pobrać wpisów Q-BRIDGE FDB."); return []
    if not qbridge_fdb_entries: print(f"  ⓘ SNMP Q-BRIDGE: Brak wpisów Q-BRIDGE FDB dla {host}"); return []

    conns = []
    for mac, vlan, base_port in qbridge_fdb_entries:
        neighbor_info = phys_map.get(mac)
        if neighbor_info and neighbor_info.get('device_id') != dev_id:
            ifidx = base2if.get(base_port)
            if ifidx:
                local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
                neighbor_host = neighbor_info.get("hostname") or neighbor_info.get("ip", f"ID:{neighbor_info.get('device_id')}")
                neighbor_if = neighbor_info.get("ifName") or neighbor_info.get("ifDescr", f"PortID:{neighbor_info.get('port_id')}")
                conns.append(_format_connection(host, local_if_name, neighbor_host, neighbor_if, vlan, "SNMP-QBRIDGE"))
    return conns

# Funkcja find_via_cli (bez zmian)
def find_via_cli(host, username, password):
    """Odkrywanie przez CLI (opakowanie na cli_utils)."""
    if not username or not password: return []
    return cli_utils.cli_get_neighbors_enhanced(host, username, password)

# *** ZMODYFIKOWANA FUNKCJA - Iteracja po liście community ***
def find_via_arp_snmp(phys_map, target_device, communities_to_try, idx2name):
    """
    Odkrywanie przez SNMP ARP. Iteruje po liście community.
    """
    if not communities_to_try: return []
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    print(f"⟶ SNMP ARP dla {host}...")

    communities = communities_to_try if isinstance(communities_to_try, list) else [communities_to_try]
    arp_entries = None

    for i, community in enumerate(communities):
        if not community: continue
        print(f"  Próba SNMP z community #{i+1} ('{community}')...")
        if arp_entries is None:
            print(f"    DEBUG discovery: Wywołanie snmp_utils.snmp_get_arp_entries(host='{host}', community='{community}')")
            current_arp = snmp_utils.snmp_get_arp_entries(host, community)
            if current_arp is not None: # Mamy odpowiedź
                arp_entries = current_arp
                print(f"    ✓ ARP Entries: Odpowiedź z community #{i+1}.")
                break # Wystarczy jedna udana próba
            else:
                 print(f"    ⓘ ARP Entries: Brak odpowiedzi/błąd z community #{i+1}.")

    if arp_entries is None: print(f"  ⓘ SNMP ARP: Brak odpowiedzi lub błąd dla wszystkich community."); return []
    if not arp_entries: print(f"  ⓘ SNMP ARP: Brak wpisów ARP dla {host}"); return []

    conns = []
    for ipaddr, mac, ifidx in arp_entries:
        neighbor_info = phys_map.get(mac)
        if neighbor_info and neighbor_info.get('device_id') != dev_id:
            local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
            neighbor_host = neighbor_info.get("hostname") or neighbor_info.get("ip", ipaddr)
            neighbor_if = neighbor_info.get("ifName") or neighbor_info.get("ifDescr", f"MAC:{mac}")
            via = f"SNMP-ARP({ipaddr})"
            conns.append(_format_connection(host, local_if_name, neighbor_host, neighbor_if, None, via))
    return conns