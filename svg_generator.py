# --- svg_generator.py ---
import xml.et.ElementTree as ET
import re
import math
import logging
from typing import List, Dict, Tuple, Optional, Any, NamedTuple

from drawio_device_builder import (
    StyleInfo, PortEndpointData, DynamicLayoutInfo,
    DEFAULT_PORTS_PER_ROW, PORT_WIDTH, PORT_HEIGHT, HORIZONTAL_SPACING,
    VERTICAL_SPACING, ROW_OFFSET_Y, CHASSIS_PADDING_X, CHASSIS_PADDING_Y,
    MIN_CHASSIS_WIDTH, MIN_CHASSIS_HEIGHT, DEFAULT_CHASSIS_HEIGHT_NO_PORTS,
    LINE_LENGTH, LABEL_OFFSET_X, MARGIN_BETWEEN_LINE_AND_LABEL,
    LABEL_LINE_HEIGHT, LABEL_PADDING, INFO_LABEL_X_OFFSET, INFO_LABEL_MIN_WIDTH,
    LOGICAL_IF_LIST_MAX_HEIGHT, PHYSICAL_PORT_LIST_MAX_HEIGHT,
    STAGGER_HORIZONTAL_OFFSET_FACTOR, STAGGER_VERTICAL_MARGIN_OFFSET,
    _calculate_dynamic_layout, _classify_ports, natsort_keygen,
    STACK_DETECTION_THRESHOLD  # Bezpośredni import stałej
)
# Nie potrzebujemy już "import drawio_device_builder", bo STACK_DETECTION_THRESHOLD jest importowane bezpośrednio

from librenms_client import LibreNMSAPI

logger = logging.getLogger(__name__)

DRAWIO_FILL_TO_SVG_FILL = {
    "#ffffff": "white", "#dae8fc": "#dae8fc",  # Poprawiona linia
    "#00FF00": "lime", "#FF0000": "red", "#FFA500": "orange",
}
DRAWIO_STROKE_TO_SVG_STROKE = {
    "#000000": "black", "#6c8ebf": "#6c8ebf",
    "#AAAAAA": "grey", "#FF9900": "#FF9900",  # Dla linii połączeń
}
SVG_DEFAULT_TEXT_COLOR = "black"
SVG_PORT_LABEL_FONT_SIZE = 7  # Zmniejszono dla lepszego dopasowania
SVG_ALIAS_FONT_SIZE = 7
SVG_INFO_TITLE_FONT_SIZE = 8
SVG_INFO_TEXT_FONT_SIZE = 7
SVG_INFO_VLAN_FONT_SIZE = 7


def _parse_drawio_style_to_dict(style_string: str) -> Dict[str, str]:
    attributes = {}
    if not style_string: return attributes
    parts = style_string.split(';')
    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            attributes[key.strip()] = value.strip()
    return attributes


