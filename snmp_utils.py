# snmp_utils.py
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, nextCmd, OctetString
)
from pysnmp.smi import exval
from pysnmp.error import PySnmpError  # Główny błąd PySNMP
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
        # Jeśli error_indication jest już wyjątkiem (np. PySnmpError lub inny), to str(error_indication) da jego opis.
        logger.warning(f"SNMP {operation_name}: Błąd indykacji dla {host}: {error_indication}")
        return True  # Zawsze traktuj error_indication jako krytyczny
    elif error_status:  # error_status jest relevantne tylko jeśli error_indication jest None
        try:
            error_status_val = int(error_status)
            error_message = error_status.prettyPrint()
        except (ValueError, TypeError, AttributeError):  # Jeśli error_status nie jest standardowym obiektem PySNMP
            logger.warning(f"SNMP {operation_name}: Nieoczekiwany format error_status dla {host}: {error_status}")
            return True  # Traktuj jako błąd

        if error_status_val == 0:  # noError
            return False

        is_critical_status = True
        # Sprawdzenie, czy to tylko 'noSuchObject/Instance' lub 'endOfMibView'
        if var_binds:
            all_nosuch_or_end = True
            for oid_vb, val_vb in var_binds:
                if not (exval.noSuchObject.isSameTypeWith(val_vb) or
                        exval.noSuchInstance.isSameTypeWith(val_vb) or
                        exval.endOfMibView.isSameTypeWith(val_vb)):
                    all_nosuch_or_end = False
                    break
            if all_nosuch_or_end:
                logger.debug(
                    f"SNMP {operation_name}: Osiągnięto koniec MIB lub OID nie istnieje dla {host} "
                    f"(Status: {error_message}, Index: {error_index.prettyPrint() if hasattr(error_index, 'prettyPrint') else error_index})")
                is_critical_status = False  # To nie jest krytyczny błąd operacji

        if is_critical_status:
            logger.warning(
                f"SNMP {operation_name}: Błąd statusu dla {host}: {error_message} at "
                f"{error_index.prettyPrint() if hasattr(error_index, 'prettyPrint') else error_index}"
                f"{(' (pierwszy OID: ' + var_binds[0][0].prettyPrint() + ')') if var_binds and len(var_binds) > 0 and hasattr(var_binds[0][0], 'prettyPrint') else ''}")
            return True
    return False  # Brak błędu krytycznego


def _execute_snmp_next_cmd(snmp_engine, auth_data, transport_target, context_data, *var_types_and_oids):
    """
    Wewnętrzna funkcja pomocnicza do opakowania wywołania nextCmd i obsługi jego wyników
    w sposób bardziej odporny na błędy.
    """
    cmd_gen = nextCmd(
        snmp_engine, auth_data, transport_target, context_data,
        *var_types_and_oids,
        lexicographicMode=False, ignoreNonIncreasingOid=True
    )
    results = []
    item_index = 0
    while True:
        item_index += 1
        error_indication_to_add, error_status_to_add, error_index_to_add, var_binds_to_add = None, None, None, None
        try:
            response_item = next(cmd_gen)
            # Logowanie surowej odpowiedzi dla celów diagnostycznych
            logger.debug(f"SNMP _execute_snmp_next_cmd (item {item_index} for {transport_target.transportAddr[0]}): "
                         f"Raw response_item type={type(response_item)}, value='{str(response_item)[:200]}'")

            if isinstance(response_item, PySnmpError):
                error_indication_to_add = response_item
            elif isinstance(response_item, tuple) and len(response_item) == 4:
                error_indication_temp, error_status_temp, error_index_temp, var_binds_temp = response_item

                # Sprawdzenie czy error_indication_temp nie jest już wyjątkiem
                if isinstance(error_indication_temp, Exception) and not isinstance(error_indication_temp, PySnmpError):
                    logger.warning(f"SNMP _execute_snmp_next_cmd: errorIndication jest wyjątkiem "
                                   f"({type(error_indication_temp)}: {error_indication_temp}), "
                                   f"ale nie PySnmpError. Opakowuję w PySnmpError.")
                    error_indication_to_add = PySnmpError(str(error_indication_temp))
                    # Jeśli errorIndication był błędem, reszta krotki może nie być wiarygodna
                    error_status_to_add, error_index_to_add, var_binds_to_add = None, None, None
                else:
                    error_indication_to_add = error_indication_temp
                    error_status_to_add = error_status_temp
                    error_index_to_add = error_index_temp
                    var_binds_to_add = var_binds_temp
            else:
                # Jeśli response_item nie jest ani PySnmpError, ani oczekiwaną krotką
                logger.error(f"SNMP _execute_snmp_next_cmd: Nieoczekiwany typ/format ({type(response_item)}) "
                             f"zwrócony przez generator nextCmd. Element: {str(response_item)[:200]}")
                error_indication_to_add = PySnmpError(f"Unexpected type/format from nextCmd: {type(response_item)}")

        except StopIteration:
            logger.debug(
                f"SNMP _execute_snmp_next_cmd: StopIteration po {item_index - 1} elementach dla {transport_target.transportAddr[0]}.")
            break
        except PySnmpError as e_pysnmp:
            logger.warning(f"SNMP _execute_snmp_next_cmd: PySnmpError podczas iteracji nextCmd "
                           f"(item {item_index} dla {transport_target.transportAddr[0]}): {e_pysnmp}")
            error_indication_to_add = e_pysnmp
        except TypeError as e_type:
            logger.error(f"SNMP _execute_snmp_next_cmd: TypeError podczas iteracji/przetwarzania nextCmd "
                         f"(item {item_index} dla {transport_target.transportAddr[0]}): {e_type}", exc_info=True)
            error_indication_to_add = PySnmpError(f"TypeError in nextCmd iteration (item {item_index}): {e_type}")
        except Exception as e_generic:
            logger.error(f"SNMP _execute_snmp_next_cmd: Nieoczekiwany błąd podczas iteracji nextCmd "
                         f"(item {item_index} dla {transport_target.transportAddr[0]}): {e_generic}", exc_info=True)
            error_indication_to_add = PySnmpError(f"Unexpected error in nextCmd (item {item_index}): {e_generic}")

        results.append((error_indication_to_add, error_status_to_add, error_index_to_add, var_binds_to_add))

        # Sprawdź, czy błąd powinien przerwać dalsze pobieranie dla tej konkretnej operacji SNMP
        if _handle_snmp_response_tuple(
                transport_target.transportAddr[0],
                "generic _execute_snmp_next_cmd",  # Nazwa operacji mogłaby być przekazana jako argument
                error_indication_to_add,
                error_status_to_add,
                error_index_to_add,
                var_binds_to_add
        ):
            logger.warning(
                f"SNMP _execute_snmp_next_cmd: Przerywam pętlę dla {transport_target.transportAddr[0]} z powodu krytycznego błędu po elemencie {item_index}.")
            break

    return results


