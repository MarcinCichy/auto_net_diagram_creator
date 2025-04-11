import xml.etree.ElementTree as ET
import copy
import math
import uuid
import config, drawio_utils
import json

class DiagramBuilder:
    """Builds the draw.io diagram XML structure."""

    def __init__(self, template: drawio_utils.DrawioTemplate):
        self.template = template
        self.template_width, self.template_height = self.template.get_dimensions()

        if self.template_width == 0 or self.template_height == 0:
            print("WARN: Template dimensions are zero. Layout might be incorrect. Using defaults.")
            self.template_width = max(self.template_width, 200)
            self.template_height = max(self.template_height, 60)
            print(f"INFO: Using default dimensions: W={self.template_width}, H={self.template_height}")
        else:
            print(f"INFO: Template dimensions loaded: W={self.template_width}, H={self.template_height}")

        self.mxfile, self.root = self._create_diagram_base()
        self.current_x = config.START_X
        self.current_y = config.START_Y
        self.switch_count = 0
        self.max_x_in_row = 0

    def _create_diagram_base(self):
        mxfile = ET.Element("mxfile", host="PythonScript", modified="...", agent="...", etag=str(uuid.uuid4()), version="1.0", type="device")
        diagram = ET.SubElement(mxfile, "diagram", id="diagram-1", name="Network Map")
        page_width = str(config.START_X * 2 + self.template_width * config.SWITCHES_PER_ROW + config.SWITCH_SPACING_X * (config.SWITCHES_PER_ROW -1)) if config.SWITCHES_PER_ROW > 0 else "827"
        page_height = str(config.START_Y * 2 + self.template_height)
        mxGraphModel = ET.SubElement(diagram, "mxGraphModel", dx="1500", dy="1000", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth=page_width, pageHeight=page_height, math="0", shadow="0")
        root = ET.SubElement(mxGraphModel, "root")
        ET.SubElement(root, "mxCell", id="0")
        ET.SubElement(root, "mxCell", id="1", parent="0")
        return mxfile, root

    def _get_unique_id(self):
        return str(uuid.uuid4())

    def _map_ports_to_template(self, device_ports_data):
        mapped_ports = {}
        identifier_field = config.get_port_identifier_field()
        regex_pattern = config.get_port_number_regex() if identifier_field == 'ifName' else None

        if not device_ports_data:
            return mapped_ports

        for port in device_ports_data:
            print("DEBUG FULL PORT DATA:\n", json.dumps(port, indent=2))
            port_id_value = port.get(identifier_field)
            print(f"DEBUG: Port {port_id_value} — ifOperStatus={port.get('ifOperStatus')}")

            template_port_num = None

            if identifier_field == 'ifName' and regex_pattern:
                template_port_num = drawio_utils.parse_port_number_from_string(port_id_value, regex_pattern)
            elif port_id_value and port_id_value.isdigit():
                template_port_num = port_id_value

            if template_port_num:
                status = 'down'
                oper_status = port.get('ifOperStatus', 'down').lower()

                if oper_status in ['up', 'testing']:
                    status = 'up'

                mapped_ports[str(template_port_num)] = {'status': status, 'raw_data': port}

        return mapped_ports

    def add_switch(self, device_name, device_ports_data):
        print(f"INFO: Adding switch '{device_name}' to diagram...")
        col_index = self.switch_count % config.SWITCHES_PER_ROW
        row_index = self.switch_count // config.SWITCHES_PER_ROW
        pos_x = config.START_X + col_index * (self.template_width + config.SWITCH_SPACING_X)
        pos_y = config.START_Y + row_index * (self.template_height + config.SWITCH_SPACING_Y)
        self.max_x_in_row = max(self.max_x_in_row, pos_x + self.template_width)
        print(f"INFO: Position calculated: X={pos_x}, Y={pos_y} (Switch {self.switch_count + 1})")

        mapped_ports = self._map_ports_to_template(device_ports_data)
        print(f"DEBUG: Mapped ports for {device_name}: {list(mapped_ports.keys())}")
        if not mapped_ports:
            print(f"WARN: No ports could be mapped for device {device_name} based on config.")

        template_elements = self.template.get_template_elements()
        id_map = {}
        elements_to_add = []

        new_group_id = None
        if self.template.group_element is not None:
            group_template = self.template.group_element
            new_group_element = copy.deepcopy(group_template)
            old_group_id = group_template.get('id')
            new_group_id = self._get_unique_id()
            id_map[old_group_id] = new_group_id

            new_group_element.set('id', new_group_id)
            new_group_element.set('parent', '1')

            geometry = new_group_element.find("./mxGeometry")
            if geometry is None:
                geometry = ET.SubElement(new_group_element, "mxGeometry")
            geometry.set('x', str(pos_x))
            geometry.set('y', str(pos_y))
            geometry.set('width', str(self.template_width))
            geometry.set('height', str(self.template_height))
            geometry.set('as', 'geometry')
            if 'relative' in geometry.attrib:
                del geometry.attrib['relative']

            elements_to_add.append(new_group_element)
        else:
            print("DEBUG: No group element in template. Positioning children absolutely.")

        for child_template in self.template.child_elements:
            new_child = copy.deepcopy(child_template)
            old_child_id = child_template.get('id')
            new_child_id = self._get_unique_id()
            id_map[old_child_id] = new_child_id

            new_child.set('id', new_child_id)
            new_child.set('parent', new_group_id if new_group_id else '1')

            geometry = new_child.find("./mxGeometry")
            if geometry is not None and not new_group_id:
                original_x = float(geometry.get('x', 0))
                original_y = float(geometry.get('y', 0))
                template_base_x, template_base_y = self.template.get_base_coords()
                abs_x = pos_x + (original_x - template_base_x)
                abs_y = pos_y + (original_y - template_base_y)
                geometry.set('x', str(abs_x))
                geometry.set('y', str(abs_y))
                if 'relative' in geometry.attrib:
                    del geometry.attrib['relative']

            port_number_value = new_child.get('value')
            is_port_element = (port_number_value and port_number_value.isdigit() and self.template.get_port_element_template(port_number_value) is not None)

            if is_port_element:
                port_info = mapped_ports.get(port_number_value)
                fill_color = config.PORT_DEFAULT_COLOR

                if port_info and 'raw_data' in port_info:
                    raw_port_data = port_info['raw_data']
                    oper_status = raw_port_data.get('ifOperStatus', 'down').lower()
                    if oper_status in ['up', 'testing']:
                        fill_color = config.PORT_UP_COLOR
                    else:
                        fill_color = config.PORT_DOWN_COLOR
                else:
                    print(f"WARN: Port {port_number_value} from template not found in API data for {device_name}. Using default color.")

                style = new_child.get('style', '')
                new_style = drawio_utils.modify_style(style, {'fillColor': fill_color})
                new_child.set('style', new_style)

                # Draw line and label
                port_data = mapped_ports.get(port_number_value)
                if port_data:
                    alias = port_data['raw_data'].get('ifAlias', '')
                    port_geometry = new_child.find("./mxGeometry")
                    if port_geometry is not None:
                        x = float(port_geometry.get('x', 0)) + pos_x
                        y = float(port_geometry.get('y', 0)) + pos_y
                        direction = -30 if int(port_number_value) % 2 == 1 else 30

                        line_id = self._get_unique_id()
                        line_cell = ET.Element("mxCell", id=line_id, parent="1", style="endArrow=none;strokeColor=#000000;", edge="1")
                        ET.SubElement(line_cell, "mxGeometry", attrib={
                            'relative': '1',
                            'as': 'geometry',
                            'points': f"{x},{y};{x},{y + direction}"
                        })
                        elements_to_add.append(line_cell)

                        label_id = self._get_unique_id()
                        label_cell = ET.Element("mxCell", id=label_id, value=alias, style="text;html=1;strokeColor=none;fillColor=none;align=center;", vertex="1", parent="1")
                        ET.SubElement(label_cell, "mxGeometry", attrib={
                            'x': str(x - 50),
                            'y': str(y + direction - 10),
                            'width': '100',
                            'height': '20',
                            'as': 'geometry'
                        })
                        elements_to_add.append(label_cell)

            elements_to_add.append(new_child)

        if config.ADD_DEVICE_LABEL:
            label_id = self._get_unique_id()
            label_cell = ET.Element("mxCell", id=label_id, parent="1",
                                     value=device_name,
                                     style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=bottom;whiteSpace=wrap;rounded=0;")
            geometry_attributes = {
                'x': str(pos_x),
                'y': str(pos_y - 25),
                'width': str(self.template_width),
                'height': "20",
                'as': "geometry"
            }
            ET.SubElement(label_cell, "mxGeometry", attrib=geometry_attributes)
            elements_to_add.append(label_cell)

        for elem in elements_to_add:
            self.root.append(elem)

        self.switch_count += 1

    def _adjust_page_size(self):
        if self.switch_count == 0:
            return

        num_rows = math.ceil(self.switch_count / config.SWITCHES_PER_ROW) if config.SWITCHES_PER_ROW > 0 else 1
        width_per_switch = self.template_width + config.SWITCH_SPACING_X
        effective_cols = min(self.switch_count, config.SWITCHES_PER_ROW) if config.SWITCHES_PER_ROW > 0 else 1
        page_width = config.START_X + effective_cols * width_per_switch
        if effective_cols > 0:
            page_width -= config.SWITCH_SPACING_X
        page_width += config.START_X

        page_height = config.START_Y + num_rows * (self.template_height + config.SWITCH_SPACING_Y)
        if num_rows > 0:
            page_height -= config.SWITCH_SPACING_Y
        page_height += config.START_Y

        diagram = self.mxfile.find('.//diagram')
        if diagram is not None:
            mxGraphModel = diagram.find('./mxGraphModel')
            if mxGraphModel is not None:
                mxGraphModel.set('pageWidth', str(int(page_width)))
                mxGraphModel.set('pageHeight', str(int(page_height)))
                mxGraphModel.set('dx', str(int(page_width * 0.6)))
                mxGraphModel.set('dy', str(int(page_height * 0.6)))
                print(f"INFO: Final page size set: Width={int(page_width)}, Height={int(page_height)}")

    def save_diagram(self, filepath):
        if self.switch_count > 0:
            self._adjust_page_size()
        else:
            print("INFO: No switches were added to the diagram.")

        try:
            xml_string = ET.tostring(self.mxfile, encoding='utf-8', method='xml')
            xml_declaration = b'<?xml version="1.0" encoding="UTF-8"?>\n'

            with open(filepath, 'wb') as f:
                f.write(xml_declaration)
                f.write(xml_string)
            print(f"SUCCESS: Diagram saved to: {filepath}")
        except IOError as e:
            print(f"ERROR: Could not write diagram file {filepath}: {e}")
        except Exception as e:
            print(f"ERROR: An unexpected error occurred during diagram saving: {e}")
