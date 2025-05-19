# snmp_utils.py
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, nextCmd, OctetString
)
from pysnmp.smi import exval
from pysnmp.error import PySnmpError
import logging
from typing import List, Tuple, Dict, Optional, Any

logger = logging.getLogger(__name__)


def _handle_snmp_response_tuple(
        host: str,
        operation_name: str,
        error_indication: Any,
        error_status: Any,
        error_index: Any,
        var_binds: Optional[List[Tuple[ObjectIdentity, Any]]]
) -> bool:
    """
    Interpretuje error_indication i error_status z krotki zwróconej przez pysnmp.
    Zwraca True, jeśli wystąpił błąd krytyczny, który powinien przerwać operację.
    """
    if error_indication:
        if isinstance(error_indication, PySnmpError):
            logger.warning(f"SNMP {operation_name}: Błąd (PySnmpError) dla {host}: {error_indication}")
        else:
            logger.warning(f"SNMP {operation_name}: Błąd indykacji dla {host}: {error_indication}")
        return True
    elif error_status:
        error_message = error_status.prettyPrint()
        if int(error_status) == 0:  # noError
            return False

        is_critical_status = True
        if var_binds:
            all_nosuch_or_end = True
            for oid, val in var_binds:
                if not (exval.noSuchObject.isSameTypeWith(val) or \
                        exval.noSuchInstance.isSameTypeWith(val) or \
                        exval.endOfMibView.isSameTypeWith(val)):
                    all_nosuch_or_end = False
                    break
            if all_nosuch_or_end:
                logger.debug(
                    f"SNMP {operation_name}: Osiągnięto koniec MIB lub OID nie istnieje dla {host} (Status: {error_message}, Index: {error_index.prettyPrint() if error_index else 'N/A'})")
                is_critical_status = False

        if is_critical_status:
            logger.warning(
                f"SNMP {operation_name}: Błąd statusu dla {host}: {error_message} at {error_index.prettyPrint() if error_index else 'N/A'}"
                f"{(' (pierwszy OID: ' + var_binds[0][0].prettyPrint() + ')') if var_binds and len(var_binds) > 0 and var_binds[0] else ''}")
            return True
    return False


