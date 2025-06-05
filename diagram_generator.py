# --- diagram_generator.py ---
import time
import logging
import xml.etree.ElementTree as ET
import re
from typing import Dict, List, Any, Optional, Tuple, Set
import pprint

from librenms_client import LibreNMSAPI
import file_io
import drawio_base
import drawio_layout
import drawio_utils
import drawio_device_builder
from drawio_device_builder import StyleInfo as DrawioStyleInfo
import svg_generator

import common_device_logic
from common_device_logic import PortEndpointData, DeviceDisplayData

from utils import find_device_in_list, get_canonical_identifier, normalize_interface_name

logger = logging.getLogger(__name__)


class DiagramGenerator:
    def __init__(self, api_client: LibreNMSAPI, config: Dict[str, Any],
                 ip_list_path: str, template_path: str,
                 output_path_drawio: str,
                 output_path_svg: str,
                 connections_json_path: str):
        self.api_client = api_client
        self.config = config
        self.ip_list_path = ip_list_path
        self.template_path = template_path
        self.output_path_drawio = output_path_drawio
        self.output_path_svg = output_path_svg
        self.connections_json_path = connections_json_path

        self.all_devices_from_api: List[Dict[str, Any]] = []
        self.target_devices_prepared_data: List[DeviceDisplayData] = []

        self.port_endpoint_mappings_drawio: Dict[str, Dict[Any, PortEndpointData]] = {}
        self.port_endpoint_mappings_svg: Dict[str, Dict[Any, PortEndpointData]] = {}

        self.external_cloud_endpoint_drawio: Optional[PortEndpointData] = None
        self.external_cloud_endpoint_svg: Optional[PortEndpointData] = None
        self.layout_positions: List[Tuple[float, float]] = []

        self.drawio_xml_generator: Optional[drawio_base.DrawioXMLGenerator] = drawio_base.DrawioXMLGenerator(
            page_width=str(self.config.get('grid_margin_x') * 2),
            page_height=str(self.config.get('grid_margin_y') * 2),
            grid_size=str(self.config.get('drawio_grid_size'))
        )
        self.global_drawio_diagram_root_cell: Optional[ET.Element] = None
        if self.drawio_xml_generator:
            self.global_drawio_diagram_root_cell = self.drawio_xml_generator.get_root_cell_element()

        try:
            self.device_styles_drawio_ref: DrawioStyleInfo = drawio_device_builder._extract_styles_from_template(
                self.template_path
            )
            logger.info(f"Pomyślnie wczytano/ustawiono style Draw.io z szablonu '{self.template_path}'.")
        except FileNotFoundError:
            logger.warning(f"Nie znaleziono pliku szablonu '{self.template_path}'. Używanie domyślnych stylów Draw.io.")
            self.device_styles_drawio_ref = DrawioStyleInfo()
        except Exception as e_style:
            logger.error(
                f"Nieoczekiwany błąd wczytywania stylów Draw.io z '{self.template_path}': {e_style}. Używanie domyślnych.",
                exc_info=True)
            self.device_styles_drawio_ref = DrawioStyleInfo()

        self.svg_diagram_obj: Optional[svg_generator.SVGDiagram] = svg_generator.SVGDiagram(
            width=self.config.get('grid_margin_x') * 2,
            height=self.config.get('grid_margin_y') * 2,
            config=self.config
        )
        logger.debug("DiagramGenerator zainicjalizowany.")

    def generate_diagram(self) -> None:
        logger.info(f"[Diagram 1/4] Wczytywanie listy urządzeń docelowych z {self.ip_list_path}...")
        target_ips_or_hosts = file_io.load_ip_list(self.ip_list_path)
        if not target_ips_or_hosts:
            logger.warning("Lista urządzeń docelowych jest pusta. Diagramy nie zostaną wygenerowane.")
            self._save_diagrams_if_needed(empty=True)
            return

        logger.info("[Diagram 2/4] Pobieranie pełnej listy wszystkich urządzeń z API LibreNMS...")
        self.all_devices_from_api = self.api_client.get_devices(
            columns="device_id,hostname,ip,sysName,purpose,os,hardware,version,serial,type,status"
        )
        if not self.all_devices_from_api:
            logger.error(
                "Nie udało się pobrać listy urządzeń z API lub lista jest pusta. Diagramy nie zostaną wygenerowane.")
            self._save_diagrams_if_needed(empty=True)
            return
        logger.info(f"Pobrano informacje o {len(self.all_devices_from_api)} urządzeniach z API.")

        logger.info("[Diagram 3/4] Identyfikacja urządzeń docelowych, przygotowanie danych i obliczanie layoutu...")
        max_diag_width, max_diag_height = self._prepare_targets_and_add_devices_to_diagrams(target_ips_or_hosts)

        final_diagram_width = max_diag_width + self.config.get('grid_margin_x') * 1.5
        final_diagram_height = max_diag_height + self.config.get('grid_margin_y') * 1.5

        if self.svg_diagram_obj:
            self.svg_diagram_obj.update_dimensions(final_diagram_width, final_diagram_height)
        if self.drawio_xml_generator:
            self.drawio_xml_generator.update_page_dimensions(final_diagram_width, final_diagram_height)

        if not self.target_devices_prepared_data:
            logger.warning("Brak urządzeń docelowych do umieszczenia na diagramach po filtrowaniu/przygotowaniu.")
            self._save_diagrams_if_needed()
            return

        self._log_port_mappings_summary()
        logger.info("[Diagram 4/4] Rysowanie połączeń między urządzeniami...")
        self._draw_all_connections()
        self._save_diagrams_if_needed()
        logger.info("✓ Generowanie diagramów zakończone.")

    def _log_port_mappings_summary(self):
        logger.debug(f"--- Podsumowanie mapowań portów DrawIO ({len(self.port_endpoint_mappings_drawio)} urządzeń) ---")
        for i, (dev_key, port_map) in enumerate(self.port_endpoint_mappings_drawio.items()):
            if i < 5 or logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"  DrawIO Mapowanie dla '{dev_key}': {len(port_map) if isinstance(port_map, dict) else 'Niepoprawny format'} portów. Przykładowe klucze: {list(port_map.keys())[:5] if port_map else 'Brak'}")
            elif i == 5 and not logger.isEnabledFor(logging.DEBUG):
                logger.debug("    ... (więcej mapowań DrawIO nie jest logowanych na poziomie INFO)")
                break
        logger.debug(f"--- Podsumowanie mapowań portów SVG ({len(self.port_endpoint_mappings_svg)} urządzeń) ---")
        for i, (dev_key, port_map) in enumerate(self.port_endpoint_mappings_svg.items()):
            if i < 5 or logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"  SVG Mapowanie dla '{dev_key}': {len(port_map) if isinstance(port_map, dict) else 'Niepoprawny format'} portów. Przykładowe klucze: {list(port_map.keys())[:5] if port_map else 'Brak'}")
            elif i == 5 and not logger.isEnabledFor(logging.DEBUG):
                logger.debug("    ... (więcej mapowań SVG nie jest logowanych na poziomie INFO)")
                break

    def _prepare_targets_and_add_devices_to_diagrams(self, target_ips_or_hosts: List[str]) -> Tuple[float, float]:
        logger.info("Krok 3a: Identyfikacja urządzeń docelowych i przygotowywanie danych...")
        target_set = set(str(ip_or_host).lower().strip() for ip_or_host in target_ips_or_hosts)
        self.target_devices_prepared_data = []
        device_render_idx_counter = 0
        for device_api_info_entry in self.all_devices_from_api:
            current_canonical_id = get_canonical_identifier(
                device_api_info_entry)

            ids_to_check_for_target_match = {str(val).lower().strip() for val in [
                device_api_info_entry.get('ip'),
                device_api_info_entry.get('hostname'),
                device_api_info_entry.get('sysName'),
                device_api_info_entry.get('purpose'),
                current_canonical_id
            ] if val}

            is_target_device = False
            for target_id_from_list in target_set:
                if target_id_from_list in ids_to_check_for_target_match:
                    is_target_device = True
                    logger.debug(
                        f"Urządzenie '{current_canonical_id or device_api_info_entry.get('hostname')}' (API ID: {device_api_info_entry.get('device_id')}) zidentyfikowane jako docelowe przez dopasowanie '{target_id_from_list}'.")
                    break

            if is_target_device:
                try:
                    prepared_data = common_device_logic.prepare_device_display_data(
                        device_api_info_entry, self.api_client, device_render_idx_counter, self.config
                    )
                    self.target_devices_prepared_data.append(prepared_data)
                    device_render_idx_counter += 1
                except Exception as e_prepare:
                    logger.error(
                        f"Błąd podczas przygotowywania danych dla urządzenia {current_canonical_id or device_api_info_entry.get('hostname')}: {e_prepare}",
                        exc_info=True)

        if not self.target_devices_prepared_data:
            logger.warning("Brak urządzeń docelowych po filtrowaniu i przygotowaniu danych.")
            return 0.0, 0.0
        logger.info(f"Znaleziono i przygotowano dane dla {len(self.target_devices_prepared_data)} urządzeń docelowych.")

        logger.info("Krok 3b: Określanie maksymalnych wymiarów elementów dla layoutu...")
        max_item_width, max_item_height = 0.0, 0.0
        for prep_data in self.target_devices_prepared_data:
            w, h = common_device_logic.get_device_render_size_from_prepared_data(prep_data)
            max_item_width, max_item_height = max(max_item_width, w), max(max_item_height, h)
        logger.info(f"Maksymalne wymiary elementu (chassis) dla layoutu: {max_item_width:.0f}x{max_item_height:.0f}")

        logger.info(
            f"Krok 3c: Obliczanie globalnego układu siatki dla {len(self.target_devices_prepared_data)} urządzeń...")

        info_label_min_w_cfg = self.config.get('info_label_min_width', 180.0)
        info_label_margin_cfg = self.config.get('info_label_margin_from_chassis', 30.0)
        effective_item_width_for_layout = max_item_width + info_label_min_w_cfg + info_label_margin_cfg

        self.layout_positions = drawio_layout.calculate_grid_layout(
            num_items=len(self.target_devices_prepared_data),
            item_width=effective_item_width_for_layout,
            item_height=max_item_height,
            config=self.config
        )
        if not self.layout_positions:
            logger.error("Nie udało się obliczyć pozycji layoutu dla urządzeń.")
            return 0.0, 0.0

        logger.info("Krok 3d: Dodawanie urządzeń do diagramów...")
        self.port_endpoint_mappings_drawio.clear()
        self.port_endpoint_mappings_svg.clear()
        actual_max_x_content, actual_max_y_content = 0.0, 0.0

        for i, prep_data_item in enumerate(self.target_devices_prepared_data):
            base_pos_x, base_pos_y = self.layout_positions[i]
            chassis_draw_pos_x = base_pos_x + info_label_min_w_cfg + info_label_margin_cfg
            final_position_for_device = (chassis_draw_pos_x, base_pos_y)
            item_canonical_id = prep_data_item.canonical_identifier
            logger.info(
                f"-- Dodawanie urządzenia {i + 1}/{len(self.target_devices_prepared_data)}: {item_canonical_id} na poz. ({final_position_for_device[0]:.0f}, {final_position_for_device[1]:.0f}) --")

            if self.global_drawio_diagram_root_cell is not None:
                logger.debug(f"  Rysowanie dla Draw.io: {item_canonical_id}")
                port_map_drawio = drawio_device_builder.add_device_to_diagram(
                    self.global_drawio_diagram_root_cell, prep_data_item,
                    self.api_client, final_position_for_device, i, self.device_styles_drawio_ref, self.config
                )
                if port_map_drawio:
                    self.port_endpoint_mappings_drawio[item_canonical_id.lower()] = port_map_drawio
                else:
                    logger.warning(f"  Draw.io: Nie uzyskano mapy portów dla '{item_canonical_id}'.")

            if self.svg_diagram_obj is not None:
                logger.debug(f"  Rysowanie dla SVG: {item_canonical_id}")
                port_map_svg = svg_generator.svg_add_device_to_diagram(
                    self.svg_diagram_obj, prep_data_item,
                    self.api_client, final_position_for_device, i, self.device_styles_drawio_ref, self.config
                )
                if port_map_svg:
                    self.port_endpoint_mappings_svg[item_canonical_id.lower()] = port_map_svg
                else:
                    logger.warning(f"  SVG: Nie uzyskano mapy portów dla '{item_canonical_id}'.")

            actual_max_x_content = max(actual_max_x_content, base_pos_x + effective_item_width_for_layout)
            actual_max_y_content = max(actual_max_y_content, base_pos_y + max_item_height)

        return actual_max_x_content, actual_max_y_content

    def _get_or_create_external_cloud_endpoint(self, diagram_type: str) -> Optional[PortEndpointData]:
        """Tworzy lub zwraca endpoint dla symbolicznej 'chmury' reprezentującej sieć zewnętrzną."""

        if diagram_type == "drawio":
            if self.external_cloud_endpoint_drawio:
                return self.external_cloud_endpoint_drawio
            if self.global_drawio_diagram_root_cell is None: return None

            cloud_x = self.config.get('grid_start_offset_x', 200) + 500
            cloud_y = self.config.get('grid_start_offset_y', 100) / 2
            cloud_w, cloud_h = 120, 80
            cloud_id = "external_cloud_drawio"

            style = "shape=cloud;fillColor=#F5F5F5;strokeColor=#666666;shadow=1;"
            cloud_cell = drawio_utils.create_vertex_cell(cloud_id, "1", "Sieć Zewnętrzna", cloud_x, cloud_y, cloud_w,
                                                         cloud_h, style)
            self.global_drawio_diagram_root_cell.append(cloud_cell)

            self.external_cloud_endpoint_drawio = PortEndpointData(
                cell_id=cloud_id, x=cloud_x + cloud_w / 2, y=cloud_y + cloud_h / 2, orientation="down"
            )
            logger.info(f"DrawIO: Utworzono symboliczną chmurę sieci zewnętrznej na ({cloud_x:.0f}, {cloud_y:.0f}).")
            return self.external_cloud_endpoint_drawio

        elif diagram_type == "svg":
            if self.external_cloud_endpoint_svg:
                return self.external_cloud_endpoint_svg
            if self.svg_diagram_obj is None: return None

            cloud_x = self.config.get('grid_start_offset_x', 200) + 500
            cloud_y = self.config.get('grid_start_offset_y', 100) / 2
            cloud_id = "external_cloud_svg"

            cloud_group = ET.Element("g", {"id": cloud_id, "transform": f"translate({cloud_x:.2f}, {cloud_y:.2f})"})
            cloud_rect = ET.Element("rect", {"x": "0", "y": "0", "width": "120", "height": "60", "rx": "30", "ry": "30",
                                             "fill": "#F5F5F5", "stroke": "#666666", "stroke-width": "1.5"})
            cloud_text = ET.Element("text", {"x": "60", "y": "35", "text-anchor": "middle", "font-size": "10px",
                                             "fill": "#333"})
            cloud_text.text = "Sieć Zewnętrzna"
            cloud_group.append(cloud_rect)
            cloud_group.append(cloud_text)
            self.svg_diagram_obj.add_element(cloud_group)

            self.external_cloud_endpoint_svg = PortEndpointData(
                cell_id=cloud_id, x=cloud_x + 60, y=cloud_y + 60, orientation="down"
            )
            logger.info(f"SVG: Utworzono symboliczną chmurę sieci zewnętrznej na ({cloud_x:.0f}, {cloud_y:.0f}).")
            return self.external_cloud_endpoint_svg

        return None

    def _find_port_map_for_connection(
            self, device_identifier_from_conn: Any,
            port_mappings_dict: Dict[str, Dict[Any, PortEndpointData]],
            side_for_log: str, missing_devices_log_tracker: Set[str]
    ) -> Optional[Dict[Any, PortEndpointData]]:
        if not device_identifier_from_conn:
            logger.debug(f"    _find_port_map ({side_for_log}): Pusty identyfikator urządzenia. Zwracam None.")
            return None

        dev_id_str_lower = str(device_identifier_from_conn).lower().strip()
        if not dev_id_str_lower:
            logger.warning(
                f"    _find_port_map ({side_for_log}): Pusty identyfikator urządzenia po strip/lower. Oryginalny: '{device_identifier_from_conn}'.")
            return None

        logger.debug(
            f"    _find_port_map ({side_for_log}): Szukam mapy dla '{dev_id_str_lower}'. Dostępne klucze mapy urządzeń: {list(port_mappings_dict.keys())[:10] if logger.isEnabledFor(logging.DEBUG) else '...'}")
        port_map_for_device = port_mappings_dict.get(dev_id_str_lower)

        if port_map_for_device is None:
            if dev_id_str_lower not in missing_devices_log_tracker:
                logger.warning(
                    f"    _find_port_map: Mapa portów dla urządzenia '{dev_id_str_lower}' ({side_for_log}) NIE ZNALEZIONA w dostępnych mapowaniach. To urządzenie prawdopodobnie nie było na liście ip_list.txt lub nie zostało poprawnie przetworzone.")
                missing_devices_log_tracker.add(dev_id_str_lower)
            return None

        logger.debug(
            f"      _find_port_map: Znaleziono mapę dla '{dev_id_str_lower}' ({side_for_log}) z {len(port_map_for_device)} portami.")
        return port_map_for_device

    def _find_endpoint_data_in_map(
            self, port_map_of_device: Dict[Any, PortEndpointData],
            port_name_from_conn: Optional[str], port_ifindex_from_conn: Optional[Any],
            device_name_for_log: str, side_for_log: str
    ) -> Optional[PortEndpointData]:
        endpoint_data: Optional[PortEndpointData] = None
        keys_attempted_log: List[str] = []

        if port_ifindex_from_conn is not None:
            try:
                ifindex_key_val = int(port_ifindex_from_conn)
                key_ifidx_str = f"ifindex_{ifindex_key_val}"
                keys_attempted_log.append(f"ifIndex:'{key_ifidx_str}'")
                endpoint_data = port_map_of_device.get(key_ifidx_str)
                if endpoint_data:
                    logger.debug(
                        f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_from_conn or key_ifidx_str}' przez '{key_ifidx_str}'.")
                    return endpoint_data
            except ValueError:
                logger.warning(
                    f"      _find_endpoint_data ({side_for_log}): Nie można przekonwertować port_ifindex_from_conn '{port_ifindex_from_conn}' na int dla urządzenia '{device_name_for_log}'.")

        port_name_conn_str_stripped = ""
        if port_name_from_conn:
            port_name_conn_str_stripped = str(port_name_from_conn).strip()
            if port_name_conn_str_stripped:
                port_name_conn_lower = port_name_conn_str_stripped.lower()
                keys_attempted_log.append(f"name_lower:'{port_name_conn_lower}'")
                endpoint_data = port_map_of_device.get(port_name_conn_lower)
                if endpoint_data:
                    logger.debug(
                        f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_conn_str_stripped}' przez bezpośrednie name_lower '{port_name_conn_lower}'.")
                    return endpoint_data

                interface_replacements = self.config.get('interface_name_replacements', {})
                normalized_port_name_attempt = normalize_interface_name(port_name_conn_str_stripped,
                                                                        interface_replacements)
                normalized_port_name_attempt_lower = normalized_port_name_attempt.lower()

                if normalized_port_name_attempt_lower != port_name_conn_lower:
                    keys_attempted_log.append(f"normalized_name_lower:'{normalized_port_name_attempt_lower}'")
                    endpoint_data = port_map_of_device.get(normalized_port_name_attempt_lower)
                    if endpoint_data:
                        logger.debug(
                            f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_conn_str_stripped}' przez normalized_name_lower '{normalized_port_name_attempt_lower}'.")
                        return endpoint_data
            else:
                logger.debug(
                    f"      _find_endpoint_data ({side_for_log}): port_name_from_conn dla '{device_name_for_log}' był pusty po strip.")

        if port_name_conn_str_stripped and port_name_conn_str_stripped.isdigit():
            visual_num_key = port_name_conn_str_stripped
            keys_attempted_log.append(f"visual_num:'{visual_num_key}'")
            endpoint_data = port_map_of_device.get(visual_num_key)
            if endpoint_data:
                logger.debug(
                    f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_from_conn}' przez visual_num_key '{visual_num_key}'.")
                return endpoint_data

        if port_name_conn_str_stripped and port_name_conn_str_stripped.lower() == "mgmt0":
            keys_attempted_log.append(f"name_exact_mgmt0:'mgmt0'")
            endpoint_data = port_map_of_device.get("mgmt0")
            if endpoint_data:
                logger.debug(
                    f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_from_conn}' przez specjalny klucz 'mgmt0'.")
                return endpoint_data

        available_keys_sample = list(port_map_of_device.keys())[:10]
        logger.warning(
            f"      _find_endpoint_data ({side_for_log}): NIE znaleziono punktu dla portu '{port_name_from_conn}' (ifIndex: {port_ifindex_from_conn}) na '{device_name_for_log}'. Próbowano kluczy: {keys_attempted_log}. Dostępne klucze w mapie portów (próbka max 10): {available_keys_sample}")
        return None

    def _log_missing_port_data(self, conn_idx: int, conn_details: Dict[str, Any],
                               src_data: Optional[PortEndpointData], tgt_data: Optional[PortEndpointData],
                               logged_missing_ports_tracker: Set[str], diagram_type: str) -> None:
        local_port_id_log = f"{conn_details.get('local_device')}:{conn_details.get('local_port') or conn_details.get('local_ifindex')}"
        remote_port_id_log = f"{conn_details.get('remote_device')}:{conn_details.get('remote_port') or conn_details.get('remote_ifindex')}"
        missing_parts_msgs = []
        if not src_data: missing_parts_msgs.append(
            f"źródła '{conn_details.get('local_port')}' (ifIndex: {conn_details.get('local_ifindex')}) na '{conn_details.get('local_device')}'")
        if not tgt_data: missing_parts_msgs.append(
            f"celu '{conn_details.get('remote_port')}' (ifIndex: {conn_details.get('remote_ifindex')}) na '{conn_details.get('remote_device')}'")

        if missing_parts_msgs:
            log_key_src = f"{diagram_type}_{local_port_id_log}"
            log_key_tgt = f"{diagram_type}_{remote_port_id_log}"
            should_log_warning = False
            if not src_data and log_key_src not in logged_missing_ports_tracker:
                logged_missing_ports_tracker.add(log_key_src)
                should_log_warning = True
            if not tgt_data and log_key_tgt not in logged_missing_ports_tracker:
                logged_missing_ports_tracker.add(log_key_tgt)
                should_log_warning = True

            msg_detail = f"({diagram_type}) [Połączenie #{conn_idx} z {conn_details.get('discovery_method', '?')}]: NIE zostanie narysowane - problem z mapowaniem dla portu {' ORAZ '.join(missing_parts_msgs)}."
            if should_log_warning:
                logger.warning(msg_detail)
            else:
                logger.debug(
                    msg_detail + " (Ostrzeżenie już zalogowano dla tego portu/urządzenia lub brak danych źródłowych/docelowych)")

    def _calculate_waypoint(self, x: float, y: float, orientation: str, offset_val: float) -> Tuple[float, float]:
        wp_x_calc, wp_y_calc = x, y
        actual_offset = offset_val
        if orientation == "up":
            wp_y_calc -= actual_offset
        elif orientation == "down":
            wp_y_calc += actual_offset
        elif orientation == "left":
            wp_x_calc -= actual_offset
        elif orientation == "right":
            wp_x_calc += actual_offset
        return wp_x_calc, wp_y_calc

    def _calculate_connection_path(self,
                                   src_ep: PortEndpointData,
                                   tgt_ep: PortEndpointData,
                                   all_device_positions: Dict[str, Tuple[float, float, float, float]],
                                   config: Dict[str, Any]
                                   ) -> List[Tuple[float, float]]:
        waypoint_offset = config.get('waypoint_offset', 20.0)

        wp_sx, wp_sy = self._calculate_waypoint(src_ep.x, src_ep.y, src_ep.orientation, waypoint_offset)
        wp_tx, wp_ty = self._calculate_waypoint(tgt_ep.x, tgt_ep.y, tgt_ep.orientation, waypoint_offset)

        path_points = [(src_ep.x, src_ep.y), (wp_sx, wp_sy)]

        if src_ep.orientation in ['up', 'down'] and tgt_ep.orientation in ['up', 'down'] and abs(wp_sx - wp_tx) > 10:
            mid_y = (wp_sy + wp_ty) / 2
            path_points.append((wp_sx, mid_y))
            path_points.append((wp_tx, mid_y))
        elif src_ep.orientation in ['left', 'right'] and tgt_ep.orientation in ['left', 'right'] and abs(
                wp_sy - wp_ty) > 10:
            mid_x = (wp_sx + wp_tx) / 2
            path_points.append((mid_x, wp_sy))
            path_points.append((mid_x, wp_ty))

        path_points.extend([(wp_tx, wp_ty), (tgt_ep.x, tgt_ep.y)])
        return path_points

    def _draw_all_connections(self) -> None:
        connections_data = file_io.load_connections_json(self.connections_json_path)
        if not connections_data:
            logger.warning(f"Brak danych o połączeniach w {self.connections_json_path}. Linie nie zostaną narysowane.")
            return
        logger.info(f"Próba narysowania {len(connections_data)} połączeń z pliku JSON...")

        drawn_links_set_drawio: Set[frozenset[str]] = set()
        drawn_links_set_svg: Set[frozenset[str]] = set()

        missing_devices_logged_drawio: Set[str] = set()
        missing_ports_logged_drawio: Set[str] = set()
        missing_devices_logged_svg: Set[str] = set()
        missing_ports_logged_svg: Set[str] = set()

        connection_drawn_count_drawio = 0
        connection_drawn_count_svg = 0

        device_bounding_boxes: Dict[str, Tuple[float, float, float, float]] = {}
        if self.svg_diagram_obj is not None:
            layout_positions_map = {prep_data.canonical_identifier.lower(): pos for prep_data, pos in
                                    zip(self.target_devices_prepared_data, self.layout_positions)}
            for prep_data in self.target_devices_prepared_data:
                item_id = prep_data.canonical_identifier.lower()
                pos = layout_positions_map.get(item_id)
                if pos:
                    w, h = prep_data.chassis_layout.width, prep_data.chassis_layout.height
                    device_bounding_boxes[item_id] = (pos[0], pos[1], w, h)

        for i, conn_details in enumerate(connections_data):
            logger.debug(f"\n--- DiagramGen: Przetwarzanie połączenia #{i + 1}/{len(connections_data)} ---")
            logger.debug(f"  Surowe dane połączenia z JSON: {pprint.pformat(conn_details)}")

            local_dev_id, lp_name, lp_ifidx = conn_details.get("local_device"), conn_details.get(
                "local_port"), conn_details.get("local_ifindex")
            remote_dev_id, rp_name, rp_ifidx = conn_details.get("remote_device"), conn_details.get(
                "remote_port"), conn_details.get("remote_ifindex")
            vlan_val = conn_details.get("vlan")
            remote_original_id_raw = conn_details.get('remote_device')  # Potrzebne dla etykiet do chmury

            if not local_dev_id or (lp_name is None and lp_ifidx is None):
                continue

            local_map_d = self._find_port_map_for_connection(local_dev_id, self.port_endpoint_mappings_drawio,
                                                             "L(DrawIO)", missing_devices_logged_drawio)
            remote_map_d = self._find_port_map_for_connection(remote_dev_id, self.port_endpoint_mappings_drawio,
                                                              "R(DrawIO)",
                                                              missing_devices_logged_drawio) if remote_dev_id else None

            if self.global_drawio_diagram_root_cell is not None and local_map_d:
                src_ep_d = self._find_endpoint_data_in_map(local_map_d, lp_name, lp_ifidx, str(local_dev_id),
                                                           "Src(DrawIO)")
                tgt_ep_d = self._find_endpoint_data_in_map(remote_map_d, rp_name, rp_ifidx, str(remote_dev_id),
                                                           "Tgt(DrawIO)") if remote_map_d else None

                if src_ep_d and tgt_ep_d:
                    link_key_d = frozenset(sorted((str(src_ep_d.cell_id), str(tgt_ep_d.cell_id))))
                    if link_key_d not in drawn_links_set_drawio:
                        edge_id = f"edge_d_{i + 1}_{src_ep_d.cell_id}_{tgt_ep_d.cell_id}"
                        edge_style = "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeWidth=1.5;endArrow=none;strokeColor=#FF9900;fontSize=8;"
                        edge_label = f"VLAN {vlan_val}" if vlan_val is not None else ""
                        edge_cell = drawio_utils.create_edge_cell(edge_id, "1", src_ep_d.cell_id, tgt_ep_d.cell_id,
                                                                  edge_style, edge_label)
                        if edge_label:
                            drawio_utils.apply_style_change(edge_cell, "labelBackgroundColor", "#FFFFFF")
                            drawio_utils.apply_style_change(edge_cell, "fontColor", "#000000")
                        self.global_drawio_diagram_root_cell.append(edge_cell)
                        drawn_links_set_drawio.add(link_key_d)
                        connection_drawn_count_drawio += 1
                        logger.debug(f"    DrawIO: Narysowano {link_key_d} (auto-routing).")
                elif src_ep_d and not remote_map_d and remote_original_id_raw:
                    cloud_ep = self._get_or_create_external_cloud_endpoint("drawio")
                    if cloud_ep:
                        edge_id = f"edge_d_cloud_{i + 1}_{src_ep_d.cell_id}"
                        edge_style = "edgeStyle=orthogonalEdgeStyle;rounded=1;strokeWidth=1.5;endArrow=classic;strokeColor=#4B9ACC;"
                        edge_cell = drawio_utils.create_edge_cell(edge_id, "1", src_ep_d.cell_id, cloud_ep.cell_id,
                                                                  edge_style, remote_original_id_raw)
                        self.global_drawio_diagram_root_cell.append(edge_cell)
                        logger.debug(
                            f"    DrawIO: Narysowano połączenie z {local_dev_id} do chmury dla '{remote_original_id_raw}'.")
                else:
                    if src_ep_d is None or (remote_map_d and tgt_ep_d is None):
                        self._log_missing_port_data(i + 1, conn_details, src_ep_d, tgt_ep_d,
                                                    missing_ports_logged_drawio, "DrawIO")

            local_map_s = self._find_port_map_for_connection(local_dev_id, self.port_endpoint_mappings_svg, "L(SVG)",
                                                             missing_devices_logged_svg)
            remote_map_s = self._find_port_map_for_connection(remote_dev_id, self.port_endpoint_mappings_svg, "R(SVG)",
                                                              missing_devices_logged_svg) if remote_dev_id else None

            if self.svg_diagram_obj is not None and local_map_s:
                src_ep_s = self._find_endpoint_data_in_map(local_map_s, lp_name, lp_ifidx, str(local_dev_id),
                                                           "Src(SVG)")
                tgt_ep_s = self._find_endpoint_data_in_map(remote_map_s, rp_name, rp_ifidx, str(remote_dev_id),
                                                           "Tgt(SVG)") if remote_map_s else None

                if src_ep_s and tgt_ep_s:
                    link_key_s = frozenset(sorted((str(src_ep_s.cell_id), str(tgt_ep_s.cell_id))))
                    if link_key_s not in drawn_links_set_svg:
                        svg_path_points = self._calculate_connection_path(src_ep_s, tgt_ep_s, device_bounding_boxes,
                                                                          self.config)
                        svg_generator.svg_draw_connection(self.svg_diagram_obj, svg_path_points,
                                                          str(vlan_val) if vlan_val is not None else None, i + 1,
                                                          self.config)
                        drawn_links_set_svg.add(link_key_s)
                        connection_drawn_count_svg += 1
                        logger.debug(f"    SVG: Narysowano {link_key_s} (smart path).")
                elif src_ep_s and not remote_map_s and remote_original_id_raw:
                    cloud_ep_svg = self._get_or_create_external_cloud_endpoint("svg")
                    if cloud_ep_svg:
                        svg_path_points = self._calculate_connection_path(src_ep_s, cloud_ep_svg, device_bounding_boxes,
                                                                          self.config)
                        svg_generator.svg_draw_connection(self.svg_diagram_obj, svg_path_points, remote_original_id_raw,
                                                          f"cloud_{i + 1}", self.config)
                        logger.debug(
                            f"    SVG: Narysowano połączenie z {local_dev_id} do chmury dla '{remote_original_id_raw}'.")
                else:
                    if src_ep_s is None or (remote_map_s and tgt_ep_s is None):
                        self._log_missing_port_data(i + 1, conn_details, src_ep_s, tgt_ep_s, missing_ports_logged_svg,
                                                    "SVG")

        logger.info(f"--- Podsumowanie _draw_all_connections ---")
        logger.info(f"  DrawIO: Narysowano unikalnych linii: {connection_drawn_count_drawio}")
        logger.info(f"  SVG: Narysowano unikalnych linii: {connection_drawn_count_svg}")

    def _save_diagrams_if_needed(self, empty: bool = False) -> None:
        if self.drawio_xml_generator and self.global_drawio_diagram_root_cell is not None:
            if empty and hasattr(self.drawio_xml_generator, 'update_page_dimensions'):
                self.drawio_xml_generator.update_page_dimensions(
                    self.config.get('min_chassis_width', 100.0),
                    self.config.get('min_chassis_height', 60.0)
                )
            file_io.save_diagram_xml(self.drawio_xml_generator.get_tree(), self.output_path_drawio)
        else:
            logger.warning("Brak generatora XML Draw.io lub korzenia. Plik Draw.io nie zostanie zapisany.")

        if self.svg_diagram_obj:
            if empty and hasattr(self.svg_diagram_obj, 'update_dimensions'):
                self.svg_diagram_obj.update_dimensions(
                    self.config.get('min_chassis_width', 100.0),
                    self.config.get('min_chassis_height', 60.0)
                )
            try:
                with open(self.output_path_svg, "w", encoding="utf-8") as f:
                    f.write(self.svg_diagram_obj.get_svg_string())
                logger.info(f"✓ Diagram SVG {'(pusty)' if empty else ''} zapisany jako {self.output_path_svg}")
            except Exception as e:
                logger.error(f"⚠ Błąd zapisu diagramu SVG {self.output_path_svg}: {e}", exc_info=True)
        else:
            logger.warning("Brak obiektu diagramu SVG. Plik SVG nie zostanie zapisany.")