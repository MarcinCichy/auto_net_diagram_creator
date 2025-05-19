# drawio_utils.py
import xml.etree.ElementTree as ET
import re  # Nie jest tu używany, można usunąć
import logging
from typing import Optional, List, Tuple, Any, Dict  # Dodano Dict

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE_FILE = "switch.drawio"  # Rozważ czy ta stała jest tu potrzebna, czy w configu/main_app


def load_drawio_template(filepath: str = DEFAULT_TEMPLATE_FILE) -> Optional[ET.ElementTree]:
    """Ładuje szablon Draw.io z pliku XML."""
    logger.debug(f"Próba załadowania szablonu Draw.io z: {filepath}")
    try:
        tree = ET.parse(filepath)
        logger.info(f"✓ Pomyślnie załadowano szablon Draw.io: {filepath}")
        return tree
    except FileNotFoundError:
        logger.error(f"Błąd: Nie znaleziono pliku szablonu Draw.io: {filepath}")
        return None
    except ET.ParseError as e:
        logger.error(f"Błąd parsowania XML szablonu Draw.io '{filepath}': {e}")
        return None
    except Exception as e:
        logger.error(f"Nieoczekiwany błąd przy ładowaniu szablonu Draw.io '{filepath}': {e}", exc_info=True)
        return None


def find_cells(root_element: ET.Element, tag_name: str = "mxCell") -> List[ET.Element]:
    """Znajduje wszystkie komórki o danym tagu (domyślnie mxCell) w obrębie elementu (rekursywnie)."""
    if root_element is None:
        logger.debug(f"find_cells: root_element jest None, zwracam pustą listę.")
        return []
    return root_element.findall(f".//{tag_name}")


def find_cells_by_value(root_element: ET.Element, criteria_func: Any) -> List[ET.Element]:
    """
    Znajduje komórki, których atrybut 'value' spełnia podane kryterium.
    criteria_func: funkcja, która przyjmuje wartość atrybutu 'value' i zwraca True/False.
    """
    if root_element is None:
        logger.debug(f"find_cells_by_value: root_element jest None, zwracam pustą listę.")
        return []
    matching_cells: List[ET.Element] = []
    for cell in find_cells(root_element):  # Używa find_cells, które szuka rekursywnie
        value = cell.get("value", "").strip()
        try:
            if criteria_func(value):
                matching_cells.append(cell)
        except Exception as e:
            logger.warning(f"Błąd podczas wywoływania criteria_func dla wartości '{value}': {e}",
                           exc_info=False)  # Niekoniecznie pełny traceback
            pass  # Ignoruj błędy w funkcji kryterium
    return matching_cells


def find_cell_by_id(root_element: ET.Element, cell_id: str) -> Optional[ET.Element]:
    """Znajduje komórkę o podanym ID w obrębie elementu (rekursywnie)."""
    if root_element is None or not cell_id:
        logger.debug(f"find_cell_by_id: root_element jest None lub cell_id jest puste ('{cell_id}'). Zwracam None.")
        return None
    return root_element.find(f".//mxCell[@id='{cell_id}']")  # XPath dla atrybutu id


def reassign_cell_ids(root_element: ET.Element, suffix: str) -> None:
    """
    Zmienia ID wszystkich komórek mxCell w poddrzewie root_element,
    dodając podany sufiks. Modyfikuje element w miejscu.
    Aktualizuje również atrybuty parent, source, target.
    """
    if root_element is None or not suffix:
        logger.debug(f"reassign_cell_ids: root_element jest None lub suffix jest pusty ('{suffix}'). Brak zmian.")
        return

    id_map: Dict[str, str] = {}
    cells_to_process = find_cells(root_element)
    logger.debug(f"reassign_cell_ids: Przetwarzanie {len(cells_to_process)} komórek z sufiksem '{suffix}'.")

    # 1. Zbuduj mapę stary_id -> nowy_id
    for cell in cells_to_process:
        old_id = cell.get("id")
        if old_id:
            if not old_id.endswith(f"_{suffix}"):  # Unikaj wielokrotnego dodawania sufiksu
                new_id = f"{old_id}_{suffix}"
                id_map[old_id] = new_id
            else:  # ID już ma poprawny sufiks
                id_map[old_id] = old_id  # Dodaj do mapy, aby referencje nadal działały

    # 2. Zaktualizuj atrybuty id, parent, source, target
    for cell in cells_to_process:
        old_id_attr = cell.get("id")
        if old_id_attr and old_id_attr in id_map:
            cell.set("id", id_map[old_id_attr])

        old_parent_attr = cell.get("parent")
        if old_parent_attr and old_parent_attr in id_map:
            cell.set("parent", id_map[old_parent_attr])

        old_source_attr = cell.get("source")
        if old_source_attr and old_source_attr in id_map:
            cell.set("source", id_map[old_source_attr])

        old_target_attr = cell.get("target")
        if old_target_attr and old_target_attr in id_map:
            cell.set("target", id_map[old_target_attr])
    logger.debug(f"reassign_cell_ids: Zakończono zmianę ID.")


