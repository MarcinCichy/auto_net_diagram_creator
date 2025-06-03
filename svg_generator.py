# --- svg_generator.py ---
import xml.etree.ElementTree as ET
import re
import math
import logging
from typing import List, Dict, Tuple, Optional, Any

from librenms_client import LibreNMSAPI
from utils import get_canonical_identifier, normalize_interface_name  # Zmieniony import

import common_device_logic
from common_device_logic import PortEndpointData, DeviceDisplayData
from drawio_device_builder import StyleInfo as DrawioStyleInfoRef

logger = logging.getLogger(__name__)

SVG_FILL_MAP = {"#ffffff": "white", "#dae8fc": "#dae8fc", "#E6E6E6": "#E6E6E6", "#D5E8D4": "#D5E8D4",
                "#F8CECC": "#F8CECC", "#FFE6CC": "#FFE6CC", "#E1D5E7": "#E1D5E7", "#f8f8f8": "#f8f8f8",
                "none": "none", }
SVG_STROKE_MAP = {"#000000": "black", "#6c8ebf": "#6c8ebf", "#666666": "#666666", "#82B366": "#82B366",
                  "#B85450": "#B85450", "#D79B00": "#D79B00", "#9673A6": "#9673A6", "#AAAAAA": "grey",
                  "#FF9900": "orange", "#bababa": "#bababa", "#c3c3c3": "#c3c3c3", "none": "none", }


def _parse_drawio_style_string_for_svg(style_string: str, default_fill: str = "white", default_stroke: str = "black",
                                       default_stroke_width: str = "1") -> Dict[str, str]:
    attrs = {"fill": default_fill, "stroke": default_stroke, "stroke-width": default_stroke_width}
    style_dict: Dict[str, str] = {}
    if not style_string: return attrs
    for part in style_string.split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            style_dict[key.strip().lower()] = value.strip()

    fill_key = style_dict.get("fillcolor", "")
    attrs["fill"] = SVG_FILL_MAP.get(fill_key, str(fill_key) if fill_key is not None else default_fill)
    stroke_key = style_dict.get("strokecolor", "")
    attrs["stroke"] = SVG_STROKE_MAP.get(stroke_key, str(stroke_key) if stroke_key is not None else default_stroke)
    attrs["stroke-width"] = str(style_dict.get("strokewidth", default_stroke_width))
    if style_dict.get("rounded") == "1":
        attrs["rx"] = str(style_dict.get("arcsize", "8"))
        attrs["ry"] = str(style_dict.get("arcsize", "8"))
    if style_dict.get("dashed") == "1":
        attrs["stroke-dasharray"] = str(style_dict.get("dashpattern", "3 3")).replace(" ", ",")
    return attrs


