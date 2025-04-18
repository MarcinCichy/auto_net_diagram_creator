# drawio_device_builder.py
import xml.etree.ElementTree as ET
import re
from librenms_client import LibreNMSAPI # Używamy skonsolidowanego klienta
import drawio_utils # Importujemy nasze utilsy

# Stałe do rysowania etykiet portów
LINE_LENGTH = 25
LABEL_OFFSET_X = 5
MARGIN_BETWEEN_LINE_AND_LABEL = 15
LABEL_LINE_HEIGHT = 10
LABEL_PADDING = 4
DEFAULT_PORT_WIDTH = 40
DEFAULT_PORT_HEIGHT = 40


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

    # Kopiujemy elementy, żeby nie modyfikować oryginalnego drzewa szablonu w pamięci
    original_cells = list(template_root)
    temp_root_for_processing = ET.Element("root")
    for cell in original_cells:
         import copy
         temp_root_for_processing.append(copy.deepcopy(cell))

    id_suffix = f"dev{device_index}"
    drawio_utils.reassign_cell_ids(temp_root_for_processing, id_suffix)

    bbox = drawio_utils.get_bounding_box(temp_root_for_processing)
    # Poprawka: get_bounding_box zwraca teraz None lub krotkę, sprawdzamy None
    if bbox is None:
         print(f"  ⚠ Nie można obliczyć wymiarów szablonu (bbox is None): {template_path}")
         return None, 0, 0
    min_x, min_y, width, height = bbox
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
                          device_index: int):
    """
    Dodaje przygotowane elementy szablonu jako grupę do globalnego diagramu,
    stosuje dane z API (kolory, etykiety) i dodaje etykietę urządzenia.
    """
    if not template_cells or global_root_cell is None:
        print("  ⚠ Brak elementów szablonu lub globalnego roota do dodania urządzenia.")
        return

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

    # 2. Dodaj komórki szablonu do globalnego roota, ustawiając parent na grupę
    cell_map = {}
    for cell_copy in template_cells:
        cell_copy.set("parent", group_id)
        global_root_cell.append(cell_copy)
        cell_id = cell_copy.get("id")
        if cell_id:
            cell_map[cell_id] = cell_copy

    # 3. Pobierz dane portów z API
    device_id = device_info.get("device_id")
    if not device_id:
        print("  ⚠ Brak device_id, nie można pobrać portów z API.")
        ports_data = []
    else:
        print(f"  Pobieranie portów dla device_id {device_id}...")
        ports_data = api_client.get_ports(str(device_id))
        if ports_data is None:
             print(f"  ⚠ Nie udało się pobrać portów dla device_id {device_id}.")
             ports_data = []
        else:
             print(f"  Pobrano {len(ports_data)} portów z API.")

    # 4. Znajdź komórki portów w dodanych elementach
    port_cell_elements = []
    for cell_id, cell in cell_map.items():
         if cell.get("parent") == group_id:
              value = cell.get("value", "").strip()
              if value.isdigit():
                   port_cell_elements.append(cell)

    try:
        port_cell_elements.sort(key=lambda c: int(c.get("value", "0").strip()))
    except Exception as e:
        print(f"  ⚠ Błąd sortowania komórek portów w szablonie: {e}")

    print(f"  Znaleziono {len(port_cell_elements)} komórek portów w szablonie dla grupy {group_id}.")

    # 5. Iteruj przez porty, stosuj style i dodawaj etykiety
    num_ports_to_process = min(len(port_cell_elements), len(ports_data))
    print(f"  Przetwarzanie {num_ports_to_process} portów...")

    for i in range(num_ports_to_process):
        api_port_info = ports_data[i]
        port_cell = port_cell_elements[i]
        port_cell_id = port_cell.get("id")

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
        except (ValueError, TypeError):
            print(f"  ⚠ Błąd geometrii dla portu {i+1} (ID: {port_cell_id}). Pomijam etykietę.")
            continue

        if port_number % 2 != 0:
            line_start_y = py
            line_end_y = py - LINE_LENGTH
        else:
            line_start_y = py + ph
            line_end_y = py + ph + LINE_LENGTH

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
        # *** ZMIENIONY STYL ETYKIETY ***
        label_style = (
            "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;" # Przywrócono align=center
            "whiteSpace=wrap;overflow=visible;rotation=-90;fontSize=9;" # overflow=visible, rotation=-90
        )

        # *** ZMIENIONE OBLICZENIA WYMIARÓW ETYKIETY ***
        lines = label_text.split('\n')
        num_lines = len(lines)
        max_line_len = max(len(line) for line in lines) if lines else 0
        # Szerokość pudełka (na ekranie poziomo) zależy od liczby linii (wysokości tekstu pionowo)
        label_width = num_lines * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
        # Wysokość pudełka (na ekranie pionowo) zależy od długości najdłuższej linii (szerokości tekstu poziomo)
        # Zwiększamy mnożnik i min. wysokość
        label_height = max(30, max_line_len * (LABEL_LINE_HEIGHT * 0.8)) + 2 * LABEL_PADDING # Użyto mnożnika 0.8

        # Pozycja etykiety
        label_abs_x = abs_px + LABEL_OFFSET_X
        if port_number % 2 != 0:
            label_abs_y = abs_line_end_y - MARGIN_BETWEEN_LINE_AND_LABEL - label_height
        else:
            label_abs_y = abs_line_end_y + MARGIN_BETWEEN_LINE_AND_LABEL

        # Tworzenie komórki etykiety
        label_cell = drawio_utils.create_label_cell(
            label_id, "1", label_text,
            label_abs_x, label_abs_y, label_width, label_height, label_style
        )
        global_root_cell.append(label_cell)

        # Ustaw target krawędzi na etykietę
        edge_cell.set("target", label_id)


    # 6. Dodaj etykietę z informacjami o urządzeniu (jako dziecko grupy)
    dev_info_id = f"device_info_{group_id_suffix}"
    device_label_html = (
        f"<div style='text-align:left;'>"
        f"<b>ID:</b> {device_info.get('device_id', 'N/A')}<br/>"
        f"<b>Host:</b> {device_info.get('hostname', 'N/A')}<br/>"
        f"<b>IP:</b> {device_info.get('ip', 'N/A')}"
        f"</div>"
    )
    dev_info_style = (
        "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;"
        "fontSize=10;whiteSpace=wrap;overflow=visible;"
    )
    info_x = 0
    info_y = -40
    info_width = max(template_width, 150)
    info_height = 40
    dev_info_cell = drawio_utils.create_label_cell(
        dev_info_id, group_id, device_label_html,
        info_x, info_y, info_width, info_height, dev_info_style
    )
    global_root_cell.append(dev_info_cell)

    print(f"  ✓ Urządzenie {current_host_identifier} przetworzone.")