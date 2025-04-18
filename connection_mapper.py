#!/usr/bin/env python3

import os
import re
import json
import pprint # Dla lepszego debugowania, można potem usunąć
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, nextCmd
)
from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException
from config import get_config
from librenms_api import LibreNMSAPI

IP_LIST_FILE = "ip_list.txt"
DEVICE_CREDENTIALS_FILE = "device_credentials.json"
OUTPUT_FILE  = "connections.txt"
JSON_OUTPUT_FILE = "connections.json"

# --- Funkcje load_ip_list, build_phys_mac_map (bez zmian) ---
def load_ip_list(path=IP_LIST_FILE):
    if not os.path.exists(path):
        print(f"Plik {path} nie istnieje.")
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except Exception as e:
        print(f"Błąd odczytu pliku {path}: {e}")
        return []

def build_phys_mac_map(api: LibreNMSAPI):
    """Buduje mapę MAC -> info o porcie (z wszystkich urządzeń w LibreNMS)"""
    phys = {}
    all_devices = api.get_devices()
    if not all_devices:
        print("Nie udało się pobrać urządzeń z LibreNMS API.")
        return {}

    print(f"Pobieranie portów dla {len(all_devices)} urządzeń...")
    count = 0
    for d in all_devices:
        count += 1
        dev_id = d.get("device_id")
        host   = d.get("hostname","")
        ip     = d.get("ip", "")
        if not dev_id:
            continue

        # print(f" ({count}/{len(all_devices)}) Przetwarzanie portów dla: {host or ip} (ID: {dev_id})") # Opcjonalny log
        try:
            ports = api.get_ports(str(dev_id))
            if not ports:
                continue

            for p in ports:
                mac = (p.get("ifPhysAddress") or "").lower().replace(":", "").replace("-", "").strip()
                pid = p.get("port_id")
                if mac and pid and len(mac) == 12:
                    phys[mac] = {
                        "device_id": dev_id,
                        "hostname":  host,
                        "ip":        ip,
                        "port_id":   pid,
                        "ifName":    p.get("ifName",""),
                        "ifDescr":   p.get("ifDescr", ""),
                        "ifIndex":   p.get("ifIndex")
                    }
        except Exception as e:
            print(f" Błąd podczas pobierania portów dla {host or ip} (ID: {dev_id}): {e}")

    print(f"Zbudowano mapę fizycznych MAC adresów: {len(phys)} wpisów.")
    return phys

# --- Funkcje find_connections_via_api, snmp_get_*, find_via_* (bez zmian) ---
# --- Poniżej wklejone te funkcje dla kompletności ---

# 1. LibreNMS API‑FDB
def find_connections_via_api(api: LibreNMSAPI, phys_map, target):
    conns = []
    dev_id = target.get("device_id")
    host = target.get("hostname") or target.get("ip", f"ID:{dev_id}")
    if not dev_id: return []

    print(f"⟶ API-FDB dla {host}")
    ports = api.get_ports(str(dev_id))
    if not ports:
        print(f"⚠ API-FDB: Brak portów dla {host}")
        return []

    fdb_found = False
    for p in ports:
        pid      = p.get("port_id")
        local_if = p.get("ifName","") or p.get("ifDescr", f"PortID:{pid}")
        if not pid: continue

        try:
            fdb_entries = api.get_port_fdb(str(dev_id), str(pid))
            if not fdb_entries:
                continue

            fdb_found = True
            for e in fdb_entries:
                mac = (e.get("mac_address") or "").lower().replace(":", "").replace("-", "").strip()
                if len(mac) != 12: continue

                neigh = phys_map.get(mac)
                if neigh:
                    if neigh['device_id'] != dev_id:
                        conns.append({
                            "local_host":    target.get("hostname", host),
                            "local_if":      local_if,
                            "neighbor_host": neigh["hostname"] or neigh.get("ip", f"ID:{neigh['device_id']}"),
                            "neighbor_if":   neigh["ifName"] or neigh.get("ifDescr", f"PortID:{neigh['port_id']}"),
                            "vlan":          e.get("vlan_id") or e.get("vlanid"),
                            "via":           "API-FDB"
                        })
        except Exception as e_fdb:
            print(f"⚠ API-FDB: Błąd FDB dla portu {local_if} (ID:{pid}) na {host}: {e_fdb}")

    if not fdb_found:
        print(f"⚠ API-FDB: Nie znaleziono żadnych wpisów FDB przez API dla {host}")
    return conns

# 2. SNMP‑Bridge‑MIB
def snmp_get_bridge_baseport_ifindex(host, community):
    base2if = {}
    try:
        for errI, errS, _, vb in nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=0), UdpTransportTarget((host,161), timeout=2, retries=1), ContextData(),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.17.1.4.1.2')),
            lexicographicMode=False):
            if errI: raise Exception(f"SNMP Error Index: {errI}")
            if errS: raise Exception(f"SNMP Error Status: {errS.prettyPrint()}")
            parts = vb[0][0].prettyPrint().split('.')
            base  = int(parts[-1])
            base2if[base] = int(vb[0][1])
    except Exception as e:
        print(f"⚠ SNMP BasePortIfIndex: Błąd dla {host}: {e}")
        return None
    return base2if

