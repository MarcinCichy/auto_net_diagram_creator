# --- drawio_device_builder.py ---
import logging
import math
import re  # Potrzebny do logiki etykiet
import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple, Optional, Any, NamedTuple

from librenms_client import LibreNMSAPI
import drawio_utils
import drawio_layout

import common_device_logic
from common_device_logic import PortEndpointData, DeviceDisplayData, DynamicLayoutInfo
from common_device_logic import (
    PORT_WIDTH, PORT_HEIGHT, HORIZONTAL_SPACING, ROW_OFFSET_Y, CHASSIS_PADDING_Y, VERTICAL_SPACING,
    WAYPOINT_OFFSET, LABEL_LINE_HEIGHT, LABEL_PADDING,
    MIN_CHASSIS_WIDTH, MIN_CHASSIS_HEIGHT  # Importuj, jeśli calculate_dynamic_device_size tego używa jako fallback
)

logger = logging.getLogger(__name__)

# Stałe specyficzne dla Draw.io dla rysowania aliasów i etykiet informacyjnych
PORT_ALIAS_LINE_EXTENSION = 30.0
PORT_ALIAS_LABEL_OFFSET_FROM_LINE = 2.0
PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER = 5.0  # Poziome przesunięcie dla czytelności obróconej etykiety
INFO_LABEL_MARGIN_FROM_CHASSIS = 30.0
INFO_LABEL_MIN_WIDTH = 200.0


class StyleInfo(NamedTuple):
    chassis: str = "rounded=1;whiteSpace=wrap;html=1;fillColor=#E6E6E6;strokeColor=#666666;shadow=1;fontColor=#333333;fontSize=10;"
    port: str = "shape=rectangle;rounded=0;whiteSpace=wrap;html=1;fontSize=8;fontColor=#333333;"  # Domyślny kolor portu i czcionki

    port_up_fill: str = "#D5E8D4"  # Jasnozielony
    port_up_stroke: str = "#82B366"  # Ciemnozielony
    port_down_fill: str = "#F8CECC"  # Jasnoczerwony
    port_down_stroke: str = "#B85450"  # Ciemnoczerwony
    port_shutdown_fill: str = "#FFE6CC"  # Jasnopomarańczowy/brzoskwiniowy
    port_shutdown_stroke: str = "#D79B00"  # Ciemnopomarańczowy/brązowy
    port_unknown_fill: str = "#E1D5E7"  # Jasnofioletowy
    port_unknown_stroke: str = "#9673A6"  # Ciemnofioletowy

    aux_line: str = "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#AAAAAA;html=1;rounded=0;"
    label_rot: str = "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=top;whiteSpace=wrap;overflow=visible;rotation=-90;fontSize=9;fontColor=#444444;"
    label_hor: str = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;overflow=visible;fontSize=9;fontColor=#444444;"
    info_label: str = "text;html=1;strokeColor=#BABABA;fillColor=#F8F8F8;align=left;verticalAlign=top;fontSize=9;whiteSpace=wrap;overflow=visible;rounded=1;spacing=4;border=#C3C3C3;fontColor=#333333;"
    dummy_endpoint_style: str = "shape=ellipse;perimeter=ellipsePerimeter;fillColor=none;strokeColor=none;resizable=0;movable=0;editable=0;portConstraint=none;noLabel=1;selectable=0;deletable=0;points=[];"