class SVGDiagram:
    def __init__(self, width: float = 2000, height: float = 1500, config: Optional[Dict[str, Any]] = None):
        self.width = width
        self.height = height
        self.config = config if config is not None else {}

        self.svg_root = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg",
                                           "xmlns:xhtml": "http://www.w3.org/1999/xhtml", "version": "1.1",
                                           "width": str(self.width), "height": str(self.height),
                                           "viewBox": f"0 0 {self.width} {self.height}"})
        ET.SubElement(self.svg_root, "rect", {"x": "0", "y": "0", "width": "100%", "height": "100%", "fill": "white"})
        defs = ET.SubElement(self.svg_root, "defs")
        style_el = ET.SubElement(defs, "style", {"type": "text/css"})

        default_font_family = self.config.get('svg_default_font_family', "Arial, Helvetica, sans-serif")
        svg_default_text_color = self.config.get('svg_default_text_color', "black")
        svg_port_label_font_size = self.config.get('svg_port_label_font_size', "8px")
        svg_alias_font_size = self.config.get('svg_alias_font_size', "7.5px")
        svg_info_title_font_size = self.config.get('svg_info_title_font_size', "8.5px")
        svg_info_text_font_size = self.config.get('svg_info_text_font_size', "8px")
        svg_connection_label_font_size = self.config.get('svg_connection_label_font_size', "7.5px")
        svg_info_hr_color = self.config.get('svg_info_hr_color', "#D0D0D0")
        svg_info_label_padding_cfg = self.config.get('svg_info_label_padding', "5px")
        label_line_height_cfg = self.config.get('label_line_height', 10.0)

        style_el.text = f"""svg {{ font-family: {default_font_family}; }}
            .port-label {{ font-size: {svg_port_label_font_size}; text-anchor: middle; dominant-baseline: central; fill: {svg_default_text_color}; }}
            .alias-label-rotated {{ font-size: {svg_alias_font_size}; fill: {svg_default_text_color}; }}
            .alias-label-horizontal {{ font-size: {svg_alias_font_size}; fill: {svg_default_text_color}; text-anchor: start; dominant-baseline: middle; }}
            .info-label-foreign-object div {{ font-family: {default_font_family}; font-size: {svg_info_text_font_size}; line-height: {float(label_line_height_cfg) + 2}px; color: {svg_default_text_color}; padding: {svg_info_label_padding_cfg}; border-radius: 6px; box-sizing: border-box; }}
            .info-label-foreign-object b {{ font-size: {svg_info_title_font_size}; font-weight: bold; }}
            .info-label-foreign-object i {{ font-style: italic; color: #555; }}
            .ports-limit-note {{ font-size: 7.5px; color: #DD7700; font-style: italic; }}
            .info-label-foreign-object hr {{ border: 0; border-top: 0.5px solid {svg_info_hr_color}; margin: 3px 0; }}
            .status-dot {{ font-size: 10px; vertical-align: middle; }}
            .connection-label {{ font-size: {svg_connection_label_font_size}; fill: {svg_default_text_color}; text-anchor: middle; paint-order: stroke; stroke: white; stroke-width: 2.5px; stroke-opacity:0.85;}}"""
        logger.debug("SVGDiagram zainicjalizowany.")

    def update_dimensions(self, width: float, height: float):
        self.width = width
        self.height = height
        self.svg_root.set("width", str(self.width))
        self.svg_root.set("height", str(self.height))
        self.svg_root.set("viewBox", f"0 0 {self.width} {self.height}")
        bg_rect = self.svg_root.find("rect[@fill='white']")
        if bg_rect is not None:
            bg_rect.set("width", str(self.width))
            bg_rect.set("height", str(self.height))
        logger.info(f"Zaktualizowano wymiary SVG na: {self.width:.0f}x{self.height:.0f}")

    def add_element(self, element: ET.Element):
        self.svg_root.append(element)

    def get_svg_string(self) -> str:
        def ensure_str_attributes(element: ET.Element):
            for key, value in list(element.attrib.items()):
                if value is None:
                    logger.warning(
                        f"SVG Attr Fix: Atrybut '{key}' elementu '{element.tag}' miał wartość None. Zastępuję pustym stringiem.")
                    element.set(key, "")
                elif not isinstance(value, str):
                    logger.warning(
                        f"SVG Attr Fix: Atrybut '{key}' elementu '{element.tag}' nie był stringiem (typ: {type(value)}, wartość: '{value}'). Konwertuję na string.")
                    element.set(key, str(value))
            for child in element:
                ensure_str_attributes(child)

        ensure_str_attributes(self.svg_root)

        try:
            if hasattr(ET, 'indent'):
                ET.indent(self.svg_root, space="  ")
        except AttributeError:
            pass
        return ET.tostring(self.svg_root, encoding="unicode", method="xml")


