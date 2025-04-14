import os
import json
from config import get_config
from diagram_builder import build_diagram_for_device_id
from librenms_api import LibreNMSAPI


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

    # Pobierz raz wszystkie urządzenia (aby nie wykonywać zapytania dla każdego IP osobno)
    devices = api.get_devices()
    print("Znalezione urządzenia:")
    print(json.dumps(devices, indent=2, ensure_ascii=False))

    for target_ip in ip_list:
        print(f"\n--- Przetwarzanie urządzenia o IP: {target_ip} ---")
        # Wyszukaj urządzenie o podanym adresie IP
        target_device = None
        for device in devices:
            if target_ip == device.get("ip") or target_ip in device.get("hostname", ""):
                target_device = device
                break

        if not target_device:
            print(f"Nie znaleziono urządzenia o adresie IP {target_ip}.")
            continue

        print("Wybrane urządzenie:")
        print(json.dumps(target_device, indent=2, ensure_ascii=False))
        device_id = target_device.get("device_id")

        # Wywołaj funkcję budującą diagram dla znalezionego device_id
        diagram_xml = build_diagram_for_device_id(api, str(device_id))
        if diagram_xml:
            output_file = f"network_diagram_{target_ip.replace('.', '_')}.drawio"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(diagram_xml)
            print(f"Diagram dla urządzenia o IP {target_ip} zapisany jako {output_file}")
        else:
            print(f"Nie udało się wygenerować diagramu dla urządzenia o IP {target_ip}")


if __name__ == "__main__":
    main()
