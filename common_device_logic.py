# common_device_logic.py
import logging
import re
import math
from typing import List, Dict, Tuple, Optional, Any, NamedTuple

from librenms_client import LibreNMSAPI
from utils import get_canonical_identifier

# Stałe związane z ograniczeniem wyświetlania portów
MAX_PHYSICAL_PORTS_FOR_CHASSIS_DISPLAY = 96
DEFAULT_PORTS_PER_ROW_NORMAL = 26
DEFAULT_PORTS_PER_ROW_LARGE_DEVICE = 48


class PortEndpointData(NamedTuple):
    cell_id: str
    x: float
    y: float
    orientation: str


class DynamicLayoutInfo(NamedTuple):
    width: float
    height: float
    num_rows: int
    ports_per_row: int  # To powinno być skonfigurowane ports_per_row użyte do obliczeń


DEFAULT_PORTS_PER_ROW = DEFAULT_PORTS_PER_ROW_NORMAL
PORT_WIDTH = 20.0;
PORT_HEIGHT = 20.0;
HORIZONTAL_SPACING = 10.0;
VERTICAL_SPACING = 15.0
ROW_OFFSET_Y = 7.0;
CHASSIS_PADDING_X = 15.0;
CHASSIS_PADDING_Y = 7.0
MIN_CHASSIS_WIDTH = 100.0;
MIN_CHASSIS_HEIGHT = 60.0;
DEFAULT_CHASSIS_HEIGHT_NO_PORTS = 40.0
WAYPOINT_OFFSET = 20.0;
LOGICAL_IF_LIST_MAX_HEIGHT = 150.0;
PHYSICAL_PORT_LIST_MAX_HEIGHT = 200.0
LABEL_LINE_HEIGHT = 10.0;
LABEL_PADDING = 4.0
STACK_DETECTION_THRESHOLD = DEFAULT_PORTS_PER_ROW_LARGE_DEVICE * 2 + 4

logger = logging.getLogger(__name__)

try:
    import natsort

    natsort_keygen = natsort.natsort_keygen()
    logger.debug("Moduł 'natsort' zaimportowany pomyślnie dla common_device_logic.")
except ImportError:
    logger.warning("Moduł 'natsort' nie znaleziony. Sortowanie nazw portów będzie standardowe.")


    def natsort_keygen():
        return lambda x: str(x)


class DeviceDisplayData(NamedTuple):
    device_api_info: Dict[str, Any]
    canonical_identifier: str
    all_physical_ports: List[Dict[str, Any]]
    physical_ports_for_chassis_layout: List[Dict[str, Any]]
    logical_interfaces: List[Dict[str, Any]]
    mgmt0_port_info: Optional[Dict[str, Any]]
    total_physical_ports_before_limit: int
    chassis_layout: DynamicLayoutInfo
    is_stack: bool
    ports_display_limited: bool


