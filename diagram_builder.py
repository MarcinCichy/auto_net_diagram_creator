# diagram_builder.py
import json
import re
import xml.etree.ElementTree as ET
from librenms_api import LibreNMSAPI

TEMPLATE_FILE = "switch.drawio"


def load_template(filename=TEMPLATE_FILE) -> ET.ElementTree:
    """Ładuje szablon diagramu z pliku XML."""
    try:
        tree = ET.parse(filename)
        return tree
    except Exception as e:
        print(f"Błąd przy ładowaniu szablonu {filename}: {e}")
        return None


def find_port_cells(root: ET.Element) -> list:
    """
    Znajduje komórki reprezentujące porty w szablonie.
    Szuka wszystkich <mxCell>, które mają atrybut 'parent' równy "wdXZIc1yJ1iBE2bjXRa4-1"
    oraz posiadają niepustą wartość w atrybucie 'value'.
    """
    port_cells = []
    for cell in root.iter("mxCell"):
        if cell.get("parent") == "wdXZIc1yJ1iBE2bjXRa4-1":
            val = cell.get("value", "").strip()
            if val:
                port_cells.append(cell)
    try:
        port_cells.sort(key=lambda c: int(c.get("value").strip()))
    except Exception as e:
        print("Błąd sortowania portów:", e)
    return port_cells


def add_api_info_to_template(tree: ET.ElementTree, api: LibreNMSAPI, device_id: str) -> None:
    """
    Pobiera porty dla danego urządzenia (device_id) z API,
    a następnie koloruje symbole portów w szablonie:
      - status "up" -> zielony (#00FF00),
      - każdy inny -> czerwony (#FF0000).

    Kolejność portów z API jest zakładana zgodna z kolejnością portów w szablonie.
    """
    ports = api.get_ports(device_id)
    print("API - Znalezione porty:")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    root = tree.getroot()  # element <mxfile>
    root_cell = root.find(".//root")
    if root_cell is None:
        print("Nie znaleziono elementu 'root' w szablonie.")
        return

    port_cells = find_port_cells(root)
    if not port_cells:
        print("Brak portów w szablonie.")
        return

    count = min(len(port_cells), len(ports))
    print(f"Przetwarzam {count} portów (Szablon: {len(port_cells)}, API: {len(ports)})")

    for i in range(count):
        api_port = ports[i]
        port_cell = port_cells[i]
        # Pobieramy status operacyjny portu i zamieniamy na małe litery
        status = api_port.get("ifOperStatus", "").lower()
        # Jeśli status zawiera "up" (np. "up" lub "up (1Gb/s)"), ustawiamy kolor zielony
        if "up" in status:
            new_color = "#00FF00"
        else:
            new_color = "#FF0000"

        current_style = port_cell.get("style", "")
        # Jeśli w stylu istnieje fillColor, zamieniamy go; w przeciwnym razie dodajemy
        if "fillColor=" in current_style:
            new_style = re.sub(r"fillColor=[^;]+", f"fillColor={new_color}", current_style)
        else:
            if not current_style.endswith(";"):
                current_style += ";"
            new_style = current_style + f"fillColor={new_color};"
        port_cell.set("style", new_style)
        print(f"Port {port_cell.get('value')} -> status: {status}, kolor: {new_color}")


def build_diagram_for_device_id(api: LibreNMSAPI, device_id: str) -> str:
    """
    Buduje diagram dla danego urządzenia na podstawie portów pobranych z API.
    Ładuje szablon switch.drawio, uzupełnia go o dane z API (kolorowanie portów)
    i zwraca diagram jako XML string.
    """
    tree = load_template()
    if tree is None:
        return ""
    add_api_info_to_template(tree, api, device_id)
    return ET.tostring(tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
