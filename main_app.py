# --- main_app.py ---
#!/usr/bin/env python3

import sys
import time
import argparse
import pprint
import os
import xml.etree.ElementTree as ET
import re

# --- Importy z naszych modułów ---
import config_loader
import file_io
from librenms_client import LibreNMSAPI
import data_processing
import discovery
import drawio_base
import drawio_layout
import drawio_device_builder
import drawio_utils

# --- Stałe ---
IP_LIST_FILE = "ip_list.txt"
DEVICE_CREDENTIALS_FILE = "device_credentials.json"
CONNECTIONS_TXT_FILE = "connections.txt"
CONNECTIONS_JSON_FILE = "connections.json"
DIAGRAM_TEMPLATE_FILE = "switch.drawio"
DIAGRAM_OUTPUT_FILE = "network_diagram.drawio"

# --- Funkcje pomocnicze ---
# (find_device_in_list i get_canonical_identifier bez zmian)
def find_device_in_list(identifier, all_devices_list):
    """Wyszukuje urządzenie w liście z API po IP lub hostname (ignorując wielkość liter)."""
    if not identifier or not all_devices_list: return None
    for d in all_devices_list:
        if d.get("ip") == identifier: return d
    if isinstance(identifier, str):
        identifier_lower = identifier.lower()
        for d in all_devices_list:
            hostname_api = d.get("hostname")
            if hostname_api and hostname_api.lower() == identifier_lower: return d
        for d in all_devices_list:
            sysname_api = d.get("sysName")
            if sysname_api and sysname_api.lower() == identifier_lower: return d
    return None

def get_canonical_identifier(device_info_from_api, original_identifier=None):
    """Zwraca preferowany (kanoniczny) identyfikator dla urządzenia."""
    if not device_info_from_api: return original_identifier
    hostname = device_info_from_api.get('hostname')
    ip = device_info_from_api.get('ip')
    purpose = device_info_from_api.get('purpose')
    hostname_looks_like_ip = False
    if hostname: hostname_looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname))
    if purpose and purpose.strip(): return purpose.strip()
    elif hostname and not hostname_looks_like_ip: return hostname
    elif ip: return ip
    elif hostname and hostname_looks_like_ip: return hostname
    elif original_identifier: return original_identifier
    else: return str(device_info_from_api.get('device_id', "(Brak ID)"))

# --- Główne funkcje wykonawcze ---

