# --- svg_generator.py ---
import xml.etree.ElementTree as ET
import re
import math
import logging
from typing import List, Dict, Tuple, Optional, Any, NamedTuple

from librenms_client import LibreNMSAPI
from utils import get_canonical_identifier

import common_device_logic
from common_device_logic import PortEndpointData, DeviceDisplayData
from common_device_logic import (
    PORT_WIDTH, PORT_HEIGHT, HORIZONTAL_SPACING, ROW_OFFSET_Y, VERTICAL_SPACING,
    WAYPOINT_OFFSET, LABEL_LINE_HEIGHT, LABEL_PADDING,
    PHYSICAL_PORT_LIST_MAX_HEIGHT, LOGICAL_IF_LIST_MAX_HEIGHT
)
from drawio_device_builder import StyleInfo as DrawioStyleInfoRef

logger = logging.getLogger(__name__)

SVG_FILL_MAP = {"#ffffff": "white", "#dae8fc": "#dae8fc", "#E6E6E6": "#E6E6E6", "#D5E8D4": "#D5E8D4",
                "#F8CECC": "#F8CECC", "#FFE6CC": "#FFE6CC", "#E1D5E7": "#E1D5E7", "#f8f8f8": "#f8f8f8",
                "none": "none", }
SVG_STROKE_MAP = {"#000000": "black", "#6c8ebf": "#6c8ebf", "#666666": "#666666", "#82B366": "#82B366",
                  "#B85450": "#B85450", "#D79B00": "#D79B00", "#9673A6": "#9673A6", "#AAAAAA": "grey",
                  "#FF9900": "orange", "#bababa": "#bababa", "#c3c3c3": "#c3c3c3", "none": "none", }
SVG_DEFAULT_TEXT_COLOR = "black";
SVG_PORT_LABEL_FONT_SIZE = "8px";
SVG_ALIAS_FONT_SIZE = "7.5px";
SVG_INFO_TITLE_FONT_SIZE = "8.5px";
SVG_INFO_TEXT_FONT_SIZE = "8px";
SVG_CONNECTION_LABEL_FONT_SIZE = "7.5px";
SVG_INFO_HR_COLOR = "#D0D0D0"
SVG_PORT_ALIAS_LINE_EXTENSION = 25.0;
SVG_PORT_ALIAS_LABEL_OFFSET_FROM_LINE = 2.0;
SVG_PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER = 3.0
SVG_INFO_LABEL_MARGIN_FROM_CHASSIS = 25.0;
SVG_INFO_LABEL_MIN_WIDTH = 180.0;
SVG_INFO_LABEL_PADDING = "5px"


def _parse_drawio_style_string_for_svg(style_string: str, default_fill: str = "white", default_stroke: str = "black",
                                       default_stroke_width: str = "1") -> Dict[str, str]:
    attrs = {"fill": default_fill, "stroke": default_stroke, "stroke-width": default_stroke_width};
    style_dict: Dict[str, str] = {}
    if not style_string: return attrs
    for part in style_string.split(';'):
        if '=' in part: key, value = part.split('=', 1); style_dict[key.strip().lower()] = value.strip()
    fill_key = style_dict.get("fillcolor", "");
    attrs["fill"] = SVG_FILL_MAP.get(fill_key, fill_key if fill_key else default_fill)
    stroke_key = style_dict.get("strokecolor", "");
    attrs["stroke"] = SVG_STROKE_MAP.get(stroke_key, stroke_key if stroke_key else default_stroke)
    attrs["stroke-width"] = style_dict.get("strokewidth", default_stroke_width)
    if style_dict.get("rounded") == "1": attrs["rx"] = style_dict.get("arcsize", "8"); attrs["ry"] = style_dict.get(
        "arcsize", "8")
    if style_dict.get("dashed") == "1": attrs["stroke-dasharray"] = style_dict.get("dashpattern", "3 3").replace(" ",
                                                                                                                 ",")
    return attrs