def snmp_get_lldp_neighbors(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[int, str, str]]]:
    OID_LLDP_REM_SYS_NAME = '1.0.8802.1.1.2.1.4.1.1.9';
    OID_LLDP_REM_PORT_ID = '1.0.8802.1.1.2.1.4.1.1.7'
    OID_LLDP_REM_PORT_DESCR = '1.0.8802.1.1.2.1.4.1.1.8';
    OID_LLDP_LOC_PORT_ID_SUBTYPE = '1.0.8802.1.1.2.1.3.7.1.2'
    OID_LLDP_LOC_PORT_ID = '1.0.8802.1.1.2.1.3.7.1.3'
    neighs_data: Dict[Tuple[int, int], Dict[str, str]] = {};
    final_results: List[Tuple[int, str, str]] = []
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP LLDP: Pobieranie danych REM dla {host}...")
        for error_indication, error_status, error_index, var_binds_table_row in nextCmd(
                snmp_engine, CommunityData(community, mpModel=1),
                UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
                ObjectType(ObjectIdentity(OID_LLDP_REM_SYS_NAME)), ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_ID)),
                ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_DESCR)), lexicographicMode=False,
                ignoreNonIncreasingOid=True
        ):
            if error_indication: logger.warning(
                f"SNMP LLDP REM: Błąd indykacji dla {host}: {error_indication}"); return None
            if _handle_snmp_response_tuple(host, "LLDP REM", error_indication, error_status, error_index,
                                           var_binds_table_row): break
            if not var_binds_table_row or len(var_binds_table_row) < 3: continue
            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) for val in var_binds_table_row) or \
                     all(exval.noSuchObject.isSameTypeWith(val[1]) for val in var_binds_table_row) or \
                     all(exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds_table_row)
            if is_end: break
            oid_parts = str(var_binds_table_row[0][0]).split('.');
            base_oid_len = len(OID_LLDP_REM_SYS_NAME.split('.'))
            if len(oid_parts) <= base_oid_len + 1: continue
            try:
                time_mark, local_port_num = int(oid_parts[base_oid_len]), int(oid_parts[base_oid_len + 1])
            except ValueError:
                continue
            key = (time_mark, local_port_num)
            if key not in neighs_data: neighs_data[key] = {}
            neighs_data[key]['sysname'] = str(var_binds_table_row[0][1]);
            neighs_data[key]['port_id'] = str(var_binds_table_row[1][1]);
            neighs_data[key]['port_descr'] = str(var_binds_table_row[2][1])

        if not neighs_data: logger.info(f"SNMP LLDP: Nie znaleziono danych REM sąsiadów dla {host}."); return []
        logger.debug(f"SNMP LLDP: Pobieranie danych LOC dla {host}...")
        loc_port_to_ifindex_map: Dict[int, int] = {}
        for error_indication_loc, error_status_loc, error_index_loc, var_binds_loc in nextCmd(
                snmp_engine, CommunityData(community, mpModel=1), UdpTransportTarget((host, 161), timeout=2, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID_SUBTYPE)),
                ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID)),
                lexicographicMode=False, ignoreNonIncreasingOid=True
        ):
            if error_indication_loc: logger.warning(
                f"SNMP LLDP LOC: Błąd indykacji dla {host}: {error_indication_loc}"); break
            if _handle_snmp_response_tuple(host, "LLDP LOC", error_indication_loc, error_status_loc, error_index_loc,
                                           var_binds_loc): break
            if not var_binds_loc or len(var_binds_loc) < 2: continue
            is_end_loc = all(exval.endOfMibView.isSameTypeWith(val[1]) for val in var_binds_loc) or \
                         all(exval.noSuchObject.isSameTypeWith(val[1]) for val in var_binds_loc) or \
                         all(exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds_loc)
            if is_end_loc: break
            oid_parts_loc = str(var_binds_loc[0][0]).split('.');
            base_oid_len_loc = len(OID_LLDP_LOC_PORT_ID_SUBTYPE.split('.'))
            if len(oid_parts_loc) <= base_oid_len_loc: continue
            try:
                local_port_num_loc, subtype, value_str = int(oid_parts_loc[base_oid_len_loc]), int(
                    var_binds_loc[0][1]), str(var_binds_loc[1][1])
            except ValueError:
                continue
            if subtype == 5:
                try:
                    loc_port_to_ifindex_map[local_port_num_loc] = int(value_str)
                except ValueError:
                    logger.warning(
                        f"SNMP LLDP LOC: Nie można sparsować ifIndex '{value_str}' dla {local_port_num_loc} na {host}")

        if not loc_port_to_ifindex_map: logger.info(
            f"SNMP LLDP: Nie udało się zbudować mapy localPortNum -> ifIndex dla {host}.")
        for key, data in neighs_data.items():
            _time_mark, local_port_num_key = key;
            ifidx = loc_port_to_ifindex_map.get(local_port_num_key, 0)
            remote_sys_name, remote_port_id_raw, remote_port_descr = data.get('sysname', '').strip(), data.get(
                'port_id', '').strip(), data.get('port_descr', '').strip()
            if not remote_sys_name: remote_sys_name = "UnknownSystem"
            chosen_remote_port = remote_port_id_raw
            if remote_port_descr and remote_port_descr != "not advertised":
                if (':' in remote_port_id_raw or len(remote_port_id_raw) > 20) and len(remote_port_descr) < len(
                    remote_port_id_raw):
                    chosen_remote_port = remote_port_descr
                elif not remote_port_id_raw or "not advertised" in remote_port_id_raw.lower():
                    chosen_remote_port = remote_port_descr
            if not chosen_remote_port or "not advertised" in chosen_remote_port.lower(): continue
            final_results.append((ifidx, remote_sys_name, chosen_remote_port))
    except PySnmpError as e_pysnmp_outer:
        logger.error(f"SNMP LLDP: Krytyczny błąd PySnmpError (poza pętlą) dla {host}: {e_pysnmp_outer}", exc_info=False)
        return None
    except Exception as e_outer:
        logger.error(f"SNMP LLDP: Ogólny, nieoczekiwany błąd (poza pętlą) dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP LLDP: Zakończono dla {host}, znaleziono {len(final_results)} sąsiadów.")
    return final_results


