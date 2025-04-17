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
    dodajemy sufiks '_device{device_index}' aby uniknąć duplikatów w globalnym pliku.
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
    3. Tworzy "grupę" (kontener) dla urządzenia – wszystkie obiekty mają parent ustawiony na id grupy.
    4. Dodaje do grupy modyfikacje (kolorowanie portów, etykiety, informacje o urządzeniu) oraz
       ustawia położenie grupy (offset_x, offset_y).
    5. Wszystkie mxCell są dodawane jako rodzeństwo do globalnego <root>.
    """
    # 1) Ładujemy szablon i reassignujemy id
    device_tree = load_template()
    if device_tree is None:
        return
    device_root_cell = device_tree.getroot().find(".//root")
    if device_root_cell is None:
        print("Nie znaleziono <root> w szablonie urządzenia.")
        return

    reassign_ids(device_root_cell, device_index)

    # 2) Obliczamy bounding box i normalizujemy pozycje
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
    if min_x == float('inf'): min_x = 0
    if min_y == float('inf'): min_y = 0
    width  = max_x - min_x
    height = max_y - min_y

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

    # 3) Przygotowujemy globalny root i tworzymy grupę
    global_root = global_tree.getroot().find("./root")
    if global_root is None:
        print("Brak <root> w globalnym pliku draw.io.")
        return

    group_id = f"group_device_{device_index}"
    group_cell = ET.Element("mxCell", {
        "id":    group_id,
        "value": "",
        "style": "group",
        "vertex":"1",
        "parent":"1"
    })
    ET.SubElement(group_cell, "mxGeometry", {
        "x":      str(offset_x),
        "y":      str(offset_y),
        "width":  str(width),
        "height": str(height),
        "as":     "geometry"
    })
    global_root.append(group_cell)

    # 4) Dodajemy wszystkie elementy z szablonu do grupy
    for child in list(device_root_cell):
        child.set("parent", group_id)
        global_root.append(child)

    # 5) Pobieramy dane portów i rysujemy krawędzie + etykiety
    device_id  = device_info.get("device_id")
    ports_data = api.get_ports(str(device_id))
    print(
        f"Urządzenie {device_id}, portów w szablonie: {len(find_port_cells(device_root_cell))}, "
        f"w API: {len(ports_data)}"
    )

    port_cells = [c for c in find_port_cells(global_root) if c.get("parent") == group_id]
    count = min(len(port_cells), len(ports_data))

    # parametry rysowania
    line_length    = 25
    label_offset_x = 5
    MARGIN         = 20  # stała przerwa między końcem linii a etykietą
    LINE_HEIGHT    = 12  # wysokość pojedynczego wiersza tekstu
    PADDING        = 4   # padding wewnątrz boxa etykiety

    for i in range(count):
        api_port  = ports_data[i]
        port_cell = port_cells[i]

        # kolorowanie portu
        status = api_port.get("ifOperStatus", "").lower()
        color  = "#00FF00" if status == "up" else "#FF0000"
        style  = port_cell.get("style", "")
        if "fillColor=" in style:
            style = re.sub(r"fillColor=[^;]+", f"fillColor={color}", style)
        else:
            if not style.endswith(";"):
                style += ";"
            style += f"fillColor={color};"
        port_cell.set("style", style)

        # geometria portu
        geom = port_cell.find("mxGeometry")
        if geom is None:
            continue
        px = float(geom.get("x", "0")) + float(geom.get("width", "40"))/2
        py = float(geom.get("y", "0"))
        ph = float(geom.get("height", "40"))

        # numer portu
        try:
            port_number = int(port_cell.get("value", "0"))
        except:
            port_number = 0

        # punkty krawędzi
        if port_number % 2 != 0:
            start_y = py
            end_y   = py - line_length
        else:
            start_y = py + ph
            end_y   = py + ph + line_length

        # tworzymy edge
        edge_id   = f"edge_{device_index}_{i}"
        edge_cell = ET.Element("mxCell", {
            "id":     edge_id,
            "value":  "",
            "style":  "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#FFFFFF;",
            "edge":   "1",
            "parent": group_id,
            "source": port_cell.get("id"),
            "target": ""
        })
        edge_geom = ET.SubElement(edge_cell, "mxGeometry", {
            "relative": "1",
            "as":       "geometry"
        })
        ET.SubElement(edge_geom, "mxPoint", {
            "as": "sourcePoint", "x": str(px), "y": str(start_y)
        })
        ET.SubElement(edge_geom, "mxPoint", {
            "as": "targetPoint", "x": str(px), "y": str(end_y)
        })
        global_root.append(edge_cell)

        # tworzymy etykietę
        label_text = api_port.get("ifAlias", "")
        label_id   = f"label_{device_index}_{i}"
        style_val  = (
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

        #––– TUTAJ: liczymy liczbę linii jako liczbę znaków (bo przy horizontal=0 każdy znak to nowy wiersz)
        total_chars = sum(len(line) for line in label_text.split("\n"))
        label_height = total_chars * LINE_HEIGHT + 2 * PADDING
        label_width  = LINE_HEIGHT + 2 * PADDING  # kwadratowy box
        label_x      = px + label_offset_x

        # pozycja Y z zachowaniem stałej przerwy
        if port_number % 2 != 0:
            # etykieta nad linią
            label_y = end_y - MARGIN - label_height
        else:
            # etykieta pod linią
            label_y = end_y + MARGIN

        label_cell = ET.Element("mxCell", {
            "id":     label_id,
            "value":  label_text,
            "style":  style_val,
            "vertex": "1",
            "parent": group_id
        })
        ET.SubElement(label_cell, "mxGeometry", {
            "x":      str(label_x),
            "y":      str(label_y),
            "width":  str(label_width),
            "height": str(label_height),
            "as":     "geometry"
        })
        global_root.append(label_cell)

        # łączymy edge z etykietą
        edge_cell.set("target", label_id)

    # dodajemy etykietę z informacjami o urządzeniu
    dev_info_id = f"device_info_{device_index}"
    device_label = (
        f"device_id: {device_info.get('device_id')}\n"
        f"hostname: {device_info.get('hostname', '')}\n"
        f"sysName: {device_info.get('sysName', '')}"
    )
    dev_info_cell = ET.Element("mxCell", {
        "id":     dev_info_id,
        "value":  device_label,
        "style":  "text;strokeColor=none;fillColor=none;align=left;verticalAlign=top;fontSize=12;",
        "vertex": "1",
        "parent": group_id
    })
    ET.SubElement(dev_info_cell, "mxGeometry", {
        "x":      "-30",
        "y":      "-170",
        "width":  "160",
        "height": "60",
        "as":     "geometry"
    })
    global_root.append(dev_info_cell)

    return (width, height)
