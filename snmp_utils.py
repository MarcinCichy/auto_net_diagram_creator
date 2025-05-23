# snmp_utils.py
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, nextCmd, OctetString
)
from pysnmp.smi import exval, rfc1902
from pysnmp.proto.rfc1905 import NoSuchObject, NoSuchInstance, EndOfMibView
from pysnmp.error import PySnmpError  # <<< DODANO BRAKUJĄCY IMPORT

import logging
from typing import List, Tuple, Dict, Optional, Any, Callable
import collections.abc

logger = logging.getLogger(__name__)


def _handle_snmp_response_tuple(
        host: str,
        operation_name: str,
        error_indication: Any,
        error_status: Any,
        error_index: Any,
        var_binds: Optional[List[Any]]
) -> bool:
    if error_indication:
        logger.warning(f"SNMP {operation_name}: Błąd indykacji dla {host}: {error_indication}")
        return True
    elif error_status:
        try:
            error_status_val = int(error_status)
            error_message = error_status.prettyPrint()
        except (ValueError, TypeError, AttributeError):
            logger.warning(f"SNMP {operation_name}: Nieoczekiwany format error_status dla {host}: {error_status}")
            return True
        if error_status_val == 0: return False

        is_critical_status = True
        if var_binds and isinstance(var_binds, collections.abc.Iterable):
            all_nosuch_or_end = True
            for oid_val_pair in var_binds:
                val_to_check = None
                if isinstance(oid_val_pair, rfc1902.ObjectType):
                    val_to_check = oid_val_pair[1]
                elif isinstance(oid_val_pair, tuple) and len(oid_val_pair) == 2:
                    val_to_check = oid_val_pair[1]

                if val_to_check is None or not (isinstance(val_to_check, (NoSuchObject, NoSuchInstance, EndOfMibView))):
                    all_nosuch_or_end = False
                    break
            if all_nosuch_or_end:
                logger.debug(
                    f"SNMP {operation_name}: Koniec MIB lub OID nie istnieje dla {host} (Status: {error_message}, Index: {error_index.prettyPrint() if hasattr(error_index, 'prettyPrint') else error_index})")
                is_critical_status = False
        elif var_binds is None:
            pass

        if is_critical_status:
            first_oid_str = ""
            if var_binds and isinstance(var_binds, collections.abc.Iterable) and len(var_binds) > 0:
                first_item = var_binds[0]
                oid_to_print = None
                if isinstance(first_item, rfc1902.ObjectType):
                    oid_to_print = first_item[0]
                elif isinstance(first_item, tuple) and len(first_item) > 0:
                    oid_to_print = first_item[0]
                if oid_to_print and hasattr(oid_to_print,
                                            'prettyPrint'): first_oid_str = f" (pierwszy OID: {oid_to_print.prettyPrint()})"
            logger.warning(
                f"SNMP {operation_name}: Błąd statusu dla {host}: {error_message} at {error_index.prettyPrint() if hasattr(error_index, 'prettyPrint') else error_index}{first_oid_str}")
            return True
    return False