def run_discovery(config, api_client, ip_list_path, creds_path, conn_txt_path, conn_json_path):
    """Wykonuje część aplikacji odpowiedzialną za odkrywanie połączeń."""
    print("\n=== Rozpoczynanie Fazy Odkrywania Połączeń ===")
    start_time = time.time()
    # Rozważ usunięcie starego pliku JSON
    # if os.path.exists(conn_json_path):
    #     try: os.remove(conn_json_path); print(f"  Usunięto stary plik: {conn_json_path}")
    #     except OSError as e: print(f"  ⚠ Nie można usunąć starego pliku {conn_json_path}: {e}")
    device_credentials = config_loader.load_device_credentials(creds_path)
    print("[Odkrywanie 1/5] Budowanie mapy MAC...")
    phys_map = data_processing.build_phys_mac_map(api_client)
    if not phys_map: print("  Ostrzeżenie: Nie udało się zbudować mapy MAC.")
    print(f"[Odkrywanie 2/5] Wczytywanie listy urządzeń z {ip_list_path}...")
    target_ips_or_hosts = file_io.load_ip_list(ip_list_path)
    if not target_ips_or_hosts: print("  Brak urządzeń docelowych na liście."); return
    print("[Odkrywanie 3/5] Pobieranie pełnej listy urządzeń z API...")
    all_devices_from_api = api_client.get_devices(columns="device_id,hostname,ip,sysName,purpose")
    if not all_devices_from_api: print("⚠ Nie udało się pobrać listy urządzeń z API."); return
    device_lookup_by_ip = {d.get("ip"): d for d in all_devices_from_api if d.get("ip")}
    device_lookup_by_hostname_lower = {d.get("hostname", "").lower(): d for d in all_devices_from_api if d.get("hostname")}
    device_lookup_by_sysname_lower = {d.get("sysName", "").lower(): d for d in all_devices_from_api if d.get("sysName")}

    print("[Odkrywanie 4/5] Przetwarzanie urządzeń i odkrywanie połączeń...")
    all_found_connections_raw = []
    processed_count = 0
    total_targets = len(target_ips_or_hosts)
    for ip_or_host in target_ips_or_hosts:
        processed_count += 1
        print(f"\n--- Odkrywanie dla ({processed_count}/{total_targets}): {ip_or_host} ---")
        target_device = find_device_in_list(ip_or_host, all_devices_from_api)
        if not target_device or not target_device.get("device_id"): print(f"  ⚠ Nie znaleziono '{ip_or_host}' w LibreNMS. Pomijam."); continue
        dev_id = target_device['device_id']; dev_host = target_device.get('hostname'); dev_ip = target_device.get('ip')
        current_host_identifier_for_lookup = dev_host or dev_ip or ip_or_host
        print(f"  Przetwarzanie jako: {current_host_identifier_for_lookup} (ID: {dev_id})")
        primary_id_lookup = dev_host; secondary_id_lookup = dev_ip
        specific_snmp_comm, comm_source = config_loader.get_specific_snmp_community(device_credentials, config["default_snmp_comm"], primary_id_lookup, secondary_id_lookup)
        if specific_snmp_comm and comm_source != "brak": print(f"  Używam SNMP community (źródło: {comm_source})")
        elif comm_source == "brak": print(f"  ⓘ Brak community SNMP dla tego urządzenia.")
        idx2name = data_processing.build_ifindex_to_name_map(api_client, str(dev_id))
        device_connections = []
        if specific_snmp_comm:
            device_connections.extend(discovery.find_via_lldp_cdp_snmp(target_device, specific_snmp_comm, idx2name))
            device_connections.extend(discovery.find_via_qbridge_snmp(phys_map, target_device, specific_snmp_comm, idx2name))
            device_connections.extend(discovery.find_via_snmp_fdb(phys_map, target_device, specific_snmp_comm, idx2name))
            device_connections.extend(discovery.find_via_arp_snmp(phys_map, target_device, specific_snmp_comm, idx2name))
        device_connections.extend(discovery.find_via_api_fdb(api_client, phys_map, target_device))
        if config["cli_username"] and config["cli_password"]:
             target_for_cli = dev_host or dev_ip
             if target_for_cli: device_connections.extend(discovery.find_via_cli(target_for_cli, config["cli_username"], config["cli_password"]))
             else: print("  ⚠ CLI: Brak hostname/IP do próby połączenia.")
        if device_connections: print(f"  ✓ Znaleziono {len(device_connections)} potencjalnych połączeń dla {current_host_identifier_for_lookup}."); all_found_connections_raw.extend(device_connections)
        else: print(f"  ❌ Nie wykryto połączeń dla {current_host_identifier_for_lookup}.")

    print("\n[Odkrywanie 5/5] Wzbogacanie danych, normalizacja, deduplikacja i zapisywanie wyników...")
    # Usunięto budowanie mapy port->ifIndex, bo nie była używana efektywnie
    enriched_connections = []
    if all_found_connections_raw:
        print("  Wzbogacanie danych o połączeniach...")
        processed_raw_count = 0
        for conn_raw in all_found_connections_raw:
            processed_raw_count += 1
            local_original = conn_raw.get('local_host'); remote_original = conn_raw.get('neighbor_host')
            local_if_raw = conn_raw.get('local_if'); remote_if_raw = conn_raw.get('neighbor_if')
            via_raw = conn_raw.get('via'); vlan_raw = conn_raw.get('vlan')
            local_ifindex_raw = conn_raw.get('local_ifindex') # Nadal None

            local_info = find_device_in_list(local_original, all_devices_from_api)
            remote_info = find_device_in_list(remote_original, all_devices_from_api)
            local_canonical = get_canonical_identifier(local_info, local_original)
            remote_canonical = get_canonical_identifier(remote_info, remote_original)

            if str(remote_canonical).lower() == 'null' or remote_canonical is None: continue
            if local_canonical == remote_canonical: continue

            enriched_conn_pre_filter = {
                "local_device": local_canonical, "local_port": local_if_raw,
                "local_ifindex": local_ifindex_raw, "remote_device": remote_canonical,
                "remote_port": remote_if_raw, "remote_ifindex": None, # Nie mamy zdalnego ifIndex
                "vlan": vlan_raw, "discovery_method": via_raw,
                "local_device_ip": local_info.get('ip') if local_info else None,
                "local_device_hostname": local_info.get('hostname') if local_info else None,
                "local_device_purpose": local_info.get('purpose') if local_info else None,
                "remote_device_ip": remote_info.get('ip') if remote_info else None,
                "remote_device_hostname": remote_info.get('hostname') if remote_info else None,
                "remote_device_purpose": remote_info.get('purpose') if remote_info else None,
                "remote_device_original": remote_original if not remote_info else None
            }
            # Usunięcie kluczy z wartością None
            enriched_conn = {k: v for k, v in enriched_conn_pre_filter.items() if v is not None}
            enriched_connections.append(enriched_conn)

        print(f"  Zebrano {len(enriched_connections)} wpisów po wzbogaceniu. Deduplikowanie...")
        final_connections = data_processing.deduplicate_connections(enriched_connections)
        # Log podsumowujący jest teraz wewnątrz deduplicate_connections

        # *** DODANO KLUCZOWY DEBUG PRINT ***
        print("\n--- DEBUG: Zawartość final_connections PRZED ZAPISEM ---")
        if final_connections:
            print(f"Liczba połączeń w final_connections: {len(final_connections)}")
            # Wydrukuj pierwsze kilka (np. 5) połączeń dla wglądu
            for idx, conn_debug in enumerate(final_connections[:5]):
                print(f"Połączenie #{idx} w final_connections:")
                pprint.pprint(conn_debug)
                # Sprawdźmy typy kluczowych wartości
                print(f"  Typy: local_device={type(conn_debug.get('local_device'))}, "
                      f"local_port={type(conn_debug.get('local_port'))}, "
                      f"remote_device={type(conn_debug.get('remote_device'))}, "
                      f"remote_port={type(conn_debug.get('remote_port'))}")
        else:
            print("Lista final_connections jest pusta.")
        print("--- KONIEC DEBUG: final_connections ---\n")
        # *******************************************

        # Wywołania funkcji zapisujących
        file_io.save_connections_txt(final_connections, conn_txt_path)
        file_io.save_connections_json(final_connections, conn_json_path)
    else:
        print("  Nie znaleziono żadnych surowych połączeń.")
        file_io.save_connections_txt([], conn_txt_path)
        file_io.save_connections_json([], conn_json_path)

    end_time = time.time()
    print(f"=== Zakończono Fazę Odkrywania Połączeń (czas: {end_time - start_time:.2f} sek.) ===")


