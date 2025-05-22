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

from drawio_device_builder import StyleInfo as DrawioStyleInfoRef  # Używamy jako referencji

logger = logging.getLogger(__name__)

# Mapowanie kolorów i inne stałe specyficzne dla SVG
SVG_FILL_MAP = {
    "#ffffff": "white", "#dae8fc": "#dae8fc", "#E6E6E6": "#E6E6E6",
    "#D5E8D4": "#D5E8D4",
    "#F8CECC": "#F8CECC",
    "#FFE6CC": "#FFE6CC",
    "#E1D5E7": "#E1D5E7",
    "#f8f8f8": "#f8f8f8",
    "none": "none",
}
SVG_STROKE_MAP = {
    "#000000": "black", "#6c8ebf": "#6c8ebf", "#666666": "#666666",
    "#82B366": "#82B366",
    "#B85450": "#B85450",
    "#D79B00": "#D79B00",
    "#9673A6": "#9673A6",
    "#AAAAAA": "grey",
    "#FF9900": "orange",
    "#bababa": "#bababa",
    "#c3c3c3": "#c3c3c3",
    "none": "none",
}
SVG_DEFAULT_TEXT_COLOR = "black"
SVG_PORT_LABEL_FONT_SIZE = "8px"
SVG_ALIAS_FONT_SIZE = "7.5px"
SVG_INFO_TITLE_FONT_SIZE = "8.5px"
SVG_INFO_TEXT_FONT_SIZE = "8px"
SVG_CONNECTION_LABEL_FONT_SIZE = "7.5px"
SVG_INFO_HR_COLOR = "#D0D0D0"

SVG_PORT_ALIAS_LINE_EXTENSION = 25.0
SVG_PORT_ALIAS_LABEL_OFFSET_FROM_LINE = 2.0
SVG_PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER = 3.0

SVG_INFO_LABEL_MARGIN_FROM_CHASSIS = 25.0
SVG_INFO_LABEL_MIN_WIDTH = 180.0
SVG_INFO_LABEL_PADDING = "5px"


def _parse_drawio_style_string_for_svg(
        style_string: str,
        default_fill: str = "white",
        default_stroke: str = "black",
        default_stroke_width: str = "1"
) -> Dict[str, str]:
    """
    Konwertuje string stylu Draw.io na słownik atrybutów SVG.
    """
    attrs = {"fill": default_fill, "stroke": default_stroke, "stroke-width": default_stroke_width}
    if not style_string:
        return attrs

    style_dict: Dict[str, str] = {}
    parts = style_string.split(';')
    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            style_dict[key.strip().lower()] = value.strip()

    fill_color_key = style_dict.get("fillcolor", "")
    attrs["fill"] = SVG_FILL_MAP.get(fill_color_key, fill_color_key if fill_color_key else default_fill)

    stroke_color_key = style_dict.get("strokecolor", "")
    attrs["stroke"] = SVG_STROKE_MAP.get(stroke_color_key, stroke_color_key if stroke_color_key else default_stroke)

    attrs["stroke-width"] = style_dict.get("strokewidth", default_stroke_width)

    if style_dict.get("rounded") == "1":
        attrs["rx"] = style_dict.get("arcsize", "8")
        attrs["ry"] = style_dict.get("arcsize", "8")

    if style_dict.get("dashed") == "1":
        dash_pattern = style_dict.get("dashpattern", "3 3").replace(" ", ",")
        attrs["stroke-dasharray"] = dash_pattern
    return attrs


