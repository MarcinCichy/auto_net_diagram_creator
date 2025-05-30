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

from utils import find_device_in_list, get_canonical_identifier

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

        self.drawio_xml_generator: Optional[drawio_base.DrawioXMLGenerator] = drawio_base.DrawioXMLGenerator(
            page_width=str(self.config.get('grid_margin_x') * 2),
            page_height=str(self.config.get('grid_margin_y') * 2),
            grid_size=str(self.config.get('drawio_grid_size'))
        )
        self.global_drawio_diagram_root_cell: Optional[ET.Element] = None
        if self.drawio_xml_generator:
            self.global_drawio_diagram_root_cell = self.drawio_xml_generator.get_root_cell_element()

        try:
            # Usunięto self.config z wywołania
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
            if i < 3:
                logger.debug(
                    f"  DrawIO Mapowanie dla '{dev_key}': {len(port_map) if isinstance(port_map, dict) else 'Niepoprawny format'} portów.")
            elif i == 3:
                logger.debug("    ... (więcej mapowań DrawIO nie jest logowanych)")
        logger.debug(f"--- Podsumowanie mapowań portów SVG ({len(self.port_endpoint_mappings_svg)} urządzeń) ---")
        for i, (dev_key, port_map) in enumerate(self.port_endpoint_mappings_svg.items()):
            if i < 3:
                logger.debug(
                    f"  SVG Mapowanie dla '{dev_key}': {len(port_map) if isinstance(port_map, dict) else 'Niepoprawny format'} portów.")
            elif i == 3:
                logger.debug("    ... (więcej mapowań SVG nie jest logowanych)")


    def _prepare_targets_and_add_devices_to_diagrams(self, target_ips_or_hosts: List[str]) -> Tuple[float, float]:
        logger.info("Krok 3a: Identyfikacja urządzeń docelowych i przygotowywanie danych...")
        target_set = set(str(ip_or_host).lower().strip() for ip_or_host in target_ips_or_hosts)
        self.target_devices_prepared_data = []
        device_render_idx_counter = 0
        for device_api_info_entry in self.all_devices_from_api:
            current_canonical_id = get_canonical_identifier(device_api_info_entry)
            ids_to_check = {str(val).lower().strip() for val in [
                device_api_info_entry.get('ip'), device_api_info_entry.get('hostname'),
                device_api_info_entry.get('sysName'), device_api_info_entry.get('purpose'),
                current_canonical_id] if val}
            if any(target_id in ids_to_check for target_id in target_set):
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

        info_label_min_w_cfg = self.config.get('info_label_min_width')
        info_label_margin_cfg = self.config.get('info_label_margin_from_chassis')
        effective_item_width_for_layout = max_item_width + info_label_min_w_cfg + info_label_margin_cfg

        layout_positions = drawio_layout.calculate_grid_layout(
            num_items=len(self.target_devices_prepared_data),
            item_width=effective_item_width_for_layout,
            item_height=max_item_height,
            config=self.config
        )
        if not layout_positions:
            logger.error("Nie udało się obliczyć pozycji layoutu dla urządzeń.")
            return 0.0, 0.0

        logger.info("Krok 3d: Dodawanie urządzeń do diagramów...")
        self.port_endpoint_mappings_drawio.clear()
        self.port_endpoint_mappings_svg.clear()
        actual_max_x_content, actual_max_y_content = 0.0, 0.0

        for i, prep_data_item in enumerate(self.target_devices_prepared_data):
            base_pos_x, base_pos_y = layout_positions[i]
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


    def _find_port_map_for_connection(
            self, device_identifier_from_conn: Any,
            port_mappings_dict: Dict[str, Dict[Any, PortEndpointData]],
            side_for_log: str, missing_devices_log_tracker: Set[str]
    ) -> Optional[Dict[Any, PortEndpointData]]:
        if not device_identifier_from_conn: return None
        dev_id_str_lower = str(device_identifier_from_conn).lower().strip()
        if not dev_id_str_lower:
            logger.warning(f"    _find_port_map: Pusty identyfikator urządzenia ({side_for_log}).")
            return None
        logger.debug(f"    _find_port_map ({side_for_log}): Szukam mapy dla '{dev_id_str_lower}'.")
        port_map_for_device = port_mappings_dict.get(dev_id_str_lower)
        if port_map_for_device is None:
            if dev_id_str_lower not in missing_devices_log_tracker:
                logger.warning(
                    f"    _find_port_map: Mapa portów dla '{dev_id_str_lower}' ({side_for_log}) NIE ZNALEZIONA.")
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
            key_ifidx_str = f"ifindex_{port_ifindex_from_conn}"
            keys_attempted_log.append(f"ifIndex:'{key_ifidx_str}'")
            endpoint_data = port_map_of_device.get(key_ifidx_str)
            if endpoint_data:
                logger.debug(f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_from_conn or key_ifidx_str}' przez '{key_ifidx_str}'.")
                return endpoint_data

        if port_name_from_conn:
            port_name_conn_str = str(port_name_from_conn).strip()
            port_name_conn_lower = port_name_conn_str.lower()
            keys_attempted_log.append(f"name_lower:'{port_name_conn_lower}'")
            endpoint_data = port_map_of_device.get(port_name_conn_lower)
            if endpoint_data:
                logger.debug(f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_conn_str}' przez '{port_name_conn_lower}'.")
                return endpoint_data

            interface_replacements = self.config.get('interface_name_replacements')
            normalized_port_name_attempt = port_name_conn_str
            temp_if_name = port_name_conn_str
            for long, short in sorted(interface_replacements.items(), key=lambda item: len(item[0]), reverse=True):
                if temp_if_name.lower().startswith(long.lower()):
                    normalized_port_name_attempt = short + temp_if_name[len(long):]
                    break
            normalized_port_name_attempt_lower = normalized_port_name_attempt.lower()

            if normalized_port_name_attempt_lower != port_name_conn_lower:
                keys_attempted_log.append(f"normalized_name_lower:'{normalized_port_name_attempt_lower}'")
                endpoint_data = port_map_of_device.get(normalized_port_name_attempt_lower)
                if endpoint_data:
                    logger.debug(f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_conn_str}' przez normalizację do '{normalized_port_name_attempt_lower}'.")
                    return endpoint_data

        if port_name_from_conn and str(port_name_from_conn).strip().isdigit():
            visual_num_key = str(port_name_from_conn).strip()
            keys_attempted_log.append(f"visual_num:'{visual_num_key}'")
            endpoint_data = port_map_of_device.get(visual_num_key)
            if endpoint_data:
                logger.debug(f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_from_conn}' przez numer wizualny '{visual_num_key}'.")
                return endpoint_data

        if port_name_from_conn and str(port_name_from_conn).lower() == "mgmt0":
             keys_attempted_log.append(f"name_exact_mgmt0:'mgmt0'")
             endpoint_data = port_map_of_device.get("mgmt0")
             if endpoint_data:
                logger.debug(f"      _find_endpoint_data ({side_for_log}): Znaleziono dla '{device_name_for_log}':'{port_name_from_conn}' przez specjalny klucz 'mgmt0'.")
                return endpoint_data

        logger.warning(
            f"      _find_endpoint_data ({side_for_log}): NIE znaleziono punktu dla portu '{port_name_from_conn}' (ifIndex: {port_ifindex_from_conn}) na '{device_name_for_log}'. Próbowano kluczy: {keys_attempted_log}. Dostępne (max 5): {list(port_map_of_device.keys())[:5]}")
        return None

    def _log_missing_port_data(self, conn_idx: int, conn_details: Dict[str, Any],
                               src_data: Optional[PortEndpointData], tgt_data: Optional[PortEndpointData],
                               logged_missing_ports_tracker: Set[str], diagram_type: str) -> None:
        local_port_id_log = f"{conn_details.get('local_device')}:{conn_details.get('local_port')}"
        remote_port_id_log = f"{conn_details.get('remote_device')}:{conn_details.get('remote_port')}"
        missing_parts_msgs = []
        if not src_data: missing_parts_msgs.append(
            f"źródła '{conn_details.get('local_port')}' (ifIndex: {conn_details.get('local_ifindex')}) na '{conn_details.get('local_device')}'")
        if not tgt_data: missing_parts_msgs.append(
            f"celu '{conn_details.get('remote_port')}' (ifIndex: {conn_details.get('remote_ifindex')}) na '{conn_details.get('remote_device')}'")
        if missing_parts_msgs:
            log_key_src = f"{diagram_type}_{local_port_id_log}";
            log_key_tgt = f"{diagram_type}_{remote_port_id_log}";
            should_log_warning = False
            if not src_data and log_key_src not in logged_missing_ports_tracker: logged_missing_ports_tracker.add(
                log_key_src); should_log_warning = True
            if not tgt_data and log_key_tgt not in logged_missing_ports_tracker: logged_missing_ports_tracker.add(
                log_key_tgt); should_log_warning = True
            msg_detail = f"({diagram_type}) [Połączenie #{conn_idx}]: NIE zostanie narysowane - problem z mapowaniem dla portu {' ORAZ '.join(missing_parts_msgs)}."
            if should_log_warning:
                logger.warning(msg_detail)
            else:
                logger.debug(msg_detail + " (Ostrzeżenie już zalogowano)")


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
        connections_processed_count = 0
        connections_skipped_incomplete = 0
        connections_skipped_no_local_map = 0
        connections_skipped_no_remote_map = 0
        connections_skipped_no_source_ep = 0
        connections_skipped_no_target_ep = 0
        connections_skipped_already_drawn_drawio = 0
        connections_skipped_already_drawn_svg = 0

        waypoint_offset_cfg = self.config.get('waypoint_offset')

        for i, conn_details in enumerate(connections_data):
            connections_processed_count += 1
            logger.debug(f"\n--- DiagramGen: Przetwarzanie połączenia #{i + 1}/{len(connections_data)} ---")
            logger.debug(f"  Surowe dane połączenia: {pprint.pformat(conn_details)}")
            local_dev_id, lp_name, lp_ifidx = conn_details.get("local_device"), conn_details.get(
                "local_port"), conn_details.get("local_ifindex")
            remote_dev_id, rp_name, rp_ifidx = conn_details.get("remote_device"), conn_details.get(
                "remote_port"), conn_details.get("remote_ifindex")
            vlan_val = conn_details.get("vlan")

            if not all([local_dev_id, lp_name or lp_ifidx is not None, remote_dev_id, rp_name or rp_ifidx is not None]):
                connections_skipped_incomplete += 1
                logger.debug(f"  SKIP Połączenie #{i + 1}: Brakujące kluczowe pola (urządzenie lub identyfikator portu).")
                continue

            if self.global_drawio_diagram_root_cell is not None:
                local_map_d = self._find_port_map_for_connection(local_dev_id, self.port_endpoint_mappings_drawio,
                                                                 "L(DrawIO)", missing_devices_logged_drawio)
                remote_map_d = self._find_port_map_for_connection(remote_dev_id, self.port_endpoint_mappings_drawio,
                                                                  "R(DrawIO)", missing_devices_logged_drawio)
                if not local_map_d: connections_skipped_no_local_map += 1
                if not remote_map_d: connections_skipped_no_remote_map += 1

                if local_map_d and remote_map_d:
                    src_ep_d = self._find_endpoint_data_in_map(local_map_d, lp_name, lp_ifidx, str(local_dev_id), "Src(DrawIO)")
                    tgt_ep_d = self._find_endpoint_data_in_map(remote_map_d, rp_name, rp_ifidx, str(remote_dev_id), "Tgt(DrawIO)")

                    if not src_ep_d: connections_skipped_no_source_ep += 1
                    if not tgt_ep_d: connections_skipped_no_target_ep += 1

                    if src_ep_d and tgt_ep_d:
                        link_key_d = frozenset(sorted((str(src_ep_d.cell_id), str(tgt_ep_d.cell_id))))
                        if link_key_d not in drawn_links_set_drawio:
                            wp_s_x, wp_s_y = self._calculate_waypoint(src_ep_d.x, src_ep_d.y, src_ep_d.orientation, waypoint_offset_cfg)
                            wp_t_x, wp_t_y = self._calculate_waypoint(tgt_ep_d.x, tgt_ep_d.y, tgt_ep_d.orientation, waypoint_offset_cfg)
                            edge_id = f"edge_d_{i + 1}_{src_ep_d.cell_id}_{tgt_ep_d.cell_id}"
                            edge_style = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeWidth=1.5;endArrow=none;strokeColor=#FF9900;fontSize=8;"
                            edge_label = f"VLAN {vlan_val}" if vlan_val is not None else ""
                            edge_cell = drawio_utils.create_edge_cell(edge_id, "1", src_ep_d.cell_id, tgt_ep_d.cell_id, edge_style, edge_label)
                            geom = edge_cell.find("./mxGeometry")
                            if geom is not None:
                                pts_arr = ET.SubElement(geom, "Array", {"as": "points"})
                                ET.SubElement(pts_arr, "mxPoint", {"x": str(round(wp_s_x)) , "y": str(round(wp_s_y))})
                                ET.SubElement(pts_arr, "mxPoint", {"x": str(round(wp_t_x)) , "y": str(round(wp_t_y))})
                            if edge_label:
                                drawio_utils.apply_style_change(edge_cell, "labelBackgroundColor", "#FFFFFF")
                                drawio_utils.apply_style_change(edge_cell, "fontColor", "#000000")
                            self.global_drawio_diagram_root_cell.append(edge_cell)
                            drawn_links_set_drawio.add(link_key_d)
                            connection_drawn_count_drawio += 1
                            logger.debug(f"    DrawIO: Narysowano {link_key_d}.")
                        else:
                            connections_skipped_already_drawn_drawio += 1
                            logger.debug(f"    DrawIO: Link {link_key_d} już narysowany.")
                    else:
                        self._log_missing_port_data(i + 1, conn_details, src_ep_d, tgt_ep_d, missing_ports_logged_drawio, "DrawIO")

            if self.svg_diagram_obj is not None:
                local_map_s = self._find_port_map_for_connection(local_dev_id, self.port_endpoint_mappings_svg, "L(SVG)", missing_devices_logged_svg)
                remote_map_s = self._find_port_map_for_connection(remote_dev_id, self.port_endpoint_mappings_svg, "R(SVG)", missing_devices_logged_svg)

                if local_map_s and remote_map_s:
                    src_ep_s = self._find_endpoint_data_in_map(local_map_s, lp_name, lp_ifidx, str(local_dev_id), "Src(SVG)")
                    tgt_ep_s = self._find_endpoint_data_in_map(remote_map_s, rp_name, rp_ifidx, str(remote_dev_id), "Tgt(SVG)")

                    if src_ep_s and tgt_ep_s:
                        link_key_s = frozenset(sorted((str(src_ep_s.cell_id), str(tgt_ep_s.cell_id))))
                        if link_key_s not in drawn_links_set_svg:
                            svg_generator.svg_draw_connection(self.svg_diagram_obj, src_ep_s, tgt_ep_s,
                                                              str(vlan_val) if vlan_val is not None else None, i + 1,
                                                              waypoint_offset_cfg, self.config)
                            drawn_links_set_svg.add(link_key_s)
                            connection_drawn_count_svg +=1
                            logger.debug(f"    SVG: Narysowano {link_key_s}.")
                        else:
                            connections_skipped_already_drawn_svg +=1
                            logger.debug(f"    SVG: Link {link_key_s} już narysowany.")
                    else:
                        self._log_missing_port_data(i + 1, conn_details, src_ep_s, tgt_ep_s, missing_ports_logged_svg, "SVG")

        logger.info(f"--- Podsumowanie _draw_all_connections ---")
        logger.info(f"  Przetworzono połączeń: {connections_processed_count} / {len(connections_data)}")
        logger.info(
            f"  Pominięto: niekompletne: {connections_skipped_incomplete}, brak mapy lokalnej: {connections_skipped_no_local_map}, "
            f"brak mapy zdalnej: {connections_skipped_no_remote_map}, brak źródłowego EP: {connections_skipped_no_source_ep}, "
            f"brak docelowego EP: {connections_skipped_no_target_ep}")
        logger.info(f"  DrawIO: Narysowano unikalnych linii: {connection_drawn_count_drawio}, pominięto już narysowane: {connections_skipped_already_drawn_drawio}")
        logger.info(f"  SVG: Narysowano unikalnych linii: {connection_drawn_count_svg}, pominięto już narysowane: {connections_skipped_already_drawn_svg}")


    def _save_diagrams_if_needed(self, empty: bool = False) -> None:
        if self.drawio_xml_generator and self.global_drawio_diagram_root_cell is not None:
            if empty and hasattr(self.drawio_xml_generator, 'update_page_dimensions'):
                self.drawio_xml_generator.update_page_dimensions(
                    self.config.get('min_chassis_width'),
                    self.config.get('min_chassis_height')
                )
            file_io.save_diagram_xml(self.drawio_xml_generator.get_tree(), self.output_path_drawio)
        else:
            logger.warning("Brak generatora XML Draw.io lub korzenia. Plik Draw.io nie zostanie zapisany.")

        if self.svg_diagram_obj:
            if empty and hasattr(self.svg_diagram_obj, 'update_dimensions'):
                self.svg_diagram_obj.update_dimensions(
                    self.config.get('min_chassis_width'),
                    self.config.get('min_chassis_height')
                )
            try:
                with open(self.output_path_svg, "w", encoding="utf-8") as f:
                    f.write(self.svg_diagram_obj.get_svg_string())
                logger.info(f"✓ Diagram SVG {'(pusty)' if empty else ''} zapisany jako {self.output_path_svg}")
            except Exception as e:
                logger.error(f"⚠ Błąd zapisu diagramu SVG {self.output_path_svg}: {e}", exc_info=True)
        else:
            logger.warning("Brak obiektu diagramu SVG. Plik SVG nie zostanie zapisany.")