def svg_add_device_to_diagram(
        svg_diagram: SVGDiagram,
        prepared_data: DeviceDisplayData,
        api_client: LibreNMSAPI,  # Nie jest już używane bezpośrednio tutaj
        position: Tuple[float, float],
        device_internal_idx: int,
        drawio_styles_ref: DrawioStyleInfoRef,
        config: Dict[str, Any]
) -> Optional[Dict[Any, PortEndpointData]]:
    port_map_for_device_svg: Dict[Any, PortEndpointData] = {}
    offset_x, offset_y = position
    interface_replacements_cfg = config.get('interface_name_replacements', {})

    current_host_identifier = prepared_data.canonical_identifier
    logger.info(
        f"SVG: Dodawanie urządzenia: {current_host_identifier} (idx: {device_internal_idx}) na ({offset_x:.0f}, {offset_y:.0f})")

    chassis_width, chassis_height = prepared_data.chassis_layout.width, prepared_data.chassis_layout.height
    device_group_main_svg = ET.Element("g", {"id": f"device_main_svg_{device_internal_idx}",
                                             "transform": f"translate({offset_x:.2f},{offset_y:.2f})"})
    chassis_svg_attrs = _parse_drawio_style_string_for_svg(drawio_styles_ref.chassis)
    chassis_rect_svg = ET.Element("rect",
                                  {"x": "0", "y": "0", "width": str(chassis_width), "height": str(chassis_height),
                                   **chassis_svg_attrs})
    device_group_main_svg.append(chassis_rect_svg)

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
    info_label_margin_cfg = config.get('info_label_margin_from_chassis')
    info_label_min_w_cfg = config.get('info_label_min_width')
    info_label_max_w_cfg = config.get('info_label_max_width')
    physical_port_list_max_h_cfg = config.get('physical_port_list_max_height')
    logical_if_list_max_h_cfg = config.get('logical_if_list_max_height')
    label_line_height_cfg = config.get('label_line_height')

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

            safe_fill_hex = fill_hex if fill_hex is not None else drawio_styles_ref.port_unknown_fill
            safe_stroke_hex = stroke_hex if stroke_hex is not None else drawio_styles_ref.port_unknown_stroke

            p_svg_attrs = {"fill": str(SVG_FILL_MAP.get(safe_fill_hex, safe_fill_hex)),
                           "stroke": str(SVG_STROKE_MAP.get(safe_stroke_hex, safe_stroke_hex)),
                           "stroke-width": "1"}
            if "rounded=0" not in drawio_styles_ref.port.lower():
                p_svg_attrs["rx"] = "3"
                p_svg_attrs["ry"] = "3"

            p_rect_svg = ET.Element("rect",
                                    {"id": p_svg_shape_id, "x": f"{px:.2f}", "y": f"{py:.2f}",
                                     "width": str(port_width_cfg),
                                     "height": str(port_height_cfg), **p_svg_attrs})
            device_group_main_svg.append(p_rect_svg)

            p_text_svg = ET.Element("text",
                                    {"x": f"{px + port_width_cfg / 2:.2f}", "y": f"{py + port_height_cfg / 2:.2f}",
                                     "class": "port-label"})
            p_text_svg.text = vis_num_str
            device_group_main_svg.append(p_text_svg)

            center_x_p_rel = px + port_width_cfg / 2
            conn_orient: str
            conn_epy_rel: float
            if row_idx % 2 == 0:
                conn_epy_rel, conn_orient = py - waypoint_offset_cfg, "up"
            else:
                conn_epy_rel, conn_orient = py + port_height_cfg + waypoint_offset_cfg, "down"

            ep_abs_x, ep_abs_y, ep_id = offset_x + center_x_p_rel, offset_y + conn_epy_rel, f"ep_svg_{device_internal_idx}_{p_svg_base_id}"
            ep_data = PortEndpointData(ep_id, ep_abs_x, ep_abs_y, conn_orient)

            if p_ifidx is not None: port_map_for_device_svg[f"ifindex_{p_ifidx}"] = ep_data
            if p_id_api is not None: port_map_for_device_svg[f"portid_{p_id_api}"] = ep_data
            port_map_for_device_svg[vis_num_str] = ep_data

            p_name_api = str(p_info.get('ifName', '')).strip()
            if p_name_api:
                port_map_for_device_svg[p_name_api.lower()] = ep_data
                normalized_p_name = normalize_interface_name(p_name_api, interface_replacements_cfg)
                if normalized_p_name.lower() != p_name_api.lower():
                    port_map_for_device_svg[normalized_p_name.lower()] = ep_data

            p_alias_api = str(p_info.get('ifAlias', '')).strip()
            if p_alias_api and p_alias_api.lower() != p_name_api.lower():
                port_map_for_device_svg[p_alias_api.lower()] = ep_data
                normalized_p_alias = normalize_interface_name(p_alias_api, interface_replacements_cfg)
                if normalized_p_alias.lower() != p_alias_api.lower() and normalized_p_alias.lower() != p_name_api.lower():
                    port_map_for_device_svg[normalized_p_alias.lower()] = ep_data

            p_descr_api = str(p_info.get('ifDescr', '')).strip()
            if p_descr_api and p_descr_api.lower() != p_name_api.lower() and p_descr_api.lower() != p_alias_api.lower():
                port_map_for_device_svg[p_descr_api.lower()] = ep_data
                normalized_p_descr = normalize_interface_name(p_descr_api, interface_replacements_cfg)
                if normalized_p_descr.lower() != p_descr_api.lower() and normalized_p_descr.lower() != p_name_api.lower() and normalized_p_descr.lower() != p_alias_api.lower():
                    port_map_for_device_svg[normalized_p_descr.lower()] = ep_data

            alias_txt = str(p_info.get("ifAlias", "")).strip()
            if alias_txt:
                aux_sx_abs, aux_ex_abs = offset_x + center_x_p_rel, offset_x + center_x_p_rel
                label_x_abs, label_y_abs = 0.0, 0.0
                text_anchor_svg = "middle"
                transform_svg = ""
                alias_label_class = "alias-label-horizontal"

                if conn_orient == "up":
                    aux_sy_abs, aux_ey_abs = offset_y + py, offset_y + py - port_alias_line_ext_cfg
                    alias_label_class = "alias-label-rotated"
                    label_x_abs = aux_ex_abs + port_alias_label_x_offset_cfg
                    label_y_abs = aux_ey_abs + port_alias_label_offset_cfg
                    text_anchor_svg = "start"
                    transform_svg = f"rotate(-90, {label_x_abs:.2f}, {label_y_abs:.2f})"
                elif conn_orient == "down":
                    aux_sy_abs, aux_ey_abs = offset_y + py + port_height_cfg, offset_y + py + port_height_cfg + port_alias_line_ext_cfg
                    alias_label_class = "alias-label-rotated"
                    label_x_abs = aux_ex_abs + port_alias_label_x_offset_cfg
                    label_y_abs = aux_ey_abs - port_alias_label_offset_cfg
                    text_anchor_svg = "end"
                    transform_svg = f"rotate(-90, {label_x_abs:.2f}, {label_y_abs:.2f})"

                aux_attrs = _parse_drawio_style_string_for_svg(drawio_styles_ref.aux_line)
                aux_line = ET.Element("line",
                                      {"x1": f"{aux_sx_abs:.2f}", "y1": f"{aux_sy_abs:.2f}", "x2": f"{aux_ex_abs:.2f}",
                                       "y2": f"{aux_ey_abs:.2f}", **aux_attrs})
                svg_diagram.add_element(aux_line)

                alias_lbl_attrs = {"x": f"{label_x_abs:.2f}", "y": f"{label_y_abs:.2f}", "class": alias_label_class,
                                   "text-anchor": text_anchor_svg}
                if transform_svg: alias_lbl_attrs["transform"] = transform_svg
                alias_lbl = ET.Element("text", alias_lbl_attrs)

                display_alias_svg = alias_txt.split('\n')[0]
                max_len_alias = 15 if alias_label_class == "alias-label-rotated" else 25
                if len(display_alias_svg) > max_len_alias:
                    display_alias_svg = display_alias_svg[:max_len_alias - 2] + ".."
                alias_lbl.text = display_alias_svg
                svg_diagram.add_element(alias_lbl)
            cur_port_idx += 1
        if cur_port_idx >= len(ports_to_draw): break

    mgmt0_info = prepared_data.mgmt0_port_info
    if mgmt0_info:
        logger.debug(f"  SVG: Dodawanie portu mgmt0 dla {current_host_identifier}...")
        mgmt0_x, mgmt0_y = chassis_width + horizontal_spacing_cfg, chassis_height / 2 - port_height_cfg / 2
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

        safe_fill_m_hex = fill_m_hex if fill_m_hex is not None else drawio_styles_ref.port_unknown_fill
        safe_stroke_m_hex = stroke_m_hex if stroke_m_hex is not None else drawio_styles_ref.port_unknown_stroke

        mgmt0_attrs = {"fill": str(SVG_FILL_MAP.get(safe_fill_m_hex, safe_fill_m_hex)),
                       "stroke": str(SVG_STROKE_MAP.get(safe_stroke_m_hex, safe_stroke_m_hex)),
                       "stroke-width": "1"}
        if "rounded=0" not in drawio_styles_ref.port.lower():
            mgmt0_attrs["rx"] = "3";
            mgmt0_attrs["ry"] = "3"

        mgmt0_rect = ET.Element("rect", {"id": mgmt0_shape_id, "x": f"{mgmt0_x:.2f}", "y": f"{mgmt0_y:.2f}",
                                         "width": str(port_width_cfg), "height": str(port_height_cfg), **mgmt0_attrs})
        device_group_main_svg.append(mgmt0_rect)

        mgmt0_text = ET.Element("text",
                                {"x": f"{mgmt0_x + port_width_cfg / 2:.2f}",
                                 "y": f"{mgmt0_y + port_height_cfg / 2:.2f}",
                                 "class": "port-label"})
        mgmt0_text.text = "M"
        device_group_main_svg.append(mgmt0_text)

        ep_abs_x_m, ep_abs_y_m = offset_x + mgmt0_x + port_width_cfg + waypoint_offset_cfg, \
                                 offset_y + mgmt0_y + port_height_cfg / 2
        ep_data_m = PortEndpointData(mgmt0_ep_id, ep_abs_x_m, ep_abs_y_m, "right")

        if mgmt0_ifidx is not None: port_map_for_device_svg[f"ifindex_{mgmt0_ifidx}"] = ep_data_m
        if mgmt0_pid is not None: port_map_for_device_svg[f"portid_{mgmt0_pid}"] = ep_data_m
        port_map_for_device_svg["mgmt0"] = ep_data_m
        mgmt0_name_api = str(mgmt0_info.get('ifName', '')).strip()
        if mgmt0_name_api:
            port_map_for_device_svg[mgmt0_name_api.lower()] = ep_data_m
            normalized_mgmt0_name = normalize_interface_name(mgmt0_name_api, interface_replacements_cfg)
            if normalized_mgmt0_name.lower() != mgmt0_name_api.lower():
                port_map_for_device_svg[normalized_mgmt0_name.lower()] = ep_data_m

        alias_txt_m = str(mgmt0_info.get("ifAlias", "")).strip()
        if alias_txt_m:
            aux_sx_m_abs = offset_x + mgmt0_x + port_width_cfg
            aux_sy_m_abs = offset_y + mgmt0_y + port_height_cfg / 2
            aux_ex_m_abs = aux_sx_m_abs + port_alias_line_ext_cfg
            aux_ey_m_abs = aux_sy_m_abs
            label_x_m_abs = aux_ex_m_abs + port_alias_label_offset_cfg
            label_y_m_abs = aux_ey_m_abs

            aux_attrs_m = _parse_drawio_style_string_for_svg(drawio_styles_ref.aux_line)
            mgmt0_aux_line = ET.Element("line", {"x1": f"{aux_sx_m_abs:.2f}", "y1": f"{aux_sy_m_abs:.2f}",
                                                 "x2": f"{aux_ex_m_abs:.2f}", "y2": f"{aux_ey_m_abs:.2f}",
                                                 **aux_attrs_m})
            svg_diagram.add_element(mgmt0_aux_line)

            mgmt0_alias_lbl = ET.Element("text", {"x": f"{label_x_m_abs:.2f}", "y": f"{label_y_m_abs:.2f}",
                                                  "class": "alias-label-horizontal", "text-anchor": "start"})
            mgmt0_alias_lbl.text = alias_txt_m
            svg_diagram.add_element(mgmt0_alias_lbl)

    svg_diagram.add_element(device_group_main_svg)

    # ... (reszta funkcji svg_add_device_to_diagram, czyli tworzenie etykiety informacyjnej, pozostaje bez zmian) ...
    dev_api_info_data = prepared_data.device_api_info
    dev_id_val = dev_api_info_data.get('device_id', 'N/A')
    hostname_raw = dev_api_info_data.get('hostname', '')
    ip_raw = dev_api_info_data.get('ip', '')
    purpose_raw = dev_api_info_data.get('purpose', '')
    display_name_main = prepared_data.canonical_identifier
    if prepared_data.is_stack:
        display_name_main += " (STACK)"

    ports_limit_info_text_svg = ""
    if prepared_data.ports_display_limited:
        ports_limit_info_text_svg = (f"(Porty ograniczone do {len(prepared_data.physical_ports_for_chassis_layout)} "
                                     f"z {prepared_data.total_physical_ports_before_limit})")

    extra_info_svg_list = []
    hostname_s, purpose_s = str(hostname_raw).strip(), str(purpose_raw).strip()
    main_name_no_stack_svg = display_name_main.replace(" (STACK)", "").strip()

    if hostname_s and hostname_s != main_name_no_stack_svg and not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', hostname_s):
        extra_info_svg_list.append(f"Host: {hostname_s}")
    if purpose_s and purpose_s != main_name_no_stack_svg:
        extra_info_svg_list.append(f"Cel: {purpose_s}")

    temp_display_ip_svg = str(ip_raw).strip() if ip_raw and str(ip_raw).strip() else 'N/A'
    if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', hostname_s) and not (ip_raw and str(ip_raw).strip()):
        temp_display_ip_svg = hostname_s

    xhtml_ns = "http://www.w3.org/1999/xhtml"
    div_container = ET.Element(f"{{{xhtml_ns}}}div", {"class": "info-label-foreign-object"})

    border_hex_svg, bg_hex_svg = "#c3c3c3", "#f8f8f8"
    try:
        styles_parsed_svg = {p.split('=')[0].lower(): p.split('=')[1] for p in drawio_styles_ref.info_label.split(';')
                             if '=' in p}
        border_hex_svg = styles_parsed_svg.get('strokecolor', border_hex_svg)
        bg_hex_svg = styles_parsed_svg.get('fillcolor', bg_hex_svg)
    except Exception:
        logger.warning("SVG: Błąd parsowania stylów info_label z DrawIO dla SVG, używam domyślnych.")

    safe_border_hex = border_hex_svg if border_hex_svg is not None else "#c3c3c3"
    safe_bg_hex = bg_hex_svg if bg_hex_svg is not None else "#f8f8f8"

    div_container.set("style",
                      f"border: 1px solid {str(SVG_STROKE_MAP.get(safe_border_hex, safe_border_hex))}; background-color: {str(SVG_FILL_MAP.get(safe_bg_hex, safe_bg_hex))};")

    def add_text_node_xhtml(parent_el, text_content, tag="span", is_bold=False, class_name=None):
        actual_tag = f"{{{xhtml_ns}}}{tag}"
        el = ET.SubElement(parent_el, actual_tag)
        safe_text_content = str(text_content) if text_content is not None else ""
        if is_bold:
            b_tag = ET.SubElement(el, f"{{{xhtml_ns}}}b")
            b_tag.text = safe_text_content
        else:
            el.text = safe_text_content
        if class_name: el.set("class", class_name)
        return el

    title_el = ET.SubElement(div_container, f"{{{xhtml_ns}}}b")
    title_el.text = str(display_name_main)
    ET.SubElement(div_container, f"{{{xhtml_ns}}}br")

    if ports_limit_info_text_svg:
        limit_el = ET.SubElement(div_container, f"{{{xhtml_ns}}}span", {"class": "ports-limit-note"})
        limit_el.text = str(ports_limit_info_text_svg)
        ET.SubElement(div_container, f"{{{xhtml_ns}}}br")

    add_text_node_xhtml(div_container, f"ID: {dev_id_val}")
    ET.SubElement(div_container, f"{{{xhtml_ns}}}br")
    for extra_line in extra_info_svg_list:
        add_text_node_xhtml(div_container, extra_line)
        ET.SubElement(div_container, f"{{{xhtml_ns}}}br")
    add_text_node_xhtml(div_container, f"IP: {temp_display_ip_svg}")

    ET.SubElement(div_container, f"{{{xhtml_ns}}}hr")

    add_text_node_xhtml(div_container, f"Porty Fizyczne ({len(prepared_data.all_physical_ports)}):", is_bold=True)
    phys_ports_div = ET.SubElement(div_container, f"{{{xhtml_ns}}}div",
                                   {
                                       "style": f"margin:0;padding-left:7px;max-height:{physical_port_list_max_h_cfg}px;overflow-y:auto;overflow-x:hidden;"})
    if prepared_data.all_physical_ports:
        for p in prepared_data.all_physical_ports:
            name, descr, alias = str(p.get('ifName', 'N/A')).strip(), str(p.get('ifDescr', '')).strip(), str(
                p.get('ifAlias', '')).strip()
            s_disp, aS_disp = str(p.get('ifOperStatus', 'u')).lower(), str(p.get('ifAdminStatus', 'u')).lower()
            s_fill_val_hex = drawio_styles_ref.port_unknown_fill
            if aS_disp == "down":
                s_fill_val_hex = drawio_styles_ref.port_shutdown_fill
            elif s_disp == "up":
                s_fill_val_hex = drawio_styles_ref.port_up_fill
            elif s_disp in ["down", "lowerlayerdown"]:
                s_fill_val_hex = drawio_styles_ref.port_down_fill

            safe_s_fill_val_hex = s_fill_val_hex if s_fill_val_hex is not None else drawio_styles_ref.port_unknown_fill
            dot_color = SVG_FILL_MAP.get(safe_s_fill_val_hex, safe_s_fill_val_hex)

            line_span = ET.SubElement(phys_ports_div, f"{{{xhtml_ns}}}span")
            dot_span = ET.SubElement(line_span, f"{{{xhtml_ns}}}span",
                                     {"style": f"color:{str(dot_color)};", "class": "status-dot"})
            dot_span.text = "• "
            line_span.append(ET.fromstring(f"<span xmlns='{xhtml_ns}'>{name}</span>"))
            extra_p_info = []
            if alias: extra_p_info.append(f"Alias: {alias}")
            if descr and descr != name and descr != alias: extra_p_info.append(f"Opis: {descr}")
            if extra_p_info:
                italic_span = ET.SubElement(line_span, f"{{{xhtml_ns}}}i")
                italic_span.text = f" ({'; '.join(extra_p_info)})"
            line_span.append(ET.fromstring(f"<span xmlns='{xhtml_ns}'> ({s_disp})</span>"))
            ET.SubElement(phys_ports_div, f"{{{xhtml_ns}}}br")
    else:
        add_text_node_xhtml(phys_ports_div, "(brak)")

    ET.SubElement(div_container, f"{{{xhtml_ns}}}hr")

    add_text_node_xhtml(div_container, f"Inne Interfejsy ({len(prepared_data.logical_interfaces)}):", is_bold=True)
    log_ifs_div = ET.SubElement(div_container, f"{{{xhtml_ns}}}div",
                                {
                                    "style": f"margin:0;padding-left:7px;max-height:{logical_if_list_max_h_cfg}px;overflow-y:auto;overflow-x:hidden;"})
    if prepared_data.logical_interfaces:
        for l_if in prepared_data.logical_interfaces:
            name_l = str(l_if.get('ifName') or l_if.get('ifDescr', 'N/A')).strip()
            s_disp_l, aS_disp_l = str(l_if.get('ifOperStatus', 'u')).lower(), str(
                l_if.get('ifAdminStatus', 'u')).lower()
            s_fill_l_hex = drawio_styles_ref.port_unknown_fill
            if aS_disp_l == "down":
                s_fill_l_hex = drawio_styles_ref.port_shutdown_fill
            elif s_disp_l == "up":
                s_fill_l_hex = drawio_styles_ref.port_up_fill
            elif s_disp_l in ["down", "lowerlayerdown"]:
                s_fill_l_hex = drawio_styles_ref.port_down_fill

            safe_s_fill_l_hex = s_fill_l_hex if s_fill_l_hex is not None else drawio_styles_ref.port_unknown_fill
            dot_color_l = SVG_FILL_MAP.get(safe_s_fill_l_hex, safe_s_fill_l_hex)

            if_type = str(l_if.get('_ifType_iana_debug', '')).strip()
            type_info = f" (Typ: {if_type})" if if_type else ""

            line_span_l = ET.SubElement(log_ifs_div, f"{{{xhtml_ns}}}span")
            dot_span_l = ET.SubElement(line_span_l, f"{{{xhtml_ns}}}span",
                                       {"style": f"color:{str(dot_color_l)};", "class": "status-dot"})
            dot_span_l.text = "• "
            line_span_l.append(ET.fromstring(f"<span xmlns='{xhtml_ns}'>{name_l}{type_info} ({s_disp_l})</span>"))
            ET.SubElement(log_ifs_div, f"{{{xhtml_ns}}}br")
    else:
        add_text_node_xhtml(log_ifs_div, "(brak)")

    info_label_width = min(max(chassis_width * 0.65, info_label_min_w_cfg), info_label_max_w_cfg)
    num_base_lines_approx = 3 + len(extra_info_svg_list) + (1 if ports_limit_info_text_svg else 0)
    base_h_approx = num_base_lines_approx * (float(label_line_height_cfg) + 4) + 15
    phys_h_approx = min(float(physical_port_list_max_h_cfg),
                        max(20, len(prepared_data.all_physical_ports) * (float(label_line_height_cfg) + 4))) + 30
    log_h_approx = min(float(logical_if_list_max_h_cfg),
                       max(20, len(prepared_data.logical_interfaces) * (float(label_line_height_cfg) + 4))) + 30
    info_lbl_h_approx = base_h_approx + phys_h_approx + log_h_approx + 25

    info_lbl_abs_x, info_lbl_abs_y = offset_x - info_label_width - info_label_margin_cfg, \
                                     offset_y + (chassis_height / 2) - (info_lbl_h_approx / 2)
    grid_margin_y_cfg = config.get('grid_margin_y', 350.0)
    info_lbl_abs_y = max((float(grid_margin_y_cfg) / 3), info_lbl_abs_y)

    foreign_object = ET.Element("foreignObject",
                                {"x": f"{info_lbl_abs_x:.2f}", "y": f"{info_lbl_abs_y:.2f}",
                                 "width": str(info_label_width), "height": str(info_lbl_h_approx)})
    foreign_object.append(div_container)
    svg_diagram.add_element(foreign_object)

    logger.info(f"✓ SVG: Urządzenie {current_host_identifier} dynamicznie przetworzone i dodane.")
    return port_map_for_device_svg