def snmp_get_lldp_neighbors(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[int, str, str]]]:
    OID_LLDP_REM_SYS_NAME = '1.0.8802.1.1.2.1.4.1.1.9';
    OID_LLDP_REM_PORT_ID = '1.0.8802.1.1.2.1.4.1.1.7'
    OID_LLDP_REM_PORT_DESCR = '1.0.8802.1.1.2.1.4.1.1.8';
    OID_LLDP_LOC_PORT_ID_SUBTYPE = '1.0.8802.1.1.2.1.3.7.1.2'  # LLDP-MIB::lldpLocPortIdSubtype
    OID_LLDP_LOC_PORT_ID = '1.0.8802.1.1.2.1.3.7.1.3'  # LLDP-MIB::lldpLocPortId

    neighs_data: Dict[Tuple[int, int], Dict[str, str]] = {};  # Klucz: (TimeMark, LocalPortNum)
    final_results: List[Tuple[int, str, str]] = []
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP LLDP: Pobieranie danych REM dla {host}...")
        # Pytamy o SystemName, PortId, PortDescr
        responses_rem = _execute_snmp_next_cmd(
            snmp_engine, CommunityData(community, mpModel=1),  # mpModel=1 dla SNMPv2c
            UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_LLDP_REM_SYS_NAME)),
            ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_ID)),
            ObjectType(ObjectIdentity(OID_LLDP_REM_PORT_DESCR))
        )

        for error_indication_rem, error_status_rem, error_index_rem, var_binds_rem in responses_rem:
            if _handle_snmp_response_tuple(host, "LLDP REM", error_indication_rem, error_status_rem, error_index_rem,
                                           var_binds_rem):
                if error_indication_rem: return None  # Krytyczny błąd, przerwij
                break  # Inny błąd statusu, ale pętla _execute_snmp_next_cmd już to obsłużyła

            if not var_binds_rem or len(var_binds_rem) < 3: continue  # Oczekujemy 3 wartości

            # Sprawdź czy to koniec MIB dla wszystkich OIDów
            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) or \
                         exval.noSuchObject.isSameTypeWith(val[1]) or \
                         exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds_rem)
            if is_end: break

            # Parsowanie indeksów z OID (TimeMark, LocalPortNum, RemIndex)
            # Przykład OID: 1.0.8802.1.1.2.1.4.1.1.9.TimeMark.LocalPortNum.RemIndex
            try:
                oid_str = str(var_binds_rem[0][0])  # Użyj OID z pierwszej zmiennej (np. SysName)
                # Sprawdź, czy OID rzeczywiście należy do tabeli lldpRemTable
                if not oid_str.startswith(OID_LLDP_REM_SYS_NAME + '.'):
                    logger.debug(
                        f"SNMP LLDP REM: OID {oid_str} nie należy do oczekiwanej tabeli. Przerywam zbieranie danych REM.")
                    break

                oid_parts = oid_str.split('.')
                # Długość OID_LLDP_REM_SYS_NAME to 11 części (licząc od 1)
                # Indeksy to TimeMark, LocalPortNum, RemIndex
                # np. OID_LLDP_REM_SYS_NAME to X.X.X...X (11 części)
                # pełny OID to X.X.X...X.TimeMark.LocalPortNum.RemIndex
                base_oid_len = len(OID_LLDP_REM_SYS_NAME.split('.'))  # Powinno być 11
                if len(oid_parts) < base_oid_len + 3:  # Potrzebujemy co najmniej 3 indeksy
                    logger.warning(
                        f"SNMP LLDP REM: Niekompletny OID: {oid_str} dla {host}. Oczekiwano więcej części indeksu.")
                    continue

                time_mark = int(oid_parts[base_oid_len])
                local_port_num = int(oid_parts[base_oid_len + 1])
                # rem_index = int(oid_parts[base_oid_len + 2]) # RemIndex nie jest nam potrzebny do klucza
            except (ValueError, IndexError) as e:
                logger.warning(
                    f"SNMP LLDP REM: Błąd parsowania indeksów z OID {str(var_binds_rem[0][0])} dla {host}: {e}")
                continue

            key = (time_mark, local_port_num)  # Używamy (TimeMark, LocalPortNum) jako klucza do danych sąsiada
            if key not in neighs_data: neighs_data[key] = {}

            neighs_data[key]['sysname'] = str(var_binds_rem[0][1])
            neighs_data[key]['port_id'] = str(var_binds_rem[1][1])
            neighs_data[key]['port_descr'] = str(var_binds_rem[2][1])

        if not neighs_data:
            logger.info(f"SNMP LLDP: Nie znaleziono danych REM sąsiadów dla {host}.")
            return []  # Zwróć pustą listę, jeśli nie ma sąsiadów

        # Krok 2: Pobierz mapowanie LocalPortNum na ifIndex
        logger.debug(f"SNMP LLDP: Pobieranie danych LOC (mapowanie PortNum -> ifIndex) dla {host}...")
        loc_port_to_ifindex_map: Dict[int, int] = {}  # Klucz: LocalPortNum, Wartość: ifIndex

        responses_loc = _execute_snmp_next_cmd(
            snmp_engine, CommunityData(community, mpModel=1),
            UdpTransportTarget((host, 161), timeout=2, retries=1), ContextData(),  # Krótszy timeout dla tej operacji
            ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID_SUBTYPE)),
            ObjectType(ObjectIdentity(OID_LLDP_LOC_PORT_ID))
        )

        for error_indication_loc, error_status_loc, error_index_loc, var_binds_loc in responses_loc:
            if _handle_snmp_response_tuple(host, "LLDP LOC", error_indication_loc, error_status_loc, error_index_loc,
                                           var_binds_loc):
                if error_indication_loc:
                    logger.warning(
                        f"SNMP LLDP: Problem z pobraniem mapowania LOC Port->ifIndex (błąd indykacji) dla {host}. Kontynuuję bez tej mapy.")
                break  # Przerwij zbieranie mapy LOC

            if not var_binds_loc or len(var_binds_loc) < 2: continue

            is_end_loc = all(exval.endOfMibView.isSameTypeWith(val[1]) or \
                             exval.noSuchObject.isSameTypeWith(val[1]) or \
                             exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds_loc)
            if is_end_loc: break

            # Parsowanie LocalPortNum z OID (OID_LLDP_LOC_PORT_ID_SUBTYPE.LocalPortNum)
            # lub OID_LLDP_LOC_PORT_ID.LocalPortNum
            try:
                oid_loc_str = str(var_binds_loc[0][0])
                if not oid_loc_str.startswith(OID_LLDP_LOC_PORT_ID_SUBTYPE + '.'):
                    logger.debug(
                        f"SNMP LLDP LOC: OID {oid_loc_str} nie należy do oczekiwanej tabeli lldpLocPortTable. Przerywam zbieranie danych LOC.")
                    break

                oid_loc_parts = oid_loc_str.split('.')
                base_oid_loc_len = len(OID_LLDP_LOC_PORT_ID_SUBTYPE.split('.'))  # Powinno być 10
                if len(oid_loc_parts) < base_oid_loc_len + 1:
                    logger.warning(f"SNMP LLDP LOC: Niekompletny OID: {oid_loc_str} dla {host}.")
                    continue

                local_port_num_loc = int(oid_loc_parts[base_oid_loc_len])
                port_id_subtype = int(var_binds_loc[0][1])
                port_id_value_str = str(var_binds_loc[1][1])  # To jest lldpLocPortId

                # Chcemy ifIndex, który jest typem 5 (ifAlias) lub 7 (ifName) w lldpLocPortIdSubtype,
                # ale standardowo lldpLocPortId, gdy subtype=ifIndex(5), *jest* ifIndexem.
                # IEEE Std 802.1AB-2016, Tabela 10-3 – lldpLocPortIdSubtype
                # 5: interfaceAlias, 7: interfaceName
                # W praktyce, często lldpLocPortId jest ifIndexem, jeśli subtype jest 'interface alias' (5) lub 'interface name' (7)
                # a urządzenie mapuje te nazwy na ifIndex.
                # Tutaj szukamy konkretnie subtype=5 (ifIndex według niektórych implementacji) lub interpretujemy port_id_value_str.
                # LibreNMS (OID: .1.0.8802.1.1.2.1.3.7.1.3) używa lldpLocPortId.
                # Czasem lldpLocPortId sam w sobie jest ifIndexem.

                if port_id_subtype == 5:  # interfaceAlias
                    # W niektórych implementacjach, jeśli lldpLocPortIdSubtype to 'interfaceAlias',
                    # to lldpLocPortId może być bezpośrednio ifIndexem.
                    try:
                        ifindex = int(port_id_value_str)
                        loc_port_to_ifindex_map[local_port_num_loc] = ifindex
                        logger.debug(
                            f"SNMP LLDP LOC: Zmapowano LocalPortNum {local_port_num_loc} -> ifIndex {ifindex} (subtype 5) dla {host}")
                    except ValueError:
                        logger.warning(
                            f"SNMP LLDP LOC: Nie można sparsować ifIndex '{port_id_value_str}' dla LocalPortNum {local_port_num_loc} (subtype 5) na {host}")
                # Można dodać obsługę subtype=7 (interfaceName) i próbować zmapować nazwę na ifIndex z innej tabeli,
                # ale to komplikuje sprawę. Na razie polegamy na subtype=5 lub braku mapy.

            except (ValueError, IndexError) as e:
                logger.warning(f"SNMP LLDP LOC: Błąd parsowania OID/wartości dla LocalPortNum na {host}: {e}")
                continue

        if not loc_port_to_ifindex_map:
            logger.info(
                f"SNMP LLDP: Nie udało się zbudować mapy localPortNum -> ifIndex dla {host} (mapa pusta lub błąd). Sąsiedzi mogą mieć ifIndex=0.")

        # Krok 3: Połącz dane REM z mapą ifIndex z LOC
        for key, data in neighs_data.items():
            _time_mark, local_port_num_key = key  # Rozpakowujemy klucz
            ifidx = loc_port_to_ifindex_map.get(local_port_num_key, 0)  # Użyj 0 jeśli nie znaleziono
            if ifidx == 0:
                logger.debug(
                    f"SNMP LLDP: Brak mapowania ifIndex dla LocalPortNum {local_port_num_key} na {host}. Używam ifIndex=0.")

            remote_sys_name = data.get('sysname', '').strip()
            remote_port_id_raw = data.get('port_id', '').strip()
            remote_port_descr = data.get('port_descr', '').strip()

            if not remote_sys_name: remote_sys_name = "UnknownSystemLLDP"  # Domyślna nazwa, jeśli pusta

            # Logika wyboru lepszego portu zdalnego (Port ID vs Port Description)
            chosen_remote_port = remote_port_id_raw
            if remote_port_descr and remote_port_descr.lower() != "not advertised":
                # Jeśli Port ID wygląda na adres MAC lub jest bardzo długi/dziwny, a Port Description jest sensowny
                if (':' in remote_port_id_raw or len(remote_port_id_raw) > 30) and \
                        remote_port_descr and len(remote_port_descr) < len(remote_port_id_raw) and \
                        not (':' in remote_port_descr):  # Port desc nie powinien być MAC
                    chosen_remote_port = remote_port_descr
                # Lub jeśli Port ID jest pusty/niezaimplementowany, a Port Description istnieje
                elif (not remote_port_id_raw or remote_port_id_raw.lower() == "not advertised") and remote_port_descr:
                    chosen_remote_port = remote_port_descr

            if not chosen_remote_port or chosen_remote_port.lower() == "not advertised":
                logger.debug(
                    f"SNMP LLDP: Pomijam sąsiada dla LocalPortNum {local_port_num_key} na {host} - brak użytecznego Remote Port ID/Description.")
                continue

            final_results.append((ifidx, remote_sys_name, chosen_remote_port))

    except Exception as e_outer:
        logger.error(f"SNMP LLDP: Ogólny, nieoczekiwany błąd (poza pętlą SNMP) dla {host}: {e_outer}", exc_info=True)
        return None  # Wskazuje na poważny problem z całą operacją

    logger.info(f"SNMP LLDP: Zakończono dla {host}, znaleziono {len(final_results)} sąsiadów.")
    return final_results


