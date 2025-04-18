# discovery.py
# (Zmiana nazwy z discovery_methods.py)
from librenms_client import LibreNMSAPI
import snmp_utils
import cli_utils

def _format_connection(local_host, local_if, neighbor_host, neighbor_if, vlan, via):
    """Pomocnicza funkcja do tworzenia spójnego formatu słownika połączenia."""
    # Podstawowe czyszczenie danych przed zwróceniem
    return {
        "local_host": str(local_host).strip() if local_host else None,
        "local_if": str(local_if).strip() if local_if else None,
        "neighbor_host": str(neighbor_host).strip() if neighbor_host else None,
        "neighbor_if": str(neighbor_if).strip() if neighbor_if else None,
        "vlan": vlan, # Może być None lub string/int
        "via": str(via).strip() if via else None,
    }

def find_via_lldp_cdp_snmp(target_device, community, idx2name):
    """Odkrywanie przez LLDP/CDP używając SNMP."""
    if not community: return []
    host = target_device.get("hostname") or target_device.get("ip")
    if not host: return []
    print(f"⟶ SNMP LLDP/CDP dla {host}...") # Usunięto community z logu dla bezpieczeństwa

    conns = []

    # LLDP
    lldp_neighbors = snmp_utils.snmp_get_lldp_neighbors(host, community)
    if lldp_neighbors is None: print(f"  ⓘ LLDP SNMP: Błąd lub brak wsparcia dla {host}")
    elif lldp_neighbors:
        print(f"  ✓ LLDP SNMP: Znaleziono {len(lldp_neighbors)} sąsiadów dla {host}")
        for ifidx, sysname, portid in lldp_neighbors:
            local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
            conns.append(_format_connection(host, local_if_name, sysname, portid, None, "LLDP"))
    else: print(f"  ⓘ LLDP SNMP: Brak sąsiadów LLDP dla {host}")

    # CDP
    cdp_neighbors = snmp_utils.snmp_get_cdp_neighbors(host, community)
    if cdp_neighbors is None: print(f"  ⓘ CDP SNMP: Błąd lub brak wsparcia dla {host}")
    elif cdp_neighbors:
        print(f"  ✓ CDP SNMP: Znaleziono {len(cdp_neighbors)} sąsiadów dla {host}")
        for ifidx, dev_id, portid in cdp_neighbors:
            local_if_name = idx2name.get(ifidx, f"ifIndex {ifidx}")
            cleaned_dev_id = dev_id.split('.')[0] # Proste czyszczenie hostname
            conns.append(_format_connection(host, local_if_name, cleaned_dev_id, portid, None, "CDP"))
    else: print(f"  ⓘ CDP SNMP: Brak sąsiadów CDP dla {host}")

    return conns

def find_via_api_fdb(api: LibreNMSAPI, phys_map, target_device):
    """Odkrywanie przez API FDB LibreNMS."""
    dev_id = target_device.get("device_id")
    host = target_device.get("hostname") or target_device.get("ip", f"ID:{dev_id}")
    if not dev_id: return []

    print(f"⟶ API-FDB dla {host}")
    conns = []
    try:
        ports = api.get_ports(str(dev_id))
        if ports is None: # Obsługa błędu API
             print(f"  ⚠ API-FDB: Błąd pobierania portów dla {host}")
             return []
        if not ports:
            print(f"  ⓘ API-FDB: Brak portów dla {host}")
            return []

        fdb_checked = False
        for p in ports:
            pid = p.get("port_id")
            local_if = p.get("ifName", "") or p.get("ifDescr", f"PortID:{pid}")
            if not pid: continue

            fdb_entries = api.get_port_fdb(str(dev_id), str(pid))
            if fdb_entries is None: # Obsługa błędu API (np. inny niż 400)
                 print(f"  ⚠ API-FDB: Błąd pobierania FDB dla portu {local_if} (ID:{pid}) na {host}")
                 continue
            if not fdb_entries:
                continue

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

        if not fdb_checked:
            print(f"  ⓘ API-FDB: Nie znaleziono żadnych wpisów FDB przez API dla {host}")

    except Exception as e:
        print(f"  ⚠ API-FDB: Ogólny błąd dla {host}: {e}")

    return conns


def find_via_snmp_fdb(phys_map, target_device, community, idx2name):
    """Odkrywanie przez SNMP FDB (Bridge-MIB)."""
    if not community: return []
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    print(f"⟶ SNMP FDB (Bridge-MIB) dla {host}...")

    base2if = snmp_utils.snmp_get_bridge_baseport_ifindex(host, community)
    if base2if is None: return []

    fdb_entries = snmp_utils.snmp_get_fdb_entries(host, community)
    if fdb_entries is None: return []
    if not fdb_entries:
        print(f"  ⓘ SNMP FDB: Brak wpisów FDB przez SNMP dla {host}")
        return []

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


def find_via_qbridge_snmp(phys_map, target_device, community, idx2name):
    """Odkrywanie przez SNMP FDB (Q-Bridge-MIB)."""
    if not community: return []
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    print(f"⟶ SNMP FDB (Q-Bridge-MIB) dla {host}...")

    base2if = snmp_utils.snmp_get_bridge_baseport_ifindex(host, community)
    if base2if is None: return []

    qbridge_fdb_entries = snmp_utils.snmp_get_qbridge_fdb(host, community)
    if qbridge_fdb_entries is None: return []
    if not qbridge_fdb_entries:
        print(f"  ⓘ SNMP Q-BRIDGE: Brak wpisów Q-BRIDGE FDB dla {host}")
        return []

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

def find_via_cli(host, username, password):
    """Odkrywanie przez CLI (opakowanie na cli_utils)."""
    if not username or not password: return []
    return cli_utils.cli_get_neighbors_enhanced(host, username, password)


def find_via_arp_snmp(phys_map, target_device, community, idx2name):
    """Odkrywanie przez SNMP ARP."""
    if not community: return []
    host = target_device.get("hostname") or target_device.get("ip")
    dev_id = target_device.get("device_id")
    if not host or not dev_id: return []
    print(f"⟶ SNMP ARP dla {host}...")

    arp_entries = snmp_utils.snmp_get_arp_entries(host, community)
    if arp_entries is None: return []
    if not arp_entries:
        print(f"  ⓘ SNMP ARP: Brak wpisów ARP dla {host}")
        return []

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