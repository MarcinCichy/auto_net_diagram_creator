# drawio_generator.py

import xml.etree.ElementTree as ET

class DrawioXMLGenerator:
    def __init__(self):
        # Tworzymy nowy dokument MXGraphModel – wszystkie mxCell zostaną dodane płasko do <root>
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

    def _create_default_cells(self):
        # Zwykle draw.io oczekuje, że <root> będzie miało dwa elementy: id="0" i id="1"
        ET.SubElement(self.root_cell, "mxCell", {"id": "0"})
        ET.SubElement(self.root_cell, "mxCell", {"id": "1", "parent": "0"})

    def to_string(self):
        return ET.tostring(self.root, encoding="utf-8", method="xml").decode("utf-8")
