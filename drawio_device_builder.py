# --- drawio_device_builder.py ---
import logging
import math
import re
import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple, Optional, Any, NamedTuple

from librenms_client import LibreNMSAPI
import drawio_utils
# import drawio_layout # Nie jest już bezpośrednio potrzebny tutaj

import common_device_logic
from common_device_logic import PortEndpointData, DeviceDisplayData

logger = logging.getLogger(__name__)


class StyleInfo(NamedTuple):
    chassis: str = "rounded=1;whiteSpace=wrap;html=1;fillColor=#E6E6E6;strokeColor=#666666;shadow=1;fontColor=#333333;fontSize=10;"
    port: str = "shape=rectangle;rounded=0;whiteSpace=wrap;html=1;fontSize=8;fontColor=#333333;"
    port_up_fill: str = "#D5E8D4";
    port_up_stroke: str = "#82B366"
    port_down_fill: str = "#F8CECC";
    port_down_stroke: str = "#B85450"
    port_shutdown_fill: str = "#FFE6CC";
    port_shutdown_stroke: str = "#D79B00"
    port_unknown_fill: str = "#E1D5E7";
    port_unknown_stroke: str = "#9673A6"
    aux_line: str = "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#AAAAAA;html=1;rounded=0;"
    label_rot: str = "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=top;whiteSpace=wrap;overflow=visible;rotation=-90;fontSize=9;fontColor=#444444;"
    label_hor: str = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;overflow=visible;fontSize=9;fontColor=#444444;"
    info_label: str = "text;html=1;strokeColor=#BABABA;fillColor=#F8F8F8;align=left;verticalAlign=top;fontSize=9;whiteSpace=wrap;overflow=visible;rounded=1;spacing=4;border=#C3C3C3;fontColor=#333333;"
    dummy_endpoint_style: str = "shape=ellipse;perimeter=ellipsePerimeter;fillColor=none;strokeColor=none;resizable=0;movable=0;editable=0;portConstraint=none;noLabel=1;selectable=0;deletable=0;points=[];"


def _extract_styles_from_template(template_path: str) -> StyleInfo: # Usunięto argument config
    if ET is None:
        logger.error("Moduł ET niedostępny w _extract_styles_from_template.")
        return StyleInfo()
    logger.debug(f"Próba wczytania stylów Draw.io z szablonu: {template_path}")
    tree = drawio_utils.load_drawio_template(template_path)
    default_styles = StyleInfo()
    if tree is None:
        logger.warning(f"Nie udało się wczytać szablonu '{template_path}'. Używam domyślnych stylów.")
        return default_styles

    root = tree.getroot()
    diag_root_cell = root.find(".//root")
    if diag_root_cell is None:
        logger.warning(f"Brak <root> w szablonie '{template_path}'. Używam domyślnych stylów.")
        return default_styles

    loaded_chassis_style, loaded_port_style = default_styles.chassis, default_styles.port

    for cell in diag_root_cell.findall("./mxCell[@vertex='1']"):
        style_attr, value_attr = cell.get("style", ""), cell.get("value", "")
        if "CHASSIS_TEMPLATE" in value_attr and style_attr:
            loaded_chassis_style = style_attr
            logger.info(f"Znaleziono styl obudowy z szablonu Draw.io.")
        elif "PORT_TEMPLATE" in value_attr and style_attr:
            loaded_port_style = style_attr
            logger.info(f"Znaleziono styl portu z szablonu Draw.io.")

    return default_styles._replace(chassis=loaded_chassis_style, port=loaded_port_style)


def calculate_dynamic_device_size(
        device_info: Dict[str, Any],
        api_client: LibreNMSAPI,
        config: Dict[str, Any],
        device_index_for_log: int = 0
) -> Tuple[float, float]:
    if ET is None: # ET nie jest tu używane, ale zostawiam dla spójności z oryginalnym kodem
        return (config.get('min_chassis_width'), config.get('min_chassis_height'))

    logger.debug(f"DrawIO: Obliczanie rozmiaru dla urządzenia (log index: {device_index_for_log})...")
    try:
        prepared_data = common_device_logic.prepare_device_display_data(
            device_info, api_client, device_index_for_log, config
        )
        width, height = common_device_logic.get_device_render_size_from_prepared_data(prepared_data)
        logger.debug(f"DrawIO: Obliczony rozmiar dla '{prepared_data.canonical_identifier}': {width}x{height}")
        return width, height
    except Exception as e:
        logger.error(f"DrawIO: Błąd obliczania rozmiaru urządzenia: {e}. Używam domyślnych.", exc_info=True)
        return (config.get('min_chassis_width'), config.get('min_chassis_height'))