class SVGDiagram:
    def __init__(self, width: float = 2000, height: float = 1500):
        self.width = width
        self.height = height
        self.svg_root = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "xmlns:xhtml": "http://www.w3.org/1999/xhtml",
            "version": "1.1",
            "width": str(self.width),
            "height": str(self.height),
            "viewBox": f"0 0 {self.width} {self.height}"
        })
        ET.SubElement(self.svg_root, "rect", {"x": "0", "y": "0", "width": "100%", "height": "100%", "fill": "white"})

        defs = ET.SubElement(self.svg_root, "defs")
        style_el = ET.SubElement(defs, "style", {"type": "text/css"})
        default_font_family = "Arial, Helvetica, sans-serif"
        style_el.text = f"""
            svg {{ font-family: {default_font_family}; }}
            .port-label {{ font-size: {SVG_PORT_LABEL_FONT_SIZE}; text-anchor: middle; dominant-baseline: central; fill: {SVG_DEFAULT_TEXT_COLOR}; }}
            .alias-label-rotated {{ font-size: {SVG_ALIAS_FONT_SIZE}; fill: {SVG_DEFAULT_TEXT_COLOR}; writing-mode: tb; glyph-orientation-vertical: 0; }}
            .alias-label-horizontal {{ font-size: {SVG_ALIAS_FONT_SIZE}; fill: {SVG_DEFAULT_TEXT_COLOR}; text-anchor: start; dominant-baseline: middle; }}
            .info-label-foreign-object div {{
                font-family: {default_font_family};
                font-size: {SVG_INFO_TEXT_FONT_SIZE};
                line-height: {LABEL_LINE_HEIGHT + 2}px; 
                color: {SVG_DEFAULT_TEXT_COLOR};
                padding: {SVG_INFO_LABEL_PADDING};
                border-radius: 6px;
                box-sizing: border-box;
            }}
            .info-label-foreign-object b {{ font-size: {SVG_INFO_TITLE_FONT_SIZE}; font-weight: bold; }}
            .info-label-foreign-object hr {{ border: 0; border-top: 0.5px solid {SVG_INFO_HR_COLOR}; margin: 3px 0; }}
            .status-dot {{ font-size: 10px; vertical-align: middle; }}
            .connection-label {{ 
                font-size: {SVG_CONNECTION_LABEL_FONT_SIZE}; 
                fill: {SVG_DEFAULT_TEXT_COLOR}; 
                text-anchor: middle;
                paint-order: stroke; stroke: white; stroke-width: 2.5px; stroke-opacity:0.85;
            }}
        """
        logger.debug("SVGDiagram zainicjalizowany.")

    def update_dimensions(self, width: float, height: float) -> None:
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

    def add_element(self, element: ET.Element) -> None:
        self.svg_root.append(element)

    def get_svg_string(self) -> str:
        try:
            if hasattr(ET, 'indent'):
                ET.indent(self.svg_root, space="  ")
        except AttributeError:
            pass
        return ET.tostring(self.svg_root, encoding="unicode", method="xml")