def snmp_get_fdb_entries(host, community):
    entries = []
    try:
        for errI, errS, _, vb in nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=0), UdpTransportTarget((host,161), timeout=5, retries=1), ContextData(),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.17.4.3.1.1')),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.17.4.3.1.2')),
            lexicographicMode=False):
            if errI: raise Exception(f"SNMP Error Index: {errI}")
            if errS: raise Exception(f"SNMP Error Status: {errS.prettyPrint()}")
            mac = vb[0][1].asOctets()
            mac = ''.join(f"{b:02x}" for b in mac)
            if len(mac) != 12: continue
            base_port = int(vb[1][1])
            entries.append((mac, base_port))
    except Exception as e:
        print(f"⚠ SNMP FDB: Błąd dla {host}: {e}")
        return None
    return entries

def find_via_snmp_fdb(api: LibreNMSAPI, phys_map, target, community):
    """2. SNMP‑Bridge‑MIB"""
    if not community: return []
    host = target.get("hostname") or target.get("ip")
    dev_id = target.get("device_id")
    print(f"⟶ SNMP-FDB fallback na {host} (używam community: '{community}')")

    base2if = snmp_get_bridge_baseport_ifindex(host, community)
    if base2if is None: return []

    fdb = snmp_get_fdb_entries(host, community)
    if fdb is None: return []
    if not fdb:
        print(f"ⓘ SNMP-FDB: Brak wpisów FDB przez SNMP dla {host}")
        return []

    idx2name = { p["ifIndex"]: p.get("ifName","") or p.get("ifDescr", "")
                 for p in api.get_ports(str(dev_id)) if p.get("ifIndex") }
    conns = []
    for mac, base in fdb:
        neigh = phys_map.get(mac)
        if neigh and neigh['device_id'] != dev_id:
            ifidx = base2if.get(base)
            if ifidx:
                local = idx2name.get(ifidx, f"ifIndex {ifidx}")
                conns.append({
                    "local_host":    host, "local_if":      local,
                    "neighbor_host": neigh["hostname"] or neigh.get("ip", f"ID:{neigh['device_id']}"),
                    "neighbor_if":   neigh["ifName"] or neigh.get("ifDescr", f"PortID:{neigh['port_id']}"),
                    "vlan":          None, "via":           "SNMP-FDB"
                })
    return conns

# 3. SNMP‑Q‑Bridge‑MIB
def snmp_get_qbridge_fdb(host, community):
    entries = []
    OID_ADDR = '1.3.6.1.2.1.17.7.1.2.2.1.1'; OID_PORT = '1.3.6.1.2.1.17.7.1.2.2.1.2'
    base_len = len(OID_ADDR.split('.'))
    try:
        for errI, errS, _, vb in nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=0), UdpTransportTarget((host,161), timeout=5, retries=1), ContextData(),
            ObjectType(ObjectIdentity(OID_ADDR)), ObjectType(ObjectIdentity(OID_PORT)),
            lexicographicMode=False):
            if errI: raise Exception(f"SNMP Error Index: {errI}")
            if errS: raise Exception(f"SNMP Error Status: {errS.prettyPrint()}")
            oid_full = vb[0][0].prettyPrint()
            if not oid_full.startswith(OID_ADDR + '.'): continue
            parts = oid_full.split('.');
            if len(parts) < base_len + 1 + 6: continue
            vlan = int(parts[base_len]); mac_bytes = vb[0][1].asOctets()
            mac = ''.join(f"{b:02x}" for b in mac_bytes)
            if len(mac) != 12: continue
            bridge_port = int(vb[1][1])
            entries.append((mac, vlan, bridge_port))
    except Exception as e:
        print(f"⚠ SNMP Q-BRIDGE FDB: Błąd dla {host}: {e}")
        return None
    return entries

def find_via_qbridge(api: LibreNMSAPI, phys_map, target, community):
    """3. SNMP‑Q‑Bridge‑MIB"""
    if not community: return []
    host = target.get("hostname") or target.get("ip"); dev_id = target.get("device_id")
    print(f"⟶ SNMP-QBRIDGE fallback na {host} (używam community: '{community}')")

    entries = snmp_get_qbridge_fdb(host, community)
    if entries is None: return []
    if not entries:
        print(f"ⓘ SNMP-QBRIDGE: Brak wpisów Q-BRIDGE FDB dla {host}")
        return []

    base2if = snmp_get_bridge_baseport_ifindex(host, community)
    if base2if is None:
        print(f"⚠ SNMP-QBRIDGE: Nie udało się pobrać mapowania BasePort->IfIndex dla {host}, pomijam wyniki Q-BRIDGE.")
        return []

    idx2name = { p["ifIndex"]: p.get("ifName","") or p.get("ifDescr", "")
                 for p in api.get_ports(str(dev_id)) if p.get("ifIndex") }
    conns = []
    for mac, vlan, base in entries:
        neigh = phys_map.get(mac)
        if neigh and neigh['device_id'] != dev_id:
            ifidx = base2if.get(base)
            if ifidx:
                local = idx2name.get(ifidx, f"ifIndex {ifidx}")
                conns.append({
                    "local_host":    host, "local_if":      local,
                    "neighbor_host": neigh["hostname"] or neigh.get("ip", f"ID:{neigh['device_id']}"),
                    "neighbor_if":   neigh["ifName"] or neigh.get("ifDescr", f"PortID:{neigh['port_id']}"),
                    "vlan":          vlan, "via":           f"SNMP-QBRIDGE"
                })
    return conns