def snmp_get_cdp_neighbors(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[int, str, str]]]:
    OID_CDP_IFINDEX = '1.3.6.1.4.1.9.9.23.1.1.1.1.6';
    OID_CDP_DEV_ID = '1.3.6.1.4.1.9.9.23.1.2.1.1.6';
    OID_CDP_PORT_ID = '1.3.6.1.4.1.9.9.23.1.2.1.1.7'
    neighs_map: Dict[str, Dict[str, Any]] = {};
    final_results: List[Tuple[int, str, str]] = []
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP CDP: Pobieranie danych dla {host}...")
        for error_indication, error_status, error_index, var_binds in nextCmd(
                snmp_engine, CommunityData(community, mpModel=1),
                UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
                ObjectType(ObjectIdentity(OID_CDP_IFINDEX)), ObjectType(ObjectIdentity(OID_CDP_DEV_ID)),
                ObjectType(ObjectIdentity(OID_CDP_PORT_ID)), lexicographicMode=False, ignoreNonIncreasingOid=True
        ):
            if error_indication: logger.warning(f"SNMP CDP: Błąd indykacji dla {host}: {error_indication}"); return None
            if _handle_snmp_response_tuple(host, "CDP", error_indication, error_status, error_index, var_binds): break
            if not var_binds or len(var_binds) < 3: continue
            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchObject.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break
            oid_suffix_parts = str(var_binds[0][0]).split(OID_CDP_IFINDEX)[1].strip('.').split('.')
            if not oid_suffix_parts: continue
            oid_key = ".".join(oid_suffix_parts)
            if oid_key not in neighs_map: neighs_map[oid_key] = {}
            try:
                neighs_map[oid_key]['ifindex'] = int(var_binds[0][1])
            except ValueError:
                continue
            neighs_map[oid_key]['dev_id'] = str(var_binds[1][1]).strip()
            neighs_map[oid_key]['port_id'] = str(var_binds[2][1]).strip()
        for data in neighs_map.values():
            if 'ifindex' in data and data.get('dev_id') and data.get('port_id'):
                final_results.append((data['ifindex'], data['dev_id'], data['port_id']))
    except PySnmpError as e_pysnmp_outer:
        logger.error(f"SNMP CDP: Krytyczny błąd PySnmpError dla {host}: {e_pysnmp_outer}", exc_info=False)
        return None
    except Exception as e_outer:
        logger.error(f"SNMP CDP: Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP CDP: Zakończono dla {host}, znaleziono {len(final_results)} sąsiadów.")
    return final_results


def snmp_get_bridge_baseport_ifindex(host: str, community: str, timeout: int = 2, retries: int = 1) -> Optional[
    Dict[int, int]]:
    OID_BASE_PORT_IFINDEX = '1.3.6.1.2.1.17.1.4.1.2'
    base_to_ifindex_map: Dict[int, int] = {}
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP BasePortIfIndex: Pobieranie danych dla {host}...")
        for error_indication, error_status, error_index, var_binds in nextCmd(
                snmp_engine, CommunityData(community, mpModel=1),
                UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
                ObjectType(ObjectIdentity(OID_BASE_PORT_IFINDEX)), lexicographicMode=False, ignoreNonIncreasingOid=True
        ):
            if error_indication: logger.warning(
                f"SNMP BasePortIfIndex: Błąd indykacji dla {host}: {error_indication}"); return None
            if _handle_snmp_response_tuple(host, "BasePortIfIndex", error_indication, error_status, error_index,
                                           var_binds): break
            if not var_binds: continue
            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchObject.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break
            oid, value = var_binds[0]
            oid_str = str(oid)
            if not oid_str.startswith(OID_BASE_PORT_IFINDEX + '.'): break
            try:
                base_port_id, ifindex = int(oid_str.split('.')[-1]), int(value); base_to_ifindex_map[
                    base_port_id] = ifindex
            except ValueError:
                continue
    except PySnmpError as e_pysnmp_outer:
        logger.error(f"SNMP BasePortIfIndex: Krytyczny błąd PySnmpError dla {host}: {e_pysnmp_outer}", exc_info=False)
        return None
    except Exception as e_outer:
        logger.error(f"SNMP BasePortIfIndex: Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP BasePortIfIndex: Zakończono dla {host}, zmapowano {len(base_to_ifindex_map)} portów.")
    return base_to_ifindex_map


