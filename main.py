import os
import json
from dotenv import load_dotenv
from librenms_api import LibreNMSAPI
from drawio_generator import DrawioXMLGenerator

load_dotenv()  # Ładuje zmienne z pliku .env

def main():
    base_url = os.getenv("base_url")
    api_key = os.getenv("api_key")
    if not base_url or not api_key:
        print("Brak base_url lub api_key w konfiguracji (.env).")
        return

    api = LibreNMSAPI(base_url, api_key)

    # Pobierz wszystkie urządzenia
    devices = api.get_devices()
    print("Znalezione urządzenia:")
    print(json.dumps(devices, indent=2, ensure_ascii=False))

    # Znajdź urządzenie o adresie IP "172.16.16.70"
    target_ip = "172.16.16.70"
    target_device = None
    for device in devices:
        if target_ip == device.get("ip") or target_ip in device.get("hostname", ""):
            target_device = device
            break

    if not target_device:
        print(f"Nie znaleziono urządzenia o adresie IP {target_ip}.")
        return

    print("Wybrane urządzenie:")
    print(json.dumps(target_device, indent=2, ensure_ascii=False))
    device_id = target_device.get("device_id")
    label = target_device.get("hostname", target_ip)

    # Pobierz porty dla wybranego urządzenia przy użyciu endpointu /devices/{device_id}/ports
    ports = api.get_ports(device_id)
    print("Znalezione porty:")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    # Uzupełniamy dane portu o opis (ifAlias) pobrany z kolumny ports (jeśli dostępny)
    for port in ports:
        # Jeżeli pole ifAlias jest puste, możemy pozostawić puste (lub później dodać dodatkowy request)
        # Tutaj przyjmujemy, że jeśliAlias jest już zawarty w danych, to jest to opis.
        if not port.get("ifAlias"):
            port["ifAlias"] = ""  # lub zachowujemy pustą wartość

    print("Zaktualizowane dane portów (z opisami):")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    # Utwórz diagram przy użyciu DrawioXMLGenerator
    generator = DrawioXMLGenerator()
    device_node = generator.add_device(label, x=200, y=50, width=200, height=60)

    # Układ portów – przykładowy układ w jednym rzędzie
    start_x = 100
    start_y = 150
    port_spacing_x = 50

    for i, port in enumerate(ports):
        port_number = i + 1
        port_ifName = port.get("ifName", f"Port {port_number}")
        port_ifAlias = port.get("ifAlias", "")
        if port_ifAlias:
            full_label = f"Port {port_number}: {port_ifName}\n{port_ifAlias}"
        else:
            full_label = f"Port {port_number}: {port_ifName}"
        oper_status = port.get("ifOperStatus", "down")
        used = (oper_status.lower() == "up")
        port_x = start_x + i * port_spacing_x
        port_y = start_y

        generator.add_port(full_label, x=port_x, y=port_y, used=used)
        print(f"Generowany port {port_number}: {full_label} | Status: {oper_status}")

    xml_string = generator.to_string()
    with open("network_diagram.drawio", "w", encoding="utf-8") as f:
        f.write(xml_string)

    print("Diagram zapisany jako network_diagram.drawio")

if __name__ == "__main__":
    main()
