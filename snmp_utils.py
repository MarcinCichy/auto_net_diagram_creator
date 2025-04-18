# snmp_utils.py
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, nextCmd
)

# --- Funkcje pomocnicze SNMP ---
def _handle_snmp_error(host, operation_name, error_indication, error_status):
    """Pomocnicza funkcja do logowania błędów SNMP."""
    if error_indication:
        # Loguj tylko istotne błędy (np. timeout), ignoruj np. noSuchName jeśli spodziewane
        # if 'timeout' in str(error_indication).lower(): # Przykład filtrowania
        print(f"⚠ SNMP {operation_name}: Błąd dla {host}: {error_indication}")
        return True
    elif error_status and error_status.prettyPrint() != 'noSuchName': # Ignoruj 'noSuchName'
        print(f"⚠ SNMP {operation_name}: Błąd dla {host}: {error_status.prettyPrint()}")
        return True
    return False


def snmp_get_lldp_neighbors(host, community, timeout=5, retries=1):
    """Pobiera sąsiadów LLDP przez SNMP."""
    OID_LLDP_REM_SYS_NAME = '1.0.8802.1.1.2.1.4.1.1.9'
    OID_LLDP_REM_PORT_ID = '1.0.8802.1.1.2.1.4.1.1.7'
    OID_LLDP_REM_PORT_DESCR = '1.0.8802.1.1.2.1.4.1.1.8'
    OID_LLDP_LOC_PORT_ID_SUBTYPE = '1.0.8802.1.1.2.1.3.7.1.2'
    OID_LLDP_LOC_PORT_ID = '1.0.8802.1.1.2.1.3.7.1.3'

    neighs = {}; final_neighs = []
    try:
        # Pobieranie REM MIB
        for error_indication, error_status, _, var_binds_table in nextCmd(
            SnmpEngine(), CommunityData(community), UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_LLDP_REM_SYS_NAME)),
            ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_ID)),
            ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_DESCR)),
            lexicographicMode=False):

            if _handle_snmp_error(host, "LLDP REM", error_indication, error_status): break
            if len(var_binds_table) < 3: continue

            oid_parts = var_binds_table[0][0].prettyPrint().split('.')
            base_len_rem_sys_name = len(OID_LLDP_REM_SYS_NAME.split('.'))
            if len(oid_parts) < base_len_rem_sys_name + 2: continue

            timeMark = int(oid_parts[base_len_rem_sys_name])
            localPortNum = int(oid_parts[base_len_rem_sys_name + 1])
            key = (timeMark, localPortNum)

            if key not in neighs: neighs[key] = {}
            neighs[key]['sysname'] = var_binds_table[0][1].prettyPrint()
            neighs[key]['port_id'] = var_binds_table[1][1].prettyPrint()
            neighs[key]['port_descr'] = var_binds_table[2][1].prettyPrint()
        else:
            # Pobieranie LOC MIB (tylko jeśli pętla REM zakończyła się normalnie)
            loc_port_map = {}
            for error_indication, error_status, _, var_binds_table in nextCmd(
                SnmpEngine(), CommunityData(community), UdpTransportTarget((host, 161), timeout=2, retries=1), ContextData(),
                ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID_SUBTYPE)),
                ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID)),
                lexicographicMode=False):

                if _handle_snmp_error(host, "LLDP LOC", error_indication, error_status): break
                if len(var_binds_table) < 2: continue

                oid_parts = var_binds_table[0][0].prettyPrint().split('.')
                base_len_loc_port = len(OID_LLDP_LOC_PORT_ID_SUBTYPE.split('.'))
                if len(oid_parts) < base_len_loc_port + 1: continue

                localPortNum = int(oid_parts[base_len_loc_port])
                subtype = int(var_binds_table[0][1])
                value = var_binds_table[1][1].prettyPrint()

                if subtype == 5: # Preferujemy ifIndex (subtype 5)
                    try:
                        loc_port_map[localPortNum] = int(value)
                    except ValueError:
                        print(f"  ⚠ SNMP LLDP LOC: Nie można sparsować ifIndex '{value}' dla localPortNum {localPortNum} na {host}")
            else:
                # Łączenie danych REM i LOC
                for key, data in neighs.items():
                    timeMark, localPortNum = key
                    ifidx = loc_port_map.get(localPortNum)
                    if ifidx:
                        remote_if = data.get('port_id', '').strip()
                        remote_if_desc = data.get('port_descr', '').strip()
                        # Użyj opisu jako remote_if jeśli port_id jest puste, nieczytelne (np. MAC) lub opis jest bardziej konkretny
                        if not remote_if or ':' in remote_if or 'mac' in remote_if.lower() or len(remote_if) > 30:
                             if remote_if_desc:
                                remote_if = remote_if_desc

                        final_neighs.append((ifidx, data.get('sysname', 'UnknownSystem').strip(), remote_if))

    except Exception as e:
        print(f"⚠ SNMP LLDP: Ogólny błąd dla {host}: {e}")
        return None

    return final_neighs