def _execute_snmp_next_cmd(snmp_engine, auth_data, transport_target, context_data, *var_types_and_oids):
    results = []
    item_index = 0
    cmd_gen_or_tuple = nextCmd(snmp_engine, auth_data, transport_target, context_data, *var_types_and_oids,
                               lexicographicMode=False, ignoreNonIncreasingOid=True)

    if isinstance(cmd_gen_or_tuple, collections.abc.Generator) or hasattr(cmd_gen_or_tuple, '__next__'):
        logger.debug(
            f"SNMP _exec: nextCmd zwróciło iterator/generator dla {transport_target.transportAddr[0]}. Iteruję.")
        cmd_gen = cmd_gen_or_tuple
        while True:
            item_index += 1;
            current_response_tuple = (PySnmpError(f"Przerwano przed odpowiedzią w iteracji {item_index}"), None, None,
                                      None)
            try:
                response_item_raw = next(cmd_gen)
                logger.debug(
                    f"SNMP _exec (item {item_index} for {transport_target.transportAddr[0]}): Raw type={type(response_item_raw)}, value='{str(response_item_raw)[:200]}'")
                if isinstance(response_item_raw, PySnmpError):
                    current_response_tuple = (response_item_raw, None, None, None)
                elif isinstance(response_item_raw, tuple) and len(response_item_raw) == 4:
                    err_ind, err_stat, err_idx, v_binds = response_item_raw
                    if err_ind is not None and not isinstance(err_ind, (PySnmpError,
                                                                        type(None))):  # PySnmpError już tu jest dzięki importowi
                        logger.warning(
                            f"SNMP _exec: errorIndication w krotce to {type(err_ind)}: '{str(err_ind)}'. Opakowuję.");
                        current_response_tuple = (PySnmpError(f"Wrapped: {err_ind}"), None, None, None)
                    else:
                        current_response_tuple = (err_ind, err_stat, err_idx, v_binds)
                else:
                    logger.error(
                        f"SNMP _exec: Nieoczekiwany typ/format ({type(response_item_raw)}) z nextCmd: {str(response_item_raw)[:200]}");
                    current_response_tuple = (PySnmpError(f"Unexpected from nextCmd: {type(response_item_raw)}"), None,
                                              None, None)
            except StopIteration:
                logger.debug(
                    f"SNMP _exec: StopIteration po {item_index - 1} elementach dla {transport_target.transportAddr[0]}."); break
            except TypeError as e_type_iter:
                logger.error(
                    f"SNMP _exec: TypeError BEZPOŚREDNIO z `next(cmd_gen)` (item {item_index} dla {transport_target.transportAddr[0]}): {e_type_iter}",
                    exc_info=True); current_response_tuple = (PySnmpError(f"TypeError iter: {e_type_iter}"), None, None,
                                                              None); results.append(current_response_tuple); break
            except PySnmpError as e_pysnmp_iter:
                logger.warning(
                    f"SNMP _exec: PySnmpError BEZPOŚREDNIO z `next(cmd_gen)` (item {item_index} dla {transport_target.transportAddr[0]}): {e_pysnmp_iter}"); current_response_tuple = (
                    e_pysnmp_iter, None, None, None)
            except Exception as e_generic_iter:
                logger.error(
                    f"SNMP _exec: Nieoczekiwany błąd BEZPOŚREDNIO z `next(cmd_gen)` (item {item_index} dla {transport_target.transportAddr[0]}): {e_generic_iter}",
                    exc_info=True); current_response_tuple = (PySnmpError(f"Unexpected iter error: {e_generic_iter}"),
                                                              None, None, None); results.append(
                    current_response_tuple); break
            results.append(current_response_tuple)
            if _handle_snmp_response_tuple(transport_target.transportAddr[0], f"_exec iter {item_index}",
                                           *current_response_tuple): logger.warning(
                f"SNMP _exec: Przerywam pętlę dla {transport_target.transportAddr[0]} - błąd krytyczny po elemencie {item_index}."); break
    elif isinstance(cmd_gen_or_tuple, tuple) and len(cmd_gen_or_tuple) == 4:  # Jeśli nextCmd od razu zwróciło krotkę
        logger.warning(
            f"SNMP _execute_snmp_next_cmd: nextCmd dla {transport_target.transportAddr[0]} zwróciło bezpośrednio krotkę (nie generator). Traktuję jako pojedynczy wynik.")
        logger.debug(f"SNMP _execute_snmp_next_cmd: Zwrócona krotka: {cmd_gen_or_tuple}")
        err_ind, err_stat, err_idx, v_binds = cmd_gen_or_tuple
        if err_ind is not None and not isinstance(err_ind, (PySnmpError, type(None))):
            logger.warning(
                f"SNMP _exec: errorIndication w zwróconej krotce to {type(err_ind)}: '{str(err_ind)}'. Opakowuję.");
            results.append((PySnmpError(f"Wrapped from immediate tuple: {err_ind}"), None, None, None))
        else:
            results.append(cmd_gen_or_tuple)
    else:  # Jeśli nextCmd zwróciło coś zupełnie nieoczekiwanego
        logger.error(
            f"SNMP _exec: nextCmd dla {transport_target.transportAddr[0]} zwróciło nieoczekiwany typ: {type(cmd_gen_or_tuple)}. Wartość: {str(cmd_gen_or_tuple)[:200]}");
        results.append((PySnmpError(f"nextCmd returned unexpected: {type(cmd_gen_or_tuple)}"), None, None, None))
    return results


