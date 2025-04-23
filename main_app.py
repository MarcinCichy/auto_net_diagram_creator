#!/usr/bin/env python3

# main_app.py
import sys
import time
import argparse
import pprint # Do debugowania i ładnego drukowania mapowań
import os
import xml.etree.ElementTree as ET
import re # Potrzebne do canonical ID i drawio_device_builder

# --- Importy z naszych modułów ---
# Upewnij się, że wszystkie te pliki .py istnieją w tym samym katalogu
import config_loader
import file_io
from librenms_client import LibreNMSAPI # Import klasy
import data_processing
import discovery # Poprawny import
import drawio_base
import drawio_layout
import drawio_device_builder # Importujemy cały moduł
import drawio_utils # Import utils

# --- Stałe ---
IP_LIST_FILE = "ip_list.txt"
DEVICE_CREDENTIALS_FILE = "device_credentials.json"
CONNECTIONS_TXT_FILE = "connections.txt"
CONNECTIONS_JSON_FILE = "connections.json"
DIAGRAM_TEMPLATE_FILE = "switch.drawio"
DIAGRAM_OUTPUT_FILE = "network_diagram.drawio"

# --- Funkcje pomocnicze ---
def find_device_in_list(identifier, all_devices_list):
    """Wyszukuje urządzenie w liście z API po IP lub hostname (ignorując wielkość liter)."""
    if not identifier or not all_devices_list: return None
    # Szukaj po IP
    for d in all_devices_list:
        if d.get("ip") == identifier: return d
    # Szukaj po hostname (dokładne dopasowanie, ignorując wielkość liter)
    if isinstance(identifier, str):
        identifier_lower = identifier.lower()
        for d in all_devices_list:
            hostname_api = d.get("hostname")
            if hostname_api and hostname_api.lower() == identifier_lower: return d
    return None

def get_canonical_identifier(original_identifier, device_info_from_api):
    """Zwraca preferowany (kanoniczny) identyfikator dla urządzenia."""
    if not device_info_from_api: return original_identifier
    hostname = device_info_from_api.get('hostname')
    ip = device_info_from_api.get('ip')
    looks_like_ip = False
    if hostname: looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname))
    if hostname and not looks_like_ip: return hostname
    elif ip: return ip
    else: return original_identifier

# --- Główne funkcje wykonawcze ---