# Funkcja draw_connections (bez zmian w tej iteracji)
def draw_connections(global_root: ET.Element, connections_data: list, port_mappings: dict, all_devices_api_list: list):
    """Rysuje linie (krawędzie) między portami urządzeń na diagramie."""
    print("\n  Krok 4d: Rysowanie połączeń między urządzeniami...")
    connection_count = 0
    drawn_links = set()
    edge_style = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeWidth=1;endArrow=none;strokeColor=#FF9900;fontSize=8;"
    print(f"INFO: Otrzymano {len(connections_data)} połączeń do przetworzenia.")
    missing_devices_logged = set(); missing_ports_logged = set()

    for i, conn in enumerate(connections_data):
        local_dev = conn.get("local_device"); local_port_name = conn.get("local_port")
        remote_dev = conn.get("remote_device"); remote_port_name = conn.get("remote_port")
        vlan = conn.get("vlan"); via = conn.get("discovery_method", "?")
        local_ifindex = conn.get("local_ifindex")
        remote_ifindex = conn.get("remote_ifindex")

        if not all([local_dev, local_port_name, remote_dev, remote_port_name]): continue
        if str(remote_dev).lower() == 'null' or local_dev == remote_dev: continue
        # print(f"\nDEBUG [Conn #{i}]: Przetwarzanie {local_dev}:{local_port_name} -> {remote_dev}:{remote_port_name} (via {via}, Lidx={local_ifindex}, Ridx={remote_ifindex})")

        local_map = port_mappings.get(local_dev)
        if not local_map and isinstance(local_dev, str): local_map = port_mappings.get(local_dev.lower())
        remote_map = port_mappings.get(remote_dev)
        if not remote_map and isinstance(remote_dev, str): remote_map = port_mappings.get(remote_dev.lower())
        source_cell_id = None; target_cell_id = None

        if not local_map:
            if local_dev not in missing_devices_logged: print(f"  INFO [Conn #{i}]: Urządzenie lokalne '{local_dev}' nie znalezione."); missing_devices_logged.add(local_dev)
            continue
        if not remote_map:
            if remote_dev not in missing_devices_logged:
                is_on_diagram = False
                possible_remote_ids = set(filter(None, [remote_dev, conn.get("remote_device_ip"), conn.get("remote_device_hostname"), conn.get("remote_device_purpose")]))
                for rem_id in possible_remote_ids:
                    if rem_id in port_mappings or (isinstance(rem_id, str) and rem_id.lower() in port_mappings): is_on_diagram = True; break
                status_str = "JEST na diagramie pod inną nazwą/IP!" if is_on_diagram else "BRAK go na diagramie"
                print(f"  INFO [Conn #{i}]: Urządzenie zdalne '{remote_dev}' nie znalezione. Status: {status_str}.")
                missing_devices_logged.add(remote_dev)
            continue

        # --- Wyszukiwanie ID komórki portu lokalnego (PRIORYTET ifIndex) ---
        source_cell_id = None
        unique_lookup_keys_local = []
        if local_ifindex is not None: key = f"ifindex_{local_ifindex}"; unique_lookup_keys_local.append(key)
        if local_port_name not in unique_lookup_keys_local: unique_lookup_keys_local.append(local_port_name)
        if isinstance(local_port_name, str):
            if local_port_name.lower().startswith("gi"): key = "GigabitEthernet" + local_port_name[2:];
            elif local_port_name.lower().startswith("fa"): key = "FastEthernet" + local_port_name[2:];
            elif local_port_name.lower().startswith("te"): key = "TenGigabitEthernet" + local_port_name[2:];
            elif local_port_name.lower().startswith("eth"): key = "Ethernet" + local_port_name[3:];
            else: key = None
            if key and key not in unique_lookup_keys_local: unique_lookup_keys_local.append(key)
            match_num = re.search(r'(\d+)$', local_port_name)
            if match_num: port_num_str = match_num.group(1);
            if port_num_str not in unique_lookup_keys_local: unique_lookup_keys_local.append(port_num_str)
        # print(f"  DEBUG [Conn #{i}]: Lookup lokalny dla portu '{local_port_name}'. Klucze: {unique_lookup_keys_local}")
        for key in unique_lookup_keys_local:
            source_cell_id = local_map.get(key)
            if source_cell_id:
                # print(f"  DEBUG [Conn #{i}]: Znaleziono local port ID: '{source_cell_id}' dla klucza '{key}'")
                break
        if not source_cell_id: print(f"  WARN [Conn #{i}]: NIE znaleziono ID komórki dla portu lokalnego '{local_port_name}' na '{local_dev}'.")

        # --- Wyszukiwanie ID komórki portu zdalnego (PRIORYTET ifIndex) ---
        target_cell_id = None
        unique_lookup_keys_remote = []
        if remote_ifindex is not None: key = f"ifindex_{remote_ifindex}"; unique_lookup_keys_remote.append(key)
        if remote_port_name not in unique_lookup_keys_remote: unique_lookup_keys_remote.append(remote_port_name)
        if isinstance(remote_port_name, str):
            if remote_port_name.lower().startswith("gi"): key = "GigabitEthernet" + remote_port_name[2:];
            elif remote_port_name.lower().startswith("fa"): key = "FastEthernet" + remote_port_name[2:];
            elif remote_port_name.lower().startswith("te"): key = "TenGigabitEthernet" + remote_port_name[2:];
            elif remote_port_name.lower().startswith("eth"): key = "Ethernet" + remote_port_name[3:];
            else: key = None
            if key and key not in unique_lookup_keys_remote: unique_lookup_keys_remote.append(key)
            match_num_rem = re.search(r'(\d+)$', remote_port_name)
            if match_num_rem: port_num_str_rem = match_num_rem.group(1);
            if port_num_str_rem not in unique_lookup_keys_remote: unique_lookup_keys_remote.append(port_num_str_rem)
        # print(f"  DEBUG [Conn #{i}]: Lookup zdalny dla portu '{remote_port_name}'. Klucze: {unique_lookup_keys_remote}")
        for key in unique_lookup_keys_remote:
            target_cell_id = remote_map.get(key)
            if target_cell_id:
                # print(f"  DEBUG [Conn #{i}]: Znaleziono remote port ID: '{target_cell_id}' dla klucza '{key}'")
                break
        if not target_cell_id: print(f"  WARN [Conn #{i}]: NIE znaleziono ID komórki dla portu zdalnego '{remote_port_name}' na '{remote_dev}'.")

        # --- Rysowanie krawędzi ---
        if source_cell_id and target_cell_id:
            link_key = tuple(sorted((source_cell_id, target_cell_id)))
            if link_key in drawn_links: continue
            edge_id = f"conn_edge_{i}_{source_cell_id}_{target_cell_id}"
            edge_label = f"VLAN {vlan}" if vlan is not None else ""
            edge_cell = drawio_utils.create_edge_cell(edge_id, "1", source_cell_id, target_cell_id, edge_style)
            if edge_label:
                edge_cell.set("value", edge_label)
                drawio_utils.apply_style_change(edge_cell, "labelBackgroundColor", "#FFFFFF")
                drawio_utils.apply_style_change(edge_cell, "fontColor", "#000000")
            global_root.append(edge_cell);
            drawn_links.add(link_key);
            connection_count += 1
            print(f"  ✓ [Conn #{i}]: Narysowano połączenie!")
        else:
            local_port_key = f"{local_dev}:{local_port_name}"; remote_port_key = f"{remote_dev}:{remote_port_name}"
            log_msg_parts = []
            if not source_cell_id: log_msg_parts.append(f"portu lokalnego '{local_port_name}'")
            if not target_cell_id: log_msg_parts.append(f"portu zdalnego '{remote_port_name}'")
            if log_msg_parts and (local_port_key not in missing_ports_logged or remote_port_key not in missing_ports_logged) :
                 print(f"  INFO [Conn #{i}]: Połączenie NIE zostało narysowane (brak ID dla { ' i '.join(log_msg_parts) }).")
                 if not source_cell_id: missing_ports_logged.add(local_port_key)
                 if not target_cell_id: missing_ports_logged.add(remote_port_key)

    print(f"\n  ✓ Zakończono rysowanie połączeń. Narysowano {connection_count} linii.")


