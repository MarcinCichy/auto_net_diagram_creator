import json
import re
import xml.etree.ElementTree as ET
from librenms_api import LibreNMSAPI
from pysnmp.hlapi import *

TEMPLATE_FILE = "switch.drawio"


#############################################
# Funkcje SNMP – pobierające dane (opcjonalnie)
#############################################
def get_snmp_arp_table(device_ip, community):
    ip_map = {}
    for (errorIndication,
         errorStatus,
         errorIndex,
         varBinds) in nextCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=0),
        UdpTransportTarget((device_ip, 161), timeout=2, retries=3),
        ContextData(),
        ObjectType(ObjectIdentity('1.3.6.1.2.1.4.22.1.3')),
        lexicographicMode=False):
        if errorIndication:
            print("ARP (IP):", errorIndication)
            break
        elif errorStatus:
            print('ARP (IP) Error: %s at %s' % (errorStatus.prettyPrint(),
                                                errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
            break
        else:
            for varBind in varBinds:
                oid, ip_val = varBind
                oid_str = oid.prettyPrint()
                parts = oid_str.split('.')
                key = tuple(parts[-5:])
                ip_map[key] = str(ip_val)
    mac_map = {}
    for (errorIndication,
         errorStatus,
         errorIndex,
         varBinds) in nextCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=0),
        UdpTransportTarget((device_ip, 161), timeout=2, retries=3),
        ContextData(),
        ObjectType(ObjectIdentity('IP-MIB', 'ipNetToMediaPhysAddress')),
        lexicographicMode=False):
        if errorIndication:
            print("ARP (MAC):", errorIndication)
            break
        elif errorStatus:
            print('ARP (MAC) Error: %s at %s' % (errorStatus.prettyPrint(),
                                                 errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
            break
        else:
            for varBind in varBinds:
                oid, mac_val = varBind
                oid_str = oid.prettyPrint()
                parts = oid_str.split('.')
                key = tuple(parts[-5:])
                mac = ':'.join(['%02x' % b for b in bytes(mac_val)])
                mac_map[key] = mac
    arp_table = {}
    for key, mac in mac_map.items():
        ip = ip_map.get(key, "unknown")
        arp_table[mac] = ip
    return arp_table


def get_snmp_fdb_table(device_ip, community):
    fdb_table = {}
    for (errorIndication,
         errorStatus,
         errorIndex,
         varBinds) in nextCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=0),
        UdpTransportTarget((device_ip, 161), timeout=2, retries=3),
        ContextData(),
        ObjectType(ObjectIdentity('BRIDGE-MIB', 'dot1dTpFdbPort')),
        lexicographicMode=False):
        if errorIndication:
            print("FDB:", errorIndication)
            break
        elif errorStatus:
            print('FDB Error: %s at %s' % (errorStatus.prettyPrint(),
                                           errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
            break
        else:
            for varBind in varBinds:
                oid, port_val = varBind
                oid_str = oid.prettyPrint()
                parts = oid_str.split('.')
                mac_parts = parts[-6:]
                try:
                    mac = ':'.join(['%02x' % int(octet) for octet in mac_parts])
                except Exception as e:
                    continue
                fdb_table[mac] = str(int(port_val))
    return fdb_table


def query_neighbor_info(device_ip, community):
    arp_table = get_snmp_arp_table(device_ip, community)
    fdb_table = get_snmp_fdb_table(device_ip, community)
    neighbor_info = {}
    for mac, port in fdb_table.items():
        info = {"mac": mac, "ip": arp_table.get(mac, "unknown")}
        if port not in neighbor_info:
            neighbor_info[port] = []
        neighbor_info[port].append(info)
    for mac, ip in arp_table.items():
        if mac not in fdb_table:
            print(f"Uwaga: MAC {mac} ({ip}) pojawił się w ARP, ale nie ma wpisu w FDB.")
    return neighbor_info


#############################################
# Funkcje przetwarzające szablon diagramu
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
    Szuka w szablonie wszystkich <mxCell> z parent ustawionym na stałą (np. "wdXZIc1yJ1iBE2bjXRa4-1")
    oraz których wartość (value) to numer portu.
    """
    port_cells = []
    for cell in root.iter("mxCell"):
        if cell.get("parent") == "wdXZIc1yJ1iBE2bjXRa4-1":
            val = cell.get("value", "").strip()
            if val and val.isdigit():
                port_cells.append(cell)
    port_cells.sort(key=lambda c: int(c.get("value").strip()))
    return port_cells


def add_api_info_to_template(tree: ET.ElementTree, api: LibreNMSAPI, device_info: dict) -> None:
    """
    - Dodaje informację o urządzeniu (device_id, hostname, sysName) w wybranym miejscu.
    - Dla każdego portu pobranego z API:
         * Koloruje komórkę portu (zielony dla "up", czerwony w przeciwnym przypadku).
         * Rysuje pionową linię wychodzącą dokładnie ze środka portu.
           Dla portów znajdujących się w górnym rzędzie (przyjmujemy, że center_y < switch_mid_y) linia wychodzi w dół,
           natomiast dla portów w dolnym rzędzie (center_y >= switch_mid_y) linia wychodzi w górę – czyli odejmujemy wartość.
         * Na linii wyświetlamy tylko wartości pobrane z API (ifName, ifIndex, ifPhysAddress, ifAlias) bez dodatkowych etykiet,
           a tekst i linia mają kolor biały.
    """
    device_id = device_info.get("device_id")
    hostname = device_info.get("hostname", "unknown")
    sysName = device_info.get("sysName", "unknown")

    # Pobieramy korzeń dokumentu
    root = tree.getroot()
    root_cell = root.find(".//root")
    if root_cell is None:
        print("Nie znaleziono elementu 'root' w szablonie.")
        return

    # Dodaj informację o urządzeniu – umieszczamy ją np. nad przełącznikiem
    device_label = f"device_id: {device_id}\nhostname: {hostname}\nsysName: {sysName}"
    device_info_cell = ET.SubElement(
        root_cell,
        "mxCell",
        {
            "id": "device_info",
            "value": device_label,
            "style": "text;strokeColor=none;fillColor=none;align=center;verticalAlign=top;fontSize=14;fontColor=#000000;",
            "vertex": "1",
            "parent": "1"
        }
    )
    ET.SubElement(
        device_info_cell,
        "mxGeometry",
        {
            "x": "200",
            "y": "20",
            "width": "180",
            "height": "60",
            "as": "geometry"
        }
    )

    # Pobieramy dane portów z API oraz komórki portów ze szablonu
    ports = api.get_ports(str(device_id))
    port_cells = find_port_cells(root)
    if not port_cells:
        print("Brak portów w szablonie.")
        return

    count = min(len(port_cells), len(ports))
    print(f"Przetwarzam {count} portów (Szablon: {len(port_cells)}, API: {len(ports)})")

    # Ustal stałą wartość określającą środek switcha – dostosuj tę wartość do Twojego diagramu.
    switch_mid_y = 28  # przykładowa wartość; zmień ją, aby odpowiadała rzeczywistemu środkowi przełącznika

    # Długość linii (w pikselach)
    line_length = 50

    for i in range(count):
        api_port = ports[i]
        port_cell = port_cells[i]
        port_number_str = port_cell.get("value", "").strip()

        # Kolorujemy port: zielony, jeśli ifOperStatus zawiera "up", inaczej czerwony
        status = api_port.get("ifOperStatus", "").lower()
        color = "#00FF00" if "up" in status else "#FF0000"
        curr_style = port_cell.get("style", "")
        if "fillColor=" in curr_style:
            new_style = re.sub(r"fillColor=[^;]+", f"fillColor={color}", curr_style)
        else:
            if not curr_style.endswith(";"):
                curr_style += ";"
            new_style = curr_style + f"fillColor={color};"
        port_cell.set("style", new_style)

        # Pobieramy współrzędne portu
        geom = port_cell.find("mxGeometry")
        if geom is None:
            continue
        try:
            x = float(geom.get("x", "0"))
            y = float(geom.get("y", "0"))
            w = float(geom.get("width", "40"))
            h = float(geom.get("height", "40"))
        except Exception as e:
            print(f"Błąd odczytu geometrii portu {port_number_str}: {e}")
            continue

        # Obliczamy środek portu – to punkt, z którego wychodzi linia
        center_x = x + w / 2
        center_y = y + h / 2
        start_x = center_x
        start_y = center_y

        try:
            port_num = int(port_number_str)
        except:
            port_num = i + 1

        # Ustalanie kierunku linii przy użyciu switch_mid_y:
        # Jeśli środek portu jest w górnym rzędzie (center_y < switch_mid_y), linia wychodzi w dół (y rośnie).
        # Jeśli port jest w dolnym rzędzie (center_y >= switch_mid_y), linia wychodzi w górę (y maleje).
        if center_y < switch_mid_y:
            end_y = center_y + line_length
        else:
            end_y = center_y - line_length
        end_x = center_x  # linia pionowa

        # Budujemy tekst – wyświetlamy tylko wartości pobrane z API (bez etykiet)
        label_text = (
            f"{api_port.get('ifName', '')}\n"
            f"{api_port.get('ifIndex', '')}\n"
            f"{api_port.get('ifPhysAddress', '')}\n"
            f"{api_port.get('ifAlias', '')}"
        )

        # Tworzymy krawędź (edge) reprezentującą linię odniesienia z opisem
        line_cell_id = f"line_{i}"
        line_cell = ET.SubElement(
            root_cell,
            "mxCell",
            {
                "id": line_cell_id,
                "value": label_text,
                "style": (
                    "edgeStyle=none;"
                    "endArrow=none;"
                    "strokeWidth=1;"
                    "strokeColor=#ffffff;"  # biała linia
                    "align=center;"
                    "verticalAlign=middle;"
                    "labelPosition=middle;"
                    "verticalLabelPosition=middle;"
                    "fontSize=10;"
                    "fontColor=#ffffff;"  # biały tekst
                ),
                "edge": "1",
                "parent": "1",
                "source": port_cell.get("id"),
                "target": ""
            }
        )
        line_geom = ET.SubElement(
            line_cell,
            "mxGeometry",
            {
                "relative": "1",
                "as": "geometry"
            }
        )
        ET.SubElement(
            line_geom,
            "mxPoint",
            {
                "as": "sourcePoint",
                "x": str(start_x),
                "y": str(start_y)
            }
        )
        ET.SubElement(
            line_geom,
            "mxPoint",
            {
                "as": "targetPoint",
                "x": str(end_x),
                "y": str(end_y)
            }
        )
        print(f"Port {port_number_str} -> status={status}, linia {'w dół' if center_y < switch_mid_y else 'w górę'}")


def build_diagram_for_device_info(api: LibreNMSAPI, device_info: dict) -> str:
    tree = load_template()
    if tree is None:
        return ""
    add_api_info_to_template(tree, api, device_info)
    return ET.tostring(tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
