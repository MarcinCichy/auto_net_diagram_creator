# drawio_utils.py
import xml.etree.ElementTree as ET
import re

DEFAULT_TEMPLATE_FILE = "switch.drawio"

def load_drawio_template(filepath=DEFAULT_TEMPLATE_FILE) -> ET.ElementTree | None:
    """Ładuje szablon Draw.io z pliku XML."""
    try:
        tree = ET.parse(filepath)
        print(f"✓ Pomyślnie załadowano szablon: {filepath}")
        return tree
    except FileNotFoundError:
        print(f"⚠ Błąd: Nie znaleziono pliku szablonu: {filepath}")
        return None
    except ET.ParseError as e:
        print(f"⚠ Błąd parsowania XML szablonu {filepath}: {e}")
        return None
    except Exception as e:
        print(f"⚠ Nieoczekiwany błąd przy ładowaniu szablonu {filepath}: {e}")
        return None

def find_cells(root_element: ET.Element, tag_name="mxCell") -> list[ET.Element]:
    """Znajduje wszystkie komórki o danym tagu (domyślnie mxCell) w obrębie elementu."""
    if root_element is None: return []
    # Szukaj rekursywnie
    return root_element.findall(f".//{tag_name}")

def find_cells_by_value(root_element: ET.Element, criteria_func) -> list[ET.Element]:
    """
    Znajduje komórki, których atrybut 'value' spełnia podane kryterium.
    criteria_func: funkcja, która przyjmuje wartość atrybutu 'value' i zwraca True/False.
    """
    if root_element is None: return []
    matching_cells = []
    for cell in find_cells(root_element): # Używa find_cells, które szuka rekursywnie
        value = cell.get("value", "").strip()
        try:
            if criteria_func(value):
                matching_cells.append(cell)
        except Exception:
            pass
    return matching_cells

def find_cell_by_id(root_element: ET.Element, cell_id: str) -> ET.Element | None:
    """Znajduje komórkę o podanym ID w obrębie elementu (rekursywnie)."""
    if root_element is None or not cell_id: return None
    # Szukaj rekursywnie w całym poddrzewie
    return root_element.find(f".//mxCell[@id='{cell_id}']")

def reassign_cell_ids(root_element: ET.Element, suffix: str):
    """
    Zmienia ID wszystkich komórek mxCell w poddrzewie root_element,
    dodając podany sufiks. Modyfikuje element w miejscu.
    """
    if root_element is None or not suffix: return
    id_map = {}
    cells_to_process = find_cells(root_element) # Znajdź wszystkie mxCell w poddrzewie

    # 1. Zbuduj mapę stary_id -> nowy_id
    for cell in cells_to_process:
        old_id = cell.get("id")
        if old_id:
            # Unikaj ponownego dodawania sufiksu
            if not old_id.endswith(f"_{suffix}"):
                new_id = f"{old_id}_{suffix}"
                id_map[old_id] = new_id
            else:
                id_map[old_id] = old_id

    # 2. Zaktualizuj atrybuty id, parent, source, target
    for cell in cells_to_process:
        old_id = cell.get("id")
        if old_id in id_map and cell.get("id") == old_id:
            cell.set("id", id_map[old_id])

        old_parent = cell.get("parent")
        if old_parent and old_parent in id_map:
            cell.set("parent", id_map[old_parent])

        old_source = cell.get("source")
        if old_source and old_source in id_map:
            cell.set("source", id_map[old_source])

        old_target = cell.get("target")
        if old_target and old_target in id_map:
            cell.set("target", id_map[old_target])

def get_bounding_box(element: ET.Element) -> tuple[float, float, float, float]:
    """Oblicza prostokąt otaczający (bounding box) dla komórek w elemencie."""
    min_x, min_y = float('inf'), float('inf')
    max_x, max_y = float('-inf'), float('-inf')
    has_geometry = False

    for cell in element.findall("./mxCell"): # Szukamy tylko w bezpośrednich dzieciach?
        geom = cell.find("./mxGeometry")
        if geom is not None:
            try:
                x = float(geom.get("x", 0))
                y = float(geom.get("y", 0))
                w = float(geom.get("width", 0))
                h = float(geom.get("height", 0))
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x + w if w > 0 else x)
                max_y = max(max_y, y + h if h > 0 else y)
                has_geometry = True
            except (ValueError, TypeError):
                continue

    if not has_geometry:
        return 0, 0, 0, 0

    width = max_x - min_x if max_x > min_x else 0
    height = max_y - min_y if max_y > min_y else 0
    final_min_x = min_x if min_x != float('inf') else 0
    final_min_y = min_y if min_y != float('inf') else 0

    return final_min_x, final_min_y, width, height


