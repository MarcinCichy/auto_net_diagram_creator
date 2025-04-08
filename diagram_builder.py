import copy
import re
import xml.etree.ElementTree as ET
import html
from librenms_api import LibreNMSAPI

TEMPLATE_FILE = "switch.drawio"  # Nazwa pliku szablonu

#############################################
# Funkcje SNMP – pozostają dla ewentualnej diagnostyki (zwracają puste wyniki)
#############################################
def get_snmp_dot1d_base_port_ifindex_map(device_ip, community, timeout=5, retries=3):
    return {}

def get_snmp_arp_table(device_ip, community, timeout=5, retries=3):
    return {}

def get_snmp_fdb_table(device_ip, community, timeout=5, retries=3):
    return {}

#############################################
# Inferowanie połączeń L2 poprzez MAC (dane z API)
#############################################
def infer_connections_by_mac(devices_map, all_ports_data):
    """
    Dla każdego urządzenia zbiera listę portów jako krotki: (ifIndex, ifPhysAddress)
    Następnie dla każdej pary urządzeń sprawdza, czy istnieje port z tym samym (niepustym)
    ifPhysAddress. Jeśli tak, uznajemy, że porty są połączone.
    Zwracamy listę krotek: (deviceA_IP, portA_ifIndex, deviceB_IP, portB_ifIndex).
    """
    links = set()
    device_mac_map = {}
    for ip, ports in all_ports_data.items():
        device_mac_map[ip] = []
        for port in ports:
            mac = port.get("ifPhysAddress", "").lower()
            if mac and mac != "00:00:00:00:00:00":
                ifindex = port.get("ifIndex")
                device_mac_map[ip].append((ifindex, mac))
    ips = list(device_mac_map.keys())
    for i in range(len(ips)):
        for j in range(i+1, len(ips)):
            ip_a = ips[i]
            ip_b = ips[j]
            for ifidx_a, mac_a in device_mac_map[ip_a]:
                for ifidx_b, mac_b in device_mac_map[ip_b]:
                    if mac_a == mac_b:
                        if ip_a < ip_b:
                            links.add((ip_a, ifidx_a, ip_b, ifidx_b))
                        else:
                            links.add((ip_b, ifidx_b, ip_a, ifidx_a))
    print(f"Infer connections by MAC: znaleziono {len(links)} połączeń.")
    return list(links)

#############################################
# Funkcje przetwarzające szablon diagramu
#############################################
def load_template(filename=TEMPLATE_FILE) -> ET.ElementTree | None:
    try:
        tree = ET.parse(filename)
        print(f"Załadowano szablon: {filename}")
        return tree
    except Exception as e:
        print(f"Błąd przy ładowaniu szablonu: {e}")
        return None

def load_template_switch_group(filename=TEMPLATE_FILE) -> ET.Element | None:
    try:
        tree = ET.parse(filename)
        root = tree.getroot()
        switch_group_id = "wdXZIc1yJ1iBE2bjXRa4-1"
        switch_group = root.find(f".//mxCell[@id='{switch_group_id}']")
        if switch_group is None:
            print(f"Błąd: Nie znaleziono grupy przełącznika o ID '{switch_group_id}' w szablonie '{filename}'.")
            return None
        print(f"Pomyślnie załadowano grupę przełącznika '{switch_group_id}' z szablonu.")
        return switch_group
    except Exception as e:
        print(f"Błąd przy ładowaniu szablonu '{filename}': {e}")
        return None

def find_port_cells(root: ET.Element) -> list:
    # Próbujemy znaleźć portowe komórki (mxCell z vertex="1" i numeryczną wartością w atrybucie value)
    port_cells = root.findall(".//mxCell[@vertex='1']")
    port_cells = [cell for cell in port_cells if cell.get("value", "").strip().isdigit()]
    print(f"find_port_cells: znaleziono {len(port_cells)} komórek portów w szablonie.")
    return port_cells

