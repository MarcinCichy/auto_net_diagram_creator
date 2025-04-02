import xml.etree.ElementTree as ET

class DrawioXMLGenerator:
    def __init__(self):
        self.root = ET.Element("mxGraphModel", {
            "dx": "1422",
            "dy": "832",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": "850",
            "pageHeight": "1100"
        })
        self.root_cell = ET.SubElement(self.root, "root")
        self._create_default_cells()

        self.device_counter = 1
        self.port_counter = 1
        self.edge_counter = 1

    def _create_default_cells(self):
        ET.SubElement(self.root_cell, "mxCell", {"id": "0"})
        ET.SubElement(self.root_cell, "mxCell", {"id": "1", "parent": "0"})

    def add_device(self, label, x, y, width=120, height=60):
        unique_id = f"device_{self.device_counter}"
        self.device_counter += 1
        cell = ET.SubElement(
            self.root_cell,
            "mxCell",
            {
                "id": unique_id,
                "value": label,
                "style": "shape=rectangle;whiteSpace=wrap;html=1;fillColor=#dae8fc;",
                "vertex": "1",
                "parent": "1"
            }
        )
        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": str(x),
                "y": str(y),
                "width": str(width),
                "height": str(height),
                "as": "geometry"
            }
        )
        return unique_id

    def add_port(self, label, x, y, used=False):
        unique_id = f"port_{self.port_counter}"
        self.port_counter += 1
        fill_color = "#b7e1cd" if used else "#e2e2e2"  # zielony jeśli port używany
        cell = ET.SubElement(
            self.root_cell,
            "mxCell",
            {
                "id": unique_id,
                "value": label,
                "style": f"shape=rectangle;whiteSpace=wrap;html=1;fillColor={fill_color};",
                "vertex": "1",
                "parent": "1"
            }
        )
        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": str(x),
                "y": str(y),
                "width": "40",
                "height": "40",
                "as": "geometry"
            }
        )
        return unique_id

    def add_connection(self, source_id, target_id, label=""):
        edge_id = f"edge_{self.edge_counter}"
        self.edge_counter += 1
        edge = ET.SubElement(
            self.root_cell,
            "mxCell",
            {
                "id": edge_id,
                "value": label,
                "style": "endArrow=block;",
                "edge": "1",
                "parent": "1",
                "source": source_id,
                "target": target_id
            }
        )
        ET.SubElement(
            edge,
            "mxGeometry",
            {
                "relative": "1",
                "as": "geometry"
            }
        )
        return edge_id

    def to_string(self):
        return ET.tostring(self.root, encoding="utf-8", method="xml").decode("utf-8")