# 4. SNMP‑ARP
def snmp_get_arp_entries(host, community):
    entries = []
    try:
        for errI, errS, _, vb in nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=0), UdpTransportTarget((host,161), timeout=5, retries=1), ContextData(),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.4.22.1.2')),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.4.22.1.3')),
            lexicographicMode=False):
            if errI: raise Exception(f"SNMP Error Index: {errI}")
            if errS: raise Exception(f"SNMP Error Status: {errS.prettyPrint()}")
            parts = vb[0][0].prettyPrint().split('.');
            if len(parts) < 5: continue
            ifidx = int(parts[-5]); mac = vb[0][1].asOctets()
            mac = ''.join(f"{b:02x}" for b in mac)
            if len(mac) != 12: continue
            ipaddr= vb[1][1].prettyPrint()
            entries.append((ipaddr, mac, ifidx))
    except Exception as e:
        print(f"⚠ SNMP ARP: Błąd dla {host}: {e}")
        return None
    return entries

def find_via_arp(api: LibreNMSAPI, phys_map, target, community):
    """4. SNMP‑ARP"""
    if not community: return []
    host = target.get("hostname") or target.get("ip"); dev_id = target.get("device_id")
    print(f"⟶ SNMP-ARP fallback na {host} (używam community: '{community}')")

    arp = snmp_get_arp_entries(host, community)
    if arp is None: return []
    if not arp:
        print(f"ⓘ SNMP-ARP: Brak wpisów ARP dla {host}")
        return []

    idx2name = { p["ifIndex"]: p.get("ifName","") or p.get("ifDescr", "")
                 for p in api.get_ports(str(dev_id)) if p.get("ifIndex") }
    conns = []
    for ipaddr, mac, ifidx in arp:
        neigh = phys_map.get(mac)
        if neigh and neigh['device_id'] != dev_id:
            local = idx2name.get(ifidx, f"ifIndex {ifidx}")
            conns.append({
                "local_host":    host, "local_if":      local,
                "neighbor_host": neigh["hostname"] or neigh.get("ip", ipaddr),
                "neighbor_if":   neigh["ifName"] or neigh.get("ifDescr", f"MAC:{mac}"),
                "vlan":          None, "via":           f"SNMP-ARP({ipaddr})"
            })
    return conns

