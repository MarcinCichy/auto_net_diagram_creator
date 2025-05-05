# --- diagram_generator.py ---

import time
import logging
import xml.etree.ElementTree as ET
import re
from typing import Dict, List, Any, Optional, Tuple, Set

from librenms_client import LibreNMSAPI
import file_io
import drawio_base
import drawio_layout
import drawio_device_builder
import drawio_utils
from utils import find_device_in_list, get_canonical_identifier # Używamy utils

logger = logging.getLogger(__name__)

class DiagramGenerator:
    """Klasa odpowiedzialna za generowanie diagramu sieciowego w formacie Draw.io."""

    def __init__(self, api_client: LibreNMSAPI, config: Dict[str, Any],
                 ip_list_path: str, template_path: str, output_path: str,
                 connections_json_path: str):
        self.api_client = api_client
        self.config = config
        self.ip_list_path = ip_list_path
        self.template_path = template_path
        self.output_path = output_path
        self.connections_json_path = connections_json_path

        self.all_devices_from_api: List[Dict] = []
        self.target_devices_details: List[Dict] = [] # Szczegóły urządzeń do umieszczenia
        self.port_mappings: Dict[Any, Dict] = {} # Mapowanie: identyfikator_urzadzenia -> {mapa_portow}
        self.xml_generator = drawio_base.DrawioXMLGenerator()
        self.global_root_cell = self.xml_generator.get_root_element()

    def generate_diagram(self) -> None:
        """Główna metoda uruchamiająca proces generowania diagramu."""
        logger.info(f"[Diagram 1/5] Wczytywanie listy urządzeń z {self.ip_list_path}...")
        target_ips_or_hosts = file_io.load_ip_list(self.ip_list_path)
        if not target_ips_or_hosts:
            logger.warning("Brak urządzeń na liście. Diagram nie zostanie wygenerowany.")
            return

        logger.info("[Diagram 2/5] Pobieranie listy urządzeń z API...")
        # Pobieramy kolumny potrzebne do identyfikacji i rysowania
        self.all_devices_from_api = self.api_client.get_devices(columns="device_id,hostname,ip,sysName,purpose")
        if not self.all_devices_from_api:
            logger.error("Nie udało się pobrać listy urządzeń z API. Diagram nie zostanie wygenerowany.")
            return

        logger.info("[Diagram 3/5] Przygotowywanie danych urządzeń docelowych...")
        self._prepare_target_device_details(target_ips_or_hosts)
        if not self.target_devices_details:
             logger.warning("Brak urządzeń z listy docelowej do umieszczenia na diagramie.")
             # Mimo to zapisujemy pusty diagram, jeśli takie jest oczekiwane zachowanie
             self._save_diagram()
             return

        logger.info("[Diagram 4/5] Obliczanie layoutu i dodawanie urządzeń do diagramu...")
        self._calculate_layout_and_add_devices()

        logger.info("[Diagram 5/5] Rysowanie połączeń...")
        self._draw_all_connections()

        # Zapis finalnego diagramu
        self._save_diagram()

    def _prepare_target_device_details(self, target_ips_or_hosts: List[str]) -> None:
        """Filtruje urządzenia API i przygotowuje dane do layoutu i rysowania."""
        logger.info("Krok 4a: Identyfikacja urządzeń docelowych i ładowanie szablonów...")
        target_set = set(str(ip_or_host).lower() for ip_or_host in target_ips_or_hosts) # Normalizacja do string i lower

        max_template_width, max_template_height = 0, 0
        device_index = 0 # Unikalny indeks dla każdego urządzenia na diagramie

        for device_api_info in self.all_devices_from_api:
            dev_ip = device_api_info.get('ip')
            dev_host = device_api_info.get('hostname')
            dev_sysname = device_api_info.get('sysName')
            dev_purpose = device_api_info.get('purpose')
            canonical_id_check = get_canonical_identifier(device_api_info)

            potential_ids = set(filter(None, [dev_ip, dev_host, dev_sysname, dev_purpose, canonical_id_check]))
            # Dodaj wersje lowercase dla porównania
            lowercase_potential_ids = {str(pid).lower() for pid in potential_ids if isinstance(pid, str)}

            # Sprawdź, czy jakikolwiek identyfikator (oryginalny lub lowercase) jest w target_set
            is_target = any(str(pid).lower() in target_set for pid in potential_ids) or \
                        any(pid_lower in target_set for pid_lower in lowercase_potential_ids)


            if not is_target:
                continue

            # To urządzenie jest na liście docelowej
            device_index += 1
            current_id_for_log = canonical_id_check if canonical_id_check else (dev_host or dev_ip or f"ID:{device_api_info.get('device_id')}")
            logger.info(f"\n-- Znaleziono urządzenie docelowe {device_index}: {current_id_for_log} --")

            template_cells, t_width, t_height = drawio_device_builder.load_and_prepare_template(
                self.template_path, device_index
            )
            if template_cells is None:
                logger.warning(f"Nie udało się załadować szablonu dla '{current_id_for_log}'. Pomijam.")
                continue

            canonical_id = canonical_id_check if canonical_id_check else f"unknown_dev_{device_index}"
            # Zbierz wszystkie możliwe identyfikatory do mapowania portów
            device_identifiers_to_map = set(filter(None, [dev_ip, dev_host, dev_sysname, dev_purpose, canonical_id]))
            lowercase_ids = {ident.lower() for ident in device_identifiers_to_map if isinstance(ident, str)}
            device_identifiers_to_map.update(lowercase_ids)

            self.target_devices_details.append({
                "identifiers": list(device_identifiers_to_map), # Lista dla mapowania portów
                "canonical_id": canonical_id,                   # Główny identyfikator
                "info": device_api_info,                        # Pełne info z API
                "template_cells": template_cells,               # Przygotowane komórki szablonu
                "width": t_width,
                "height": t_height,
                "index": device_index                           # Unikalny indeks na diagramie
            })
            max_template_width = max(max_template_width, t_width)
            max_template_height = max(max_template_height, t_height)

        # Zapisz maksymalne wymiary do wykorzystania w layout
        self.max_template_width = max_template_width
        self.max_template_height = max_template_height


    def _calculate_layout_and_add_devices(self) -> None:
        """Oblicza layout i dodaje urządzenia (komórki) do XML."""
        num_devices = len(self.target_devices_details)
        logger.info(f"Krok 4b: Obliczanie layoutu dla {num_devices} urządzeń...")
        layout_positions = drawio_layout.calculate_grid_layout(
            num_devices, self.max_template_width, self.max_template_height
        )

        logger.info("Krok 4c: Dodawanie urządzeń do diagramu...")
        for i, device_data in enumerate(self.target_devices_details):
            current_id_for_log = device_data.get("canonical_id", f"Index {i}")
            logger.info(f"\n-- Dodawanie urządzenia {i+1}/{num_devices}: {current_id_for_log} --")

            # Zwracana mapa: {'port_id_or_ifindex': {'cell_id': ..., 'x': ..., 'y': ..., 'orientation': ...}}
            port_map_data = drawio_device_builder.add_device_to_diagram(
                global_root_cell=self.global_root_cell,
                template_cells=device_data["template_cells"],
                template_width=device_data["width"],
                template_height=device_data["height"],
                device_info=device_data["info"],
                api_client=self.api_client,
                position=layout_positions[i],
                device_index=device_data["index"]
            )

            if port_map_data is not None:
                # Mapowanie WSZYSTKICH identyfikatorów na tę samą mapę portów
                for identifier in device_data["identifiers"]:
                    if identifier:
                        self.port_mappings[identifier] = port_map_data
                logger.info(f"✓ Zmapowano identyfikatory: {device_data['identifiers']} na mapę portów urządzenia {current_id_for_log}")
            else:
                logger.warning(f"Brak mapy portów dla urządzenia {current_id_for_log}.")


    def _draw_all_connections(self) -> None:
        """Wczytuje dane o połączeniach i rysuje je na diagramie."""
        connections_data = file_io.load_connections_json(self.connections_json_path)
        if connections_data is None or not connections_data:
            logger.warning(f"Brak danych o połączeniach w {self.connections_json_path} lub plik jest pusty. Linie nie zostaną narysowane.")
            return

        logger.info(f"Krok 4d: Rysowanie {len(connections_data)} połączeń między urządzeniami...")
        connection_count = 0
        edge_style_base = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;html=1;strokeWidth=1.5;endArrow=none;strokeColor=#FF9900;fontSize=8;"
        missing_devices_logged: Set[str] = set()
        missing_ports_logged: Set[str] = set()
        drawn_links: Set[frozenset] = set() # Klucz: frozenset ID komórek końcowych

        WAYPOINT_OFFSET = drawio_device_builder.WAYPOINT_OFFSET # Pobierz stałą

        for i, conn in enumerate(connections_data):
            local_dev = conn.get("local_device")
            local_port_name = conn.get("local_port")
            remote_dev = conn.get("remote_device")
            remote_port_name = conn.get("remote_port")
            vlan = conn.get("vlan")
            via = conn.get("discovery_method", "?")
            local_ifindex = conn.get("local_ifindex")
            remote_ifindex = conn.get("remote_ifindex")

            logger.debug(f"\n--- Przetwarzanie Conn #{i}: {local_dev}:{local_port_name} ({local_ifindex}) <-> {remote_dev}:{remote_port_name} ({remote_ifindex}) ---")

            if not all([local_dev, local_port_name, remote_dev, remote_port_name]):
                logger.debug(f"Conn #{i}: Pomijam - brak kluczowych danych (dev/port).")
                continue
            # Sprawdzenie self-connection już w deduplikacji, ale dla pewności
            if str(local_dev).lower() == str(remote_dev).lower():
                logger.debug(f"Conn #{i}: Pomijam - self-connection (wg nazwy).")
                continue


            # --- Znajdź mapowania portów dla urządzeń ---
            local_map_dev = self._find_port_map(local_dev, conn, "local", missing_devices_logged)
            remote_map_dev = self._find_port_map(remote_dev, conn, "remote", missing_devices_logged)

            if not local_map_dev or not remote_map_dev:
                 logger.debug(f"Conn #{i}: Pomijam - nie znaleziono mapowania dla jednego z urządzeń.")
                 continue # Błąd logowany w _find_port_map

            # --- Znajdź dane punktów końcowych dla portów ---
            source_data = self._find_endpoint_data(local_map_dev, local_port_name, local_ifindex, local_dev, "źródła")
            target_data = self._find_endpoint_data(remote_map_dev, remote_port_name, remote_ifindex, remote_dev, "celu")

            if not source_data or not target_data:
                self._log_missing_port_data(i, conn, source_data, target_data, missing_ports_logged)
                continue

            # --- Sprawdź kompletność danych i unikalność linku ---
            source_cell_id = source_data.get("cell_id")
            target_cell_id = target_data.get("cell_id")
            source_x = source_data.get("x")
            source_y = source_data.get("y")
            source_orientation = source_data.get("orientation")
            target_x = target_data.get("x")
            target_y = target_data.get("y")
            target_orientation = target_data.get("orientation")

            if not all([source_cell_id, target_cell_id,
                        isinstance(source_x, (int, float)), isinstance(source_y, (int, float)), source_orientation,
                        isinstance(target_x, (int, float)), isinstance(target_y, (int, float)), target_orientation]):
                logger.debug(f"Conn #{i}: BŁĄD - Brak pełnych danych (współrzędne/orientacja/id) do narysowania linii. Source={source_data}, Target={target_data}.")
                continue

            logger.debug(f"Conn #{i}: OK - Znaleziono dane i współrzędne. Source=({source_x},{source_y} orient={source_orientation}), Target=({target_x},{target_y} orient={target_orientation}).")

            link_key = frozenset([source_cell_id, target_cell_id])
            if link_key in drawn_links:
                logger.debug(f"Conn #{i}: Pomijam - link {link_key} (wg ID komórek: {source_cell_id} <-> {target_cell_id}) już narysowany.")
                continue

            # --- Oblicz waypointy ---
            wp_source_x, wp_source_y = self._calculate_waypoint(source_x, source_y, source_orientation, WAYPOINT_OFFSET)
            wp_target_x, wp_target_y = self._calculate_waypoint(target_x, target_y, target_orientation, WAYPOINT_OFFSET)
            waypoints = [(wp_source_x, wp_source_y), (wp_target_x, wp_target_y)]
            logger.debug(f"Conn #{i}: Waypointy: Source WP=({wp_source_x},{wp_source_y}), Target WP=({wp_target_x},{wp_target_y})")

            # --- Utwórz i dodaj krawędź ---
            edge_id = f"conn_edge_{i}_{source_cell_id}_{target_cell_id}"
            edge_style = edge_style_base
            edge_label = f"VLAN {vlan}" if vlan is not None else ""

            edge_cell = drawio_utils.create_floating_edge_cell(
                edge_id=edge_id,
                parent_id="1", # Krawędzie są zwykle dziećmi warstwy '1'
                style=edge_style,
                source_point=(source_x, source_y),
                target_point=(target_x, target_y),
                waypoints=waypoints
            )
            logger.debug(f"Conn #{i}: Tworzenie pływającej krawędzi ID '{edge_id}' P1=({source_x},{source_y}), P2=({target_x},{target_y}), Waypointy={waypoints}, Style='{edge_style}', Label='{edge_label}'")

            if edge_label:
                edge_cell.set("value", edge_label)
                drawio_utils.apply_style_change(edge_cell, "labelBackgroundColor", "#FFFFFF")
                drawio_utils.apply_style_change(edge_cell, "fontColor", "#000000")

            self.global_root_cell.append(edge_cell)
            drawn_links.add(link_key)
            connection_count += 1

        logger.info(f"\n✓ Zakończono rysowanie połączeń. Narysowano {connection_count} linii.")


    def _find_port_map(self, device_identifier: Any, conn: Dict, side: str, missing_devices_logged: Set) -> Optional[Dict]:
        """Pomocnicza funkcja do znajdowania mapy portów dla danego urządzenia."""
        # Spróbuj z oryginalnym identyfikatorem
        port_map = self.port_mappings.get(device_identifier)
        # Spróbuj z wersją lowercase, jeśli to string
        if not port_map and isinstance(device_identifier, str):
            port_map = self.port_mappings.get(device_identifier.lower())

        if not port_map:
            if device_identifier not in missing_devices_logged:
                # Sprawdź, czy urządzenie JEST na diagramie pod inną nazwą
                remote_ip = conn.get(f"{side}_device_ip")
                remote_host = conn.get(f"{side}_device_hostname")
                remote_purpose = conn.get(f"{side}_device_purpose")
                is_on_diagram_somewhere = any(
                    alt_id in self.port_mappings or (isinstance(alt_id, str) and alt_id.lower() in self.port_mappings)
                    for alt_id in filter(None, [remote_ip, remote_host, remote_purpose])
                )
                status_str = "JEST na diagramie pod inną nazwą/IP!" if is_on_diagram_somewhere else "BRAK go na diagramie"
                logger.info(f"Urządzenie {side} '{device_identifier}' nie znalezione w mapowaniach portów. Status: {status_str}.")
                missing_devices_logged.add(device_identifier)
            logger.debug(f"BŁĄD - Brak mapy portów dla urządzenia {side} '{device_identifier}'.")
            return None
        return port_map

    def _find_endpoint_data(self, port_map_for_device: Dict, port_name: str, ifindex: Optional[Any], device_name: str, side: str) -> Optional[Dict]:
        """Pomocnicza funkcja do znajdowania danych punktu końcowego dla portu."""
        endpoint_data = None
        lookup_keys_tried = []

        # 1. Spróbuj po ifIndex (najbardziej wiarygodne)
        if ifindex is not None:
            key_ifindex = f"ifindex_{ifindex}"
            lookup_keys_tried.append(f"ifindex ({key_ifindex})")
            endpoint_data = port_map_for_device.get(key_ifindex)
            if endpoint_data: return endpoint_data

        # 2. Spróbuj po dokładnej nazwie portu
        if port_name:
             lookup_keys_tried.append(f"dokładna nazwa ({port_name})")
             endpoint_data = port_map_for_device.get(port_name)
             if endpoint_data: return endpoint_data

             # 3. Spróbuj po nazwie portu lowercase (fallback)
             if isinstance(port_name, str):
                 port_name_lower = port_name.lower()
                 lookup_keys_tried.append(f"nazwa lowercase ({port_name_lower})")
                 # Iteruj, bo klucze w mapie mogą mieć różną wielkość liter
                 for map_key, map_value in port_map_for_device.items():
                      if isinstance(map_key, str) and map_key.lower() == port_name_lower:
                          return map_value

        # 4. Fallback: Spróbuj po samym numerze z nazwy portu (jeśli jest na końcu)
        if isinstance(port_name, str):
            match_num = re.search(r'(\d+)$', port_name)
            if match_num:
                key_num = match_num.group(1)
                lookup_keys_tried.append(f"fallback numer ({key_num})")
                endpoint_data = port_map_for_device.get(key_num)
                if endpoint_data: return endpoint_data

        logger.debug(f"Nie znaleziono danych punktu końcowego dla portu '{port_name}' (ifIndex: {ifindex}) urządzenia '{device_name}' ({side}). Próbowano kluczy: {', '.join(lookup_keys_tried)}.")
        return None # Nie znaleziono

    def _log_missing_port_data(self, conn_index: int, conn: Dict, source_data: Optional[Dict], target_data: Optional[Dict], missing_ports_logged: Set) -> None:
        """Loguje informacje o braku danych dla portów."""
        local_port_key = f"{conn.get('local_device')}:{conn.get('local_port')}"
        remote_port_key = f"{conn.get('remote_device')}:{conn.get('remote_port')}"
        log_msg_parts = []
        if not source_data: log_msg_parts.append(f"źródła '{conn.get('local_port')}'")
        if not target_data: log_msg_parts.append(f"celu '{conn.get('remote_port')}'")

        if log_msg_parts:
             # Loguj tylko raz dla danego portu
             should_log = (not source_data and local_port_key not in missing_ports_logged) or \
                          (not target_data and remote_port_key not in missing_ports_logged)
             if should_log:
                 logger.info(f"[Conn #{conn_index}]: Połączenie NIE zostało narysowane (brak danych dla portu { ' i '.join(log_msg_parts) }).")
                 logger.debug(f"Conn #{conn_index}: BŁĄD - Nie znaleziono danych portu dla: { ' i '.join(log_msg_parts) }.")
                 if not source_data: missing_ports_logged.add(local_port_key)
                 if not target_data: missing_ports_logged.add(remote_port_key)


    def _calculate_waypoint(self, x: float, y: float, orientation: str, offset: float) -> Tuple[float, float]:
        """Oblicza współrzędne waypointu na podstawie punktu końcowego i orientacji."""
        wp_x, wp_y = x, y
        if orientation == "up": wp_y -= offset
        elif orientation == "down": wp_y += offset
        elif orientation == "left": wp_x -= offset
        elif orientation == "right": wp_x += offset
        return wp_x, wp_y

    def _save_diagram(self) -> None:
        """Zapisuje wygenerowany diagram do pliku XML."""
        file_io.save_diagram_xml(self.xml_generator.get_tree(), self.output_path)