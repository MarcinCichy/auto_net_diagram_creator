# drawio_device_builder.py
import xml.etree.ElementTree as ET
import re
import pprint
import math
from librenms_client import LibreNMSAPI
import drawio_utils
import copy

# Stałe (bez zmian)
LINE_LENGTH = 25
LABEL_OFFSET_X = 5
MARGIN_BETWEEN_LINE_AND_LABEL = 15
LABEL_LINE_HEIGHT = 10
LABEL_PADDING = 4
DEFAULT_PORT_WIDTH = 40
DEFAULT_PORT_HEIGHT = 40
VERTICAL_OFFSET_FROM_GROUP_TOP = 0
INFO_LABEL_X_OFFSET = -150
INFO_LABEL_MIN_WIDTH = 200
LOGICAL_IF_LIST_MAX_HEIGHT = 150


# Funkcja load_and_prepare_template (bez zmian)
def load_and_prepare_template(template_path, device_index):
    """Ładuje szablon, reasynuje ID, normalizuje pozycje."""
    print(f"  Przygotowanie szablonu '{template_path}' dla urządzenia {device_index}...")
    device_tree = drawio_utils.load_drawio_template(template_path)
    if device_tree is None: return None, 0, 0
    template_root = device_tree.getroot().find(".//root")
    if template_root is None:
        print(f"  ⚠ Nie znaleziono elementu <root> w szablonie: {template_path}")
        return None, 0, 0
    original_cells = list(template_root)
    temp_root_for_processing = ET.Element("root")
    for cell in original_cells:
         temp_root_for_processing.append(copy.deepcopy(cell))
    id_suffix = f"dev{device_index}"
    drawio_utils.reassign_cell_ids(temp_root_for_processing, id_suffix)
    bbox = drawio_utils.get_bounding_box(temp_root_for_processing)
    if bbox is None:
         print(f"  ⚠ Nie można obliczyć wymiarów szablonu (bbox is None): {template_path}")
         return None, 0, 0
    min_x, min_y, width, height = bbox
    if width <= 0 or height <= 0:
        print(f"  ⚠ Ostrzeżenie: Obliczone wymiary szablonu są nieprawidłowe ({width}x{height}).")
        width = INFO_LABEL_MIN_WIDTH
        height = 100
    drawio_utils.normalize_positions(temp_root_for_processing, min_x, min_y)
    print(f"  Szablon przygotowany, wymiary (po normalizacji): {width}x{height}")
    template_cells = list(temp_root_for_processing)
    return template_cells, width, height


