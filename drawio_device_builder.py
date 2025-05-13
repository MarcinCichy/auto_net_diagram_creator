# --- drawio_device_builder.py ---
import sys
import os  # *** DODANO BRAKUJĄCY IMPORT OS ***

print("--- sys.path na początku drawio_device_builder.py ---")
for p_path in sys.path:
    print(p_path)
print("--- Koniec sys.path w drawio_device_builder.py ---")

print("\n--- Diagnostyka importu 'xml' WEWNĄTRZ drawio_device_builder.py (Próba 1) ---")
xml_module_in_builder_p1 = None
et_module_in_builder_p1 = None
try:
    import xml

    xml_module_in_builder_p1 = xml
    print(f"Pomyślnie zaimportowano 'xml' (Próba 1).")
    if hasattr(xml_module_in_builder_p1, '__file__'):
        xml_file_path_p1 = xml_module_in_builder_p1.__file__
        print(f"Lokalizacja zaimportowanego modułu 'xml' (Próba 1): {xml_file_path_p1}")

        standard_lib_path_fragment = os.path.join(sys.prefix, "Lib", "xml")
        normalized_xml_file_path = os.path.normpath(xml_file_path_p1)
        normalized_standard_lib_fragment = os.path.normpath(standard_lib_path_fragment)

        if normalized_xml_file_path.lower().startswith(normalized_standard_lib_fragment.lower()):
            print("Moduł 'xml' (Próba 1) wydaje się być poprawnie zaimportowany ze standardowej biblioteki.")
        else:
            print(
                f"!!! OSTRZEŻENIE (Próba 1): Moduł 'xml' zaimportowany z '{xml_file_path_p1}' NIE wydaje się pochodzić ze standardowej biblioteki ({standard_lib_path_fragment})!")
    else:
        print("Zaimportowany moduł 'xml' (Próba 1) nie ma atrybutu '__file__'.")

    print("\nPróba importu 'xml.etree.ElementTree' (Próba 1)...")
    import xml.etree.ElementTree as ET_DIAG_P1

    et_module_in_builder_p1 = ET_DIAG_P1
    print("Pomyślnie zaimportowano 'xml.etree.ElementTree' jako ET_DIAG_P1 (Próba 1).")

except Exception as e_diag_p1:
    print(f"!!! BŁĄD podczas diagnostyki (Próba 1): {e_diag_p1}")
    import traceback

    traceback.print_exc()
print("--- Koniec diagnostyki (Próba 1) ---\n")

print("--- Diagnostyka tuż PRZED właściwym importem 'xml.etree.ElementTree as ET' ---")
xml_module_before_actual_import = None
try:
    import xml

    xml_module_before_actual_import = xml
    print(f"Moduł 'xml' dostępny przed właściwym importem ET.")
    if hasattr(xml_module_before_actual_import, '__file__'):
        print(f"Lokalizacja 'xml' przed importem ET: {xml_module_before_actual_import.__file__}")
    if hasattr(xml_module_before_actual_import, 'etree'):
        print(f"Atrybut 'xml.etree' istnieje. Typ: {type(xml_module_before_actual_import.etree)}")
        if hasattr(xml_module_before_actual_import.etree, 'ElementTree'):
            print(f"Atrybut 'xml.etree.ElementTree' istnieje.")
        else:
            print(f"!!! Brak 'ElementTree' w 'xml.etree'. dir(xml.etree): {dir(xml_module_before_actual_import.etree)}")
    else:
        print(f"!!! Brak atrybutu 'etree' w module 'xml'. dir(xml): {dir(xml_module_before_actual_import)}")
except Exception as e_check_xml:
    print(f"Błąd podczas sprawdzania modułu 'xml' przed właściwym importem ET: {e_check_xml}")

print("Próba wykonania właściwego importu: import xml.etree.ElementTree as ET")
try:
    import xml.etree.ElementTree as ET

    print("WŁAŚCIWY IMPORT 'xml.etree.ElementTree as ET' ZAKOŃCZONY SUKCESEM.")
except ModuleNotFoundError as mnfe_actual:
    print(f"!!! ModuleNotFoundError przy WŁAŚCIWYM IMPORCIE 'xml.etree.ElementTree as ET': {mnfe_actual}")
    print(f"    Nazwa modułu z błędu: {mnfe_actual.name}")
    if mnfe_actual.name == 'xml.et':
        print("    Błąd dotyczy 'xml.et'. Sprawdzanie zawartości załadowanego 'xml.etree'...")
        if xml_module_before_actual_import and hasattr(xml_module_before_actual_import, 'etree'):
            try:
                from xml import etree as etree_check

                print(f"    dir(xml.etree): {dir(etree_check)}")
                if not hasattr(etree_check, 'ElementTree'):
                    print("    !!! Potwierdzono brak 'ElementTree' w 'xml.etree' tuż przed błędem.")
            except Exception as e_inspect:
                print(f"    Błąd podczas inspekcji xml.etree: {e_inspect}")
        else:
            print("    Nie udało się załadować 'xml' lub 'xml.etree' do dalszej inspekcji.")
    import traceback

    traceback.print_exc()