class SVGDiagram:
    def __init__(self, width: float = 2000, height: float = 1500):
        self.width = width
        self.height = height
        self.svg_root = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "xmlns:xhtml": "http://www.w3.org/1999/xhtml",  # Dla foreignObject
            "version": "1.1",
            "width": str(self.width),
            "height": str(self.height),
            "viewBox": f"0 0 {self.width} {self.height}"
        })
        defs = ET.SubElement(self.svg_root, "defs")
        style_el = ET.SubElement(defs, "style", {"type": "text/css"})
        style_el.text = f"""
            .port-label {{ font-family: Arial, sans-serif; font-size: {SVG_PORT_LABEL_FONT_SIZE}px; text-anchor: middle; dominant-baseline: central; fill: {SVG_DEFAULT_TEXT_COLOR}; }}
            .alias-label {{ font-family: Arial, sans-serif; font-size: {SVG_ALIAS_FONT_SIZE}px; fill: {SVG_DEFAULT_TEXT_COLOR}; }}
            .info-label-title {{ font-family: Arial, sans-serif; font-size: {SVG_INFO_TITLE_FONT_SIZE}px; font-weight: bold; fill: {SVG_DEFAULT_TEXT_COLOR}; }}
            .info-label-text {{ font-family: Arial, sans-serif; font-size: {SVG_INFO_TEXT_FONT_SIZE}px; fill: {SVG_DEFAULT_TEXT_COLOR}; }}
            .info-label-status-up {{ fill: {DRAWIO_FILL_TO_SVG_FILL.get("#00FF00", "green")}; }}
            .info-label-status-down {{ fill: {DRAWIO_FILL_TO_SVG_FILL.get("#FF0000", "red")}; }}
            .info-label-status-unknown {{ fill: {DRAWIO_FILL_TO_SVG_FILL.get("#FFA500", "orange")}; }}
            .info-label-hr {{ stroke: #D0D0D0; stroke-width: 0.5; }}
            .connection-label {{ font-family: Arial, sans-serif; font-size: {SVG_INFO_VLAN_FONT_SIZE}px; fill: {SVG_DEFAULT_TEXT_COLOR}; text-anchor: middle; }}
            .scrollable-port-list {{
                font-family: Arial, sans-serif; font-size: {SVG_INFO_TEXT_FONT_SIZE}px;
                line-height: {LABEL_LINE_HEIGHT + 1}px;
                color: {SVG_DEFAULT_TEXT_COLOR}; /* Kolor tekstu dla HTML w foreignObject */
            }}
        """

    def update_dimensions(self, width: float, height: float):
        self.width = width
        self.height = height
        self.svg_root.set("width", str(self.width))
        self.svg_root.set("height", str(self.height))
        self.svg_root.set("viewBox", f"0 0 {self.width} {self.height}")

    def add_element(self, element: ET.Element):
        self.svg_root.append(element)

    def get_svg_string(self) -> str:
        try:
            ET.indent(self.svg_root, space="  ")
        except AttributeError:
            pass
        return ET.tostring(self.svg_root, encoding="unicode", method="xml")


