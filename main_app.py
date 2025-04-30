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
try:
    import config_loader
    import file_io
    from librenms_client import LibreNMSAPI
    import data_processing
    import discovery
    import drawio_base
    import drawio_layout
    import drawio_device_builder # Użyj zmodyfikowanej wersji (zwracającej dict z x,y,orient)
    import drawio_utils         # Użyj zmodyfikowanej wersji (z create_floating_edge_cell)
except ImportError as e:
    print(f"Błąd importu modułu: {e}. Upewnij się, że wszystkie pliki .py znajdują się w odpowiednim miejscu.")
    sys.exit(1)


# --- Stałe ---
IP_LIST_FILE = "ip_list.txt"
CONNECTIONS_TXT_FILE = "connections.txt"
CONNECTIONS_JSON_FILE = "connections.json"
DIAGRAM_TEMPLATE_FILE = "switch.drawio"
DIAGRAM_OUTPUT_FILE = "network_diagram.drawio"

# --- Funkcje pomocnicze (bez zmian) ---
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
    purpose = device_info_from_api.get('purpose')
    if purpose and purpose.strip(): return purpose.strip()
    hostname = device_info_from_api.get('hostname')
    if hostname:
        hostname_looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname))
        if not hostname_looks_like_ip: return hostname
    ip = device_info_from_api.get('ip')
    if ip: return ip
    if hostname and hostname_looks_like_ip: return hostname
    if original_identifier: return original_identifier
    return str(device_info_from_api.get('device_id', "(Brak ID)"))

# --- Główne funkcje wykonawcze ---

