#!/usr/bin/env python3

# main_app.py
import sys
import time
import argparse
import pprint # Do debugowania
import os

# --- Importy z naszych modułów ---
import config_loader
import file_io
from librenms_client import LibreNMSAPI
import data_processing
import discovery # Poprawiony import
import drawio_base
import drawio_layout
import drawio_device_builder
import drawio_utils # Dodano import utils, może być potrzebny

# --- Stałe ---
IP_LIST_FILE = "ip_list.txt"
DEVICE_CREDENTIALS_FILE = "device_credentials.json"
CONNECTIONS_TXT_FILE = "connections.txt"
CONNECTIONS_JSON_FILE = "connections.json"
DIAGRAM_TEMPLATE_FILE = "switch.drawio"
DIAGRAM_OUTPUT_FILE = "network_diagram.drawio"

# --- Funkcje pomocnicze ---
def find_device_in_list(ip_or_host, all_devices_list):
    """Wyszukuje urządzenie w liście pobranej z API."""
    target_device = None
    # Szukanie po IP
    for d in all_devices_list:
        if d.get("ip") == ip_or_host:
            return d # Zwróć pierwszy pasujący po IP

    # Szukanie po hostname (dokładne dopasowanie)
    for d in all_devices_list:
        if d.get("hostname", "").lower() == ip_or_host.lower():
            return d # Zwróć pierwszy pasujący po hostname

    # Szukanie po hostname (częściowe dopasowanie)
    found_partial = [d for d in all_devices_list if d.get("hostname") and ip_or_host.lower() in d.get("hostname", "").lower()]
    if len(found_partial) == 1:
        return found_partial[0]
    elif len(found_partial) > 1:
        print(f"  ⚠ Znaleziono wiele urządzeń pasujących częściowo do '{ip_or_host}': {[d.get('hostname') for d in found_partial]}. Traktuję jako nieznalezione.")
        return None

    return None # Nie znaleziono


# --- Główne funkcje wykonawcze ---

def run_discovery(config, api_client, ip_list_path, creds_path, conn_txt_path, conn_json_path):
    """Wykonuje część aplikacji odpowiedzialną za odkrywanie połączeń."""
    print("\n=== Rozpoczynanie Fazy Odkrywania Połączeń ===")
    start_time = time.time()

    device_credentials = config_loader.load_device_credentials(creds_path)

    print("[Odkrywanie 1/4] Budowanie mapy MAC...")
    phys_map = data_processing.build_phys_mac_map(api_client)
    if not phys_map:
        print("  Ostrzeżenie: Nie udało się zbudować mapy MAC.")

    print(f"[Odkrywanie 2/4] Wczytywanie listy urządzeń z {ip_list_path}...")
    target_ips_or_hosts = file_io.load_ip_list(ip_list_path)
    if not target_ips_or_hosts:
        print("  Brak urządzeń do przetworzenia w fazie odkrywania.")
        return

    print("[Odkrywanie 3/4] Przetwarzanie urządzeń...")
    all_found_connections = []
    all_devices_from_api = api_client.get_devices()
    if not all_devices_from_api:
         print("⚠ Nie udało się pobrać listy urządzeń z API. Przerywam odkrywanie.")
         return

    processed_count = 0
    for ip_or_host in target_ips_or_hosts:
        processed_count += 1
        print(f"\n--- Odkrywanie dla ({processed_count}/{len(target_ips_or_hosts)}): {ip_or_host} ---")

        target_device = find_device_in_list(ip_or_host, all_devices_from_api)
        if not target_device or not target_device.get("device_id"):
            print(f"  ⚠ Nie znaleziono urządzenia '{ip_or_host}' w LibreNMS lub brak jego device_id. Pomijam.")
            continue

        dev_id = target_device['device_id']
        dev_host = target_device.get('hostname')
        dev_ip = target_device.get('ip')
        current_host_identifier = dev_host or dev_ip
        print(f"  Znaleziono urządzenie: {current_host_identifier} (ID: {dev_id})")

        primary_id = dev_host or dev_ip
        secondary_id = dev_ip if dev_host and dev_ip != dev_host else None
        specific_snmp_comm, comm_source = config_loader.get_specific_snmp_community(
            device_credentials, config["default_snmp_comm"], primary_id, secondary_id
        )
        if specific_snmp_comm and comm_source != "brak": print(f"  Używam SNMP community (źródło: {comm_source})") # Skrócono log
        elif comm_source == "brak": print(f"  ⓘ Brak community SNMP dla tego urządzenia.")

        idx2name = data_processing.build_ifindex_to_name_map(api_client, str(dev_id))
        # if not idx2name and specific_snmp_comm: print("  ⚠ Nie udało się zbudować mapy ifIndex->nazwa.") # Mniej gadatliwe

        device_connections = []
        # Kolejność wywołań metod odkrywania
        if specific_snmp_comm: device_connections.extend(discovery.find_via_lldp_cdp_snmp(target_device, specific_snmp_comm, idx2name))
        device_connections.extend(discovery.find_via_api_fdb(api_client, phys_map, target_device))
        if specific_snmp_comm: device_connections.extend(discovery.find_via_snmp_fdb(phys_map, target_device, specific_snmp_comm, idx2name))
        if specific_snmp_comm: device_connections.extend(discovery.find_via_qbridge_snmp(phys_map, target_device, specific_snmp_comm, idx2name))
        if config["cli_username"] and config["cli_password"]:
             target_for_cli = dev_host or dev_ip
             if target_for_cli: device_connections.extend(discovery.find_via_cli(target_for_cli, config["cli_username"], config["cli_password"]))
             else: print("  ⚠ CLI: Brak identyfikatora (hostname/IP) do próby połączenia CLI.")
        if specific_snmp_comm: device_connections.extend(discovery.find_via_arp_snmp(phys_map, target_device, specific_snmp_comm, idx2name))

        if device_connections:
            print(f"  ✓ Znaleziono {len(device_connections)} potencjalnych połączeń dla {current_host_identifier}.")
            all_found_connections.extend(device_connections)
        else:
            print(f"  ❌ Nie wykryto połączeń dla {current_host_identifier}.")

    print("\n[Odkrywanie 4/4] Zapisywanie wyników odkrywania...")
    if not all_found_connections:
        print("  Nie znaleziono żadnych połączeń w całej sieci.")
    else:
        final_connections = data_processing.deduplicate_connections(all_found_connections)
        print(f"  Po deduplikacji: {len(final_connections)} unikalnych połączeń.")
        file_io.save_connections_txt(final_connections, conn_txt_path)
        file_io.save_connections_json(final_connections, conn_json_path)

    end_time = time.time()
    print(f"=== Zakończono Fazę Odkrywania Połączeń (czas: {end_time - start_time:.2f} sek.) ===")