def svg_add_device_to_diagram(
        svg_diagram: SVGDiagram, device_info: dict, api_client: LibreNMSAPI,
        position: tuple[float, float], device_index: int,
        drawio_styles_ref: StyleInfo
) -> Optional[Dict[Any, PortEndpointData]]:
    port_map_for_device_svg: Dict[Any, PortEndpointData] = {}
    offset_x, offset_y = position
    device_group_svg_id = f"dev_svg_{device_index}"

    current_host_identifier = device_info.get('purpose') or device_info.get('hostname') or device_info.get('ip',
                                                                                                           f"ID:{device_info.get('device_id')}")
    logger.debug(f"SVG Gen: Dodawanie {current_host_identifier}...")  # Zmieniono na debug

    device_id = device_info.get("device_id")
    ports_data_raw = api_client.get_ports(str(device_id)) if device_id else []
    ports_data = ports_data_raw if ports_data_raw is not None else []
    all_physical_ports_api, logical_interfaces_api, mgmt0_api_info_data = _classify_ports(ports_data)

    physical_ports_to_draw_svg = [p for p in all_physical_ports_api if p != mgmt0_api_info_data]
    try:
        physical_ports_to_draw_svg.sort(key=lambda p: natsort_keygen(p.get('ifName', '')))
    except Exception:
        physical_ports_to_draw_svg.sort(key=lambda p: str(p.get('ifName', '')))

    num_phys_ports_layout = len(physical_ports_to_draw_svg)
    layout_info_svg: DynamicLayoutInfo = _calculate_dynamic_layout(num_phys_ports_layout)
    chassis_w_svg = layout_info_svg.width
    chassis_h_svg = layout_info_svg.height
    num_layout_rows_svg = layout_info_svg.num_rows

    device_group = ET.Element("g", {"id": device_group_svg_id, "transform": f"translate({offset_x},{offset_y})"})

    chassis_style_parsed = _parse_drawio_style_to_dict(drawio_styles_ref.chassis)
    chassis_rect_svg = ET.Element("rect", {
        "x": "0", "y": "0", "width": str(chassis_w_svg), "height": str(chassis_h_svg),
        "fill": DRAWIO_FILL_TO_SVG_FILL.get(chassis_style_parsed.get("fillColor", ""),
                                            chassis_style_parsed.get("fillColor", "white")),
        "stroke": DRAWIO_STROKE_TO_SVG_STROKE.get(chassis_style_parsed.get("strokeColor", ""),
                                                  chassis_style_parsed.get("strokeColor", "black")),
        "stroke-width": "1"
    })
    if chassis_style_parsed.get("rounded") == "1":
        chassis_rect_svg.set("rx", "8");
        chassis_rect_svg.set("ry", "8")
    device_group.append(chassis_rect_svg)

    ports_in_rows_dist_svg = []
    if num_phys_ports_layout > 0:
        if num_layout_rows_svg == 1:
            ports_in_rows_dist_svg.append(num_phys_ports_layout)
        elif num_layout_rows_svg == 2:
            r1_c = math.ceil(num_phys_ports_layout / 2.0);
            ports_in_rows_dist_svg.append(int(r1_c));
            ports_in_rows_dist_svg.append(num_phys_ports_layout - int(r1_c))
        else:
            rem_p = num_phys_ports_layout
            for _ in range(num_layout_rows_svg):
                c = min(rem_p, DEFAULT_PORTS_PER_ROW);
                ports_in_rows_dist_svg.append(c);
                rem_p -= c
                if rem_p <= 0: break

    port_overall_idx_svg = 0
    for r_idx_svg, num_p_in_row_svg in enumerate(ports_in_rows_dist_svg):
        for col_idx_in_row_svg in range(num_p_in_row_svg):
            if port_overall_idx_svg >= len(physical_ports_to_draw_svg): break
            port_api_info = physical_ports_to_draw_svg[port_overall_idx_svg]
            vis_port_num_svg = port_overall_idx_svg + 1

            px_rel = CHASSIS_PADDING_X + col_idx_in_row_svg * (PORT_WIDTH + HORIZONTAL_SPACING)
            py_rel = ROW_OFFSET_Y + r_idx_svg * (PORT_HEIGHT + VERTICAL_SPACING)

            port_api_ifindex = port_api_info.get("ifIndex")
            port_id_svg = f"port_svg_{device_index}_{port_api_ifindex if port_api_ifindex is not None else port_overall_idx_svg}"

            status_svg = port_api_info.get("ifOperStatus", "unknown").lower()
            fill_key_svg = drawio_styles_ref.port_up if status_svg == "up" else (
                drawio_styles_ref.port_down if status_svg == "down" else drawio_styles_ref.port_unknown)
            p_fill_svg = DRAWIO_FILL_TO_SVG_FILL.get(fill_key_svg, fill_key_svg)

            port_style_p = _parse_drawio_style_to_dict(drawio_styles_ref.port)
            p_stroke_svg = DRAWIO_STROKE_TO_SVG_STROKE.get(port_style_p.get("strokeColor", ""),
                                                           port_style_p.get("strokeColor", "grey"))

            p_rect_svg = ET.Element("rect",
                                    {"id": port_id_svg, "x": str(px_rel), "y": str(py_rel), "width": str(PORT_WIDTH),
                                     "height": str(PORT_HEIGHT), "fill": p_fill_svg, "stroke": p_stroke_svg,
                                     "stroke-width": "0.5"})
            device_group.append(p_rect_svg)
            p_text_svg = ET.Element("text", {"x": str(px_rel + PORT_WIDTH / 2), "y": str(py_rel + PORT_HEIGHT / 2),
                                             "class": "port-label"})  # Usunięto +1
            p_text_svg.text = str(vis_port_num_svg)
            device_group.append(p_text_svg)

            center_x_rel_svg = px_rel + PORT_WIDTH / 2
            ep_orient_svg: str
            line_end_y_rel_svg: float
            if r_idx_svg % 2 == 0:
                line_end_y_rel_svg = py_rel - LINE_LENGTH; ep_orient_svg = "up"
            else:
                line_end_y_rel_svg = py_rel + PORT_HEIGHT + LINE_LENGTH; ep_orient_svg = "down"

            ep_abs_x_svg = offset_x + center_x_rel_svg
            ep_abs_y_svg = offset_y + line_end_y_rel_svg

            ep_data_svg = PortEndpointData(cell_id=port_id_svg, x=ep_abs_x_svg, y=ep_abs_y_svg,
                                           orientation=ep_orient_svg)
            port_name_api_val = port_api_info.get('ifName')
            if port_api_ifindex is not None: port_map_for_device_svg[f"ifindex_{port_api_ifindex}"] = ep_data_svg
            if port_name_api_val: port_map_for_device_svg[port_name_api_val] = ep_data_svg
            port_map_for_device_svg[str(vis_port_num_svg)] = ep_data_svg

            alias_txt_svg = port_api_info.get("ifAlias", "")
            if alias_txt_svg:
                # Linia pomocnicza - punkt początkowy na krawędzi portu
                line_start_x_aux = center_x_rel_svg
                line_start_y_aux = py_rel if ep_orient_svg == "up" else py_rel + PORT_HEIGHT

                aux_line_svg_el = ET.Element("line", {
                    "x1": str(line_start_x_aux), "y1": str(line_start_y_aux),
                    "x2": str(center_x_rel_svg), "y2": str(line_end_y_rel_svg),
                    "stroke": DRAWIO_STROKE_TO_SVG_STROKE.get(
                        _parse_drawio_style_to_dict(drawio_styles_ref.aux_line).get("strokeColor", ""), "grey"),
                    "stroke-width": "1"
                })
                device_group.append(aux_line_svg_el)

                eff_lbl_x_offset = LABEL_OFFSET_X
                eff_vert_margin = MARGIN_BETWEEN_LINE_AND_LABEL

                is_staggered_svg = (col_idx_in_row_svg % 2 != 0)
                if is_staggered_svg:
                    eff_lbl_x_offset += (PORT_WIDTH + HORIZONTAL_SPACING) * STAGGER_HORIZONTAL_OFFSET_FACTOR
                    eff_vert_margin += STAGGER_VERTICAL_MARGIN_OFFSET

                # Pozycja X dla etykiety (środek obrotu)
                # Chcemy, aby etykieta była na prawo od linii pionowej
                final_lbl_x_svg = center_x_rel_svg + eff_lbl_x_offset

                # Pozycja Y dla etykiety (środek obrotu)
                # Dla orientacji "up", etykieta jest powyżej końca linii, dla "down" - poniżej
                if ep_orient_svg == "up":
                    final_lbl_y_svg = line_end_y_rel_svg - eff_vert_margin - (
                        LABEL_PADDING)  # Dodatkowy odstęp od linii
                else:
                    final_lbl_y_svg = line_end_y_rel_svg + eff_vert_margin + (
                                LABEL_LINE_HEIGHT + LABEL_PADDING)  # Dodatkowy odstęp

                alias_lines = alias_txt_svg.split('\n')
                display_alias = alias_lines[0]
                if len(alias_lines) > 1 and len(display_alias) > 20:  # Skróć, jeśli długi i wieloliniowy
                    display_alias = display_alias[:18] + ".."
                elif len(display_alias) > 25:  # Skróć, jeśli długi jednoliniowy
                    display_alias = display_alias[:23] + ".."

                alias_lbl_svg_el = ET.Element("text", {"class": "alias-label"})
                # Ustawienie punktu (x,y) i transformacji dla obrotu
                # (x,y) to punkt, względem którego chcemy pozycjonować po obrocie
                alias_lbl_svg_el.set("x", str(final_lbl_x_svg))
                alias_lbl_svg_el.set("y", str(final_lbl_y_svg))
                alias_lbl_svg_el.set("transform", f"rotate(-90 {final_lbl_x_svg} {final_lbl_y_svg})")

                # text-anchor i dominant-baseline dla lepszego pozycjonowania obróconego tekstu
                if ep_orient_svg == "up":
                    alias_lbl_svg_el.set("text-anchor", "end")  # Tekst "kończy się" na (x,y)
                else:  # down
                    alias_lbl_svg_el.set("text-anchor", "start")  # Tekst "zaczyna się" na (x,y)
                alias_lbl_svg_el.set("dominant-baseline", "middle")

                alias_lbl_svg_el.text = display_alias
                device_group.append(alias_lbl_svg_el)

            port_overall_idx_svg += 1
        if port_overall_idx_svg >= len(physical_ports_to_draw_svg): break

    if mgmt0_api_info_data:
        mgmt0_x_rel_svg = chassis_w_svg + HORIZONTAL_SPACING + 5  # Mały dodatkowy odstęp
        mgmt0_y_rel_svg = chassis_h_svg / 2 - PORT_HEIGHT / 2
        mgmt0_id_svg = f"mgmt0_svg_{device_index}"

        status_mgmt0_svg = mgmt0_api_info_data.get("ifOperStatus", "unknown").lower()
        fill_key_mgmt0_svg = drawio_styles_ref.port_up if status_mgmt0_svg == "up" else (
            drawio_styles_ref.port_down if status_mgmt0_svg == "down" else drawio_styles_ref.port_unknown)
        mgmt0_fill_svg = DRAWIO_FILL_TO_SVG_FILL.get(fill_key_mgmt0_svg, fill_key_mgmt0_svg)

        mgmt0_rect_svg = ET.Element("rect", {"id": mgmt0_id_svg, "x": str(mgmt0_x_rel_svg), "y": str(mgmt0_y_rel_svg),
                                             "width": str(PORT_WIDTH), "height": str(PORT_HEIGHT),
                                             "fill": mgmt0_fill_svg, "stroke": "black", "stroke-width": "0.5"})
        device_group.append(mgmt0_rect_svg)
        mgmt0_text_svg = ET.Element("text", {"x": str(mgmt0_x_rel_svg + PORT_WIDTH / 2),
                                             "y": str(mgmt0_y_rel_svg + PORT_HEIGHT / 2), "class": "port-label"})
        mgmt0_text_svg.text = "M"
        device_group.append(mgmt0_text_svg)

        ep_abs_x_mgmt0_svg = offset_x + mgmt0_x_rel_svg + PORT_WIDTH + LINE_LENGTH
        ep_abs_y_mgmt0_svg = offset_y + mgmt0_y_rel_svg + PORT_HEIGHT / 2
        ep_data_mgmt0_svg = PortEndpointData(cell_id=mgmt0_id_svg, x=ep_abs_x_mgmt0_svg, y=ep_abs_y_mgmt0_svg,
                                             orientation="right")
        if mgmt0_api_info_data.get('ifName'): port_map_for_device_svg[mgmt0_api_info_data['ifName']] = ep_data_mgmt0_svg
        port_map_for_device_svg["mgmt0"] = ep_data_mgmt0_svg

    # --- Etykieta Informacyjna z <foreignObject> dla przewijania ---
    info_label_group_outer = ET.Element("g", {
        "transform": f"translate({offset_x + INFO_LABEL_X_OFFSET}, {offset_y + ROW_OFFSET_Y})"})
    info_label_content_width = max(chassis_w_svg, INFO_LABEL_MIN_WIDTH)

    # Budowanie HTML dla foreignObject
    html_content = "<div xmlns='http://www.w3.org/1999/xhtml' class='scrollable-port-list' style='padding:2px;'>"

    dev_id_val = device_info.get('device_id', 'N/A');
    hostname_raw = device_info.get('hostname', '');
    ip_raw = device_info.get('ip', '');
    purpose_raw = device_info.get('purpose', '')
    display_name_main = purpose_raw.strip() if purpose_raw and purpose_raw.strip() else (
        hostname_raw if hostname_raw and not bool(
            re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', str(hostname_raw))) else (ip_raw if ip_raw else (
            hostname_raw if hostname_raw else (
                f"Urządzenie ID: {dev_id_val}" if dev_id_val != 'N/A' else "(Brak Nazwy)"))))
    if len(all_physical_ports_api) > STACK_DETECTION_THRESHOLD: display_name_main += " <b>(STACK)</b>"

    html_content += f"<div style='font-size:{SVG_INFO_TITLE_FONT_SIZE}px; font-weight:bold;'>{display_name_main}</div>"
    html_content += f"<div style='font-size:{SVG_INFO_TEXT_FONT_SIZE}px;'>ID: {dev_id_val}, IP: {ip_raw or 'N/A'}</div>"
    html_content += "<hr style='border:0; border-top:0.5px solid #D0D0D0; margin: 2px 0;'/>"

    html_content += f"<div style='font-size:{SVG_INFO_TITLE_FONT_SIZE}px; font-weight:bold;'>Porty Fizyczne ({len(all_physical_ports_api)}):</div>"
    html_content += f"<div style='max-height:{PHYSICAL_PORT_LIST_MAX_HEIGHT}px; overflow-y:auto;'>"  # DIV z przewijaniem
    for phys_port in all_physical_ports_api:
        p_name = phys_port.get('ifName', 'N/A')
        p_alias = phys_port.get('ifAlias', '')
        p_descr = phys_port.get('ifDescr', '')
        p_status = phys_port.get('ifOperStatus', 'unknown').lower()
        status_color_html = DRAWIO_FILL_TO_SVG_FILL.get(drawio_styles_ref.port_up if p_status == "up" else (
            drawio_styles_ref.port_down if p_status == "down" else drawio_styles_ref.port_unknown), "grey")
        extra_info_html = p_alias if p_alias else (p_descr if p_descr != p_name else '')
        extra_info_str_html = f" <i>({extra_info_html})</i>" if extra_info_html else ""
        html_content += f"<div><span style='color:{status_color_html};'>•</span> {p_name}{extra_info_str_html} ({p_status})</div>"
    html_content += "</div>"
    html_content += "<hr style='border:0; border-top:0.5px solid #D0D0D0; margin: 2px 0;'/>"

    html_content += f"<div style='font-size:{SVG_INFO_TITLE_FONT_SIZE}px; font-weight:bold;'>Inne Interfejsy ({len(logical_interfaces_api)}):</div>"
    html_content += f"<div style='max-height:{LOGICAL_IF_LIST_MAX_HEIGHT}px; overflow-y:auto;'>"  # DIV z przewijaniem
    for log_if in logical_interfaces_api:
        l_name = log_if.get('ifName', 'N/A')
        l_status = log_if.get('ifOperStatus', 'unknown').lower()
        status_color_html_log = DRAWIO_FILL_TO_SVG_FILL.get(drawio_styles_ref.port_up if l_status == "up" else (
            drawio_styles_ref.port_down if l_status == "down" else drawio_styles_ref.port_unknown), "grey")
        html_content += f"<div><span style='color:{status_color_html_log};'>•</span> {l_name} ({l_status})</div>"
    html_content += "</div>"
    html_content += "</div>"  # Koniec scrollable-port-list

    # Obliczanie przybliżonej wysokości na podstawie liczby linii (bardzo zgrubne)
    # Lepszym podejściem byłoby renderowanie HTML w tle i pobranie wysokości, ale to skomplikowane.
    num_base_lines_info = 3  # Tytuł, ID/IP, HR
    num_phys_ports_lines = 1 + len(all_physical_ports_api)  # Tytuł + porty
    num_logic_ifs_lines = 1 + len(logical_interfaces_api)
    approx_total_lines = num_base_lines_info + num_phys_ports_lines + num_logic_ifs_lines
    # Wysokość dla foreignObject - powinna być sumą wysokości sekcji z max-height
    foreign_object_height = (LABEL_LINE_HEIGHT + 2) * 3 + 5 + \
                            min(PHYSICAL_PORT_LIST_MAX_HEIGHT,
                                (LABEL_LINE_HEIGHT + 1) * (len(all_physical_ports_api) + 1) + 5) + \
                            min(LOGICAL_IF_LIST_MAX_HEIGHT,
                                (LABEL_LINE_HEIGHT + 1) * (len(logical_interfaces_api) + 1) + 5) + 10

    foreign_object = ET.Element("foreignObject", {
        "x": "0", "y": "0",  # Pozycja względem grupy info_label_group_outer
        "width": str(info_label_content_width),
        "height": str(foreign_object_height)  # Użyj obliczonej wysokości
    })
    # Osadź HTML - ważne jest, aby HTML był poprawnym XHTML
    # ET.fromstring oczekuje dobrze sformatowanego XML, więc musimy go opakować
    try:
        xhtml_parser = ET.XMLParser(encoding="utf-8")
        # Poprawka: Usuń zewnętrzny div, jeśli html_content już go ma.
        # html_content powinien zaczynać się od <div class='scrollable-port-list'>...</div>
        # a nie być opakowany w dodatkowy div.
        # Sprawdźmy, czy html_content zaczyna się od znacznika div
        if html_content.strip().startswith("<div xmlns='http://www.w3.org/1999/xhtml' class='scrollable-port-list'"):
            # Jeśli tak, parsujemy bezpośrednio
            parsed_html_div = ET.fromstring(html_content, parser=xhtml_parser)
            foreign_object.append(parsed_html_div)
        else:
            # Jeśli nie, opakowujemy (stara logika)
            html_element_wrapper = ET.fromstring(f"<div xmlns='http://www.w3.org/1999/xhtml'>{html_content}</div>",
                                                 parser=xhtml_parser)
            if html_element_wrapper.tag == "{http://www.w3.org/1999/xhtml}div" and len(html_element_wrapper) == 1:
                actual_content_div = html_element_wrapper.find(
                    "{http://www.w3.org/1999/xhtml}div[@class='scrollable-port-list']")
                if actual_content_div is not None:
                    foreign_object.append(actual_content_div)
                else:
                    foreign_object.append(html_element_wrapper[0])
            else:
                foreign_object.append(html_element_wrapper)


    except ET.ParseError as e_parse:
        logger.error(f"Błąd parsowania XHTML dla foreignObject: {e_parse}\nTreść HTML:\n{html_content[:500]}...")
        error_text_el = ET.Element("text", {"x": "0", "y": "10", "fill": "red"})
        error_text_el.text = "Błąd renderowania etykiety informacyjnej."
        foreign_object.append(error_text_el)

    info_label_group_outer.append(foreign_object)
    device_group.append(info_label_group_outer)

    svg_diagram.add_element(device_group)
    return port_map_for_device_svg