def snmp_get_cdp_neighbors(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[int, str, str]]]:
    # OIDy dla tabeli cdpCacheTable
    OID_CDP_IFINDEX = '1.3.6.1.4.1.9.9.23.1.1.1.1.1'  # cdpCacheIfIndex - UWAGA: Poprawiono OID! Wcześniej było .6
    OID_CDP_DEV_ID = '1.3.6.1.4.1.9.9.23.1.2.1.1.6'  # cdpCtAddress (jedno z wielu, ale Device ID jest częściej w cdpCacheDeviceId)
    # Powinno być cdpCacheDeviceId = 1.3.6.1.4.1.9.9.23.1.1.1.1.6
    # ale ten OID był już użyty jako ifIndex, co jest błędem.
    # Użyjmy bardziej standardowych OIDów dla CDPv2-MIB
    OID_CDP_CACHE_DEVICE_ID = '1.3.6.1.4.1.9.9.23.1.1.1.1.6'  # cdpCacheDeviceId
    OID_CDP_CACHE_DEVICE_PORT = '1.3.6.1.4.1.9.9.23.1.1.1.1.7'  # cdpCacheDevicePort
    # OID_CDP_IFINDEX z cdpGlobalTable nie ma sensu w kontekście sąsiadów
    # Potrzebujemy cdpCacheIfIndex

    neighs_map: Dict[str, Dict[str, Any]] = {}  # Klucz to unikalny identyfikator sąsiada (np. z OID)
    final_results: List[Tuple[int, str, str]] = []
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP CDP: Pobieranie danych dla {host}...")
        responses = _execute_snmp_next_cmd(
            snmp_engine, CommunityData(community, mpModel=1),
            UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_CDP_IFINDEX)),  # cdpCacheIfIndex
            ObjectType(ObjectIdentity(OID_CDP_CACHE_DEVICE_ID)),  # cdpCacheDeviceId
            ObjectType(ObjectIdentity(OID_CDP_CACHE_DEVICE_PORT))  # cdpCacheDevicePort
        )

        for error_indication, error_status, error_index, var_binds in responses:
            if _handle_snmp_response_tuple(host, "CDP", error_indication, error_status, error_index, var_binds):
                if error_indication: return None
                break

            if not var_binds or len(var_binds) < 3: continue

            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) or \
                         exval.noSuchObject.isSameTypeWith(val[1]) or \
                         exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break

            # Indeksem tabeli cdpCacheTable jest cdpCacheIfIndex oraz cdpCacheDeviceIndex (kolejny sąsiad na tym samym ifIndex)
            # OID dla cdpCacheIfIndex: 1.3.6.1.4.1.9.9.23.1.1.1.1.1.cdpCacheIfIndex.cdpCacheDeviceIndex
            # OID dla cdpCacheDeviceId: 1.3.6.1.4.1.9.9.23.1.1.1.1.6.cdpCacheIfIndex.cdpCacheDeviceIndex
            # OID dla cdpCacheDevicePort: 1.3.6.1.4.1.9.9.23.1.1.1.1.7.cdpCacheIfIndex.cdpCacheDeviceIndex

            oid_ifidx_str = str(var_binds[0][0])
            oid_devid_str = str(var_binds[1][0])
            oid_portid_str = str(var_binds[2][0])

            # Sprawdzenie, czy OIDy należą do tej samej instancji tabeli
            if not oid_ifidx_str.startswith(OID_CDP_IFINDEX + '.') or \
                    not oid_devid_str.startswith(OID_CDP_CACHE_DEVICE_ID + '.') or \
                    not oid_portid_str.startswith(OID_CDP_CACHE_DEVICE_PORT + '.'):
                logger.debug(f"SNMP CDP: OIDy nie pasują do oczekiwanych wzorców tabeli cdpCacheTable. Przerywam.")
                break

            try:
                # Wyodrębnij indeksy (cdpCacheIfIndex.cdpCacheDeviceIndex)
                suffix_ifidx = oid_ifidx_str.split(OID_CDP_IFINDEX + '.')[1]
                suffix_devid = oid_devid_str.split(OID_CDP_CACHE_DEVICE_ID + '.')[1]
                suffix_portid = oid_portid_str.split(OID_CDP_CACHE_DEVICE_PORT + '.')[1]

                if not (suffix_ifidx == suffix_devid == suffix_portid):
                    logger.warning(
                        f"SNMP CDP: Niezgodne sufiksy OID dla wpisu: IfIdxS='{suffix_ifidx}', DevIdS='{suffix_devid}', PortIdS='{suffix_portid}' na {host}. Pomijam.")
                    continue

                oid_key = suffix_ifidx  # Użyj pełnego sufiksu jako klucza (np. "IfIndex.DeviceIndex")
            except IndexError:
                logger.warning(
                    f"SNMP CDP: Błąd parsowania sufiksu OID dla {host}. OIDy: {oid_ifidx_str}, {oid_devid_str}, {oid_portid_str}")
                continue

            if oid_key not in neighs_map: neighs_map[oid_key] = {}

            try:
                neighs_map[oid_key]['ifindex'] = int(var_binds[0][1])
            except ValueError:
                logger.warning(
                    f"SNMP CDP: Nie można sparsować ifIndex '{var_binds[0][1]}' dla klucza OID {oid_key} na {host}.")
                continue  # Pomiń ten wpis, jeśli ifIndex jest nieprawidłowy

            neighs_map[oid_key]['dev_id'] = str(var_binds[1][1]).strip()
            neighs_map[oid_key]['port_id'] = str(var_binds[2][1]).strip()

        for data_key, data_val in neighs_map.items():
            if 'ifindex' in data_val and data_val.get('dev_id') and data_val.get('port_id'):
                final_results.append((data_val['ifindex'], data_val['dev_id'], data_val['port_id']))
            else:
                logger.debug(
                    f"SNMP CDP: Pominięto niekompletny wpis z mapy dla klucza OID {data_key} na {host}: {data_val}")

    except Exception as e_outer:
        logger.error(f"SNMP CDP: Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP CDP: Zakończono dla {host}, znaleziono {len(final_results)} sąsiadów.")
    return final_results