def run_discovery(config, api_client, ip_list_path, creds_path, conn_txt_path, conn_json_path):
    """Wykonuje część aplikacji odpowiedzialną za odkrywanie połączeń."""
    print("\n=== Rozpoczynanie Fazy Odkrywania Połączeń ===")
    start_time = time.time()
    device_credentials = config_loader.load_device_credentials(creds_path)
    print("[Odkrywanie 1/5] Budowanie mapy MAC...")
    phys_map = data_processing.build_phys_mac_map(api_client)
    if not phys_map: print("  Ostrzeżenie: Nie udało się zbudować mapy MAC.")

    print(f"[Odkrywanie 2/5] Wczytywanie listy urządzeń z {ip_list_path}...")
    target_ips_or_hosts = file_io.load_ip_list(ip_list_path)
    if not target_ips_or_hosts:
        print("  Brak urządzeń do przetworzenia w fazie odkrywania.")
        return

    print("[Odkrywanie 3/5] Pobieranie pełnej listy urządzeń z API (dla mapowania)...")
    all_devices_from_api = api_client.get_devices()
    if not all_devices_from_api:
         print("⚠ Nie udało się pobrać listy urządzeń z API. Przerywam odkrywanie.")
         return
    device_lookup_by_ip = {d.get("ip"): d for d in all_devices_from_api if d.get("ip")}
    device_lookup_by_hostname_lower = {d.get("hostname", "").lower(): d for d in all_devices_from_api if d.get("hostname")}

    print("[Odkrywanie 4/5] Przetwarzanie urządzeń i odkrywanie połączeń...")
    all_found_connections_raw = []
    processed_count = 0
    for ip_or_host in target_ips_or_hosts:
        processed_count += 1
        print(f"\n--- Odkrywanie dla ({processed_count}/{len(target_ips_or_hosts)}): {ip_or_host} ---")
        target_device = find_device_in_list(ip_or_host, all_devices_from_api)
        if not target_device or not target_device.get("device_id"):
            print(f"  ⚠ Nie znaleziono urządzenia '{ip_or_host}' w LibreNMS (wg IP/Hostname). Pomijam.")
            continue

        # *** DODANE LOGOWANIE INFORMACJI O URZĄDZENIU ***
        print(f"  DEBUG [Discovery]: Znaleziono info dla '{ip_or_host}' w API:")
        pprint.pprint(target_device) # Wydrukuj cały słownik dla pełnego obrazu
        # *** KONIEC LOGOWANIA ***

        dev_id = target_device['device_id']
        dev_host = target_device.get('hostname')
        dev_ip = target_device.get('ip')
        current_host_identifier = dev_host or dev_ip or ip_or_host
        print(f"  Przetwarzanie jako: {current_host_identifier} (ID: {dev_id})")

        primary_id_lookup = dev_host or dev_ip
        secondary_id_lookup = dev_ip if dev_host and dev_ip != dev_host else None
        specific_snmp_comm, comm_source = config_loader.get_specific_snmp_community(
            device_credentials, config["default_snmp_comm"], primary_id_lookup, secondary_id_lookup
        )
        if specific_snmp_comm and comm_source != "brak": print(f"  Używam SNMP community (źródło: {comm_source})")
        elif comm_source == "brak": print(f"  ⓘ Brak community SNMP dla tego urządzenia.")

        idx2name = data_processing.build_ifindex_to_name_map(api_client, str(dev_id))

        device_connections = []
        # Wywołania metod discovery
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
            all_found_connections_raw.extend(device_connections)
        else:
            print(f"  ❌ Nie wykryto połączeń dla {current_host_identifier}.")

    print("\n[Odkrywanie 5/5] Wzbogacanie danych, normalizacja i zapisywanie wyników...")
    enriched_connections = []
    if all_found_connections_raw:
        print("  Wzbogacanie danych o połączeniach o dodatkowe identyfikatory...")
        for conn_raw in all_found_connections_raw:
            local_original = conn_raw.get('local_host')
            remote_original = conn_raw.get('neighbor_host')

            local_info = device_lookup_by_ip.get(local_original)
            if not local_info and isinstance(local_original, str): local_info = device_lookup_by_hostname_lower.get(local_original.lower())
            remote_info = device_lookup_by_ip.get(remote_original)
            if not remote_info and isinstance(remote_original, str): remote_info = device_lookup_by_hostname_lower.get(remote_original.lower())

            local_canonical = get_canonical_identifier(local_original, local_info)
            remote_canonical = get_canonical_identifier(remote_original, remote_info)

            if str(remote_canonical).lower() == 'null': continue

            enriched_conn = {
                "local_device": local_canonical,
                "local_port": conn_raw.get('local_if'),
                "remote_device": remote_canonical,
                "remote_port": conn_raw.get('neighbor_if'),
                "vlan": conn_raw.get('vlan'),
                "discovery_method": conn_raw.get('via'),
                "local_device_ip": local_info.get('ip') if local_info else None,
                "local_device_hostname": local_info.get('hostname') if local_info else None,
                "remote_device_ip": remote_info.get('ip') if remote_info else None,
                "remote_device_hostname": remote_info.get('hostname') if remote_info else None,
                "remote_device_original": remote_original if not remote_info else None
            }
            enriched_conn = {k: v for k, v in enriched_conn.items() if v is not None}
            enriched_connections.append(enriched_conn)

        print(f"  Zebrano {len(enriched_connections)} wpisów po wzbogaceniu i filtracji 'null'. Deduplikowanie...")
        final_connections = data_processing.deduplicate_connections(enriched_connections)
        print(f"  Po deduplikacji: {len(final_connections)} unikalnych połączeń.")
        file_io.save_connections_txt(final_connections, conn_txt_path)
        file_io.save_connections_json(final_connections, conn_json_path)
    else:
        print("  Nie znaleziono żadnych połączeń w całej sieci.")

    end_time = time.time()
    print(f"=== Zakończono Fazę Odkrywania Połączeń (czas: {end_time - start_time:.2f} sek.) ===")