def svg_add_device_to_diagram(
        svg_diagram: SVGDiagram,
        device_api_info: Dict[str, Any],
        api_client: LibreNMSAPI,
        position: Tuple[float, float],
        device_internal_idx: int,
        drawio_styles_ref: DrawioStyleInfoRef
) -> Optional[Dict[Any, PortEndpointData]]:
    """
    Dodaje reprezentację urządzenia (chassis, porty, etykiety) do diagramu SVG.
    """
    port_map_for_device_svg: Dict[Any, PortEndpointData] = {}
    offset_x, offset_y = position
    device_svg_group_main_id = f"device_main_svg_{device_internal_idx}"

    try:
        prepared_data: DeviceDisplayData = common_device_logic.prepare_device_display_data(
            device_api_info, api_client, device_internal_idx
        )
    except Exception as e:
        logger.error(
            f"SVG: Krytyczny błąd podczas przygotowywania danych urządzenia dla '{device_api_info.get('hostname')}': {e}. Pomijam to urządzenie.",
            exc_info=True)
        return None

    current_host_identifier = prepared_data.canonical_identifier
    logger.info(
        f"SVG: Dynamiczne dodawanie urządzenia: {current_host_identifier} (index: {device_internal_idx}) na pozycji ({offset_x:.0f}, {offset_y:.0f})")

    chassis_width = prepared_data.chassis_layout.width
    chassis_height = prepared_data.chassis_layout.height

    device_group_main_svg = ET.Element("g", {
        "id": device_svg_group_main_id,
        "transform": f"translate({offset_x:.2f},{offset_y:.2f})"
    })

    chassis_svg_attrs = _parse_drawio_style_string_for_svg(drawio_styles_ref.chassis, default_fill="lightgrey",
                                                           default_stroke="black")
    chassis_rect_svg = ET.Element("rect", {
        "x": "0", "y": "0", "width": str(chassis_width), "height": str(chassis_height),
        **chassis_svg_attrs
    })
    device_group_main_svg.append(chassis_rect_svg)

    num_layout_rows_svg = prepared_data.chassis_layout.num_rows
    ports_per_row_config_svg = prepared_data.chassis_layout.ports_per_row
    ports_in_rows_distribution_svg: List[int] = []

    if prepared_data.physical_ports_for_chassis_layout:
        if num_layout_rows_svg == 1:
            ports_in_rows_distribution_svg.append(len(prepared_data.physical_ports_for_chassis_layout))
        elif num_layout_rows_svg == 2:
            r1_c = math.ceil(len(prepared_data.physical_ports_for_chassis_layout) / 2.0)
            ports_in_rows_distribution_svg.append(int(r1_c))
            ports_in_rows_distribution_svg.append(len(prepared_data.physical_ports_for_chassis_layout) - int(r1_c))
        else:
            remaining_ports = len(prepared_data.physical_ports_for_chassis_layout)
            for _ in range(num_layout_rows_svg):
                count_this_row = min(remaining_ports, ports_per_row_config_svg)
                ports_in_rows_distribution_svg.append(count_this_row)
                remaining_ports -= count_this_row
                if remaining_ports <= 0: break

    current_port_overall_idx_svg = 0
    for row_idx_svg, num_ports_in_this_row_svg in enumerate(ports_in_rows_distribution_svg):
        if num_ports_in_this_row_svg == 0: continue
        current_row_content_width_svg = num_ports_in_this_row_svg * PORT_WIDTH + max(0,
                                                                                     num_ports_in_this_row_svg - 1) * HORIZONTAL_SPACING
        row_start_x_relative_svg = (chassis_width - current_row_content_width_svg) / 2

        for col_idx_in_row_svg in range(num_ports_in_this_row_svg):
            if current_port_overall_idx_svg >= len(prepared_data.physical_ports_for_chassis_layout): break
            port_api_info = prepared_data.physical_ports_for_chassis_layout[current_port_overall_idx_svg]
            visual_port_num_str_svg = str(current_port_overall_idx_svg + 1)
            px_rel = row_start_x_relative_svg + col_idx_in_row_svg * (PORT_WIDTH + HORIZONTAL_SPACING)
            py_rel = ROW_OFFSET_Y + row_idx_svg * (PORT_HEIGHT + VERTICAL_SPACING)
            port_ifindex_svg = port_api_info.get("ifIndex")
            port_id_api_svg = port_api_info.get("port_id")
            port_svg_base_id_part = f"p{port_ifindex_svg if port_ifindex_svg is not None else port_id_api_svg if port_id_api_svg is not None else f'vis{visual_port_num_str_svg}'}"
            port_svg_shape_id = f"svgshape_port_{device_internal_idx}_{port_svg_base_id_part}"

            status_svg = str(port_api_info.get("ifOperStatus", "unknown")).lower()
            admin_status_svg = str(port_api_info.get("ifAdminStatus", "unknown")).lower()

            # Poprawione pobieranie kolorów
            fill_color_hex = drawio_styles_ref.port_unknown_fill
            stroke_color_hex = drawio_styles_ref.port_unknown_stroke

            if admin_status_svg == "down":
                fill_color_hex = drawio_styles_ref.port_shutdown_fill
                stroke_color_hex = drawio_styles_ref.port_shutdown_stroke
            elif status_svg == "up":
                fill_color_hex = drawio_styles_ref.port_up_fill
                stroke_color_hex = drawio_styles_ref.port_up_stroke
            elif status_svg == "down" or status_svg == "lowerlayerdown":
                fill_color_hex = drawio_styles_ref.port_down_fill
                stroke_color_hex = drawio_styles_ref.port_down_stroke

            port_svg_attrs = {
                "fill": SVG_FILL_MAP.get(fill_color_hex, fill_color_hex),
                "stroke": SVG_STROKE_MAP.get(stroke_color_hex, stroke_color_hex),
                "stroke-width": "1"
            }
            if "rounded=0" not in drawio_styles_ref.port:
                port_svg_attrs["rx"] = "3";
                port_svg_attrs["ry"] = "3"

            port_rect_svg = ET.Element("rect", {
                "id": port_svg_shape_id, "x": f"{px_rel:.2f}", "y": f"{py_rel:.2f}",
                "width": str(PORT_WIDTH), "height": str(PORT_HEIGHT), **port_svg_attrs
            })
            device_group_main_svg.append(port_rect_svg)
            port_text_svg = ET.Element("text", {
                "x": f"{px_rel + PORT_WIDTH / 2:.2f}", "y": f"{py_rel + PORT_HEIGHT / 2:.2f}", "class": "port-label"
            })
            port_text_svg.text = visual_port_num_str_svg
            device_group_main_svg.append(port_text_svg)

            center_x_port_relative_svg = px_rel + PORT_WIDTH / 2
            conn_orient_svg: str;
            conn_ep_y_relative_svg: float
            if row_idx_svg % 2 == 0:
                conn_ep_y_relative_svg = py_rel - WAYPOINT_OFFSET;
                conn_orient_svg = "up"
            else:
                conn_ep_y_relative_svg = py_rel + PORT_HEIGHT + WAYPOINT_OFFSET;
                conn_orient_svg = "down"
            conn_ep_abs_x_svg = offset_x + center_x_port_relative_svg
            conn_ep_abs_y_svg = offset_y + conn_ep_y_relative_svg
            ep_svg_id = f"ep_svg_{device_internal_idx}_{port_svg_base_id_part}"
            ep_data_svg = PortEndpointData(ep_svg_id, conn_ep_abs_x_svg, conn_ep_abs_y_svg, conn_orient_svg)

            port_name_api_val = port_api_info.get('ifName')
            if port_ifindex_svg is not None: port_map_for_device_svg[f"ifindex_{port_ifindex_svg}"] = ep_data_svg
            if port_id_api_svg is not None: port_map_for_device_svg[f"portid_{port_id_api_svg}"] = ep_data_svg
            if port_name_api_val:
                port_map_for_device_svg[port_name_api_val] = ep_data_svg
                port_map_for_device_svg[port_name_api_val.lower()] = ep_data_svg
            port_map_for_device_svg[visual_port_num_str_svg] = ep_data_svg  # Mapowanie po numerze wizualnym

            alias_text_svg = str(port_api_info.get("ifAlias", "")).strip()
            if alias_text_svg:
                aux_line_start_x_abs_svg = offset_x + center_x_port_relative_svg
                aux_line_end_x_abs_svg = aux_line_start_x_abs_svg
                label_x_abs_svg: float;
                label_y_abs_svg: float
                text_anchor_svg = "middle";
                transform_svg = ""
                if conn_orient_svg == "up":
                    aux_line_start_y_abs_svg = offset_y
                    aux_line_end_y_abs_svg = aux_line_start_y_abs_svg - SVG_PORT_ALIAS_LINE_EXTENSION
                    label_x_abs_svg = aux_line_end_x_abs_svg + SVG_PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER
                    label_y_abs_svg = aux_line_end_y_abs_svg - SVG_PORT_ALIAS_LABEL_OFFSET_FROM_LINE
                    text_anchor_svg = "end";
                    transform_svg = f"rotate(-90 {label_x_abs_svg:.2f} {label_y_abs_svg:.2f})"
                else:
                    aux_line_start_y_abs_svg = offset_y + chassis_height
                    aux_line_end_y_abs_svg = aux_line_start_y_abs_svg + SVG_PORT_ALIAS_LINE_EXTENSION
                    label_x_abs_svg = aux_line_end_x_abs_svg + SVG_PORT_ALIAS_LABEL_X_OFFSET_FROM_LINE_CENTER
                    label_y_abs_svg = aux_line_end_y_abs_svg + SVG_PORT_ALIAS_LABEL_OFFSET_FROM_LINE
                    text_anchor_svg = "start";
                    transform_svg = f"rotate(-90 {label_x_abs_svg:.2f} {label_y_abs_svg:.2f})"

                aux_line_attrs_svg = _parse_drawio_style_string_for_svg(drawio_styles_ref.aux_line,
                                                                        default_stroke="grey")
                aux_line_el_svg = ET.Element("line", {
                    "x1": f"{aux_line_start_x_abs_svg:.2f}", "y1": f"{aux_line_start_y_abs_svg:.2f}",
                    "x2": f"{aux_line_end_x_abs_svg:.2f}", "y2": f"{aux_line_end_y_abs_svg:.2f}", **aux_line_attrs_svg
                })
                svg_diagram.add_element(aux_line_el_svg)
                alias_label_el_svg = ET.Element("text", {
                    "x": f"{label_x_abs_svg:.2f}", "y": f"{label_y_abs_svg:.2f}",
                    "class": "alias-label-rotated", "text-anchor": text_anchor_svg, "transform": transform_svg
                })
                display_alias_svg = alias_text_svg.split('\n')[0]
                if len(display_alias_svg) > 20: display_alias_svg = display_alias_svg[:18] + ".."
                alias_label_el_svg.text = display_alias_svg
                svg_diagram.add_element(alias_label_el_svg)
            current_port_overall_idx_svg += 1
        if current_port_overall_idx_svg >= len(prepared_data.physical_ports_for_chassis_layout): break

    mgmt0_info_svg = prepared_data.mgmt0_port_info
    if mgmt0_info_svg:
        logger.debug(f"  SVG: Dodawanie portu mgmt0 dla {current_host_identifier}...")
        mgmt0_x_relative_svg = chassis_width + HORIZONTAL_SPACING
        mgmt0_y_relative_svg = chassis_height / 2 - PORT_HEIGHT / 2
        mgmt0_ifindex_val_svg = mgmt0_info_svg.get('ifIndex')
        mgmt0_portid_val_svg = mgmt0_info_svg.get('port_id')
        mgmt0_svg_base_id_part = f"mgmt0_{mgmt0_ifindex_val_svg if mgmt0_ifindex_val_svg is not None else mgmt0_portid_val_svg if mgmt0_portid_val_svg is not None else 'na'}"
        mgmt0_svg_shape_id = f"svgshape_mgmt0_{device_internal_idx}_{mgmt0_svg_base_id_part}"
        mgmt0_ep_svg_id = f"ep_svg_mgmt0_{device_internal_idx}_{mgmt0_svg_base_id_part}"

        status_mgmt0_svg = str(mgmt0_info_svg.get("ifOperStatus", "unknown")).lower()
        admin_status_mgmt0_svg = str(mgmt0_info_svg.get("ifAdminStatus", "unknown")).lower()

        # Poprawione pobieranie kolorów dla mgmt0
        fill_hex_mgmt0 = drawio_styles_ref.port_unknown_fill
        stroke_hex_mgmt0 = drawio_styles_ref.port_unknown_stroke
        if admin_status_mgmt0_svg == "down":
            fill_hex_mgmt0 = drawio_styles_ref.port_shutdown_fill
            stroke_hex_mgmt0 = drawio_styles_ref.port_shutdown_stroke
        elif status_mgmt0_svg == "up":
            fill_hex_mgmt0 = drawio_styles_ref.port_up_fill
            stroke_hex_mgmt0 = drawio_styles_ref.port_up_stroke
        elif status_mgmt0_svg == "down" or status_mgmt0_svg == "lowerlayerdown":
            fill_hex_mgmt0 = drawio_styles_ref.port_down_fill
            stroke_hex_mgmt0 = drawio_styles_ref.port_down_stroke

        mgmt0_svg_attrs = {
            "fill": SVG_FILL_MAP.get(fill_hex_mgmt0, fill_hex_mgmt0),
            "stroke": SVG_STROKE_MAP.get(stroke_hex_mgmt0, stroke_hex_mgmt0),
            "stroke-width": "1"
        }
        mgmt0_rect_svg = ET.Element("rect", {
            "id": mgmt0_svg_shape_id, "x": f"{mgmt0_x_relative_svg:.2f}", "y": f"{mgmt0_y_relative_svg:.2f}",
            "width": str(PORT_WIDTH), "height": str(PORT_HEIGHT), **mgmt0_svg_attrs
        })
        device_group_main_svg.append(mgmt0_rect_svg)
        mgmt0_text_svg = ET.Element("text", {
            "x": f"{mgmt0_x_relative_svg + PORT_WIDTH / 2:.2f}", "y": f"{mgmt0_y_relative_svg + PORT_HEIGHT / 2:.2f}",
            "class": "port-label"
        })
        mgmt0_text_svg.text = "M"
        device_group_main_svg.append(mgmt0_text_svg)

        conn_ep_abs_x_mgmt0_svg = offset_x + mgmt0_x_relative_svg + PORT_WIDTH + WAYPOINT_OFFSET
        conn_ep_abs_y_mgmt0_svg = offset_y + mgmt0_y_relative_svg + PORT_HEIGHT / 2
        ep_data_mgmt0_svg = PortEndpointData(mgmt0_ep_svg_id, conn_ep_abs_x_mgmt0_svg, conn_ep_abs_y_mgmt0_svg, "right")

        mgmt0_name_val_svg = mgmt0_info_svg.get('ifName')
        if mgmt0_ifindex_val_svg is not None: port_map_for_device_svg[
            f"ifindex_{mgmt0_ifindex_val_svg}"] = ep_data_mgmt0_svg
        if mgmt0_portid_val_svg is not None: port_map_for_device_svg[
            f"portid_{mgmt0_portid_val_svg}"] = ep_data_mgmt0_svg
        if mgmt0_name_val_svg:
            port_map_for_device_svg[mgmt0_name_val_svg] = ep_data_mgmt0_svg
            port_map_for_device_svg[mgmt0_name_val_svg.lower()] = ep_data_mgmt0_svg
        port_map_for_device_svg["mgmt0"] = ep_data_mgmt0_svg

        alias_text_mgmt_svg = str(mgmt0_info_svg.get("ifAlias", "")).strip()
        if alias_text_mgmt_svg:
            aux_line_start_x_mgmt_abs_svg = offset_x + mgmt0_x_relative_svg + PORT_WIDTH
            aux_line_start_y_mgmt_abs_svg = offset_y + mgmt0_y_relative_svg + PORT_HEIGHT / 2
            aux_line_end_x_mgmt_abs_svg = aux_line_start_x_mgmt_abs_svg + SVG_PORT_ALIAS_LINE_EXTENSION
            aux_line_end_y_mgmt_abs_svg = aux_line_start_y_mgmt_abs_svg
            aux_line_attrs_mgmt_svg = _parse_drawio_style_string_for_svg(drawio_styles_ref.aux_line,
                                                                         default_stroke="grey")
            mgmt0_aux_line_el_svg = ET.Element("line", {
                "x1": f"{aux_line_start_x_mgmt_abs_svg:.2f}", "y1": f"{aux_line_start_y_mgmt_abs_svg:.2f}",
                "x2": f"{aux_line_end_x_mgmt_abs_svg:.2f}", "y2": f"{aux_line_end_y_mgmt_abs_svg:.2f}",
                **aux_line_attrs_mgmt_svg
            })
            svg_diagram.add_element(mgmt0_aux_line_el_svg)
            label_x_mgmt_abs_svg = aux_line_end_x_mgmt_abs_svg + SVG_PORT_ALIAS_LABEL_OFFSET_FROM_LINE
            label_y_mgmt_abs_svg = aux_line_end_y_mgmt_abs_svg
            mgmt0_alias_label_el_svg = ET.Element("text", {
                "x": f"{label_x_mgmt_abs_svg:.2f}", "y": f"{label_y_mgmt_abs_svg:.2f}",
                "class": "alias-label-horizontal"
            })
            mgmt0_alias_label_el_svg.text = alias_text_mgmt_svg
            svg_diagram.add_element(mgmt0_alias_label_el_svg)

    svg_diagram.add_element(device_group_main_svg)

    dev_info_api = prepared_data.device_api_info
    dev_id_val_svg = dev_info_api.get('device_id', 'N/A')
    hostname_raw_svg = dev_info_api.get('hostname', '')
    ip_raw_svg = dev_info_api.get('ip', '')
    purpose_raw_svg = dev_info_api.get('purpose', '')
    display_name_main_svg = prepared_data.canonical_identifier
    if prepared_data.is_stack: display_name_main_svg += " (STACK)"
    extra_info_svg_list = []
    hostname_str_svg = str(hostname_raw_svg).strip()
    purpose_str_svg = str(purpose_raw_svg).strip()
    main_name_no_stack_svg = display_name_main_svg.replace(" (STACK)", "")
    if hostname_str_svg and hostname_str_svg != main_name_no_stack_svg and not re.match(
            r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname_str_svg):
        extra_info_svg_list.append(f"Host: {hostname_str_svg}")
    if purpose_str_svg and purpose_str_svg != main_name_no_stack_svg:
        extra_info_svg_list.append(f"Cel: {purpose_str_svg}")
    temp_display_ip_svg = str(ip_raw_svg).strip() if ip_raw_svg else 'N/A'
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname_str_svg) and not ip_raw_svg:
        temp_display_ip_svg = hostname_str_svg

    xhtml_ns = "http://www.w3.org/1999/xhtml"  # Namespace dla XHTML
    xhtml_div_content = ET.Element(f"{{{xhtml_ns}}}div", {"class": "info-label-foreign-object"})

    info_label_border_color_hex = "#c3c3c3"  # Domyślny
    info_label_bg_color_hex = "#f8f8f8"  # Domyślny
    try:  # Bezpieczne pobieranie kolorów ze stylu DrawIO
        style_parts = {p.split('=')[0].lower(): p.split('=')[1] for p in drawio_styles_ref.info_label.split(';') if
                       '=' in p}
        info_label_border_color_hex = style_parts.get('strokecolor', info_label_border_color_hex)
        info_label_bg_color_hex = style_parts.get('fillcolor', info_label_bg_color_hex)
    except Exception:
        logger.warning(
            "SVG: Nie można sparsować kolorów dla etykiety informacyjnej ze stylu DrawIO. Używam domyślnych.")

    xhtml_div_content.set("style",
                          f"border: 1px solid {SVG_STROKE_MAP.get(info_label_border_color_hex, info_label_border_color_hex)}; background-color: {SVG_FILL_MAP.get(info_label_bg_color_hex, info_label_bg_color_hex)};")

    title_b = ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}b");
    title_b.text = display_name_main_svg
    ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}br")
    id_text_node = ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}span");
    id_text_node.text = f"ID: {dev_id_val_svg}"
    if extra_info_svg_list:
        ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}br")
        extra_text_node = ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}span");
        extra_text_node.text = "; ".join(extra_info_svg_list)
    ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}br")
    ip_text_node = ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}span");
    ip_text_node.text = f"IP: {temp_display_ip_svg}"
    ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}hr")

    phys_ports_title_b = ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}b");
    phys_ports_title_b.text = f"Porty Fizyczne ({len(prepared_data.all_physical_ports)}):"
    phys_ports_div = ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}div", {
        "style": f"max-height:{PHYSICAL_PORT_LIST_MAX_HEIGHT}px; overflow-y:auto; overflow-x:hidden;"})
    if prepared_data.all_physical_ports:
        for phys_port in prepared_data.all_physical_ports:
            port_line_div = ET.SubElement(phys_ports_div, f"{{{xhtml_ns}}}div")
            status_dot_span = ET.SubElement(port_line_div, f"{{{xhtml_ns}}}span", {"class": "status-dot"})

            status_p = str(phys_port.get("ifOperStatus", "unknown")).lower()
            admin_status_p = str(phys_port.get("ifAdminStatus", "unknown")).lower()
            status_color_hex = drawio_styles_ref.port_unknown_fill  # Domyślny
            if admin_status_p == "down":
                status_color_hex = drawio_styles_ref.port_shutdown_fill
            elif status_p == "up":
                status_color_hex = drawio_styles_ref.port_up_fill
            elif status_p == "down" or status_p == "lowerlayerdown":
                status_color_hex = drawio_styles_ref.port_down_fill
            status_dot_span.set("style",
                                f"color:{SVG_FILL_MAP.get(status_color_hex, status_color_hex)};")  # Użyj fill color dla kropki
            status_dot_span.text = "• "

            name = str(phys_port.get('ifName', 'N/A')).strip()
            descr = str(phys_port.get('ifDescr', '')).strip()
            alias = str(phys_port.get('ifAlias', '')).strip()
            extra_info_parts = []
            if alias: extra_info_parts.append(f"Alias: {alias}")
            if descr and descr != name and descr != alias: extra_info_parts.append(f"Opis: {descr}")
            extra_info_str = f" ({'; '.join(extra_info_parts)})" if extra_info_parts else ""

            port_text_span = ET.SubElement(port_line_div, f"{{{xhtml_ns}}}span")
            port_text_span.text = f"{name}{extra_info_str} ({status_p})"
    else:
        no_phys_div = ET.SubElement(phys_ports_div, f"{{{xhtml_ns}}}div");
        no_phys_div.text = "(brak)"
    ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}hr")

    logical_ifs_title_b = ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}b");
    logical_ifs_title_b.text = f"Inne Interfejsy ({len(prepared_data.logical_interfaces)}):"
    logical_ifs_div = ET.SubElement(xhtml_div_content, f"{{{xhtml_ns}}}div", {
        "style": f"max-height:{LOGICAL_IF_LIST_MAX_HEIGHT}px; overflow-y:auto; overflow-x:hidden;"})
    if prepared_data.logical_interfaces:
        for logical_if in prepared_data.logical_interfaces:
            log_if_line_div = ET.SubElement(logical_ifs_div, f"{{{xhtml_ns}}}div")
            log_status_dot_span = ET.SubElement(log_if_line_div, f"{{{xhtml_ns}}}span", {"class": "status-dot"})

            status_l = str(logical_if.get('ifOperStatus', 'unknown')).lower()
            admin_status_l = str(logical_if.get('ifAdminStatus', 'unknown')).lower()
            status_color_l_hex = drawio_styles_ref.port_unknown_fill
            if admin_status_l == "down":
                status_color_l_hex = drawio_styles_ref.port_shutdown_fill
            elif status_l == "up":
                status_color_l_hex = drawio_styles_ref.port_up_fill
            elif status_l == "down" or status_l == "lowerlayerdown":
                status_color_l_hex = drawio_styles_ref.port_down_fill
            log_status_dot_span.set("style", f"color:{SVG_FILL_MAP.get(status_color_l_hex, status_color_l_hex)};")
            log_status_dot_span.text = "• "

            name_l = str(logical_if.get('ifName') or logical_if.get('ifDescr', 'N/A')).strip()
            if_type_str = str(logical_if.get('_ifType_iana_debug', '')).strip()
            type_info = f" (Typ: {if_type_str})" if if_type_str else ""

            log_if_text_span = ET.SubElement(log_if_line_div, f"{{{xhtml_ns}}}span")
            log_if_text_span.text = f"{name_l}{type_info} ({status_l})"
    else:
        no_logical_div = ET.SubElement(logical_ifs_div, f"{{{xhtml_ns}}}div");
        no_logical_div.text = "(brak)"

    info_label_width_svg = max(chassis_width * 0.7, SVG_INFO_LABEL_MIN_WIDTH)
    # Przybliżona wysokość etykiety - może wymagać dostosowania
    num_lines_base = 3 + len(extra_info_svg_list)
    num_lines_phys = 1 + min(5, len(prepared_data.all_physical_ports))  # Przykład ograniczenia dla obliczeń
    num_lines_log = 1 + min(3, len(prepared_data.logical_interfaces))
    estimated_height = (num_lines_base + num_lines_phys + num_lines_log) * (
                LABEL_LINE_HEIGHT + 3) + 40  # + paddingi i hr

    info_label_height_svg = max(100, estimated_height)
    info_label_abs_x_svg = offset_x - info_label_width_svg - SVG_INFO_LABEL_MARGIN_FROM_CHASSIS
    info_label_abs_y_svg = offset_y
    foreign_object_svg = ET.Element("foreignObject", {
        "x": f"{info_label_abs_x_svg:.2f}", "y": f"{info_label_abs_y_svg:.2f}",
        "width": str(info_label_width_svg), "height": str(info_label_height_svg)
    })
    foreign_object_svg.append(xhtml_div_content)
    svg_diagram.add_element(foreign_object_svg)

    logger.info(f"✓ SVG: Urządzenie {current_host_identifier} dynamicznie przetworzone i dodane do diagramu.")
    return port_map_for_device_svg