def create_port_cells(root_cell: ET.Element, api_ports: list, device_id: str) -> list:
    """
    Jeśli szablon nie zawiera portowych komórek, ta funkcja generuje je dynamicznie.
    Każdy port (uzyskany z API) tworzy nową komórkę mxCell o wartości numeru portu.
    """
    created_cells = []
    x0, y0 = 10, 10  # Początkowa pozycja dla portów – można dostosować
    spacing = 40     # Odstęp między portami
    for idx, port in enumerate(api_ports):
        port_number = str(port.get('ifIndex'))
        new_cell = ET.Element("mxCell", {
            "id": f"port_{device_id}_{port_number}",
            "value": port_number,
            "style": "shape=ellipse;fillColor=#FFFFFF;strokeColor=#000000;",
            "vertex": "1",
            "parent": "1"
        })
        geom = ET.SubElement(new_cell, "mxGeometry", {
            "x": str(x0 + idx * spacing),
            "y": str(y0),
            "width": "30",
            "height": "30",
            "as": "geometry"
        })
        root_cell.append(new_cell)
        created_cells.append(new_cell)
    print(f"create_port_cells: utworzono {len(created_cells)} komórek portów z API.")
    return created_cells

def add_device_info_label(root_cell: ET.Element, device_info: dict):
    device_id = device_info.get("device_id", "N/A")
    hostname = device_info.get("hostname", "N/A")
    ip_address = device_info.get("hostname", "N/A")
    device_label = (
        f"Hostname: {html.escape(str(hostname))}\n"
        f"IP: {html.escape(str(ip_address))}\n"
        f"Device ID: {html.escape(str(device_id))}"
    )
    label_id = f"device_info_label_{device_id}"
    if root_cell.find(f".//mxCell[@id='{label_id}']") is not None:
        print("Etykieta z informacjami o urządzeniu już istnieje.")
        return
    style = ("text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;"
             "whiteSpace=wrap;rounded=0;fontSize=12;fontColor=#333333;fontStyle=1;")
    info_cell = ET.SubElement(root_cell, "mxCell", {
        "id": label_id,
        "value": device_label,
        "style": style,
        "vertex": "1",
        "parent": "1"
    })
    ET.SubElement(info_cell, "mxGeometry", {
        "x": "300", "y": "330", "width": "200", "height": "60", "as": "geometry"
    })
    print(f"Dodano etykietę z informacjami o urządzeniu (ID: {label_id}).")

def add_port_status_to_template(tree: ET.ElementTree, api: LibreNMSAPI, device_info: dict) -> None:
    """
    Kolorowanie portów odbywa się na podstawie danych API.
    Jeśli w szablonie nie znaleziono portowych komórek, dynamicznie tworzymy je przy użyciu create_port_cells.
    """
    device_id = device_info.get("device_id")
    if not device_id:
        print("Brak device_id – nie można pobrać portów.")
        return

    root = tree.getroot()
    mx_graph_model = root.find(".//mxGraphModel")
    if mx_graph_model is None:
        print("Błąd: Brak <mxGraphModel> w szablonie.")
        return
    root_cell = mx_graph_model.find("./root")
    if root_cell is None:
        print("Błąd: Brak <root> w <mxGraphModel>.")
        return

    add_device_info_label(root_cell, device_info)

    print(f"Pobieranie portów dla urządzenia ID: {device_id}...")
    try:
        api_ports = api.get_ports(str(device_id))
        if not api_ports:
            print(f"Nie znaleziono portów w API dla urządzenia {device_id} lub wystąpił błąd.")
            return
        print(f"Pobrano {len(api_ports)} portów z API.")
    except Exception as e:
        print(f"Błąd podczas pobierania portów dla {device_id}: {e}")
        return

    # Szukamy portowych komórek w szablonie
    port_cells = find_port_cells(root)
    if not port_cells:
        print("Brak komórek portów w szablonie – tworzymy je dynamicznie.")
        port_cells = create_port_cells(root_cell, api_ports, str(device_id))

    api_ports_by_ifindex = {p.get('ifIndex'): p for p in api_ports if p.get('ifIndex') is not None}

    print("Mapowanie portów API do komórek szablonu i kolorowanie:")
    colored_count = 0
    for cell in port_cells:
        port_number_str = cell.get("value", "").strip()
        try:
            port_num_int = int(port_number_str)
        except ValueError:
            continue
        found_api_port = api_ports_by_ifindex.get(port_num_int)
        if found_api_port:
            colored_count += 1
            status = found_api_port.get("ifOperStatus", "").lower()
            if "up" in status:
                color = "#00FF00"  # zielony
            elif "down" in status:
                color = "#FF0000"  # czerwony
            else:
                color = "#CCCCCC"  # szary
            curr_style = cell.get("style", "")
            if "fillColor=" in curr_style:
                new_style = re.sub(r"fillColor=[^;]+", f"fillColor={color}", curr_style)
            else:
                if curr_style and not curr_style.endswith(';'):
                    curr_style += ';'
                new_style = curr_style + f"fillColor={color};"
            cell.set("style", new_style)
        else:
            print(f"  Port {port_number_str}: Brak dopasowania – pozostaje bez zmian.")
    print(f"Pokolorowano {colored_count} portów.")

