# diagram_builder.py
import json
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
    Zakładamy, że porty znajdują się w grupie o id 'wdXZIc1yJ1iBE2bjXRa4-1'.
    """
    groups = root.findall(".//mxCell[@id='wdXZIc1yJ1iBE2bjXRa4-1']")
    if not groups:
        print("Nie znaleziono grupy portów w szablonie.")
        return []
    port_group = groups[0]
    # Wybieramy wszystkie komórki potomne, które mają niepusty atrybut 'value'
    port_cells = [
        cell for cell in port_group.findall("mxCell")
        if cell.get("value") and cell.get("value").strip() != ""
    ]
    try:
        port_cells.sort(key=lambda c: int(c.get("value").strip()))
    except Exception as e:
        print("Błąd sortowania portów:", e)
    return port_cells


def add_api_info_to_template(tree: ET.ElementTree, api: LibreNMSAPI, device_id: str) -> None:
    """
    Pobiera porty dla danego urządzenia (device_id) z API,
    a następnie w szablonie (switch.drawio) dla każdego portu – w kolejności –
    zmienia tło portu na zielone, jeśli aktywny, oraz dodaje linię
    wychodzącą w górę/dół i etykietę z ifName + ifAlias.
    """
    ports = api.get_ports(device_id)
    print("API - Znalezione porty:")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    # Uzupełniamy dane portów – jeśli ifAlias jest pusty, pozostawiamy pusty ciąg
    for port in ports:
        if not port.get("ifAlias"):
            port["ifAlias"] = ""
    print("API - Zaktualizowane dane portów (z opisami):")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    root = tree.getroot()  # element <mxfile> lub <mxGraphModel>
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
        port_number = i + 1

        ifName = api_port.get("ifName", f"Port {port_number}")
        ifAlias = api_port.get("ifAlias", "")
        oper_status = api_port.get("ifOperStatus", "down")
        used = (oper_status.lower() == "up")

        # Modyfikujemy styl istniejącego portu – jeżeli port jest aktywny, dajemy zielone tło
        # style="rounded=0;whiteSpace=wrap;html=1;autosize=1;rotation=0;"
        old_style = port_cell.get("style", "")
        if used:
            # Dodaj fillColor=#b7e1cd (lub inny odcień zieleni) do stylu
            if "fillColor" not in old_style:
                new_style = old_style + ";fillColor=#b7e1cd;"
            else:
                # Podmieniamy istniejący fillColor
                parts = old_style.split(";")
                parts = [p for p in parts if not p.startswith("fillColor")]
                new_style = ";".join(parts) + ";fillColor=#b7e1cd;"
            port_cell.set("style", new_style)

        # Pobieramy współrzędne portu
        geom = port_cell.find("mxGeometry")
        if geom is not None:
            try:
                x = float(geom.get("x", "0"))
                y = float(geom.get("y", "0"))
            except ValueError:
                x, y = 0, 0
        else:
            x, y = 0, 0

        # Logika określająca kierunek linii (góra/dół)
        # Przykład: jeżeli y < 20 => górny rząd => linia wychodzi w górę
        # jeżeli y >= 20 => dolny rząd => linia wychodzi w dół
        if y < 20:
            # Górny rząd
            label_x = x
            label_y = y - 40  # odsuń etykietę w górę
        else:
            # Dolny rząd
            label_x = x
            label_y = y + 30  # odsuń etykietę w dół

        # Tworzymy etykietę (ifName + ifAlias)
        full_label = f"Port {port_number}: {ifName}"
        if ifAlias:
            full_label += f"\n{ifAlias}"

        new_label_id = f"api_label_{port_number}"
        new_label_cell = ET.SubElement(
            root_cell,
            "mxCell",
            {
                "id": new_label_id,
                "value": full_label,
                "style": "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;",
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
                "width": "120",
                "height": "40",
                "as": "geometry"
            }
        )

        # Dodajemy krawędź
        new_edge_id = f"api_edge_{port_number}"
        new_edge = ET.SubElement(
            root_cell,
            "mxCell",
            {
                "id": new_edge_id,
                "value": "",
                "style": "endArrow=block;dashed=1;",
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

        print(
            f"[Port {port_number}] {ifName} - {ifAlias} (Status: {oper_status}) => styl: {port_cell.get('style', '')}")


def build_diagram_for_device_id(api: LibreNMSAPI, device_id: str) -> str:
    """
    Buduje diagram dla danego urządzenia na podstawie portów pobranych z API.
    Ładuje szablon switch.drawio, uzupełnia go o dane z API i zwraca diagram jako XML string.
    """
    tree = load_template()
    if tree is None:
        return ""
    add_api_info_to_template(tree, api, device_id)
    return ET.tostring(tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