def _get_varbind_list_safely(var_binds_from_response: Any, operation_name: str, host: str) -> Optional[
    List[rfc1902.ObjectType]]:
    if var_binds_from_response is None:
        logger.debug(f"SNMP {operation_name}: Otrzymano var_binds=None dla {host}.")
        return None
    actual_var_binds_to_check = var_binds_from_response
    if isinstance(var_binds_from_response, list) and \
            len(var_binds_from_response) == 1 and \
            isinstance(var_binds_from_response[0], collections.abc.Iterable) and \
            not isinstance(var_binds_from_response[0], tuple) and \
            not isinstance(var_binds_from_response[0], rfc1902.ObjectType):
        logger.debug(f"SNMP {operation_name}: Wykryto dodatkowe zagnieżdżenie w var_binds dla {host}. Rozpakowuję.")
        actual_var_binds_to_check = var_binds_from_response[0]
    if not isinstance(actual_var_binds_to_check, collections.abc.Iterable):
        logger.warning(
            f"SNMP {operation_name}: var_binds po potencjalnym rozpakowaniu nie są iterowalne ({type(actual_var_binds_to_check)}) dla {host}. Pomijam.")
        return None
    validated_var_binds: List[rfc1902.ObjectType] = []
    for item in actual_var_binds_to_check:
        if isinstance(item, rfc1902.ObjectType):
            validated_var_binds.append(item)
        elif isinstance(item, tuple) and len(item) == 2:
            logger.debug(
                f"SNMP {operation_name}: Element w var_binds jest zwykłą krotką, nie ObjectType: {item}. Używam go.")
            validated_var_binds.append(item)  # type: ignore
        else:
            logger.warning(
                f"SNMP {operation_name}: Element w var_binds nie jest oczekiwanym ObjectType ani krotką(OID,Wartość): {item} (typ: {type(item)}) dla {host}. Pomijam ten element.")
            if isinstance(item, str): logger.error(
                f"SNMP {operation_name}: KRYTYCZNY PROBLEM - element varbindu to string: '{item}'.")
    return validated_var_binds if validated_var_binds else None


def adapt_snmp_function(
        host: str, community: str, timeout: int, retries: int,
        operation_name: str, oids_to_query: List[str],
        expected_oids_per_response: int,
        data_mapper_func: Callable[[List[rfc1902.ObjectType], str], Any]
) -> Optional[List[Any]]:
    snmp_engine = SnmpEngine()
    aggregated_results: List[Any] = []
    try:
        logger.debug(f"SNMP {operation_name}: Pobieranie danych dla {host}...")
        object_types = [ObjectType(ObjectIdentity(oid)) for oid in oids_to_query]
        responses = _execute_snmp_next_cmd(snmp_engine, CommunityData(community, mpModel=1),
                                           UdpTransportTarget((host, 161), timeout=timeout, retries=retries),
                                           ContextData(), *object_types)
        if not responses or (responses[0][0] is not None):
            logger.warning(
                f"SNMP {operation_name}: Nie udało się pobrać danych dla {host}: {responses[0][0] if responses and responses[0] else 'Brak odpowiedzi'}");
            return None
        for error_indication, error_status, error_index, var_binds_from_response in responses:
            if error_indication: continue
            var_binds_list = _get_varbind_list_safely(var_binds_from_response, operation_name, host)
            if not var_binds_list:
                if error_status is not None and int(
                    error_status) == 0 and var_binds_from_response is None: logger.debug(
                    f"SNMP {operation_name}: Bezpiecznie puste var_binds z noError dla {host}.");
                continue
            all_end_for_this_response = True
            for oid_val_tuple in var_binds_list:
                val_to_check = oid_val_tuple[1]
                if not isinstance(val_to_check, (NoSuchObject, NoSuchInstance,
                                                 EndOfMibView)): all_end_for_this_response = False; break
            if all_end_for_this_response: logger.debug(
                f"SNMP {operation_name}: Wykryto koniec MIB dla {host}. Przerywam."); break
            if expected_oids_per_response > 0 and len(var_binds_list) != expected_oids_per_response:
                logger.warning(
                    f"SNMP {operation_name}: Oczekiwano {expected_oids_per_response} par OID/Wartość dla {host}, otrzymano {len(var_binds_list)}. Pomijam: {var_binds_list}");
                continue
            parsed_data = data_mapper_func(var_binds_list, host)
            if parsed_data == "BREAK_OUTER_LOOP": logger.debug(
                f"SNMP {operation_name}: Parser zasygnalizował przerwanie pętli dla {host}."); break
            if parsed_data:
                if isinstance(parsed_data, list):
                    aggregated_results.extend(parsed_data)
                else:
                    aggregated_results.append(parsed_data)
    except Exception as e_outer:
        logger.error(f"SNMP {operation_name}: Ogólny błąd dla {host}: {e_outer}", exc_info=True); return None
    if not aggregated_results and responses and responses[0][0] is not None: logger.warning(
        f"SNMP {operation_name}: Nie udało się pobrać danych i nie znaleziono wpisów dla {host} ({responses[0][0]})."); return None
    logger.info(f"SNMP {operation_name}: Zakończono dla {host}, znaleziono {len(aggregated_results)} wpisów/elementów.")
    return aggregated_results


