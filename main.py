# main.py

import os
import json
import xml.etree.ElementTree as ET
from config import get_config
from diagram_builder import add_api_info_to_template
from librenms_api import LibreNMSAPI
from drawio_generator import DrawioXMLGenerator

def load_ip_list(file_path="ip_list.txt"):
    """
    Ładuje listę adresów IP z pliku (jeden adres na linię).
    """
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

def main():
    try:
        config = get_config()
    except ValueError as e:
        print(e)
        return

    api = LibreNMSAPI(config["base_url"], config["api_key"])
    ip_list = load_ip_list()
    if not ip_list:
        print("Brak adresów IP do analizy w pliku ip_list.txt")
        return

    # Pobieramy wszystkie urządzenia z LibreNMS
    devices = api.get_devices()

    # Tworzymy globalny diagram, do którego będą doklejane wszystkie urządzenia
    generator = DrawioXMLGenerator()
    global_tree = ET.ElementTree(generator.root)

    # Ustalanie układu – parametry do dynamicznego rozmieszczania
    devices_per_row = 3               # liczba urządzeń w jednym wierszu
    margin_between_devices_x = 40     # margines pomiędzy urządzeniami poziomo
    margin_between_rows_y = 80        # margines pomiędzy wierszami

    # Pozycja startowa
    offset_x = 0
    offset_y = 0
    current_row_max_height = 0  # zapamiętuje maksymalną wysokość w bieżącym wierszu

    # Licznik urządzeń (do obliczania pozycji w siatce)
    device_counter = 0

    for ip in ip_list:
        print(f"\n--- Przetwarzanie urządzenia o IP: {ip} ---")
        target_device = None
        for device in devices:
            # Wyszukiwanie urządzenia po adresie IP lub fragmencie w hostname
            if ip == device.get("ip") or ip in device.get("hostname", ""):
                target_device = device
                break

        if not target_device:
            print(f"Nie znaleziono urządzenia o IP {ip}.")
            continue

        print("Wybrane urządzenie:")
        print(json.dumps(target_device, indent=2, ensure_ascii=False))
        device_counter += 1

        # Wywołanie funkcji add_api_info_to_template, która teraz zwraca krotkę (width, height)
        device_width, device_height = add_api_info_to_template(
            global_tree,
            api,
            target_device,
            device_counter,
            offset_x,
            offset_y
        )

        # Aktualizujemy offset_x do następnego urządzenia w wierszu
        offset_x += device_width + margin_between_devices_x

        # Uaktualniamy maksymalną wysokość w bieżącym wierszu
        if device_height > current_row_max_height:
            current_row_max_height = device_height

        # Po ustawieniu określonej liczby urządzeń w wierszu, przechodzimy do nowego wiersza
        if device_counter % devices_per_row == 0:
            offset_x = 0
            offset_y += current_row_max_height + margin_between_rows_y
            current_row_max_height = 0

    # Zapisujemy finalny diagram do pliku
    output_file = "network_diagram.drawio"
    with open(output_file, "w", encoding="utf-8") as f:
        diagram_xml = ET.tostring(global_tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
        f.write(diagram_xml)
    print(f"\nDiagram zapisany jako {output_file}")

if __name__ == "__main__":
    main()