def snmp_get_fdb_entries(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, int]]]:
    OID_FDB_ADDRESS = '1.3.6.1.2.1.17.4.3.1.1';
    OID_FDB_PORT = '1.3.6.1.2.1.17.4.3.1.2'
    entries: List[Tuple[str, int]] = []
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP FDB (Bridge-MIB): Pobieranie danych dla {host}...")
        for error_indication, error_status, error_index, var_binds in nextCmd(
                snmp_engine, CommunityData(community, mpModel=1),
                UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
                ObjectType(ObjectIdentity(OID_FDB_ADDRESS)), ObjectType(ObjectIdentity(OID_FDB_PORT)),
                lexicographicMode=False, ignoreNonIncreasingOid=True
        ):
            if error_indication: logger.warning(f"SNMP FDB: Błąd indykacji dla {host}: {error_indication}"); return None
            if _handle_snmp_response_tuple(host, "FDB (Bridge-MIB)", error_indication, error_status, error_index,
                                           var_binds): break
            if not var_binds or len(var_binds) < 2: continue
            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchObject.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break
            oid_addr_str, oid_port_str = str(var_binds[0][0]), str(var_binds[1][0])
            if not oid_addr_str.startswith(OID_FDB_ADDRESS + '.') or not oid_port_str.startswith(
                OID_FDB_PORT + '.'): break
            addr_suffix, port_suffix = oid_addr_str.split(OID_FDB_ADDRESS)[1], oid_port_str.split(OID_FDB_PORT)[1]
            if addr_suffix != port_suffix: continue
            mac_value = var_binds[0][1]
            if not isinstance(mac_value, OctetString): continue
            mac_str = ''.join(f"{b:02x}" for b in mac_value.asOctets())
            if len(mac_str) != 12: continue
            try:
                base_port_id = int(var_binds[1][1])
            except ValueError:
                continue
            entries.append((mac_str, base_port_id))
    except PySnmpError as e_pysnmp_outer:
        logger.error(f"SNMP FDB: Krytyczny błąd PySnmpError dla {host}: {e_pysnmp_outer}", exc_info=False)
        return None
    except Exception as e_outer:
        logger.error(f"SNMP FDB (Bridge-MIB): Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP FDB (Bridge-MIB): Zakończono dla {host}, znaleziono {len(entries)} wpisów.")
    return entries


def snmp_get_qbridge_fdb(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, int, int]]]:
    OID_QBRIDGE_ADDRESS = '1.3.6.1.2.1.17.7.1.2.2.1.1';
    OID_QBRIDGE_PORT = '1.3.6.1.2.1.17.7.1.2.2.1.2'
    entries: List[Tuple[str, int, int]] = []
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP FDB (Q-Bridge-MIB): Pobieranie danych dla {host}...")
        for error_indication, error_status, error_index, var_binds in nextCmd(
                snmp_engine, CommunityData(community, mpModel=1),
                UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
                ObjectType(ObjectIdentity(OID_QBRIDGE_ADDRESS)), ObjectType(ObjectIdentity(OID_QBRIDGE_PORT)),
                lexicographicMode=False, ignoreNonIncreasingOid=True
        ):
            if error_indication: logger.warning(
                f"SNMP Q-FDB: Błąd indykacji dla {host}: {error_indication}"); return None
            if _handle_snmp_response_tuple(host, "FDB (Q-Bridge-MIB)", error_indication, error_status, error_index,
                                           var_binds): break
            if not var_binds or len(var_binds) < 2: continue
            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchObject.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break
            oid_addr_str, oid_port_str = str(var_binds[0][0]), str(var_binds[1][0])
            if not oid_addr_str.startswith(OID_QBRIDGE_ADDRESS + '.') or not oid_port_str.startswith(
                OID_QBRIDGE_PORT + '.'): break
            addr_suffix_parts = oid_addr_str.split(OID_QBRIDGE_ADDRESS + '.')[1].split('.')
            port_suffix_parts = oid_port_str.split(OID_QBRIDGE_PORT + '.')[1].split('.')
            if addr_suffix_parts != port_suffix_parts or len(addr_suffix_parts) < 1 + 6: continue
            try:
                vlan_id = int(addr_suffix_parts[0])
            except ValueError:
                continue
            mac_value = var_binds[0][1]
            if not isinstance(mac_value, OctetString): continue
            mac_str = ''.join(f"{b:02x}" for b in mac_value.asOctets())
            if len(mac_str) != 12: continue
            try:
                base_port_id = int(var_binds[1][1])
            except ValueError:
                continue
            entries.append((mac_str, vlan_id, base_port_id))
    except PySnmpError as e_pysnmp_outer:
        logger.error(f"SNMP Q-FDB: Krytyczny błąd PySnmpError dla {host}: {e_pysnmp_outer}", exc_info=False)
        return None
    except Exception as e_outer:
        logger.error(f"SNMP FDB (Q-Bridge-MIB): Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP FDB (Q-Bridge-MIB): Zakończono dla {host}, znaleziono {len(entries)} wpisów.")
    return entries