# Funkcja rysująca połączenia (z poprawionym logowaniem)
def draw_connections(global_root: ET.Element, connections_data: list, port_mappings: dict, all_devices_api_list: list):
    """
    Rysuje linie (krawędzie) między portami urządzeń na diagramie.
    Ulepszono logowanie i wyszukiwanie urządzeń.
    """
    print("\n  Krok 4d: Rysowanie połączeń między urządzeniami...")
    connection_count = 0
    drawn_links = set()
    edge_style = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeWidth=1;endArrow=none;strokeColor=#FF9900;fontSize=8;" # Pomarańczowy

    print(f"INFO: Otrzymano {len(connections_data)} połączeń do przetworzenia.")
    # pprint.pprint(port_mappings) # DEBUG

    missing_devices_logged = set()
    missing_ports_logged = set()

    for i, conn in enumerate(connections_data):
        local_dev = conn.get("local_device") # Oczekujemy tu kanonicznego ID
        local_port_name = conn.get("local_port")
        remote_dev = conn.get("remote_device") # Oczekujemy tu kanonicznego ID
        remote_port_name = conn.get("remote_port")
        vlan = conn.get("vlan")

        if not all([local_dev, local_port_name, remote_dev, remote_port_name]): continue
        if str(remote_dev).lower() == 'null' or local_dev == remote_dev: continue

        local_map = port_mappings.get(local_dev)
        if not local_map and isinstance(local_dev, str): local_map = port_mappings.get(local_dev.lower())
        remote_map = port_mappings.get(remote_dev)
        if not remote_map and isinstance(remote_dev, str): remote_map = port_mappings.get(remote_dev.lower())

        source_cell_id = None
        target_cell_id = None

        if not local_map:
            if local_dev not in missing_devices_logged:
                 print(f"  INFO: Urządzenie lokalne '{local_dev}' nie znalezione na diagramie (prawdopodobnie spoza ip_list.txt).")
                 missing_devices_logged.add(local_dev)
            continue
        if not remote_map:
            if remote_dev not in missing_devices_logged:
                remote_dev_extra_info = ""
                # Użyj dodatkowych pól z conn, jeśli istnieją
                ip_info = conn.get("remote_device_ip")
                host_info = conn.get("remote_device_hostname")
                orig_info = conn.get("remote_device_original")
                details = []
                if host_info and host_info != remote_dev: details.append(f"Hostname={host_info}")
                if ip_info and ip_info != remote_dev: details.append(f"IP={ip_info}")
                if orig_info and orig_info != remote_dev: details.append(f"Oryginalny={orig_info}")
                info_str = f" (Inne ID: {', '.join(details)})" if details else ""
                # Sprawdźmy, czy JEST na diagramie pod inną nazwą
                is_on_diagram = False
                found_dev_info_api = find_device_in_list(remote_dev, all_devices_api_list)
                if found_dev_info_api:
                    api_ip = found_dev_info_api.get('ip')
                    api_host = found_dev_info_api.get('hostname')
                    if api_ip and api_ip in port_mappings: is_on_diagram = True
                    if api_host and api_host in port_mappings: is_on_diagram = True
                    if api_host and api_host.lower() in port_mappings: is_on_diagram = True
                status_str = "(JEST na diagramie pod inną nazwą/IP!)" if is_on_diagram else "(BRAK go na diagramie)"
                print(f"  INFO: Urządzenie zdalne '{remote_dev}' nie narysowane (brak klucza '{remote_dev}' w mapowaniu){info_str} - {status_str}.")
                missing_devices_logged.add(remote_dev)
            continue

        # Wyszukiwanie portów (nadal może wymagać ulepszeń)
        lookup_keys_local = [local_port_name]
        if isinstance(local_port_name, str):
             if local_port_name.isdigit(): lookup_keys_local.append(local_port_name)
             if local_port_name.startswith("Eth"): lookup_keys_local.append("Ethernet" + local_port_name[3:])
        for key in lookup_keys_local:
             source_cell_id = local_map.get(key)
             if source_cell_id: break

        lookup_keys_remote = [remote_port_name]
        if isinstance(remote_port_name, str):
             if remote_port_name.isdigit(): lookup_keys_remote.append(remote_port_name)
             if remote_port_name.startswith("Eth"): lookup_keys_remote.append("Ethernet" + remote_port_name[3:])
        for key in lookup_keys_remote:
             target_cell_id = remote_map.get(key)
             if target_cell_id: break

        if source_cell_id and target_cell_id:
            link_key = tuple(sorted((source_cell_id, target_cell_id)))
            if link_key in drawn_links: continue
            edge_id = f"conn_edge_{i}_{source_cell_id}_{target_cell_id}"
            edge_label = f"VLAN {vlan}" if vlan is not None else ""
            edge_cell = drawio_utils.create_edge_cell(
                edge_id, "1", source_cell_id, target_cell_id, edge_style
            )
            if edge_label:
                 edge_cell.set("value", edge_label)
                 drawio_utils.apply_style_change(edge_cell, "labelBackgroundColor", "#FFFFFF")
                 drawio_utils.apply_style_change(edge_cell, "fontColor", "#000000")
            global_root.append(edge_cell)
            drawn_links.add(link_key)
            connection_count += 1
        else:
             local_port_key = f"{local_dev}:{local_port_name}"
             remote_port_key = f"{remote_dev}:{remote_port_name}"
             if not source_cell_id and local_port_key not in missing_ports_logged:
                  print(f"  WARN: Nie znaleziono portu '{local_port_name}' na narysowanym urządzeniu '{local_dev}'. Dostępne klucze (max 10): {list(local_map.keys())[:10]}...")
                  missing_ports_logged.add(local_port_key)
             if not target_cell_id and remote_port_key not in missing_ports_logged:
                  print(f"  WARN: Nie znaleziono portu '{remote_port_name}' na narysowanym urządzeniu '{remote_dev}'. Dostępne klucze (max 10): {list(remote_map.keys())[:10]}...")
                  missing_ports_logged.add(remote_port_key)

    print(f"  ✓ Narysowano {connection_count} połączeń.")