# Funkcja run_discovery (POPRAWIONE BŁĘDY SKŁADNIOWE)
def run_discovery(config, api_client, ip_list_path, conn_txt_path, conn_json_path):
    """Wykonuje część aplikacji odpowiedzialną za odkrywanie połączeń."""
    print("\n=== Rozpoczynanie Fazy Odkrywania Połączeń ===")
    start_time = time.time()
    print("[Odkrywanie 1/5] Budowanie mapy MAC...")
    phys_map = data_processing.build_phys_mac_map(api_client)
    if not phys_map: print("  Ostrzeżenie: Nie udało się zbudować mapy MAC.")
    print(f"[Odkrywanie 2/5] Wczytywanie listy urządzeń z {ip_list_path}...")
    target_ips_or_hosts = file_io.load_ip_list(ip_list_path)
    if not target_ips_or_hosts: print("  Brak urządzeń docelowych na liście."); return
    print("[Odkrywanie 3/5] Pobieranie pełnej listy urządzeń z API...")
    all_devices_from_api = api_client.get_devices(columns="device_id,hostname,ip,sysName,purpose")
    if not all_devices_from_api: print("⚠ Nie udało się pobrać listy urządzeń z API."); return
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
        current_host_identifier_for_lookup = get_canonical_identifier(target_device, ip_or_host)
        print(f"  Przetwarzanie jako: {current_host_identifier_for_lookup} (ID: {dev_id})")
        communities = config_loader.get_communities_to_try(config.get("default_snmp_communities", []))
        idx2name = data_processing.build_ifindex_to_name_map(api_client, str(dev_id))
        device_connections = []
        if communities:
            device_connections.extend(discovery.find_via_lldp_cdp_snmp(target_device, communities, idx2name))
            device_connections.extend(discovery.find_via_qbridge_snmp(phys_map, target_device, communities, idx2name))
            device_connections.extend(discovery.find_via_snmp_fdb(phys_map, target_device, communities, idx2name))
            device_connections.extend(discovery.find_via_arp_snmp(phys_map, target_device, communities, idx2name))
        else: print("  ⓘ Brak skonfigurowanych community SNMP do próby.")
        device_connections.extend(discovery.find_via_api_fdb(api_client, phys_map, target_device))
        cli_user = config.get("cli_username"); cli_pass = config.get("cli_password")
        if cli_user and cli_pass:
            target_for_cli = dev_host or dev_ip
            if target_for_cli: device_connections.extend(discovery.find_via_cli(target_for_cli, cli_user, cli_pass))
            else: print("  ⚠ CLI: Brak hostname/IP do próby połączenia.")
        else: print("  ⓘ Brak danych logowania CLI w konfiguracji.")
        if device_connections: print(f"  ✓ Znaleziono {len(device_connections)} potencjalnych połączeń dla {current_host_identifier_for_lookup}."); all_found_connections_raw.extend(device_connections)
        else: print(f"  ❌ Nie wykryto połączeń dla {current_host_identifier_for_lookup}.")
    print("\n[Odkrywanie 5/5] Wzbogacanie danych, normalizacja, deduplikacja i zapisywanie wyników...")
    print("  Budowanie mapy portów (nazwa/opis) -> ifIndex z danych API...")
    port_to_ifindex_map = {}
    processed_api_dev_count = 0; total_api_devices = len(all_devices_from_api)
    for device_api_info in all_devices_from_api:
        processed_api_dev_count += 1
        if processed_api_dev_count % max(1, total_api_devices // 20) == 0 or processed_api_dev_count == total_api_devices: print(f"    Przetworzono mapę ifIndex dla {processed_api_dev_count}/{total_api_devices} urządzeń API...")
        dev_id = device_api_info.get("device_id"); canonical_id = get_canonical_identifier(device_api_info)
        if not dev_id or not canonical_id: continue
        try:
            ports = api_client.get_ports(str(dev_id), columns="ifIndex,ifName,ifDescr,ifAlias")
            if ports:
                for p in ports:
                    # --- POPRAWIONY BLOK (usunięcie średników) ---
                    ifindex = p.get("ifIndex")
                    if ifindex is None:
                        continue

                    ifname = p.get("ifName")
                    if ifname:
                        port_to_ifindex_map[(canonical_id, ifname)] = ifindex

                    ifdescr = p.get("ifDescr")
                    if ifdescr and ifdescr != ifname:
                        port_to_ifindex_map[(canonical_id, ifdescr)] = ifindex

                    ifalias = p.get("ifAlias")
                    if ifalias and ifalias != ifname and ifalias != ifdescr:
                        port_to_ifindex_map[(canonical_id, ifalias)] = ifindex
                    # --- KONIEC POPRAWIONEGO BLOKU ---
        except Exception as e: print(f"  ⚠ Błąd pobierania portów API dla mapy ifIndex (urządzenie ID {dev_id}, nazwa {canonical_id}): {e}")
    print(f"  ✓ Zbudowano mapę port -> ifIndex dla {len(port_to_ifindex_map)} wpisów.")
    enriched_connections = []
    if all_found_connections_raw:
        print("  Wzbogacanie danych o połączeniach (w tym ifIndex)...")
        for conn_raw in all_found_connections_raw:
            local_original = conn_raw.get('local_host'); remote_original = conn_raw.get('neighbor_host')
            local_if_raw = conn_raw.get('local_if'); remote_if_raw = conn_raw.get('neighbor_if')
            via_raw = conn_raw.get('via'); vlan_raw = conn_raw.get('vlan')
            local_ifindex_cli = conn_raw.get('local_ifindex')
            local_info = find_device_in_list(local_original, all_devices_from_api)
            remote_info = find_device_in_list(remote_original, all_devices_from_api)
            local_canonical = get_canonical_identifier(local_info, local_original)
            remote_canonical = get_canonical_identifier(remote_info, remote_original)
            if str(remote_canonical).lower() == 'null' or remote_canonical is None: continue
            if local_canonical and remote_canonical and local_canonical == remote_canonical: continue
            local_ifindex = local_ifindex_cli
            if local_ifindex is None and local_canonical and local_if_raw: local_ifindex = port_to_ifindex_map.get((local_canonical, local_if_raw))
            remote_ifindex = None
            if remote_canonical and remote_if_raw: remote_ifindex = port_to_ifindex_map.get((remote_canonical, remote_if_raw))
            enriched_conn_pre_filter = {
                "local_device": local_canonical, "local_port": local_if_raw, "local_ifindex": local_ifindex,
                "remote_device": remote_canonical, "remote_port": remote_if_raw, "remote_ifindex": remote_ifindex,
                "vlan": vlan_raw, "discovery_method": via_raw,
                "local_device_ip": local_info.get('ip') if local_info else None, "local_device_hostname": local_info.get('hostname') if local_info else None,
                "local_device_purpose": local_info.get('purpose') if local_info else None, "remote_device_ip": remote_info.get('ip') if remote_info else None,
                "remote_device_hostname": remote_info.get('hostname') if remote_info else None, "remote_device_purpose": remote_info.get('purpose') if remote_info else None,
                "remote_device_original": remote_original if not remote_info else None
            }
            enriched_conn = {k: v for k, v in enriched_conn_pre_filter.items() if v is not None}
            enriched_connections.append(enriched_conn)
        print(f"  Zebrano {len(enriched_connections)} wpisów po wzbogaceniu. Deduplikowanie...")
        final_connections = data_processing.deduplicate_connections(enriched_connections)
        file_io.save_connections_txt(final_connections, conn_txt_path)
        file_io.save_connections_json(final_connections, conn_json_path)
    else:
        print("  Nie znaleziono żadnych surowych połączeń.")
        file_io.save_connections_txt([], conn_txt_path); file_io.save_connections_json([], conn_json_path)
    end_time = time.time()
    print(f"=== Zakończono Fazę Odkrywania Połączeń (czas: {end_time - start_time:.2f} sek.) ===")


# Funkcja draw_connections (wersja z create_floating_edge_cell - bez zmian od ostatniej)
def draw_connections(global_root: ET.Element, connections_data: list, port_mappings: dict, all_devices_api_list: list):
    """
    Rysuje linie (krawędzie) między portami urządzeń na diagramie.
    *** WERSJA Z DEFINIOWANIEM LINII WYŁĄCZNIE PRZEZ PUNKTY ***
    """
    print("\n  Krok 4d: Rysowanie połączeń między urządzeniami...")
    connection_count = 0
    edge_style_base = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;html=1;strokeWidth=1.5;endArrow=none;strokeColor=#FF9900;fontSize=8;"
    print(f"INFO: Otrzymano {len(connections_data)} połączeń do przetworzenia.")
    missing_devices_logged = set(); missing_ports_logged = set()
    WAYPOINT_OFFSET = drawio_device_builder.WAYPOINT_OFFSET if hasattr(drawio_device_builder, 'WAYPOINT_OFFSET') else 20
    drawn_links = set()

    for i, conn in enumerate(connections_data):
        local_dev = conn.get("local_device"); local_port_name = conn.get("local_port")
        remote_dev = conn.get("remote_device"); remote_port_name = conn.get("remote_port")
        vlan = conn.get("vlan"); via = conn.get("discovery_method", "?")
        local_ifindex = conn.get("local_ifindex")
        remote_ifindex = conn.get("remote_ifindex")

        print(f"\n--- DEBUG Conn #{i}: Przetwarzanie {local_dev}:{local_port_name} ({local_ifindex}) <-> {remote_dev}:{remote_port_name} ({remote_ifindex}) ---")

        if not all([local_dev, local_port_name, remote_dev, remote_port_name]):
            print(f"  DEBUG Conn #{i}: Pomijam - brak kluczowych danych (dev/port)."); continue
        if str(remote_dev).lower() == 'null' or local_dev == remote_dev:
            print(f"  DEBUG Conn #{i}: Pomijam - remote to null lub self-connection."); continue

        local_map_dev = port_mappings.get(local_dev)
        if not local_map_dev and isinstance(local_dev, str): local_map_dev = port_mappings.get(local_dev.lower())
        remote_map_dev = port_mappings.get(remote_dev)
        if not remote_map_dev and isinstance(remote_dev, str): remote_map_dev = port_mappings.get(remote_dev.lower())

        if not local_map_dev:
            if local_dev not in missing_devices_logged: print(f"  INFO [Conn #{i}]: Urządzenie lokalne '{local_dev}' nie znalezione w mapowaniach."); missing_devices_logged.add(local_dev)
            print(f"  DEBUG Conn #{i}: BŁĄD - Brak mapy dla urządzenia lokalnego '{local_dev}'."); continue
        if not remote_map_dev:
            if remote_dev not in missing_devices_logged:
                is_on_diagram = any(rem_id in port_mappings or (isinstance(rem_id, str) and rem_id.lower() in port_mappings) for rem_id in filter(None, [remote_dev, conn.get("remote_device_ip"), conn.get("remote_device_hostname"), conn.get("remote_device_purpose")]))
                status_str = "JEST na diagramie pod inną nazwą/IP!" if is_on_diagram else "BRAK go na diagramie"; print(f"  INFO [Conn #{i}]: Urządzenie zdalne '{remote_dev}' nie znalezione w mapowaniach. Status: {status_str}.")
                missing_devices_logged.add(remote_dev)
            print(f"  DEBUG Conn #{i}: BŁĄD - Brak mapy dla urządzenia zdalnego '{remote_dev}'."); continue

        source_data = None; target_data = None
        lookup_key_found_local = "N/A"; lookup_key_found_remote = "N/A"

        found_it_local = False
        if local_ifindex is not None: key_ifindex = f"ifindex_{local_ifindex}"; source_data = local_map_dev.get(key_ifindex);
        if source_data: lookup_key_found_local = key_ifindex; found_it_local = True
        if not found_it_local and local_port_name:
            key_portname = local_port_name; source_data = local_map_dev.get(key_portname)
            if source_data: lookup_key_found_local = key_portname; found_it_local = True
            else:
                key_lower = key_portname.lower()
                for map_key, map_value in local_map_dev.items():
                    if isinstance(map_key, str) and map_key.lower() == key_lower: source_data = map_value; lookup_key_found_local = f"{key_portname} (jako {map_key})"; found_it_local = True; break
        if not found_it_local and isinstance(local_port_name, str):
             match_num = re.search(r'(\d+)$', local_port_name)
             if match_num: key_num = match_num.group(1); source_data = local_map_dev.get(key_num)
             if source_data: lookup_key_found_local = key_num + " (fallback z numeru)"; found_it_local = True

        found_it_remote = False
        if remote_ifindex is not None: key_ifindex_rem = f"ifindex_{remote_ifindex}"; target_data = remote_map_dev.get(key_ifindex_rem)
        if target_data: lookup_key_found_remote = key_ifindex_rem; found_it_remote = True
        if not found_it_remote and remote_port_name:
            key_portname_rem = remote_port_name; target_data = remote_map_dev.get(key_portname_rem)
            if target_data: lookup_key_found_remote = key_portname_rem; found_it_remote = True
            else:
                key_lower_rem = key_portname_rem.lower()
                for map_key, map_value in remote_map_dev.items():
                     if isinstance(map_key, str) and map_key.lower() == key_lower_rem: target_data = map_value; lookup_key_found_remote = f"{key_portname_rem} (jako {map_key})"; found_it_remote = True; break
        if not found_it_remote and isinstance(remote_port_name, str):
             match_num_rem = re.search(r'(\d+)$', remote_port_name)
             if match_num_rem: key_num_rem = match_num_rem.group(1); target_data = remote_map_dev.get(key_num_rem)
             if target_data: lookup_key_found_remote = key_num_rem + " (fallback z numeru)"; found_it_remote = True

        if source_data and target_data:
            source_x = source_data.get("x"); source_y = source_data.get("y"); source_orientation = source_data.get("orientation")
            target_x = target_data.get("x"); target_y = target_data.get("y"); target_orientation = target_data.get("orientation")
            source_cell_id_for_key = source_data.get("cell_id"); target_cell_id_for_key = target_data.get("cell_id")

            if not all([isinstance(source_x, (int, float)), isinstance(source_y, (int, float)), source_orientation, isinstance(target_x, (int, float)), isinstance(target_y, (int, float)), target_orientation, source_cell_id_for_key, target_cell_id_for_key]):
                 print(f"  DEBUG Conn #{i}: BŁĄD - Brak pełnych danych (współrzędne/orientacja/id) do narysowania linii. Source={source_data}, Target={target_data}."); continue

            print(f"  DEBUG Conn #{i}: OK - Znaleziono dane i współrzędne. Source=({source_x},{source_y} orient={source_orientation}), Target=({target_x},{target_y} orient={target_orientation}).")

            link_key = frozenset([source_cell_id_for_key, target_cell_id_for_key])
            if link_key in drawn_links: print(f"  DEBUG Conn #{i}: Pomijam - link {link_key} (wg ID) już narysowany."); continue

            wp_source_x, wp_source_y = source_x, source_y
            if source_orientation == "up": wp_source_y -= WAYPOINT_OFFSET
            elif source_orientation == "down": wp_source_y += WAYPOINT_OFFSET
            elif source_orientation == "left": wp_source_x -= WAYPOINT_OFFSET
            elif source_orientation == "right": wp_source_x += WAYPOINT_OFFSET
            wp_target_x, wp_target_y = target_x, target_y
            if target_orientation == "up": wp_target_y -= WAYPOINT_OFFSET
            elif target_orientation == "down": wp_target_y += WAYPOINT_OFFSET
            elif target_orientation == "left": wp_target_x -= WAYPOINT_OFFSET
            elif target_orientation == "right": wp_target_x += WAYPOINT_OFFSET
            waypoints = [(wp_source_x, wp_source_y), (wp_target_x, wp_target_y)]
            print(f"  DEBUG Conn #{i}: Waypointy: Source WP=({wp_source_x},{wp_source_y}), Target WP=({wp_target_x},{wp_target_y})")

            edge_id = f"conn_edge_{i}_{source_cell_id_for_key}_{target_cell_id_for_key}"
            edge_style = edge_style_base
            edge_label = f"VLAN {vlan}" if vlan is not None else ""
            edge_cell = drawio_utils.create_floating_edge_cell(edge_id, "1", edge_style, source_point=(source_x, source_y), target_point=(target_x, target_y), waypoints=waypoints)
            print(f"  DEBUG Conn #{i}: Tworzenie pływającej krawędzi ID '{edge_id}' P1=({source_x},{source_y}), P2=({target_x},{target_y}), Waypointy={waypoints}, Style='{edge_style}', Label='{edge_label}'")
            if edge_label: edge_cell.set("value", edge_label); drawio_utils.apply_style_change(edge_cell, "labelBackgroundColor", "#FFFFFF"); drawio_utils.apply_style_change(edge_cell, "fontColor", "#000000")
            global_root.append(edge_cell); drawn_links.add(link_key); connection_count += 1
        else:
            local_port_key = f"{local_dev}:{local_port_name}"; remote_port_key = f"{remote_dev}:{remote_port_name}"
            log_msg_parts = []
            if not source_data: log_msg_parts.append(f"danych źródła '{local_port_name}' (klucz: {lookup_key_found_local})")
            if not target_data: log_msg_parts.append(f"danych celu '{remote_port_name}' (klucz: {lookup_key_found_remote})")
            if log_msg_parts and (local_port_key not in missing_ports_logged or remote_port_key not in missing_ports_logged) :
                print(f"  INFO [Conn #{i}]: Połączenie NIE zostało narysowane (brak danych dla { ' i '.join(log_msg_parts) }).")
                print(f"  DEBUG Conn #{i}: BŁĄD - Nie znaleziono danych dla: { ' i '.join(log_msg_parts) }.")
                if not source_data: missing_ports_logged.add(local_port_key)
                if not target_data: missing_ports_logged.add(remote_port_key)

    print(f"\n  ✓ Zakończono rysowanie połączeń. Narysowano {connection_count} linii.")


# Funkcja run_diagram_generation (bez zmian)
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
    port_cell_mappings = {} # Nadal używamy tej struktury
    processed_count = 0
    print("  Krok 4a: Przygotowanie szablonów i informacji...")
    target_set = set(ip_or_host.lower() for ip_or_host in target_ips_or_hosts if isinstance(ip_or_host, str))
    target_set.update(ip for ip in target_ips_or_hosts if not isinstance(ip, str))

    device_index = 0
    for device_api_info in all_devices_from_api:
        dev_ip = device_api_info.get('ip'); dev_host = device_api_info.get('hostname')
        dev_sysname = device_api_info.get('sysName'); dev_purpose = device_api_info.get('purpose')
        is_target = False
        potential_ids = list(filter(None, [dev_ip, dev_host, dev_sysname, dev_purpose]))
        canonical_id_check = get_canonical_identifier(device_api_info)
        if canonical_id_check: potential_ids.append(canonical_id_check)

        for pid in potential_ids:
            if pid in target_set or (isinstance(pid, str) and pid.lower() in target_set):
                 is_target = True; break
        if not is_target: continue

        device_index += 1
        current_id_for_log = canonical_id_check if canonical_id_check else (dev_host or dev_ip)
        print(f"\n  -- Przetwarzanie urządzenia {device_index}: {current_id_for_log} --")

        template_cells, t_width, t_height = drawio_device_builder.load_and_prepare_template(template_path, device_index)
        if template_cells is None: print(f"  ⚠ Nie udało się załadować szablonu dla '{current_id_for_log}'. Pomijam."); continue

        canonical_id = canonical_id_check if canonical_id_check else f"unknown_dev_{device_index}"
        device_identifiers_to_map = set(filter(None, [dev_ip, dev_host, dev_sysname, dev_purpose, canonical_id]))
        lowercase_ids = {ident.lower() for ident in device_identifiers_to_map if isinstance(ident, str)}
        device_identifiers_to_map.update(lowercase_ids)

        device_details_for_layout.append({
            "identifiers": list(device_identifiers_to_map), "canonical_id": canonical_id,
            "info": device_api_info, "template_cells": template_cells,
            "width": t_width, "height": t_height, "index": device_index
        })
        max_template_width = max(max_template_width, t_width); max_template_height = max(max_template_height, t_height)

    if not device_details_for_layout: print("  Brak urządzeń z listy docelowej do umieszczenia na diagramie."); return

    print(f"  Krok 4b: Obliczanie layoutu dla {len(device_details_for_layout)} urządzeń...")
    layout_positions = drawio_layout.calculate_grid_layout(len(device_details_for_layout), max_template_width, max_template_height)

    print("  Krok 4c: Dodawanie urządzeń do diagramu...")
    for i, device_data in enumerate(device_details_for_layout):
        current_id_for_log = device_data.get("canonical_id", f"Index {i}")
        print(f"\n  -- Dodawanie urządzenia {i+1}/{len(device_details_for_layout)}: {current_id_for_log} --")
        # Zwracana mapa zawiera teraz {'port_id': {'cell_id': ..., 'x': ..., 'y': ..., 'orientation': ...}}
        port_map_data = drawio_device_builder.add_device_to_diagram(
            global_root, device_data["template_cells"], device_data["width"], device_data["height"],
            device_data["info"], api_client, layout_positions[i], device_data["index"]
        )
        if port_map_data is not None:
            # Mapowanie id_urzadzenia -> mapa_portow
            for identifier in device_data["identifiers"]:
                if identifier: port_cell_mappings[identifier] = port_map_data
            print(f"  ✓ Zmapowano identyfikatory: {device_data['identifiers']} na mapę portów urządzenia {current_id_for_log}")
        else:
            print(f"  ⚠ Brak mapy portów dla urządzenia {current_id_for_log}.")

    print("[Diagram 5/5] Rysowanie połączeń...")
    connections_data = file_io.load_connections_json(connections_json_path)
    if connections_data is not None:
        draw_connections(global_root, connections_data, port_cell_mappings, all_devices_from_api)
    else:
        print(f"  Brak danych o połączeniach w {connections_json_path} lub błąd odczytu.")

    file_io.save_diagram_xml(generator.get_tree(), output_path)
    end_time = time.time()
    print(f"=== Zakończono Fazę Generowania Diagramu (czas: {end_time - start_time:.2f} sek.) ===")

# Główny blok wykonawczy (bez zmian)
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Narzędzie do odkrywania połączeń sieciowych i generowania diagramów Draw.io.")
    parser.add_argument("--discover", action="store_true", help="Uruchom tylko fazę odkrywania.")
    parser.add_argument("--diagram", action="store_true", help="Uruchom tylko fazę generowania diagramu.")
    parser.add_argument("--ip-list", default=IP_LIST_FILE, help=f"Plik z listą IP/Hostname urządzeń do umieszczenia na diagramie (domyślnie: {IP_LIST_FILE}).")
    parser.add_argument("--conn-txt", default=CONNECTIONS_TXT_FILE, help=f"Plik .txt z wynikowymi połączeniami (domyślnie: {CONNECTIONS_TXT_FILE}).")
    parser.add_argument("--conn-json", default=CONNECTIONS_JSON_FILE, help=f"Plik .json z wynikowymi połączeniami (domyślnie: {CONNECTIONS_JSON_FILE}).")
    parser.add_argument("--template", default=DIAGRAM_TEMPLATE_FILE, help=f"Plik szablonu .drawio urządzenia (domyślnie: {DIAGRAM_TEMPLATE_FILE}).")
    parser.add_argument("--diagram-out", default=DIAGRAM_OUTPUT_FILE, help=f"Plik wyjściowy diagramu .drawio (domyślnie: {DIAGRAM_OUTPUT_FILE}).")
    parser.add_argument("--no-verify-ssl", action="store_true", help="Wyłącz weryfikację SSL dla API LibreNMS.")
    args = parser.parse_args()

    run_discovery_flag = args.discover
    run_diagram_flag = args.diagram
    if not run_discovery_flag and not run_diagram_flag:
        print("Nie podano flagi --discover ani --diagram. Domyślnie uruchamiam obie fazy.")
        run_discovery_flag = True
        run_diagram_flag = True

    print("--- Uruchamianie Aplikacji ---")
    app_start_time = time.time()
    try: env_config = config_loader.get_env_config()
    except ValueError as e: print(f"Błąd krytyczny konfiguracji .env: {e}"); sys.exit(1)
    except FileNotFoundError: print("Błąd krytyczny: Plik .env nie został znaleziony."); sys.exit(1)
    except Exception as e: print(f"Nieoczekiwany błąd ładowania konfiguracji .env: {e}"); sys.exit(1)

    api = LibreNMSAPI(env_config.get("base_url"), env_config.get("api_key"), verify_ssl=(not args.no_verify_ssl))
    if not env_config.get("base_url") or not env_config.get("api_key"): print("Błąd krytyczny: Brak base_url lub api_key w konfiguracji .env"); sys.exit(1)

    if run_discovery_flag: run_discovery(env_config, api, args.ip_list, args.conn_txt, args.conn_json)
    if run_diagram_flag:
        if not os.path.exists(args.template): print(f"⚠ Błąd: Plik szablonu '{args.template}' nie istnieje. Nie można wygenerować diagramu.")
        elif not os.path.exists(args.conn_json): print(f"⚠ Plik połączeń '{args.conn_json}' nie istnieje lub jest pusty. Linie połączeń nie zostaną narysowane."); run_diagram_generation(env_config, api, args.ip_list, args.template, args.diagram_out, args.conn_json)
        else: run_diagram_generation(env_config, api, args.ip_list, args.template, args.diagram_out, args.conn_json)

    app_end_time = time.time()
    print(f"\n--- Zakończono. Całkowity czas: {app_end_time - app_start_time:.2f} sek. ---")