# --- LLDP ---
def snmp_get_lldp_neighbors(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[int, str, str]]]:
    OID_LLDP_REM_SYS_NAME = '1.0.8802.1.1.2.1.4.1.1.9';
    OID_LLDP_REM_PORT_ID = '1.0.8802.1.1.2.1.4.1.1.7';
    OID_LLDP_REM_PORT_DESCR = '1.0.8802.1.1.2.1.4.1.1.8'
    OID_LLDP_LOC_PORT_ID_SUBTYPE = '1.0.8802.1.1.2.1.3.7.1.2';
    OID_LLDP_LOC_PORT_ID = '1.0.8802.1.1.2.1.3.7.1.3'
    neighs_data: Dict[Tuple[int, int], Dict[str, str]] = {};
    final_results: List[Tuple[int, str, str]] = []
    snmp_engine = SnmpEngine();
    op_name_rem = "LLDP REM";
    op_name_loc = "LLDP LOC"
    try:
        logger.debug(f"SNMP {op_name_rem}: Pobieranie danych dla {host}...")
        responses_rem = _execute_snmp_next_cmd(snmp_engine, CommunityData(community, mpModel=1),
                                               UdpTransportTarget((host, 161), timeout=timeout, retries=retries),
                                               ContextData(), ObjectType(ObjectIdentity(OID_LLDP_REM_SYS_NAME)),
                                               ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_ID)),
                                               ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_DESCR)))
        if not responses_rem or (responses_rem[0][0] is not None): logger.warning(
            f"SNMP {op_name_rem}: Nie udało się pobrać danych dla {host}: {responses_rem[0][0] if responses_rem and responses_rem[0] else 'Brak odpowiedzi'}"); return None
        for error_indication, error_status, error_index, var_binds_from_response in responses_rem:
            if error_indication: continue
            var_binds_list = _get_varbind_list_safely(var_binds_from_response, op_name_rem, host)
            if not var_binds_list:
                if error_status is not None and int(
                    error_status) == 0 and var_binds_from_response is None: logger.debug(
                    f"SNMP {op_name_rem}: Bezpiecznie puste var_binds z noError."); break
                continue
            if all(
                isinstance(vb[1], (NoSuchObject, NoSuchInstance, EndOfMibView)) for vb in var_binds_list): logger.debug(
                f"SNMP {op_name_rem}: Koniec MIB."); break
            if not (len(var_binds_list) == 3): logger.warning(
                f"SNMP {op_name_rem}: Oczekiwano 3 par OID/Wartość, otrzymano {len(var_binds_list)}. Pomijam: {var_binds_list}"); continue
            try:
                oid_str = str(var_binds_list[0][0])
                if not oid_str.startswith(OID_LLDP_REM_SYS_NAME + '.'): logger.debug(
                    f"SNMP {op_name_rem}: OID {oid_str} nie pasuje. Przerywam."); break
                oid_parts = oid_str.split('.');
                base_oid_len = len(OID_LLDP_REM_SYS_NAME.split('.'))
                if len(oid_parts) < base_oid_len + 3: logger.warning(
                    f"SNMP {op_name_rem}: Niekompletny OID: {oid_str}."); continue
                time_mark, local_port_num = int(oid_parts[base_oid_len]), int(oid_parts[base_oid_len + 1])
            except (ValueError, IndexError) as e:
                logger.warning(
                    f"SNMP {op_name_rem}: Błąd parsowania indeksów z OID {str(var_binds_list[0][0])}: {e}"); continue
            key = (time_mark, local_port_num);
            if key not in neighs_data: neighs_data[key] = {}
            neighs_data[key]['sysname'], neighs_data[key]['port_id'], neighs_data[key]['port_descr'] = str(
                var_binds_list[0][1]), str(var_binds_list[1][1]), str(var_binds_list[2][1])
        if not neighs_data: logger.info(f"SNMP {op_name_rem}: Nie znaleziono danych REM sąsiadów dla {host}.")
        loc_port_to_ifindex_map: Dict[int, int] = {}
        logger.debug(f"SNMP {op_name_loc}: Pobieranie danych dla {host}...")
        responses_loc = _execute_snmp_next_cmd(snmp_engine, CommunityData(community, mpModel=1),
                                               UdpTransportTarget((host, 161), timeout=2, retries=1), ContextData(),
                                               ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID_SUBTYPE)),
                                               ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID)))
        if responses_loc and responses_loc[0][0] is not None: logger.warning(
            f"SNMP {op_name_loc}: Nie udało się pobrać danych: {responses_loc[0][0]}. Kontynuuję bez mapy.")
        for error_indication, error_status, error_index, var_binds_from_response_loc in responses_loc:
            if error_indication: continue
            var_binds_list_loc = _get_varbind_list_safely(var_binds_from_response_loc, op_name_loc, host)
            if not var_binds_list_loc:
                if error_status is not None and int(
                    error_status) == 0 and var_binds_from_response_loc is None: logger.debug(
                    f"SNMP {op_name_loc}: Bezpiecznie puste var_binds z noError."); break
                continue
            if all(isinstance(vb[1], (NoSuchObject, NoSuchInstance, EndOfMibView)) for vb in
                   var_binds_list_loc): logger.debug(f"SNMP {op_name_loc}: Koniec MIB."); break
            if not (len(var_binds_list_loc) == 2): logger.warning(
                f"SNMP {op_name_loc}: Oczekiwano 2 par OID/Wartość. Pomijam."); continue
            try:
                oid_loc_str = str(var_binds_list_loc[0][0])
                if not oid_loc_str.startswith(OID_LLDP_LOC_PORT_ID_SUBTYPE + '.'): logger.debug(
                    f"SNMP {op_name_loc}: OID {oid_loc_str} nie pasuje. Przerywam."); break
                oid_loc_parts = oid_loc_str.split('.');
                base_oid_loc_len = len(OID_LLDP_LOC_PORT_ID_SUBTYPE.split('.'))
                if len(oid_loc_parts) < base_oid_loc_len + 1: logger.warning(
                    f"SNMP {op_name_loc}: Niekompletny OID: {oid_loc_str}."); continue
                local_port_num_loc, port_id_subtype, port_id_value_str = int(oid_loc_parts[base_oid_loc_len]), int(
                    var_binds_list_loc[0][1]), str(var_binds_list_loc[1][1])
                if port_id_subtype == 5:
                    try:
                        loc_port_to_ifindex_map[local_port_num_loc] = int(port_id_value_str); logger.debug(
                            f"SNMP {op_name_loc}: Zmapowano LPN {local_port_num_loc} -> ifIndex {int(port_id_value_str)}.")
                    except ValueError:
                        logger.warning(f"SNMP {op_name_loc}: Nie można sparsować ifIndex '{port_id_value_str}'.")
            except (ValueError, IndexError) as e:
                logger.warning(f"SNMP {op_name_loc}: Błąd parsowania OID/wartości: {e}"); continue
        if not loc_port_to_ifindex_map and neighs_data: logger.info(
            f"SNMP LLDP: Nie udało się zbudować mapy localPortNum -> ifIndex dla {host}.")
        for key, data in neighs_data.items():
            _t, lpn_key = key;
            ifidx = loc_port_to_ifindex_map.get(lpn_key, 0)
            if ifidx == 0 and loc_port_to_ifindex_map: logger.debug(
                f"SNMP LLDP: Brak mapowania ifIndex dla LPN {lpn_key}. Używam ifIndex=0.")
            rem_sys, rem_port_raw, rem_descr = data.get('sysname', '').strip(), data.get('port_id',
                                                                                         '').strip(), data.get(
                'port_descr', '').strip()
            chosen_rem_port = rem_port_raw;
            if not rem_sys: rem_sys = "UnknownLLDP"
            if rem_descr and rem_descr.lower() != "not advertised":
                if (':' in rem_port_raw or len(rem_port_raw) > 30) and rem_descr and len(rem_descr) < len(
                    rem_port_raw) and not (':' in rem_descr):
                    chosen_rem_port = rem_descr
                elif (not rem_port_raw or rem_port_raw.lower() == "not advertised") and rem_descr:
                    chosen_rem_port = rem_descr
            if not chosen_rem_port or chosen_rem_port.lower() == "not advertised": logger.debug(
                f"SNMP LLDP: Pomijam sąsiada dla LPN {lpn_key} - brak Portu."); continue
            final_results.append((ifidx, rem_sys, chosen_rem_port))
    except Exception as e_outer:
        logger.error(f"SNMP LLDP: Ogólny błąd dla {host}: {e_outer}", exc_info=True); return None
    if not neighs_data and responses_rem and responses_rem[0][0] is not None: logger.warning(
        f"SNMP LLDP: Operacja REM nie powiodła się: {responses_rem[0][0]}. Zwracam None."); return None
    logger.info(f"SNMP LLDP: Zakończono dla {host}, znaleziono {len(final_results)} sąsiadów.")
    return final_results