def classify_ports(ports_data_from_api: List[Dict[str, Any]], device_hostname_for_log: str = "Nieznane urządzenie") -> \
        Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    physical_ports: List[Dict[str, Any]] = []
    logical_interfaces: List[Dict[str, Any]] = []
    mgmt0_port_info: Optional[Dict[str, Any]] = None
    physical_name_patterns = re.compile(
        r'^(Eth|Gi|Te|Fa|Hu|Twe|Fo|mgmt|Management|Serial|Port\s?\d|SFP|XFP|QSFP|em\d|ens\d|eno\d|enp\d+s\d+|ge-|xe-|et-|bri|lan\d|po\d+)',
        re.IGNORECASE)
    stack_port_pattern = re.compile(r'^[a-zA-Z]+[-]?\d+/\d+(/\d+)+$', re.IGNORECASE)
    logical_name_patterns = re.compile(
        r'^(Vlan|vl|Loopback|Lo|lo\d*|Port-channel|Po|Bundle-Ether|ae|Tunnel|Tun|Null|Nu|Cpu|Fabric|Voice|Async|Group-Async|ipsec|gre|sit|pimreg|mgmt[1-9]|Irq|Service-Engine|Dialer|Virtual-Access|Virtual-Template|Subinterface|BVI|BV|Cellular)|.*\.\d+$',
        re.IGNORECASE)
    physical_types_iana = {'ethernetcsmacd', 'fastether', 'gigabitethernet', 'fastetherfx', 'infinitiband', 'sonet',
                           'sdsl', 'hdsl', 'shdsl', 'adsl', 'radsl', 'vdsl', 'ieee80211', 'opticalchannel',
                           'fibrechannel', 'propvirtual', 'proppointtopointserial', 'ppp', 'eon', 'tokenring', 'atm',
                           'frameRelay', 'hssi', 'hippi', 'isdn', 'x25', 'aal5', 'voiceem', 'voicefxo', 'voicefxs',
                           'digitalpowerline', 'modem', 'serial', 'docsCableMaclayer', 'docsCableDownstream',
                           'docsCableUpstream', 'ieee8023adLag'}
    logical_types_iana = {'l3ipvlan', 'softwareLoopback', 'tunnel', 'propMultiplexor', 'bridge', 'other', 'l2vlan',
                          'voiceoverip', 'atmSubInterface', 'virtualipaddress', 'mp OvaLink', 'iana vielf'}
    temp_mgmt0_candidates = []
    other_ports_to_classify = []
    for port in ports_data_from_api:
        if_name_lower = str(port.get('ifName', '')).lower()
        if_descr_lower = str(port.get('ifDescr', '')).lower()
        if 'mgmt0' == if_name_lower or 'management0' == if_name_lower or 'mgmt0' == if_descr_lower or 'management0' == if_descr_lower or (
                if_name_lower.startswith("mgmt") and if_name_lower.endswith("0")) or (
                if_descr_lower.startswith("mgmt") and if_descr_lower.endswith("0")):
            temp_mgmt0_candidates.append(port)
        else:
            other_ports_to_classify.append(port)
    if temp_mgmt0_candidates:
        mgmt0_with_mac = [p for p in temp_mgmt0_candidates if p.get('ifPhysAddress')];
        mgmt0_port_info = mgmt0_with_mac[0] if mgmt0_with_mac else temp_mgmt0_candidates[0]
        logger.debug(
            f"Port mgmt0 zidentyfikowany dla {device_hostname_for_log}: {mgmt0_port_info.get('ifName')} (ID: {mgmt0_port_info.get('port_id')})")
        if mgmt0_port_info not in physical_ports: physical_ports.append(mgmt0_port_info)  # Dodaj do listy fizycznych
    for port_info in other_ports_to_classify:
        if_name, if_descr, if_type_raw, if_phys_address, if_oper_status = str(port_info.get('ifName', '')), str(
            port_info.get('ifDescr', '')), port_info.get('ifType'), str(port_info.get('ifPhysAddress', '')), str(
            port_info.get('ifOperStatus', '')).lower()
        if port_info != mgmt0_port_info and (if_oper_status == "notpresent" or (
                if_oper_status == "lowerlayerdown" and not if_phys_address)): logger.debug(
            f"Pomijanie portu '{if_name}' ({if_descr}) na {device_hostname_for_log} (status: '{if_oper_status}', brak MAC)."); continue
        has_mac = bool(
            if_phys_address and len(if_phys_address.replace(':', '').replace('-', '').replace('.', '')) >= 12)
        if_type_iana = (
            str(if_type_raw['iana']).lower() if isinstance(if_type_raw, dict) and 'iana' in if_type_raw else str(
                if_type_raw).lower() if isinstance(if_type_raw, str) else '')
        is_physical = False
        if if_type_iana in physical_types_iana:
            is_physical = True
        elif if_type_iana in logical_types_iana:
            is_physical = False
        elif stack_port_pattern.match(if_name) or stack_port_pattern.match(if_descr):
            is_physical = True
        elif physical_name_patterns.match(if_name) or physical_name_patterns.match(if_descr):
            is_physical = True
        elif logical_name_patterns.match(if_name) or logical_name_patterns.match(if_descr):
            is_physical = False
        elif has_mac:
            is_physical = True
        else:
            is_physical = False
        if if_type_iana == 'ieee8023adlag' or any(
            k in if_name.lower() for k in ['port-ch', 'bundle-eth', 'lag', 'bond']) or any(
            k in if_descr.lower() for k in ['port-ch', 'bundle-eth', 'lag', 'bond']): is_physical = has_mac
        if port_info == mgmt0_port_info and mgmt0_port_info in physical_ports: continue  # Unikaj duplikatu mgmt0
        if is_physical:
            if port_info not in physical_ports: physical_ports.append(port_info)
        else:
            port_info['_ifType_iana_debug'] = if_type_iana
            if port_info not in logical_interfaces: logical_interfaces.append(port_info)
    logger.info(
        f"Klasyfikacja portów dla '{device_hostname_for_log}': {len(physical_ports)} fizycznych (w tym mgmt0), {len(logical_interfaces)} logicznych/innych.")
    return physical_ports, logical_interfaces, mgmt0_port_info


