import xml.etree.ElementTree as ET
import re
import copy
import config

def modify_style(style_str, new_styles):
    """Parses a draw.io style string, updates it, and returns the new string."""
    styles = {}
    if style_str:
        parts = style_str.strip(';').split(';')
        for part in parts:
            if '=' in part:
                key, value = part.split('=', 1)
                styles[key.strip()] = value.strip()

    styles.update(new_styles)

    # Clean up potential 'fillColor=none' or empty values
    if 'fillColor' in styles and (not styles['fillColor'] or styles['fillColor'].lower() == 'none'):
        del styles['fillColor']

    return ";".join(f"{k}={v}" for k, v in styles.items() if v) + ";"

def parse_port_number_from_string(input_string, regex_pattern):
    """
    Extracts the port number from a string (like ifName, ifAlias)
    using the provided regex pattern.
    Uses re.IGNORECASE to handle different letter cases.
    """
    if not input_string or not regex_pattern:
        return None
    match = re.search(regex_pattern, input_string, re.IGNORECASE)
    if match:
        return match.groups()[-1] if match.groups() else match.group(0)
    else:
        print(f"DEBUG: No regex match for port extraction in '{input_string}' using pattern '{regex_pattern}'")
    return None

class DrawioTemplate:
    """Loads and analyzes the switch_template.drawio file."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.tree = None
        self.template_root = None
        self.group_element = None
        self.child_elements = []
        self.port_elements = {}
        self.dimensions = {'width': 0, 'height': 0}
        self.base_coords = {'x': 0, 'y': 0}

        self._load_and_parse()

    def _load_and_parse(self):
        try:
            self.tree = ET.parse(self.filepath)
            self.template_root = self.tree.find('.//diagram/mxGraphModel/root')
            if self.template_root is None:
                raise ValueError("Could not find <root> element in the template file.")

            for cell in self.template_root.findall("./mxCell"):
                style = cell.get('style', '')
                if 'group;' in style:
                    self.group_element = cell
                    print(f"INFO: Found group element using style check: id='{cell.get('id')}'")
                    break

            if self.group_element is None:
                print("WARN: No <mxCell> with style='group;...' found. Assuming all elements belong to the template.")
                self.child_elements = self.template_root.findall("./mxCell[@id!='0'][@id!='1']")
                if not self.child_elements:
                    raise ValueError("Template contains no usable mxCell elements.")
                self._calculate_bounds_from_elements(self.child_elements)
            else:
                group_id = self.group_element.get('id')
                self.child_elements = self.template_root.findall(f"./mxCell[@parent='{group_id}']")
                print(f"INFO: Found group element with id '{group_id}' and {len(self.child_elements)} child elements.")
                geometry = self.group_element.find("./mxGeometry")
                if geometry is not None:
                    self.dimensions['width'] = float(geometry.get('width', 0))
                    self.dimensions['height'] = float(geometry.get('height', 0))
                    self.base_coords['x'] = float(geometry.get('x', 0))
                    self.base_coords['y'] = float(geometry.get('y', 0))
                    print(f"INFO: Group dimensions: W={self.dimensions['width']}, H={self.dimensions['height']}. Base coords: X={self.base_coords['x']}, Y={self.base_coords['y']}")
                else:
                    print("WARN: Group element has no <mxGeometry>. Calculating bounds from children.")
                    self._calculate_bounds_from_elements(self.child_elements)

            for elem in self.child_elements:
                style = elem.get('style', '')
                value = elem.get('value')
                if value and value.isdigit() and 'rounded=0;' in style:
                    self.port_elements[value] = elem

            if not self.port_elements:
                print("WARN: No port elements identified in the template based on numeric value and 'rounded=0;'.")
        except FileNotFoundError:
            raise FileNotFoundError(f"Template file not found: {self.filepath}")
        except ET.ParseError as e:
            raise ValueError(f"Error parsing template XML file {self.filepath}: {e}")

    def _calculate_bounds_from_elements(self, elements):
        min_x, min_y = float('inf'), float('inf')
        max_x, max_y = float('-inf'), float('-inf')
        for elem in elements:
            geometry = elem.find("./mxGeometry")
            if geometry is not None:
                x = float(geometry.get('x', 0))
                y = float(geometry.get('y', 0))
                w = float(geometry.get('width', 0))
                h = float(geometry.get('height', 0))
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x + w)
                max_y = max(max_y, y + h)
        if min_x == float('inf'):
            min_x = 0
        if min_y == float('inf'):
            min_y = 0

        self.base_coords = {'x': min_x, 'y': min_y}
        self.dimensions = {'width': max_x - min_x, 'height': max_y - min_y}
        print(f"INFO: Calculated bounds: W={self.dimensions['width']}, H={self.dimensions['height']}. Base coords: X={self.base_coords['x']}, Y={self.base_coords['y']}")

    def get_template_elements(self):
        elements = []
        if self.group_element is not None:
            elements.append(self.group_element)
        elements.extend(self.child_elements)
        return elements

    def get_port_element_template(self, port_number_value):
        return self.port_elements.get(str(port_number_value))

    def get_dimensions(self):
        return self.dimensions['width'], self.dimensions['height']

    def get_base_coords(self):
        return self.base_coords['x'], self.base_coords['y']