def _calculate_svg_waypoint(x: float, y: float, orientation: str, offset_val: float) -> Tuple[float, float]:
    """Pomocnicza funkcja do obliczania waypointów, wyodrębniona dla SVG."""
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


def svg_draw_connection(svg_diagram: SVGDiagram,
                        source_port_data: PortEndpointData, target_port_data: PortEndpointData,
                        vlan_label: Optional[str], connection_idx: int,
                        waypoint_offset_val: float,
                        config: Dict[str, Any]):
    if not source_port_data or not target_port_data: return

    conn_stroke_color_conf = config.get('connection_stroke_color', "#FF9900")  # Domyślny pomarańczowy
    conn_stroke_width = config.get('connection_stroke_width', "1.5")

    final_stroke_color = SVG_STROKE_MAP.get(conn_stroke_color_conf, conn_stroke_color_conf)

    path_d_parts = [f"M {source_port_data.x:.2f},{source_port_data.y:.2f}"]

    wp_sx, wp_sy = _calculate_svg_waypoint(source_port_data.x, source_port_data.y, source_port_data.orientation,
                                           waypoint_offset_val)
    path_d_parts.append(f"L {wp_sx:.2f},{wp_sy:.2f}")

    wp_tx, wp_ty = _calculate_svg_waypoint(target_port_data.x, target_port_data.y, target_port_data.orientation,
                                           waypoint_offset_val)
    path_d_parts.append(f"L {wp_tx:.2f},{wp_ty:.2f}")

    path_d_parts.append(f"L {target_port_data.x:.2f},{target_port_data.y:.2f}")

    path_attrs = {"d": " ".join(path_d_parts),
                  "stroke": str(final_stroke_color),
                  "stroke-width": str(conn_stroke_width),
                  "fill": "none",
                  "id": f"conn_svg_{connection_idx}"}
    svg_diagram.add_element(ET.Element("path", path_attrs))

    if vlan_label:
        label_x = (wp_sx + wp_tx) / 2
        label_y = (wp_sy + wp_ty) / 2
        vlan_text_el = ET.Element("text", {"x": f"{label_x:.2f}", "y": f"{label_y:.2f}", "class": "connection-label"})
        vlan_text_el.set("dy", "-2px")
        vlan_text_el.text = f"VLAN {vlan_label}"
        svg_diagram.add_element(vlan_text_el)