def calculate_device_chassis_layout(num_total_physical_ports_for_layout: int,
                                    num_ports_actually_displaying: int) -> DynamicLayoutInfo:
    if num_ports_actually_displaying <= 0:
        return DynamicLayoutInfo(MIN_CHASSIS_WIDTH, DEFAULT_CHASSIS_HEIGHT_NO_PORTS, 0, 0)

    ports_per_row_config = DEFAULT_PORTS_PER_ROW_LARGE_DEVICE if num_ports_actually_displaying > MAX_PHYSICAL_PORTS_FOR_CHASSIS_DISPLAY / 1.5 else DEFAULT_PORTS_PER_ROW_NORMAL

    num_rows = max(1, math.ceil(num_ports_actually_displaying / ports_per_row_config))

    actual_ports_in_widest_row = 0
    if num_rows > 0:
        ports_left = num_ports_actually_displaying
        max_in_row_calc = 0  # Zmieniono nazwę, żeby nie kolidować z pętlą
        for _ in range(num_rows):  # Pętla po obliczonej liczbie rzędów
            current_row_count = min(ports_left, ports_per_row_config)
            if current_row_count > max_in_row_calc:
                max_in_row_calc = current_row_count
            ports_left -= current_row_count
            if ports_left <= 0:  # Poprawna składnia warunku if
                break
                # Ustalenie szerokości najszerszego rzędu
        if num_rows == 1:
            actual_ports_in_widest_row = num_ports_actually_displaying
        else:  # Jeśli więcej niż 1 rząd, najszerszy rząd to ports_per_row_config lub mniej (ostatni rząd)
            # max_in_row_calc powinien dać poprawną wartość
            actual_ports_in_widest_row = max_in_row_calc if max_in_row_calc > 0 else ports_per_row_config

    else:  # num_rows == 0 (nie powinno się zdarzyć, jeśli num_ports_actually_displaying > 0)
        actual_ports_in_widest_row = 0

    chassis_content_width = actual_ports_in_widest_row * PORT_WIDTH + \
                            max(0, actual_ports_in_widest_row - 1) * HORIZONTAL_SPACING
    chassis_width = max(MIN_CHASSIS_WIDTH, chassis_content_width + 2 * CHASSIS_PADDING_X)

    chassis_content_height = num_rows * PORT_HEIGHT + \
                             max(0, num_rows - 1) * VERTICAL_SPACING
    chassis_height = max(MIN_CHASSIS_HEIGHT, chassis_content_height + ROW_OFFSET_Y + CHASSIS_PADDING_Y)

    logger.debug(
        f"Layout chassis: {num_ports_actually_displaying} portów (z {num_total_physical_ports_for_layout} kandydatów). "
        f"Rzędy:{num_rows}, Porty/rząd (konfig):{ports_per_row_config}, Najszerszy rząd (obliczony):{actual_ports_in_widest_row}. "
        f"Wymiary chassis: {chassis_width:.0f}x{chassis_height:.0f}")

    return DynamicLayoutInfo(chassis_width, chassis_height, num_rows, ports_per_row_config)


