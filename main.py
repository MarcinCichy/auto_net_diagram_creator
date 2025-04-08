import os
import time
import xml.etree.ElementTree as ET
from config import get_config
from librenms_api import LibreNMSAPI
from diagram_builder import (
    infer_connections_by_mac,  # Używamy funkcji infer_connections_by_mac
    load_template_switch_group, copy_and_modify_switch,
    create_device_label, create_connection_edge, add_port_status_to_template
)

def load_ip_list(file_path="ip_list.txt"):
    ips = []
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    ips.append(line)
    else:
        print(f"Plik {file_path} nie istnieje.")
    return ips

def get_port_name_by_ifindex(ports_list, ifindex):
    if not ports_list:
        return str(ifindex)
    for port in ports_list:
        if port.get('ifIndex') == ifindex:
            return port.get('ifName', str(ifindex))
    return str(ifindex)

def main():
    start_time = time.time()
    try:
        config = get_config()
    except ValueError as e:
        print(f"Błąd konfiguracji: {e}")
        return

    api = LibreNMSAPI(config["base_url"], config["api_key"])
    target_hosts = load_ip_list()
    if not target_hosts:
        print("Plik ip_list.txt jest pusty lub nie istnieje.")
        return

    print(f"Znaleziono {len(target_hosts)} wpisów w ip_list.txt.")

    # Ładowanie szablonu grupy przełącznika z pliku switch.drawio
    print("Ładowanie szablonu grupy przełącznika...")
    template_switch_group = load_template_switch_group()
    if template_switch_group is None:
        print("Nie można kontynuować bez szablonu grupy przełącznika.")
        return

    print("\n--- Etap 1: Zbieranie danych z API (SNMP pomijamy) ---")
    devices_map = {}       # { ip: device_info }
    all_ports_data = {}    # { ip: [port_info, ...] }
    processed_ips = set()

    print("Pobieranie listy urządzeń z LibreNMS...")
    all_devices = api.get_devices()
    if not all_devices:
        print("Nie udało się pobrać listy urządzeń z LibreNMS. Przerwanie.")
        return

    # Zakładamy, że hostname zawiera adres IP
    devices_by_hostname = { dev.get('hostname'): dev for dev in all_devices if dev.get('hostname') }
    print(f"Pobrano {len(all_devices)} urządzeń z LibreNMS.")

    for host in target_hosts:
        print(f"\nPrzetwarzanie wpisu: {host}")
        device_info = devices_by_hostname.get(host)
        if not device_info:
            print(f"  Ostrzeżenie: Nie znaleziono urządzenia {host} w LibreNMS. Pomijanie.")
            continue

        actual_ip = device_info.get("hostname")
        if not actual_ip:
            print(f"  Ostrzeżenie: Brak adresu dla {device_info.get('hostname')}. Pomijanie.")
            devices_map[host] = device_info
            continue

        if actual_ip in processed_ips:
            print(f"  Urządzenie {actual_ip} zostało już przetworzone. Pomijanie.")
            continue

        print(f"  Znaleziono: ID={device_info.get('device_id')}, Hostname={device_info.get('hostname')}, IP={actual_ip}")
        devices_map[actual_ip] = device_info
        processed_ips.add(actual_ip)

        device_id = device_info.get("device_id")
        ports = api.get_ports(device_id)
        all_ports_data[actual_ip] = ports if ports else []
        print(f"  Pobrano {len(all_ports_data[actual_ip])} portów z API.")

    print("\n--- Etap 2: Inferowanie połączeń L2 oparte na adresach MAC z API ---")
    links = infer_connections_by_mac(devices_map, all_ports_data)

    print("\n--- Etap 3: Budowanie pliku Draw.io ---")
    mxfile = ET.Element("mxfile", {"host": "CustomScript", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": "topology_diagram", "name": "Network Topology"})
    mxGraphModel = ET.SubElement(diagram, "mxGraphModel", {
        "dx": "2000", "dy": "1500", "grid": "1", "gridSize": "10", "guides": "1",
        "tooltips": "1", "connect": "1", "arrows": "1", "fold": "1", "page": "1",
        "pageScale": "1", "pageWidth": "2400", "pageHeight": "1800",
        "math": "0", "shadow": "0"
    })
    root_cell = ET.SubElement(mxGraphModel, "root")
    ET.SubElement(root_cell, "mxCell", {"id": "0"})
    ET.SubElement(root_cell, "mxCell", {"id": "1", "parent": "0"})

    global_port_maps = {}
    start_x, start_y = 50, 50
    current_x, current_y = start_x, start_y
    switch_width, switch_height = 835, 60
    x_spacing = 100
    y_spacing = 160
    switches_per_row = 2
    device_count = 0

    print("Dodawanie wizualizacji przełączników do diagramu...")
    for ip in devices_map.keys():
        device_info = devices_map[ip]
        ports = all_ports_data.get(ip, [])
        device_suffix = ip.replace('.', '_')

        copied_switch_group, port_map = copy_and_modify_switch(template_switch_group, device_info, ports, current_x, current_y, device_suffix)
        if copied_switch_group is not None:
            root_cell.append(copied_switch_group)
            global_port_maps[ip] = port_map

            label_cell = create_device_label(device_info, current_x, current_y, device_suffix)
            if label_cell is not None:
                root_cell.append(label_cell)
            print(f"  Dodano przełącznik oraz etykietę dla {ip}")

            device_count += 1
            current_x += switch_width + x_spacing
            if device_count % switches_per_row == 0:
                current_x = start_x
                current_y += switch_height + y_spacing
        else:
            print(f"  Nie udało się utworzyć kopii dla {ip}.")

    print(f"\nDodawanie {len(links)} połączeń do diagramu...")
    edge_counter = 0
    for link in links:
        ip_a, port_a_ifidx, ip_b, port_b_ifidx = link
        # Zakładamy, że używamy numerów portów (ifIndex) jako kluczy w global_port_maps – upewnij się, że porty istnieją
        cell_id_a = global_port_maps.get(ip_a, {}).get(str(port_a_ifidx))
        cell_id_b = global_port_maps.get(ip_b, {}).get(str(port_b_ifidx))
        if not cell_id_a or not cell_id_b:
            print(f"  Nie znaleziono komórek portów dla połączenia między {ip_a} a {ip_b}.")
            continue

        port_a_name = get_port_name_by_ifindex(all_ports_data.get(ip_a), port_a_ifidx)
        port_b_name = get_port_name_by_ifindex(all_ports_data.get(ip_b), port_b_ifidx)
        edge_counter += 1
        connection_elements = create_connection_edge(cell_id_a, cell_id_b, port_a_name, port_b_name, edge_counter)
        for element in connection_elements:
            root_cell.append(element)
        print(f"  Dodano połączenie: {ip_a}[{port_a_name}] <=> {ip_b}[{port_b_name}]")

    output_file = "network_topology_visual.drawio"
    try:
        try:
            ET.indent(mxfile, space="  ", level=0)
        except AttributeError:
            pass
        xml_string = ET.tostring(mxfile, encoding="unicode", method="xml")
        final_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_string
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(final_xml)
        print(f"\nDiagram zapisany jako: {output_file}")
    except Exception as e:
        print(f"Błąd przy zapisie pliku {output_file}: {e}")

    end_time = time.time()
    print(f"\nZakończono przetwarzanie w {end_time - start_time:.2f} sekund.")

if __name__ == "__main__":
    main()