def normalize_positions(element: ET.Element, min_x: float, min_y: float):
    """Przesuwa bezpośrednie dzieci mxCell elementu tak, aby lewy górny róg był w (0,0)."""
    if element is None: return
    for cell in element.findall("./mxCell"):
        geom = cell.find("./mxGeometry")
        if geom is not None:
            try:
                x = float(geom.get("x", "0"))
                y = float(geom.get("y", "0"))
                geom.set("x", str(x - min_x))
                geom.set("y", str(y - min_y))
            except (ValueError, TypeError):
                continue

def apply_style_change(cell: ET.Element, style_key: str, style_value: str):
    """Dodaje lub modyfikuje pojedynczy klucz w atrybucie 'style' komórki."""
    if cell is None: return
    style = cell.get("style", "")
    style_parts = [part.strip() for part in style.split(';') if part.strip()]
    new_parts = []
    found = False
    for part in style_parts:
        if part.startswith(f"{style_key}="):
            new_parts.append(f"{style_key}={style_value}")
            found = True
        else:
            new_parts.append(part)
    if not found:
        new_parts.append(f"{style_key}={style_value}")

    new_style = ";".join(new_parts)
    if new_style and not new_style.endswith(';'):
        new_style += ';'
    cell.set("style", new_style)


def create_group_cell(group_id: str, parent_id: str, x: float, y: float, width: float, height: float) -> ET.Element:
    """Tworzy element mxCell reprezentujący grupę."""
    group_cell = ET.Element("mxCell", {
        "id": group_id, "value": "", "style": "group;strokeColor=none;fillColor=none;",
        "vertex": "1", "connectable": "0",
        "parent": parent_id
    })
    ET.SubElement(group_cell, "mxGeometry", {
        "x": str(x), "y": str(y),
        "width": str(width), "height": str(height),
        "as": "geometry"
    })
    return group_cell

def create_label_cell(label_id: str, parent_id: str, value: str, x: float, y: float, width: float, height: float, style: str) -> ET.Element:
    """Tworzy element mxCell dla etykiety tekstowej lub innego wierzchołka."""
    label_cell = ET.Element("mxCell", {
         "id": label_id, "value": value, "style": style,
         "vertex": "1", "parent": parent_id
    })
    ET.SubElement(label_cell, "mxGeometry", {
         "x": str(x), "y": str(y),
         "width": str(width), "height": str(height),
         "as": "geometry"
    })
    return label_cell

def create_edge_cell(edge_id: str, parent_id: str, source_id: str, target_id: str, style: str) -> ET.Element:
    """Tworzy element mxCell dla krawędzi (linii) z logicznym source/target."""
    edge_cell = ET.Element("mxCell", {
        "id": edge_id, "value": "", "style": style,
        "edge": "1", "parent": parent_id,
        "source": source_id if source_id else "",
        "target": target_id if target_id else ""
    })
    ET.SubElement(edge_cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    return edge_cell

# --- NOWA FUNKCJA ---
def create_floating_edge_cell(edge_id: str, parent_id: str, style: str,
                              source_point: tuple[float, float],
                              target_point: tuple[float, float],
                              waypoints: list[tuple[float, float]] = None) -> ET.Element:
    """
    Tworzy element mxCell dla krawędzi (linii) zdefiniowanej przez punkty,
    bez ustawiania atrybutów source i target.
    """
    edge_cell = ET.Element("mxCell", {
        "id": edge_id, "value": "", "style": style,
        "edge": "1", "parent": parent_id # Krawędzie też muszą mieć parent (zwykle '1')
        # UWAGA: BRAK source i target
    })
    geometry = ET.SubElement(edge_cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    # Dodaj punkt początkowy
    ET.SubElement(geometry, "mxPoint", {"as": "sourcePoint", "x": str(source_point[0]), "y": str(source_point[1])})
    # Dodaj punkt końcowy
    ET.SubElement(geometry, "mxPoint", {"as": "targetPoint", "x": str(target_point[0]), "y": str(target_point[1])})

    # Dodaj waypointy, jeśli istnieją
    if waypoints:
        points_array = ET.SubElement(geometry, "Array", {"as": "points"})
        for wp_x, wp_y in waypoints:
            ET.SubElement(points_array, "mxPoint", {"x": str(wp_x), "y": str(wp_y)})

    return edge_cell
# --- KONIEC NOWEJ FUNKCJI ---