# Funkcja generująca diagram (zaktualizowana o logikę kanonicznych ID)
def run_diagram_generation(config, api_client, ip_list_path, template_path, output_path, connections_json_path):
    """Wykonuje część aplikacji odpowiedzialną za generowanie diagramu."""
    print("\n=== Rozpoczynanie Fazy Generowania Diagramu ===")
    start_time = time.time()
    print(f"[Diagram 1/5] Wczytywanie listy urządzeń z {ip_list_path}...")
    target_ips_or_hosts = file_io.load_ip_list(ip_list_path)
    if not target_ips_or_hosts: return
    print("[Diagram 2/5] Pobieranie listy urządzeń z LibreNMS API...")
    all_devices_from_api = api_client.get_devices(columns="device_id,hostname,ip,sysName")
    if not all_devices_from_api: return
    print("[Diagram 3/5] Inicjalizacja generatora diagramu Draw.io...")
    generator = drawio_base.DrawioXMLGenerator()
    global_root = generator.get_root_element()
    print("[Diagram 4/5] Przetwarzanie urządzeń i budowanie diagramu...")
    device_details_for_layout = []
    max_template_width, max_template_height = 0, 0
    port_cell_mappings = {} # Słownik na mapowania portów {KANONICZNY_identyfikator_urz: {port_map}}

    processed_count = 0
    print("  Krok 4a: Przygotowanie szablonów i zbieranie informacji...")
    # Mapa do przechowywania kanonicznego ID dla każdego ip_or_host z listy wejściowej
    input_to_canonical_map = {}

    for ip_or_host in target_ips_or_hosts:
        processed_count += 1
        target_device_info = find_device_in_list(ip_or_host, all_devices_from_api)
        if not target_device_info or not target_device_info.get("device_id"):
             print(f"  Pomijam '{ip_or_host}' w diagramie (nie znaleziono lub brak ID).")
             continue

        template_cells, t_width, t_height = drawio_device_builder.load_and_prepare_template(
            template_path, processed_count
        )
        if template_cells is None: continue

        # Ustal KANONICZNY identyfikator dla tego urządzenia
        canonical_id = get_canonical_identifier(ip_or_host, target_device_info)
        input_to_canonical_map[ip_or_host] = canonical_id # Zapamiętaj mapowanie
        # print(f"  Urządzenie wejściowe '{ip_or_host}' mapuje na kanoniczny ID: '{canonical_id}'")

        device_details_for_layout.append({
            "canonical_id": canonical_id, # Zapisz kanoniczny ID
            "info": target_device_info,
            "template_cells": template_cells,
            "width": t_width, "height": t_height, "index": processed_count
        })
        max_template_width = max(max_template_width, t_width)
        max_template_height = max(max_template_height, t_height)

    if not device_details_for_layout: return

    print(f"  Krok 4b: Obliczanie layoutu dla {len(device_details_for_layout)} urządzeń...")
    layout_positions = drawio_layout.calculate_grid_layout(
        len(device_details_for_layout), max_template_width, max_template_height
    )

    print("  Krok 4c: Dodawanie urządzeń do diagramu...")
    for i, device_data in enumerate(device_details_for_layout):
        port_map = drawio_device_builder.add_device_to_diagram(
            global_root,
            device_data["template_cells"],
            device_data["width"], device_data["height"],
            device_data["info"], api_client,
            layout_positions[i], device_data["index"]
        )
        # ZAPIS MAPOWANIA POD KANONICZNYM ID
        if port_map:
            canonical_id = device_data["canonical_id"]
            if canonical_id:
                 port_cell_mappings[canonical_id] = port_map
                 # Dodaj też inne znane ID jako klucze wskazujące na tę samą mapę
                 dev_ip = device_data["info"].get('ip')
                 dev_host = device_data["info"].get('hostname')
                 if dev_ip and dev_ip != canonical_id: port_cell_mappings[dev_ip] = port_map
                 if dev_host and dev_host != canonical_id:
                      port_cell_mappings[dev_host] = port_map
                      port_cell_mappings[dev_host.lower()] = port_map # Również małe litery


    # Krok rysowania połączeń
    print("[Diagram 5/5] Rysowanie połączeń...")
    connections_data = file_io.load_connections_json(connections_json_path)
    if connections_data:
         # Przekaż mapowania i PEŁNĄ listę urządzeń z API do funkcji rysującej
         draw_connections(global_root, connections_data, port_cell_mappings, all_devices_from_api)
    else:
         print("  Brak danych o połączeniach do narysowania.")

    # Zapisz diagram
    file_io.save_diagram_xml(generator.get_tree(), output_path)
    end_time = time.time()
    print(f"=== Zakończono Fazę Generowania Diagramu (czas: {end_time - start_time:.2f} sek.) ===")