# Funkcja add_device_to_diagram (poprawione rysowanie linii pomocniczych)
def add_device_to_diagram(global_root_cell: ET.Element,
                          template_cells: list[ET.Element],
                          template_width: float,
                          template_height: float,
                          device_info: dict,
                          api_client: LibreNMSAPI,
                          position: tuple[float, float],
                          device_index: int) -> dict:
    """
    Dodaje urządzenie do diagramu, klasyfikując interfejsy.
    Zwraca mapowanie portów fizycznych na ID punktów końcowych.
    *** POPRAWIONO RYSOANIE LINII POMOCNICZYCH ALIASÓW ***
    """
    port_map_for_device = {}
    if not template_cells or global_root_cell is None: return port_map_for_device

    offset_x, offset_y = position
    group_id_suffix = f"dev{device_index}"
    group_id = f"group_{group_id_suffix}"
    current_host_identifier = device_info.get('purpose') or device_info.get('hostname') or device_info.get('ip', f"ID:{device_info.get('device_id')}")
    print(f"  Dodawanie urządzenia {current_host_identifier}...")

    # === Sekcje 1-4 bez zmian ===
    # 1. Grupa
    group_cell = drawio_utils.create_group_cell(group_id, "1", offset_x, offset_y, template_width, template_height)
    global_root_cell.append(group_cell)
    # 2. Komórki szablonu
    cell_map = {}
    all_port_cells_in_template = []
    for cell_copy in template_cells:
        cell_id = cell_copy.get("id");
        if cell_id in ["0", "1"]: continue
        cell_copy.set("parent", group_id); global_root_cell.append(cell_copy)
        if cell_id: cell_map[cell_id] = cell_copy
        value = cell_copy.get("value", "").strip()
        if value.isdigit() or value.lower() == "mgmt0":
            all_port_cells_in_template.append(cell_copy)
    # 3. Porty API
    device_id = device_info.get("device_id"); ports_data = []
    if device_id: ports_data = api_client.get_ports(str(device_id))
    if ports_data is None: ports_data = []
    # 4. Klasyfikacja
    physical_ports_from_api = []; logical_interfaces = []
    physical_name_patterns = re.compile(r'^(Eth|Gi|Fa|Te|Hu|Twe|Fo|mgmt|Management|TenGig|FortyGig|HundredGig|Serial|Port\s?\d|SFP|XFP|QSFP|em\d|ens\d|eno\d|enp\d+s\d+)', re.IGNORECASE)
    logical_name_patterns = re.compile(r'^(Vlan|Loopback|Lo|Port-channel|Po|Tunnel|Tun|Null|Nu|Stack|Cpu|Fabric|Bundle-Ether|VoIP|Voice|Async|Group-Async|ipsec|gre|sit|pimreg)', re.IGNORECASE)
    physical_types_iana = {'ethernetcsmacd', 'fastether', 'gigabitethernet', 'fastetherfx', 'infinitiband', 'sonet', 'sdsl', 'hdsl', 'shdsl', 'adsl', 'radsl', 'vdsl', 'ieee80211'}
    logical_types_iana = {'l3ipvlan', 'softwareloopback', 'ieee8023adlag', 'l2vlan', 'propvirtual'}
    mgmt0_port_info = None
    for port_info in ports_data:
        if_name = port_info.get('ifName', '')
        if if_name.lower() == 'mgmt0': mgmt0_port_info = port_info; physical_ports_from_api.append(port_info); continue
        if_type_raw = port_info.get('ifType'); if_type_iana = ''
        if isinstance(if_type_raw, dict): if_type_iana = if_type_raw.get('iana', '').lower()
        elif isinstance(if_type_raw, str): if_type_iana = if_type_raw.lower()
        if_descr = port_info.get('ifDescr', '')
        is_physical = False
        if if_type_iana in physical_types_iana: is_physical = True
        elif if_type_iana in logical_types_iana: is_physical = False
        elif logical_name_patterns.match(if_name) or logical_name_patterns.match(if_descr): is_physical = False
        elif physical_name_patterns.match(if_name) or physical_name_patterns.match(if_descr): is_physical = True
        elif (not if_type_iana or if_type_iana == 'other') and not (logical_name_patterns.match(if_name) or logical_name_patterns.match(if_descr)): is_physical = True
        if is_physical: physical_ports_from_api.append(port_info)
        else: port_info['_ifType_iana'] = if_type_iana; logical_interfaces.append(port_info)
    print(f"  Sklasyfikowano: {len(physical_ports_from_api)} portów fizycznych, {len(logical_interfaces)} innych interfejsów.")

    # === Sekcja 5: Przetwarzanie portów fizycznych (ZMIANA: Przywrócenie logiki linii pomocniczych) ===
    numeric_port_cells = []
    mgmt0_cell_from_template = None
    for cell in all_port_cells_in_template:
        value = cell.get("value", "").strip()
        if value.isdigit(): numeric_port_cells.append(cell)
        elif value.lower() == "mgmt0": mgmt0_cell_from_template = cell
    try: numeric_port_cells.sort(key=lambda c: int(c.get("value", "0").strip()))
    except Exception as e: print(f"  ⚠ Błąd sortowania numerycznych komórek portów: {e}")
    print(f"  Znaleziono {len(numeric_port_cells)} numerycznych komórek portów i {'komórkę mgmt0' if mgmt0_cell_from_template else 'brak komórki mgmt0'} w szablonie.")

    processed_api_ports = set()
    num_physical_to_process = min(len(numeric_port_cells), len(physical_ports_from_api))
    for i in range(num_physical_to_process):
        api_port_info = None; api_port_index = -1
        for idx, p_info in enumerate(physical_ports_from_api):
            if idx not in processed_api_ports and p_info.get('ifName', '').lower() != 'mgmt0':
                api_port_info = p_info; api_port_index = idx; break
        if api_port_info is None: print(f"  Ostrzeżenie: Zabrakło portów API do mapowania do komórki #{i+1}"); break
        processed_api_ports.add(api_port_index)
        port_cell = numeric_port_cells[i]; port_cell_id = port_cell.get("id")
        if not port_cell_id: continue

        # Kolorowanie portu
        status = api_port_info.get("ifOperStatus", "unknown").lower(); color = "#00FF00" if status == "up" else ("#FF0000" if status == "down" else "#FFA500")
        drawio_utils.apply_style_change(port_cell, "fillColor", color)

        # Obliczanie pozycji linii pomocniczej i punktu końcowego
        port_geom = port_cell.find("./mxGeometry");
        if port_geom is None: continue
        try:
            px = float(port_geom.get("x", 0)) + float(port_geom.get("width", DEFAULT_PORT_WIDTH)) / 2
            py = float(port_geom.get("y", 0)); ph = float(port_geom.get("height", DEFAULT_PORT_HEIGHT))
            port_number = int(port_cell.get("value", "0").strip())
            if port_number % 2 != 0: line_start_y, line_end_y = py, py - LINE_LENGTH # Góra
            else: line_start_y, line_end_y = py + ph, py + ph + LINE_LENGTH # Dół
            endpoint_abs_x = offset_x + px
            endpoint_abs_y = offset_y + line_end_y
        except (ValueError, TypeError): continue

        # *** Utwórz niewidoczny punkt końcowy (nadal potrzebny do mapowania) ***
        dummy_endpoint_id = f"ep_{port_cell_id}"
        dummy_style = "shape=none;fillColor=none;strokeColor=none;resizable=0;movable=0;editable=0;"
        dummy_vertex_cell = drawio_utils.create_label_cell(dummy_endpoint_id, "1", "", endpoint_abs_x - 0.5, endpoint_abs_y - 0.5, 1, 1, dummy_style)
        global_root_cell.append(dummy_vertex_cell)
        # ********************************************************************

        # --- Zapisz mapowanie na ID PUNKTU KOŃCOWEGO ---
        port_name_api = api_port_info.get('ifName');
        if port_name_api: port_map_for_device[port_name_api] = dummy_endpoint_id
        port_ifindex_api = api_port_info.get('ifIndex');
        if port_ifindex_api is not None: port_map_for_device[f"ifindex_{port_ifindex_api}"] = dummy_endpoint_id
        port_value_template = port_cell.get("value", "").strip();
        if port_value_template.isdigit(): port_map_for_device[port_value_template] = dummy_endpoint_id
        port_descr_api = api_port_info.get('ifDescr');
        if port_descr_api: port_map_for_device[port_descr_api] = dummy_endpoint_id
        # -----------------------------------------------

        # *** ZMIANA: Rysowanie linii pomocniczej i etykiety aliasu ***
        edge_id = f"edge_{port_cell_id}"; edge_style = "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#AAAAAA;"
        label_text = api_port_info.get("ifAlias", "")
        target_id_for_aux_line = dummy_endpoint_id # Domyślnie cel to punkt końcowy

        if label_text:
            label_id = f"label_{port_cell_id}"
            label_style = ("text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;overflow=visible;rotation=-90;fontSize=9;")
            lines = label_text.split('\n'); num_lines = len(lines); max_line_len = max(len(line) for line in lines) if lines else 0
            label_width = num_lines * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING; label_height = max(30, max_line_len * (LABEL_LINE_HEIGHT * 0.8)) + 2 * LABEL_PADDING
            label_abs_x_pos = endpoint_abs_x + LABEL_OFFSET_X
            if port_number % 2 != 0: label_abs_y_pos = endpoint_abs_y - MARGIN_BETWEEN_LINE_AND_LABEL - label_height
            else: label_abs_y_pos = endpoint_abs_y + MARGIN_BETWEEN_LINE_AND_LABEL
            label_cell = drawio_utils.create_label_cell(label_id, "1", label_text, label_abs_x_pos, label_abs_y_pos, label_width, label_height, label_style)
            global_root_cell.append(label_cell);
            target_id_for_aux_line = label_id # Jeśli jest etykieta, linia pomocnicza wskazuje na nią

        # Rysuj linię pomocniczą od portu do etykiety (lub punktu, jeśli brak etykiety)
        # Używamy punktów absolutnych dla linii pomocniczej, aby uniknąć problemów z Draw.io
        abs_line_start_y = offset_y + line_start_y
        edge_cell = drawio_utils.create_edge_cell(edge_id, "1", "", "", edge_style)
        edge_geom = edge_cell.find("./mxGeometry")
        ET.SubElement(edge_geom, "mxPoint", {"as": "sourcePoint", "x": str(endpoint_abs_x), "y": str(abs_line_start_y)})
        ET.SubElement(edge_geom, "mxPoint", {"as": "targetPoint", "x": str(endpoint_abs_x), "y": str(endpoint_abs_y)})
        # Ustaw cel linii na etykietę lub punkt końcowy
        edge_cell.set("source", port_cell_id) # Źródłem jest port
        edge_cell.set("target", target_id_for_aux_line) # Celem jest etykieta lub punkt
        global_root_cell.append(edge_cell)
        # ***********************************************************

    # Przetwarzanie portu mgmt0 (ZMIANA: Przywrócenie logiki linii pomocniczej)
    if mgmt0_cell_from_template:
        mgmt0_cell_id = mgmt0_cell_from_template.get("id")
        mgmt0_api_info = None
        for port_info in physical_ports_from_api:
            if port_info.get('ifIndex') in processed_api_ports: continue
            if port_info.get('ifName', '').lower() == 'mgmt0' or port_info.get('ifDescr', '').lower() == 'mgmt0':
                mgmt0_api_info = port_info; break
        if mgmt0_api_info and mgmt0_cell_id:
            print(f"  Przetwarzanie portu mgmt0...")
            status = mgmt0_api_info.get("ifOperStatus", "unknown").lower(); color = "#00FF00" if status == "up" else ("#FF0000" if status == "down" else "#FFA500")
            drawio_utils.apply_style_change(mgmt0_cell_from_template, "fillColor", color)
            mgmt0_geom = mgmt0_cell_from_template.find("./mxGeometry")
            if mgmt0_geom:
                try:
                    px = float(mgmt0_geom.get("x", 0)) + float(mgmt0_geom.get("width", DEFAULT_PORT_WIDTH))
                    py = float(mgmt0_geom.get("y", 0)) + float(mgmt0_geom.get("height", DEFAULT_PORT_HEIGHT)) / 2
                    endpoint_abs_x = offset_x + px + LINE_LENGTH
                    endpoint_abs_y = offset_y + py
                except (ValueError, TypeError): endpoint_abs_x, endpoint_abs_y = offset_x + template_width + 50, offset_y + template_height/2

                # Utwórz niewidoczny punkt końcowy dla mgmt0
                dummy_endpoint_id_mgmt = f"ep_{mgmt0_cell_id}"
                dummy_style_mgmt = "shape=none;fillColor=none;strokeColor=none;resizable=0;movable=0;editable=0;"
                dummy_vertex_cell_mgmt = drawio_utils.create_label_cell(dummy_endpoint_id_mgmt, "1", "", endpoint_abs_x - 0.5, endpoint_abs_y - 0.5, 1, 1, dummy_style_mgmt)
                global_root_cell.append(dummy_vertex_cell_mgmt)

                # Zapisz mapowanie na ID PUNKTU KOŃCOWEGO
                port_name_api = mgmt0_api_info.get('ifName');
                if port_name_api: port_map_for_device[port_name_api] = dummy_endpoint_id_mgmt
                port_ifindex_api = mgmt0_api_info.get('ifIndex');
                if port_ifindex_api is not None: port_map_for_device[f"ifindex_{port_ifindex_api}"] = dummy_endpoint_id_mgmt
                port_descr_api = mgmt0_api_info.get('ifDescr');
                if port_descr_api: port_map_for_device[port_descr_api] = dummy_endpoint_id_mgmt
                port_map_for_device["mgmt0"] = dummy_endpoint_id_mgmt
                print(f"  ✓ Zmapowano port mgmt0 na punkt końcowy (cell ID: {dummy_endpoint_id_mgmt}).")

                # *** ZMIANA: Rysowanie linii pomocniczej i aliasu dla mgmt0 ***
                edge_id_mgmt = f"edge_{mgmt0_cell_id}"; edge_style_mgmt = "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#AAAAAA;"
                label_text = mgmt0_api_info.get("ifAlias", "")
                target_id_for_mgmt_aux = dummy_endpoint_id_mgmt # Domyślnie punkt

                if label_text:
                    label_id = f"label_{mgmt0_cell_id}"; label_style = ("text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;overflow=visible;fontSize=9;")
                    lines = label_text.split('\n'); max_line_len = max(len(line) for line in lines) if lines else 0
                    label_width = max(50, max_line_len * (LABEL_LINE_HEIGHT * 0.8)) + 2 * LABEL_PADDING; label_height = len(lines) * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
                    label_abs_x_pos = endpoint_abs_x + MARGIN_BETWEEN_LINE_AND_LABEL; label_abs_y_pos = endpoint_abs_y - label_height / 2
                    label_cell = drawio_utils.create_label_cell(label_id, "1", label_text, label_abs_x_pos, label_abs_y_pos, label_width, label_height, label_style)
                    global_root_cell.append(label_cell)
                    target_id_for_mgmt_aux = label_id # Cel to etykieta

                # Rysuj linię pomocniczą od portu mgmt0 do etykiety (lub punktu)
                abs_line_start_x = offset_x + px
                edge_cell_mgmt = drawio_utils.create_edge_cell(edge_id_mgmt, "1", "", "", edge_style_mgmt)
                edge_geom_mgmt = edge_cell_mgmt.find("./mxGeometry")
                ET.SubElement(edge_geom_mgmt, "mxPoint", {"as": "sourcePoint", "x": str(abs_line_start_x), "y": str(endpoint_abs_y)})
                ET.SubElement(edge_geom_mgmt, "mxPoint", {"as": "targetPoint", "x": str(endpoint_abs_x), "y": str(endpoint_abs_y)})
                edge_cell_mgmt.set("source", mgmt0_cell_id)
                edge_cell_mgmt.set("target", target_id_for_mgmt_aux)
                global_root_cell.append(edge_cell_mgmt)
                # ***********************************************************
            else: print("  ⚠ Brak geometrii dla komórki mgmt0 w szablonie.")
        else: print(f"  Ostrzeżenie: Znaleziono komórkę 'mgmt0' w szablonie, ale nie znaleziono portu w danych API.")

    # === SEKCJA 7: ETYKIETA INFORMACYJNA URZĄDZENIA (bez zmian) ===
    dev_info_id = f"device_info_{group_id_suffix}"
    dev_id_val = device_info.get('device_id', 'N/A'); hostname_raw = device_info.get('hostname', ''); ip_raw = device_info.get('ip', ''); purpose_raw = device_info.get('purpose', '')
    temp_display_ip = ip_raw if ip_raw else 'N/A'; hostname_looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', str(hostname_raw)))
    if hostname_looks_like_ip and not ip_raw: temp_display_ip = hostname_raw
    display_name_main = "(Brak Nazwy)"
    if purpose_raw and purpose_raw.strip(): display_name_main = purpose_raw.strip()
    elif hostname_raw and not hostname_looks_like_ip: display_name_main = hostname_raw
    elif ip_raw: display_name_main = ip_raw
    elif hostname_raw and hostname_looks_like_ip: display_name_main = hostname_raw
    display_extra_info = []
    if hostname_raw and hostname_raw != display_name_main and not hostname_looks_like_ip: display_extra_info.append(f"Host: {hostname_raw}")
    if purpose_raw and purpose_raw.strip() and purpose_raw.strip() != display_name_main: display_extra_info.append(f"Cel: {purpose_raw.strip()}")
    base_device_label_html = f"<div style='text-align:left;'><b>{display_name_main}</b><br/>ID: {dev_id_val}<br/>"
    if display_extra_info: base_device_label_html += "<br/>".join(display_extra_info) + "<br/>"
    base_device_label_html += f"IP: {temp_display_ip}</div>"
    logical_interface_list_html = f"<b>Inne Interfejsy ({len(logical_interfaces)}):</b><br/>"
    logical_interface_list_html += f"<div style='margin:0; padding-left:5px; max-height:{LOGICAL_IF_LIST_MAX_HEIGHT}px; overflow:auto;'>"
    if logical_interfaces:
        logical_interfaces.sort(key=lambda p: p.get('ifName', ''))
        for logical_if in logical_interfaces:
            name = logical_if.get('ifName') or logical_if.get('ifDescr', 'N/A'); status = logical_if.get('ifOperStatus', 'unknown').lower()
            status_color = "green" if status == "up" else ("red" if status == "down" else "orange"); if_type_str = logical_if.get('_ifType_iana', ''); type_info = f" ({if_type_str})" if if_type_str else ""
            logical_interface_list_html += f"<font color='{status_color}'>•</font>&nbsp;{name}{type_info}&nbsp;({status})<br/>"
    else: logical_interface_list_html += "(brak)<br/>"
    logical_interface_list_html += "</div>";
    full_device_label_html = base_device_label_html + "<hr size='1'/>" + logical_interface_list_html
    dev_info_style = ("text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;fontSize=9;whiteSpace=wrap;overflow=visible;rounded=0;spacing=4;")
    info_width = max(template_width, INFO_LABEL_MIN_WIDTH)
    num_base_lines = 3 + len(display_extra_info); base_height = num_base_lines * (LABEL_LINE_HEIGHT + 2) + 10
    logical_if_section_height = LOGICAL_IF_LIST_MAX_HEIGHT + 15
    actual_logical_if_height = max(15, len(logical_interfaces) * (LABEL_LINE_HEIGHT + 4))
    if len(logical_interfaces) > 0: logical_if_section_height = min(logical_if_section_height, actual_logical_if_height + 15)
    else: logical_if_section_height = 30
    info_height = base_height + logical_if_section_height + 5
    label_abs_x_pos = offset_x + INFO_LABEL_X_OFFSET; label_abs_y_pos = offset_y + VERTICAL_OFFSET_FROM_GROUP_TOP; label_parent_id = "1"
    dev_info_cell = drawio_utils.create_label_cell(dev_info_id, label_parent_id, full_device_label_html, label_abs_x_pos, label_abs_y_pos, info_width, info_height, dev_info_style)
    global_root_cell.append(dev_info_cell)

    print(f"  ✓ Urządzenie {current_host_identifier} przetworzone.")
    return port_map_for_device