class SVGDiagram:
    def __init__(self, width: float = 2000, height: float = 1500):
        self.width = width;
        self.height = height
        self.svg_root = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg",
                                           "xmlns:xhtml": "http://www.w3.org/1999/xhtml", "version": "1.1",
                                           "width": str(self.width), "height": str(self.height),
                                           "viewBox": f"0 0 {self.width} {self.height}"})
        ET.SubElement(self.svg_root, "rect", {"x": "0", "y": "0", "width": "100%", "height": "100%", "fill": "white"})
        defs = ET.SubElement(self.svg_root, "defs");
        style_el = ET.SubElement(defs, "style", {"type": "text/css"})
        default_font_family = "Arial, Helvetica, sans-serif"
        style_el.text = f"""svg {{ font-family: {default_font_family}; }}
            .port-label {{ font-size: {SVG_PORT_LABEL_FONT_SIZE}; text-anchor: middle; dominant-baseline: central; fill: {SVG_DEFAULT_TEXT_COLOR}; }}
            .alias-label-rotated {{ font-size: {SVG_ALIAS_FONT_SIZE}; fill: {SVG_DEFAULT_TEXT_COLOR}; writing-mode: tb; glyph-orientation-vertical: 0; }}
            .alias-label-horizontal {{ font-size: {SVG_ALIAS_FONT_SIZE}; fill: {SVG_DEFAULT_TEXT_COLOR}; text-anchor: start; dominant-baseline: middle; }}
            .info-label-foreign-object div {{ font-family: {default_font_family}; font-size: {SVG_INFO_TEXT_FONT_SIZE}; line-height: {LABEL_LINE_HEIGHT + 2}px; color: {SVG_DEFAULT_TEXT_COLOR}; padding: {SVG_INFO_LABEL_PADDING}; border-radius: 6px; box-sizing: border-box; }}
            .info-label-foreign-object b {{ font-size: {SVG_INFO_TITLE_FONT_SIZE}; font-weight: bold; }}
            .info-label-foreign-object i {{ font-style: italic; color: #555; }} 
            .ports-limit-note {{ font-size: 7px; color: #DD7700; font-style: italic; }}
            .info-label-foreign-object hr {{ border: 0; border-top: 0.5px solid {SVG_INFO_HR_COLOR}; margin: 3px 0; }}
            .status-dot {{ font-size: 10px; vertical-align: middle; }}
            .connection-label {{ font-size: {SVG_CONNECTION_LABEL_FONT_SIZE}; fill: {SVG_DEFAULT_TEXT_COLOR}; text-anchor: middle; paint-order: stroke; stroke: white; stroke-width: 2.5px; stroke-opacity:0.85;}}"""
        logger.debug("SVGDiagram zainicjalizowany.")

    def update_dimensions(self, width: float, height: float):
        self.width = width;
        self.height = height;
        self.svg_root.set("width", str(self.width));
        self.svg_root.set("height", str(self.height));
        self.svg_root.set("viewBox", f"0 0 {self.width} {self.height}")
        bg_rect = self.svg_root.find("rect[@fill='white']");
        if bg_rect is not None: bg_rect.set("width", str(self.width)); bg_rect.set("height", str(self.height))
        logger.info(f"Zaktualizowano wymiary SVG na: {self.width:.0f}x{self.height:.0f}")

    def add_element(self, element: ET.Element):
        self.svg_root.append(element)

    def get_svg_string(self) -> str:
        try:
            if hasattr(ET, 'indent'): ET.indent(self.svg_root, space="  ")
        except AttributeError:
            pass
        return ET.tostring(self.svg_root, encoding="unicode", method="xml")