# --- Główny blok wykonawczy ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Narzędzie do odkrywania połączeń sieciowych i generowania diagramów Draw.io.")
    # ... (definicje argumentów bez zmian) ...
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
        print(f"Błąd krytyczny konfiguracji .env: {e}"); sys.exit(1)
    except Exception as e:
         print(f"Nieoczekiwany błąd podczas ładowania konfiguracji .env: {e}"); sys.exit(1)

    api = LibreNMSAPI(env_config["base_url"], env_config["api_key"], verify_ssl=(not args.no_verify_ssl))

    if run_discovery_flag:
        run_discovery(env_config, api, args.ip_list, args.creds_json, args.conn_txt, args.conn_json)

    if run_diagram_flag:
        if not os.path.exists(args.template):
             print(f"⚠ Błąd krytyczny: Plik szablonu '{args.template}' nie istnieje. Przerywam generowanie diagramu.")
        elif not os.path.exists(args.conn_json) and not run_discovery_flag:
             print(f"⚠ Ostrzeżenie: Plik połączeń '{args.conn_json}' nie istnieje (a faza odkrywania nie była uruchomiona). Linie nie zostaną narysowane.")
             run_diagram_generation(env_config, api, args.ip_list, args.template, args.diagram_out, args.conn_json)
        else:
             run_diagram_generation(env_config, api, args.ip_list, args.template, args.diagram_out, args.conn_json)

    app_end_time = time.time()
    print(f"\n--- Zakończono działanie aplikacji. Całkowity czas: {app_end_time - app_start_time:.2f} sek. ---")