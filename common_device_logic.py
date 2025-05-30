# common_device_logic.py
import logging
import re
import math
from typing import List, Dict, Tuple, Optional, Any, NamedTuple, Pattern, Set

from librenms_client import LibreNMSAPI
from utils import get_canonical_identifier # Zakładamy, że utils jest w zasięgu

logger = logging.getLogger(__name__)

try:
    import natsort
    natsort_keygen = natsort.natsort_keygen()
    logger.debug("Moduł 'natsort' zaimportowany pomyślnie dla common_device_logic.")
except ImportError:
    logger.warning("Moduł 'natsort' nie znaleziony. Sortowanie nazw portów będzie standardowe.")
    def natsort_keygen(): # type: ignore
        return lambda x: str(x)


class PortEndpointData(NamedTuple):
    cell_id: str
    x: float
    y: float
    orientation: str


class DynamicLayoutInfo(NamedTuple):
    width: float
    height: float
    num_rows: int
    ports_per_row: int


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


def _compile_regex_from_config(config: Dict[str, Any], key: str, default_pattern: str = ".*", flags: int = 0) -> Pattern[str]:
    pattern_str = config.get(key) # config_loader zapewni wartość domyślną, jeśli klucza nie ma w .ini
    try:
        if pattern_str and pattern_str.strip():
            return re.compile(pattern_str, flags)
    except re.error as e:
        logger.error(f"Błąd kompilacji regex z config dla klucza '{key}' (wzorzec: '{pattern_str}'): {e}. Używam domyślnego '{default_pattern}'.")
    # Jeśli pattern_str jest None (bo klucza nie było i default z config_loader to None) lub pusty, lub błąd kompilacji
    return re.compile(default_pattern, flags)