except ImportError as ie_actual:
    print(f"!!! ImportError przy WŁAŚCIWYM IMPORCIE 'xml.etree.ElementTree as ET': {ie_actual}")
    import traceback

    traceback.print_exc()
except Exception as e_actual:
    print(f"!!! Inny błąd przy WŁAŚCIWYM IMPORCIE 'xml.etree.ElementTree as ET': {e_actual}")
    import traceback

    traceback.print_exc()

print("--- Koniec diagnostyki PRZED i W TRAKCIE właściwego importu ET ---\n")

if 'ET' not in locals() and 'ET' not in globals():
    print(
        "!!! OSTRZEŻENIE GŁÓWNE: Moduł 'ET' (xml.etree.ElementTree) nie został pomyślnie zaimportowany. Dalsze działanie drawio_device_builder będzie niepoprawne.")
    ET = None

# --- Oryginalne importy kontynuują poniżej ---
# import os # Już zaimportowane na górze dla diagnostyki
import re
import math
import logging
from typing import List, Dict, Tuple, Optional, Any, NamedTuple

logger = logging.getLogger(__name__)

try:
    import natsort

    natsort_keygen = natsort.natsort_keygen()
    logger.debug("Moduł 'natsort' zaimportowany pomyślnie.")
except ImportError:
    logger.warning(
        "Moduł 'natsort' nie znaleziony. Sortowanie nazw portów będzie standardowe. Zainstaluj: pip install natsort")


    def natsort_keygen():
        return lambda x: str(x)

from librenms_client import LibreNMSAPI
import drawio_utils
import copy

# ... (reszta pliku drawio_device_builder.py bez zmian) ...
# --- Stałe Definiujące Wygląd i Układ ---
DEFAULT_PORTS_PER_ROW = 26
PORT_WIDTH = 20.0
PORT_HEIGHT = 20.0
HORIZONTAL_SPACING = 10.0
VERTICAL_SPACING = 15.0
ROW_OFFSET_Y = 7.0
CHASSIS_PADDING_X = 15.0
CHASSIS_PADDING_Y = 7.0
MIN_CHASSIS_WIDTH = 100.0
MIN_CHASSIS_HEIGHT = 60.0
DEFAULT_CHASSIS_HEIGHT_NO_PORTS = 40.0
STACK_DETECTION_THRESHOLD = DEFAULT_PORTS_PER_ROW * 2 + 4
LINE_LENGTH = 25.0
LABEL_OFFSET_X = 5.0
MARGIN_BETWEEN_LINE_AND_LABEL = 15.0
LABEL_LINE_HEIGHT = 10.0
LABEL_PADDING = 4.0
WAYPOINT_OFFSET = 20
INFO_LABEL_X_OFFSET = -150
INFO_LABEL_MIN_WIDTH = 200
LOGICAL_IF_LIST_MAX_HEIGHT = 150
PHYSICAL_PORT_LIST_MAX_HEIGHT = 200
STAGGER_HORIZONTAL_OFFSET_FACTOR = 0.75
STAGGER_VERTICAL_MARGIN_OFFSET = 10.0


class StyleInfo(NamedTuple):
    chassis: str = "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;"
    port: str = "rounded=0;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
    port_up: str = "#00FF00"
    port_down: str = "#FF0000"
    port_unknown: str = "#FFA500"
    aux_line: str = "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#AAAAAA;html=1;rounded=0;"
    label_rot: str = "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;overflow=visible;rotation=-90;fontSize=9;"
    label_hor: str = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;overflow=visible;fontSize=9;"
    info_label: str = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;fontSize=9;whiteSpace=wrap;overflow=visible;rounded=0;spacing=4;"


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


def _extract_styles_from_template(template_path: str) -> StyleInfo:
    if ET is None:
        logger.error(
            "Moduł ET (ElementTree) nie jest dostępny w _extract_styles_from_template. Używam domyślnych stylów.")
        return StyleInfo()
    tree = drawio_utils.load_drawio_template(template_path)
    if tree is None:
        logger.warning("Nie udało się wczytać szablonu, używanie domyślnych stylów.")
        return StyleInfo()
    root = tree.getroot().find(".//root")
    if root is None:
        logger.warning("Brak elementu <root> w szablonie, używanie domyślnych stylów.")
        return StyleInfo()
    chassis_style = None
    port_style = None
    for cell in root.findall("./mxCell[@vertex='1']"):
        style = cell.get("style", "")
        if "rounded=1" in style:
            geom = cell.find("./mxGeometry")
            if geom is not None and float(geom.get("width", 0)) > 50:
                chassis_style = style
                logger.debug(f"Znaleziono styl obudowy: {style}")
                break
    port_cell = drawio_utils.find_cell_by_id(root, 'wdXZIc1yJ1iBE2bjXRa4-3')
    if port_cell is not None:
        port_style = port_cell.get("style")
        if port_style:
            logger.debug(f"Znaleziono styl portu: {port_style}")
    return StyleInfo(
        chassis=chassis_style or StyleInfo.chassis,
        port=port_style or StyleInfo.port
    )


