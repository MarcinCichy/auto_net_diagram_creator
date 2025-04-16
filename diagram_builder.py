# diagram_builder.py

import json
import re
import xml.etree.ElementTree as ET
from librenms_api import LibreNMSAPI
from pysnmp.hlapi import *

TEMPLATE_FILE = "switch.drawio"


#############################################
# Funkcje pomocnicze dotyczące szablonu
#############################################
def load_template(filename=TEMPLATE_FILE) -> ET.ElementTree:
    try:
        tree = ET.parse(filename)
        return tree
    except Exception as e:
        print(f"Błąd przy ładowaniu szablonu {filename}: {e}")
        return None


def find_port_cells(root: ET.Element) -> list:
    """
    Szuka komórek, których 'value' to same cyfry (np. '1', '2', '3').
    Dostosuj kryterium, jeśli Twój szablon oznacza porty inaczej.
    """
    port_cells = []
    for cell in root.iter("mxCell"):
        val = cell.get("value", "").strip()
        if val.isdigit():
            port_cells.append(cell)
    try:
        port_cells.sort(key=lambda c: int(c.get("value").strip()))
    except Exception as e:
        print("Błąd sortowania portów:", e)
    return port_cells


def reassign_ids(root_cell: ET.Element, device_index: int):
    """
    Dla wszystkich <mxCell> w root_cell zmieniamy id, parent, source, target:
    dodajemy sufix '_device{device_index}' aby uniknąć duplikatów w globalnym pliku.
    """
    id_map = {}
    for cell in root_cell.findall("./mxCell"):
        old_id = cell.get("id")
        if old_id:
            new_id = f"{old_id}_device{device_index}"
            id_map[old_id] = new_id

    for cell in root_cell.findall("./mxCell"):
        old_id = cell.get("id")
        if old_id in id_map:
            cell.set("id", id_map[old_id])
        old_parent = cell.get("parent")
        if old_parent in id_map:
            cell.set("parent", id_map[old_parent])
        old_source = cell.get("source")
        if old_source in id_map:
            cell.set("source", id_map[old_source])
        old_target = cell.get("target")
        if old_target in id_map:
            cell.set("target", id_map[old_target])