def snmp_get_cdp_neighbors(host, community, timeout=5, retries=1):
    """Pobiera sąsiadów CDP przez SNMP."""
    OID_CDP_IFINDEX = '1.3.6.1.4.1.9.9.23.1.1.1.1.6'
    OID_CDP_DEV_ID = '1.3.6.1.4.1.9.9.23.1.2.1.1.6'
    OID_CDP_PORT_ID = '1.3.6.1.4.1.9.9.23.1.2.1.1.7'

    neighs = {}; final_neighs = []
    try:
        for error_indication, error_status, _, var_binds_table in nextCmd(
            SnmpEngine(), CommunityData(community), UdpTransportTarget((host,161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_CDP_IFINDEX)),
            ObjectType(ObjectIdentity(OID_CDP_DEV_ID)),
            ObjectType(ObjectIdentity(OID_CDP_PORT_ID)),
            lexicographicMode=False):

            if _handle_snmp_error(host, "CDP", error_indication, error_status): break
            if len(var_binds_table) < 3: continue

            oid_key_str = '.'.join(var_binds_table[0][0].prettyPrint().split('.')[:-1])
            if oid_key_str not in neighs: neighs[oid_key_str] = {}

            for oid, value in var_binds_table:
                 oid_name = oid.prettyPrint()
                 val_str = value.prettyPrint()
                 if oid_name.startswith(OID_CDP_IFINDEX):
                    try: neighs[oid_key_str]['ifindex'] = int(val_str)
                    except ValueError: print(f"  ⚠ SNMP CDP: Nie można sparsować ifIndex '{val_str}' dla {oid_key_str} na {host}")
                 elif oid_name.startswith(OID_CDP_DEV_ID): neighs[oid_key_str]['dev_id'] = val_str
                 elif oid_name.startswith(OID_CDP_PORT_ID): neighs[oid_key_str]['port_id'] = val_str
        else:
            for data in neighs.values():
                if 'ifindex' in data and 'dev_id' in data and 'port_id' in data:
                    final_neighs.append((data['ifindex'], data['dev_id'].strip(), data['port_id'].strip()))

    except Exception as e:
        print(f"⚠ SNMP CDP: Ogólny błąd dla {host}: {e}")
        return None
    return final_neighs