def prepare_device_display_data(dev_api_info: Dict[str, Any], api: LibreNMSAPI, dev_idx: int) -> DeviceDisplayData:
    canon_id = get_canonical_identifier(dev_api_info) or f"Urządzenie_idx_{dev_idx}"
    logger.debug(f"Przygotowywanie danych wyświetlania dla: {canon_id} (ID API: {dev_api_info.get('device_id')})")
    ports_data = []
    if dev_api_info.get("device_id"):
        try:
            ports_data = api.get_ports(str(dev_api_info["device_id"]),
                                       columns="port_id,ifIndex,ifName,ifDescr,ifType,ifPhysAddress,ifOperStatus,ifAdminStatus,ifAlias") or []
        except Exception as e:
            logger.error(f"Błąd pobierania portów dla {canon_id}: {e}", exc_info=True)

    all_phys_classified, logical_ifs_classified, mgmt0_info = classify_ports(ports_data, canon_id)

    phys_candidates = []
    for p in all_phys_classified:
        if p == mgmt0_info: continue
        adm_down = str(p.get('ifAdminStatus', 'up')).lower() == 'down'
        oper_down = str(p.get('ifOperStatus', 'unknown')).lower() in ['down', 'lowerlayerdown', 'notpresent']
        has_alias = bool(p.get('ifAlias', '').strip())
        if adm_down and oper_down and not has_alias:
            logger.debug(f"Port '{p.get('ifName')}' na '{canon_id}' pominięty w layoucie (admin&oper down, no_alias).")
            continue
        phys_candidates.append(p)

    try:
        phys_candidates.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
    except Exception:
        logger.warning(f"Błąd natsort dla portów fizycznych '{canon_id}'. Używam standardowego sortowania.")
        phys_candidates.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))

    total_phys_before_limit = len(phys_candidates)
    limited_flag = False
    phys_for_layout: List[Dict[str, Any]]

    if total_phys_before_limit > MAX_PHYSICAL_PORTS_FOR_CHASSIS_DISPLAY:
        logger.info(
            f"Dla '{canon_id}' liczba portów ({total_phys_before_limit}) przekracza limit ({MAX_PHYSICAL_PORTS_FOR_CHASSIS_DISPLAY}). Ograniczam.")
        phys_for_layout = phys_candidates[:MAX_PHYSICAL_PORTS_FOR_CHASSIS_DISPLAY]
        limited_flag = True
        logger.info(f"Wyświetlonych zostanie {len(phys_for_layout)} portów dla '{canon_id}'.")
    else:
        phys_for_layout = phys_candidates

    try:
        all_phys_classified.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
        logical_ifs_classified.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
    except Exception:
        logger.warning(f"Błąd natsort dla pełnych list portów '{canon_id}'. Używam standardowego sortowania.")
        all_phys_classified.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))
        logical_ifs_classified.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))

    layout_info = calculate_device_chassis_layout(total_phys_before_limit, len(phys_for_layout))
    is_stack_dev = len(all_phys_classified) > STACK_DETECTION_THRESHOLD  # Bazuj na wszystkich fizycznych (z mgmt0)

    return DeviceDisplayData(
        device_api_info=dev_api_info,
        canonical_identifier=canon_id,
        all_physical_ports=all_phys_classified,
        physical_ports_for_chassis_layout=phys_for_layout,
        logical_interfaces=logical_ifs_classified,
        mgmt0_port_info=mgmt0_info,
        total_physical_ports_before_limit=total_phys_before_limit,
        chassis_layout=layout_info,
        is_stack=is_stack_dev,
        ports_display_limited=limited_flag
    )


def get_device_render_size_from_prepared_data(prep_data: DeviceDisplayData) -> Tuple[float, float]:
    return prep_data.chassis_layout.width, prep_data.chassis_layout.height