def build_diagram_for_device_info(api: LibreNMSAPI, device_info: dict) -> str:
    print("Ładowanie szablonu diagramu...")
    tree = load_template()
    if tree is None:
        return ""
    print("Modyfikowanie szablonu danymi z API...")
    add_port_status_to_template(tree, api, device_info)
    try:
        try:
            ET.indent(tree.getroot(), space="  ", level=0)
        except AttributeError:
            pass
        xml_string = ET.tostring(tree.getroot(), encoding="unicode", method="xml")
        final_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_string
        return final_xml
    except Exception as e:
        print(f"Błąd przy konwersji szablonu: {e}")
        return ""

def copy_and_modify_switch(template_group: ET.Element, device_info: dict,
                           ports_data: list, x_offset: int, y_offset: int,
                           device_suffix: str) -> tuple[ET.Element | None, dict]:
    print(f"Tworzenie kopii przełącznika dla: {device_info.get('hostname', device_suffix)} w ({x_offset},{y_offset})")
    try:
        copied_group = copy.deepcopy(template_group)
    except Exception as e:
        print(f"Błąd przy kopiowaniu szablonu: {e}")
        return None, {}

    # 1. Upewniamy się, że to jest 'vertex' i że ma styl 'group'
    copied_group.set("vertex", "1")
    current_style = copied_group.get("style", "")
    if "group" not in current_style:
        # Dodaj styl grupy – zapobiega „znikaniu” po wczytaniu do Draw.io
        # container=1 – pozwala trzymać elementy w środku, collapsible=0 – blokuje zwijanie grupy,
        # Możesz dostosować childLayout, np. horizontal, vertical, itp.
        group_style = "group;container=1;collapsible=0;recursiveResize=0;childLayout=horizontal;"
        copied_group.set("style", group_style + current_style)
    else:
        # Możesz dopisać inne atrybuty, jeśli częściowo styl "group" jest już tam obecny
        pass

    # 2. Wygeneruj nowe ID
    id_map = {}
    elements_to_process = [copied_group] + list(copied_group.iter())
    for element in elements_to_process:
        if element.tag == 'mxCell':
            old_id = element.get('id')
            if old_id:
                new_id = f"{old_id}_{device_suffix}"
                element.set('id', new_id)
                id_map[old_id] = new_id

    # 3. Podmień parent/source/target na nowe ID (z id_map)
    for element in elements_to_process:
        if element.tag == 'mxCell':
            old_parent = element.get('parent')
            if old_parent and old_parent in id_map and old_parent != template_group.get('id'):
                element.set('parent', id_map[old_parent])
            old_source = element.get('source')
            if old_source and old_source in id_map:
                element.set('source', id_map[old_source])
            old_target = element.get('target')
            if old_target and old_target in id_map:
                element.set('target', id_map[old_target])

    # 4. Ustaw geometrię głównej grupy – wystarczająco dużą
    geom = copied_group.find("./mxGeometry")
    if geom is None:
        geom = ET.SubElement(copied_group, "mxGeometry", {
            "x": str(x_offset),
            "y": str(y_offset),
            "width": "600",    # Szerokość grupy – dostosuj
            "height": "200",   # Wysokość grupy – dostosuj
            "as": "geometry"
        })
    else:
        # Dodaj przesunięcie
        try:
            original_x = float(geom.get("x", "0"))
            original_y = float(geom.get("y", "0"))
            geom.set("x", str(original_x + x_offset))
            geom.set("y", str(original_y + y_offset))
            if not geom.get("width"):
                geom.set("width", "600")
            if not geom.get("height"):
                geom.set("height", "200")
        except Exception as e:
            print(f"Ostrzeżenie przy przetwarzaniu geometrii: {e}")

    port_cell_map = {}

    # 5. Jeśli w szablonie są 'porty' (mxCell vertex=1, value=cyfra), spróbujmy je zmapować
    port_cells = [cell for cell in copied_group.findall(".//mxCell[@vertex='1']") if cell.get("value", "").strip().isdigit()]
    if not port_cells:
        print("Brak portów w szablonie kopii – porty nie zostały znalezione.")
    else:
        for cell in port_cells:
            port_value = cell.get("value", "").strip()
            if port_value.isdigit():
                new_cell_id = cell.get('id')
                port_cell_map[port_value] = new_cell_id
                # Upewniamy się, że port jest 'wewnątrz' grupy – geometry relative=1
                port_geom = cell.find("mxGeometry")
                if port_geom is not None:
                    port_geom.set("relative", "1")  # Porty liczone wzgl. grupy
                # Możesz dodać kolorowanie portu w tym miejscu, np. na podstawie ifOperStatus

    print(f"Zakończono kopię dla {device_info.get('hostname', device_suffix)} – znaleziono {len(port_cell_map)} portów.")
    return copied_group, port_cell_map