# --- CDP ---
def _parse_cdp_data_mapper(var_binds_list: List[rfc1902.ObjectType], host: str) -> Optional[Tuple[int, str, str]]:
    try:
        ifindex = int(var_binds_list[0][1])  # Dostęp do wartości przez indeks [1]
    except (ValueError, TypeError, AttributeError):
        logger.warning(
            f"SNMP CDP DataMap: Nie można sparsować ifIndex '{var_binds_list[0][1]}' dla {host}."); return None
    dev_id = str(var_binds_list[1][1]).strip()
    port_id = str(var_binds_list[2][1]).strip()
    if not (dev_id and port_id): logger.debug(f"SNMP CDP DataMap: Brak dev_id lub port_id dla {host}."); return None
    return (ifindex, dev_id, port_id)


def snmp_get_cdp_neighbors(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[int, str, str]]]:
    OIDS = ['1.3.6.1.4.1.9.9.23.1.1.1.1.1', '1.3.6.1.4.1.9.9.23.1.1.1.1.6', '1.3.6.1.4.1.9.9.23.1.1.1.1.7']
    return adapt_snmp_function(host, community, timeout, retries, "CDP", OIDS, 3, _parse_cdp_data_mapper)


# --- BridgeBasePortIfIndex ---
def snmp_get_bridge_baseport_ifindex(host: str, community: str, timeout: int = 2, retries: int = 1) -> Optional[
    Dict[int, int]]:
    OID_BASE_PORT_IFINDEX = '1.3.6.1.2.1.17.1.4.1.2'
    base_to_ifindex_map: Dict[int, int] = {};
    snmp_engine = SnmpEngine();
    operation_name = "BasePortIfIndex"
    try:
        logger.debug(f"SNMP {operation_name}: Pobieranie danych dla {host}...")
        responses = _execute_snmp_next_cmd(snmp_engine, CommunityData(community, mpModel=1),
                                           UdpTransportTarget((host, 161), timeout=timeout, retries=retries),
                                           ContextData(), ObjectType(ObjectIdentity(OID_BASE_PORT_IFINDEX)))
        if not responses or (responses[0][0] is not None): logger.warning(
            f"SNMP {operation_name}: Nie udało się pobrać danych dla {host}: {responses[0][0] if responses and responses[0] else 'Brak odpowiedzi'}"); return None
        for error_indication, error_status, error_index, var_binds_from_response in responses:
            if error_indication: continue
            var_binds_list = _get_varbind_list_safely(var_binds_from_response, operation_name, host)
            if not var_binds_list:
                if error_status is not None and int(
                    error_status) == 0 and var_binds_from_response is None: logger.debug(
                    f"SNMP {operation_name}: Bezpiecznie puste var_binds z noError."); break
                continue
            if all(
                isinstance(vb[1], (NoSuchObject, NoSuchInstance, EndOfMibView)) for vb in var_binds_list): logger.debug(
                f"SNMP {operation_name}: Koniec MIB."); break
            for oid_val_pair in var_binds_list:
                oid, value = oid_val_pair[0], oid_val_pair[1];
                oid_str = str(oid)
                if not oid_str.startswith(OID_BASE_PORT_IFINDEX + '.'): logger.debug(
                    f"SNMP {operation_name}: OID {oid_str} nie pasuje. Przerywam pętlę po var_binds_list."); break
                try:
                    base_to_ifindex_map[int(oid_str.split('.')[-1])] = int(value)
                except (ValueError, IndexError, TypeError) as e:
                    logger.warning(
                        f"SNMP {operation_name}: Błąd parsowania OID/wartości: {e} dla OID={oid_str}, Value={value}"); continue
    except Exception as e_outer:
        logger.error(f"SNMP {operation_name}: Ogólny błąd dla {host}: {e_outer}", exc_info=True); return None
    if not base_to_ifindex_map and responses and responses[0][0] is not None: logger.warning(
        f"SNMP {operation_name}: Nie udało się pobrać danych i nie zmapowano portów ({responses[0][0]})."); return None
    logger.info(f"SNMP {operation_name}: Zakończono dla {host}, zmapowano {len(base_to_ifindex_map)} portów.")
    return base_to_ifindex_map