def snmp_get_arp_entries(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, str, int]]]:
    OID_ARP_IFINDEX = '1.3.6.1.2.1.4.22.1.1';
    OID_ARP_MAC = '1.3.6.1.2.1.4.22.1.2';
    OID_ARP_IP = '1.3.6.1.2.1.4.22.1.3'
    entries: List[Tuple[str, str, int]] = []
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP ARP: Pobieranie danych dla {host}...")
        for error_indication, error_status, error_index, var_binds in nextCmd(
                snmp_engine, CommunityData(community, mpModel=1),
                UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
                ObjectType(ObjectIdentity(OID_ARP_IFINDEX)), ObjectType(ObjectIdentity(OID_ARP_MAC)),
                ObjectType(ObjectIdentity(OID_ARP_IP)), lexicographicMode=False, ignoreNonIncreasingOid=True
        ):
            if error_indication: logger.warning(f"SNMP ARP: Błąd indykacji dla {host}: {error_indication}"); return None
            if _handle_snmp_response_tuple(host, "ARP", error_indication, error_status, error_index, var_binds): break
            if not var_binds or len(var_binds) < 3: continue
            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchObject.isSameTypeWith(val[1]) for val in var_binds) or \
                     all(exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break
            oid_ifindex_str, oid_mac_str, oid_ip_str = str(var_binds[0][0]), str(var_binds[1][0]), str(var_binds[2][0])
            if not oid_ifindex_str.startswith(OID_ARP_IFINDEX + '.') or \
                    not oid_mac_str.startswith(OID_ARP_MAC + '.') or \
                    not oid_ip_str.startswith(OID_ARP_IP + '.'): break
            ifindex_suffix, mac_suffix, ip_suffix = oid_ifindex_str.split(OID_ARP_IFINDEX)[1], \
            oid_mac_str.split(OID_ARP_MAC)[1], oid_ip_str.split(OID_ARP_IP)[1]
            if not (ifindex_suffix == mac_suffix == ip_suffix): continue
            try:
                if_index = int(var_binds[0][1])
            except ValueError:
                continue
            mac_value = var_binds[1][1]
            if not isinstance(mac_value, OctetString): continue
            mac_str = ''.join(f"{b:02x}" for b in mac_value.asOctets())
            if len(mac_str) != 12: continue
            ip_address = str(var_binds[2][1])
            entries.append((ip_address, mac_str, if_index))
    except PySnmpError as e_pysnmp_outer:
        logger.error(f"SNMP ARP: Krytyczny błąd PySnmpError dla {host}: {e_pysnmp_outer}", exc_info=False)
        return None
    except Exception as e_outer:
        logger.error(f"SNMP ARP: Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP ARP: Zakończono dla {host}, znaleziono {len(entries)} wpisów.")
    return entries