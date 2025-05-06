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
# Importujemy teraz potrzebne elementy z drawio_device_builder
import drawio_device_builder # Importujemy cały moduł, żeby mieć dostęp do stałej WAYPOINT_OFFSET
# Jawnie importujemy potrzebne funkcje i klasy/typy
from drawio_device_builder import (
    calculate_dynamic_device_size,
    add_device_to_diagram,
    StyleInfo,
    PortEndpointData, # Importujemy definicję NamedTuple
    _extract_styles_from_template # Importujemy funkcję do stylów
)
import drawio_utils
from utils import find_device_in_list, get_canonical_identifier

logger = logging.getLogger(__name__)

class DiagramGenerator:
    """Klasa odpowiedzialna za generowanie diagramu sieciowego w formacie Draw.io."""

    def __init__(self, api_client: LibreNMSAPI, config: Dict[str, Any],
                 ip_list_path: str, template_path: str, output_path: str,
                 connections_json_path: str):
        self.api_client = api_client
        self.config = config
        self.ip_list_path = ip_list_path
        self.template_path = template_path # Używany do wczytania stylów
        self.output_path = output_path
        self.connections_json_path = connections_json_path

        self.all_devices_from_api: List[Dict] = []
        self.target_devices_info: List[Dict] = [] # Info z API dla urządzeń docelowych
        self.port_mappings: Dict[Any, Dict[Any, PortEndpointData]] = {} # Zmieniono typ wartości w mapowaniu
        self.xml_generator = drawio_base.DrawioXMLGenerator()
        self.global_root_cell = self.xml_generator.get_root_element()
        # Wczytaj style raz przy inicjalizacji
        try:
             self.device_styles: StyleInfo = _extract_styles_from_template(self.template_path)
        except FileNotFoundError:
             logger.error(f"Nie znaleziono pliku szablonu '{self.template_path}' do wczytania stylów. Używanie stylów domyślnych.")
             self.device_styles = StyleInfo() # Użyj domyślnych stylów
        except Exception as e:
             logger.error(f"Nieoczekiwany błąd podczas wczytywania stylów z '{self.template_path}': {e}. Używanie stylów domyślnych.")
             self.device_styles = StyleInfo()


    def generate_diagram(self) -> None:
        """Główna metoda uruchamiająca proces generowania diagramu."""
        logger.info(f"[Diagram 1/4] Wczytywanie listy urządzeń z {self.ip_list_path}...")
        target_ips_or_hosts = file_io.load_ip_list(self.ip_list_path)
        if not target_ips_or_hosts:
            logger.warning("Brak urządzeń na liście. Diagram nie zostanie wygenerowany.")
            return

        logger.info("[Diagram 2/4] Pobieranie listy urządzeń z API...")
        self.all_devices_from_api = self.api_client.get_devices(columns="device_id,hostname,ip,sysName,purpose")
        if not self.all_devices_from_api:
            logger.error("Nie udało się pobrać listy urządzeń z API. Diagram nie zostanie wygenerowany.")
            return

        logger.info("[Diagram 3/4] Identyfikacja urządzeń docelowych i obliczanie layoutu...")
        self._prepare_targets_and_calculate_layout(target_ips_or_hosts)
        if not self.target_devices_info:
             logger.warning("Brak urządzeń z listy docelowej do umieszczenia na diagramie.")
             self._save_diagram() # Zapisz pusty diagram
             return

        logger.info("[Diagram 4/4] Rysowanie połączeń...")
        self._draw_all_connections()

        self._save_diagram()

    def _prepare_targets_and_calculate_layout(self, target_ips_or_hosts: List[str]) -> None:
        """
        Filtruje urządzenia, oblicza ich dynamiczne rozmiary (Faza 1),
        oblicza globalny layout (Grid), a następnie dodaje urządzenia do XML (Faza 2).
        """
        logger.info("Krok 3a: Identyfikacja urządzeń docelowych...")
        target_set = set(str(ip_or_host).lower() for ip_or_host in target_ips_or_hosts)
        self.target_devices_info = []

        device_index = 0
        for device_api_info in self.all_devices_from_api:
            # Sprawdzanie czy urządzenie jest na liście docelowej (logika bez zmian)
            dev_ip = device_api_info.get('ip'); dev_host = device_api_info.get('hostname')
            dev_sysname = device_api_info.get('sysName'); dev_purpose = device_api_info.get('purpose')
            canonical_id_check = get_canonical_identifier(device_api_info)
            potential_ids = set(filter(None, [dev_ip, dev_host, dev_sysname, dev_purpose, canonical_id_check]))
            lowercase_potential_ids = {str(pid).lower() for pid in potential_ids if isinstance(pid, str)}
            is_target = any(str(pid).lower() in target_set for pid in potential_ids) or \
                        any(pid_lower in target_set for pid_lower in lowercase_potential_ids)

            if is_target:
                 device_index += 1
                 device_api_info['_internal_index'] = device_index # Zachowaj unikalny indeks
                 self.target_devices_info.append(device_api_info)
                 current_id_for_log = canonical_id_check or dev_host or dev_ip or f"ID:{device_api_info.get('device_id')}"
                 logger.debug(f"Znaleziono urządzenie docelowe {device_index}: {current_id_for_log}")

        if not self.target_devices_info:
             return

        logger.info(f"Znaleziono {len(self.target_devices_info)} urządzeń docelowych.")
        logger.info("Krok 3b: Obliczanie dynamicznych rozmiarów dla każdego urządzenia (Faza 1 Layoutu)...")
        calculated_sizes: List[Tuple[float, float]] = []
        max_width = 0.0
        max_height = 0.0
        # Stałe minimalne - importowane lub zdefiniowane tutaj
        min_width_fallback = drawio_device_builder.MIN_CHASSIS_WIDTH
        min_height_fallback = drawio_device_builder.MIN_CHASSIS_HEIGHT

        for i, device_info in enumerate(self.target_devices_info):
            current_id_for_log = get_canonical_identifier(device_info) or f"Index {i}"
            logger.debug(f"Obliczanie rozmiaru dla {current_id_for_log}...")
            try:
                 # Wywołaj funkcję obliczającą rozmiar
                 width, height = calculate_dynamic_device_size(device_info, self.api_client)
                 calculated_sizes.append((width, height))
                 max_width = max(max_width, width)
                 max_height = max(max_height, height)
                 logger.debug(f"Obliczony rozmiar dla {current_id_for_log}: {width}x{height}")
            except Exception as e:
                 logger.error(f"Błąd podczas obliczania rozmiaru dla {current_id_for_log}: {e}. Używam rozmiaru minimalnego.")
                 # Użyj zaimportowanych/zdefiniowanych stałych
                 calculated_sizes.append((min_width_fallback, min_height_fallback))
                 max_width = max(max_width, min_width_fallback)
                 max_height = max(max_height, min_height_fallback)


        logger.info(f"Krok 3c: Obliczanie globalnego układu siatki (max wymiary: {max_width:.0f}x{max_height:.0f})...")
        layout_positions = drawio_layout.calculate_grid_layout(
            len(self.target_devices_info), max_width, max_height
        )

        logger.info("Krok 3d: Dodawanie urządzeń do diagramu z dynamicznymi rozmiarami (Faza 2 Layoutu)...")
        self.port_mappings = {} # Resetuj mapowania
        for i, device_info in enumerate(self.target_devices_info):
            current_id_for_log = get_canonical_identifier(device_info) or f"Index {i}"
            device_index = device_info.get('_internal_index', i + 1)
            logger.info(f"\n-- Dodawanie urządzenia {i+1}/{len(self.target_devices_info)}: {current_id_for_log} --")

            # Wywołaj funkcję budującą z drawio_device_builder
            port_map_data = add_device_to_diagram(
                global_root_cell=self.global_root_cell,
                device_info=device_info,
                api_client=self.api_client,
                position=layout_positions[i],
                device_index=device_index,
                styles=self.device_styles # Przekaż wczytane style
            )

            if port_map_data is not None:
                # Mapowanie identyfikatorów (logika bez zmian)
                dev_ip = device_info.get('ip'); dev_host = device_info.get('hostname')
                dev_sysname = device_info.get('sysName'); dev_purpose = device_info.get('purpose')
                canonical_id = get_canonical_identifier(device_info)
                device_identifiers_to_map = set(filter(None, [dev_ip, dev_host, dev_sysname, dev_purpose, canonical_id]))
                lowercase_ids = {str(ident).lower() for ident in device_identifiers_to_map if isinstance(ident, str)}
                device_identifiers_to_map.update(lowercase_ids)

                for identifier in device_identifiers_to_map:
                    if identifier:
                        self.port_mappings[identifier] = port_map_data
                logger.info(f"✓ Zmapowano identyfikatory: {list(device_identifiers_to_map)} na mapę portów urządzenia {current_id_for_log}")
            else:
                logger.warning(f"Brak mapy portów dla urządzenia {current_id_for_log} (funkcja add_device_to_diagram zwróciła None).")


    def _draw_all_connections(self) -> None:
        """Wczytuje dane o połączeniach i rysuje je na diagramie."""
        connections_data = file_io.load_connections_json(self.connections_json_path)
        if connections_data is None or not connections_data:
            logger.warning(f"Brak danych o połączeniach w {self.connections_json_path} lub plik jest pusty. Linie nie zostaną narysowane.")
            return

        logger.info(f"Rysowanie {len(connections_data)} połączeń między urządzeniami...")
        connection_count = 0
        edge_style_base = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;html=1;strokeWidth=1.5;endArrow=none;strokeColor=#FF9900;fontSize=8;"
        missing_devices_logged: Set[str] = set()
        missing_ports_logged: Set[str] = set()
        drawn_links: Set[frozenset] = set()

        # Użyj stałej WAYPOINT_OFFSET z drawio_device_builder
        WAYPOINT_OFFSET = drawio_device_builder.WAYPOINT_OFFSET

        for i, conn in enumerate(connections_data):
            local_dev = conn.get("local_device"); local_port_name = conn.get("local_port")
            remote_dev = conn.get("remote_device"); remote_port_name = conn.get("remote_port")
            vlan = conn.get("vlan"); via = conn.get("discovery_method", "?")
            local_ifindex = conn.get("local_ifindex")
            remote_ifindex = conn.get("remote_ifindex")

            logger.debug(f"\n--- Przetwarzanie Conn #{i}: {local_dev}:{local_port_name} ({local_ifindex}) <-> {remote_dev}:{remote_port_name} ({remote_ifindex}) ---")

            # Podstawowe filtrowanie (bez zmian)
            if not all([local_dev, local_port_name, remote_dev, remote_port_name]):
                logger.debug(f"Conn #{i}: Pomijam - brak kluczowych danych (dev/port).")
                continue
            if str(local_dev).lower() == str(remote_dev).lower():
                logger.debug(f"Conn #{i}: Pomijam - self-connection (wg nazwy).")
                continue

            # Znajdź mapowania dla urządzeń (bez zmian)
            local_map_dev = self._find_port_map(local_dev, conn, "local", missing_devices_logged)
            remote_map_dev = self._find_port_map(remote_dev, conn, "remote", missing_devices_logged)

            if not local_map_dev or not remote_map_dev:
                 logger.debug(f"Conn #{i}: Pomijam - nie znaleziono mapowania dla jednego z urządzeń.")
                 continue

            # Znajdź dane punktów końcowych (bez zmian w logice szukania)
            source_data: Optional[PortEndpointData] = self._find_endpoint_data(local_map_dev, local_port_name, local_ifindex, local_dev, "źródła")
            target_data: Optional[PortEndpointData] = self._find_endpoint_data(remote_map_dev, remote_port_name, remote_ifindex, remote_dev, "celu")

            if not source_data or not target_data:
                self._log_missing_port_data(i, conn, source_data, target_data, missing_ports_logged)
                continue

            # --- Sprawdź kompletność danych i unikalność linku ---
            # >>> POPRAWKA TUTAJ: Dostęp przez kropkę <<<
            source_cell_id = source_data.cell_id
            target_cell_id = target_data.cell_id
            source_x = source_data.x
            source_y = source_data.y
            source_orientation = source_data.orientation
            target_x = target_data.x
            target_y = target_data.y
            target_orientation = target_data.orientation
            # <<< KONIEC POPRAWKI >>>

            # Walidacja pobranych danych (czy nie są None lub niepoprawnego typu)
            # Sprawdzamy, czy mamy wszystkie potrzebne informacje do rysowania
            if not all([source_cell_id, target_cell_id,
                        isinstance(source_x, (int, float)), isinstance(source_y, (int, float)), source_orientation,
                        isinstance(target_x, (int, float)), isinstance(target_y, (int, float)), target_orientation]):
                logger.warning(f"Conn #{i}: BŁĄD - Niekompletne dane punktu końcowego po pobraniu. Source={source_data}, Target={target_data}.")
                continue

            logger.debug(f"Conn #{i}: OK - Znaleziono dane. Source EP ID={source_cell_id}, Target EP ID={target_cell_id}")

            # Sprawdzanie unikalności linku (bez zmian)
            link_key = frozenset([source_cell_id, target_cell_id])
            if link_key in drawn_links:
                logger.debug(f"Conn #{i}: Pomijam - link {link_key} już narysowany.")
                continue

            # Obliczanie waypointów (bez zmian)
            wp_source_x, wp_source_y = self._calculate_waypoint(source_x, source_y, source_orientation, WAYPOINT_OFFSET)
            wp_target_x, wp_target_y = self._calculate_waypoint(target_x, target_y, target_orientation, WAYPOINT_OFFSET)
            waypoints = [(wp_source_x, wp_source_y), (wp_target_x, wp_target_y)]
            logger.debug(f"Conn #{i}: Waypointy: Source WP=({wp_source_x},{wp_source_y}), Target WP=({wp_target_x},{wp_target_y})")

            # Tworzenie krawędzi (bez zmian)
            edge_id = f"conn_edge_{i}_{source_cell_id}_{target_cell_id}"
            edge_style = edge_style_base
            edge_label = f"VLAN {vlan}" if vlan is not None else ""

            edge_cell = drawio_utils.create_floating_edge_cell(
                edge_id=edge_id, parent_id="1", style=edge_style,
                source_point=(source_x, source_y), target_point=(target_x, target_y),
                waypoints=waypoints
            )
            logger.debug(f"Conn #{i}: Tworzenie krawędzi ID '{edge_id}' P1=({source_x},{source_y}), P2=({target_x},{target_y})")

            if edge_label:
                edge_cell.set("value", edge_label)
                drawio_utils.apply_style_change(edge_cell, "labelBackgroundColor", "#FFFFFF")
                drawio_utils.apply_style_change(edge_cell, "fontColor", "#000000")

            self.global_root_cell.append(edge_cell)
            drawn_links.add(link_key)
            connection_count += 1

        logger.info(f"\n✓ Zakończono rysowanie połączeń. Narysowano {connection_count} linii.")


    # --- Funkcje pomocnicze dla rysowania połączeń (bez zmian) ---
    def _find_port_map(self, device_identifier: Any, conn: Dict, side: str, missing_devices_logged: Set) -> Optional[Dict[Any, PortEndpointData]]:
        """Znajduje mapę portów dla urządzenia w self.port_mappings."""
        # Sprawdź oryginalny identyfikator
        port_map = self.port_mappings.get(device_identifier)
        # Sprawdź wersję lowercase, jeśli string
        if port_map is None and isinstance(device_identifier, str):
            port_map = self.port_mappings.get(device_identifier.lower())

        if port_map is None:
            if device_identifier not in missing_devices_logged:
                # Sprawdź, czy urządzenie jest na diagramie pod inną nazwą/IP
                alt_ids = [conn.get(f"{side}_device_ip"), conn.get(f"{side}_device_hostname"), conn.get(f"{side}_device_purpose")]
                is_on_diagram_somewhere = any(
                    alt_id in self.port_mappings or (isinstance(alt_id, str) and alt_id.lower() in self.port_mappings)
                    for alt_id in filter(None, alt_ids)
                )
                status_str = "JEST na diagramie pod inną nazwą/IP!" if is_on_diagram_somewhere else "BRAK go na diagramie"
                # Użyj bardziej widocznego poziomu logowania, np. INFO lub WARNING
                logger.info(f"Urządzenie {side} '{device_identifier}' nie znalezione w mapowaniach portów. Status: {status_str}.")
                missing_devices_logged.add(device_identifier)
            logger.debug(f"BŁĄD - Brak mapy portów dla urządzenia {side} '{device_identifier}'.")
            return None
        return port_map


    def _find_endpoint_data(self, port_map_for_device: Dict[Any, PortEndpointData], port_name: Optional[str], ifindex: Optional[Any], device_name: str, side: str) -> Optional[PortEndpointData]:
        """Wyszukuje dane punktu końcowego dla portu w mapie danego urządzenia."""
        endpoint_data: Optional[PortEndpointData] = None
        lookup_keys_tried: List[str] = []

        # 1. ifIndex (najbardziej wiarygodny)
        if ifindex is not None:
            key_ifindex = f"ifindex_{ifindex}"
            lookup_keys_tried.append(key_ifindex)
            endpoint_data = port_map_for_device.get(key_ifindex)
            if endpoint_data: return endpoint_data

        # 2. Dokładna nazwa portu
        if port_name:
            lookup_keys_tried.append(f"name:'{port_name}'")
            endpoint_data = port_map_for_device.get(port_name)
            if endpoint_data: return endpoint_data

            # 3. Nazwa portu lowercase (jako klucz w mapie)
            if isinstance(port_name, str):
                port_name_lower = port_name.lower()
                if port_name_lower != port_name: # Szukaj tylko jeśli różni się od oryginału
                     lookup_keys_tried.append(f"name_lower:'{port_name_lower}'")
                     endpoint_data = port_map_for_device.get(port_name_lower)
                     if endpoint_data: return endpoint_data

        # 4. Numer wizualny (fallback - jeśli dynamiczny builder dodaje mapowanie po numerze)
        # Sprawdźmy, czy port_name wygląda jak numer lub kończy się numerem
        visual_num_key: Optional[str] = None
        if port_name and isinstance(port_name, str) :
            if port_name.isdigit():
                 visual_num_key = port_name
            else:
                 match_num = re.search(r'(\d+)$', port_name)
                 if match_num:
                      # Użyj samego numeru jako klucza (np. '1', '2', ...)
                      visual_num_key = match_num.group(1)

        if visual_num_key:
            lookup_keys_tried.append(f"vis_num:'{visual_num_key}'")
            endpoint_data = port_map_for_device.get(visual_num_key)
            if endpoint_data: return endpoint_data

        # 5. Sprawdź ifAlias i ifDescr jako klucze (jeśli są w conn)
        # Te pola nie są standardowo w conn, ale można by je dodać przy enrich
        # if conn.get(f"{side}_port_alias"): ...
        # if conn.get(f"{side}_port_descr"): ...

        logger.debug(f"Nie znaleziono danych punktu końcowego dla portu '{port_name}' (ifIndex: {ifindex}) urządzenia '{device_name}' ({side}). Próbowano kluczy: {', '.join(lookup_keys_tried)}.")
        return None # Nie znaleziono

    def _log_missing_port_data(self, conn_index: int, conn: Dict, source_data: Optional[PortEndpointData], target_data: Optional[PortEndpointData], missing_ports_logged: Set) -> None:
        """Loguje informacje o braku danych dla portów."""
        local_port_key = f"{conn.get('local_device')}:{conn.get('local_port')}"
        remote_port_key = f"{conn.get('remote_device')}:{conn.get('remote_port')}"
        log_msg_parts = []
        if not source_data: log_msg_parts.append(f"źródła '{conn.get('local_port')}'")
        if not target_data: log_msg_parts.append(f"celu '{conn.get('remote_port')}'")

        if log_msg_parts:
             should_log = (not source_data and local_port_key not in missing_ports_logged) or \
                          (not target_data and remote_port_key not in missing_ports_logged)
             if should_log:
                 # Użyjmy poziomu INFO, bo to ważna informacja dla użytkownika
                 logger.info(f"[Conn #{conn_index}]: Połączenie NIE zostanie narysowane (brak danych dla portu { ' i '.join(log_msg_parts) }).")
                 logger.debug(f"Conn #{conn_index}: BŁĄD - Nie znaleziono danych portu dla: { ' i '.join(log_msg_parts) }.")
                 if not source_data: missing_ports_logged.add(local_port_key)
                 if not target_data: missing_ports_logged.add(remote_port_key)


    def _calculate_waypoint(self, x: float, y: float, orientation: str, offset: float) -> Tuple[float, float]:
        """Oblicza współrzędne waypointu."""
        wp_x, wp_y = x, y
        if orientation == "up": wp_y -= offset
        elif orientation == "down": wp_y += offset
        elif orientation == "left": wp_x -= offset
        elif orientation == "right": wp_x += offset
        return wp_x, wp_y

    def _save_diagram(self) -> None:
        """Zapisuje wygenerowany diagram do pliku XML."""
        file_io.save_diagram_xml(self.xml_generator.get_tree(), self.output_path)