def svg_draw_connection(
        svg_diagram: SVGDiagram,
        source_endpoint_data: PortEndpointData,
        target_endpoint_data: PortEndpointData,
        vlan_id_str: Optional[str],
        connection_idx: int,
        waypoint_offset_val: float = 20.0
):
    line_svg_id = f"conn_svg_{connection_idx}"

    x1_abs, y1_abs = source_endpoint_data.x, source_endpoint_data.y
    orient1 = source_endpoint_data.orientation
    x2_abs, y2_abs = target_endpoint_data.x, target_endpoint_data.y
    orient2 = target_endpoint_data.orientation

    path_data = f"M {x1_abs} {y1_abs} "

    mid_x1, mid_y1 = x1_abs, y1_abs
    if orient1 == "up":
        mid_y1 -= waypoint_offset_val
    elif orient1 == "down":
        mid_y1 += waypoint_offset_val
    elif orient1 == "left":
        mid_x1 -= waypoint_offset_val
    elif orient1 == "right":
        mid_x1 += waypoint_offset_val
    path_data += f"L {mid_x1} {mid_y1} "

    mid_x2, mid_y2 = x2_abs, y2_abs
    if orient2 == "up":
        mid_y2 -= waypoint_offset_val
    elif orient2 == "down":
        mid_y2 += waypoint_offset_val
    elif orient2 == "left":
        mid_x2 -= waypoint_offset_val
    elif orient2 == "right":
        mid_x2 += waypoint_offset_val

    if orient1 in ["up", "down"] and orient2 in ["up", "down"]:
        path_data += f"L {mid_x2} {mid_y1} "
    elif orient1 in ["left", "right"] and orient2 in ["left", "right"]:
        path_data += f"L {mid_x1} {mid_y2} "
    else:
        if orient1 in ["up", "down"]:
            path_data += f"L {mid_x1} {mid_y2} "
        else:
            path_data += f"L {mid_x2} {mid_y1} "

    path_data += f"L {mid_x2} {mid_y2} "
    path_data += f"L {x2_abs} {y2_abs}"

    conn_path_svg = ET.Element("path", {
        "id": line_svg_id,
        "d": path_data,
        "stroke": DRAWIO_STROKE_TO_SVG_STROKE.get("#FF9900", "orange"),
        "stroke-width": "1.5",
        "fill": "none"
    })

    conn_group_svg = ET.Element("g")
    conn_group_svg.append(conn_path_svg)

    if vlan_id_str:
        label_x = (mid_x1 + mid_x2) / 2
        label_y = (mid_y1 + mid_y2) / 2
        if abs(mid_x1 - mid_x2) < abs(mid_y1 - mid_y2):
            label_x = mid_x1 if abs(x1_abs - mid_x1) < abs(x2_abs - mid_x2) else mid_x2
            label_x += 5
        else:
            label_y = mid_y1 if abs(y1_abs - mid_y1) < abs(y2_abs - mid_y2) else mid_y2
            label_y -= 3

        vlan_text_svg = ET.Element("text", {
            "x": str(label_x), "y": str(label_y),
            "class": "connection-label",
            "style": "paint-order: stroke; stroke: white; stroke-width: 2.5px; stroke-opacity:0.7;"
        })
        vlan_text_svg.text = f"VLAN {vlan_id_str}"
        conn_group_svg.append(vlan_text_svg)

    svg_diagram.add_element(conn_group_svg)