def run_diagram_generation(config, api_client, ip_list_path, template_path, output_path):
    """Wykonuje część aplikacji odpowiedzialną za generowanie diagramu."""
    print("\n=== Rozpoczynanie Fazy Generowania Diagramu ===")
    start_time = time.time()

    print(f"[Diagram 1/4] Wczytywanie listy urządzeń z {ip_list_path}...")
    target_ips_or_hosts = file_io.load_ip_list(ip_list_path)
    if not target_ips_or_hosts:
         print("  Brak urządzeń do wygenerowania diagramu.")
         return

    print("[Diagram 2/4] Pobieranie listy urządzeń z LibreNMS API...")
    # Pobierz tylko niezbędne dane do identyfikacji i rysowania
    all_devices_from_api = api_client.get_devices(columns="device_id,hostname,ip,sysName") # Dodano sysName jeśli jest potrzebne
    if not all_devices_from_api:
         print("⚠ Nie udało się pobrać listy urządzeń z API. Przerywam generowanie diagramu.")
         return

    print("[Diagram 3/4] Inicjalizacja generatora diagramu Draw.io...")
    generator = drawio_base.DrawioXMLGenerator()
    global_root = generator.get_root_element()

    print("[Diagram 4/4] Przetwarzanie urządzeń i budowanie diagramu...")
    device_details_for_layout = []
    max_template_width, max_template_height = 0, 0 # Maksymalne wymiary szablonów

    processed_count = 0
    print("  Krok 4a: Przygotowanie szablonów i zbieranie informacji...")
    for ip_or_host in target_ips_or_hosts:
        processed_count += 1
        target_device_info = find_device_in_list(ip_or_host, all_devices_from_api)

        if not target_device_info:
            print(f"  Nie znaleziono urządzenia '{ip_or_host}'. Pomijam w diagramie.")
            continue

        # Sprawdź czy mamy device_id
        if not target_device_info.get("device_id"):
             print(f"  Urządzenie '{ip_or_host}' nie ma device_id. Pomijam w diagramie.")
             continue

        template_cells, t_width, t_height = drawio_device_builder.load_and_prepare_template(
            template_path, processed_count
        )
        if template_cells is None:
            print(f"  Nie udało się przygotować szablonu dla {ip_or_host}. Pomijam.")
            continue

        device_details_for_layout.append({
            "info": target_device_info,
            "template_cells": template_cells,
            "width": t_width,
            "height": t_height,
            "index": processed_count
        })
        max_template_width = max(max_template_width, t_width)
        max_template_height = max(max_template_height, t_height)

    if not device_details_for_layout:
         print("  Brak poprawnie przetworzonych urządzeń do umieszczenia na diagramie.")
         return

    print(f"  Krok 4b: Obliczanie layoutu dla {len(device_details_for_layout)} urządzeń (max wymiar: {max_template_width}x{max_template_height})...")
    layout_positions = drawio_layout.calculate_grid_layout(
        len(device_details_for_layout), max_template_width, max_template_height
    )

    print("  Krok 4c: Dodawanie urządzeń do diagramu...")
    for i, device_data in enumerate(device_details_for_layout):
        # Przekazujemy pełne device_info i klienta API do funkcji rysującej
        drawio_device_builder.add_device_to_diagram(
            global_root,
            device_data["template_cells"],
            device_data["width"], # Przekaż rzeczywiste wymiary tego szablonu
            device_data["height"],
            device_data["info"],
            api_client, # Przekazujemy klienta API
            layout_positions[i],
            device_data["index"]
        )

    # Zapisz diagram
    file_io.save_diagram_xml(generator.get_tree(), output_path)

    end_time = time.time()
    print(f"=== Zakończono Fazę Generowania Diagramu (czas: {end_time - start_time:.2f} sek.) ===")