# Funkcja generująca diagram (bez zmian)
def run_diagram_generation(config, api_client, ip_list_path, template_path, output_path, connections_json_path):
    """Wykonuje część aplikacji odpowiedzialną za generowanie diagramu."""
    print("\n=== Rozpoczynanie Fazy Generowania Diagramu ===")
    start_time = time.time()
    print(f"[Diagram 1/5] Wczytywanie listy urządzeń z {ip_list_path}...")
    target_ips_or_hosts = file_io.load_ip_list(ip_list_path)
    if not target_ips_or_hosts: print("  Brak urządzeń na liście."); return
    print("[Diagram 2/5] Pobieranie listy urządzeń z API...")
    all_devices_from_api = api_client.get_devices(columns="device_id,hostname,ip,sysName,purpose")
    if not all_devices_from_api: print("⚠ Nie udało się pobrać listy urządzeń z API."); return
    print("[Diagram 3/5] Inicjalizacja generatora diagramu...")
    generator = drawio_base.DrawioXMLGenerator()
    global_root = generator.get_root_element()
    print("[Diagram 4/5] Przetwarzanie urządzeń i budowanie diagramu...")
    device_details_for_layout = []
    max_template_width, max_template_height = 0, 0
    port_cell_mappings = {}
    processed_count = 0
    print("  Krok 4a: Przygotowanie szablonów i informacji...")
    for ip_or_host in target_ips_or_hosts:
        processed_count += 1
        target_device_info = find_device_in_list(ip_or_host, all_devices_from_api)
        if not target_device_info or not target_device_info.get("device_id"):
            print(f"  Pomijam '{ip_or_host}' (nie znaleziono w API).")
            continue
        template_cells, t_width, t_height = drawio_device_builder.load_and_prepare_template(template_path, processed_count)
        if template_cells is None: print(f"  ⚠ Nie udało się załadować szablonu dla '{ip_or_host}'."); continue
        canonical_id = get_canonical_identifier(target_device_info, ip_or_host)
        if not canonical_id: canonical_id = f"unknown_dev_{processed_count}"
        device_identifiers_to_map = set()
        dev_ip = target_device_info.get('ip'); dev_host = target_device_info.get('hostname'); dev_purpose = target_device_info.get('purpose')
        device_identifiers_to_map.add(ip_or_host)
        if dev_ip: device_identifiers_to_map.add(dev_ip)
        if dev_host: device_identifiers_to_map.add(dev_host); device_identifiers_to_map.add(dev_host.lower())
        if dev_purpose: device_identifiers_to_map.add(dev_purpose); device_identifiers_to_map.add(dev_purpose.lower())
        if canonical_id: device_identifiers_to_map.add(canonical_id); device_identifiers_to_map.add(canonical_id.lower())
        device_identifiers_to_map = set(filter(None, device_identifiers_to_map))
        device_details_for_layout.append({
            "identifiers": list(device_identifiers_to_map), "canonical_id": canonical_id,
            "info": target_device_info, "template_cells": template_cells,
            "width": t_width, "height": t_height, "index": processed_count
        })
        max_template_width = max(max_template_width, t_width); max_template_height = max(max_template_height, t_height)
    if not device_details_for_layout: print("  Brak urządzeń do umieszczenia na diagramie."); return
    print(f"  Krok 4b: Obliczanie layoutu dla {len(device_details_for_layout)} urządzeń...")
    layout_positions = drawio_layout.calculate_grid_layout(len(device_details_for_layout), max_template_width, max_template_height)
    print("  Krok 4c: Dodawanie urządzeń do diagramu...")
    for i, device_data in enumerate(device_details_for_layout):
        port_map = drawio_device_builder.add_device_to_diagram(global_root, device_data["template_cells"], device_data["width"], device_data["height"], device_data["info"], api_client, layout_positions[i], device_data["index"])
        if port_map is not None:
            for identifier in device_data["identifiers"]:
                if identifier: port_cell_mappings[identifier] = port_map
        else:
            canonical_id_for_log = device_data.get("canonical_id", ["N/A"])[0]
            print(f"WARN: Brak mapy portów dla urządzenia {canonical_id_for_log}.")
    print("[Diagram 5/5] Rysowanie połączeń...")
    connections_data = file_io.load_connections_json(connections_json_path)
    if connections_data:
        draw_connections(global_root, connections_data, port_cell_mappings, all_devices_from_api) # Wywołanie funkcji rysującej
    else:
        print(f"  Brak danych o połączeniach w {connections_json_path}.")
    file_io.save_diagram_xml(generator.get_tree(), output_path)
    end_time = time.time()
    print(f"=== Zakończono Fazę Generowania Diagramu (czas: {end_time - start_time:.2f} sek.) ===")