# 5. LLDP / CDP via SNMP
def snmp_get_lldp_neighbors(host, community):
    OID_LLDP_REM_CHASSIS_ID_SUBTYPE = '1.0.8802.1.1.2.1.4.1.1.4'; OID_LLDP_REM_CHASSIS_ID = '1.0.8802.1.1.2.1.4.1.1.5'
    OID_LLDP_REM_PORT_ID_SUBTYPE = '1.0.8802.1.1.2.1.4.1.1.6'; OID_LLDP_REM_PORT_ID = '1.0.8802.1.1.2.1.4.1.1.7'
    OID_LLDP_REM_PORT_DESCR = '1.0.8802.1.1.2.1.4.1.1.8'; OID_LLDP_REM_SYS_NAME = '1.0.8802.1.1.2.1.4.1.1.9'
    OID_LLDP_REM_SYS_DESCR = '1.0.8802.1.1.2.1.4.1.1.10'
    OID_LLDP_LOC_PORT_ID_SUBTYPE = '1.0.8802.1.1.2.1.3.7.1.2'; OID_LLDP_LOC_PORT_ID = '1.0.8802.1.1.2.1.3.7.1.3'

    neighs = {}; final_neighs = None
    try:
        for errI, errS, _, vb in nextCmd( # Pobieranie REM MIB
            SnmpEngine(), CommunityData(community), UdpTransportTarget((host, 161), timeout=5, retries=1), ContextData(),
            ObjectType(ObjectIdentity(OID_LLDP_REM_SYS_NAME)), ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_ID)),
            ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_DESCR)), lexicographicMode=False):
            if errI: raise Exception(f"SNMP Error Index: {errI}"); break
            if errS: raise Exception(f"SNMP Error Status: {errS.prettyPrint()}"); break
            oid_parts = vb[0][0].prettyPrint().split('.'); base_len_rem_sys_name = len(OID_LLDP_REM_SYS_NAME.split('.'))
            if len(oid_parts) < base_len_rem_sys_name + 2: continue
            timeMark = int(oid_parts[base_len_rem_sys_name]); localPortNum = int(oid_parts[base_len_rem_sys_name + 1])
            key = (timeMark, localPortNum);
            if key not in neighs: neighs[key] = {}
            neighs[key]['sysname'] = vb[0][1].prettyPrint(); neighs[key]['port_id'] = vb[1][1].prettyPrint()
            neighs[key]['port_descr'] = vb[2][1].prettyPrint()
        else: # Wykonane jeśli pętla zakończyła się normalnie (bez break)
            loc_port_map = {} # Pobieranie LOC MIB
            for errI, errS, _, vb in nextCmd(
                SnmpEngine(), CommunityData(community), UdpTransportTarget((host, 161), timeout=2, retries=1), ContextData(),
                ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID_SUBTYPE)), ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID)),
                lexicographicMode=False):
                if errI: raise Exception(f"SNMP Error Index: {errI}"); break
                if errS: raise Exception(f"SNMP Error Status: {errS.prettyPrint()}"); break
                oid_parts = vb[0][0].prettyPrint().split('.'); base_len_loc_port = len(OID_LLDP_LOC_PORT_ID_SUBTYPE.split('.'))
                if len(oid_parts) < base_len_loc_port + 1: continue
                localPortNum = int(oid_parts[base_len_loc_port]); subtype = int(vb[0][1]); value = vb[1][1].prettyPrint()
                if subtype == 5: # Preferujemy ifIndex
                    try: loc_port_map[localPortNum] = int(value)
                    except ValueError: print(f"⚠ SNMP LLDP LOC: Nie można sparsować ifIndex '{value}' dla localPortNum {localPortNum} na {host}")
            else: # Łączenie danych
                final_neighs = []
                for key, data in neighs.items():
                    timeMark, localPortNum = key; ifidx = loc_port_map.get(localPortNum)
                    if ifidx:
                        remote_if = data.get('port_id', '');
                        if not remote_if or remote_if.startswith('0x') or len(remote_if) > 30: remote_if = data.get('port_descr', remote_if)
                        final_neighs.append((ifidx, data.get('sysname', 'UnknownSystem'), remote_if))
    except Exception as e:
        print(f"⚠ SNMP LLDP: Błąd dla {host} z community '{community}': {e}")
        return None # Błąd ogólny
    return final_neighs # Może być pusta lista lub None

def snmp_get_cdp_neighbors(host, community):
    OID_CDP_IFINDEX = '1.3.6.1.4.1.9.9.23.1.1.1.1.6'; OID_CDP_DEV_ID = '1.3.6.1.4.1.9.9.23.1.2.1.1.6'; OID_CDP_PORT_ID = '1.3.6.1.4.1.9.9.23.1.2.1.1.7'
    neighs = {}; final_neighs = None
    try:
        for errI, errS, _, vb in nextCmd(
            SnmpEngine(), CommunityData(community), UdpTransportTarget((host,161), timeout=5, retries=1), ContextData(),
            ObjectType(ObjectIdentity(OID_CDP_IFINDEX)), ObjectType(ObjectIdentity(OID_CDP_DEV_ID)),
            ObjectType(ObjectIdentity(OID_CDP_PORT_ID)), lexicographicMode=False):
            if errI: raise Exception(f"SNMP Error Index: {errI}"); break
            if errS: raise Exception(f"SNMP Error Status: {errS.prettyPrint()}"); break
            oid_key_str = '.'.join(vb[0][0].prettyPrint().split('.')[:-1])
            if oid_key_str not in neighs: neighs[oid_key_str] = {}
            oid_name = vb[0][0].prettyPrint(); value = vb[0][1].prettyPrint()
            if oid_name.startswith(OID_CDP_IFINDEX):
                try: neighs[oid_key_str]['ifindex'] = int(value)
                except ValueError: print(f"⚠ SNMP CDP: Nie można sparsować ifIndex '{value}' dla {oid_key_str} na {host}")
            elif oid_name.startswith(OID_CDP_DEV_ID): neighs[oid_key_str]['dev_id'] = value
            elif oid_name.startswith(OID_CDP_PORT_ID): neighs[oid_key_str]['port_id'] = value
        else:
            final_neighs = []
            for data in neighs.values():
                if 'ifindex' in data and 'dev_id' in data and 'port_id' in data:
                    final_neighs.append((data['ifindex'], data['dev_id'], data['port_id']))
    except Exception as e:
        print(f"⚠ SNMP CDP: Błąd dla {host} z community '{community}': {e}")
        return None
    return final_neighs # Może być pusta lista lub None

