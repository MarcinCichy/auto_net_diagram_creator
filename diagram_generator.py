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
import drawio_device_builder  # Importujemy cały moduł
from drawio_device_builder import (
    calculate_dynamic_device_size,
    add_device_to_diagram as add_device_to_drawio_diagram,  # Alias dla jasności
    StyleInfo,
    PortEndpointData,
    _extract_styles_from_template,
    WAYPOINT_OFFSET as DRAWIO_WAYPOINT_OFFSET
)
import svg_generator  # Importujemy nasz nowy moduł SVG
import drawio_utils
from utils import find_device_in_list, get_canonical_identifier

logger = logging.getLogger(__name__)


class DiagramGenerator:
    """Klasa odpowiedzialna za generowanie diagramu sieciowego w formatach Draw.io i SVG."""

    def __init__(self, api_client: LibreNMSAPI, config: Dict[str, Any],
                 ip_list_path: str, template_path: str,
                 output_path_drawio: str,
                 output_path_svg: str,
                 connections_json_path: str):
        self.api_client = api_client
        self.config = config
        self.ip_list_path = ip_list_path
        self.template_path = template_path  # Używany do stylów Draw.io i jako referencja dla SVG
        self.output_path_drawio = output_path_drawio
        self.output_path_svg = output_path_svg
        self.connections_json_path = connections_json_path

        self.all_devices_from_api: List[Dict] = []
        self.target_devices_info: List[Dict] = []
        # To mapowanie będzie używane przez obie logiki rysowania połączeń.
        # Ważne, aby PortEndpointData.x i .y były globalnymi współrzędnymi.
        self.port_mappings: Dict[Any, Dict[Any, PortEndpointData]] = {}

        # Inicjalizacja dla Draw.io
        self.drawio_xml_generator: Optional[drawio_base.DrawioXMLGenerator] = drawio_base.DrawioXMLGenerator()
        self.global_drawio_root_cell: Optional[ET.Element] = self.drawio_xml_generator.get_root_element()
        try:
            # Style te będą używane przez Draw.io i jako referencja dla SVG
            self.device_styles_drawio_ref: StyleInfo = _extract_styles_from_template(self.template_path)
        except FileNotFoundError:
            logger.error(
                f"Nie znaleziono pliku szablonu '{self.template_path}' do wczytania stylów. Używanie stylów domyślnych.")
            self.device_styles_drawio_ref = StyleInfo()
        except Exception as e:
            logger.error(
                f"Nieoczekiwany błąd wczytywania stylów z '{self.template_path}': {e}. Używanie stylów domyślnych.")
            self.device_styles_drawio_ref = StyleInfo()

        # Inicjalizacja dla SVG
        # Wymiary SVG zostaną zaktualizowane po obliczeniu layoutu
        self.svg_diagram_obj: Optional[svg_generator.SVGDiagram] = svg_generator.SVGDiagram()

    def generate_diagram(self) -> None:
        """Główna metoda uruchamiająca proces generowania diagramów."""
        logger.info(f"[Diagram 1/4] Wczytywanie listy urządzeń z {self.ip_list_path}...")
        target_ips_or_hosts = file_io.load_ip_list(self.ip_list_path)
        if not target_ips_or_hosts:
            logger.warning("Brak urządzeń na liście. Diagramy nie zostaną wygenerowane.")
            return

        logger.info("[Diagram 2/4] Pobieranie listy urządzeń z API...")
        self.all_devices_from_api = self.api_client.get_devices(columns="device_id,hostname,ip,sysName,purpose")
        if not self.all_devices_from_api:
            logger.error("Nie udało się pobrać listy urządzeń z API. Diagramy nie zostaną wygenerowane.")
            return

        logger.info("[Diagram 3/4] Identyfikacja urządzeń docelowych i obliczanie layoutu...")
        max_diag_width, max_diag_height = self._prepare_targets_and_calculate_layout(target_ips_or_hosts)

        if self.svg_diagram_obj:
            # Ustaw ostateczne wymiary dla płótna SVG na podstawie obliczonego layoutu
            # Dodajemy marginesy, aby obiekty nie były na krawędzi
            final_svg_width = max_diag_width + drawio_layout.DEFAULT_MARGIN_X * 1.5  # Trochę większy margines
            final_svg_height = max_diag_height + drawio_layout.DEFAULT_MARGIN_Y * 1.5
            self.svg_diagram_obj.update_dimensions(final_svg_width, final_svg_height)
            logger.info(f"Ustawiono wymiary SVG: {final_svg_width:.0f}x{final_svg_height:.0f}")

        if not self.target_devices_info:
            logger.warning("Brak urządzeń z listy docelowej do umieszczenia na diagramach.")
            self._save_diagrams()
            return

        logger.info("[Diagram 4/4] Rysowanie połączeń...")
        self._draw_all_connections()

        self._save_diagrams()

    def _prepare_targets_and_calculate_layout(self, target_ips_or_hosts: List[str]) -> Tuple[float, float]:
        """
        Filtruje urządzenia, oblicza ich dynamiczne rozmiary,
        oblicza globalny layout, a następnie dodaje urządzenia do obu diagramów.
        Zwraca maksymalne potrzebne wymiary diagramu (szerokość, wysokość) bez końcowych marginesów.
        """
        logger.info("Krok 3a: Identyfikacja urządzeń docelowych...")
        target_set = set(str(ip_or_host).lower() for ip_or_host in target_ips_or_hosts)
        self.target_devices_info = []
        device_idx_counter = 0  # Unikalny indeks dla ID elementów
        for device_api_info in self.all_devices_from_api:
            dev_ip = device_api_info.get('ip');
            dev_host = device_api_info.get('hostname')
            dev_sysname = device_api_info.get('sysName');
            dev_purpose = device_api_info.get('purpose')
            canonical_id_check = get_canonical_identifier(device_api_info)
            potential_ids = set(filter(None, [dev_ip, dev_host, dev_sysname, dev_purpose, canonical_id_check]))
            lowercase_potential_ids = {str(pid).lower() for pid in potential_ids if isinstance(pid, str)}
            is_target = any(str(pid).lower() in target_set for pid in potential_ids) or \
                        any(pid_lower in target_set for pid_lower in lowercase_potential_ids)

            if is_target:
                device_idx_counter += 1
                device_api_info['_internal_index'] = device_idx_counter
                self.target_devices_info.append(device_api_info)
                current_id_for_log = canonical_id_check or dev_host or dev_ip or f"ID:{device_api_info.get('device_id')}"
                logger.debug(f"Znaleziono urządzenie docelowe ({device_idx_counter}): {current_id_for_log}")

        if not self.target_devices_info:
            logger.warning("Brak urządzeń docelowych po wstępnym filtrowaniu.")
            return 0.0, 0.0

        logger.info(f"Znaleziono {len(self.target_devices_info)} urządzeń docelowych do umieszczenia na diagramach.")
        logger.info("Krok 3b: Obliczanie dynamicznych rozmiarów dla każdego urządzenia...")
        calculated_sizes: List[Tuple[float, float]] = []
        max_item_width = 0.0
        max_item_height = 0.0
        size_calculator = calculate_dynamic_device_size  # Z drawio_device_builder

        for i, device_info in enumerate(self.target_devices_info):
            current_id_for_log = get_canonical_identifier(device_info) or f"Index {i}"
            logger.debug(f"Obliczanie rozmiaru dla {current_id_for_log}...")
            try:
                width, height = size_calculator(device_info, self.api_client)
                calculated_sizes.append((width, height))
                max_item_width = max(max_item_width, width)
                max_item_height = max(max_item_height, height)
                logger.debug(f"Obliczony rozmiar dla {current_id_for_log}: {width}x{height}")
            except Exception as e:
                min_w_fallback = drawio_device_builder.MIN_CHASSIS_WIDTH
                min_h_fallback = drawio_device_builder.MIN_CHASSIS_HEIGHT
                logger.error(
                    f"Błąd podczas obliczania rozmiaru dla {current_id_for_log}: {e}. Używam {min_w_fallback}x{min_h_fallback}.")
                calculated_sizes.append((min_w_fallback, min_h_fallback))
                max_item_width = max(max_item_width, min_w_fallback)
                max_item_height = max(max_item_height, min_h_fallback)

        logger.info(
            f"Krok 3c: Obliczanie globalnego układu siatki (max wymiary elementu: {max_item_width:.0f}x{max_item_height:.0f})...")
        layout_positions = drawio_layout.calculate_grid_layout(
            len(self.target_devices_info), max_item_width, max_item_height
        )

        total_diagram_width_content = 0
        total_diagram_height_content = 0
        if layout_positions:
            for i, (lx, ly) in enumerate(layout_positions):
                item_w, item_h = calculated_sizes[i]
                total_diagram_width_content = max(total_diagram_width_content, lx + item_w)
                total_diagram_height_content = max(total_diagram_height_content, ly + item_h)

        logger.info("Krok 3d: Dodawanie urządzeń do diagramów...")
        self.port_mappings = {}  # Resetuj/Inicjalizuj mapowania
        for i, device_info in enumerate(self.target_devices_info):
            current_id_for_log = get_canonical_identifier(device_info) or f"Index {i}"
            device_internal_idx = device_info.get('_internal_index', i + 1)
            current_position = layout_positions[i]
            logger.info(
                f"-- Dodawanie urządzenia {i + 1}/{len(self.target_devices_info)}: {current_id_for_log} na pozycji {current_position} --")

            port_map_data_for_device: Optional[Dict[Any, PortEndpointData]] = None

            # Generowanie dla Draw.io
            if self.global_drawio_root_cell is not None:
                logger.debug(f"  Rysowanie dla Draw.io: {current_id_for_log}")
                port_map_data_for_device = add_device_to_drawio_diagram(
                    global_root_cell=self.global_drawio_root_cell,
                    device_info=device_info, api_client=self.api_client,
                    position=current_position, device_index=device_internal_idx,
                    styles=self.device_styles_drawio_ref
                )
                # Wypełnij self.port_mappings danymi z Draw.io.
                # SVG będzie polegać na tych samych globalnych koordynatach z PortEndpointData.
                if port_map_data_for_device:
                    dev_ip = device_info.get('ip');
                    dev_host = device_info.get('hostname')
                    dev_sysname = device_info.get('sysName');
                    dev_purpose = device_info.get('purpose')
                    canonical_id = get_canonical_identifier(device_info)
                    ids_to_map = set(filter(None, [dev_ip, dev_host, dev_sysname, dev_purpose, canonical_id]))
                    lowercase_ids = {str(ident).lower() for ident in ids_to_map if isinstance(ident, str)}
                    ids_to_map.update(lowercase_ids)
                    for identifier in ids_to_map:
                        if identifier:
                            self.port_mappings[identifier] = port_map_data_for_device
                    logger.info(f"  Draw.io: Zmapowano porty dla {current_id_for_log}")

            # Generowanie dla SVG
            if self.svg_diagram_obj is not None:
                logger.debug(f"  Rysowanie dla SVG: {current_id_for_log}")
                # svg_add_device_to_diagram również zwraca mapę portów, ale jeśli
                # self.port_mappings jest już wypełnione przez Draw.io, i PortEndpointData
                # zawiera globalne x,y, to nie musimy jej nadpisywać, chyba że SVG potrzebuje
                # innych cell_id lub specyficznych danych. Na razie zakładamy, że SVG może
                # używać tej samej mapy do resolwowania połączeń.
                _ = svg_generator.svg_add_device_to_diagram(  # Zignoruj zwróconą mapę SVG na razie
                    svg_diagram=self.svg_diagram_obj,
                    device_info=device_info, api_client=self.api_client,
                    position=current_position, device_index=device_internal_idx,
                    drawio_styles_ref=self.device_styles_drawio_ref
                )
                logger.info(f"  SVG: Narysowano urządzenie {current_id_for_log}")

        return total_diagram_width_content, total_diagram_height_content

    def _draw_all_connections(self) -> None:
        connections_data = file_io.load_connections_json(self.connections_json_path)
        if not connections_data:
            logger.warning(f"Brak danych o połączeniach w {self.connections_json_path}. Linie nie zostaną narysowane.")
            return

        logger.info(f"Rysowanie {len(connections_data)} połączeń...")
        connection_count = 0
        drawn_links_set: Set[frozenset] = set()  # Wspólny set dla obu formatów, klucz na ID portów/endpointów
        missing_devices_log_set: Set[str] = set()
        missing_ports_log_set: Set[str] = set()

        for i, conn in enumerate(connections_data):
            local_dev = conn.get("local_device");
            local_port_name = conn.get("local_port")
            remote_dev = conn.get("remote_device");
            remote_port_name = conn.get("remote_port")
            vlan = conn.get("vlan");  # via = conn.get("discovery_method", "?")
            local_ifindex = conn.get("local_ifindex")
            remote_ifindex = conn.get("remote_ifindex")

            if not all([local_dev, local_port_name, remote_dev, remote_port_name]): continue

            local_map_dev = self._find_port_map(local_dev, conn, "local", missing_devices_log_set)
            remote_map_dev = self._find_port_map(remote_dev, conn, "remote", missing_devices_log_set)
            if not local_map_dev or not remote_map_dev: continue

            source_data = self._find_endpoint_data(local_map_dev, local_port_name, local_ifindex, str(local_dev),
                                                   "źródła")
            target_data = self._find_endpoint_data(remote_map_dev, remote_port_name, remote_ifindex, str(remote_dev),
                                                   "celu")

            if not source_data or not target_data:
                self._log_missing_port_data(i, conn, source_data, target_data, missing_ports_log_set)
                continue

            # Używamy cell_id z PortEndpointData jako części klucza unikalności.
            # Dla Draw.io to ID komórki endpointu, dla SVG może to być ID portu.
            # Zakładamy, że są one wystarczająco unikalne.
            link_key = frozenset(sorted((str(source_data.cell_id), str(target_data.cell_id))))
            if link_key in drawn_links_set:
                logger.debug(f"Conn #{i}: Pomijam - link {link_key} już narysowany w jednym z formatów.")
                continue

            drawn_this_iteration = False
            # Rysowanie dla Draw.io
            if self.global_drawio_root_cell is not None:
                wp_source_x, wp_source_y = self._calculate_waypoint(source_data.x, source_data.y,
                                                                    source_data.orientation, DRAWIO_WAYPOINT_OFFSET)
                wp_target_x, wp_target_y = self._calculate_waypoint(target_data.x, target_data.y,
                                                                    target_data.orientation, DRAWIO_WAYPOINT_OFFSET)
                waypoints_drawio = [(wp_source_x, wp_source_y), (wp_target_x, wp_target_y)]

                edge_id_drawio = f"conn_edge_drawio_{i}_{source_data.cell_id}_{target_data.cell_id}"
                edge_style_drawio = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;html=1;strokeWidth=1.5;endArrow=none;strokeColor=#FF9900;fontSize=8;"
                edge_label_drawio = f"VLAN {vlan}" if vlan is not None else ""

                edge_cell_drawio = drawio_utils.create_floating_edge_cell(
                    edge_id=edge_id_drawio, parent_id="1", style=edge_style_drawio,
                    source_point=(source_data.x, source_data.y), target_point=(target_data.x, target_data.y),
                    waypoints=waypoints_drawio
                )
                if edge_label_drawio:
                    edge_cell_drawio.set("value", edge_label_drawio)
                    drawio_utils.apply_style_change(edge_cell_drawio, "labelBackgroundColor", "#FFFFFF")
                    drawio_utils.apply_style_change(edge_cell_drawio, "fontColor", "#000000")
                self.global_drawio_root_cell.append(edge_cell_drawio)
                drawn_this_iteration = True

            # Rysowanie dla SVG
            if self.svg_diagram_obj is not None:
                svg_generator.svg_draw_connection(
                    svg_diagram=self.svg_diagram_obj,
                    source_endpoint_data=source_data,
                    target_endpoint_data=target_data,
                    vlan_id_str=vlan,
                    connection_idx=i
                )
                drawn_this_iteration = True

            if drawn_this_iteration:
                drawn_links_set.add(link_key)
                connection_count += 1

        logger.info(
            f"\n✓ Zakończono rysowanie połączeń. Narysowano (lub próbowano) {connection_count} unikalnych linii.")

    def _find_port_map(self, device_identifier: Any, conn: Dict, side: str, missing_devices_logged: Set) -> Optional[
        Dict[Any, PortEndpointData]]:
        port_map = self.port_mappings.get(device_identifier)
        if port_map is None and isinstance(device_identifier, str):
            port_map = self.port_mappings.get(device_identifier.lower())

        if port_map is None:
            if device_identifier not in missing_devices_logged:
                alt_ids_to_check = [conn.get(f"{side}_device_ip"), conn.get(f"{side}_device_hostname"),
                                    conn.get(f"{side}_device_purpose")]
                is_on_diagram = any(
                    alt_id in self.port_mappings or (isinstance(alt_id, str) and alt_id.lower() in self.port_mappings)
                    for alt_id in filter(None, alt_ids_to_check)
                )
                status_message = "JEST na diagramie pod inną nazwą/IP" if is_on_diagram else "BRAK go na diagramie"
                logger.info(
                    f"Urządzenie {side} '{device_identifier}' nie znalezione w aktywnych mapowaniach portów. Status: {status_message}.")
                missing_devices_logged.add(device_identifier)
            return None
        return port_map

    def _find_endpoint_data(self, port_map_for_device: Dict[Any, PortEndpointData], port_name: Optional[str],
                            ifindex: Optional[Any], device_name_str: str, side_info: str) -> Optional[PortEndpointData]:
        endpoint_data: Optional[PortEndpointData] = None
        keys_attempted: List[str] = []

        if ifindex is not None:
            key_ifidx_str = f"ifindex_{ifindex}"
            keys_attempted.append(key_ifidx_str)
            endpoint_data = port_map_for_device.get(key_ifidx_str)
            if endpoint_data: return endpoint_data

        if port_name:
            keys_attempted.append(f"name:'{port_name}'")
            endpoint_data = port_map_for_device.get(port_name)
            if endpoint_data: return endpoint_data

            if isinstance(port_name, str):
                port_name_lc = port_name.lower()
                if port_name_lc != port_name:  # Only if different
                    keys_attempted.append(f"name_lc:'{port_name_lc}'")
                    endpoint_data = port_map_for_device.get(port_name_lc)
                    if endpoint_data: return endpoint_data

        # Fallback na numer wizualny, jeśli port_name wygląda jak numer
        visual_number_key: Optional[str] = None
        if port_name and isinstance(port_name, str):
            if port_name.isdigit():
                visual_number_key = port_name
            else:  # Spróbuj wyciągnąć numer z końca nazwy portu, np. "Port 1" -> "1"
                num_match = re.search(r'(\d+)$', port_name)
                if num_match: visual_number_key = num_match.group(1)

        if visual_number_key:
            keys_attempted.append(f"vis_num:'{visual_number_key}'")
            endpoint_data = port_map_for_device.get(visual_number_key)
            if endpoint_data: return endpoint_data

        logger.debug(
            f"Nie znaleziono punktu końcowego dla portu '{port_name}' (ifIndex: {ifindex}) na urządzeniu '{device_name_str}' ({side_info}). Próbowano kluczy: {keys_attempted}")
        return None

    def _log_missing_port_data(self, conn_idx: int, conn_details: Dict, src_data: Optional[PortEndpointData],
                               tgt_data: Optional[PortEndpointData], logged_missing_ports: Set) -> None:
        local_port_id = f"{conn_details.get('local_device')}:{conn_details.get('local_port')}"
        remote_port_id = f"{conn_details.get('remote_device')}:{conn_details.get('remote_port')}"
        missing_parts_msgs = []
        if not src_data: missing_parts_msgs.append(
            f"źródła '{conn_details.get('local_port')}' na '{conn_details.get('local_device')}'")
        if not tgt_data: missing_parts_msgs.append(
            f"celu '{conn_details.get('remote_port')}' na '{conn_details.get('remote_device')}'")

        if missing_parts_msgs:
            log_this_time = (not src_data and local_port_id not in logged_missing_ports) or \
                            (not tgt_data and remote_port_id not in logged_missing_ports)
            if log_this_time:
                logger.info(
                    f"[Połączenie #{conn_idx}]: NIE zostanie narysowane (brak danych dla portu {' ORAZ '.join(missing_parts_msgs)}).")
                if not src_data: logged_missing_ports.add(local_port_id)
                if not tgt_data: logged_missing_ports.add(remote_port_id)

    def _calculate_waypoint(self, x: float, y: float, orientation: str, offset_val: float) -> Tuple[float, float]:
        wp_x_calc, wp_y_calc = x, y
        if orientation == "up":
            wp_y_calc -= offset_val
        elif orientation == "down":
            wp_y_calc += offset_val
        elif orientation == "left":
            wp_x_calc -= offset_val
        elif orientation == "right":
            wp_x_calc += offset_val
        return wp_x_calc, wp_y_calc

    def _save_diagrams(self) -> None:
        # Zapisz diagram Draw.io
        if self.drawio_xml_generator:
            file_io.save_diagram_xml(self.drawio_xml_generator.get_tree(), self.output_path_drawio)
        else:
            logger.warning("Brak generatora XML Draw.io do zapisania (być może błąd inicjalizacji).")

        # Zapisz diagram SVG
        if self.svg_diagram_obj:
            try:
                with open(self.output_path_svg, "w", encoding="utf-8") as f:
                    f.write(self.svg_diagram_obj.get_svg_string())
                logger.info(f"✓ Diagram SVG zapisany jako {self.output_path_svg}")
            except Exception as e:
                logger.error(f"⚠ Błąd zapisu diagramu SVG do pliku {self.output_path_svg}: {e}")
        else:
            logger.warning("Brak obiektu diagramu SVG do zapisania (być może błąd inicjalizacji).")