# --- Główny blok wykonawczy ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Narzędzie do odkrywania połączeń sieciowych i generowania diagramów Draw.io.")
    parser.add_argument("--discover", action="store_true", help="Uruchom tylko fazę odkrywania.")
    parser.add_argument("--diagram", action="store_true", help="Uruchom tylko fazę generowania diagramu.")
    parser.add_argument("--ip-list", default=IP_LIST_FILE, help=f"Plik z listą IP/Hostname (domyślnie: {IP_LIST_FILE}).")
    parser.add_argument("--creds-json", default=DEVICE_CREDENTIALS_FILE, help=f"Plik JSON z danymi SNMP (domyślnie: {DEVICE_CREDENTIALS_FILE}).")
    parser.add_argument("--conn-txt", default=CONNECTIONS_TXT_FILE, help=f"Plik .txt z połączeniami (domyślnie: {CONNECTIONS_TXT_FILE}).")
    parser.add_argument("--conn-json", default=CONNECTIONS_JSON_FILE, help=f"Plik .json z połączeniami (domyślnie: {CONNECTIONS_JSON_FILE}).")
    parser.add_argument("--template", default=DIAGRAM_TEMPLATE_FILE, help=f"Plik szablonu .drawio (domyślnie: {DIAGRAM_TEMPLATE_FILE}).")
    parser.add_argument("--diagram-out", default=DIAGRAM_OUTPUT_FILE, help=f"Plik wyjściowy diagramu .drawio (domyślnie: {DIAGRAM_OUTPUT_FILE}).")
    parser.add_argument("--no-verify-ssl", action="store_true", help="Wyłącz weryfikację SSL dla API.")
    args = parser.parse_args()

    run_discovery_flag = args.discover
    run_diagram_flag = args.diagram
    if not run_discovery_flag and not run_diagram_flag:
        print("Domyślnie uruchamiam obie fazy (discover i diagram).")
        run_discovery_flag = True
        run_diagram_flag = True

    print("--- Uruchamianie Aplikacji ---")
    app_start_time = time.time()
    try:
        env_config = config_loader.get_env_config()
    except ValueError as e: print(f"Błąd krytyczny .env: {e}"); sys.exit(1)
    except Exception as e: print(f"Błąd ładowania .env: {e}"); sys.exit(1)

    api = LibreNMSAPI(env_config["base_url"], env_config["api_key"], verify_ssl=(not args.no_verify_ssl))

    if run_discovery_flag:
        run_discovery(env_config, api, args.ip_list, args.creds_json, args.conn_txt, args.conn_json)

    if run_diagram_flag:
        # *** WAŻNE: Upewnij się, że plik JSON jest aktualny przed tą fazą! ***
        if not os.path.exists(args.template): print(f"⚠ Błąd: Plik szablonu '{args.template}' nie istnieje.");
        elif not os.path.exists(args.conn_json):
             print(f"⚠ Plik połączeń '{args.conn_json}' nie istnieje. Linie nie zostaną narysowane.")
             run_diagram_generation(env_config, api, args.ip_list, args.template, args.diagram_out, args.conn_json)
        else:
             run_diagram_generation(env_config, api, args.ip_list, args.template, args.diagram_out, args.conn_json)

    app_end_time = time.time()
    print(f"\n--- Zakończono. Całkowity czas: {app_end_time - app_start_time:.2f} sek. ---")