#############################################
# Funkcja dodająca dane urządzenia do globalnego diagramu
#############################################
def add_api_info_to_template(global_tree: ET.ElementTree,
                             api: LibreNMSAPI,
                             device_info: dict,
                             device_index: int = 1,
                             offset_x: float = 0,
                             offset_y: float = 0) -> tuple:
    """
    1. Ładuje szablon (switch.drawio) i dokonuje reassign_ids.
    2. Oblicza bounding box fragmentu i normalizuje pozycje (lewy górny róg -> 0,0).
    3. Tworzy "grupę" (kontener) dla urządzenia – wszystkie obiekty mają atrybut parent ustawiony na id grupy.
    4. Dodaje do grupy modyfikacje (kolorowanie portów, etykiety, informacje o urządzeniu) oraz
       ustawia położenie grupy (offset_x, offset_y).
    5. Wszystkie mxCell są dodawane jako rodzeństwo do globalnego <root>.
    """
    device_tree = load_template()
    if device_tree is None:
        return
    device_root_cell = device_tree.getroot().find(".//root")
    if device_root_cell is None:
        print("Nie znaleziono <root> w szablonie urządzenia.")
        return

    # Reassignuj ID, by uniknąć konfliktów
    reassign_ids(device_root_cell, device_index)

    # Oblicz bounding box
    min_x = float('inf')
    min_y = float('inf')
    max_x = float('-inf')
    max_y = float('-inf')
    for cell in device_root_cell.findall("./mxCell"):
        geom = cell.find("mxGeometry")
        if geom is not None:
            try:
                x = float(geom.get("x", "0"))
                y = float(geom.get("y", "0"))
                w = float(geom.get("width", "0"))
                h = float(geom.get("height", "0"))
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x + w)
                max_y = max(max_y, y + h)
            except Exception:
                continue
    if min_x == float('inf'):
        min_x = 0
    if min_y == float('inf'):
        min_y = 0
    width = max_x - min_x
    height = max_y - min_y

    # Normalizujemy – przesuwamy wszystkie elementy, aby lewy górny róg był (0,0)
    for cell in device_root_cell.findall("./mxCell"):
        geom = cell.find("mxGeometry")
        if geom is not None:
            try:
                x = float(geom.get("x", "0"))
                y = float(geom.get("y", "0"))
                geom.set("x", str(x - min_x))
                geom.set("y", str(y - min_y))
            except Exception:
                continue

    # Pobierz globalny <root> z global_tree – wszystkie mxCell będą dodawane bezpośrednio do niego
    global_root = global_tree.getroot().find("./root")
    if global_root is None:
        print("Brak <root> w globalnym pliku draw.io.")
        return

    # Utwórz grupę (kontener) dla urządzenia
    group_id = f"group_device_{device_index}"
    group_cell = ET.Element("mxCell", {
        "id": group_id,
        "value": "",
        "style": "group",
        "vertex": "1",
        "parent": "1"  # Dodajemy grupę jako bezpośrednie dziecko <root>
    })
    group_geom = ET.SubElement(group_cell, "mxGeometry", {
        "x": str(offset_x),
        "y": str(offset_y),
        "width": str(width),
        "height": str(height),
        "as": "geometry"
    })
    # Dodaj grupę do globalnego <root>
    global_root.append(group_cell)

    # Teraz przekaż wszystkie komórki z szablonu – ustawiając ich parent na group_id – ale dodaj je bezpośrednio do global_root
    for child in list(device_root_cell):
        child.set("parent", group_id)
        global_root.append(child)

    # Pobierz porty z API – na podstawie device_id
    device_id = device_info.get("device_id")
    ports_data = api.get_ports(str(device_id))
    print(
        f"Urządzenie {device_id}, portów w szablonie: {len(find_port_cells(device_root_cell))}, w API: {len(ports_data)}")
    # W grupie szukamy portów – ponieważ wszystkie elementy zostały przepisane z szablonu
    port_cells = find_port_cells(global_root)  # wyszukujemy we wszystkich elementach global_root
    # filtrowanie tylko tych, które należą do naszej grupy (mają parent == group_id)
    port_cells = [cell for cell in port_cells if cell.get("parent") == group_id]
    count = min(len(port_cells), len(ports_data))

    # Parametry do rysowania linii i etykiet
    line_length = 25
    label_offset_x = 5
    lower_ports_count = count // 2

    for i in range(count):
        api_port = ports_data[i]
        port_cell = port_cells[i]

        # Kolor portu
        status = api_port.get("ifOperStatus", "").lower()
        color = "#00FF00" if status == "up" else "#FF0000"
        current_style = port_cell.get("style", "")
        if "fillColor=" in current_style:
            new_style = re.sub(r"fillColor=[^;]+", f"fillColor={color}", current_style)
        else:
            if not current_style.endswith(";"):
                current_style += ";"
            new_style = current_style + f"fillColor={color};"
        port_cell.set("style", new_style)

        # Pobieramy geometrię portu do obliczenia pozycji etykiety i krawędzi
        geom = port_cell.find("mxGeometry")
        if geom is None:
            continue
        try:
            px = float(geom.get("x", "0")) + float(geom.get("width", "40")) / 2
            py = float(geom.get("y", "0"))
            ph = float(geom.get("height", "40"))
        except:
            continue

        # Pobieramy numer portu z wartości komórki (przyjmujemy, że porty są numerowane)
        try:
            port_number = int(port_cell.get("value", "0"))
        except:
            port_number = 0

        # Dla portów górnych (numer nieparzysty) linia zaczyna się od górnej krawędzi i idzie w górę;
        # dla portów dolnych (numer parzysty) linia zaczyna się od dolnej krawędzi i idzie w dół.
        if port_number % 2 != 0:  # port numer nieparzysty – górny port
            start_y = py  # zaczynamy od górnej krawędzi
            end_y = py - line_length  # linia idzie w górę (odjęcie wartości)
        else:  # port numer parzysty – dolny port
            start_y = py + ph  # zaczynamy od dolnej krawędzi
            end_y = py + ph + line_length  # linia idzie w dół (dodanie wartości)

        # Tworzymy krawędź (edge)
        edge_id = f"edge_{device_index}_{i}"
        edge_cell = ET.Element("mxCell", {
            "id": edge_id,
            "value": "",
            "style": "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#FFFFFF;",
            "edge": "1",
            "parent": group_id,  # atrybut parent wskazuje na naszą grupę
            "source": port_cell.get("id"),
            "target": ""  # Będziemy ustawiać target na etykietę
        })
        edge_geom = ET.SubElement(edge_cell, "mxGeometry", {
            "relative": "1",
            "as": "geometry"
        })
        ET.SubElement(edge_geom, "mxPoint", {
            "as": "sourcePoint",
            "x": str(px),
            "y": str(start_y)
        })
        ET.SubElement(edge_geom, "mxPoint", {
            "as": "targetPoint",
            "x": str(px),
            "y": str(end_y)
        })
        global_root.append(edge_cell)  # Dodajemy krawędź do global_root

        # Tworzymy etykietę portu (osobne mxCell) z informacjami z API
        label_text = f"{api_port.get('ifAlias', '')}"
        label_id = f"label_{device_index}_{i}"

        # Styl wymuszający pionowy układ tekstu:
        #  - rotation=180 obraca całą etykietę o 180° i t jest pion
        #  - horizontal=0 oznacza, że tekst jest renderowany w pionie
        #  - labelPosition=middle;verticalLabelPosition=middle;align=center;verticalAlign=middle
        #    starają się umieścić tekst centralnie w ramce
        style_val = (
            "text;html=1;"
            "strokeColor=none;"
            "fillColor=none;"
            "fontSize=10;"
            "align=center;"
            "verticalAlign=middle;"
            "horizontal=0;"
            "labelPosition=middle;"
            "verticalLabelPosition=middle;"
            "rotation=180;"
        )

        # Określamy oszacowaną wysokość pojedynczego wiersza (dla czcionki o rozmiarze 10/12 może to być około 12 jednostek)
        line_height = 12
        # Możesz ustalić dodatkowy margines, żeby zapewnić odstęp od linii
        margin_top = 5
        margin_bottom = 5

        # Oszacowujemy liczbę wierszy – jeśli tekst nie zawiera "\n", to będzie jeden wiersz
        num_lines = label_text.count("\n") + 1
        estimated_label_height = num_lines * line_height

        # Dla portów górnych (nieparzystych) etykieta ma być wyświetlona nad końcem linii,
        # czyli przesunięcie to - (oszacowana wysokość etykiety + margines)
        # Dla portów dolnych (parzystych) etykieta ma się wyświetlać poniżej końca linii, czyli dodajemy margines.
        if port_number % 2 != 0:  # port nieparzysty (górny)
            label_y = end_y - estimated_label_height - margin_top
        else:  # port parzysty (dolny)
            label_y = end_y + margin_bottom

        # Ustal pozycję etykiety w zależności od rodzaju portu:
        # 1. Dla portów górnych (nieparzystych) etykieta ma być wyświetlona powyżej końca linii
        # 2. Dla portów dolnych (parzystych) etykieta ma być wyświetlona poniżej końca linii
        if port_number % 2 != 0:
            label_y = end_y - 120  # Przesunięcie w górę; 80 to przykładowa wysokość etykiety
        else:
            label_y = end_y + 40  # Przesunięcie w dół; 10 to przykładowy margines

        label_cell = ET.Element("mxCell", {
            "id": label_id,
            "value": label_text,
            "style": style_val,
            "vertex": "1",
            "parent": group_id
        })
        # Uwaga: width i height odwrócone, bo po obrocie "szerszy" wymiar staje się "wyższy"
        ET.SubElement(label_cell, "mxGeometry", {
            "x": str(px + label_offset_x),
            # "y": str(end_y - 10),
            "y": str(label_y),
            "width": "20",   # wąski na 20
            "height": "80",  # wysoki na 80
            "as": "geometry"
        })
        global_root.append(label_cell)  # Dodaj etykietę do global_root

        # Ustawiamy target krawędzi na id etykiety
        edge_cell.set("target", label_id)

    # Dodajmy etykietę z informacjami o urządzeniu (device_id, hostname, sysName)
    dev_info_id = f"device_info_{device_index}"
    device_label = (
        f"device_id: {device_info.get('device_id')}\n"
        f"hostname: {device_info.get('hostname', '')}\n"
        f"sysName: {device_info.get('sysName', '')}"
    )
    dev_info_cell = ET.Element("mxCell", {
        "id": dev_info_id,
        "value": device_label,
        "style": "text;strokeColor=none;fillColor=none;align=left;verticalAlign=top;fontSize=12;",
        "vertex": "1",
        "parent": group_id
    })
    # etykiety urządzeń
    dev_info_geom = ET.SubElement(dev_info_cell, "mxGeometry", {
        "x": "-30",
        "y": "-170",
        "width": "160",
        "height": "60",
        "as": "geometry"
    })
    global_root.append(dev_info_cell)
    return (width, height)