# --- FDB (Bridge-MIB) ---
def _parse_fdb_data_mapper(var_binds_list: List[rfc1902.ObjectType], host: str) -> Optional[Tuple[str, int]]:
    oid_addr_obj, oid_port_obj = var_binds_list[0][0], var_binds_list[1][0]
    val_addr_obj, val_port_obj = var_binds_list[0][1], var_binds_list[1][1]
    OID_FDB_ADDRESS = '1.3.6.1.2.1.17.4.3.1.1';
    OID_FDB_PORT = '1.3.6.1.2.1.17.4.3.1.2'
    if not (str(oid_addr_obj).startswith(OID_FDB_ADDRESS + '.') and str(oid_port_obj).startswith(
        OID_FDB_PORT + '.')): return "BREAK_OUTER_LOOP"  # type: ignore
    try:
        addr_s, port_s = str(oid_addr_obj).split(OID_FDB_ADDRESS + '.')[1], str(oid_port_obj).split(OID_FDB_PORT + '.')[
            1]
        if addr_s != port_s: logger.warning(
            f"SNMP FDB DataMapper: Niezgodne sufiksy OID dla {host}. Pomijam."); return None
    except IndexError:
        logger.warning(f"SNMP FDB DataMapper: Błąd parsowania sufiksu OID dla {host}."); return None
    if not isinstance(val_addr_obj, OctetString): logger.warning(
        f"SNMP FDB DataMapper: Oczekiwano OctetString dla MAC dla {host}."); return None
    mac_s = ''.join(f"{b:02x}" for b in val_addr_obj.asOctets());
    if len(mac_s) != 12: logger.warning(f"SNMP FDB DataMapper: Nieprawidłowy MAC '{mac_s}' dla {host}."); return None
    try:
        base_port_id = int(val_port_obj)
    except (ValueError, TypeError):
        logger.warning(f"SNMP FDB DataMapper: Nie można sparsować BasePortID '{val_port_obj}' dla {host}."); return None
    return (mac_s, base_port_id)