def find_via_lldp_cdp(api: LibreNMSAPI, target, community):
    """5. LLDP + CDP via SNMP"""
    if not community: return []
    host = target.get("hostname") or target.get("ip"); dev_id = target.get("device_id")
    print(f"⟶ LLDP+CDP fallback na {host} (używam community: '{community}')")

    idx2name = { p["ifIndex"]: p.get("ifName","") or p.get("ifDescr", "")
                 for p in api.get_ports(str(dev_id)) if p.get("ifIndex") }

    conns = []
    lldp_neighbors = snmp_get_lldp_neighbors(host, community)
    if lldp_neighbors is None: print(f"ⓘ LLDP: Błąd lub brak wsparcia SNMP LLDP dla {host}")
    elif lldp_neighbors:
        print(f"✓ LLDP: Znaleziono {len(lldp_neighbors)} sąsiadów LLDP dla {host}")
        for ifidx, sysname, portid in lldp_neighbors:
            local = idx2name.get(ifidx, f"ifIndex {ifidx}"); conns.append({
                "local_host": host, "local_if": local, "neighbor_host": sysname.strip(),
                "neighbor_if": portid.strip(), "vlan": None, "via": "LLDP" })
    else: print(f"ⓘ LLDP: Brak sąsiadów LLDP dla {host}")

    cdp_neighbors = snmp_get_cdp_neighbors(host, community)
    if cdp_neighbors is None: print(f"ⓘ CDP: Błąd lub brak wsparcia SNMP CDP dla {host}")
    elif cdp_neighbors:
        print(f"✓ CDP: Znaleziono {len(cdp_neighbors)} sąsiadów CDP dla {host}")
        for ifidx, dev, portid in cdp_neighbors:
             local = idx2name.get(ifidx, f"ifIndex {ifidx}"); conns.append({
                 "local_host": host, "local_if": local, "neighbor_host": dev.strip(),
                 "neighbor_if": portid.strip(), "vlan": None, "via": "CDP" })
    else: print(f"ⓘ CDP: Brak sąsiadów CDP dla {host}")

    unique_conns = []; seen = set() # Prosta deduplikacja
    for c in conns:
        key_part1 = f"{c['local_host']}:{c['local_if']}"; key_part2 = f"{c['neighbor_host']}:{c['neighbor_if']}"
        conn_key = tuple(sorted((key_part1, key_part2)))
        if conn_key not in seen: unique_conns.append(c); seen.add(conn_key)
    return unique_conns