def get_bounding_box(element: ET.Element) -> Tuple[float, float, float, float]:
    """Oblicza prostokąt otaczający (bounding box) dla komórek mxCell będących bezpośrednimi dziećmi elementu."""
    min_x, min_y = float('inf'), float('inf')
    max_x_coord, max_y_coord = float('-inf'), float('-inf')  # Przechowuje max X i Y koordynaty, a nie X+W
    has_geometry = False

    # Szukaj tylko w bezpośrednich dzieciach mxCell, które mają geometrię
    for cell in element.findall("./mxCell"):
        geom_element = cell.find("./mxGeometry[@as='geometry']")
        if geom_element is not None:
            try:
                x = float(geom_element.get("x", 0.0))
                y = float(geom_element.get("y", 0.0))
                # Szerokość i wysokość mogą być nieobecne dla niektórych elementów (np. krawędzi bez punktów)
                # ale dla wierzchołków powinny być. Jeśli nie ma, załóż 0.
                w = float(geom_element.get("width", 0.0))
                h = float(geom_element.get("height", 0.0))

                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x_coord = max(max_x_coord, x + w)
                max_y_coord = max(max_y_coord, y + h)
                has_geometry = True
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Błąd konwersji geometrii dla komórki {cell.get('id')}: {e}. Pomijam tę komórkę w obliczeniach BBox.")
                continue

    if not has_geometry:
        logger.debug("get_bounding_box: Nie znaleziono komórek z geometrią. Zwracam (0,0,0,0).")
        return 0.0, 0.0, 0.0, 0.0

    # Jeśli min_x/min_y pozostały 'inf', to znaczy, że nie było poprawnych geometrii (choć has_geometry byłoby False)
    # Jednakże, jeśli has_geometry jest True, to min_x/min_y nie będą 'inf'.
    final_min_x = min_x if min_x != float('inf') else 0.0
    final_min_y = min_y if min_y != float('inf') else 0.0

    width = max_x_coord - final_min_x if max_x_coord > final_min_x else 0.0
    height = max_y_coord - final_min_y if max_y_coord > final_min_y else 0.0

    logger.debug(f"Obliczono BoundingBox: x={final_min_x}, y={final_min_y}, w={width}, h={height}")
    return final_min_x, final_min_y, width, height


def normalize_positions(element: ET.Element, min_x: float, min_y: float) -> None:
    """Przesuwa bezpośrednie dzieci mxCell elementu tak, aby lewy górny róg BBox był w (0,0)."""
    if element is None: return
    logger.debug(f"Normalizowanie pozycji w elemencie (parent) z przesunięciem: min_x={min_x}, min_y={min_y}")
    for cell in element.findall("./mxCell"):  # Tylko bezpośrednie dzieci
        geom = cell.find("./mxGeometry[@as='geometry']")
        if geom is not None:
            try:
                x = float(geom.get("x", "0"))
                y = float(geom.get("y", "0"))
                geom.set("x", str(x - min_x))
                geom.set("y", str(y - min_y))
            except (ValueError, TypeError) as e:
                logger.warning(f"Błąd konwersji geometrii podczas normalizacji dla komórki {cell.get('id')}: {e}.")
                continue


def set_style_value(style_string: Optional[str], key: str, value: str) -> str:
    """
    Ustawia lub zastępuje wartość klucza w stringu stylu Draw.io.
    Zachowuje istniejące pary klucz=wartość. Zwraca nowy string stylu.
    """
    if style_string is None: style_string = ""
    style_string = style_string.strip()
    if style_string.endswith(';'): style_string = style_string[:-1]  # Usuń końcowy średnik jeśli jest

    parts = style_string.split(';')
    new_parts: List[str] = []
    found = False
    key_prefix = f"{key}="

    for part in parts:
        clean_part = part.strip()
        if not clean_part: continue  # Pomiń puste części (np. po podwójnym średniku)
        if clean_part.startswith(key_prefix):
            new_parts.append(f"{key_prefix}{value}")
            found = True
        else:
            new_parts.append(clean_part)

    if not found:
        new_parts.append(f"{key_prefix}{value}")

    # Złóż z powrotem i dodaj końcowy średnik, jeśli są jakieś części i całość nie jest pusta
    result = ";".join(new_parts)
    if result:  # Dodaj średnik tylko jeśli string nie jest pusty
        result += ';'
    return result


