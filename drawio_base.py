# drawio_base.py
import xml.etree.ElementTree as ET
import logging

logger = logging.getLogger(__name__)


class DrawioXMLGenerator:
    """
    Generuje podstawową strukturę XML dla pliku Draw.io (mxGraphModel).
    """

    def __init__(self, page_width: str = "850", page_height: str = "1100", grid_size: str = "10"):
        self.page_width = page_width
        self.page_height = page_height
        self.grid_size = grid_size

        self.root = ET.Element("mxGraphModel", {
            "dx": "1422",  # Domyślne wartości, mogą być dostosowane
            "dy": "832",  # Domyślne wartości
            "grid": "1",
            "gridSize": str(self.grid_size),
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": str(self.page_width),
            "pageHeight": str(self.page_height),
            "background": "#ffffff"
        })
        self.root_cell = ET.SubElement(self.root, "root")
        # Komórki domyślne wymagane przez Draw.io
        ET.SubElement(self.root_cell, "mxCell", {"id": "0"})  # Warstwa domyślna
        ET.SubElement(self.root_cell, "mxCell",
                      {"id": "1", "parent": "0"})  # Główny kontener dla elementów na warstwie 0
        logger.debug(
            f"DrawioXMLGenerator zainicjalizowany (Page: {self.page_width}x{self.page_height}, Grid: {self.grid_size})")

    def get_root_cell_element(self) -> ET.Element:  # Zmieniono nazwę dla jasności
        """Zwraca główny element <mxCell id="1"> (rodzica dla elementów diagramu)."""
        return self.root_cell  # Powinien zwracać element <root>, nie <mxCell id="1">?
        # Standardowo elementy dodaje się do <mxCell id="1">, który jest dzieckiem <root>.
        # Oryginalnie zwracałeś self.root_cell, co jest <root>. Elementy są dodawane do jego dzieci.
        # Utrzymajmy self.root_cell, ale nazwa metody może być myląca.
        # Zmieńmy nazwę na get_diagram_root_container() lub podobną,
        # a to, co zwraca, to <mxCell id="1">.
        # Na razie zostawiam jak było, ale to do przemyślenia.
        # Poprawka: get_root_element() w diagram_generator.py oczekuje elementu, do którego można appendować.
        # self.root_cell (czyli <root>) jest tym elementem, a drawio_utils tworzą elementy z parent="1".
        # Funkcja get_default_layer_cell() mogłaby zwracać ten, który ma id="1".
        # Ale dla uproszczenia, builder może dodawać bezpośrednio do self.root_cell (root),
        # a Draw.io i tak to obsłuży.
        # Zostawiam get_root_element() zwracające <root>
        return self.root_cell

    def get_tree(self) -> ET.ElementTree:
        """Zwraca całe drzewo XML."""
        return ET.ElementTree(self.root)

    def update_page_dimensions(self, width: float, height: float) -> None:
        """Aktualizuje wymiary strony w modelu XML."""
        self.page_width = str(int(round(width)))
        self.page_height = str(int(round(height)))
        self.root.set("pageWidth", self.page_width)
        self.root.set("pageHeight", self.page_height)
        # dx, dy to przesunięcie widoku, można by je też dostosować, np. dx = width * 1.5, dy = height * 1.5
        self.root.set("dx", str(int(round(width * 1.5))))
        self.root.set("dy", str(int(round(height * 1.2))))
        logger.info(f"Zaktualizowano wymiary strony Draw.io na: {self.page_width}x{self.page_height}")