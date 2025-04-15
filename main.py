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

    # Pobieramy wszystkie urządzenia z LibreNMS (aby nie wykonywać zapytania dla każdego IP osobno)
    devices = api.get_devices()

    # Tworzymy globalny diagram, do którego będą doklejane wszystkie urządzenia
    generator = DrawioXMLGenerator()
    global_tree = ET.ElementTree(generator.root)

    # Ustalanie układu w siatce: liczba urządzeń w jednym wierszu, marginesy pomiędzy
    devices_per_row = 3           # liczba urządzeń w jednym wierszu, dostosuj według potrzeb
    margin_x = 1500               # odstęp poziomy pomiędzy urządzeniami (w pikselach)
    margin_y = 300                # odstęp pionowy pomiędzy urządzeniami (w pikselach)

    device_counter = 0  # numeracja urządzeń

    for ip in ip_list:
        print(f"\n--- Przetwarzanie urządzenia o IP: {ip} ---")
        target_device = None
        for device in devices:
            # Wyszukujemy urządzenie po adresie IP lub podciągu w hostname
            if ip == device.get("ip") or ip in device.get("hostname", ""):
                target_device = device
                break

        if not target_device:
            print(f"Nie znaleziono urządzenia o adresie IP {ip}.")
            continue

        print("Wybrane urządzenie:")
        print(json.dumps(target_device, indent=2, ensure_ascii=False))
        device_counter += 1

        # Obliczamy pozycję w siatce
        row = (device_counter - 1) // devices_per_row
        col = (device_counter - 1) % devices_per_row
        offset_x = col * margin_x
        offset_y = row * margin_y
        print(f"Urządzenie {device_counter} pozycjonowane w siatce: kolumna {col}, wiersz {row} (offset: {offset_x}, {offset_y})")

        # Dodajemy informacje o urządzeniu (na podstawie szablonu) do globalnego diagramu,
        # przekazując obliczony offset. Funkcja add_api_info_to_template odpowiada za
        # dołączenie fragmentu diagramu (dla pojedynczego urządzenia) do globalnego drzewa.
        add_api_info_to_template(global_tree, api, target_device, device_counter, offset_x, offset_y)

    # Zapisujemy finalny diagram z wszystkimi urządzeniami do pliku
    output_file = "network_diagram.drawio"
    with open(output_file, "w", encoding="utf-8") as f:
        diagram_xml = ET.tostring(global_tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
        f.write(diagram_xml)
    print(f"\nDiagram zapisany jako {output_file}")

if __name__ == "__main__":
    main()
