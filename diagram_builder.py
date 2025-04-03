# diagram_builder.py
import json
import re
import xml.etree.ElementTree as ET
from librenms_api import LibreNMSAPI

# Importujemy pysnmp – upewnij się, że jest zainstalowany: pip install pysnmp
from pysnmp.hlapi import *

TEMPLATE_FILE = "switch.drawio"


#############################################
# Funkcje SNMP – pobierające rzeczywiste dane
#############################################
def get_snmp_arp_table(device_ip, community):
    """
    Pobiera tablicę ARP przy użyciu SNMP (IP-MIB: ipNetToMediaTable).
    Zwraca słownik mapujący MAC -> IP.
    Dla każdego wiersza w tabeli:
      - OID ipNetToMediaNetAddress (.1.3.6.1.2.1.4.22.1.3) zawiera adres IP,
      - OID ipNetToMediaPhysAddress (.1.3.6.1.2.1.4.22.1.2) zawiera adres MAC.
    Łączymy te dane po wspólnym identyfikatorze (instancji).
    """
    ip_map = {}
    # Pobieramy adresy IP z ipNetToMediaNetAddress
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
                # Zakładamy, że ostatnie 5 liczb tworzy instancję: ifIndex + 4-octetowy adres IP.
                key = tuple(parts[-5:])
                ip_map[key] = str(ip_val)

    mac_map = {}
    # Pobieramy adresy MAC z ipNetToMediaPhysAddress
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
                key = tuple(parts[-5:])  # identyfikator taki sam jak dla adresów IP
                # Konwertujemy mac_val (OctetString) do postaci hex, np. "00:11:22:33:44:55"
                mac = ':'.join(['%02x' % b for b in bytes(mac_val)])
                mac_map[key] = mac

    arp_table = {}
    for key, mac in mac_map.items():
        ip = ip_map.get(key, "unknown")
        arp_table[mac] = ip
    return arp_table


def get_snmp_fdb_table(device_ip, community):
    """
    Pobiera tablicę FDB (dot1dTpFdbTable) przy użyciu SNMP (BRIDGE-MIB: dot1dTpFdbPort).
    Zwraca słownik mapujący MAC -> numer portu (jako string).
    ODCZYTUJEMY OID: .1.3.6.1.2.1.17.4.3.1.2.
    Indeks OID zawiera adres MAC w postaci sześciu liczb.
    """
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
                # Ostatnie 6 liczb OID stanowią adres MAC (w formie dziesiętnej)
                mac_parts = parts[-6:]
                try:
                    mac = ':'.join(['%02x' % int(octet) for octet in mac_parts])
                except Exception as e:
                    continue
                fdb_table[mac] = str(int(port_val))
    return fdb_table


def query_neighbor_info(device_ip, community):
    """
    Łączy dane z ARP i FDB, tworząc mapowanie:
      klucz: numer portu (jako string),
      wartość: lista słowników z informacjami o sąsiednim urządzeniu (MAC oraz IP).
    Jeśli w tablicy ARP pojawi się MAC, którego nie mamy w FDB, wypisujemy ostrzeżenie.
    """
    arp_table = get_snmp_arp_table(device_ip, community)
    fdb_table = get_snmp_fdb_table(device_ip, community)
    neighbor_info = {}

    # Iterujemy po wpisach FDB – to daje nam powiązanie MAC -> port
    for mac, port in fdb_table.items():
        info = {"mac": mac, "ip": arp_table.get(mac, "unknown")}
        if port not in neighbor_info:
            neighbor_info[port] = []
        neighbor_info[port].append(info)

    # Wypisujemy ostrzeżenie, jeśli w ARP pojawił się MAC, którego nie mamy w FDB
    for mac, ip in arp_table.items():
        if mac not in fdb_table:
            print(f"Uwaga: MAC {mac} ({ip}) pojawił się w ARP, ale nie ma wpisu w FDB.")
    return neighbor_info


#######################################
# Funkcje przetwarzające szablon diagramu
#######################################
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
    Szuka wszystkich <mxCell> z atrybutem 'parent' równym "wdXZIc1yJ1iBE2bjXRa4-1"
    oraz posiadających niepusty atrybut 'value'.
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
    koloruje symbole portów w szablonie (zielony dla "up", czerwony dla innych)
    oraz wypisuje w konsoli informacje o sąsiedztwie pobrane przez SNMP (ARP/FDB).
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

    # Pobieramy dane sąsiedztwa przez SNMP.
    # Załóżmy, że adres IP switcha oraz SNMP community są znane – np. z konfiguracji.
    device_ip = "192.168.1.1"  # przykładowy adres IP switcha
    snmp_community = "public"
    neighbor_info = query_neighbor_info(device_ip, snmp_community)
    print("Dane sąsiedztwa (SNMP):")
    print(json.dumps(neighbor_info, indent=2, ensure_ascii=False))

    count = min(len(port_cells), len(ports))
    print(f"Przetwarzam {count} portów (Szablon: {len(port_cells)}, API: {len(ports)})")

    for i in range(count):
        api_port = ports[i]
        port_cell = port_cells[i]
        port_number = port_cell.get("value", "").strip()  # numer portu jako string

        # Kolorowanie portu – jeśli status zawiera "up", kolor zielony, w przeciwnym razie czerwony
        status = api_port.get("ifOperStatus", "").lower()
        if "up" in status:
            new_color = "#00FF00"
        else:
            new_color = "#FF0000"

        current_style = port_cell.get("style", "")
        if "fillColor=" in current_style:
            new_style = re.sub(r"fillColor=[^;]+", f"fillColor={new_color}", current_style)
        else:
            if not current_style.endswith(";"):
                current_style += ";"
            new_style = current_style + f"fillColor={new_color};"
        port_cell.set("style", new_style)
        print(f"Port {port_number} -> status: {status}, kolor: {new_color}")

        # Wypisujemy informacje o sąsiedztwie dla danego portu
        neighbors = neighbor_info.get(port_number, [])
        if neighbors:
            for neighbor in neighbors:
                print(f"Port {port_number} sąsiad: MAC: {neighbor['mac']}, IP: {neighbor['ip']}")
        else:
            print(f"Port {port_number} nie posiada danych o sąsiedztwie (SNMP ARP/FDB).")


def build_diagram_for_device_id(api: LibreNMSAPI, device_id: str) -> str:
    """
    Buduje diagram dla danego urządzenia na podstawie portów pobranych z API.
    Ładuje szablon switch.drawio, uzupełnia go o dane z API (kolorowanie portów oraz analiza sąsiedztwa)
    i zwraca diagram jako XML string.
    """
    tree = load_template()
    if tree is None:
        return ""
    add_api_info_to_template(tree, api, device_id)
    return ET.tostring(tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