def apply_style_change(cell: ET.Element, style_key: str, style_value: str) -> None:
    """Dodaje lub modyfikuje pojedynczy klucz w atrybucie 'style' komórki."""
    if cell is None:
        logger.warning(f"apply_style_change: Próba modyfikacji stylu dla komórki None (klucz: {style_key}).")
        return
    current_style = cell.get("style", "")
    new_style = set_style_value(current_style, style_key, style_value)
    cell.set("style", new_style)


def create_group_cell(group_id: str, parent_id: str, x: float, y: float, width: float, height: float) -> ET.Element:
    """Tworzy element mxCell reprezentujący grupę (kontener)."""
    group_cell = ET.Element("mxCell", {
        "id": group_id, "value": "",
        # Styl grupy: niewidoczna ramka, niewidoczne wypełnienie, aby nie przeszkadzała wizualnie
        "style": "group;strokeColor=none;fillColor=none;movable=1;resizable=1;rotatable=0;deletable=1;editable=0;connectable=0;",
        "vertex": "1", "connectable": "0",  # Grupy zwykle nie są connectable
        "parent": parent_id
    })
    ET.SubElement(group_cell, "mxGeometry", {
        "x": str(x), "y": str(y),
        "width": str(width), "height": str(height),
        "as": "geometry"
    })
    return group_cell


def create_vertex_cell(  # Zmieniono nazwę z create_label_cell dla ogólności
        cell_id: str, parent_id: str, value: str,
        x: float, y: float, width: float, height: float,
        style: str, vertex: str = "1", connectable: str = "1"  # Domyślnie wierzchołek i connectable
) -> ET.Element:
    """Tworzy element mxCell dla wierzchołka (np. etykiety, kształtu)."""
    cell = ET.Element("mxCell", {
        "id": cell_id, "value": value, "style": style,
        "vertex": vertex, "parent": parent_id, "connectable": connectable
    })
    ET.SubElement(cell, "mxGeometry", {
        "x": str(x), "y": str(y),
        "width": str(width), "height": str(height),
        "as": "geometry"
    })
    return cell


def create_edge_cell(
        edge_id: str, parent_id: str,
        source_id: Optional[str], target_id: Optional[str],
        style: str, value: str = ""
) -> ET.Element:
    """Tworzy element mxCell dla krawędzi (linii) z logicznym source/target."""
    attrs = {
        "id": edge_id, "value": value, "style": style,
        "edge": "1", "parent": parent_id,
    }
    if source_id: attrs["source"] = source_id
    if target_id: attrs["target"] = target_id

    edge_cell = ET.Element("mxCell", attrs)
    ET.SubElement(edge_cell, "mxGeometry", {"relative": "1", "as": "geometry"})  # Krawędzie zwykle mają relative=1
    return edge_cell


def create_floating_edge_cell(
        edge_id: str, parent_id: str, style: str,
        source_point: Tuple[float, float],
        target_point: Tuple[float, float],
        waypoints: Optional[List[Tuple[float, float]]] = None,
        value: str = ""
) -> ET.Element:
    """
    Tworzy element mxCell dla krawędzi (linii) zdefiniowanej przez punkty (sourcePoint, targetPoint),
    bez ustawiania atrybutów 'source' i 'target' (nie jest przyczepiona do wierzchołków).
    """
    edge_cell = ET.Element("mxCell", {
        "id": edge_id, "value": value, "style": style,
        "edge": "1", "parent": parent_id
    })
    geometry = ET.SubElement(edge_cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    ET.SubElement(geometry, "mxPoint", {"as": "sourcePoint", "x": str(source_point[0]), "y": str(source_point[1])})
    ET.SubElement(geometry, "mxPoint", {"as": "targetPoint", "x": str(target_point[0]), "y": str(target_point[1])})

    if waypoints:
        points_array = ET.SubElement(geometry, "Array", {"as": "points"})
        for wp_x, wp_y in waypoints:
            ET.SubElement(points_array, "mxPoint", {"x": str(wp_x), "y": str(wp_y)})
    return edge_cell