def snmp_get_fdb_entries(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, int]]]:
    OIDS = ['1.3.6.1.2.1.17.4.3.1.1', '1.3.6.1.2.1.17.4.3.1.2']
    return adapt_snmp_function(host, community, timeout, retries, "FDB (Bridge-MIB)", OIDS, 2, _parse_fdb_data_mapper)


# --- FDB (Q-Bridge-MIB) ---
def _parse_qbridge_fdb_data_mapper(var_binds_list: List[rfc1902.ObjectType], host: str) -> Optional[
    Tuple[str, int, int]]:
    oid_addr_obj, oid_port_obj = var_binds_list[0][0], var_binds_list[1][0]
    val_addr_obj, val_port_obj = var_binds_list[0][1], var_binds_list[1][1]
    OID_QBRIDGE_ADDRESS = '1.3.6.1.2.1.17.7.1.2.2.1.1';
    OID_QBRIDGE_PORT = '1.3.6.1.2.1.17.7.1.2.2.1.2'
    if not (str(oid_addr_obj).startswith(OID_QBRIDGE_ADDRESS + '.') and str(oid_port_obj).startswith(
        OID_QBRIDGE_PORT + '.')): return "BREAK_OUTER_LOOP"  # type: ignore
    try:
        addr_suffix_p, port_suffix_p = str(oid_addr_obj).split(OID_QBRIDGE_ADDRESS + '.')[1].split('.'), \
        str(oid_port_obj).split(OID_QBRIDGE_PORT + '.')[1].split('.')
        if addr_suffix_p != port_suffix_p or len(addr_suffix_p) < 1 + 6: logger.warning(
            f"SNMP Q-FDB DataMapper: Niezgodne/niekompletne sufiksy OID dla {host}. Pomijam."); return None
        vlan_id = int(addr_suffix_p[0])
    except (IndexError, ValueError) as e:
        logger.warning(f"SNMP Q-FDB DataMapper: Błąd parsowania sufiksu/VLAN ID dla {host}: {e}."); return None
    if not isinstance(val_addr_obj, OctetString): logger.warning(
        f"SNMP Q-FDB DataMapper: Oczekiwano OctetString dla MAC dla {host}."); return None
    mac_s = ''.join(f"{b:02x}" for b in val_addr_obj.asOctets());
    if len(mac_s) != 12: logger.warning(f"SNMP Q-FDB DataMapper: Nieprawidłowy MAC '{mac_s}' dla {host}."); return None
    try:
        base_port_id = int(val_port_obj)
    except (ValueError, TypeError):
        logger.warning(
            f"SNMP Q-FDB DataMapper: Nie można sparsować BasePortID '{val_port_obj}' dla {host}."); return None
    return (mac_s, vlan_id, base_port_id)


