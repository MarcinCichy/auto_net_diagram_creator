# drawio_device_builder.py
import xml.etree.ElementTree as ET
import re
from librenms_client import LibreNMSAPI
import drawio_utils # Importujemy nasze utilsy
import copy # Potrzebne do deepcopy

# Stałe do rysowania etykiet portów
LINE_LENGTH = 25
LABEL_OFFSET_X = 5
MARGIN_BETWEEN_LINE_AND_LABEL = 15
LABEL_LINE_HEIGHT = 10
LABEL_PADDING = 4
DEFAULT_PORT_WIDTH = 40
DEFAULT_PORT_HEIGHT = 40


# Funkcja load_and_prepare_template bez zmian (wklejona dla kontekstu)
def load_and_prepare_template(template_path, device_index):
    """
    Ładuje szablon, reasynuje ID, normalizuje pozycje.
    Zwraca listę komórek szablonu, szerokość i wysokość, lub (None, 0, 0).
    """
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
        print(f"  ⚠ Ostrzeżenie: Obliczone wymiary szablonu są nieprawidłowe ({width}x{height}). Używam domyślnych 100x100.")
        width, height = 100, 100
    drawio_utils.normalize_positions(temp_root_for_processing, min_x, min_y)
    print(f"  Szablon przygotowany, wymiary (po normalizacji): {width}x{height}")
    template_cells = list(temp_root_for_processing)
    return template_cells, width, height