def _extract_styles_from_template(template_path: str) -> StyleInfo:
    if ET is None:
        logger.error(
            "Moduł ET (ElementTree) nie jest dostępny w _extract_styles_from_template. Używam domyślnych stylów Draw.io.")
        return StyleInfo()

    logger.debug(f"Próba wczytania stylów Draw.io z szablonu: {template_path}")
    tree = drawio_utils.load_drawio_template(template_path)
    default_styles = StyleInfo()  # Pobierz domyślne wartości na start

    if tree is None:
        logger.warning(f"Nie udało się wczytać szablonu '{template_path}', używanie domyślnych stylów Draw.io.")
        return default_styles

    root = tree.getroot()
    diag_root_cell = root.find(".//root")
    if diag_root_cell is None:
        logger.warning(f"Brak elementu <root> w szablonie '{template_path}', używanie domyślnych stylów Draw.io.")
        return default_styles

    loaded_chassis_style = default_styles.chassis
    loaded_port_style = default_styles.port
    # Można dodać ładowanie innych specyficznych kolorów, jeśli są zdefiniowane w szablonie
    # np. przez specjalne komórki z wartościami "PORT_UP_FILL_TEMPLATE" itp.
    # Na razie zakładamy, że szablon definiuje tylko ogólny styl chassis i portu.

    for cell in diag_root_cell.findall("./mxCell[@vertex='1']"):
        style = cell.get("style", "")
        value = cell.get("value", "")
        if "CHASSIS_TEMPLATE" in value and style:
            loaded_chassis_style = style
            logger.info(f"Znaleziono styl obudowy z szablonu Draw.io: {style}")
        elif "PORT_TEMPLATE" in value and style:
            loaded_port_style = style
            logger.info(f"Znaleziono styl portu z szablonu Draw.io: {style}")

    # Zwróć StyleInfo z załadowanymi stylami chassis/port i domyślnymi dla reszty
    return default_styles._replace(chassis=loaded_chassis_style, port=loaded_port_style)


def calculate_dynamic_device_size(device_info: Dict[str, Any], api_client: LibreNMSAPI,
                                  device_index_for_log: int = 0) -> Tuple[float, float]:
    """Oblicza dynamiczny rozmiar urządzenia. Deleguje do common_device_logic."""
    if ET is None: return (common_device_logic.MIN_CHASSIS_WIDTH, common_device_logic.MIN_CHASSIS_HEIGHT)

    logger.debug(f"DrawIO: Obliczanie rozmiaru dla urządzenia (log index: {device_index_for_log})...")
    try:
        prepared_data = common_device_logic.prepare_device_display_data(device_info, api_client, device_index_for_log)
        width, height = common_device_logic.get_device_render_size_from_prepared_data(prepared_data)
        logger.debug(
            f"DrawIO: Obliczony rozmiar dla urządzenia '{prepared_data.canonical_identifier}': {width}x{height}")
        return width, height
    except Exception as e:
        logger.error(f"DrawIO: Błąd podczas obliczania rozmiaru urządzenia: {e}. Używam domyślnych wymiarów.",
                     exc_info=True)
        return (common_device_logic.MIN_CHASSIS_WIDTH, common_device_logic.MIN_CHASSIS_HEIGHT)