def svg_add_device_to_diagram(
        svg_diagram: SVGDiagram, device_api_info: Dict[str, Any], api_client: LibreNMSAPI,
        position: Tuple[float, float], device_internal_idx: int, drawio_styles_ref: DrawioStyleInfoRef
) -> Optional[Dict[Any, PortEndpointData]]:
    port_map_for_device_svg: Dict[Any, PortEndpointData] = {}
    offset_x, offset_y = position
    try:
        prepared_data: DeviceDisplayData = common_device_logic.prepare_device_display_data(device_api_info, api_client,
                                                                                           device_internal_idx)
    except Exception as e:
        logger.error(f"SVG: Krytyczny błąd przygotowania danych dla '{device_api_info.get('hostname')}': {e}. Pomijam.",
                     exc_info=True);
        return None
    current_host_identifier = prepared_data.canonical_identifier
    logger.info(
        f"SVG: Dodawanie urządzenia: {current_host_identifier} (idx: {device_internal_idx}) na ({offset_x:.0f}, {offset_y:.0f})")
    chassis_width, chassis_height = prepared_data.chassis_layout.width, prepared_data.chassis_layout.height
    device_group_main_svg = ET.Element("g", {"id": f"device_main_svg_{device_internal_idx}",
                                             "transform": f"translate({offset_x:.2f},{offset_y:.2f})"})
    chassis_svg_attrs = _parse_drawio_style_string_for_svg(drawio_styles_ref.chassis);
    chassis_rect_svg = ET.Element("rect",
                                  {"x": "0", "y": "0", "width": str(chassis_width), "height": str(chassis_height),
                                   **chassis_svg_attrs});
    device_group_main_svg.append(chassis_rect_svg)
    ports_to_draw = prepared_data.physical_ports_for_chassis_layout
    num_layout_rows, ports_per_row_config = prepared_data.chassis_layout.num_rows, prepared_data.chassis_layout.ports_per_row
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
        cur_row_w = num_ports_row * PORT_WIDTH + max(0, num_ports_row - 1) * HORIZONTAL_SPACING;
        row_start_x = (chassis_width - cur_row_w) / 2
        for col_idx in range(num_ports_row):
            if cur_port_idx >= len(ports_to_draw): break
            p_info = ports_to_draw[cur_port_idx];
            vis_num_str = str(cur_port_idx + 1)
            px, py = row_start_x + col_idx * (PORT_WIDTH + HORIZONTAL_SPACING), ROW_OFFSET_Y + row_idx * (
                        PORT_HEIGHT + VERTICAL_SPACING)
            p_ifidx, p_id_api = p_info.get("ifIndex"), p_info.get("port_id")
            p_svg_base_id = f"p{p_ifidx if p_ifidx is not None else p_id_api if p_id_api is not None else f'vis{vis_num_str}'}"
            p_svg_shape_id = f"svgshape_port_{device_internal_idx}_{p_svg_base_id}"
            status, admin_status = str(p_info.get("ifOperStatus", "u")).lower(), str(
                p_info.get("ifAdminStatus", "u")).lower()
            fill_hex, stroke_hex = drawio_styles_ref.port_unknown_fill, drawio_styles_ref.port_unknown_stroke
            if admin_status == "down":
                fill_hex, stroke_hex = drawio_styles_ref.port_shutdown_fill, drawio_styles_ref.port_shutdown_stroke
            elif status == "up":
                fill_hex, stroke_hex = drawio_styles_ref.port_up_fill, drawio_styles_ref.port_up_stroke
            elif status in ["down", "lowerlayerdown"]:
                fill_hex, stroke_hex = drawio_styles_ref.port_down_fill, drawio_styles_ref.port_down_stroke
            p_svg_attrs = {"fill": SVG_FILL_MAP.get(fill_hex, fill_hex),
                           "stroke": SVG_STROKE_MAP.get(stroke_hex, stroke_hex), "stroke-width": "1"}
            if "rounded=0" not in drawio_styles_ref.port: p_svg_attrs["rx"] = "3"; p_svg_attrs["ry"] = "3"
            p_rect_svg = ET.Element("rect",
                                    {"id": p_svg_shape_id, "x": f"{px:.2f}", "y": f"{py:.2f}", "width": str(PORT_WIDTH),
                                     "height": str(PORT_HEIGHT), **p_svg_attrs});
            device_group_main_svg.append(p_rect_svg)
            p_text_svg = ET.Element("text", {"x": f"{px + PORT_WIDTH / 2:.2f}", "y": f"{py + PORT_HEIGHT / 2:.2f}",
                                             "class": "port-label"});
            p_text_svg.text = vis_num_str;
            device_group_main_svg.append(p_text_svg)
            center_x_p_rel = px + PORT_WIDTH / 2;
            conn_orient: str;
            conn_epy_rel: float
            if row_idx % 2 == 0:
                conn_epy_rel, conn_orient = py - WAYPOINT_OFFSET, "up"
            else:
                conn_epy_rel, conn_orient = py + PORT_HEIGHT + WAYPOINT_OFFSET, "down"
            ep_abs_x, ep_abs_y, ep_id = offset_x + center_x_p_rel, offset_y + conn_epy_rel, f"ep_svg_{device_internal_idx}_{p_svg_base_id}"
            ep_data = PortEndpointData(ep_id, ep_abs_x, ep_abs_y, conn_orient)
            p_name_api = p_info.get('ifName')
            if p_ifidx is not None: port_map_for_device_svg[f"ifindex_{p_ifidx}"] = ep_data
            if p_id_api is not None: port_map_for_device_svg[f"portid_{p_id_api}"] = ep_data
            if p_name_api: port_map_for_device_svg[p_name_api.lower()] = ep_data
            port_map_for_device_svg[vis_num_str] = ep_data
            alias_txt = str(p_info.get("ifAlias", "")).strip()
            if alias_txt:
                aux_sx, aux_ex = offset_x + center_x_p_rel, offset_x + center_x_p_rel;
                lbl_x, lbl_y, txt_anc, trans = 0.0, 0.0, "middle", ""
                if conn_orient == "up":
                    aux_sy, aux_ey = offset_y, offset_y - SVG_PORT_ALIAS_LINE_EXTENSION; lbl_x, lbl_y = aux_ex + SVG_PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER, aux_ey - SVG_PORT_ALIAS_LABEL_OFFSET_FROM_LINE; txt_anc, trans = "end", f"rotate(-90 {lbl_x:.2f} {lbl_y:.2f})"
                else:
                    aux_sy, aux_ey = offset_y + chassis_height, offset_y + chassis_height + SVG_PORT_ALIAS_LINE_EXTENSION; lbl_x, lbl_y = aux_ex + SVG_PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER, aux_ey + SVG_PORT_ALIAS_LABEL_OFFSET_FROM_LINE; txt_anc, trans = "start", f"rotate(-90 {lbl_x:.2f} {lbl_y:.2f})"
                aux_attrs = _parse_drawio_style_string_for_svg(drawio_styles_ref.aux_line);
                aux_line = ET.Element("line", {"x1": f"{aux_sx:.2f}", "y1": f"{aux_sy:.2f}", "x2": f"{aux_ex:.2f}",
                                               "y2": f"{aux_ey:.2f}", **aux_attrs});
                svg_diagram.add_element(aux_line)
                alias_lbl = ET.Element("text",
                                       {"x": f"{lbl_x:.2f}", "y": f"{lbl_y:.2f}", "class": "alias-label-rotated",
                                        "text-anchor": txt_anc, "transform": trans})
                disp_alias = alias_txt.split('\n')[0];
                alias_lbl.text = disp_alias[:18] + ".." if len(disp_alias) > 20 else disp_alias;
                svg_diagram.add_element(alias_lbl)
            cur_port_idx += 1
        if cur_port_idx >= len(ports_to_draw): break

    mgmt0_info = prepared_data.mgmt0_port_info
    if mgmt0_info:
        logger.debug(f"  SVG: Dodawanie portu mgmt0 dla {current_host_identifier}...")
        mgmt0_x, mgmt0_y = chassis_width + HORIZONTAL_SPACING, chassis_height / 2 - PORT_HEIGHT / 2
        mgmt0_ifidx, mgmt0_pid = mgmt0_info.get('ifIndex'), mgmt0_info.get('port_id')
        mgmt0_base_id = f"mgmt0_{mgmt0_ifidx if mgmt0_ifidx is not None else mgmt0_pid if mgmt0_pid is not None else 'na'}"
        mgmt0_shape_id, mgmt0_ep_id = f"svgshape_mgmt0_{device_internal_idx}_{mgmt0_base_id}", f"ep_svg_mgmt0_{device_internal_idx}_{mgmt0_base_id}"
        status_m, admin_status_m = str(mgmt0_info.get("ifOperStatus", "u")).lower(), str(
            mgmt0_info.get("ifAdminStatus", "u")).lower()
        fill_m_hex, stroke_m_hex = drawio_styles_ref.port_unknown_fill, drawio_styles_ref.port_unknown_stroke
        if admin_status_m == "down":
            fill_m_hex, stroke_m_hex = drawio_styles_ref.port_shutdown_fill, drawio_styles_ref.port_shutdown_stroke
        elif status_m == "up":
            fill_m_hex, stroke_m_hex = drawio_styles_ref.port_up_fill, drawio_styles_ref.port_up_stroke
        elif status_m in ["down", "lowerlayerdown"]:
            fill_m_hex, stroke_m_hex = drawio_styles_ref.port_down_fill, drawio_styles_ref.port_down_stroke
        mgmt0_attrs = {"fill": SVG_FILL_MAP.get(fill_m_hex, fill_m_hex),
                       "stroke": SVG_STROKE_MAP.get(stroke_m_hex, stroke_m_hex), "stroke-width": "1"}
        mgmt0_rect = ET.Element("rect", {"id": mgmt0_shape_id, "x": f"{mgmt0_x:.2f}", "y": f"{mgmt0_y:.2f}",
                                         "width": str(PORT_WIDTH), "height": str(PORT_HEIGHT), **mgmt0_attrs});
        device_group_main_svg.append(mgmt0_rect)
        mgmt0_text = ET.Element("text",
                                {"x": f"{mgmt0_x + PORT_WIDTH / 2:.2f}", "y": f"{mgmt0_y + PORT_HEIGHT / 2:.2f}",
                                 "class": "port-label"});
        mgmt0_text.text = "M";
        device_group_main_svg.append(mgmt0_text)
        ep_abs_x_m, ep_abs_y_m = offset_x + mgmt0_x + PORT_WIDTH + WAYPOINT_OFFSET, offset_y + mgmt0_y + PORT_HEIGHT / 2
        ep_data_m = PortEndpointData(mgmt0_ep_id, ep_abs_x_m, ep_abs_y_m, "right")
        mgmt0_name_api = mgmt0_info.get('ifName')
        if mgmt0_ifidx is not None: port_map_for_device_svg[f"ifindex_{mgmt0_ifidx}"] = ep_data_m
        if mgmt0_pid is not None: port_map_for_device_svg[f"portid_{mgmt0_pid}"] = ep_data_m
        if mgmt0_name_api: port_map_for_device_svg[mgmt0_name_api.lower()] = ep_data_m
        port_map_for_device_svg["mgmt0"] = ep_data_m
        alias_txt_m = str(mgmt0_info.get("ifAlias", "")).strip()
        if alias_txt_m:
            aux_sx_m, aux_sy_m = offset_x + mgmt0_x + PORT_WIDTH, offset_y + mgmt0_y + PORT_HEIGHT / 2;
            aux_ex_m, aux_ey_m = aux_sx_m + SVG_PORT_ALIAS_LINE_EXTENSION, aux_sy_m
            aux_attrs_m = _parse_drawio_style_string_for_svg(drawio_styles_ref.aux_line);
            mgmt0_aux_line = ET.Element("line",
                                        {"x1": f"{aux_sx_m:.2f}", "y1": f"{aux_sy_m:.2f}", "x2": f"{aux_ex_m:.2f}",
                                         "y2": f"{aux_ey_m:.2f}", **aux_attrs_m});
            svg_diagram.add_element(mgmt0_aux_line)
            lbl_x_m, lbl_y_m = aux_ex_m + SVG_PORT_ALIAS_LABEL_OFFSET_FROM_LINE, aux_ey_m
            mgmt0_alias_lbl = ET.Element("text", {"x": f"{lbl_x_m:.2f}", "y": f"{lbl_y_m:.2f}",
                                                  "class": "alias-label-horizontal"});
            mgmt0_alias_lbl.text = alias_txt_m;
            svg_diagram.add_element(mgmt0_alias_lbl)
    svg_diagram.add_element(device_group_main_svg)

    dev_api, dev_id_val = prepared_data.device_api_info, prepared_data.device_api_info.get('device_id', 'N/A')
    hostname_raw, ip_raw, purpose_raw = dev_api.get('hostname', ''), dev_api.get('ip', ''), dev_api.get('purpose', '')
    display_name_main = prepared_data.canonical_identifier
    if prepared_data.is_stack: display_name_main += " (STACK)"
    ports_limit_info_text_svg = ""
    if prepared_data.ports_display_limited:
        ports_limit_info_text_svg = (f"(Wyświetlanie portów na chassis ograniczone do "
                                     f"{len(prepared_data.physical_ports_for_chassis_layout)} "
                                     f"z {prepared_data.total_physical_ports_before_limit} kandydatów.)")
    extra_info_svg = [];
    hostname_s, purpose_s = str(hostname_raw).strip(), str(purpose_raw).strip()
    main_name_no_stack_svg = display_name_main.replace(" (STACK)", "")
    if hostname_s and hostname_s != main_name_no_stack_svg and not re.match(r'^\d{1,3}(\.\d{1,3}){3}$',
                                                                            hostname_s): extra_info_svg.append(
        f"Host: {hostname_s}")
    if purpose_s and purpose_s != main_name_no_stack_svg: extra_info_svg.append(f"Cel: {purpose_s}")
    temp_display_ip_svg = str(ip_raw).strip() if ip_raw and str(ip_raw).strip() else 'N/A'
    if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', hostname_s) and not (
            ip_raw and str(ip_raw).strip()): temp_display_ip_svg = hostname_s
    xhtml_ns = "http://www.w3.org/1999/xhtml";
    xhtml_div = ET.Element(f"{{{xhtml_ns}}}div", {"class": "info-label-foreign-object"})
    border_hex_svg, bg_hex_svg = "#c3c3c3", "#f8f8f8"
    try:
        styles_parsed_svg = {p.split('=')[0].lower(): p.split('=')[1] for p in drawio_styles_ref.info_label.split(';')
                             if '=' in p}; border_hex_svg, bg_hex_svg = styles_parsed_svg.get('strokecolor',
                                                                                              border_hex_svg), styles_parsed_svg.get(
            'fillcolor', bg_hex_svg)
    except:
        logger.warning("SVG: Błąd parsowania stylów info_label.")
    xhtml_div.set("style",
                  f"border: 1px solid {SVG_STROKE_MAP.get(border_hex_svg, border_hex_svg)}; background-color: {SVG_FILL_MAP.get(bg_hex_svg, bg_hex_svg)};")

    def add_text_node_xhtml(parent, text, tag="span", is_bold=False, class_name=None):
        el_tag = "b" if is_bold else tag;
        el = ET.SubElement(parent, f"{{{xhtml_ns}}}{el_tag}");
        el.text = text
        if class_name: el.set("class", class_name);
        return el

    add_text_node_xhtml(xhtml_div, display_name_main, is_bold=True);
    if prepared_data.ports_display_limited:
        ET.SubElement(xhtml_div, f"{{{xhtml_ns}}}br")
        add_text_node_xhtml(xhtml_div, ports_limit_info_text_svg, tag="small", class_name="ports-limit-note")
    ET.SubElement(xhtml_div, f"{{{xhtml_ns}}}br");
    add_text_node_xhtml(xhtml_div, f"ID: {dev_id_val}");
    if extra_info_svg: ET.SubElement(xhtml_div, f"{{{xhtml_ns}}}br"); add_text_node_xhtml(xhtml_div,
                                                                                          "; ".join(extra_info_svg))
    ET.SubElement(xhtml_div, f"{{{xhtml_ns}}}br");
    add_text_node_xhtml(xhtml_div, f"IP: {temp_display_ip_svg}");
    ET.SubElement(xhtml_div, f"{{{xhtml_ns}}}hr")
    add_text_node_xhtml(xhtml_div, f"Porty Fizyczne ({len(prepared_data.all_physical_ports)}):", is_bold=True)
    phys_div = ET.SubElement(xhtml_div, f"{{{xhtml_ns}}}div", {
        "style": f"max-height:{PHYSICAL_PORT_LIST_MAX_HEIGHT}px; overflow-y:auto; overflow-x:hidden;"})
    if prepared_data.all_physical_ports:
        for p_port_info in prepared_data.all_physical_ports:  # Zmieniono nazwę zmiennej p na p_port_info
            line_div = ET.SubElement(phys_div, f"{{{xhtml_ns}}}div");
            dot_span = ET.SubElement(line_div, f"{{{xhtml_ns}}}span", {"class": "status-dot"})
            s_op = str(p_port_info.get("ifOperStatus", "u")).lower()  # Zmieniono s na s_op
            aS_adm = str(p_port_info.get("ifAdminStatus", "u")).lower()  # Zmieniono aS na aS_adm
            s_color_hex = drawio_styles_ref.port_unknown_fill  # Zmieniono s_color na s_color_hex
            if aS_adm == "down":
                s_color_hex = drawio_styles_ref.port_shutdown_fill
            elif s_op == "up":
                s_color_hex = drawio_styles_ref.port_up_fill
            elif s_op in ["down", "lowerlayerdown"]:
                s_color_hex = drawio_styles_ref.port_down_fill
            dot_span.set("style", f"color:{SVG_FILL_MAP.get(s_color_hex, s_color_hex)};");
            dot_span.text = "• "
            nm, dsc, als = str(p_port_info.get('ifName', 'N/A')).strip(), str(
                p_port_info.get('ifDescr', '')).strip(), str(p_port_info.get('ifAlias', '')).strip();
            e_inf_p = []
            if als: e_inf_p.append(f"Alias: {als}")
            if dsc and dsc != nm and dsc != als: e_inf_p.append(f"Opis: {dsc}")
            e_inf_s = f" ({'; '.join(e_inf_p)})" if e_inf_p else "";
            add_text_node_xhtml(line_div, f"{nm}{e_inf_s} ({s_op})", tag="span")
    else:
        add_text_node_xhtml(phys_div, "(brak)")
    ET.SubElement(xhtml_div, f"{{{xhtml_ns}}}hr")
    add_text_node_xhtml(xhtml_div, f"Inne Interfejsy ({len(prepared_data.logical_interfaces)}):", is_bold=True)
    log_div = ET.SubElement(xhtml_div, f"{{{xhtml_ns}}}div", {
        "style": f"max-height:{LOGICAL_IF_LIST_MAX_HEIGHT}px; overflow-y:auto; overflow-x:hidden;"})
    if prepared_data.logical_interfaces:
        for l_if in prepared_data.logical_interfaces:
            line_div_l = ET.SubElement(log_div, f"{{{xhtml_ns}}}div");
            dot_span_l = ET.SubElement(line_div_l, f"{{{xhtml_ns}}}span", {"class": "status-dot"})
            s_l, aS_l = str(l_if.get('ifOperStatus', 'u')).lower(), str(l_if.get('ifAdminStatus', 'u')).lower();
            s_color_l_hex = drawio_styles_ref.port_unknown_fill  # Zmieniono s_color_l
            if aS_l == "down":
                s_color_l_hex = drawio_styles_ref.port_shutdown_fill
            elif s_l == "up":
                s_color_l_hex = drawio_styles_ref.port_up_fill
            elif s_l in ["down", "lowerlayerdown"]:
                s_color_l_hex = drawio_styles_ref.port_down_fill
            dot_span_l.set("style", f"color:{SVG_FILL_MAP.get(s_color_l_hex, s_color_l_hex)};");
            dot_span_l.text = "• "
            nm_l = str(l_if.get('ifName') or l_if.get('ifDescr', 'N/A')).strip();
            typ_str = str(l_if.get('_ifType_iana_debug', '')).strip();
            typ_inf = f" (Typ: {typ_str})" if typ_str else ""
            add_text_node_xhtml(line_div_l, f"{nm_l}{typ_inf} ({s_l})", tag="span")
    else:
        add_text_node_xhtml(log_div, "(brak)")

    info_w = max(chassis_width * 0.7, SVG_INFO_LABEL_MIN_WIDTH)
    num_base_lines_info = 3 + len(extra_info_svg) + (1 if prepared_data.ports_display_limited else 0)
    base_h_info = num_base_lines_info * (LABEL_LINE_HEIGHT + 4) + 15
    phys_ports_section_h = min(PHYSICAL_PORT_LIST_MAX_HEIGHT,
                               max(25, len(prepared_data.all_physical_ports) * (LABEL_LINE_HEIGHT + 3))) + 30
    logical_ifs_section_h = min(LOGICAL_IF_LIST_MAX_HEIGHT,
                                max(25, len(prepared_data.logical_interfaces) * (LABEL_LINE_HEIGHT + 3))) + 30
    info_lbl_h = base_h_info + phys_ports_section_h + logical_ifs_section_h + 25
    info_lbl_abs_x, info_lbl_abs_y = offset_x - info_w - SVG_INFO_LABEL_MARGIN_FROM_CHASSIS, offset_y + (
                chassis_height / 2) - (info_lbl_h / 2)
    info_lbl_abs_y = max((SVG_INFO_LABEL_MARGIN_FROM_CHASSIS / 2), info_lbl_abs_y)
    f_obj = ET.Element("foreignObject",
                       {"x": f"{info_lbl_abs_x:.2f}", "y": f"{info_lbl_abs_y:.2f}", "width": str(info_w),
                        "height": str(info_lbl_h)});
    f_obj.append(xhtml_div);
    svg_diagram.add_element(f_obj)
    logger.info(f"✓ SVG: Urządzenie {current_host_identifier} przetworzone i dodane.")
    return port_map_for_device_svg