def add_device_to_diagram(
        global_root_cell: ET.Element,
        prepared_data: DeviceDisplayData,
        api_client: LibreNMSAPI,
        position: Tuple[float, float],
        device_internal_idx: int,
        styles: StyleInfo,
        config: Dict[str, Any]
) -> Optional[Dict[Any, PortEndpointData]]:
    if ET is None:
        logger.critical("add_device_to_diagram (DrawIO): Moduł ET niedostępny.")
        return None

    port_map_for_device: Dict[Any, PortEndpointData] = {}
    offset_x, offset_y = position
    group_id_base, group_cell_id = f"dev{device_internal_idx}", f"group_dev{device_internal_idx}"

    current_host_identifier = prepared_data.canonical_identifier
    logger.info(
        f"DrawIO: Dodawanie urządzenia: {current_host_identifier} (idx: {device_internal_idx}) na ({offset_x:.0f}, {offset_y:.0f})")

    chassis_width, chassis_height = prepared_data.chassis_layout.width, prepared_data.chassis_layout.height
    device_group_cell = drawio_utils.create_group_cell(group_cell_id, "1", offset_x, offset_y, chassis_width,
                                                       chassis_height)
    global_root_cell.append(device_group_cell)

    chassis_id = f"chassis_{group_id_base}"
    chassis_cell = drawio_utils.create_vertex_cell(chassis_id, group_cell_id, "", 0, 0, chassis_width, chassis_height,
                                                   styles.chassis)
    global_root_cell.append(chassis_cell)

    ports_to_draw = prepared_data.physical_ports_for_chassis_layout
    num_layout_rows, ports_per_row_config = prepared_data.chassis_layout.num_rows, prepared_data.chassis_layout.ports_per_row

    port_width_cfg = config.get('port_width')
    port_height_cfg = config.get('port_height')
    horizontal_spacing_cfg = config.get('port_horizontal_spacing')
    vertical_spacing_cfg = config.get('port_vertical_spacing')
    row_offset_y_cfg = config.get('port_row_offset_y')
    waypoint_offset_cfg = config.get('waypoint_offset')
    port_alias_line_ext_cfg = config.get('port_alias_line_extension')
    port_alias_label_offset_cfg = config.get('port_alias_label_offset_from_line')
    port_alias_label_x_offset_cfg = config.get('port_alias_label_x_offset_from_line_center')
    label_line_height_cfg = config.get('label_line_height')
    label_padding_cfg = config.get('label_padding')
    info_label_margin_cfg = config.get('info_label_margin_from_chassis')
    info_label_min_w_cfg = config.get('info_label_min_width')
    info_label_max_w_cfg = config.get('info_label_max_width')
    physical_port_list_max_h_cfg = config.get('physical_port_list_max_height')
    logical_if_list_max_h_cfg = config.get('logical_if_list_max_height')

    ports_in_rows_dist: List[int] = []
    if ports_to_draw:
        if num_layout_rows == 1:
            ports_in_rows_dist.append(len(ports_to_draw))
        elif num_layout_rows == 2:
            r1_c = math.ceil(len(ports_to_draw) / 2.0)
            ports_in_rows_dist.append(int(r1_c))
            ports_in_rows_dist.append(len(ports_to_draw) - int(r1_c))
        else:
            if num_layout_rows > 0:
                rem_p = len(ports_to_draw)
                for _ in range(num_layout_rows):
                    c_tr = min(rem_p, ports_per_row_config)
                    ports_in_rows_dist.append(c_tr)
                    rem_p -= c_tr
                    if rem_p <= 0: break

    cur_port_idx = 0
    for row_idx, num_ports_row in enumerate(ports_in_rows_dist):
        if num_ports_row == 0: continue
        cur_row_w = num_ports_row * port_width_cfg + max(0, num_ports_row - 1) * horizontal_spacing_cfg
        row_start_x = (chassis_width - cur_row_w) / 2
        for col_idx in range(num_ports_row):
            if cur_port_idx >= len(ports_to_draw): break
            p_info = ports_to_draw[cur_port_idx]
            vis_num_str = str(cur_port_idx + 1)
            px, py = row_start_x + col_idx * (port_width_cfg + horizontal_spacing_cfg), \
                     row_offset_y_cfg + row_idx * (port_height_cfg + vertical_spacing_cfg)

            p_ifidx, p_id_api = p_info.get("ifIndex"), p_info.get("port_id")
            p_cell_base_id = f"p{p_ifidx if p_ifidx is not None else p_id_api if p_id_api is not None else f'vis{vis_num_str}'}"
            p_cell_id, conn_dummy_id = f"port_{group_id_base}_{p_cell_base_id}", f"ep_conn_{group_id_base}_{p_cell_base_id}"

            status, admin_status = str(p_info.get("ifOperStatus", "u")).lower(), str(
                p_info.get("ifAdminStatus", "u")).lower()
            p_style = styles.port
            fill, stroke = styles.port_unknown_fill, styles.port_unknown_stroke
            if admin_status == "down":
                fill, stroke = styles.port_shutdown_fill, styles.port_shutdown_stroke
            elif status == "up":
                fill, stroke = styles.port_up_fill, styles.port_up_stroke
            elif status in ["down", "lowerlayerdown"]:
                fill, stroke = styles.port_down_fill, styles.port_down_stroke
            p_style = drawio_utils.set_style_value(drawio_utils.set_style_value(p_style, "fillColor", fill),
                                                   "strokeColor", stroke)

            p_cell = drawio_utils.create_vertex_cell(p_cell_id, group_cell_id, vis_num_str, px, py, port_width_cfg,
                                                     port_height_cfg, p_style)
            global_root_cell.append(p_cell)

            center_x_p_rel = px + port_width_cfg / 2
            conn_orient: str
            conn_epy_rel: float
            if row_idx % 2 == 0:
                conn_epy_rel, conn_orient = py - waypoint_offset_cfg, "up"
            else:
                conn_epy_rel, conn_orient = py + port_height_cfg + waypoint_offset_cfg, "down"

            ep_abs_x, ep_abs_y = offset_x + center_x_p_rel, offset_y + conn_epy_rel
            dummy_vtx = drawio_utils.create_vertex_cell(conn_dummy_id, "1", "", ep_abs_x - 0.5, ep_abs_y - 0.5, 1, 1,
                                                        styles.dummy_endpoint_style, connectable="1")
            global_root_cell.append(dummy_vtx)

            ep_data = PortEndpointData(conn_dummy_id, ep_abs_x, ep_abs_y, conn_orient)
            p_name_api = p_info.get('ifName')
            if p_ifidx is not None: port_map_for_device[f"ifindex_{p_ifidx}"] = ep_data
            if p_id_api is not None: port_map_for_device[f"portid_{p_id_api}"] = ep_data
            if p_name_api: port_map_for_device[p_name_api.lower()] = ep_data
            port_map_for_device[vis_num_str] = ep_data

            alias_txt = str(p_info.get("ifAlias", "")).strip()
            if alias_txt:
                alias_lbl_id, aux_edge_id = f"lbl_alias_{group_id_base}_{p_cell_base_id}", f"edge_aux_{group_id_base}_{p_cell_base_id}"
                lines = alias_txt.split('\n')
                num_lines, max_len = len(lines), max(len(l) for l in lines) if lines else 0
                lbl_unrot_w, lbl_unrot_h = num_lines * label_line_height_cfg + 2 * label_padding_cfg, \
                                           max(15, max_len * (label_line_height_cfg * 0.65)) + 2 * label_padding_cfg

                aux_sx_abs, aux_ex_abs = offset_x + center_x_p_rel, offset_x + center_x_p_rel
                lbl_drawio_x, lbl_drawio_y = 0.0, 0.0
                current_label_style = styles.label_rot

                if conn_orient == "up":
                    aux_sy_abs, aux_ey_abs = offset_y + py, offset_y + py - port_alias_line_ext_cfg
                    lbl_drawio_x, lbl_drawio_y = aux_ex_abs + port_alias_label_x_offset_cfg, \
                                                 aux_ey_abs - lbl_unrot_h - port_alias_label_offset_cfg
                elif conn_orient == "down":
                    aux_sy_abs, aux_ey_abs = offset_y + py + port_height_cfg, \
                                             offset_y + py + port_height_cfg + port_alias_line_ext_cfg
                    lbl_drawio_x, lbl_drawio_y = aux_ex_abs + port_alias_label_x_offset_cfg, \
                                                 aux_ey_abs + port_alias_label_offset_cfg

                alias_lbl_cell = drawio_utils.create_vertex_cell(alias_lbl_id, "1", alias_txt, lbl_drawio_x,
                                                                 lbl_drawio_y, lbl_unrot_w, lbl_unrot_h,
                                                                 current_label_style, connectable="0")
                global_root_cell.append(alias_lbl_cell)

                aux_edge_cell = drawio_utils.create_floating_edge_cell(aux_edge_id, "1", styles.aux_line,
                                                                       (aux_sx_abs, aux_sy_abs),
                                                                       (aux_ex_abs, aux_ey_abs))
                global_root_cell.append(aux_edge_cell)
            cur_port_idx += 1
        if cur_port_idx >= len(ports_to_draw): break

    mgmt0_info = prepared_data.mgmt0_port_info
    if mgmt0_info:
        logger.debug(f"  DrawIO: Dodawanie portu mgmt0 dla {current_host_identifier}...")
        mgmt0_ifidx, mgmt0_pid = mgmt0_info.get('ifIndex'), mgmt0_info.get('port_id')
        mgmt0_base_id = f"mgmt0_{mgmt0_ifidx if mgmt0_ifidx is not None else mgmt0_pid if mgmt0_pid is not None else 'na'}"
        mgmt0_cell_id, mgmt0_conn_id = f"port_{group_id_base}_{mgmt0_base_id}", f"ep_conn_{group_id_base}_{mgmt0_base_id}"

        mgmt0_x, mgmt0_y = chassis_width + horizontal_spacing_cfg, chassis_height / 2 - port_height_cfg / 2
        status_m, admin_status_m = str(mgmt0_info.get("ifOperStatus", "u")).lower(), str(
            mgmt0_info.get("ifAdminStatus", "u")).lower()
        mgmt0_style = styles.port
        fill_m, stroke_m = styles.port_unknown_fill, styles.port_unknown_stroke
        if admin_status_m == "down":
            fill_m, stroke_m = styles.port_shutdown_fill, styles.port_shutdown_stroke
        elif status_m == "up":
            fill_m, stroke_m = styles.port_up_fill, styles.port_up_stroke
        elif status_m in ["down", "lowerlayerdown"]:
            fill_m, stroke_m = styles.port_down_fill, styles.port_down_stroke
        mgmt0_style = drawio_utils.set_style_value(drawio_utils.set_style_value(mgmt0_style, "fillColor", fill_m),
                                                   "strokeColor", stroke_m)

        mgmt0_cell = drawio_utils.create_vertex_cell(mgmt0_cell_id, group_cell_id, "M", mgmt0_x, mgmt0_y,
                                                     port_width_cfg, port_height_cfg, mgmt0_style)
        global_root_cell.append(mgmt0_cell)

        ep_abs_x_m, ep_abs_y_m = offset_x + mgmt0_x + port_width_cfg + waypoint_offset_cfg, \
                                 offset_y + mgmt0_y + port_height_cfg / 2
        mgmt0_dummy_vtx = drawio_utils.create_vertex_cell(mgmt0_conn_id, "1", "", ep_abs_x_m - 0.5, ep_abs_y_m - 0.5, 1,
                                                          1, styles.dummy_endpoint_style, connectable="1")
        global_root_cell.append(mgmt0_dummy_vtx)

        ep_data_m = PortEndpointData(mgmt0_conn_id, ep_abs_x_m, ep_abs_y_m, "right")
        mgmt0_name_api = mgmt0_info.get('ifName')
        if mgmt0_ifidx is not None: port_map_for_device[f"ifindex_{mgmt0_ifidx}"] = ep_data_m
        if mgmt0_pid is not None: port_map_for_device[f"portid_{mgmt0_pid}"] = ep_data_m
        if mgmt0_name_api: port_map_for_device[mgmt0_name_api.lower()] = ep_data_m
        port_map_for_device["mgmt0"] = ep_data_m

        alias_txt_m = str(mgmt0_info.get("ifAlias", "")).strip()
        if alias_txt_m:
            mgmt0_alias_id, mgmt0_aux_id = f"lbl_alias_{group_id_base}_{mgmt0_base_id}", f"edge_aux_{group_id_base}_{mgmt0_base_id}"
            lines_m = alias_txt_m.split('\n')
            num_lines_m, max_len_m = len(lines_m), max(len(l) for l in lines_m) if lines_m else 0
            lbl_m_w, lbl_m_h = max(30, max_len_m * (label_line_height_cfg * 0.7)) + 2 * label_padding_cfg, \
                               num_lines_m * label_line_height_cfg + 2 * label_padding_cfg

            aux_sx_m_abs, aux_sy_m_abs = offset_x + mgmt0_x + port_width_cfg, offset_y + mgmt0_y + port_height_cfg / 2
            aux_ex_m_abs, aux_ey_m_abs = aux_sx_m_abs + port_alias_line_ext_cfg, aux_sy_m_abs
            lbl_drawio_x_m, lbl_drawio_y_m = aux_ex_m_abs + port_alias_label_offset_cfg, \
                                             aux_ey_m_abs - lbl_m_h / 2

            mgmt0_alias_cell = drawio_utils.create_vertex_cell(mgmt0_alias_id, "1", alias_txt_m, lbl_drawio_x_m,
                                                               lbl_drawio_y_m, lbl_m_w, lbl_m_h, styles.label_hor,
                                                               connectable="0")
            global_root_cell.append(mgmt0_alias_cell)
            mgmt0_aux_edge = drawio_utils.create_floating_edge_cell(mgmt0_aux_id, "1", styles.aux_line,
                                                                    (aux_sx_m_abs, aux_sy_m_abs),
                                                                    (aux_ex_m_abs, aux_ey_m_abs))
            global_root_cell.append(mgmt0_aux_edge)

    info_lbl_id = f"info_lbl_{group_id_base}"
    dev_info = prepared_data.device_api_info
    dev_id_val, hostname_raw, ip_raw, purpose_raw = dev_info.get('device_id', 'N/A'), \
        dev_info.get('hostname', ''), \
        dev_info.get('ip', ''), \
        dev_info.get('purpose', '')
    display_name_main = prepared_data.canonical_identifier
    if prepared_data.is_stack:
        display_name_main += " <b>(STACK)</b>"

    ports_limit_info_html = ""
    if prepared_data.ports_display_limited:
        ports_limit_info_html = (
            f"<br/><font color='#FF8C00' style='font-size:8px'><i>(Wyświetlanie portów na chassis ograniczone do "
            f"{len(prepared_data.physical_ports_for_chassis_layout)} "
            f"z {prepared_data.total_physical_ports_before_limit} kandydatów.)</i></font>")

    display_extra = []
    hostname_s, purpose_s = str(hostname_raw).strip(), str(purpose_raw).strip()
    main_name_no_stack = display_name_main.replace("<b>(STACK)</b>", "").strip()
    if hostname_s and hostname_s != main_name_no_stack and not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', hostname_s):
        display_extra.append(f"Host: {hostname_s}")
    if purpose_s and purpose_s != main_name_no_stack:
        display_extra.append(f"Cel: {purpose_s}")

    temp_disp_ip = str(ip_raw).strip() if ip_raw and str(ip_raw).strip() else 'N/A'
    if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', hostname_s) and not (ip_raw and str(ip_raw).strip()):
        temp_disp_ip = hostname_s

    base_dev_lbl_html = f"<div style='text-align:left;padding:2px;'><b>{display_name_main}</b>{ports_limit_info_html}<br/>ID: {dev_id_val}"
    if display_extra:
        base_dev_lbl_html += "<br/>" + "<br/>".join(display_extra)
    base_dev_lbl_html += f"<br/>IP: {temp_disp_ip}</div>"

    phys_ports_html = f"<div style='padding:2px;'><b>Porty Fizyczne ({len(prepared_data.all_physical_ports)}):</b></div><div style='margin:0;padding-left:7px;max-height:{physical_port_list_max_h_cfg}px;overflow-y:auto;overflow-x:hidden;'>"
    if prepared_data.all_physical_ports:
        for p in prepared_data.all_physical_ports:
            name, descr, alias = str(p.get('ifName', 'N/A')).strip(), \
                str(p.get('ifDescr', '')).strip(), \
                str(p.get('ifAlias', '')).strip()
            s_disp, aS_disp = str(p.get('ifOperStatus', 'u')).lower(), str(p.get('ifAdminStatus', 'u')).lower()

            s_fill_val = styles.port_unknown_fill.split('=')[-1]
            if aS_disp == "down":
                s_fill_val = styles.port_shutdown_fill.split('=')[-1]
            elif s_disp == "up":
                s_fill_val = styles.port_up_fill.split('=')[-1]
            elif s_disp in ["down", "lowerlayerdown"]:
                s_fill_val = styles.port_down_fill.split('=')[-1]

            extra_p_info = []
            if alias: extra_p_info.append(f"Alias: {alias}")
            if descr and descr != name and descr != alias: extra_p_info.append(f"Opis: {descr}")
            extra_s = f" <i>({'; '.join(extra_p_info)})</i>" if extra_p_info else ""
            phys_ports_html += f"<font color='{s_fill_val}'>•</font>&nbsp;{name}{extra_s}&nbsp;({s_disp})<br/>"
    else:
        phys_ports_html += "<div style='padding-left:7px;'>(brak)</div>"
    phys_ports_html += "</div>"

    log_ifs_html = f"<div style='padding:2px;'><b>Inne Interfejsy ({len(prepared_data.logical_interfaces)}):</b></div><div style='margin:0;padding-left:7px;max-height:{logical_if_list_max_h_cfg}px;overflow-y:auto;overflow-x:hidden;'>"
    if prepared_data.logical_interfaces:
        for l_if in prepared_data.logical_interfaces:
            name_l = str(l_if.get('ifName') or l_if.get('ifDescr', 'N/A')).strip()
            s_disp_l, aS_disp_l = str(l_if.get('ifOperStatus', 'u')).lower(), str(
                l_if.get('ifAdminStatus', 'u')).lower()

            s_fill_l_val = styles.port_unknown_fill.split('=')[-1]
            if aS_disp_l == "down":
                s_fill_l_val = styles.port_shutdown_fill.split('=')[-1]
            elif s_disp_l == "up":
                s_fill_l_val = styles.port_up_fill.split('=')[-1]
            elif s_disp_l in ["down", "lowerlayerdown"]:
                s_fill_l_val = styles.port_down_fill.split('=')[-1]

            if_type = str(l_if.get('_ifType_iana_debug', '')).strip()
            type_info = f" (Typ: {if_type})" if if_type else ""
            log_ifs_html += f"<font color='{s_fill_l_val}'>•</font>&nbsp;{name_l}{type_info}&nbsp;({s_disp_l})<br/>"
    else:
        log_ifs_html += "<div style='padding-left:7px;'>(brak)</div>"
    log_ifs_html += "</div>"

    full_dev_lbl_html = f"{base_dev_lbl_html}<hr size='1' style='margin:2px 0;'/>{phys_ports_html}<hr size='1' style='margin:2px 0;'/>{log_ifs_html}"

    info_label_width = min(max(chassis_width * 0.65, info_label_min_w_cfg), info_label_max_w_cfg)

    num_base_lines = 3 + len(display_extra) + (2 if prepared_data.ports_display_limited else 0)
    base_h = num_base_lines * (label_line_height_cfg + 3) + 10
    phys_h = min(physical_port_list_max_h_cfg,
                 max(20, len(prepared_data.all_physical_ports) * (label_line_height_cfg + 3))) + 25
    log_h = min(logical_if_list_max_h_cfg,
                max(20, len(prepared_data.logical_interfaces) * (label_line_height_cfg + 3))) + 25
    info_lbl_h = base_h + phys_h + log_h + 20

    info_lbl_abs_x, info_lbl_abs_y = offset_x - info_label_width - info_label_margin_cfg, \
                                     offset_y + (chassis_height / 2) - (info_lbl_h / 2)

    grid_margin_y_cfg = config.get('grid_margin_y')
    info_lbl_abs_y = max((grid_margin_y_cfg / 3), info_lbl_abs_y)

    dev_info_lbl_cell = drawio_utils.create_vertex_cell(info_lbl_id, "1", full_dev_lbl_html, info_lbl_abs_x,
                                                        info_lbl_abs_y, info_label_width, info_lbl_h, styles.info_label,
                                                        connectable="0")
    global_root_cell.append(dev_info_lbl_cell)

    logger.info(f"✓ DrawIO: Urządzenie {current_host_identifier} dynamicznie przetworzone i dodane.")
    return port_map_for_device