def add_device_to_diagram(
        global_root_cell: ET.Element,
        device_api_info: Dict[str, Any],
        api_client: LibreNMSAPI,
        position: Tuple[float, float],
        device_internal_idx: int,
        styles: StyleInfo
) -> Optional[Dict[Any, PortEndpointData]]:
    if ET is None:
        logger.critical("add_device_to_diagram (DrawIO): Moduł ET (ElementTree) nie jest dostępny.")
        return None

    port_map_for_device: Dict[Any, PortEndpointData] = {}
    offset_x, offset_y = position
    group_id_base = f"dev{device_internal_idx}"
    group_cell_id = f"group_{group_id_base}"

    try:
        prepared_data: DeviceDisplayData = common_device_logic.prepare_device_display_data(
            device_api_info, api_client, device_internal_idx
        )
    except Exception as e:
        logger.error(
            f"DrawIO: Krytyczny błąd przygotowania danych dla '{device_api_info.get('hostname')}': {e}. Pomijam.",
            exc_info=True)
        return None

    current_host_identifier = prepared_data.canonical_identifier
    logger.info(
        f"DrawIO: Dodawanie urządzenia: {current_host_identifier} (idx: {device_internal_idx}) na ({offset_x:.0f}, {offset_y:.0f})")

    chassis_width = prepared_data.chassis_layout.width
    chassis_height = prepared_data.chassis_layout.height

    device_group_cell = drawio_utils.create_group_cell(
        group_cell_id, "1", offset_x, offset_y, chassis_width, chassis_height
    )
    global_root_cell.append(device_group_cell)

    chassis_id = f"chassis_{group_id_base}"
    chassis_cell = drawio_utils.create_vertex_cell(
        chassis_id, group_cell_id, "", 0, 0, chassis_width, chassis_height, styles.chassis
    )
    global_root_cell.append(chassis_cell)

    # Rysowanie portów fizycznych
    num_layout_rows = prepared_data.chassis_layout.num_rows
    ports_per_row_config = prepared_data.chassis_layout.ports_per_row
    ports_in_rows_distribution: List[int] = []
    # ... (logika ports_in_rows_distribution jak w poprzedniej odpowiedzi) ...
    if prepared_data.physical_ports_for_chassis_layout:
        if num_layout_rows == 1:
            ports_in_rows_distribution.append(len(prepared_data.physical_ports_for_chassis_layout))
        elif num_layout_rows == 2:
            r1_c = math.ceil(len(prepared_data.physical_ports_for_chassis_layout) / 2.0);
            ports_in_rows_distribution.append(int(r1_c))
            ports_in_rows_distribution.append(len(prepared_data.physical_ports_for_chassis_layout) - int(r1_c))
        else:
            remaining_ports = len(prepared_data.physical_ports_for_chassis_layout)
            for _ in range(num_layout_rows):
                count_this_row = min(remaining_ports, ports_per_row_config);
                ports_in_rows_distribution.append(count_this_row)
                remaining_ports -= count_this_row
                if remaining_ports <= 0: break

    current_port_overall_idx = 0
    for row_idx, num_ports_in_this_row in enumerate(ports_in_rows_distribution):
        if num_ports_in_this_row == 0: continue
        current_row_content_width = num_ports_in_this_row * PORT_WIDTH + max(0,
                                                                             num_ports_in_this_row - 1) * HORIZONTAL_SPACING
        row_start_x_relative = (chassis_width - current_row_content_width) / 2
        for col_idx_in_row in range(num_ports_in_this_row):
            if current_port_overall_idx >= len(prepared_data.physical_ports_for_chassis_layout): break
            port_api_info = prepared_data.physical_ports_for_chassis_layout[current_port_overall_idx]
            visual_port_number_str = str(current_port_overall_idx + 1)
            port_x_relative = row_start_x_relative + col_idx_in_row * (PORT_WIDTH + HORIZONTAL_SPACING)
            port_y_relative = ROW_OFFSET_Y + row_idx * (PORT_HEIGHT + VERTICAL_SPACING)
            port_ifindex = port_api_info.get("ifIndex");
            port_id_api = port_api_info.get("port_id")
            port_cell_base_id = f"p{port_ifindex if port_ifindex is not None else port_id_api if port_id_api is not None else f'vis{visual_port_number_str}'}"
            port_cell_id = f"port_{group_id_base}_{port_cell_base_id}"
            conn_dummy_endpoint_id = f"ep_conn_{group_id_base}_{port_cell_base_id}"

            status = str(port_api_info.get("ifOperStatus", "unknown")).lower()
            admin_status = str(port_api_info.get("ifAdminStatus", "unknown")).lower()
            port_style_actual = styles.port
            fill_color, stroke_color = styles.port_unknown_fill, styles.port_unknown_stroke
            if admin_status == "down":
                fill_color, stroke_color = styles.port_shutdown_fill, styles.port_shutdown_stroke
            elif status == "up":
                fill_color, stroke_color = styles.port_up_fill, styles.port_up_stroke
            elif status == "down" or status == "lowerlayerdown":
                fill_color, stroke_color = styles.port_down_fill, styles.port_down_stroke
            port_style_actual = drawio_utils.set_style_value(port_style_actual, "fillColor", fill_color)
            port_style_actual = drawio_utils.set_style_value(port_style_actual, "strokeColor", stroke_color)

            port_cell = drawio_utils.create_vertex_cell(port_cell_id, group_cell_id, visual_port_number_str,
                                                        port_x_relative, port_y_relative, PORT_WIDTH, PORT_HEIGHT,
                                                        port_style_actual)
            global_root_cell.append(port_cell)

            center_x_port_relative = port_x_relative + PORT_WIDTH / 2
            conn_line_orientation: str;
            conn_line_endpoint_y_relative: float
            if row_idx % 2 == 0:
                conn_line_endpoint_y_relative = port_y_relative - WAYPOINT_OFFSET; conn_line_orientation = "up"
            else:
                conn_line_endpoint_y_relative = port_y_relative + PORT_HEIGHT + WAYPOINT_OFFSET; conn_line_orientation = "down"
            conn_endpoint_abs_x = offset_x + center_x_port_relative;
            conn_endpoint_abs_y = offset_y + conn_line_endpoint_y_relative
            dummy_vertex_cell = drawio_utils.create_vertex_cell(conn_dummy_endpoint_id, "1", "",
                                                                conn_endpoint_abs_x - 0.5, conn_endpoint_abs_y - 0.5, 1,
                                                                1, styles.dummy_endpoint_style, connectable="1")
            global_root_cell.append(dummy_vertex_cell)
            endpoint_data = PortEndpointData(conn_dummy_endpoint_id, conn_endpoint_abs_x, conn_endpoint_abs_y,
                                             conn_line_orientation)
            # Mapowanie portów
            port_name_api = port_api_info.get('ifName')
            if port_ifindex is not None: port_map_for_device[f"ifindex_{port_ifindex}"] = endpoint_data
            if port_id_api is not None: port_map_for_device[f"portid_{port_id_api}"] = endpoint_data
            if port_name_api: port_map_for_device[port_name_api] = endpoint_data; port_map_for_device[
                port_name_api.lower()] = endpoint_data
            port_descr_api = port_api_info.get('ifDescr')
            if port_descr_api and port_descr_api.lower() not in [k.lower() for k in [port_name_api] if k]:
                port_map_for_device[port_descr_api] = endpoint_data; port_map_for_device[
                port_descr_api.lower()] = endpoint_data
            port_alias_api = port_api_info.get('ifAlias')
            if port_alias_api and port_alias_api.lower() not in [k.lower() for k in [port_name_api, port_descr_api] if
                                                                 k]: port_map_for_device[
                port_alias_api] = endpoint_data; port_map_for_device[port_alias_api.lower()] = endpoint_data
            port_map_for_device[visual_port_number_str] = endpoint_data

            alias_text = str(port_api_info.get("ifAlias", "")).strip()
            if alias_text:
                # ... (logika rysowania etykiety aliasu i linii pomocniczej jak w poprzedniej odpowiedzi) ...
                alias_label_id = f"label_alias_{group_id_base}_{port_cell_base_id}";
                aux_edge_id = f"edge_aux_{group_id_base}_{port_cell_base_id}"
                lines = alias_text.split('\n');
                num_lines_alias = len(lines);
                max_line_len_alias = max(len(line) for line in lines) if lines else 0
                label_unrotated_width = num_lines_alias * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
                label_unrotated_height = max(15, max_line_len_alias * (LABEL_LINE_HEIGHT * 0.65)) + 2 * LABEL_PADDING
                aux_line_start_x_abs = offset_x + center_x_port_relative;
                aux_line_end_x_abs = aux_line_start_x_abs
                label_drawio_abs_x: float;
                label_drawio_abs_y: float
                if conn_line_orientation == "up":
                    aux_line_start_y_abs = offset_y;
                    aux_line_end_y_abs = aux_line_start_y_abs - PORT_ALIAS_LINE_EXTENSION
                    label_drawio_abs_x = aux_line_end_x_abs + PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER
                    label_drawio_abs_y = aux_line_end_y_abs - label_unrotated_height - PORT_ALIAS_LABEL_OFFSET_FROM_LINE
                else:
                    aux_line_start_y_abs = offset_y + chassis_height;
                    aux_line_end_y_abs = aux_line_start_y_abs + PORT_ALIAS_LINE_EXTENSION
                    label_drawio_abs_x = aux_line_end_x_abs + PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER
                    label_drawio_abs_y = aux_line_end_y_abs + PORT_ALIAS_LABEL_OFFSET_FROM_LINE
                alias_label_cell = drawio_utils.create_vertex_cell(alias_label_id, "1", alias_text, label_drawio_abs_x,
                                                                   label_drawio_abs_y, label_unrotated_width,
                                                                   label_unrotated_height, styles.label_rot,
                                                                   connectable="0")
                global_root_cell.append(alias_label_cell)
                aux_edge_cell = drawio_utils.create_floating_edge_cell(aux_edge_id, "1", styles.aux_line,
                                                                       (aux_line_start_x_abs, aux_line_start_y_abs),
                                                                       (aux_line_end_x_abs, aux_line_end_y_abs))
                global_root_cell.append(aux_edge_cell)
            current_port_overall_idx += 1
        if current_port_overall_idx >= len(prepared_data.physical_ports_for_chassis_layout): break

    # --- Rysowanie portu MGMT0 ---
    mgmt0_info = prepared_data.mgmt0_port_info
    if mgmt0_info:
        # ... (logika rysowania mgmt0 jak w poprzedniej odpowiedzi, używając styles.port_shutdown_fill itp.) ...
        logger.debug(f"  DrawIO: Dodawanie portu mgmt0 dla {current_host_identifier}...")
        mgmt0_ifindex = mgmt0_info.get('ifIndex');
        mgmt0_port_id_api = mgmt0_info.get('port_id')
        mgmt0_cell_base_id = f"mgmt0_{mgmt0_ifindex if mgmt0_ifindex is not None else mgmt0_port_id_api if mgmt0_port_id_api is not None else 'na'}"
        mgmt0_port_cell_id = f"port_{group_id_base}_{mgmt0_cell_base_id}";
        mgmt0_conn_dummy_endpoint_id = f"ep_conn_{group_id_base}_{mgmt0_cell_base_id}"
        mgmt0_x_relative = chassis_width + HORIZONTAL_SPACING;
        mgmt0_y_relative = chassis_height / 2 - PORT_HEIGHT / 2
        status_mgmt0 = str(mgmt0_info.get("ifOperStatus", "unknown")).lower();
        admin_status_mgmt0 = str(mgmt0_info.get("ifAdminStatus", "unknown")).lower()
        mgmt0_style_actual = styles.port;
        fill_color_m = styles.port_unknown_fill;
        stroke_color_m = styles.port_unknown_stroke
        if admin_status_mgmt0 == "down":
            fill_color_m = styles.port_shutdown_fill; stroke_color_m = styles.port_shutdown_stroke
        elif status_mgmt0 == "up":
            fill_color_m = styles.port_up_fill; stroke_color_m = styles.port_up_stroke
        elif status_mgmt0 == "down" or status_mgmt0 == "lowerlayerdown":
            fill_color_m = styles.port_down_fill; stroke_color_m = styles.port_down_stroke
        mgmt0_style_actual = drawio_utils.set_style_value(mgmt0_style_actual, "fillColor", fill_color_m)
        mgmt0_style_actual = drawio_utils.set_style_value(mgmt0_style_actual, "strokeColor", stroke_color_m)
        mgmt0_cell = drawio_utils.create_vertex_cell(mgmt0_port_cell_id, group_cell_id, "M", mgmt0_x_relative,
                                                     mgmt0_y_relative, PORT_WIDTH, PORT_HEIGHT, mgmt0_style_actual)
        global_root_cell.append(mgmt0_cell)
        conn_endpoint_abs_x_mgmt = offset_x + mgmt0_x_relative + PORT_WIDTH + WAYPOINT_OFFSET
        conn_endpoint_abs_y_mgmt = offset_y + mgmt0_y_relative + PORT_HEIGHT / 2
        mgmt0_dummy_vertex_cell = drawio_utils.create_vertex_cell(mgmt0_conn_dummy_endpoint_id, "1", "",
                                                                  conn_endpoint_abs_x_mgmt - 0.5,
                                                                  conn_endpoint_abs_y_mgmt - 0.5, 1, 1,
                                                                  styles.dummy_endpoint_style, connectable="1")
        global_root_cell.append(mgmt0_dummy_vertex_cell)
        endpoint_data_mgmt = PortEndpointData(mgmt0_conn_dummy_endpoint_id, conn_endpoint_abs_x_mgmt,
                                              conn_endpoint_abs_y_mgmt, "right")
        mgmt0_name_api = mgmt0_info.get('ifName')
        if mgmt0_ifindex is not None: port_map_for_device[f"ifindex_{mgmt0_ifindex}"] = endpoint_data_mgmt
        if mgmt0_port_id_api is not None: port_map_for_device[f"portid_{mgmt0_port_id_api}"] = endpoint_data_mgmt
        if mgmt0_name_api: port_map_for_device[mgmt0_name_api] = endpoint_data_mgmt; port_map_for_device[
            mgmt0_name_api.lower()] = endpoint_data_mgmt
        port_map_for_device["mgmt0"] = endpoint_data_mgmt
        alias_text_mgmt = str(mgmt0_info.get("ifAlias", "")).strip()
        if alias_text_mgmt:
            mgmt0_alias_label_id = f"label_alias_{group_id_base}_{mgmt0_cell_base_id}";
            mgmt0_aux_edge_id = f"edge_aux_{group_id_base}_{mgmt0_cell_base_id}"
            lines_mgmt = alias_text_mgmt.split('\n');
            num_lines_mgmt = len(lines_mgmt);
            max_line_len_mgmt = max(len(line) for line in lines_mgmt) if lines_mgmt else 0
            label_mgmt_width = max(30, max_line_len_mgmt * (LABEL_LINE_HEIGHT * 0.7)) + 2 * LABEL_PADDING
            label_mgmt_height = num_lines_mgmt * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
            aux_line_start_x_mgmt_abs = offset_x + mgmt0_x_relative + PORT_WIDTH;
            aux_line_start_y_mgmt_abs = offset_y + mgmt0_y_relative + PORT_HEIGHT / 2
            aux_line_end_x_mgmt_abs = aux_line_start_x_mgmt_abs + PORT_ALIAS_LINE_EXTENSION;
            aux_line_end_y_mgmt_abs = aux_line_start_y_mgmt_abs
            label_drawio_x_mgmt_abs = aux_line_end_x_mgmt_abs + PORT_ALIAS_LABEL_OFFSET_FROM_LINE
            label_drawio_y_mgmt_abs = aux_line_end_y_mgmt_abs - label_mgmt_height / 2
            mgmt0_alias_label_cell = drawio_utils.create_vertex_cell(mgmt0_alias_label_id, "1", alias_text_mgmt,
                                                                     label_drawio_x_mgmt_abs, label_drawio_y_mgmt_abs,
                                                                     label_mgmt_width, label_mgmt_height,
                                                                     styles.label_hor, connectable="0")
            global_root_cell.append(mgmt0_alias_label_cell)
            mgmt0_aux_edge_cell = drawio_utils.create_floating_edge_cell(mgmt0_aux_edge_id, "1", styles.aux_line,
                                                                         (aux_line_start_x_mgmt_abs,
                                                                          aux_line_start_y_mgmt_abs),
                                                                         (aux_line_end_x_mgmt_abs,
                                                                          aux_line_end_y_mgmt_abs))
            global_root_cell.append(mgmt0_aux_edge_cell)

    # --- ETYKIETA INFORMACYJNA URZĄDZENIA ---
    # ... (logika generowania HTML i dodawania etykiety jak w poprzedniej odpowiedzi, bez zmian) ...
    info_label_id = f"info_label_{group_id_base}";
    dev_info = prepared_data.device_api_info
    dev_id_val = dev_info.get('device_id', 'N/A');
    hostname_raw = dev_info.get('hostname', '');
    ip_raw = dev_info.get('ip', '');
    purpose_raw = dev_info.get('purpose', '')
    display_name_main = prepared_data.canonical_identifier
    if prepared_data.is_stack: display_name_main += " <b>(STACK)</b>"
    display_extra_info = [];
    hostname_str = str(hostname_raw).strip();
    purpose_str = str(purpose_raw).strip();
    main_name_no_stack = display_name_main.replace(" <b>(STACK)</b>", "")
    if hostname_str and hostname_str != main_name_no_stack and not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$',
                                                                            hostname_str): display_extra_info.append(
        f"Host: {hostname_str}")
    if purpose_str and purpose_str != main_name_no_stack: display_extra_info.append(f"Cel: {purpose_str}")
    temp_display_ip = str(ip_raw).strip() if ip_raw else 'N/A'
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname_str) and not ip_raw: temp_display_ip = hostname_str
    base_device_label_html = f"<div style='text-align:left; padding:2px;'><b>{display_name_main}</b><br/>ID: {dev_id_val}"
    if display_extra_info: base_device_label_html += "<br/>" + "<br/>".join(display_extra_info)
    base_device_label_html += f"<br/>IP: {temp_display_ip}</div>"
    physical_ports_html = f"<div style='padding:2px;'><b>Porty Fizyczne ({len(prepared_data.all_physical_ports)}):</b></div><div style='margin:0; padding-left:7px; max-height:{common_device_logic.PHYSICAL_PORT_LIST_MAX_HEIGHT}px; overflow-y:auto; overflow-x:hidden;'>"
    if prepared_data.all_physical_ports:
        for phys_port in prepared_data.all_physical_ports:
            name = str(phys_port.get('ifName', 'N/A')).strip();
            descr = str(phys_port.get('ifDescr', '')).strip();
            alias = str(phys_port.get('ifAlias', '')).strip()
            status_disp = str(phys_port.get('ifOperStatus', 'unknown')).lower();
            admin_status_disp = str(phys_port.get('ifAdminStatus', 'unknown')).lower()
            status_color = styles.port_unknown_fill  # Domyślny
            if admin_status_disp == "down":
                status_color = styles.port_shutdown_fill
            elif status_disp == "up":
                status_color = styles.port_up_fill
            elif status_disp == "down" or status_disp == "lowerlayerdown":
                status_color = styles.port_down_fill
            extra_info_parts = []
            if alias: extra_info_parts.append(f"Alias: {alias}")
            if descr and descr != name and descr != alias: extra_info_parts.append(f"Opis: {descr}")
            extra_info_str = f" <i>({'; '.join(extra_info_parts)})</i>" if extra_info_parts else ""
            physical_ports_html += f"<font color='{status_color}'>•</font>&nbsp;{name}{extra_info_str}&nbsp;({status_disp})<br/>"
    else:
        physical_ports_html += "<div style='padding-left:7px;'>(brak)</div>"
    physical_ports_html += "</div>"
    logical_interface_list_html = f"<div style='padding:2px;'><b>Inne Interfejsy ({len(prepared_data.logical_interfaces)}):</b></div><div style='margin:0; padding-left:7px; max-height:{common_device_logic.LOGICAL_IF_LIST_MAX_HEIGHT}px; overflow-y:auto; overflow-x:hidden;'>"
    if prepared_data.logical_interfaces:
        for logical_if in prepared_data.logical_interfaces:
            name = str(logical_if.get('ifName') or logical_if.get('ifDescr', 'N/A')).strip();
            status_disp = str(logical_if.get('ifOperStatus', 'unknown')).lower();
            admin_status_disp = str(logical_if.get('ifAdminStatus', 'unknown')).lower()
            status_color = styles.port_unknown_fill
            if admin_status_disp == "down":
                status_color = styles.port_shutdown_fill
            elif status_disp == "up":
                status_color = styles.port_up_fill
            elif status_disp == "down":
                status_color = styles.port_down_fill
            if_type_str = str(logical_if.get('_ifType_iana_debug', '')).strip();
            type_info = f" (Typ: {if_type_str})" if if_type_str else ""
            logical_interface_list_html += f"<font color='{status_color}'>•</font>&nbsp;{name}{type_info}&nbsp;({status_disp})<br/>"
    else:
        logical_interface_list_html += "<div style='padding-left:7px;'>(brak)</div>"
    logical_interface_list_html += "</div>"
    full_device_label_html = f"{base_device_label_html}<hr size='1' style='margin: 2px 0;'/>{physical_ports_html}<hr size='1' style='margin: 2px 0;'/>{logical_interface_list_html}"
    info_label_width = max(chassis_width * 0.7, INFO_LABEL_MIN_WIDTH)
    num_base_lines_info = 3 + len(display_extra_info);
    base_h_info = num_base_lines_info * (LABEL_LINE_HEIGHT + 3) + 10
    phys_ports_section_h = min(common_device_logic.PHYSICAL_PORT_LIST_MAX_HEIGHT,
                               max(20, len(prepared_data.all_physical_ports) * (LABEL_LINE_HEIGHT + 3))) + 25
    logical_ifs_section_h = min(common_device_logic.LOGICAL_IF_LIST_MAX_HEIGHT,
                                max(20, len(prepared_data.logical_interfaces) * (LABEL_LINE_HEIGHT + 3))) + 25
    info_label_height = base_h_info + phys_ports_section_h + logical_ifs_section_h + 20
    info_label_abs_x = offset_x - info_label_width - INFO_LABEL_MARGIN_FROM_CHASSIS
    info_label_abs_y = offset_y + (chassis_height / 2) - (info_label_height / 2)
    info_label_abs_y = max((drawio_layout.DEFAULT_MARGIN_Y / 3) if drawio_layout else 20.0, info_label_abs_y)
    dev_info_label_cell = drawio_utils.create_vertex_cell(info_label_id, "1", full_device_label_html, info_label_abs_x,
                                                          info_label_abs_y, info_label_width, info_label_height,
                                                          styles.info_label, connectable="0")
    global_root_cell.append(dev_info_label_cell)

    logger.info(f"✓ DrawIO: Urządzenie {current_host_identifier} dynamicznie przetworzone i dodane.")
    return port_map_for_device