def svg_draw_connection(svg_diagram: SVGDiagram, src_ep: PortEndpointData, tgt_ep: PortEndpointData,
                        vlan_id: Optional[str], conn_idx: int):
    line_id, lbl_id = f"conn_line_svg_{conn_idx}", f"conn_label_svg_{conn_idx}"
    x1, y1, o1 = src_ep.x, src_ep.y, src_ep.orientation
    x2, y2, o2 = tgt_ep.x, tgt_ep.y, tgt_ep.orientation

    path_data = f"M {x1:.2f} {y1:.2f} "
    wp1x, wp1y = x1, y1
    wp2x, wp2y = x2, y2

    # Poprawiona logika - usunięto znaki ~
    if o1 == "up":
        wp1y -= WAYPOINT_OFFSET
    elif o1 == "down":
        wp1y += WAYPOINT_OFFSET
    elif o1 == "left":
        wp1x -= WAYPOINT_OFFSET
    elif o1 == "right":
        wp1x += WAYPOINT_OFFSET

    path_data += f"L {wp1x:.2f} {wp1y:.2f} "

    if o2 == "up":
        wp2y -= WAYPOINT_OFFSET
    elif o2 == "down":
        wp2y += WAYPOINT_OFFSET
    elif o2 == "left":
        wp2x -= WAYPOINT_OFFSET
    elif o2 == "right":
        wp2x += WAYPOINT_OFFSET

    # Poprawiona logika rysowania ścieżki
    if (o1 in ["up", "down"] and o2 in ["up", "down"]):  # Osie równoległe, oba pionowe
        mid_y = (wp1y + wp2y) / 2
        path_data += f"L {wp1x:.2f} {mid_y:.2f} "
        path_data += f"L {wp2x:.2f} {mid_y:.2f} "
    elif (o1 in ["left", "right"] and o2 in ["left", "right"]):  # Osie równoległe, oba poziome
        mid_x = (wp1x + wp2x) / 2
        path_data += f"L {mid_x:.2f} {wp1y:.2f} "
        path_data += f"L {mid_x:.2f} {wp2y:.2f} "
    else:  # Osie prostopadłe (L-kształt)
        if o1 in ["up", "down"]:  # Start pionowy, koniec poziomy
            path_data += f"L {wp1x:.2f} {wp2y:.2f} "
        else:  # Start poziomy, koniec pionowy (o1 in ["left", "right"])
            path_data += f"L {wp2x:.2f} {wp1y:.2f} "

    path_data += f"L {wp2x:.2f} {wp2y:.2f} L {x2:.2f} {y2:.2f}"
    conn_path = ET.Element("path", {"id": line_id, "d": path_data, "stroke": SVG_STROKE_MAP.get("#FF9900", "orange"),
                                    "stroke-width": "1.5", "fill": "none"});
    svg_diagram.add_element(conn_path)
    if vlan_id:
        lbl_x, lbl_y = (wp1x + wp2x) / 2, (wp1y + wp2y) / 2;
        txt_anc = "middle"
        delta_x = abs(wp1x - wp2x);
        delta_y = abs(wp1y - wp2y)
        if delta_x < 10 and delta_y > 0:  # Bardziej pionowa
            lbl_x += 5
            txt_anc = "start" if wp1x <= wp2x else "end"
        elif delta_y < 10 and delta_x > 0:  # Bardziej pozioma
            lbl_y -= 3
        vlan_txt = ET.Element("text",
                              {"id": lbl_id, "x": f"{lbl_x:.2f}", "y": f"{lbl_y:.2f}", "class": "connection-label",
                               "dominant-baseline": "middle", "text-anchor": txt_anc});
        vlan_txt.text = f"VLAN {vlan_id}";
        svg_diagram.add_element(vlan_txt)