# 6. CLI‑fallback (ZMODYFIKOWANA FUNKCJA)
def cli_get_neighbors_enhanced(host, username, password):
    """
    Rozszerzona wersja próby pobrania sąsiadów przez CLI (LLDP, potem CDP).
    Zaktualizowano parsowanie LLDP na podstawie dostarczonego formatu wyjściowego.
    DODANO: Wyciąganie VLAN ID, jeśli jest dostępne.
    """
    if not username or not password: return []
    print(f"⟶ CLI fallback na {host}")
    device_params = {
        "device_type": "autodetect", "host": host, "username": username, "password": password,
        "global_delay_factor": 2, "session_log": f"{host}_netmiko_session.log"
    }
    conns = []; conn = None
    try:
        print(f"  CLI: Łączenie z {host}...");
        conn = ConnectHandler(**device_params)
        print(f"  CLI: Połączono z {host} ({conn.device_type})")

        # --- Próba LLDP przez CLI (NOWA LOGIKA PARSOWANIA + VLAN ID) ---
        try:
            lldp_command = "show lldp neighbors detail"
            print(f"  CLI: Wykonywanie '{lldp_command}'...")
            lldp_output = conn.send_command_timing(lldp_command, delay_factor=5)

            if lldp_output:
                print(f"  CLI: Otrzymano dane LLDP, próba parsowania (nowy format)...")
                parsed_count = 0
                # Usuń potencjalny nagłówek przed podziałem na bloki
                header_line_match = re.search(r'Device ID\s+Local Intf\s+Hold-time', lldp_output, re.IGNORECASE)
                if header_line_match:
                    lldp_data_start_index = header_line_match.end()
                    lldp_data = lldp_output[lldp_data_start_index:]
                else:
                    first_chassis_match = re.search(r'Chassis id:', lldp_output, re.IGNORECASE)
                    if first_chassis_match:
                         lldp_data = lldp_output[first_chassis_match.start():]
                    else:
                         lldp_data = lldp_output

                # Podziel dane na bloki zaczynające się od "Chassis id:"
                blocks = re.split(r'\n(?=Chassis id:)', lldp_data, flags=re.IGNORECASE)

                for block in blocks:
                    if not block.strip() or not block.lower().startswith('chassis id:'):
                        continue

                    # Wyciągnij dane używając wyrażeń regularnych szukających konkretnych linii
                    local_if_match = re.search(r'^Local Port id:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)
                    remote_sys_match = re.search(r'^System Name:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)
                    remote_port_id_match = re.search(r'^Port id:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)
                    remote_port_desc_match = re.search(r'^Port Description:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)
                    # *** NOWOŚĆ: Szukanie VLAN ID ***
                    vlan_match = re.search(r'^Vlan ID:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)

                    if local_if_match and remote_sys_match and remote_port_id_match:
                        local_if = local_if_match.group(1).strip()
                        if not local_if or 'not advertised' in local_if.lower():
                             print(f"  CLI-LLDP: Pominięto sąsiada - brak poprawnego Local Port id w bloku:\n{block[:200]}...")
                             continue

                        remote_sys = remote_sys_match.group(1).strip()
                        remote_port = remote_port_id_match.group(1).strip()

                        if (not remote_port or 'not advertised' in remote_port.lower()) and remote_port_desc_match:
                             remote_port_desc = remote_port_desc_match.group(1).strip()
                             if remote_port_desc and 'not advertised' not in remote_port_desc.lower():
                                 remote_port = remote_port_desc

                        # *** NOWOŚĆ: Przetwarzanie VLAN ID ***
                        vlan_id = None # Domyślnie brak VLANu
                        if vlan_match:
                            vlan_value = vlan_match.group(1).strip()
                            # Sprawdź czy znaleziono wartość i nie jest to "not advertised"
                            if vlan_value and 'not advertised' not in vlan_value.lower():
                                vlan_id = vlan_value # Przypisz znaleziony VLAN ID

                        # Dodaj tylko jeśli mamy wszystkie kluczowe informacje
                        if local_if and remote_sys and remote_port:
                             conns.append({
                                 "local_host": host,
                                 "local_if": local_if,
                                 "neighbor_host": remote_sys,
                                 "neighbor_if": remote_port,
                                 "vlan": vlan_id, # *** Użycie znalezionego VLAN ID ***
                                 "via": "CLI-LLDP"
                             })
                             parsed_count += 1
                        else:
                             print(f"  CLI-LLDP: Pominięto sąsiada - brak kompletu danych (local_if, remote_sys, remote_port) w bloku:\n{block[:200]}...")

                if parsed_count > 0:
                    print(f"✓ CLI-LLDP: Sparsowano {parsed_count} połączeń LLDP dla {host} (nowy format).")
                elif lldp_output :
                    print(f"ⓘ CLI-LLDP: Otrzymano dane, ale nie udało się sparsować połączeń (nowy format) dla {host}.")

            else:
                 print(f"ⓘ CLI-LLDP: Brak danych wyjściowych LLDP dla {host}.")

        except Exception as e_lldp:
            print(f"⚠ Błąd CLI-LLDP: Nie udało się uzyskać/sparsować danych LLDP dla {host}: {e_lldp}")

        # --- Próba CDP przez CLI (bez zmian) ---
        if not conns:
            try:
                cdp_command = "show cdp neighbors detail"
                print(f"  CLI: Wykonywanie '{cdp_command}'...")
                cdp_output = conn.send_command_timing(cdp_command, delay_factor=4)
                if cdp_output and "Device ID" in cdp_output:
                    print(f"  CLI: Otrzymano dane CDP, próba parsowania...")
                    cdp_blocks = re.split(r'^-{10,}', cdp_output, flags=re.MULTILINE)
                    parsed_count_cdp = 0
                    for block in cdp_blocks:
                        dev_id_match = re.search(r'Device ID:\s*(\S+)', block, re.IGNORECASE)
                        local_if_match = re.search(r'Interface:\s*(\S+?),', block, re.IGNORECASE)
                        remote_if_match = re.search(r'Port ID \(outgoing port\):\s*(\S+)', block, re.IGNORECASE)
                        if dev_id_match and local_if_match and remote_if_match:
                            conns.append({ "local_host": host, "local_if": local_if_match.group(1), "neighbor_host": dev_id_match.group(1),
                                           "neighbor_if": remote_if_match.group(1), "vlan": None, "via": "CLI-CDP" })
                            parsed_count_cdp += 1
                    if parsed_count_cdp > 0: print(f"✓ CLI-CDP: Sparsowano {parsed_count_cdp} połączeń CDP dla {host}")
                    elif cdp_output : print(f"ⓘ CLI-CDP: Otrzymano dane, ale nie udało się sparsować połączeń dla {host}.")
            except Exception as e_cdp:
                print(f"ⓘ CLI-CDP: Nie udało się uzyskać/sparsować danych CDP dla {host}: {e_cdp}")

        conn.disconnect()
        print(f"  CLI: Rozłączono z {host}")

    except (NetmikoTimeoutException, NetmikoAuthenticationException) as e_auth:
        print(f"⚠ Błąd CLI: Problem z połączeniem/autoryzacją SSH do {host}: {e_auth}")
    except Exception as e_conn:
        print(f"⚠ Błąd CLI: Ogólny błąd SSH dla {host}: {e_conn}")
        if conn and conn.is_alive():
            try: conn.disconnect()
            except: pass

    return conns


# --- Główna funkcja main (bez zmian w stosunku do wersji z JSON credentials) ---
def main():
    try:
        cfg = get_config()
    except ValueError as e:
        print(f"Błąd krytyczny konfiguracji: {e}")
        return

    if not cfg.get("base_url") or not cfg.get("api_key"):
        print("Brak BASE_URL lub API_KEY w konfiguracji. Nie można kontynuować.")
        return

    api = LibreNMSAPI(cfg["base_url"], cfg["api_key"])
    default_snmp_comm = cfg.get("default_snmp_comm")
    cli_user = cfg.get("cli_username")
    cli_pass = cfg.get("cli_password")

    # Wczytaj specyficzne dane SNMP z pliku JSON
    device_credentials = {}
    try:
        with open(DEVICE_CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
            creds_list = json.load(f)
            device_credentials = {cred['identifier']: cred['snmp_community']
                                  for cred in creds_list if 'identifier' in cred and 'snmp_community' in cred}
        print(f"✓ Wczytano dane SNMP dla {len(device_credentials)} urządzeń z {DEVICE_CREDENTIALS_FILE}")
    except FileNotFoundError:
        print(f"ⓘ Informacja: Plik {DEVICE_CREDENTIALS_FILE} nie znaleziony.")
        if not default_snmp_comm:
             print("  Brak również domyślnego SNMP_COMMUNITY w .env. Metody SNMP nie będą używane.")
    except json.JSONDecodeError as e:
         print(f"⚠ Błąd parsowania pliku JSON {DEVICE_CREDENTIALS_FILE}: {e}")
    except Exception as e:
        print(f"⚠ Błąd wczytywania pliku {DEVICE_CREDENTIALS_FILE}: {e}")

    print("Buduję globalną mapę MAC → (urządzenie, port)...")
    phys_map = build_phys_mac_map(api)
    if not phys_map:
        print("⚠ Nie udało się zbudować mapy MAC. Dalsze działanie (FDB/ARP) będzie ograniczone.")

    ips = load_ip_list()
    if not ips:
        print("Brak adresów IP w pliku ip_list.txt do przetworzenia.")
        return

    all_conns = []
    all_devices_from_api = api.get_devices()
    if not all_devices_from_api:
        print("⚠ Nie udało się pobrać listy urządzeń z API. Zatrzymuję.")
        return

    device_lookup = {d.get("ip"): d for d in all_devices_from_api if d.get("ip")}
    hostname_lookup = {d.get("hostname"): d for d in all_devices_from_api if d.get("hostname")}

    processed_devices_count = 0
    for ip_or_host in ips:
        processed_devices_count += 1
        print(f"\n--- Przetwarzanie ({processed_devices_count}/{len(ips)}): {ip_or_host} ---")

        target_device = device_lookup.get(ip_or_host) or hostname_lookup.get(ip_or_host)
        if not target_device:
            found = [d for d in all_devices_from_api if ip_or_host in d.get("hostname", "")]
            if len(found) == 1: target_device = found[0]
            elif len(found) > 1:
                print(f"⚠ Znaleziono wiele urządzeń pasujących do '{ip_or_host}': {[d.get('hostname') for d in found]}. Pomijam.")
                continue

        if not target_device or not target_device.get("device_id"):
            print(f"⚠ Nie znaleziono urządzenia '{ip_or_host}' w LibreNMS lub brak jego device_id.")
            continue

        dev_id = target_device['device_id']
        dev_host = target_device.get('hostname')
        dev_ip = target_device.get('ip')
        primary_identifier = dev_host or dev_ip
        secondary_identifier = dev_ip if dev_host and dev_ip != dev_host else None

        # Ustalanie community string dla bieżącego urządzenia
        specific_snmp_comm = device_credentials.get(primary_identifier)
        comm_source = f"JSON dla '{primary_identifier}'"
        if not specific_snmp_comm and secondary_identifier:
            specific_snmp_comm = device_credentials.get(secondary_identifier)
            if specific_snmp_comm: comm_source = f"JSON dla '{secondary_identifier}'"
        if not specific_snmp_comm:
            specific_snmp_comm = default_snmp_comm
            if specific_snmp_comm: comm_source = "domyślny z .env"
            else: comm_source = "brak"

        print(f"=== Analiza {dev_host or dev_ip} (ID {dev_id}) ===")
        if specific_snmp_comm and comm_source != "brak":
             print(f"  Używam SNMP community: '{specific_snmp_comm}' (źródło: {comm_source})")
        elif comm_source == "brak":
             print(f"  Brak community SNMP dla tego urządzenia. Metody SNMP zostaną pominięte.")

        # Kolejność prób odkrywania połączeń
        conns = []

        # 1. LLDP / CDP via SNMP (Tylko jeśli mamy community)
        if not conns and specific_snmp_comm:
            conns = find_via_lldp_cdp(api, target_device, specific_snmp_comm)

        # 2. API LibreNMS FDB (Nie wymaga SNMP)
        if not conns:
            conns = find_connections_via_api(api, phys_map, target_device)

        # 3. SNMP FDB (Bridge-MIB) (Tylko jeśli mamy community)
        if not conns and specific_snmp_comm:
            conns = find_via_snmp_fdb(api, phys_map, target_device, specific_snmp_comm)

        # 4. SNMP Q-BRIDGE FDB (VLAN-aware) (Tylko jeśli mamy community)
        if not conns and specific_snmp_comm:
            conns = find_via_qbridge(api, phys_map, target_device, specific_snmp_comm)

        # 5. CLI (Nie wymaga SNMP) - Używa ZMODYFIKOWANEJ funkcji
        if not conns and cli_user and cli_pass:
            target_for_cli = dev_host or dev_ip
            conns = cli_get_neighbors_enhanced(target_for_cli, cli_user, cli_pass)

        # 6. SNMP ARP (Najmniej dokładne dla L2) (Tylko jeśli mamy community)
        if not conns and specific_snmp_comm:
             conns = find_via_arp(api, phys_map, target_device, specific_snmp_comm)

        # Koniec prób

        if conns:
            print(f"✓ Znaleziono {len(conns)} połączeń dla {dev_host or dev_ip}:")
            unique_device_conns = []
            seen_device_links = set()
            for c in conns:
                # *** Poprawka w logowaniu - sprawdzaj czy VLAN nie jest None ***
                vlan_str = f"(VLAN {c.get('vlan')})" if c.get('vlan') is not None else ""
                key_part1 = f"{c['local_host']}:{c['local_if']}"
                key_part2 = f"{c['neighbor_host']}:{c['neighbor_if']}"
                conn_key = tuple(sorted((key_part1, key_part2))) + (c['via'],)

                if conn_key not in seen_device_links:
                    print(f"  {c['local_host']}:{c['local_if']} → "
                          f"{c['neighbor_host']}:{c['neighbor_if']} "
                          f"{vlan_str} via {c['via']}")
                    unique_device_conns.append(c)
                    seen_device_links.add(conn_key)
            all_conns.extend(unique_device_conns)
        else:
            print(f"❌ Nie wykryto żadnych połączeń dla {dev_host or dev_ip} żadną z metod.")

    # DODANE DEBUGOWANIE - można potem usunąć
    # print("\nDEBUG: Sprawdzanie zawartości all_conns PRZED globalną deduplikacją:")
    # pprint.pprint(all_conns)
    # print(f"DEBUG: Liczba elementów w all_conns: {len(all_conns)}\n")

    # Zapis wyników
    if all_conns:
        print(f"\n=== Podsumowanie ===")
        final_unique_conns = []
        seen_global_links = set()
        for c in all_conns:
            key_part1 = f"{c['local_host']}:{c['local_if']}"
            key_part2 = f"{c['neighbor_host']}:{c['neighbor_if']}"
            conn_key = tuple(sorted((key_part1, key_part2)))
            if conn_key not in seen_global_links:
                final_unique_conns.append(c)
                seen_global_links.add(conn_key)

        # DODANE DEBUGOWANIE - można potem usunąć
        # print("\nDEBUG: Sprawdzanie zawartości final_unique_conns PO globalnej deduplikacji (przed zapisem):")
        # pprint.pprint(final_unique_conns)
        # print(f"DEBUG: Liczba elementów w final_unique_conns: {len(final_unique_conns)}\n")

        print(f"Łącznie znaleziono {len(final_unique_conns)} unikalnych połączeń.")

        # Zapis do pliku tekstowego
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                sorted_conns = sorted(final_unique_conns, key=lambda x: (x['local_host'], x['local_if']))
                for c in sorted_conns:
                    # *** Poprawka w zapisie - sprawdzaj czy VLAN nie jest None ***
                    vlan_str = f"(VLAN {c.get('vlan')})" if c.get('vlan') is not None else ""
                    f.write(f"{c['local_host']}:{c['local_if']} → "
                            f"{c['neighbor_host']}:{c['neighbor_if']} "
                            f"{vlan_str} via {c['via']}\n")
            print(f"✅ Połączenia zapisane w {OUTPUT_FILE}")
        except Exception as e:
            print(f"⚠ Błąd zapisu do pliku {OUTPUT_FILE}: {e}")

        # Zapis do pliku JSON
        try:
            json_data = []
            for c in sorted_conns:
                 json_data.append({
                    "local_device": c['local_host'], "local_port": c['local_if'],
                    "remote_device": c['neighbor_host'], "remote_port": c['neighbor_if'],
                    "vlan": c.get('vlan'), # Zapisze null jeśli VLAN jest None
                    "discovery_method": c['via']
                 })
            with open(JSON_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=4, ensure_ascii=False)
            print(f"✅ Połączenia zapisane w {JSON_OUTPUT_FILE}")
        except Exception as e:
            print(f"⚠ Błąd zapisu do pliku {JSON_OUTPUT_FILE}: {e}")

    else:
        print("\n❌ Nie znaleziono żadnych połączeń dla podanych adresów IP.")

if __name__ == "__main__":
    main()