# --- Główny blok wykonawczy ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Narzędzie do odkrywania połączeń sieciowych i generowania diagramów Draw.io.")
    parser.add_argument("--discover", action="store_true", help="Uruchom tylko fazę odkrywania połączeń.")
    parser.add_argument("--diagram", action="store_true", help="Uruchom tylko fazę generowania diagramu.")
    parser.add_argument("--ip-list", default=IP_LIST_FILE, help=f"Ścieżka do pliku z listą IP/Hostname (domyślnie: {IP_LIST_FILE}).")
    parser.add_argument("--creds-json", default=DEVICE_CREDENTIALS_FILE, help=f"Ścieżka do pliku JSON z danymi SNMP (domyślnie: {DEVICE_CREDENTIALS_FILE}).")
    parser.add_argument("--conn-txt", default=CONNECTIONS_TXT_FILE, help=f"Ścieżka do zapisu pliku .txt z połączeniami (domyślnie: {CONNECTIONS_TXT_FILE}).")
    parser.add_argument("--conn-json", default=CONNECTIONS_JSON_FILE, help=f"Ścieżka do zapisu pliku .json z połączeniami (domyślnie: {CONNECTIONS_JSON_FILE}).")
    parser.add_argument("--template", default=DIAGRAM_TEMPLATE_FILE, help=f"Ścieżka do pliku szablonu .drawio (domyślnie: {DIAGRAM_TEMPLATE_FILE}).")
    parser.add_argument("--diagram-out", default=DIAGRAM_OUTPUT_FILE, help=f"Ścieżka do zapisu diagramu .drawio (domyślnie: {DIAGRAM_OUTPUT_FILE}).")
    parser.add_argument("--no-verify-ssl", action="store_true", help="Wyłącz weryfikację certyfikatu SSL dla API LibreNMS.")


    args = parser.parse_args()

    run_discovery_flag = args.discover
    run_diagram_flag = args.diagram
    if not run_discovery_flag and not run_diagram_flag:
        print("Nie podano flag --discover ani --diagram. Domyślnie uruchamiam obie fazy.")
        run_discovery_flag = True
        run_diagram_flag = True

    print("--- Uruchamianie Aplikacji Mapowania Sieci ---")
    app_start_time = time.time()

    try:
        env_config = config_loader.get_env_config()
    except ValueError as e:
        print(f"Błąd krytyczny konfiguracji .env: {e}")
        sys.exit(1)
    except Exception as e:
         print(f"Nieoczekiwany błąd podczas ładowania konfiguracji .env: {e}")
         sys.exit(1)

    # Inicjalizacja klienta API z opcją SSL
    api = LibreNMSAPI(
        env_config["base_url"],
        env_config["api_key"],
        verify_ssl=(not args.no_verify_ssl) # verify_ssl jest True jeśli NIE podano flagi --no-verify-ssl
    )

    if run_discovery_flag:
        run_discovery(
            env_config, api, args.ip_list, args.creds_json, args.conn_txt, args.conn_json
        )

    if run_diagram_flag:
        # Sprawdź czy szablon istnieje przed uruchomieniem diagramu
        if not os.path.exists(args.template):
             print(f"⚠ Błąd krytyczny: Plik szablonu '{args.template}' nie istnieje. Przerywam generowanie diagramu.")
        else:
             run_diagram_generation(
                 env_config, api, args.ip_list, args.template, args.diagram_out
             )

    app_end_time = time.time()
    print(f"\n--- Zakończono działanie aplikacji. Całkowity czas: {app_end_time - app_start_time:.2f} sek. ---")