# --- Pozostałe funkcje snmp_get_bridge_baseport_ifindex, snmp_get_fdb_entries,
# --- snmp_get_qbridge_fdb, snmp_get_arp_entries pozostają takie same jak w poprzedniej wersji,
# --- ponieważ używają _execute_snmp_next_cmd, które zostało poprawione.
# --- Jeśli dla nich również występują problemy, należy je zdiagnozować osobno,
# --- ale ulepszona _execute_snmp_next_cmd powinna dać lepsze logi.

def snmp_get_bridge_baseport_ifindex(host: str, community: str, timeout: int = 2, retries: int = 1) -> Optional[
    Dict[int, int]]:
    OID_BASE_PORT_IFINDEX = '1.3.6.1.2.1.17.1.4.1.2'  # dot1dBasePortIfIndex
    base_to_ifindex_map: Dict[int, int] = {}
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP BasePortIfIndex: Pobieranie danych dla {host}...")
        responses = _execute_snmp_next_cmd(
            snmp_engine, CommunityData(community, mpModel=1),
            UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_BASE_PORT_IFINDEX))
        )
        for error_indication, error_status, error_index, var_binds in responses:
            if _handle_snmp_response_tuple(host, "BasePortIfIndex", error_indication, error_status, error_index,
                                           var_binds):
                if error_indication: return None
                break

            if not var_binds: continue  # Powinien być jeden varbind

            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) or \
                         exval.noSuchObject.isSameTypeWith(val[1]) or \
                         exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break

            oid, value = var_binds[0]  # Oczekujemy jednego OIDa
            oid_str = str(oid)

            if not oid_str.startswith(OID_BASE_PORT_IFINDEX + '.'):  # Upewnij się, że to nadal ta sama tabela
                logger.debug(
                    f"SNMP BasePortIfIndex: OID {oid_str} nie pasuje do wzorca {OID_BASE_PORT_IFINDEX}. Przerywam.")
                break

            try:
                base_port_id = int(oid_str.split('.')[-1])  # Ostatnia część OID to base_port_id
                ifindex = int(value)
                base_to_ifindex_map[base_port_id] = ifindex
            except (ValueError, IndexError) as e:
                logger.warning(
                    f"SNMP BasePortIfIndex: Błąd parsowania OID/wartości dla {host}: OID={oid_str}, Value={value}, Błąd={e}")
                continue
    except Exception as e_outer:
        logger.error(f"SNMP BasePortIfIndex: Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP BasePortIfIndex: Zakończono dla {host}, zmapowano {len(base_to_ifindex_map)} portów.")
    return base_to_ifindex_map


