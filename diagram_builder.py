# diagram_builder.py
import json
import xml.etree.ElementTree as ET
from librenms_api import LibreNMSAPI

TEMPLATE_FILE = "switch.drawio"


def load_template(filename=TEMPLATE_FILE) -> ET.ElementTree:
    """Ładuje szablon diagramu z pliku XML."""
    tree = ET.parse(filename)
    return tree


def find_port_cells(root: ET.Element) -> list:
    """
    Znajduje komórki reprezentujące porty w szablonie.
    Zakładamy, że porty znajdują się w grupie o id "wdXZIc1yJ1iBE2bjXRa4-1".
    """
    port_group = root.find(".//mxCell[@id='wdXZIc1yJ1iBE2bjXRa4-1']")
    if port_group is None:
        print("Nie znaleziono grupy portów w szablonie.")
        return []
    # Wybieramy wszystkie mxCell, które mają atrybut 'value' (numer portu)
    port_cells = [cell for cell in port_group.findall("mxCell") if cell.get("value")]
    # Sortujemy porty według numeru (zakładamy, że value to liczba jako tekst)
    port_cells.sort(key=lambda c: int(c.get("value").strip()))
    return port_cells


def add_api_info_to_template(tree: ET.ElementTree, api: LibreNMSAPI, device_id: str) -> None:
    """
    Pobiera porty dla danego urządzenia (device_id) z API,
    a następnie w szablonie (switch.drawio) dla każdego portu, w kolejności,
    dodaje nową etykietę z informacjami z API (ifName oraz ifAlias)
    oraz łączy (edge) port z tą etykietą.
    """
    # Pobierz porty dla urządzenia z API
    ports = api.get_ports(device_id)
    print("API - Znalezione porty:")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    # Uzupełniamy dane portów – jeśli ifAlias jest pusty, pozostawiamy pusty ciąg
    for port in ports:
        if not port.get("ifAlias"):
            port["ifAlias"] = ""
    print("API - Zaktualizowane dane portów (z opisami):")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    root = tree.getroot()  # mxGraphModel
    port_cells = find_port_cells(root)
    if not port_cells:
        print("Brak portów w szablonie.")
        return

    count = min(len(port_cells), len(ports))
    print(f"Przetwarzam {count} portów (Szablon: {len(port_cells)}, API: {len(ports)})")

    root_cell = root.find("root")
    if root_cell is None:
        print("Nie znaleziono elementu 'root' w szablonie.")
        return

    for i in range(count):
        api_port = ports[i]
        port_cell = port_cells[i]
        port_number = i + 1
        ifName = api_port.get("ifName", f"Port {port_number}")
        ifAlias = api_port.get("ifAlias", "")
        full_label = f"Port {port_number}: {ifName}" + (f"\n{ifAlias}" if ifAlias else "")
        oper_status = api_port.get("ifOperStatus", "down")

        # Pobieramy pozycję portu z szablonu
        geom = port_cell.find("mxGeometry")
        if geom is not None:
            try:
                x = float(geom.get("x", "0"))
                y = float(geom.get("y", "0"))
            except ValueError:
                x, y = 0, 0
        else:
            x, y = 0, 0

        # Ustal pozycję etykiety – przesunięcie w prawo od portu
        label_x = x + 30
        label_y = y

        new_label_id = f"api_label_{i + 1}"
        new_label_cell = ET.SubElement(
            root_cell,
            "mxCell",
            {
                "id": new_label_id,
                "value": full_label,
                "style": "rounded=1;whiteSpace=wrap;html=1;fillColor=#b7e1cd;",  # kolor zielony lub inny
                "vertex": "1",
                "parent": "1"
            }
        )
        ET.SubElement(
            new_label_cell,
            "mxGeometry",
            {
                "x": str(label_x),
                "y": str(label_y),
                "width": "100",
                "height": "40",
                "as": "geometry"
            }
        )

        # Dodajemy krawędź (edge) łączącą port w szablonie z nową etykietą
        new_edge_id = f"api_edge_{i + 1}"
        new_edge = ET.SubElement(
            root_cell,
            "mxCell",
            {
                "id": new_edge_id,
                "value": "",
                "style": "endArrow=block;dashed=1;",  # styl linii (przerywana ze strzałką)
                "edge": "1",
                "parent": "1",
                "source": port_cell.get("id"),
                "target": new_label_id
            }
        )
        ET.SubElement(
            new_edge,
            "mxGeometry",
            {
                "relative": "1",
                "as": "geometry"
            }
        )
        print(f"Do portu {port_number} dodano etykietę: {full_label} (Status: {oper_status})")


def build_diagram_for_device_id(api, device_id: str) -> str:
    """
    Buduje diagram dla danego urządzenia na podstawie portów pobranych z API.
    Ładuje szablon switch.drawio, uzupełnia go o dane z API, a następnie zwraca diagram jako XML string.
    """
    tree = load_template()
    add_api_info_to_template(tree, api, device_id)
    return ET.tostring(tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
