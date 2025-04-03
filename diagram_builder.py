# diagram_builder.py
import json
from librenms_api import LibreNMSAPI
from drawio_generator import DrawioXMLGenerator


def build_diagram_for_ip(api: LibreNMSAPI, target_ip: str):
    # Pobierz wszystkie urządzenia
    devices = api.get_devices()

    # Znajdź urządzenie o zadanym adresie IP
    target_device = None
    for device in devices:
        if target_ip == device.get("ip") or target_ip in device.get("hostname", ""):
            target_device = device
            break

    if not target_device:
        print(f"Nie znaleziono urządzenia o adresie IP {target_ip}.")
        return None

    print("Wybrane urządzenie:")
    print(json.dumps(target_device, indent=2, ensure_ascii=False))

    device_id = target_device.get("device_id")
    label = target_device.get("hostname", target_ip)

    # Pobierz porty dla urządzenia
    ports = api.get_ports(device_id)
    print("Znalezione porty:")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    # Uzupełniamy dane – numerujemy porty oraz łączymy ifName z ifAlias (opis portu)
    for port in ports:
        if not port.get("ifAlias"):
            port["ifAlias"] = ""  # pozostawiamy pusty, jeżeli brak opisu

    print("Zaktualizowane dane portów (z opisami):")
    print(json.dumps(ports, indent=2, ensure_ascii=False))

    # Generujemy diagram
    generator = DrawioXMLGenerator()
    generator.add_device(label, x=200, y=50, width=200, height=60)

    # Układ portów – przykładowo w jednym rzędzie
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

    return generator.to_string()