def create_device_label(device_info: dict, x_offset: int, y_offset: int, device_suffix: str) -> ET.Element | None:
    hostname = device_info.get("hostname", "N/A")
    ip_address = device_info.get("hostname", "N/A")
    label_text = f"<b>{html.escape(str(hostname))}</b>\n{html.escape(str(ip_address))}"
    label_id = f"label_{device_suffix}"
    style = ("text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;"
             "whiteSpace=wrap;rounded=0;fontSize=11;fontColor=#000000;")
    label_cell = ET.Element("mxCell", {
        "id": label_id, "value": label_text, "style": style,
        "vertex": "1", "parent": "1"
    })
    label_width = 180
    label_height = 40
    label_x = x_offset + (830 / 2) - (label_width / 2)
    label_y = y_offset + 400 - label_height - 10
    ET.SubElement(label_cell, "mxGeometry", {
        "x": str(label_x), "y": str(label_y),
        "width": str(label_width), "height": str(label_height), "as": "geometry"
    })
    return label_cell

def create_connection_edge(source_cell_id: str, target_cell_id: str, source_port_name: str, target_port_name: str, edge_counter: int) -> list[ET.Element]:
    edge_id = f"link_edge_{edge_counter}"
    elements = []
    style = ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
             "endArrow=none;endFill=0;startArrow=none;startFill=0;"
             "strokeWidth=1;strokeColor=#555555;labelBackgroundColor=#ffffff;")
    edge = ET.Element("mxCell", {
        "id": edge_id, "value": "", "style": style,
        "edge": "1", "parent": "1", "source": source_cell_id, "target": target_cell_id
    })
    ET.SubElement(edge, "mxGeometry", {"relative": "1", "as": "geometry"})
    elements.append(edge)
    if source_port_name:
        lbl_id = f"lbl_src_{edge_counter}"
        lbl_cell = ET.Element("mxCell", {
            "id": lbl_id, "value": html.escape(source_port_name),
            "style": "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];labelBackgroundColor=#FFFFFF;fontSize=9;",
            "vertex": "1", "connectable": "0", "parent": edge_id
        })
        lbl_geom = ET.SubElement(lbl_cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        ET.SubElement(lbl_geom, "mxPoint", {"as": "offset", "x": "0", "y": "10"})
        lbl_cell.set("geometry", lbl_geom.get("as"))
        lbl_cell.find("mxGeometry").set("x", "-1")
        elements.append(lbl_cell)
    if target_port_name:
        lbl_id = f"lbl_tgt_{edge_counter}"
        lbl_cell = ET.Element("mxCell", {
            "id": lbl_id, "value": html.escape(target_port_name),
            "style": "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];labelBackgroundColor=#FFFFFF;fontSize=9;",
            "vertex": "1", "connectable": "0", "parent": edge_id
        })
        lbl_geom = ET.SubElement(lbl_cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        ET.SubElement(lbl_geom, "mxPoint", {"as": "offset", "x": "0", "y": "-10"})
        lbl_cell.set("geometry", lbl_geom.get("as"))
        lbl_cell.find("mxGeometry").set("x", "1")
        elements.append(lbl_cell)
    return elements
