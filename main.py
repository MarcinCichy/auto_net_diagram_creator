import os
import json
from config import get_config
from librenms_api import LibreNMSAPI
from diagram_builder import build_diagram_for_ip

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

    for ip in ip_list:
        print(f"\n--- Przetwarzanie urządzenia o IP: {ip} ---")
        diagram_xml = build_diagram_for_ip(api, ip)
        if diagram_xml:
            output_file = f"network_diagram_{ip.replace('.', '_')}.drawio"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(diagram_xml)
            print(f"Diagram dla urządzenia {ip} zapisany jako {output_file}")
        else:
            print(f"Nie udało się wygenerować diagramu dla urządzenia {ip}")

if __name__ == "__main__":
    main()