def snmp_get_qbridge_fdb(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, int, int]]]:
    OIDS = ['1.3.6.1.2.1.17.7.1.2.2.1.1', '1.3.6.1.2.1.17.7.1.2.2.1.2']
    return adapt_snmp_function(host, community, timeout, retries, "FDB (Q-Bridge-MIB)", OIDS, 2,
                               _parse_qbridge_fdb_data_mapper)


# --- ARP ---
def _parse_arp_data_mapper(var_binds_list: List[rfc1902.ObjectType], host: str) -> Optional[Tuple[str, str, int]]:
    val_ifidx_obj, val_mac_obj, val_ip_obj = var_binds_list[0][1], var_binds_list[1][1], var_binds_list[2][1]
    try:
        if_idx = int(val_ifidx_obj)
    except (ValueError, TypeError):
        logger.warning(f"SNMP ARP DataMap: Nie można sparsować ifIndex '{val_ifidx_obj}' dla {host}."); return None
    if not isinstance(val_mac_obj, OctetString): logger.warning(
        f"SNMP ARP DataMap: Oczekiwano OctetString dla MAC dla {host}."); return None
    mac_s = ''.join(f"{b:02x}" for b in val_mac_obj.asOctets())
    if len(mac_s) != 12:
        if not mac_s: logger.debug(f"SNMP ARP DataMap: Pusty MAC dla {host}. Pomijam."); return None
        logger.warning(f"SNMP ARP DataMap: Nieprawidłowy MAC '{mac_s}' dla {host}.");
        return None
    ip_addr = str(val_ip_obj)
    if not ip_addr: logger.warning(f"SNMP ARP DataMap: Pusty IP dla {host}. Pomijam."); return None
    return (ip_addr, mac_s, if_idx)


def snmp_get_arp_entries(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, str, int]]]:
    OIDS = ['1.3.6.1.2.1.4.22.1.1', '1.3.6.1.2.1.4.22.1.2', '1.3.6.1.2.1.4.22.1.3']
    return adapt_snmp_function(host, community, timeout, retries, "ARP", OIDS, 3, _parse_arp_data_mapper)