def _calculate_dynamic_layout(num_ports: int) -> DynamicLayoutInfo:
    if num_ports <= 0:
        return DynamicLayoutInfo(width=MIN_CHASSIS_WIDTH, height=DEFAULT_CHASSIS_HEIGHT_NO_PORTS, num_rows=0,
                                 ports_per_row=0)
    ports_per_actual_row = DEFAULT_PORTS_PER_ROW
    num_rows = max(1, math.ceil(num_ports / ports_per_actual_row))
    if num_rows == 1 and num_ports > 0:
        if num_ports > ports_per_actual_row / 2 and num_ports < ports_per_actual_row:
            if num_ports > 10:
                num_rows = 2
    if num_rows == 2:
        ports_in_row1 = math.ceil(num_ports / 2.0)
        ports_in_row2 = num_ports - ports_in_row1
        ports_in_widest_row = max(ports_in_row1, ports_in_row2)
        ports_per_actual_row = int(ports_in_widest_row)
    elif num_rows == 1:
        ports_in_widest_row = num_ports
        ports_per_actual_row = num_ports
    else:
        ports_in_widest_row = DEFAULT_PORTS_PER_ROW
    chassis_width = ports_in_widest_row * PORT_WIDTH + max(0,
                                                           ports_in_widest_row - 1) * HORIZONTAL_SPACING + 2 * CHASSIS_PADDING_X
    chassis_height = ROW_OFFSET_Y + num_rows * PORT_HEIGHT + max(0, num_rows - 1) * VERTICAL_SPACING + CHASSIS_PADDING_Y
    if num_ports == 0:
        chassis_height = DEFAULT_CHASSIS_HEIGHT_NO_PORTS
    else:
        min_h_eff = MIN_CHASSIS_HEIGHT if num_rows >= 2 else (MIN_CHASSIS_HEIGHT / 1.5)
        chassis_height = max(min_h_eff, chassis_height)
    chassis_width = max(MIN_CHASSIS_WIDTH, chassis_width)
    return DynamicLayoutInfo(width=chassis_width, height=chassis_height, num_rows=num_rows,
                             ports_per_row=ports_per_actual_row)


def _classify_ports(ports_data: List[Dict]) -> Tuple[List[Dict], List[Dict], Optional[Dict]]:
    physical_ports_from_api: List[Dict] = []
    logical_interfaces: List[Dict] = []
    mgmt0_api_port_info: Optional[Dict] = None
    physical_name_patterns = re.compile(
        r'^(Eth|Gi|Fa|Te|Hu|Twe|Fo|mgmt|Management|TenGig|FortyGig|HundredGig|Serial|Port\s?\d|SFP|XFP|QSFP|em\d|ens\d|eno\d|enp\d+s\d+|ge-|xe-|et-|eno|lan)',
        re.IGNORECASE)
    logical_name_patterns = re.compile(
        r'^(Vlan|vl|Loopback|Lo|lo\d*|Port-channel|Po|Bundle-Ether|ae|Tunnel|Tun|Null|Nu|Cpu|Fabric|Voice|Async|Group-Async|ipsec|gre|sit|pimreg|mgmt[1-9]|Irq|Service-Engine|Dialer|Virtual-Access|Virtual-Template)|.*\.\d+$',
        re.IGNORECASE)
    stack_port_pattern = re.compile(r'^[a-zA-Z]+[-]?\d+/\d+(/\d+)+$', re.IGNORECASE)
    physical_types_iana = {'ethernetcsmacd', 'fastether', 'gigabitethernet', 'fastetherfx', 'infinitiband', 'sonet',
                           'sdsl', 'hdsl', 'shdsl', 'adsl', 'radsl', 'vdsl', 'ieee80211', 'opticalchannel',
                           'fibrechannel', 'propvirtual'}
    logical_types_iana = {'l3ipvlan', 'softwareloopback', 'ieee8023adlag', 'l2vlan', 'tunnel', 'propMultiplexor',
                          'bridge', 'other'}
    temp_mgmt0_candidates = []
    other_ports = []
    for port_info in ports_data:
        if_name = port_info.get('ifName', '').lower()
        if_descr = port_info.get('ifDescr', '').lower()
        if if_name == 'mgmt0' or if_descr == 'mgmt0':
            temp_mgmt0_candidates.append(port_info)
        else:
            other_ports.append(port_info)
    if temp_mgmt0_candidates:
        mgmt0_api_port_info = temp_mgmt0_candidates[0]
        logger.debug(f"Znaleziono port mgmt0: {mgmt0_api_port_info.get('ifName')}")
        physical_ports_from_api.append(mgmt0_api_port_info)
    for port_info in other_ports:
        if_name = port_info.get('ifName', '')
        if_descr = port_info.get('ifDescr', '')
        if_type_raw = port_info.get('ifType')
        if_phys_address = port_info.get('ifPhysAddress')
        has_mac = bool(
            if_phys_address and len(str(if_phys_address).replace(':', '').replace('-', '').replace('.', '')) >= 12)
        if_type_iana = ''
        if isinstance(if_type_raw, dict):
            if_type_iana = if_type_raw.get('iana', '').lower()
        elif isinstance(if_type_raw, str):
            if_type_iana = if_type_raw.lower()
        name_looks_physical = bool(physical_name_patterns.match(if_name) or physical_name_patterns.match(if_descr))
        name_looks_logical = bool(logical_name_patterns.match(if_name) or logical_name_patterns.match(if_descr))
        name_looks_like_stack_port = bool(stack_port_pattern.match(if_name))
        is_physical = False
        if name_looks_logical:
            is_physical = False
        elif if_type_iana in logical_types_iana:
            is_physical = False
        elif name_looks_like_stack_port:
            is_physical = True
        elif if_type_iana in physical_types_iana:
            is_physical = True
        elif has_mac and not name_looks_logical:
            is_physical = True
        elif not has_mac and name_looks_physical and not name_looks_logical:
            is_physical = True
        else:
            is_physical = False
        if is_physical:
            if port_info not in physical_ports_from_api:
                physical_ports_from_api.append(port_info)
        else:
            port_info['_ifType_iana'] = if_type_iana
            logical_interfaces.append(port_info)
    logger.info(
        f"Sklasyfikowano: {len(physical_ports_from_api)} portów fizycznych, {len(logical_interfaces)} innych interfejsów.")
    return physical_ports_from_api, logical_interfaces, mgmt0_api_port_info