def snmp_get_bridge_baseport_ifindex(host, community, timeout=2, retries=1):
    """Pobiera mapowanie Base Port ID na IfIndex (potrzebne dla FDB)."""
    base2if = {}
    try:
        for error_indication, error_status, _, var_binds in nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=0), UdpTransportTarget((host,161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.17.1.4.1.2')),
            lexicographicMode=False):

            if _handle_snmp_error(host, "BasePortIfIndex", error_indication, error_status): break
            if not var_binds: continue
            oid, value = var_binds[0]
            parts = oid.prettyPrint().split('.')
            if len(parts) < 1: continue
            base_port = int(parts[-1])
            base2if[base_port] = int(value)
    except Exception as e:
        print(f"⚠ SNMP BasePortIfIndex: Błąd dla {host}: {e}")
        return None
    return base2if

def snmp_get_fdb_entries(host, community, timeout=5, retries=1):
    """Pobiera wpisy FDB z Bridge-MIB (MAC, Base Port)."""
    entries = []
    try:
        for error_indication, error_status, _, var_binds_table in nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=0), UdpTransportTarget((host,161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.17.4.3.1.1')),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.17.4.3.1.2')),
            lexicographicMode=False):

            if _handle_snmp_error(host, "FDB", error_indication, error_status): break
            if len(var_binds_table) < 2: continue

            mac_bytes = var_binds_table[0][1].asOctets()
            mac = ''.join(f"{b:02x}" for b in mac_bytes)
            if len(mac) != 12: continue
            base_port = int(var_binds_table[1][1])
            entries.append((mac, base_port))
    except Exception as e:
        print(f"⚠ SNMP FDB: Błąd dla {host}: {e}")
        return None
    return entries

def snmp_get_qbridge_fdb(host, community, timeout=5, retries=1):
    """Pobiera wpisy FDB z Q-Bridge-MIB (MAC, VLAN, Base Port)."""
    entries = []
    OID_QBRIDGE_MAC = '1.3.6.1.2.1.17.7.1.2.2.1.1'
    OID_QBRIDGE_PORT = '1.3.6.1.2.1.17.7.1.2.2.1.2'
    base_len_mac = len(OID_QBRIDGE_MAC.split('.'))
    try:
        for error_indication, error_status, _, var_binds_table in nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=0), UdpTransportTarget((host,161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_QBRIDGE_MAC)),
            ObjectType(ObjectIdentity(OID_QBRIDGE_PORT)),
            lexicographicMode=False):

            if _handle_snmp_error(host, "Q-BRIDGE FDB", error_indication, error_status): break
            if len(var_binds_table) < 2: continue

            oid_full_mac = var_binds_table[0][0].prettyPrint()
            if not oid_full_mac.startswith(OID_QBRIDGE_MAC + '.'): continue

            parts = oid_full_mac.split('.')
            if len(parts) < base_len_mac + 1 + 6: continue
            vlan = int(parts[base_len_mac])
            mac_bytes = var_binds_table[0][1].asOctets()
            mac = ''.join(f"{b:02x}" for b in mac_bytes)
            if len(mac) != 12: continue

            bridge_port = int(var_binds_table[1][1])
            entries.append((mac, vlan, bridge_port))
    except Exception as e:
        print(f"⚠ SNMP Q-BRIDGE FDB: Błąd dla {host}: {e}")
        return None
    return entries

def snmp_get_arp_entries(host, community, timeout=5, retries=1):
    """Pobiera wpisy ARP (IP, MAC, IfIndex)."""
    entries = []
    OID_ARP_MAC = '1.3.6.1.2.1.4.22.1.2'
    OID_ARP_IP = '1.3.6.1.2.1.4.22.1.3'
    base_len_mac = len(OID_ARP_MAC.split('.'))
    try:
        for error_indication, error_status, _, var_binds_table in nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=0), UdpTransportTarget((host,161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_ARP_MAC)),
            ObjectType(ObjectIdentity(OID_ARP_IP)),
            lexicographicMode=False):

            if _handle_snmp_error(host, "ARP", error_indication, error_status): break
            if len(var_binds_table) < 2: continue

            oid_full_mac = var_binds_table[0][0].prettyPrint()
            if not oid_full_mac.startswith(OID_ARP_MAC + '.'): continue

            parts = oid_full_mac.split('.')
            if len(parts) < base_len_mac + 1 + 4: continue
            ifidx = int(parts[base_len_mac])
            mac_bytes = var_binds_table[0][1].asOctets()
            mac = ''.join(f"{b:02x}" for b in mac_bytes)
            if len(mac) != 12: continue

            ipaddr = var_binds_table[1][1].prettyPrint()
            entries.append((ipaddr, mac, ifidx))
    except Exception as e:
        print(f"⚠ SNMP ARP: Błąd dla {host}: {e}")
        return None
    return entries