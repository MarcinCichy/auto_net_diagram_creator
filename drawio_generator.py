# drawio_generator.py
import xml.etree.ElementTree as ET
import html


class DrawioXMLGenerator:
    def __init__(self):
        # Podstawowa struktura pliku Draw.io
        self.drawio_root = ET.Element("mxfile", {"host": "app.diagrams.net", "type": "device"})
        self.diagram = ET.SubElement(self.drawio_root, "diagram", {"id": "diagram_1", "name": "Network Topology"})
        self.mxGraphModel = ET.SubElement(self.diagram, "mxGraphModel", {
            "dx": "1422", "dy": "832", "grid": "1", "gridSize": "10", "guides": "1",
            "tooltips": "1", "connect": "1", "arrows": "1", "fold": "1", "page": "1",
            "pageScale": "1", "pageWidth": "1600", "pageHeight": "1200",  # Zwiększony rozmiar strony
            "math": "0", "shadow": "0"
        })
        self.root_cell = ET.SubElement(self.mxGraphModel, "root")
        # Domyślne komórki warstwy 0 i 1
        ET.SubElement(self.root_cell, "mxCell", {"id": "0"})
        ET.SubElement(self.root_cell, "mxCell", {"id": "1", "parent": "0"})

        self.node_counter = 0  # Zmieniono nazwę dla jasności
        self.edge_counter = 0

    def _get_unique_id(self, prefix):
        self.node_counter += 1
        return f"{prefix}_{self.node_counter}"

    def _get_unique_edge_id(self):
        self.edge_counter += 1
        return f"edge_{self.edge_counter}"

    # def add_device(self, label, x, y, width=160, height=60, style=None, device_type='switch'):
    #     """Dodaje urządzenie (węzeł) do diagramu."""
    #     unique_id = self._get_unique_id("device")
    #
    #     # Podstawowy styl dla przełącznika/routera
    #     default_style = (
    #         "shape=rectangle;rounded=1;whiteSpace=wrap;html=1;arcSize=2;"
    #         "strokeColor=#4F81BD;fillColor=#DAE8FC;shadow=1;"
    #     )
    #     # Można dodać style dla różnych typów urządzeń
    #     # if device_type == 'server': default_style = "..."
    #
    #     final_style = style if style else default_style
    #
    #     cell = ET.SubElement(
    #         self.root_cell,
    #         "mxCell",
    #         {
    #             "id": unique_id,
    #             "value": html.escape(label),  # Użyj html.escape dla bezpieczeństwa
    #             "style": final_style,
    #             "vertex": "1",
    #             "parent": "1"  # Dodajemy do warstwy 1
    #         }
    #     )
    #     ET.SubElement(
    #         cell,
    #         "mxGeometry",
    #         {
    #             "x": str(x), "y": str(y),
    #             "width": str(width), "height": str(height),
    #             "as": "geometry"
    #         }
    #     )
    #     return unique_id

    def add_switch_group(self, label, x, y, width=300, height=100):
        """
        Dodaje 'grupę' reprezentującą przełącznik, wewnątrz której będą porty.
        Zwraca ID tej grupy, aby móc do niej dodawać porty jako dzieci.
        """
        group_id = self._get_unique_id("group")

        # Styl grupy – 'group;container=1...' sprawi, że Draw.io nie będzie jej usuwać przy wczytaniu
        group_style = (
            "group;container=1;collapsible=0;recursiveResize=0;childLayout=horizontal;"
            "strokeColor=#4F81BD;fillColor=#DAE8FC;shadow=1;"
        )

        cell = ET.SubElement(
            self.root_cell,
            "mxCell",
            {
                "id": group_id,
                "value": html.escape(label),
                "style": group_style,
                "vertex": "1",
                "parent": "1"  # Należy do warstwy 1
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
        return group_id

    def add_connection(self, source_id, target_id, label="", source_label="", target_label=""):
        """
        Dodaje połączenie (krawędź) między dwoma elementami.
        Dodano source_label i target_label dla etykiet portów.
        """
        edge_id = self._get_unique_edge_id()

        # Styl krawędzi - prosta linia bez strzałek, etykiety na końcach
        style = (
            "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
            "endArrow=none;endFill=0;"  # Usunięto strzałki
            "startArrow=none;startFill=0;"
            "strokeWidth=2;strokeColor=#6c6c6c;"  # Grubsza, szara linia
            "labelBackgroundColor=#ffffff;"  # Białe tło dla etykiet
        )

        # Tworzenie wartości z etykietami końcowymi
        # Draw.io używa atrybutów 'exitX', 'exitY', 'entryX', 'entryY' do określenia punktów połączenia
        # Etykiety można dodać jako część 'style' lub jako osobną komórkę (bardziej skomplikowane)
        # Prostsze podejście: dodajemy je do głównej etykiety krawędzi (value)
        # value = html.escape(label) # Główna etykieta (np. prędkość) - rzadko używana tutaj

        edge = ET.SubElement(
            self.root_cell,
            "mxCell",
            {
                "id": edge_id,
                "value": "",  # Główna etykieta pusta
                "style": style,
                "edge": "1",
                "parent": "1",
                "source": source_id,
                "target": target_id
            }
        )
        geom = ET.SubElement(edge, "mxGeometry", {"relative": "1", "as": "geometry"})

        # Dodanie etykiet końcowych jako osobne komórki "przyklejone" do krawędzi
        # Etykieta dla źródła (source_label)
        if source_label:
            source_lbl_id = self._get_unique_id("label")
            lbl_cell = ET.SubElement(self.root_cell, "mxCell", {
                "id": source_lbl_id,
                "value": html.escape(source_label),
                "style": "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];labelBackgroundColor=#FFFFFF;",
                "vertex": "1",  # Tak, etykieta krawędzi jest wierzchołkiem
                "connectable": "0",  # Nie można się do niej podłączyć
                "parent": edge_id  # Rodzicem jest krawędź
            })
            lbl_geom = ET.SubElement(lbl_cell, "mxGeometry", {"relative": "1", "as": "geometry"})
            # Pozycjonowanie etykiety blisko punktu źródłowego (-1 oznacza punkt źródłowy)
            ET.SubElement(lbl_geom, "mxPoint", {"as": "offset", "x": "0", "y": "10"})  # Mały offset Y
            lbl_cell.set("geometry", lbl_geom.get("as"))  # Poprawka dla Draw.io
            lbl_cell.find("mxGeometry").set("x", "-1")  # Ustawienie punktu odniesienia na źródło

        # Etykieta dla celu (target_label)
        if target_label:
            target_lbl_id = self._get_unique_id("label")
            lbl_cell = ET.SubElement(self.root_cell, "mxCell", {
                "id": target_lbl_id,
                "value": html.escape(target_label),
                "style": "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];labelBackgroundColor=#FFFFFF;",
                "vertex": "1",
                "connectable": "0",
                "parent": edge_id
            })
            lbl_geom = ET.SubElement(lbl_cell, "mxGeometry", {"relative": "1", "as": "geometry"})
            # Pozycjonowanie etykiety blisko punktu docelowego (1 oznacza punkt docelowy)
            ET.SubElement(lbl_geom, "mxPoint", {"as": "offset", "x": "0", "y": "-10"})  # Mały offset Y w górę
            lbl_cell.set("geometry", lbl_geom.get("as"))
            lbl_cell.find("mxGeometry").set("x", "1")  # Ustawienie punktu odniesienia na cel

        return edge_id

    def to_string(self):
        """Zwraca kompletny XML jako string."""
        # Użyj ET.indent dla ładniejszego formatowania XML (Python 3.9+)
        try:
            ET.indent(self.drawio_root, space="\t", level=0)
        except AttributeError:
            # ET.indent nie jest dostępne w starszych wersjach Pythona
            pass

        # Dodaj deklarację XML
        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml_str += ET.tostring(self.drawio_root, encoding="unicode", method="xml")
        # Zastąp standardowe encje HTML, których Draw.io może nie lubić w wartościach etykiet
        xml_str = xml_str.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')

        # Draw.io oczekuje, że zawartość diagramu będzie w CDATA, ale ElementTree tego nie robi łatwo.
        # Zazwyczaj działa bez CDATA, ale jeśli są problemy, można spróbować to obejść ręcznie.
        # Na razie zostawiamy bez CDATA.

        return xml_str