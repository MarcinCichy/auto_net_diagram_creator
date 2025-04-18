# drawio_base.py
import xml.etree.ElementTree as ET

class DrawioXMLGenerator:
    """
    Generuje podstawową strukturę XML dla pliku Draw.io (mxGraphModel).
    """
    def __init__(self, page_width="850", page_height="1100", grid_size="10"):
        self.root = ET.Element("mxGraphModel", {
            "dx": "1422",
            "dy": "832",
            "grid": "1",
            "gridSize": str(grid_size),
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": str(page_width),
            "pageHeight": str(page_height),
            "background": "#ffffff" # Dodano białe tło
        })
        self.root_cell = ET.SubElement(self.root, "root")
        # Komórki domyślne
        ET.SubElement(self.root_cell, "mxCell", {"id": "0"})
        ET.SubElement(self.root_cell, "mxCell", {"id": "1", "parent": "0"}) # Domyślna warstwa

    def get_root_element(self) -> ET.Element:
        """Zwraca główny element <root> diagramu."""
        return self.root_cell

    def get_tree(self) -> ET.ElementTree:
        """Zwraca całe drzewo XML."""
        return ET.ElementTree(self.root)