def svg_draw_connection(
        svg_diagram: SVGDiagram,
        source_endpoint_data: PortEndpointData,
        target_endpoint_data: PortEndpointData,
        vlan_id_str: Optional[str],
        connection_idx: int
):
    line_svg_id = f"conn_line_svg_{connection_idx}"
    label_svg_id = f"conn_label_svg_{connection_idx}"
    x1_abs, y1_abs, orient1 = source_endpoint_data.x, source_endpoint_data.y, source_endpoint_data.orientation
    x2_abs, y2_abs, orient2 = target_endpoint_data.x, target_endpoint_data.y, target_endpoint_data.orientation
    path_data = f"M {x1_abs:.2f} {y1_abs:.2f} "
    wp1_x, wp1_y = x1_abs, y1_abs
    if orient1 == "up":
        wp1_y -= WAYPOINT_OFFSET
    elif orient1 == "down":
        wp1_y += WAYPOINT_OFFSET
    elif orient1 == "left":
        wp1_x -= WAYPOINT_OFFSET
    elif orient1 == "right":
        wp1_x += WAYPOINT_OFFSET
    path_data += f"L {wp1_x:.2f} {wp1_y:.2f} "
    wp2_x, wp2_y = x2_abs, y2_abs
    if orient2 == "up":
        wp2_y -= WAYPOINT_OFFSET
    elif orient2 == "down":
        wp2_y += WAYPOINT_OFFSET
    elif orient2 == "left":
        wp2_x -= WAYPOINT_OFFSET
    elif orient2 == "right":
        wp2_x += WAYPOINT_OFFSET

    if orient1 in ["up", "down"] and orient2 in ["up", "down"]:  # Oba pionowe
        mid_y = (wp1_y + wp2_y) / 2
        path_data += f"L {wp1_x:.2f} {mid_y:.2f} "
        path_data += f"L {wp2_x:.2f} {mid_y:.2f} "
    elif orient1 in ["left", "right"] and orient2 in ["left", "right"]:  # Oba poziome
        mid_x = (wp1_x + wp2_x) / 2
        path_data += f"L {mid_x:.2f} {wp1_y:.2f} "
        path_data += f"L {mid_x:.2f} {wp2_y:.2f} "
    elif (orient1 in ["up", "down"] and orient2 in ["left", "right"]) or \
            (orient1 in ["left", "right"] and orient2 in ["up", "down"]):  # Mieszane
        path_data += f"L {wp1_x:.2f} {wp2_y:.2f} "  # Jeden zakręt prosty (L-kształtny)
        # Alternatywnie, jeśli chcemy dwa zakręty dla bardziej złożonego routingu:
        # if orient1 in ["up", "down"]: # Start pionowy
        #     path_data += f"L {wp1_x:.2f} {wp2_y:.2f} "
        # else: # Start poziomy
        #     path_data += f"L {wp2_x:.2f} {wp1_y:.2f} "

    path_data += f"L {wp2_x:.2f} {wp2_y:.2f} "
    path_data += f"L {x2_abs:.2f} {y2_abs:.2f}"
    conn_path_svg = ET.Element("path", {
        "id": line_svg_id, "d": path_data,
        "stroke": SVG_STROKE_MAP.get("#FF9900", "orange"),
        "stroke-width": "1.5", "fill": "none"
    })
    svg_diagram.add_element(conn_path_svg)

    if vlan_id_str:
        # Środek segmentu między wp1 a wp2 (lub bardziej złożona logika)
        # Prosty środek geometryczny między (wp1_x, wp1_y) a (wp2_x, wp2_y)
        # Jeśli ścieżka ma punkt pośredni (np. mid_x, mid_y), użyj środka dłuższego segmentu
        label_x = (wp1_x + wp2_x) / 2
        label_y = (wp1_y + wp2_y) / 2

        # Prosta heurystyka dla odsunięcia etykiety, jeśli linia jest prawie pionowa/pozioma
        # Można dodać atrybut 'dy' lub 'dx' do elementu text dla lepszego pozycjonowania
        text_offset_dy = "0.35em"  # Domyślne wyrównanie pionowe
        if abs(wp1_x - wp2_x) < 10:  # Bardziej pionowa linia
            label_x += 5  # Odsuń w prawo
            text_anchor_label = "start"
        elif abs(wp1_y - wp2_y) < 10:  # Bardziej pozioma linia
            label_y -= 3  # Odsuń w górę
            text_anchor_label = "middle"
        else:
            text_anchor_label = "middle"

        vlan_text_svg = ET.Element("text", {
            "id": label_svg_id,
            "x": f"{label_x:.2f}", "y": f"{label_y:.2f}",
            "class": "connection-label",
            "dominant-baseline": "middle",  # Lepsze wyrównanie dla różnych text-anchor
            "text-anchor": text_anchor_label
            # "dy": text_offset_dy # Można użyć dla precyzyjniejszego pionowego wyrównania
        })
        vlan_text_svg.text = f"VLAN {vlan_id_str}"
        svg_diagram.add_element(vlan_text_svg)