def add_device_to_diagram(global_root_cell: ET.Element,
                          template_cells: list[ET.Element],
                          template_width: float,
                          template_height: float,
                          device_info: dict,
                          api_client: LibreNMSAPI,
                          position: tuple[float, float],
                          device_index: int) -> dict: # *** ZMIANA: Dodano typ zwracany ***
    """
    Dodaje urządzenie do diagramu, klasyfikując interfejsy, pokazując
    fizyczne na szablonie, a inne w etykiecie.
    Zwraca mapowanie portów fizycznych: {nazwa_portu_API: cell_id_drawio}.
    """
    port_map_for_device = {} # *** NOWOŚĆ: Inicjalizacja mapy dla tego urządzenia ***

    if not template_cells or global_root_cell is None:
        print("  ⚠ Brak elementów szablonu lub globalnego roota do dodania urządzenia.")
        return port_map_for_device # Zwróć pustą mapę

    offset_x, offset_y = position
    group_id_suffix = f"dev{device_index}"
    group_id = f"group_{group_id_suffix}"
    current_host_identifier = device_info.get('hostname') or device_info.get('ip', f"ID:{device_info.get('device_id')}")

    print(f"  Dodawanie urządzenia {current_host_identifier} (ID grupy: {group_id}) na pozycji {position}...")

    # 1. Utwórz komórkę grupy
    group_cell = drawio_utils.create_group_cell(
        group_id, "1", offset_x, offset_y, template_width, template_height
    )
    global_root_cell.append(group_cell)

    # 2. Dodaj komórki szablonu do globalnego roota
    cell_map = {}
    for cell_copy in template_cells:
        cell_id = cell_copy.get("id")
        if cell_id in ["0", "1"]: continue
        cell_copy.set("parent", group_id)
        global_root_cell.append(cell_copy)
        if cell_id:
            cell_map[cell_id] = cell_copy

    # 3. Pobierz WSZYSTKIE porty/interfejsy z API
    device_id = device_info.get("device_id")
    ports_data = []
    if not device_id:
        print("  ⚠ Brak device_id, nie można pobrać portów z API.")
    else:
        print(f"  Pobieranie WSZYSTKICH portów/interfejsów dla device_id {device_id}...")
        ports_data = api_client.get_ports(str(device_id))
        if ports_data is None:
             print(f"  ⚠ Nie udało się pobrać portów dla device_id {device_id}.")
             ports_data = []
        else:
             print(f"  Pobrano {len(ports_data)} interfejsów z API.")

    # *** Klasyfikacja interfejsów ***
    physical_ports_from_api = []
    logical_interfaces = []
    physical_types_iana = {'ethernetcsmacd', 'propvirtual', 'fastether', 'gigabitethernet', 'fastetherfx', 'infinitiband', 'sonet', 'sdsl', 'hdsl', 'shdsl', 'adsl', 'radsl', 'vdsl'}
    physical_name_patterns = re.compile(r'^(Eth|Gi|Fa|Te|Hu|Twe|Fo|mgmt|Management|TenGig|FortyGig|HundredGig|Serial|Port\s?\d|SFP|XFP|QSFP)', re.IGNORECASE)
    logical_name_patterns = re.compile(r'^(Vlan|Loopback|Lo|Port-channel|Po|Tunnel|Tun|Null|Nu|Stack|Cpu|Fabric|Bundle-Ether|VoIP|Voice|Async|Group-Async)', re.IGNORECASE)

    for port_info in ports_data:
        if_type_raw = port_info.get('ifType')
        if_type_iana = ''
        if isinstance(if_type_raw, dict): if_type_iana = if_type_raw.get('iana', '').lower()
        elif isinstance(if_type_raw, str): if_type_iana = if_type_raw.lower()
        if_name = port_info.get('ifName', '')
        if_descr = port_info.get('ifDescr', '')
        is_physical = False
        if if_type_iana in physical_types_iana: is_physical = True
        elif not if_type_iana or if_type_iana == 'other':
             if physical_name_patterns.match(if_name) or physical_name_patterns.match(if_descr):
                 is_physical = True
        if logical_name_patterns.match(if_name) or logical_name_patterns.match(if_descr): is_physical = False
        elif if_type_iana in {'l3ipvlan', 'softwareloopback', 'ieee8023adlag', 'l2vlan'}: is_physical = False

        if is_physical:
            physical_ports_from_api.append(port_info)
        else:
            port_info['_ifType_iana'] = if_type_iana
            logical_interfaces.append(port_info)

    print(f"  Sklasyfikowano: {len(physical_ports_from_api)} portów fizycznych, {len(logical_interfaces)} innych interfejsów.")

    # 4. Znajdź komórki portów w szablonie
    port_cells_in_group = []
    for cell_id, cell in cell_map.items():
         if cell.get("parent") == group_id:
              value = cell.get("value", "").strip()
              if value.isdigit():
                   port_cells_in_group.append(cell)
    try:
        port_cells_in_group.sort(key=lambda c: int(c.get("value", "0").strip()))
    except Exception as e:
        print(f"  ⚠ Błąd sortowania komórek portów w szablonie: {e}")
    print(f"  Znaleziono {len(port_cells_in_group)} komórek portów w szablonie dla grupy {group_id}.")

    # 5. Przetwarzanie FIZYCZNYCH portów
    num_physical_to_process = min(len(port_cells_in_group), len(physical_ports_from_api))
    print(f"  Przetwarzanie {num_physical_to_process} fizycznych portów (wg szablonu)...")

    for i in range(num_physical_to_process):
        api_port_info = physical_ports_from_api[i]
        port_cell = port_cells_in_group[i]
        port_cell_id = port_cell.get("id")
        if not port_cell_id: continue

        # *** NOWOŚĆ: Zapisz mapowanie ***
        port_name_api = api_port_info.get('ifName')
        if port_name_api:
            port_map_for_device[port_name_api] = port_cell_id
        # Można dodać mapowanie po ifIndex jako fallback
        port_ifindex_api = api_port_info.get('ifIndex')
        if port_ifindex_api is not None:
             # Klucz w formacie "ifindex_X", aby uniknąć kolizji z nazwami
             port_map_for_device[f"ifindex_{port_ifindex_api}"] = port_cell_id

        # a) Kolorowanie
        status = api_port_info.get("ifOperStatus", "unknown").lower()
        color = "#00FF00" if status == "up" else ("#FF0000" if status == "down" else "#FFA500")
        drawio_utils.apply_style_change(port_cell, "fillColor", color)

        # b) Dodawanie linii i etykiety (ifAlias)
        port_geom = port_cell.find("./mxGeometry")
        if port_geom is None: continue
        try:
            px = float(port_geom.get("x", 0)) + float(port_geom.get("width", DEFAULT_PORT_WIDTH)) / 2
            py = float(port_geom.get("y", 0))
            ph = float(port_geom.get("height", DEFAULT_PORT_HEIGHT))
            port_number = int(port_cell.get("value", "0").strip())
        except (ValueError, TypeError): continue
        if port_number % 2 != 0: line_start_y, line_end_y = py, py - LINE_LENGTH
        else: line_start_y, line_end_y = py + ph, py + ph + LINE_LENGTH

        # c) Krawędź (linia)
        edge_id = f"edge_{port_cell_id}"
        edge_style = "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#AAAAAA;"
        edge_cell = drawio_utils.create_edge_cell(edge_id, "1", port_cell_id, "", edge_style)
        edge_geom = edge_cell.find("./mxGeometry")
        abs_px = offset_x + px
        abs_line_start_y = offset_y + line_start_y
        abs_line_end_y = offset_y + line_end_y
        ET.SubElement(edge_geom, "mxPoint", {"as": "sourcePoint", "x": str(abs_px), "y": str(abs_line_start_y)})
        ET.SubElement(edge_geom, "mxPoint", {"as": "targetPoint", "x": str(abs_px), "y": str(abs_line_end_y)})
        global_root_cell.append(edge_cell)

        # d) Etykieta (ifAlias)
        label_text = api_port_info.get("ifAlias", "")
        if not label_text:
            edge_cell.set("target", port_cell_id)
            continue
        label_id = f"label_{port_cell_id}"
        label_style = (
            "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;"
            "whiteSpace=wrap;overflow=visible;rotation=-90;fontSize=9;" )
        lines = label_text.split('\n'); num_lines = len(lines)
        max_line_len = max(len(line) for line in lines) if lines else 0
        label_width = num_lines * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
        label_height = max(30, max_line_len * (LABEL_LINE_HEIGHT * 0.8)) + 2 * LABEL_PADDING
        label_abs_x = abs_px + LABEL_OFFSET_X
        if port_number % 2 != 0: label_abs_y = abs_line_end_y - MARGIN_BETWEEN_LINE_AND_LABEL - label_height
        else: label_abs_y = abs_line_end_y + MARGIN_BETWEEN_LINE_AND_LABEL
        label_cell = drawio_utils.create_label_cell(
            label_id, "1", label_text, label_abs_x, label_abs_y, label_width, label_height, label_style
        )
        global_root_cell.append(label_cell)
        edge_cell.set("target", label_id)

    # 6. Dodaj etykietę z informacjami o urządzeniu ORAZ listą innych interfejsów
    dev_info_id = f"device_info_{group_id_suffix}"
    # Logika sprawdzająca Host/IP
    dev_id_val = device_info.get('device_id', 'N/A')
    hostname_val = device_info.get('hostname', 'N/A')
    ip_val = device_info.get('ip', 'N/A')
    looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', str(hostname_val)))
    display_host = hostname_val
    display_ip = ip_val
    if looks_like_ip and (ip_val == 'N/A' or not ip_val):
         display_ip = hostname_val; display_host = "(Brak Hosta)"
    elif hostname_val == 'N/A' or not hostname_val:
         if ip_val and ip_val != 'N/A': display_host = f"(Host={ip_val})"
         else: display_host = "(Brak Danych)"
    # Tworzenie HTML etykiety
    base_device_label_html = (f"<div style='text-align:left;'><b>ID:</b> {dev_id_val}<br/><b>Host:</b> {display_host}<br/><b>IP:</b> {display_ip}</div>")
    logical_interface_list_html = "<b>Inne Interfejsy:</b><br/><div style='margin:0;padding-left:5px;max-height:100px;overflow:auto;'>"
    if logical_interfaces:
        logical_interfaces.sort(key=lambda p: p.get('ifName', ''))
        for logical_if in logical_interfaces:
            name = logical_if.get('ifName') or logical_if.get('ifDescr', 'N/A')
            status = logical_if.get('ifOperStatus', 'unknown').lower()
            status_color = "green" if status == "up" else ("red" if status == "down" else "orange")
            if_type_str = logical_if.get('_ifType_iana', '')
            type_info = f" ({if_type_str})" if if_type_str else ""
            logical_interface_list_html += f"<font color='{status_color}'>•</font> {name}{type_info} ({status})<br/>"
    else: logical_interface_list_html += "(brak)<br/>"
    logical_interface_list_html += "</div>"
    full_device_label_html = base_device_label_html + "<hr size='1'/>" + logical_interface_list_html
    # Styl etykiety
    dev_info_style = ("text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;fontSize=9;whiteSpace=wrap;overflow=visible;rounded=0;spacing=4;")
    # Wymiary i pozycja
    info_width = max(template_width, 220)
    base_height = 50
    logical_if_height = max(20, len(logical_interfaces) * (LABEL_LINE_HEIGHT + 4))
    info_height = base_height + logical_if_height
    info_x = -100
    vertical_gap_above_group = 15
    info_y = -(info_height + vertical_gap_above_group)
    # Tworzenie komórki
    dev_info_cell = drawio_utils.create_label_cell(
        dev_info_id, group_id, full_device_label_html, info_x, info_y, info_width, info_height, dev_info_style
    )
    global_root_cell.append(dev_info_cell)

    print(f"  ✓ Urządzenie {current_host_identifier} przetworzone (z listą interfejsów).")
    # *** ZMIANA: Zwróć mapowanie portów ***
    return port_map_for_device