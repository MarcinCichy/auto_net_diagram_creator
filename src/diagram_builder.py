# src/diagram_builder.py
import xml.etree.ElementTree as ET
import copy
import math
import uuid
from . import config, drawio_utils # Relative imports

class DiagramBuilder:
    """Builds the draw.io diagram XML structure."""

    def __init__(self, template: drawio_utils.DrawioTemplate):
        self.template = template
        self.mxfile, self.root = self._create_diagram_base()
        self.current_x = config.START_X
        self.current_y = config.START_Y
        self.switch_count = 0
        self.max_x_in_row = 0 # Track max width for page sizing

        # Get template dimensions for layout
        self.template_width, self.template_height = self.template.get_dimensions()
        if self.template_width == 0 or self.template_height == 0:
             print("WARN: Template dimensions are zero. Layout might be incorrect. Using defaults.")
             self.template_width = max(self.template_width, 200) # Default size
             self.template_height = max(self.template_height, 60)

    def _create_diagram_base(self):
        """Creates the basic draw.io XML file structure."""
        mxfile = ET.Element("mxfile", host="PythonScript", modified="...", agent="...", etag=str(uuid.uuid4()), version="1.0", type="device")
        diagram = ET.SubElement(mxfile, "diagram", id="diagram-1", name="Network Map")
        # Initial page size, will be adjusted later
        page_width = str(config.START_X * 2 + self.template_width * config.SWITCHES_PER_ROW + config.SWITCH_SPACING_X * (config.SWITCHES_PER_ROW -1)) if config.SWITCHES_PER_ROW > 0 else "827"
        page_height = str(config.START_Y * 2 + self.template_height) # Initial height for one row
        mxGraphModel = ET.SubElement(diagram, "mxGraphModel", dx="1500", dy="1000", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth=page_width, pageHeight=page_height, math="0", shadow="0")
        root = ET.SubElement(mxGraphModel, "root")
        ET.SubElement(root, "mxCell", id="0") # Root cell
        ET.SubElement(root, "mxCell", id="1", parent="0") # Default layer
        return mxfile, root

    def _get_unique_id(self):
        """Generates a unique ID for draw.io elements."""
        # Using UUID ensures uniqueness even if script runs multiple times partially
        return str(uuid.uuid4())

    def _map_ports_to_template(self, device_ports_data):
        """
        Maps LibreNMS port data to the template's port numbers ('1', '2', etc.).
        Returns a dictionary: {'template_port_number': {'status': 'up'/'down', 'raw_data': {...}}}
        """
        mapped_ports = {}
        identifier_field = config.get_port_identifier_field()
        regex_pattern = config.get_port_number_regex() if identifier_field == 'ifName' else None

        if not device_ports_data:
             return mapped_ports

        for port in device_ports_data:
            port_id_value = port.get(identifier_field)
            template_port_num = None

            if identifier_field == 'ifName' and regex_pattern:
                template_port_num = drawio_utils.parse_port_number_from_string(port_id_value, regex_pattern)
            elif port_id_value and port_id_value.isdigit(): # If using ifAlias/ifDescr and it's already a number
                 template_port_num = port_id_value
            else:
                 # Attempt direct match if value is numeric (e.g. ifAlias='5')
                 if port_id_value and port_id_value.isdigit():
                     template_port_num = port_id_value
                 # else:
                 #    print(f"DEBUG: Could not determine template port number for port {identifier_field}={port_id_value}")


            if template_port_num:
                 # Determine status
                 status = 'down' # Default
                 # LibreNMS: up, down, testing, dormant, notPresent, lowerLayerDown, administratively down
                 oper_status = port.get('ifOperStatus', 'down').lower()
                 admin_status = port.get('ifAdminStatus', 'up').lower()

                 if admin_status == 'down':
                     status = 'down' # Treat admin down as down for coloring
                 elif oper_status in ['up', 'testing']:
                     status = 'up'
                 # else remains 'down'

                 mapped_ports[str(template_port_num)] = {'status': status, 'raw_data': port}
                 # print(f"DEBUG: Mapped port {identifier_field}='{port_id_value}' to template number '{template_port_num}', Status: {status}")


        return mapped_ports


    def add_switch(self, device_name, device_ports_data):
        """Adds a switch instance to the diagram."""
        print(f"INFO: Adding switch '{device_name}' to diagram...")

        # Calculate position
        col_index = self.switch_count % config.SWITCHES_PER_ROW
        row_index = self.switch_count // config.SWITCHES_PER_ROW
        pos_x = config.START_X + col_index * (self.template_width + config.SWITCH_SPACING_X)
        pos_y = config.START_Y + row_index * (self.template_height + config.SWITCH_SPACING_Y)
        self.max_x_in_row = max(self.max_x_in_row, pos_x + self.template_width)

        print(f"INFO: Position calculated: X={pos_x}, Y={pos_y} (Switch {self.switch_count + 1})")


        # Map LibreNMS ports to template port numbers
        mapped_ports = self._map_ports_to_template(device_ports_data)
        if not mapped_ports:
             print(f"WARN: No ports could be mapped for device {device_name} based on config.")

        # --- Clone and modify template elements ---
        template_elements = self.template.get_template_elements()
        id_map = {} # Maps old template IDs to new unique IDs
        elements_to_add = []

        # 1. Clone group element (if it exists) and set its new position/ID
        new_group_id = None
        if self.template.group_element is not None:
             group_template = self.template.group_element
             new_group_element = copy.deepcopy(group_template)
             old_group_id = group_template.get('id')
             new_group_id = self._get_unique_id()
             id_map[old_group_id] = new_group_id

             new_group_element.set('id', new_group_id)
             new_group_element.set('parent', '1') # Parent to default layer

             # Set the group's position
             geometry = new_group_element.find("./mxGeometry")
             if geometry is None:
                 geometry = ET.SubElement(new_group_element, "mxGeometry") # Pass tag positionally
             geometry.set('x', str(pos_x))
             geometry.set('y', str(pos_y))
             geometry.set('width', str(self.template_width)) # Ensure size is set
             geometry.set('height', str(self.template_height))
             geometry.set('as', 'geometry')
             if 'relative' in geometry.attrib: # Remove relative if present
                  del geometry.attrib['relative']

             elements_to_add.append(new_group_element)
             print(f"DEBUG: Cloned group element. Old ID: {old_group_id}, New ID: {new_group_id}")
        else:
             # No group element, children will be parented to '1' and positioned absolutely
             print("DEBUG: No group element in template. Positioning children absolutely.")


        # 2. Clone child elements
        for child_template in self.template.child_elements:
            new_child = copy.deepcopy(child_template)
            old_child_id = child_template.get('id')
            new_child_id = self._get_unique_id()
            id_map[old_child_id] = new_child_id

            new_child.set('id', new_child_id)

            # Set parent: either the new group or the default layer '1'
            new_child.set('parent', new_group_id if new_group_id else '1')

            # Adjust geometry
            geometry = new_child.find("./mxGeometry")
            if geometry is not None:
                 # If there's a group, the child's geometry (x,y) should remain relative to the group's origin (0,0)
                 # If there's NO group, we need to calculate absolute position.
                 if not new_group_id:
                      original_x = float(geometry.get('x', 0))
                      original_y = float(geometry.get('y', 0))
                      # Calculate absolute position relative to template's base and new switch position
                      template_base_x, template_base_y = self.template.get_base_coords()
                      abs_x = pos_x + (original_x - template_base_x)
                      abs_y = pos_y + (original_y - template_base_y)
                      geometry.set('x', str(abs_x))
                      geometry.set('y', str(abs_y))
                      if 'relative' in geometry.attrib: # Ensure it's absolute
                           del geometry.attrib['relative']
                 # else: Child coords are relative to the parent group, keep original x,y from template


                # --- Color the port if applicable ---
                port_number_value = new_child.get('value')
                is_port_element = (port_number_value and port_number_value.isdigit() and
                                   self.template.get_port_element_template(port_number_value) is not None)

                if is_port_element:
                    port_info = mapped_ports.get(port_number_value)
                    fill_color = config.PORT_DEFAULT_COLOR # Default gray
                    if port_info:
                        if port_info['status'] == 'up':
                            fill_color = config.PORT_UP_COLOR
                        elif port_info['status'] == 'down':
                            fill_color = config.PORT_DOWN_COLOR
                        # print(f"DEBUG: Coloring port {port_number_value}: Status={port_info['status']}, Color={fill_color}")

                    else:
                         # Port number from template not found in mapped data
                         # print(f"DEBUG: Port {port_number_value} not found in LibreNMS data for {device_name}. Using default color.")
                         pass # Use default color


                    style = new_child.get('style', '')
                    new_style = drawio_utils.modify_style(style, {'fillColor': fill_color})
                    new_child.set('style', new_style)

            elements_to_add.append(new_child)

        # 3. Add device label (optional)
        if config.ADD_DEVICE_LABEL:
             label_id = self._get_unique_id()
             # --- KOREKTA TUTAJ ---
             # Przekazujemy "mxCell" jako pierwszy argument pozycyjny
             label_cell = ET.Element("mxCell", id=label_id, parent="1",
                                      value=device_name,
                                      style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=bottom;whiteSpace=wrap;rounded=0;")
             # --- KOREKTA TUTAJ ---
             # Przekazujemy "mxGeometry" jako pierwszy argument pozycyjny po 'label_cell'
             label_geom = ET.SubElement(label_cell, "mxGeometry",
                                        x=str(pos_x),
                                        y=str(pos_y - 25), # Position above
                                        width=str(self.template_width),
                                        height="20", As="geometry")
             elements_to_add.append(label_cell)


        # 4. Append all new elements to the diagram root
        for elem in elements_to_add:
            self.root.append(elem)

        self.switch_count += 1


    def _adjust_page_size(self):
         """Adjusts the diagram page size based on content."""
         if self.switch_count == 0: return

         num_rows = math.ceil(self.switch_count / config.SWITCHES_PER_ROW) if config.SWITCHES_PER_ROW > 0 else 1
         # Width based on the widest row
         # Ensure we have at least the width of one switch if only one column
         width_per_switch = self.template_width + config.SWITCH_SPACING_X
         effective_cols = min(self.switch_count, config.SWITCHES_PER_ROW) if config.SWITCHES_PER_ROW > 0 else 1
         page_width = config.START_X + effective_cols * width_per_switch - config.SWITCH_SPACING_X + config.START_X # Adjust calculation

         # Height based on number of rows
         page_height = config.START_Y + num_rows * (self.template_height + config.SWITCH_SPACING_Y) - config.SWITCH_SPACING_Y + config.START_Y # Adjust calculation


         diagram = self.mxfile.find('.//diagram')
         if diagram is not None:
            mxGraphModel = diagram.find('./mxGraphModel')
            if mxGraphModel is not None:
                mxGraphModel.set('pageWidth', str(int(page_width)))
                mxGraphModel.set('pageHeight', str(int(page_height)))
                # Adjust initial view center? Optional.
                mxGraphModel.set('dx', str(int(page_width * 0.6))) # Adjust dx/dy for better initial view
                mxGraphModel.set('dy', str(int(page_height * 0.6)))
                print(f"INFO: Final page size set: Width={int(page_width)}, Height={int(page_height)}")


    def save_diagram(self, filepath):
        """Saves the completed diagram XML to a file."""
        if self.switch_count > 0:
             self._adjust_page_size() # Adjust page size before saving
        else:
             print("INFO: No switches were added to the diagram.")

        try:
            # Pretty print helps debugging but adds whitespace draw.io might not like
            # ET.indent(self.mxfile, space="\t", level=0) # Optional pretty printing
            xml_string = ET.tostring(self.mxfile, encoding='utf-8', method='xml')
            xml_declaration = b'<?xml version="1.0" encoding="UTF-8"?>\n'

            with open(filepath, 'wb') as f:
                f.write(xml_declaration)
                f.write(xml_string)
            print(f"\nSUCCESS: Diagram saved to: {filepath}")
        except IOError as e:
            print(f"ERROR: Could not write diagram file {filepath}: {e}")
        except Exception as e:
            print(f"ERROR: An unexpected error occurred during diagram saving: {e}")