def snmp_get_fdb_entries(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, int]]]:
    OID_FDB_ADDRESS = '1.3.6.1.2.1.17.4.3.1.1'  # dot1dTpFdbAddress
    OID_FDB_PORT = '1.3.6.1.2.1.17.4.3.1.2'  # dot1dTpFdbPort
    # OID_FDB_STATUS = '1.3.6.1.2.1.17.4.3.1.3' # dot1dTpFdbStatus (np. 3=learned, 1=other, 2=invalid, 4=self, 5=mgmt)

    entries: List[Tuple[str, int]] = []
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP FDB (Bridge-MIB): Pobieranie danych dla {host}...")
        # Pytamy o adres MAC i port
        responses = _execute_snmp_next_cmd(
            snmp_engine, CommunityData(community, mpModel=1),
            UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_FDB_ADDRESS)),
            ObjectType(ObjectIdentity(OID_FDB_PORT))
        )
        for error_indication, error_status, error_index, var_binds in responses:
            if _handle_snmp_response_tuple(host, "FDB (Bridge-MIB)", error_indication, error_status, error_index,
                                           var_binds):
                if error_indication: return None
                break

            if not var_binds or len(var_binds) < 2: continue

            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) or \
                         exval.noSuchObject.isSameTypeWith(val[1]) or \
                         exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break

            oid_addr_str, oid_port_str = str(var_binds[0][0]), str(var_binds[1][0])

            # Sprawdzenie, czy OIDy należą do tej samej instancji tabeli
            if not oid_addr_str.startswith(OID_FDB_ADDRESS + '.') or \
                    not oid_port_str.startswith(OID_FDB_PORT + '.'):
                logger.debug(f"SNMP FDB: OIDy nie pasują do oczekiwanych wzorców tabeli dot1dTpFdbTable. Przerywam.")
                break

            try:
                # Indeksem tabeli dot1dTpFdbTable jest adres MAC (przekształcony na części OID)
                addr_suffix = oid_addr_str.split(OID_FDB_ADDRESS + '.')[1]
                port_suffix = oid_port_str.split(OID_FDB_PORT + '.')[1]

                if addr_suffix != port_suffix:  # Sufiksy indeksu muszą być takie same
                    logger.warning(
                        f"SNMP FDB: Niezgodne sufiksy OID dla wpisu: AddrS='{addr_suffix}', PortS='{port_suffix}' na {host}. Pomijam.")
                    continue
            except IndexError:
                logger.warning(
                    f"SNMP FDB: Błąd parsowania sufiksu OID dla {host}. OIDy: Addr={oid_addr_str}, Port={oid_port_str}")
                continue

            mac_value = var_binds[0][1]
            if not isinstance(mac_value, OctetString):  # Adres MAC powinien być OctetString
                logger.warning(
                    f"SNMP FDB: Oczekiwano OctetString dla MAC, otrzymano {type(mac_value)} dla {host}, OID {oid_addr_str}.")
                continue

            mac_str = ''.join(f"{b:02x}" for b in mac_value.asOctets())
            if len(mac_str) != 12:  # Poprawny MAC ma 12 znaków hex
                logger.warning(
                    f"SNMP FDB: Otrzymano nieprawidłowy format MAC '{mac_str}' (długość {len(mac_str)}) dla {host}.")
                continue

            try:
                base_port_id = int(var_binds[1][1])  # dot1dTpFdbPort to BasePortID
            except ValueError:
                logger.warning(
                    f"SNMP FDB: Nie można sparsować BasePortID '{var_binds[1][1]}' dla MAC {mac_str} na {host}.")
                continue

            entries.append((mac_str, base_port_id))
    except Exception as e_outer:
        logger.error(f"SNMP FDB (Bridge-MIB): Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP FDB (Bridge-MIB): Zakończono dla {host}, znaleziono {len(entries)} wpisów.")
    return entries