def calculate_dynamic_device_size(device_info: Dict, api_client: LibreNMSAPI) -> Tuple[float, float]:
    device_id = device_info.get("device_id")
    ports_data = []
    if device_id:
        try:
            ports_data_raw = api_client.get_ports(str(device_id),
                                                  columns="ifName,ifDescr,ifType,ifPhysAddress,ifOperStatus,ifAlias")
            ports_data = ports_data_raw if ports_data_raw is not None else []
        except Exception as e:
            logger.error(f"Wyjątek podczas pobierania portów dla obliczenia rozmiaru (ID: {device_id}): {e}")
            ports_data = []
    else:
        logger.warning(f"Brak device_id dla {device_info.get('hostname')}, nie można obliczyć dynamicznego rozmiaru.")
        return MIN_CHASSIS_WIDTH, DEFAULT_CHASSIS_HEIGHT_NO_PORTS
    physical_ports, _, mgmt0_info = _classify_ports(ports_data)
    num_physical_ports_for_layout = len([p for p in physical_ports if p != mgmt0_info])
    layout_info = _calculate_dynamic_layout(num_physical_ports_for_layout)
    return layout_info.width, layout_info.height


def add_device_to_diagram(
        global_root_cell: Any,
        device_info: dict, api_client: LibreNMSAPI,
        position: tuple[float, float], device_index: int, styles: StyleInfo
) -> Optional[Dict[Any, PortEndpointData]]:
    if ET is None:
        logger.error("Moduł ET (ElementTree) nie jest dostępny w add_device_to_diagram. Nie można dodać urządzenia.")
        return None

    port_map_for_device: Dict[Any, PortEndpointData] = {}
    offset_x, offset_y = position
    group_id_suffix = f"dev{device_index}"
    group_id = f"group_{group_id_suffix}"
    current_host_identifier = device_info.get('purpose') or device_info.get('hostname') or device_info.get('ip',
                                                                                                           f"ID:{device_info.get('device_id')}")
    logger.info(f"Dynamiczne dodawanie urządzenia {current_host_identifier}...")

    device_id = device_info.get("device_id")
    ports_data = []
    if device_id:
        try:
            ports_data_raw = api_client.get_ports(str(device_id))
            ports_data = ports_data_raw if ports_data_raw is not None else []
        except Exception as e:
            logger.error(f"Wyjątek podczas pobierania portów API dla ID: {device_id} ({current_host_identifier}): {e}")
            ports_data = []
    else:
        logger.warning(f"Brak device_id dla {current_host_identifier}. Rysuję minimalną obudowę.")

    all_physical_ports_from_api, logical_interfaces, mgmt0_api_port_info = _classify_ports(ports_data)
    physical_ports_to_draw = [p for p in all_physical_ports_from_api if p != mgmt0_api_port_info]
    try:
        physical_ports_to_draw.sort(key=lambda p: natsort_keygen(p.get('ifName', '')))
    except Exception as e:
        logger.warning(f"Błąd sortowania portów fizycznych: {e}. Używam standardowego sortowania.")
        physical_ports_to_draw.sort(key=lambda p: str(p.get('ifName', '')))

    num_physical_ports_for_layout = len(physical_ports_to_draw)
    layout_info = _calculate_dynamic_layout(num_physical_ports_for_layout)
    chassis_width = layout_info.width
    chassis_height = layout_info.height
    num_rows = layout_info.num_rows

    logger.info(
        f"Obliczone wymiary dla {current_host_identifier}: {chassis_width}x{chassis_height}, porty do rys.: {num_physical_ports_for_layout}, rzędy: {num_rows}")

    group_cell = drawio_utils.create_group_cell(group_id, "1", offset_x, offset_y, chassis_width, chassis_height)
    if global_root_cell is not None: global_root_cell.append(group_cell)

    chassis_id = f"chassis_{group_id_suffix}"
    chassis_cell = drawio_utils.create_label_cell(chassis_id, group_id, "", 0, 0, chassis_width, chassis_height,
                                                  styles.chassis)
    if global_root_cell is not None: global_root_cell.append(chassis_cell)

    processed_api_ports_indices = set()
    port_cells_generated = {}

    ports_in_rows_distribution = []
    if num_physical_ports_for_layout > 0:
        if num_rows == 1:
            ports_in_rows_distribution.append(num_physical_ports_for_layout)
        elif num_rows == 2:
            r1_count = math.ceil(num_physical_ports_for_layout / 2.0);
            ports_in_rows_distribution.append(int(r1_count));
            ports_in_rows_distribution.append(num_physical_ports_for_layout - int(r1_count))
        else:
            remaining_ports = num_physical_ports_for_layout
            for _ in range(num_rows):
                count = min(remaining_ports, DEFAULT_PORTS_PER_ROW);
                ports_in_rows_distribution.append(count);
                remaining_ports -= count
                if remaining_ports <= 0: break

    current_port_overall_index = 0
    for row_idx, num_ports_in_this_row in enumerate(ports_in_rows_distribution):
        for col_idx_in_row in range(num_ports_in_this_row):
            if current_port_overall_index >= len(physical_ports_to_draw): break
            api_port_info = physical_ports_to_draw[current_port_overall_index]
            visual_port_number = current_port_overall_index + 1
            port_x_rel = CHASSIS_PADDING_X + col_idx_in_row * (PORT_WIDTH + HORIZONTAL_SPACING)
            port_y_rel = ROW_OFFSET_Y + row_idx * (PORT_HEIGHT + VERTICAL_SPACING)
            port_ifindex = api_port_info.get("ifIndex")
            port_cell_id = f"dynport_{group_id_suffix}_{port_ifindex if port_ifindex is not None else current_port_overall_index}"
            port_style_str = styles.port
            status = api_port_info.get("ifOperStatus", "unknown").lower()
            fill_color = styles.port_up if status == "up" else (
                styles.port_down if status == "down" else styles.port_unknown)
            port_style_str = drawio_utils.set_style_value(port_style_str, "fillColor", fill_color)
            port_cell = drawio_utils.create_label_cell(port_cell_id, group_id, str(visual_port_number), port_x_rel,
                                                       port_y_rel, PORT_WIDTH, PORT_HEIGHT, port_style_str)
            if global_root_cell is not None: global_root_cell.append(port_cell)
            if port_ifindex is not None:
                processed_api_ports_indices.add(port_ifindex)
                port_cells_generated[port_ifindex] = port_cell_id
            center_x_rel = port_x_rel + PORT_WIDTH / 2
            endpoint_orientation: str;
            line_end_y_rel: float
            if row_idx % 2 == 0:
                line_end_y_rel = port_y_rel - LINE_LENGTH; endpoint_orientation = "up"
            else:
                line_end_y_rel = port_y_rel + PORT_HEIGHT + LINE_LENGTH; endpoint_orientation = "down"
            endpoint_abs_x = offset_x + center_x_rel;
            endpoint_abs_y = offset_y + line_end_y_rel
            dummy_endpoint_id = f"ep_{port_cell_id}"
            dummy_style = "shape=none;fillColor=none;strokeColor=none;resizable=0;movable=0;editable=0;portConstraint=none;noLabel=1;"
            dummy_vertex_cell = drawio_utils.create_label_cell(dummy_endpoint_id, "1", "", endpoint_abs_x - 0.5,
                                                               endpoint_abs_y - 0.5, 1, 1, dummy_style)
            if global_root_cell is not None: global_root_cell.append(dummy_vertex_cell)
            endpoint_data = PortEndpointData(cell_id=dummy_endpoint_id, x=endpoint_abs_x, y=endpoint_abs_y,
                                             orientation=endpoint_orientation)
            port_name_api = api_port_info.get('ifName')
            if port_ifindex is not None: port_map_for_device[f"ifindex_{port_ifindex}"] = endpoint_data
            if port_name_api: port_map_for_device[port_name_api] = endpoint_data
            port_descr_api = api_port_info.get('ifDescr')
            if port_descr_api and port_descr_api != port_name_api: port_map_for_device[port_descr_api] = endpoint_data
            port_alias_api = api_port_info.get('ifAlias')
            if port_alias_api: port_map_for_device[port_alias_api] = endpoint_data
            port_map_for_device[str(visual_port_number)] = endpoint_data
            alias_text = api_port_info.get("ifAlias", "")
            if alias_text:
                label_id = f"label_{port_cell_id}";
                target_id_for_aux_line = label_id;
                label_style_str = styles.label_rot
                lines = alias_text.split('\n');
                num_lines = len(lines);
                max_line_len = max(len(line) for line in lines) if lines else 0
                label_cell_unrotated_width = num_lines * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
                label_cell_unrotated_height = max(30, max_line_len * (LABEL_LINE_HEIGHT * 0.8)) + 2 * LABEL_PADDING
                effective_label_x_offset_from_endpoint = LABEL_OFFSET_X;
                effective_margin_between_line_and_label = MARGIN_BETWEEN_LINE_AND_LABEL
                is_staggered = (col_idx_in_row % 2 != 0)
                if is_staggered:
                    effective_label_x_offset_from_endpoint += (
                                                                          PORT_WIDTH + HORIZONTAL_SPACING) * STAGGER_HORIZONTAL_OFFSET_FACTOR
                    effective_margin_between_line_and_label += STAGGER_VERTICAL_MARGIN_OFFSET
                label_final_x_pos = endpoint_abs_x + effective_label_x_offset_from_endpoint
                if endpoint_orientation == "up":
                    label_final_y_pos = endpoint_abs_y - effective_margin_between_line_and_label - label_cell_unrotated_height
                else:
                    label_final_y_pos = endpoint_abs_y + effective_margin_between_line_and_label
                label_cell = drawio_utils.create_label_cell(label_id, "1", alias_text, label_final_x_pos,
                                                            label_final_y_pos, label_cell_unrotated_width,
                                                            label_cell_unrotated_height, label_style_str)
                if global_root_cell is not None: global_root_cell.append(label_cell)
            else:
                target_id_for_aux_line = dummy_endpoint_id
            edge_id = f"edge_aux_{port_cell_id}"
            edge_cell = drawio_utils.create_edge_cell(edge_id, group_id, port_cell_id, target_id_for_aux_line,
                                                      styles.aux_line)
            edge_geom = edge_cell.find("./mxGeometry")
            if edge_geom is not None and ET is not None: ET.SubElement(edge_geom, "mxPoint",
                                                                       {"as": "targetPoint", "x": str(endpoint_abs_x),
                                                                        "y": str(endpoint_abs_y)})
            if global_root_cell is not None: global_root_cell.append(edge_cell)
            current_port_overall_index += 1
        if current_port_overall_index >= len(physical_ports_to_draw): break

    if mgmt0_api_port_info:
        logger.info("Dodawanie portu mgmt0...")
        mgmt0_ifindex = mgmt0_api_port_info.get('ifIndex');
        mgmt0_port_id = f"dynport_{group_id_suffix}_mgmt0"
        mgmt0_x_rel = chassis_width + HORIZONTAL_SPACING;
        mgmt0_y_rel = chassis_height / 2 - PORT_HEIGHT / 2
        mgmt0_style_str = styles.port;
        status_mgmt0 = mgmt0_api_port_info.get("ifOperStatus", "unknown").lower()
        fill_color_mgmt0 = styles.port_up if status_mgmt0 == "up" else (
            styles.port_down if status_mgmt0 == "down" else styles.port_unknown)
        mgmt0_style_str = drawio_utils.set_style_value(mgmt0_style_str, "fillColor", fill_color_mgmt0)
        mgmt0_cell = drawio_utils.create_label_cell(mgmt0_port_id, group_id, "M", mgmt0_x_rel, mgmt0_y_rel, PORT_WIDTH,
                                                    PORT_HEIGHT, mgmt0_style_str)
        if global_root_cell is not None: global_root_cell.append(mgmt0_cell)
        if mgmt0_ifindex is not None: processed_api_ports_indices.add(mgmt0_ifindex); port_cells_generated[
            mgmt0_ifindex] = mgmt0_port_id
        endpoint_abs_x_mgmt = offset_x + mgmt0_x_rel + PORT_WIDTH + LINE_LENGTH;
        endpoint_abs_y_mgmt = offset_y + mgmt0_y_rel + PORT_HEIGHT / 2
        endpoint_orientation_mgmt = "right";
        dummy_endpoint_id_mgmt = f"ep_{mgmt0_port_id}"
        dummy_style_mgmt = "shape=none;fillColor=none;strokeColor=none;resizable=0;movable=0;editable=0;portConstraint=none;noLabel=1;"
        dummy_vertex_cell_mgmt = drawio_utils.create_label_cell(dummy_endpoint_id_mgmt, "1", "",
                                                                endpoint_abs_x_mgmt - 0.5, endpoint_abs_y_mgmt - 0.5, 1,
                                                                1, dummy_style_mgmt)
        if global_root_cell is not None: global_root_cell.append(dummy_vertex_cell_mgmt)
        endpoint_data_mgmt = PortEndpointData(cell_id=dummy_endpoint_id_mgmt, x=endpoint_abs_x_mgmt,
                                              y=endpoint_abs_y_mgmt, orientation=endpoint_orientation_mgmt)
        port_name_api_mgmt = mgmt0_api_port_info.get('ifName')
        if port_name_api_mgmt: port_map_for_device[port_name_api_mgmt] = endpoint_data_mgmt
        if mgmt0_ifindex is not None: port_map_for_device[f"ifindex_{mgmt0_ifindex}"] = endpoint_data_mgmt
        port_descr_api_mgmt = mgmt0_api_port_info.get('ifDescr')
        if port_descr_api_mgmt: port_map_for_device[port_descr_api_mgmt] = endpoint_data_mgmt
        port_map_for_device["mgmt0"] = endpoint_data_mgmt
        alias_text_mgmt = mgmt0_api_port_info.get("ifAlias", "");
        target_id_for_mgmt_aux = dummy_endpoint_id_mgmt
        if alias_text_mgmt:
            label_id_mgmt = f"label_{mgmt0_port_id}";
            target_id_for_mgmt_aux = label_id_mgmt;
            label_style_mgmt_str = styles.label_hor
            lines_mgmt = alias_text_mgmt.split('\n');
            max_line_len_mgmt = max(len(line) for line in lines_mgmt) if lines_mgmt else 0
            label_width_mgmt = max(50, max_line_len_mgmt * (LABEL_LINE_HEIGHT * 0.8)) + 2 * LABEL_PADDING
            label_height_mgmt = len(lines_mgmt) * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
            label_abs_x_pos_mgmt = endpoint_abs_x_mgmt + MARGIN_BETWEEN_LINE_AND_LABEL;
            label_abs_y_pos_mgmt = endpoint_abs_y_mgmt - label_height_mgmt / 2
            label_cell_mgmt = drawio_utils.create_label_cell(label_id_mgmt, "1", alias_text_mgmt, label_abs_x_pos_mgmt,
                                                             label_abs_y_pos_mgmt, label_width_mgmt, label_height_mgmt,
                                                             label_style_mgmt_str)
            if global_root_cell is not None: global_root_cell.append(label_cell_mgmt)
        edge_id_mgmt = f"edge_aux_{mgmt0_port_id}"
        edge_cell_mgmt = drawio_utils.create_edge_cell(edge_id_mgmt, group_id, mgmt0_port_id, target_id_for_mgmt_aux,
                                                       styles.aux_line)
        edge_geom_mgmt = edge_cell_mgmt.find("./mxGeometry")
        if edge_geom_mgmt is not None and ET is not None: ET.SubElement(edge_geom_mgmt, "mxPoint", {"as": "targetPoint",
                                                                                                    "x": str(
                                                                                                        endpoint_abs_x_mgmt),
                                                                                                    "y": str(
                                                                                                        endpoint_abs_y_mgmt)})
        if global_root_cell is not None: global_root_cell.append(edge_cell_mgmt)

    is_stack = len(all_physical_ports_from_api) > STACK_DETECTION_THRESHOLD
    dev_info_id = f"device_info_{group_id_suffix}"
    dev_id_val = device_info.get('device_id', 'N/A');
    hostname_raw = device_info.get('hostname', '');
    ip_raw = device_info.get('ip', '');
    purpose_raw = device_info.get('purpose', '')
    temp_display_ip = ip_raw if ip_raw else 'N/A';
    hostname_looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', str(hostname_raw)))
    if hostname_looks_like_ip and not ip_raw: temp_display_ip = hostname_raw
    display_name_main = "(Brak Nazwy)"
    if purpose_raw and purpose_raw.strip():
        display_name_main = purpose_raw.strip()
    elif hostname_raw and not hostname_looks_like_ip:
        display_name_main = hostname_raw
    elif ip_raw:
        display_name_main = ip_raw
    elif hostname_raw and hostname_looks_like_ip:
        display_name_main = hostname_raw
    elif dev_id_val != 'N/A':
        display_name_main = f"Urządzenie ID: {dev_id_val}"
    if is_stack: display_name_main += " <b>(STACK)</b>"
    display_extra_info = []
    if hostname_raw and hostname_raw != display_name_main.replace(" <b>(STACK)</b>",
                                                                  "") and not hostname_looks_like_ip: display_extra_info.append(
        f"Host: {hostname_raw}")
    if purpose_raw and purpose_raw.strip() and purpose_raw.strip() != display_name_main.replace(" <b>(STACK)</b>",
                                                                                                ""): display_extra_info.append(
        f"Cel: {purpose_raw.strip()}")
    base_device_label_html = f"<div style='text-align:left;'><b>{display_name_main}</b><br/>ID: {dev_id_val}"
    if display_extra_info: base_device_label_html += "<br/>" + "<br/>".join(display_extra_info)
    base_device_label_html += f"<br/>IP: {temp_display_ip}</div>"
    physical_ports_html = f"<b>Porty Fizyczne ({len(all_physical_ports_from_api)}):</b><br/><div style='margin:0; padding-left:5px; max-height:{PHYSICAL_PORT_LIST_MAX_HEIGHT}px; overflow:auto;'>"
    if all_physical_ports_from_api:
        try:
            sorted_all_physical = sorted(all_physical_ports_from_api, key=lambda p: natsort_keygen(p.get('ifName', '')))
        except Exception as e:
            logger.warning(f"Błąd sortowania listy portów fizycznych w etykiecie: {e}"); sorted_all_physical = sorted(
                all_physical_ports_from_api, key=lambda p: str(p.get('ifName', '')))
        for phys_port in sorted_all_physical:
            name = phys_port.get('ifName', 'N/A');
            descr = phys_port.get('ifDescr', '');
            alias = phys_port.get('ifAlias', '');
            status = phys_port.get('ifOperStatus', 'unknown').lower();
            ifindex = phys_port.get('ifIndex')
            status_color = "green" if status == "up" else ("red" if status == "down" else "orange");
            extra_info = alias if alias else (descr if descr != name else '');
            extra_info_str = f" <i>({extra_info})</i>" if extra_info else ""
            mapped_marker = " [M]" if ifindex in processed_api_ports_indices else ""
            physical_ports_html += f"<font color='{status_color}'>•</font>&nbsp;{name}{extra_info_str}&nbsp;({status}){mapped_marker}<br/>"
    else:
        physical_ports_html += "(brak)<br/>"
    physical_ports_html += "</div>";
    logical_interface_list_html = f"<b>Inne Interfejsy ({len(logical_interfaces)}):</b><br/><div style='margin:0; padding-left:5px; max-height:{LOGICAL_IF_LIST_MAX_HEIGHT}px; overflow:auto;'>"
    if logical_interfaces:
        try:
            logical_interfaces.sort(key=lambda p: natsort_keygen(p.get('ifName', '')))
        except:
            logical_interfaces.sort(key=lambda p: str(p.get('ifName', '')))
        for logical_if in logical_interfaces:
            name = logical_if.get('ifName') or logical_if.get('ifDescr', 'N/A');
            status = logical_if.get('ifOperStatus', 'unknown').lower()
            status_color = "green" if status == "up" else ("red" if status == "down" else "orange");
            if_type_str = logical_if.get('_ifType_iana', '');
            type_info = f" ({if_type_str})" if if_type_str else ""
            logical_interface_list_html += f"<font color='{status_color}'>•</font>&nbsp;{name}{type_info}&nbsp;({status})<br/>"
    else:
        logical_interface_list_html += "(brak)<br/>"
    logical_interface_list_html += "</div>";
    full_device_label_html = f"{base_device_label_html}<hr size='1'/>{physical_ports_html}<hr size='1'/>{logical_interface_list_html}"
    info_width = max(chassis_width, INFO_LABEL_MIN_WIDTH)
    num_base_lines = 3 + len(display_extra_info);
    base_height = num_base_lines * (LABEL_LINE_HEIGHT + 2) + 10
    phys_port_section_height = min(PHYSICAL_PORT_LIST_MAX_HEIGHT,
                                   max(15, len(all_physical_ports_from_api) * (LABEL_LINE_HEIGHT + 1))) + 20
    logical_if_section_height = min(LOGICAL_IF_LIST_MAX_HEIGHT,
                                    max(15, len(logical_interfaces) * (LABEL_LINE_HEIGHT + 1))) + 20
    info_height = base_height + phys_port_section_height + logical_if_section_height + 15
    label_abs_x_pos_info = offset_x + INFO_LABEL_X_OFFSET;
    label_abs_y_pos_info = offset_y + ROW_OFFSET_Y;
    label_parent_id = "1"
    dev_info_cell = drawio_utils.create_label_cell(dev_info_id, label_parent_id, full_device_label_html,
                                                   label_abs_x_pos_info, label_abs_y_pos_info, info_width, info_height,
                                                   styles.info_label)
    if global_root_cell is not None: global_root_cell.append(dev_info_cell)

    logger.info(f"✓ Urządzenie {current_host_identifier} dynamicznie przetworzone.")
    return port_map_for_device


if not hasattr(drawio_utils, 'set_style_value'):
    def set_style_value(style_string: Optional[str], key: str, value: str) -> str:
        if style_string is None: style_string = ""
        style_string = style_string.strip()
        if style_string.endswith(';'): style_string = style_string[:-1]
        parts = style_string.split(';')
        new_parts = [];
        found = False;
        key_prefix = f"{key}="
        for part in parts:
            clean_part = part.strip()
            if not clean_part: continue
            if clean_part.startswith(key_prefix):
                new_parts.append(f"{key_prefix}{value}");
                found = True
            else:
                new_parts.append(clean_part)
        if not found: new_parts.append(f"{key_prefix}{value}")
        new_parts = [p for p in new_parts if p]
        result = ";".join(new_parts)
        if result: result += ';'
        return result


    drawio_utils.set_style_value = set_style_value