def classify_ports(
        ports_data_from_api: List[Dict[str, Any]],
        device_hostname_for_log: str = "Nieznane urządzenie",
        config: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:

    if config is None: config = {}

    physical_ports: List[Dict[str, Any]] = []
    logical_interfaces: List[Dict[str, Any]] = []
    mgmt0_port_info: Optional[Dict[str, Any]] = None

    # Pobieranie regexów i zbiorów z konfiguracji
    # _compile_regex_from_config użyje swojego default_pattern jeśli klucz z config da None lub pusty string
    physical_name_patterns = _compile_regex_from_config(config, 'physical_name_patterns_re', flags=re.IGNORECASE)
    stack_port_pattern = _compile_regex_from_config(config, 'stack_port_pattern_re', flags=re.IGNORECASE)
    logical_name_patterns = _compile_regex_from_config(config, 'logical_name_patterns_re', flags=re.IGNORECASE)

    # config.get() tutaj polega na tym, że config_loader dostarczył wartości domyślne (puste zbiory, jeśli nie ma w .ini)
    physical_types_iana: Set[str] = config.get('physical_types_iana_set')
    logical_types_iana: Set[str] = config.get('logical_types_iana_set')

    logger.debug(f"ClassifyPorts ({device_hostname_for_log}): Używane regexy: Phys='{physical_name_patterns.pattern}', Stack='{stack_port_pattern.pattern}', Logical='{logical_name_patterns.pattern}'")
    logger.debug(f"ClassifyPorts ({device_hostname_for_log}): Używane zbiory IANA: Phys (len:{len(physical_types_iana)}), Logical (len:{len(logical_types_iana)})")


    temp_mgmt0_candidates = []
    other_ports_to_classify = []

    for port in ports_data_from_api:
        if_name_lower = str(port.get('ifName', '')).lower()
        if_descr_lower = str(port.get('ifDescr', '')).lower()

        if 'mgmt0' == if_name_lower or 'management0' == if_name_lower or \
           'mgmt0' == if_descr_lower or 'management0' == if_descr_lower or \
           (if_name_lower.startswith("mgmt") and if_name_lower.endswith("0")) or \
           (if_descr_lower.startswith("mgmt") and if_descr_lower.endswith("0")):
            temp_mgmt0_candidates.append(port)
        else:
            other_ports_to_classify.append(port)

    if temp_mgmt0_candidates:
        mgmt0_with_mac = [p for p in temp_mgmt0_candidates if p.get('ifPhysAddress')];
        if mgmt0_with_mac:
            mgmt0_port_info = mgmt0_with_mac[0]
        else:
            mgmt0_port_info = temp_mgmt0_candidates[0]

        logger.debug(
            f"Port mgmt0 zidentyfikowany dla {device_hostname_for_log}: {mgmt0_port_info.get('ifName')} (ID: {mgmt0_port_info.get('port_id')})")
        if mgmt0_port_info not in physical_ports:
             physical_ports.append(mgmt0_port_info)


    for port_info in other_ports_to_classify:
        if_name, if_descr = str(port_info.get('ifName', '')), str(port_info.get('ifDescr', ''))
        if_type_raw = port_info.get('ifType')
        if_phys_address = str(port_info.get('ifPhysAddress', ''))
        if_oper_status = str(port_info.get('ifOperStatus', '')).lower()

        if port_info != mgmt0_port_info and \
           (if_oper_status == "notpresent" or (if_oper_status == "lowerlayerdown" and not if_phys_address)):
            logger.debug(
                f"Pomijanie portu '{if_name}' ({if_descr}) na {device_hostname_for_log} (status: '{if_oper_status}', brak MAC).")
            continue

        has_mac = bool(if_phys_address and len(if_phys_address.replace(':', '').replace('-', '').replace('.', '')) >= 12)

        if_type_iana = ''
        if isinstance(if_type_raw, dict) and 'iana' in if_type_raw:
            if_type_iana = str(if_type_raw['iana']).lower()
        elif isinstance(if_type_raw, str):
            if_type_iana = if_type_raw.lower()

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

        if if_type_iana == 'ieee8023adlag' or \
           any(k in if_name.lower() for k in ['port-ch', 'bundle-eth', 'lag', 'bond', 'ae']) or \
           any(k in if_descr.lower() for k in ['port-ch', 'bundle-eth', 'lag', 'bond', 'ae']):
            is_physical = has_mac
            if not has_mac:
                 logger.debug(f"Port LAG '{if_name}' ({if_descr}) na {device_hostname_for_log} bez MAC traktowany jako logiczny.")

        if port_info == mgmt0_port_info and mgmt0_port_info in physical_ports:
            continue

        if is_physical:
            if port_info not in physical_ports:
                physical_ports.append(port_info)
        else:
            port_info['_ifType_iana_debug'] = if_type_iana
            if port_info not in logical_interfaces:
                logical_interfaces.append(port_info)

    logger.info(
        f"Klasyfikacja portów dla '{device_hostname_for_log}': {len(physical_ports)} fizycznych (w tym mgmt0, jeśli znaleziono), {len(logical_interfaces)} logicznych/innych.")
    return physical_ports, logical_interfaces, mgmt0_port_info


def calculate_device_chassis_layout(
        num_total_physical_ports_for_layout: int,
        num_ports_actually_displaying: int,
        config: Dict[str, Any]
    ) -> DynamicLayoutInfo:

    # Pobieranie wartości z config; config_loader zapewni wartości domyślne
    min_chassis_width = config.get('min_chassis_width')
    default_chassis_height_no_ports = config.get('default_chassis_height_no_ports')

    if num_ports_actually_displaying <= 0:
        return DynamicLayoutInfo(min_chassis_width, default_chassis_height_no_ports, 0, 0)

    max_physical_ports_display_cfg = config.get('max_physical_ports_for_chassis_display')
    ports_per_row_large_cfg = config.get('default_ports_per_row_large_device')
    ports_per_row_normal_cfg = config.get('default_ports_per_row_normal')

    port_width_cfg = config.get('port_width')
    port_height_cfg = config.get('port_height')
    horizontal_spacing_cfg = config.get('port_horizontal_spacing')
    vertical_spacing_cfg = config.get('port_vertical_spacing')
    row_offset_y_cfg = config.get('port_row_offset_y')
    chassis_padding_x_cfg = config.get('chassis_padding_x')
    chassis_padding_y_cfg = config.get('chassis_padding_y')
    min_chassis_height_cfg = config.get('min_chassis_height')


    ports_per_row_config = ports_per_row_large_cfg \
        if num_ports_actually_displaying > max_physical_ports_display_cfg / 1.5 \
        else ports_per_row_normal_cfg

    num_rows = max(1, math.ceil(num_ports_actually_displaying / ports_per_row_config))

    actual_ports_in_widest_row = 0
    if num_rows > 0:
        ports_left = num_ports_actually_displaying
        max_in_row_calc = 0
        for _ in range(num_rows):
            current_row_count = min(ports_left, ports_per_row_config)
            if current_row_count > max_in_row_calc:
                max_in_row_calc = current_row_count
            ports_left -= current_row_count
            if ports_left <= 0:
                break
        if num_rows == 1:
            actual_ports_in_widest_row = num_ports_actually_displaying
        else:
            actual_ports_in_widest_row = min(num_ports_actually_displaying, ports_per_row_config)
    else:
        actual_ports_in_widest_row = 0

    chassis_content_width = actual_ports_in_widest_row * port_width_cfg + \
                            max(0, actual_ports_in_widest_row - 1) * horizontal_spacing_cfg
    chassis_width = max(min_chassis_width, chassis_content_width + 2 * chassis_padding_x_cfg)

    chassis_content_height = num_rows * port_height_cfg + \
                             max(0, num_rows - 1) * vertical_spacing_cfg
    chassis_height = max(min_chassis_height_cfg, chassis_content_height + row_offset_y_cfg + chassis_padding_y_cfg)

    logger.debug(
        f"Layout chassis: {num_ports_actually_displaying} portów (z {num_total_physical_ports_for_layout} kandydatów). "
        f"Rzędy:{num_rows}, Porty/rząd (konfig):{ports_per_row_config}, Najszerszy rząd (użyty do obliczeń):{actual_ports_in_widest_row}. "
        f"Wymiary chassis: {chassis_width:.0f}x{chassis_height:.0f}")

    return DynamicLayoutInfo(chassis_width, chassis_height, num_rows, ports_per_row_config)


def prepare_device_display_data(
        dev_api_info: Dict[str, Any],
        api: LibreNMSAPI,
        dev_idx: int,
        config: Dict[str, Any]
    ) -> DeviceDisplayData:

    canon_id = get_canonical_identifier(dev_api_info) or f"Urządzenie_idx_{dev_idx}"
    logger.debug(f"Przygotowywanie danych wyświetlania dla: {canon_id} (ID API: {dev_api_info.get('device_id')})")

    ports_data = []
    if dev_api_info.get("device_id"):
        try:
            ports_data = api.get_ports(
                str(dev_api_info["device_id"]),
                columns="port_id,ifIndex,ifName,ifDescr,ifType,ifPhysAddress,ifOperStatus,ifAdminStatus,ifAlias"
            ) or []
        except Exception as e:
            logger.error(f"Błąd pobierania portów dla {canon_id} (ID: {dev_api_info.get('device_id')}): {e}", exc_info=True)

    all_phys_classified, logical_ifs_classified, mgmt0_info = classify_ports(ports_data, canon_id, config)

    phys_candidates_for_layout = []
    for p in all_phys_classified:
        if p == mgmt0_info:
            continue
        adm_down = str(p.get('ifAdminStatus', 'up')).lower() == 'down'
        oper_down = str(p.get('ifOperStatus', 'unknown')).lower() in ['down', 'lowerlayerdown', 'notpresent']
        has_alias = bool(str(p.get('ifAlias', '')).strip())

        if adm_down and oper_down and not has_alias:
            logger.debug(f"Port '{p.get('ifName')}' ({p.get('ifDescr')}) na '{canon_id}' pominięty w layoucie chassis (admin&oper down, brak aliasu).")
            continue
        phys_candidates_for_layout.append(p)

    try:
        phys_candidates_for_layout.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
    except Exception:
        logger.warning(f"Błąd natsort dla portów fizycznych (layout) '{canon_id}'. Używam standardowego sortowania.")
        phys_candidates_for_layout.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))


    total_phys_before_limit = len(phys_candidates_for_layout)
    limited_flag = False
    phys_for_layout: List[Dict[str, Any]]

    max_ports_display_cfg = config.get('max_physical_ports_for_chassis_display')

    if total_phys_before_limit > max_ports_display_cfg:
        logger.info(
            f"Dla '{canon_id}' liczba portów fizycznych do layoutu ({total_phys_before_limit}) przekracza limit ({max_ports_display_cfg}). Ograniczam.")
        phys_for_layout = phys_candidates_for_layout[:max_ports_display_cfg]
        limited_flag = True
        logger.info(f"Wyświetlonych zostanie {len(phys_for_layout)} portów na chassis dla '{canon_id}'.")
    else:
        phys_for_layout = phys_candidates_for_layout

    try:
        all_phys_classified.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
        logical_ifs_classified.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
    except Exception:
        logger.warning(f"Błąd natsort dla pełnych list portów '{canon_id}'. Używam standardowego sortowania.")
        all_phys_classified.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))
        logical_ifs_classified.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))


    layout_info = calculate_device_chassis_layout(total_phys_before_limit, len(phys_for_layout), config)

    stack_threshold_factor = config.get('stack_detection_threshold_factor')
    stack_threshold_offset = config.get('stack_detection_threshold_offset')
    ports_per_row_large_cfg = config.get('default_ports_per_row_large_device')

    stack_detection_threshold = ports_per_row_large_cfg * stack_threshold_factor + stack_threshold_offset
    is_stack_dev = len(all_phys_classified) > stack_detection_threshold
    if is_stack_dev:
        logger.info(f"Urządzenie '{canon_id}' zidentyfikowane jako STACK (porty: {len(all_phys_classified)}, próg: {stack_detection_threshold}).")


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