def snmp_get_qbridge_fdb(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, int, int]]]:
    OID_QBRIDGE_ADDRESS = '1.3.6.1.2.1.17.7.1.2.2.1.1'  # dot1qTpFdbAddress (był błąd w nazwie, to jest poprawny OID)
    OID_QBRIDGE_PORT = '1.3.6.1.2.1.17.7.1.2.2.1.2'  # dot1qTpFdbPort
    # Indeksem tabeli dot1qTpFdbTable jest VLAN ID oraz adres MAC.
    # OID_QBRIDGE_ADDRESS.<VLAN_ID>.<MAC_part1>.<MAC_part2>...
    # OID_QBRIDGE_PORT.<VLAN_ID>.<MAC_part1>.<MAC_part2>...

    entries: List[Tuple[str, int, int]] = []  # (mac, vlan_id, base_port_id)
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP FDB (Q-Bridge-MIB): Pobieranie danych dla {host}...")
        responses = _execute_snmp_next_cmd(
            snmp_engine, CommunityData(community, mpModel=1),
            UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_QBRIDGE_ADDRESS)),
            ObjectType(ObjectIdentity(OID_QBRIDGE_PORT))
        )
        for error_indication, error_status, error_index, var_binds in responses:
            if _handle_snmp_response_tuple(host, "FDB (Q-Bridge-MIB)", error_indication, error_status, error_index,
                                           var_binds):
                if error_indication: return None
                break

            if not var_binds or len(var_binds) < 2: continue

            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) or \
                         exval.noSuchObject.isSameTypeWith(val[1]) or \
                         exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break

            oid_addr_str, oid_port_str = str(var_binds[0][0]), str(var_binds[1][0])

            if not oid_addr_str.startswith(OID_QBRIDGE_ADDRESS + '.') or \
                    not oid_port_str.startswith(OID_QBRIDGE_PORT + '.'):
                logger.debug(f"SNMP Q-FDB: OIDy nie pasują do oczekiwanych wzorców tabeli dot1qTpFdbTable. Przerywam.")
                break

            try:
                # Sufiks OID dla dot1qTpFdbTable: VLAN_ID + 6 oktetów MAC
                addr_suffix_parts = oid_addr_str.split(OID_QBRIDGE_ADDRESS + '.')[1].split('.')
                port_suffix_parts = oid_port_str.split(OID_QBRIDGE_PORT + '.')[1].split('.')

                if addr_suffix_parts != port_suffix_parts:
                    logger.warning(
                        f"SNMP Q-FDB: Niezgodne sufiksy OID dla wpisu: AddrS='{'.'.join(addr_suffix_parts)}', PortS='{'.'.join(port_suffix_parts)}' na {host}. Pomijam.")
                    continue

                if len(addr_suffix_parts) < 1 + 6:  # Potrzebujemy VLAN ID + 6 części MAC adresu
                    logger.warning(
                        f"SNMP Q-FDB: Niekompletny sufiks OID '{'.'.join(addr_suffix_parts)}' dla {host}. Oczekiwano VLAN ID i 6 części MAC.")
                    continue

                vlan_id = int(addr_suffix_parts[0])
                # Adres MAC jest w wartości, nie w OID (wartość OID_QBRIDGE_ADDRESS to sam MAC)
            except (IndexError, ValueError) as e:
                logger.warning(
                    f"SNMP Q-FDB: Błąd parsowania sufiksu OID lub VLAN ID dla {host}: {e}. OIDy: Addr={oid_addr_str}, Port={oid_port_str}")
                continue

            mac_value = var_binds[0][1]  # Wartość OID_QBRIDGE_ADDRESS to MAC
            if not isinstance(mac_value, OctetString):
                logger.warning(
                    f"SNMP Q-FDB: Oczekiwano OctetString dla MAC, otrzymano {type(mac_value)} dla {host}, VLAN {vlan_id}, OID {oid_addr_str}.")
                continue

            mac_str = ''.join(f"{b:02x}" for b in mac_value.asOctets())
            if len(mac_str) != 12:
                logger.warning(
                    f"SNMP Q-FDB: Otrzymano nieprawidłowy format MAC '{mac_str}' (długość {len(mac_str)}) dla {host}, VLAN {vlan_id}.")
                continue

            try:
                base_port_id = int(var_binds[1][1])  # dot1qTpFdbPort to BasePortID
            except ValueError:
                logger.warning(
                    f"SNMP Q-FDB: Nie można sparsować BasePortID '{var_binds[1][1]}' dla MAC {mac_str}, VLAN {vlan_id} na {host}.")
                continue

            entries.append((mac_str, vlan_id, base_port_id))
    except Exception as e_outer:
        logger.error(f"SNMP FDB (Q-Bridge-MIB): Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP FDB (Q-Bridge-MIB): Zakończono dla {host}, znaleziono {len(entries)} wpisów.")
    return entries


def snmp_get_arp_entries(host: str, community: str, timeout: int = 5, retries: int = 1) -> Optional[
    List[Tuple[str, str, int]]]:
    # Tabela ipNetToMediaTable z RFC 1213 (IP-MIB)
    OID_ARP_IFINDEX = '1.3.6.1.2.1.4.22.1.1'  # ipNetToMediaIfIndex
    OID_ARP_MAC = '1.3.6.1.2.1.4.22.1.2'  # ipNetToMediaPhysAddress
    OID_ARP_IP = '1.3.6.1.2.1.4.22.1.3'  # ipNetToMediaNetAddress
    # OID_ARP_TYPE = '1.3.6.1.2.1.4.22.1.4'   # ipNetToMediaType (np. 3=dynamic, 4=static)

    entries: List[Tuple[str, str, int]] = []  # (ip_address, mac_address, ifIndex)
    snmp_engine = SnmpEngine()
    try:
        logger.debug(f"SNMP ARP: Pobieranie danych dla {host}...")
        responses = _execute_snmp_next_cmd(
            snmp_engine, CommunityData(community, mpModel=1),
            UdpTransportTarget((host, 161), timeout=timeout, retries=retries), ContextData(),
            ObjectType(ObjectIdentity(OID_ARP_IFINDEX)),
            ObjectType(ObjectIdentity(OID_ARP_MAC)),
            ObjectType(ObjectIdentity(OID_ARP_IP))
        )
        for error_indication, error_status, error_index, var_binds in responses:
            if _handle_snmp_response_tuple(host, "ARP", error_indication, error_status, error_index, var_binds):
                if error_indication: return None
                break

            if not var_binds or len(var_binds) < 3: continue

            is_end = all(exval.endOfMibView.isSameTypeWith(val[1]) or \
                         exval.noSuchObject.isSameTypeWith(val[1]) or \
                         exval.noSuchInstance.isSameTypeWith(val[1]) for val in var_binds)
            if is_end: break

            oid_ifindex_str, oid_mac_str, oid_ip_str = str(var_binds[0][0]), str(var_binds[1][0]), str(var_binds[2][0])

            # Indeksem tabeli ipNetToMediaTable jest ipNetToMediaIfIndex oraz ipNetToMediaNetAddress
            # OID_ARP_IFINDEX.<ifIndex>.<IP_part1>.<IP_part2>.<IP_part3>.<IP_part4>
            # OID_ARP_MAC.<ifIndex>.<IP_part1>...
            # OID_ARP_IP.<ifIndex>.<IP_part1>...

            if not oid_ifindex_str.startswith(OID_ARP_IFINDEX + '.') or \
                    not oid_mac_str.startswith(OID_ARP_MAC + '.') or \
                    not oid_ip_str.startswith(OID_ARP_IP + '.'):
                logger.debug(f"SNMP ARP: OIDy nie pasują do oczekiwanych wzorców tabeli ipNetToMediaTable. Przerywam.")
                break

            try:
                ifindex_suffix = oid_ifindex_str.split(OID_ARP_IFINDEX + '.')[1]
                mac_suffix = oid_mac_str.split(OID_ARP_MAC + '.')[1]
                ip_suffix = oid_ip_str.split(OID_ARP_IP + '.')[1]

                if not (ifindex_suffix == mac_suffix == ip_suffix):
                    logger.warning(
                        f"SNMP ARP: Niezgodne sufiksy OID dla wpisu: IfIdxS='{ifindex_suffix}', MacS='{mac_suffix}', IpS='{ip_suffix}' na {host}. Pomijam.")
                    continue
            except IndexError:
                logger.warning(
                    f"SNMP ARP: Błąd parsowania sufiksu OID dla {host}. OIDy: IfIdx={oid_ifindex_str}, MAC={oid_mac_str}, IP={oid_ip_str}")
                continue

            try:
                if_index = int(var_binds[0][1])  # Wartość ipNetToMediaIfIndex
            except ValueError:
                logger.warning(f"SNMP ARP: Nie można sparsować ifIndex '{var_binds[0][1]}' dla {host}.")
                continue

            mac_value = var_binds[1][1]  # Wartość ipNetToMediaPhysAddress
            if not isinstance(mac_value, OctetString):
                logger.warning(
                    f"SNMP ARP: Oczekiwano OctetString dla MAC, otrzymano {type(mac_value)} dla {host}, OID {oid_mac_str}.")
                continue

            mac_str = ''.join(f"{b:02x}" for b in mac_value.asOctets())
            if len(mac_str) != 12:
                # Czasem MAC może być pusty (np. dla wpisów ARP typu 'incomplete')
                if not mac_str:  # Jeśli MAC jest pusty
                    logger.debug(f"SNMP ARP: Pusty adres MAC dla wpisu ARP na {host}, OID {oid_mac_str}. Pomijam.")
                    continue
                logger.warning(
                    f"SNMP ARP: Otrzymano nieprawidłowy format MAC '{mac_str}' (długość {len(mac_str)}) dla {host}.")
                continue

            ip_address = str(var_binds[2][1])  # Wartość ipNetToMediaNetAddress
            if not ip_address:  # IP nie powinno być puste
                logger.warning(f"SNMP ARP: Pusty adres IP dla wpisu ARP na {host}, MAC {mac_str}. Pomijam.")
                continue

            entries.append((ip_address, mac_str, if_index))
    except Exception as e_outer:
        logger.error(f"SNMP ARP: Ogólny błąd dla {host}: {e_outer}", exc_info=True)
        return None
    logger.info(f"SNMP ARP: Zakończono dla {host}, znaleziono {len(entries)} wpisów.")
    return entries