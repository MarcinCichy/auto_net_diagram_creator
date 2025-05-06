# --- drawio_device_builder.py ---
import xml.etree.ElementTree as ET
import re
import math
import logging
from typing import List, Dict, Tuple, Optional, Any, NamedTuple

import logger

logger = logging.getLogger(__name__)

# Użyj standardowej biblioteki, jeśli 'natsort' nie jest dostępny
try:
    import natsort
    natsort_keygen = natsort.natsort_keygen()
    logger.debug("Moduł 'natsort' zaimportowany pomyślnie.")
except ImportError:
    logger.warning("Moduł 'natsort' nie znaleziony. Sortowanie nazw portów będzie standardowe.")
    # Prosty klucz sortujący jako fallback
    def natsort_keygen():
        return lambda x: str(x)

from librenms_client import LibreNMSAPI
import drawio_utils
import copy # Nadal może być potrzebny do głębokich kopii

logger = logging.getLogger(__name__)

# --- Stałe Definiujące Wygląd i Układ ---
# Wartości można dostosować do preferencji wizualnych
DEFAULT_PORTS_PER_ROW = 26  # Ile portów zmieści się w jednym rzędzie przed zawinięciem
PORT_WIDTH = 20.0
PORT_HEIGHT = 20.0
HORIZONTAL_SPACING = 10.0  # Odstęp między portami w poziomie
VERTICAL_SPACING = 15.0    # Odstęp między rzędami portów
ROW_OFFSET_Y = 7.0       # Odległość pierwszego rzędu portów od górnej krawędzi obudowy
CHASSIS_PADDING_X = 15.0   # Margines wewnętrzny obudowy po bokach
CHASSIS_PADDING_Y = 7.0    # Margines wewnętrzny obudowy góra/dół (względem rzędów portów)
MIN_CHASSIS_WIDTH = 100.0  # Minimalna szerokość, nawet dla małej liczby portów
MIN_CHASSIS_HEIGHT = 60.0 # Minimalna wysokość (np. dla 2 rzędów)
DEFAULT_CHASSIS_HEIGHT_NO_PORTS = 40.0 # Wysokość, gdy nie ma portów / błąd API

STACK_DETECTION_THRESHOLD = DEFAULT_PORTS_PER_ROW * 2 + 4 # Np. 2*26 + 4 = 56. Urządzenia z >56 portami fiz. będą oznaczane jako stos.

# Stałe dla linii pomocniczych i etykiet (bez zmian)
LINE_LENGTH = 25.0
LABEL_OFFSET_X = 5.0
MARGIN_BETWEEN_LINE_AND_LABEL = 15.0
LABEL_LINE_HEIGHT = 10.0
LABEL_PADDING = 4.0
WAYPOINT_OFFSET = 20 # Z poprzednich modyfikacji - dla rysowania połączeń
INFO_LABEL_X_OFFSET = -150
INFO_LABEL_MIN_WIDTH = 200
LOGICAL_IF_LIST_MAX_HEIGHT = 150
PHYSICAL_PORT_LIST_MAX_HEIGHT = 200 # Z poprzedniej modyfikacji

# --- Struktury Danych ---
class StyleInfo(NamedTuple):
    """Przechowuje wyciągnięte style z szablonu."""
    chassis: str = "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;" # Domyślny styl obudowy
    port: str = "rounded=0;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;" # Domyślny styl portu
    port_up: str = "#00FF00"
    port_down: str = "#FF0000"
    port_unknown: str = "#FFA500"
    aux_line: str = "edgeStyle=orthogonalEdgeStyle;endArrow=none;strokeWidth=1;strokeColor=#AAAAAA;html=1;rounded=0;"
    label_rot: str = "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;overflow=visible;rotation=-90;fontSize=9;"
    label_hor: str = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;overflow=visible;fontSize=9;"
    info_label: str = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;fontSize=9;whiteSpace=wrap;overflow=visible;rounded=0;spacing=4;"

class PortEndpointData(NamedTuple):
    """Przechowuje dane punktu końcowego dla mapowania."""
    cell_id: str
    x: float
    y: float
    orientation: str # 'up', 'down', 'left', 'right'

class DynamicLayoutInfo(NamedTuple):
    """Przechowuje obliczone wymiary i układ."""
    width: float
    height: float
    num_rows: int
    ports_per_row: int

# --- Funkcje Pomocnicze ---

def _extract_styles_from_template(template_path: str) -> StyleInfo:
    """Wczytuje szablon i wyciąga style dla obudowy i portu."""
    logger.info(f"Wczytywanie stylów z szablonu: {template_path}")
    tree = drawio_utils.load_drawio_template(template_path)
    if tree is None:
        logger.warning("Nie udało się wczytać szablonu, używanie domyślnych stylów.")
        return StyleInfo()

    root = tree.getroot().find(".//root")
    if root is None:
        logger.warning("Brak elementu <root> w szablonie, używanie domyślnych stylów.")
        return StyleInfo()

    chassis_style = None
    port_style = None

    # Próba znalezienia obudowy (np. pierwszy duży, zaokrąglony prostokąt)
    for cell in root.findall("./mxCell[@vertex='1']"):
        style = cell.get("style", "")
        if "rounded=1" in style: # Prosta heurystyka
            geom = cell.find("./mxGeometry")
            if geom is not None and float(geom.get("width", 0)) > 50: # Kolejna heurystyka
                chassis_style = style
                logger.debug(f"Znaleziono styl obudowy: {style}")
                break
    # Próba znalezienia portu (np. pierwszy z value="1")
    port_cell = drawio_utils.find_cell_by_id(root, 'wdXZIc1yJ1iBE2bjXRa4-3') # Użyjmy znanego ID portu '1'
    if port_cell is not None:
         port_style = port_cell.get("style")
         if port_style:
              logger.debug(f"Znaleziono styl portu: {port_style}")

    # Zwróć style (lub domyślne, jeśli nie znaleziono)
    return StyleInfo(
        chassis=chassis_style or StyleInfo.chassis,
        port=port_style or StyleInfo.port
        # Kolory i inne style zostają domyślne z definicji StyleInfo
    )

def _calculate_dynamic_layout(num_ports: int) -> DynamicLayoutInfo:
    """Oblicza wymiary obudowy i układ portów na podstawie ich liczby."""
    if num_ports <= 0:
        return DynamicLayoutInfo(width=MIN_CHASSIS_WIDTH, height=DEFAULT_CHASSIS_HEIGHT_NO_PORTS, num_rows=0, ports_per_row=0)

    # Oblicz liczbę wierszy (minimum 2 dla estetyki, chyba że portów jest bardzo mało)
    ports_per_actual_row = DEFAULT_PORTS_PER_ROW
    num_rows = max(2, math.ceil(num_ports / ports_per_actual_row))

    # Oblicz liczbę portów w najszerszym rzędzie, aby wyznaczyć szerokość
    ports_in_widest_row = 0
    if num_ports <= ports_per_actual_row:
        ports_in_widest_row = num_ports
    elif num_ports <= ports_per_actual_row * 2: # Do 2 rzędów
         ports_in_widest_row = max(ports_per_actual_row, num_ports - ports_per_actual_row)
    else: # Więcej niż 2 rzędy (choć layout zakłada 2 na razie)
         ports_in_widest_row = ports_per_actual_row # Zakładamy pełne rzędy

    # Oblicz wymiary obudowy
    chassis_width = ports_in_widest_row * PORT_WIDTH + max(0, ports_in_widest_row - 1) * HORIZONTAL_SPACING + 2 * CHASSIS_PADDING_X
    chassis_height = ROW_OFFSET_Y + num_rows * PORT_HEIGHT + max(0, num_rows - 1) * VERTICAL_SPACING + CHASSIS_PADDING_Y

    # Zapewnij minimalne wymiary
    chassis_width = max(MIN_CHASSIS_WIDTH, chassis_width)
    chassis_height = max(MIN_CHASSIS_HEIGHT, chassis_height)

    return DynamicLayoutInfo(width=chassis_width, height=chassis_height, num_rows=num_rows, ports_per_row=ports_per_actual_row)

def _classify_ports(ports_data: List[Dict]) -> Tuple[List[Dict], List[Dict], Optional[Dict]]:
    """
    Klasyfikuje porty - wersja 4: próba "zgadnięcia" portów stosu wg nazwy.
    """
    physical_ports_from_api: List[Dict] = []
    logical_interfaces: List[Dict] = []
    mgmt0_api_port_info: Optional[Dict] = None

    # Wzorce (z naciskiem na wykluczanie logicznych i wykrywanie wzorca stosu)
    physical_name_patterns = re.compile(r'^(Eth|Gi|Fa|Te|Hu|Twe|Fo|mgmt|Management|TenGig|FortyGig|HundredGig|Serial|Port\s?\d|SFP|XFP|QSFP|em\d|ens\d|eno\d|enp\d+s\d+|ge-|xe-|et-|eno|lan)', re.IGNORECASE)
    logical_name_patterns = re.compile(r'^(Vlan|vl|Loopback|Lo|lo\d*|Port-channel|Po|Bundle-Ether|ae|Tunnel|Tun|Null|Nu|Cpu|Fabric|Voice|Async|Group-Async|ipsec|gre|sit|pimreg|mgmt[1-9]|Irq|Service-Engine|Dialer|Virtual-Access|Virtual-Template)|.*\.\d+$', re.IGNORECASE) # Rozszerzono o więcej typów
    # NOWY: Wzorzec dla typowych nazw portów stosu/modułowych (Typ[Cyfra/Cyfra/Cyfra...])
    stack_port_pattern = re.compile(r'^[a-zA-Z]+[-]?\d+/\d+(/\d+)+$', re.IGNORECASE)

    # Typy IANA (bez zmian)
    physical_types_iana = {
        'ethernetcsmacd', 'fastether', 'gigabitethernet', 'fastetherfx',
        'infinitiband', 'sonet', 'sdsl', 'hdsl', 'shdsl', 'adsl', 'radsl', 'vdsl',
        'ieee80211', 'opticalchannel', 'fibrechannel', 'propvirtual'
    }
    logical_types_iana = {
        'l3ipvlan', 'softwareloopback', 'ieee8023adlag', 'l2vlan', 'tunnel',
        'propMultiplexor', 'bridge', 'other' # 'other' nadal ostrożnie
    }

    temp_mgmt0_candidates = []
    other_ports = []

    # Wyodrębnij mgmt0
    for port_info in ports_data:
        if_name = port_info.get('ifName', '').lower()
        if_descr = port_info.get('ifDescr', '').lower()
        if if_name == 'mgmt0' or if_descr == 'mgmt0':
             temp_mgmt0_candidates.append(port_info)
        else:
             other_ports.append(port_info)

    if temp_mgmt0_candidates:
         mgmt0_api_port_info = temp_mgmt0_candidates[0]
         logger.debug(f"Znaleziono port mgmt0: {mgmt0_api_port_info.get('ifName')}")
         physical_ports_from_api.append(mgmt0_api_port_info)

    # Klasyfikuj pozostałe porty
    for port_info in other_ports:
        if_name = port_info.get('ifName', '')
        if_descr = port_info.get('ifDescr', '')
        if_type_raw = port_info.get('ifType')
        if_phys_address = port_info.get('ifPhysAddress')

        has_mac = bool(if_phys_address and len(str(if_phys_address).replace(':','').replace('-','').replace('.','')) >= 12)

        if_type_iana = ''
        if isinstance(if_type_raw, dict): if_type_iana = if_type_raw.get('iana', '').lower()
        elif isinstance(if_type_raw, str): if_type_iana = if_type_raw.lower()

        # Sprawdź, czy nazwa/opis pasują do wzorców
        name_looks_physical = bool(physical_name_patterns.match(if_name) or physical_name_patterns.match(if_descr))
        name_looks_logical = bool(logical_name_patterns.match(if_name) or logical_name_patterns.match(if_descr))
        name_looks_like_stack_port = bool(stack_port_pattern.match(if_name)) # Sprawdź nowy wzorzec

        # --- LOGIKA DECYZJI v4 ---
        is_physical = False # Domyślnie logiczny

        # 1. Wyklucz, jeśli nazwa JEDNOZNACZNIE logiczna (najwyższy priorytet)
        if name_looks_logical:
            is_physical = False
            logger.debug(f"Port '{if_name}' ({if_descr}) -> LOGICZNY (wzorzec nazwy logicznej).")
        # 2. Wyklucz, jeśli typ IANA JEDNOZNACZNIE logiczny
        elif if_type_iana in logical_types_iana:
            is_physical = False
            logger.debug(f"Port '{if_name}' ({if_descr}) -> LOGICZNY (typ IANA logiczny: {if_type_iana}).")
        # 3. Uznaj za FIZYCZNY, jeśli nazwa pasuje do wzorca portu stosu/modułowego (SILNE ZAŁOŻENIE)
        elif name_looks_like_stack_port:
             is_physical = True
             logger.debug(f"Port '{if_name}' ({if_descr}) -> FIZYCZNY (heurystyka: wzorzec nazwy stosu/modułu).")
        # 4. Uznaj za FIZYCZNY, jeśli typ IANA jest jednoznacznie fizyczny
        elif if_type_iana in physical_types_iana:
            is_physical = True
            logger.debug(f"Port '{if_name}' ({if_descr}) -> FIZYCZNY (typ IANA fizyczny: {if_type_iana}).")
        # 5. Uznaj za FIZYCZNY, jeśli ma MAC i nazwa NIE wygląda na logiczną (fallback ogólny)
        elif has_mac and not name_looks_logical:
             is_physical = True
             logger.debug(f"Port '{if_name}' ({if_descr}) -> FIZYCZNY (fallback: ma MAC, nazwa nie wygląda na logiczną, typ: {if_type_iana}).")
        # 6. Uznaj za FIZYCZNY, jeśli NIE ma MAC, ale nazwa wygląda na fizyczną (wg ogólnego wzorca) i NIE logiczną (fallback dla np. portów serial bez MAC)
        elif not has_mac and name_looks_physical and not name_looks_logical:
             is_physical = True
             logger.debug(f"Port '{if_name}' ({if_descr}) -> FIZYCZNY (fallback: BRAK MAC, ale nazwa wygląda na fizyczną i nie na logiczną, typ: {if_type_iana}).")
        # 7. W pozostałych przypadkach -> LOGICZNY
        else:
             is_physical = False
             logger.debug(f"Port '{if_name}' ({if_descr}) -> LOGICZNY (ostateczny fallback).")

        # --- KONIEC LOGIKI v4 ---

        if is_physical:
            physical_ports_from_api.append(port_info)
        else:
            port_info['_ifType_iana'] = if_type_iana
            logical_interfaces.append(port_info)

    logger.info(f"Sklasyfikowano (v4 - zgaduj stos): {len(physical_ports_from_api)} portów fizycznych, {len(logical_interfaces)} innych interfejsów.")
    return physical_ports_from_api, logical_interfaces, mgmt0_api_port_info


# --- Główna Funkcja Budująca ---

# NOWA funkcja do obliczania rozmiaru - wywoływana w pierwszej fazie layoutu
def calculate_dynamic_device_size(device_info: Dict, api_client: LibreNMSAPI) -> Tuple[float, float]:
    """Pobiera porty i oblicza wymagany dynamiczny rozmiar urządzenia."""
    device_id = device_info.get("device_id")
    ports_data = []
    if device_id:
        try:
            ports_data_raw = api_client.get_ports(str(device_id), columns="ifName,ifDescr,ifType") # Minimum potrzebne do klasyfikacji
            ports_data = ports_data_raw if ports_data_raw is not None else []
        except Exception as e:
            logger.error(f"Wyjątek podczas pobierania portów dla obliczenia rozmiaru (ID: {device_id}): {e}")
            ports_data = []
    else:
        logger.warning(f"Brak device_id dla {device_info.get('hostname')}, nie można obliczyć dynamicznego rozmiaru.")
        return MIN_CHASSIS_WIDTH, DEFAULT_CHASSIS_HEIGHT_NO_PORTS # Zwróć rozmiar domyślny/minimalny

    # Klasyfikuj, żeby dostać tylko fizyczne
    physical_ports, _, _ = _classify_ports(ports_data)
    num_physical_ports = len(physical_ports)

    # Oblicz layout
    layout_info = _calculate_dynamic_layout(num_physical_ports)
    return layout_info.width, layout_info.height


# ZMODYFIKOWANA funkcja dodająca urządzenie
def add_device_to_diagram(
    global_root_cell: ET.Element,
    device_info: dict,
    api_client: LibreNMSAPI,
    position: tuple[float, float],
    device_index: int,
    styles: StyleInfo # Przekazujemy wyciągnięte style
) -> Optional[Dict[Any, PortEndpointData]]: # Zmieniono typ zwracany
    """
    Dodaje urządzenie do diagramu, DYNAMICZNIE generując jego obudowę i porty.
    """
    port_map_for_device: Dict[Any, PortEndpointData] = {}
    offset_x, offset_y = position
    group_id_suffix = f"dev{device_index}"
    group_id = f"group_{group_id_suffix}"
    current_host_identifier = device_info.get('purpose') or device_info.get('hostname') or device_info.get('ip', f"ID:{device_info.get('device_id')}")
    logger.info(f"Dynamiczne dodawanie urządzenia {current_host_identifier}...")

    # --- Pobierz i sklasyfikuj porty ---
    device_id = device_info.get("device_id")
    ports_data = []
    if device_id:
        try:
            ports_data_raw = api_client.get_ports(str(device_id)) # Pobierz pełne dane portów
            if ports_data_raw is None:
                logger.warning(f"Nie udało się pobrać portów z API dla urządzenia ID: {device_id} ({current_host_identifier}). Rysuję minimalną obudowę.")
                ports_data = []
            else:
                 ports_data = ports_data_raw
        except Exception as e:
             logger.error(f"Wyjątek podczas pobierania portów API dla urządzenia ID: {device_id} ({current_host_identifier}): {e}")
             ports_data = []
    else:
        logger.warning(f"Brak device_id dla {current_host_identifier}, nie można pobrać portów API. Rysuję minimalną obudowę.")

    physical_ports_from_api, logical_interfaces, mgmt0_api_port_info = _classify_ports(ports_data)

    # Sortowanie portów fizycznych (bez mgmt0 na razie)
    physical_ports_to_draw = [p for p in physical_ports_from_api if p != mgmt0_api_port_info]
    try:
        physical_ports_to_draw.sort(key=lambda p: natsort_keygen()(p.get('ifName', '')))
        logger.debug(f"Posortowano {len(physical_ports_to_draw)} portów fizycznych do rysowania.")
    except Exception as e:
         logger.warning(f"Błąd sortowania portów fizycznych: {e}")
         physical_ports_to_draw.sort(key=lambda p: p.get('ifName', ''))


    # --- Oblicz dynamiczny layout ---
    num_physical_ports = len(physical_ports_to_draw)
    layout_info = _calculate_dynamic_layout(num_physical_ports)
    chassis_width = layout_info.width
    chassis_height = layout_info.height
    num_rows = layout_info.num_rows
    ports_per_row = layout_info.ports_per_row
    logger.info(f"Obliczone wymiary dla {current_host_identifier}: {chassis_width}x{chassis_height}, porty: {num_physical_ports}, rzędy: {num_rows}")


    # --- Generuj elementy XML ---
    # 1. Grupa (jak poprzednio)
    group_cell = drawio_utils.create_group_cell(group_id, "1", offset_x, offset_y, chassis_width, chassis_height)
    global_root_cell.append(group_cell)

    # 2. Obudowa (dynamiczny rozmiar, styl z szablonu)
    chassis_id = f"chassis_{group_id_suffix}"
    chassis_cell = drawio_utils.create_label_cell(
        chassis_id, group_id, "", # Pusta etykieta, rodzicem jest grupa
        0, 0, chassis_width, chassis_height, # Pozycja (0,0) względem grupy
        styles.chassis # Użyj stylu wyciągniętego z szablonu
    )
    global_root_cell.append(chassis_cell)

    # 3. Porty Fizyczne (dynamiczne generowanie)
    processed_api_ports_indices = set() # Do oznaczania w etykiecie info
    port_cells_generated = {} # Mapowanie ifIndex -> cell_id wygenerowanego portu

    for i, api_port_info in enumerate(physical_ports_to_draw):
        port_index = i # Indeks 0-based
        visual_port_number = i + 1 # Numer 1-based do wyświetlenia

        # Oblicz pozycję portu
        row_index = port_index // ports_per_row
        col_index = port_index % ports_per_row
        port_x_rel = CHASSIS_PADDING_X + col_index * (PORT_WIDTH + HORIZONTAL_SPACING)
        port_y_rel = ROW_OFFSET_Y + row_index * (PORT_HEIGHT + VERTICAL_SPACING)

        # ID komórki portu
        port_ifindex = api_port_info.get("ifIndex")
        # Użyjemy ifIndex jeśli jest, inaczej indeksu pętli dla unikalności ID
        port_cell_id = f"dynport_{group_id_suffix}_{port_ifindex if port_ifindex is not None else i}"

        # Styl portu i kolor statusu
        port_style = styles.port # Bazowy styl portu z szablonu
        status = api_port_info.get("ifOperStatus", "unknown").lower()
        fill_color = styles.port_up if status == "up" else (styles.port_down if status == "down" else styles.port_unknown)
        port_style = drawio_utils.set_style_value(port_style, "fillColor", fill_color) # Ustaw kolor dynamicznie

        # Utwórz komórkę portu
        port_cell = drawio_utils.create_label_cell(
            port_cell_id, group_id, str(visual_port_number), # Etykieta to numer 1..N
            port_x_rel, port_y_rel, PORT_WIDTH, PORT_HEIGHT,
            port_style
        )
        global_root_cell.append(port_cell)
        if port_ifindex is not None:
             processed_api_ports_indices.add(port_ifindex)
             port_cells_generated[port_ifindex] = port_cell_id


        # Oblicz punkt końcowy i orientację
        center_x_rel = port_x_rel + PORT_WIDTH / 2
        endpoint_orientation: str
        line_start_y_rel: float
        line_end_y_rel: float

        if row_index % 2 == 0: # Górny rząd (i kolejne parzyste, jeśli będą)
            line_start_y_rel = port_y_rel
            line_end_y_rel = port_y_rel - LINE_LENGTH
            endpoint_orientation = "up"
        else: # Dolny rząd (i kolejne nieparzyste)
            line_start_y_rel = port_y_rel + PORT_HEIGHT
            line_end_y_rel = port_y_rel + PORT_HEIGHT + LINE_LENGTH
            endpoint_orientation = "down"

        endpoint_abs_x = offset_x + center_x_rel
        endpoint_abs_y = offset_y + line_end_y_rel

        # Utwórz niewidoczny punkt końcowy
        dummy_endpoint_id = f"ep_{port_cell_id}"
        dummy_style = "shape=none;fillColor=none;strokeColor=none;resizable=0;movable=0;editable=0;portConstraint=none;noLabel=1;"
        dummy_vertex_cell = drawio_utils.create_label_cell(
            dummy_endpoint_id, "1", "",
            endpoint_abs_x - 0.5, endpoint_abs_y - 0.5, 1, 1,
            dummy_style
        )
        global_root_cell.append(dummy_vertex_cell)

        # Zapisz mapowanie (wiele kluczy)
        endpoint_data = PortEndpointData(
            cell_id=dummy_endpoint_id,
            x=endpoint_abs_x,
            y=endpoint_abs_y,
            orientation=endpoint_orientation
        )
        port_name_api = api_port_info.get('ifName')
        if port_ifindex is not None: port_map_for_device[f"ifindex_{port_ifindex}"] = endpoint_data
        if port_name_api: port_map_for_device[port_name_api] = endpoint_data
        port_descr_api = api_port_info.get('ifDescr')
        if port_descr_api and port_descr_api != port_name_api: port_map_for_device[port_descr_api] = endpoint_data
        port_alias_api = api_port_info.get('ifAlias')
        if port_alias_api: port_map_for_device[port_alias_api] = endpoint_data
        port_map_for_device[str(visual_port_number)] = endpoint_data # Mapowanie po numerze wizualnym


        # Generuj linię pomocniczą i etykietę aliasu
        alias_text = api_port_info.get("ifAlias", "")
        if alias_text:
            label_id = f"label_{port_cell_id}"
            target_id_for_aux_line = label_id

            # Styl i rozmiar etykiety (obrócona dla górnych, pozioma dla dolnych?)
            # Dla uproszczenia na razie zrobimy wszystkie obrócone jak dla górnego rzędu
            # ale pozycję Y dostosujemy
            label_style = styles.label_rot
            lines = alias_text.split('\n')
            num_lines = len(lines); max_line_len = max(len(line) for line in lines) if lines else 0
            label_width = num_lines * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
            label_height = max(30, max_line_len * (LABEL_LINE_HEIGHT * 0.8)) + 2 * LABEL_PADDING

            label_abs_x_pos = endpoint_abs_x + LABEL_OFFSET_X
            # Dostosuj pozycję Y etykiety
            if endpoint_orientation == "up": # Górny rząd
                 label_abs_y_pos = endpoint_abs_y - MARGIN_BETWEEN_LINE_AND_LABEL - label_height
            else: # Dolny rząd
                 label_abs_y_pos = endpoint_abs_y + MARGIN_BETWEEN_LINE_AND_LABEL

            label_cell = drawio_utils.create_label_cell(label_id, "1", alias_text, label_abs_x_pos, label_abs_y_pos, label_width, label_height, label_style)
            global_root_cell.append(label_cell)
        else:
            target_id_for_aux_line = dummy_endpoint_id # Brak aliasu, cel to punkt

        # Rysuj linię pomocniczą
        edge_id = f"edge_aux_{port_cell_id}"
        edge_cell = drawio_utils.create_edge_cell(edge_id, group_id, port_cell_id, target_id_for_aux_line, styles.aux_line)
        edge_geom = edge_cell.find("./mxGeometry")
        if edge_geom is not None:
             ET.SubElement(edge_geom, "mxPoint", {"as": "targetPoint", "x": str(endpoint_abs_x), "y": str(endpoint_abs_y)})
        global_root_cell.append(edge_cell)

    # 4. Port mgmt0 (jeśli istnieje - rysowany osobno, np. po prawej)
    # (Opcjonalnie: można go też wrzucić do pętli portów fizycznych z inną logiką pozycji)
    if mgmt0_api_port_info:
         logger.info("Dodawanie portu mgmt0...")
         # Prosta implementacja: dodaj mały kwadracik po prawej stronie obudowy
         mgmt0_ifindex = mgmt0_api_port_info.get('ifIndex')
         mgmt0_port_id = f"dynport_{group_id_suffix}_mgmt0"
         mgmt0_x_rel = chassis_width + HORIZONTAL_SPACING # Po prawej stronie obudowy
         mgmt0_y_rel = chassis_height / 2 - PORT_HEIGHT / 2 # Na środku wysokości

         mgmt0_style = styles.port # Użyj stylu portu
         status = mgmt0_api_port_info.get("ifOperStatus", "unknown").lower()
         fill_color = styles.port_up if status == "up" else (styles.port_down if status == "down" else styles.port_unknown)
         mgmt0_style = drawio_utils.set_style_value(mgmt0_style, "fillColor", fill_color)

         mgmt0_cell = drawio_utils.create_label_cell(
             mgmt0_port_id, group_id, "M", # Etykieta 'M' w środku
             mgmt0_x_rel, mgmt0_y_rel, PORT_WIDTH, PORT_HEIGHT,
             mgmt0_style
         )
         global_root_cell.append(mgmt0_cell)
         if mgmt0_ifindex is not None:
              processed_api_ports_indices.add(mgmt0_ifindex)
              port_cells_generated[mgmt0_ifindex] = mgmt0_port_id


         # Punkt końcowy dla mgmt0 (w prawo)
         endpoint_abs_x_mgmt = offset_x + mgmt0_x_rel + PORT_WIDTH + LINE_LENGTH
         endpoint_abs_y_mgmt = offset_y + mgmt0_y_rel + PORT_HEIGHT / 2
         endpoint_orientation_mgmt = "right"
         dummy_endpoint_id_mgmt = f"ep_{mgmt0_port_id}"
         dummy_style_mgmt = "shape=none;fillColor=none;strokeColor=none;resizable=0;movable=0;editable=0;portConstraint=none;noLabel=1;"
         dummy_vertex_cell_mgmt = drawio_utils.create_label_cell(
             dummy_endpoint_id_mgmt, "1", "",
             endpoint_abs_x_mgmt - 0.5, endpoint_abs_y_mgmt - 0.5, 1, 1,
             dummy_style_mgmt
         )
         global_root_cell.append(dummy_vertex_cell_mgmt)

         # Zapisz mapowanie dla mgmt0
         endpoint_data_mgmt = PortEndpointData(
             cell_id=dummy_endpoint_id_mgmt,
             x=endpoint_abs_x_mgmt,
             y=endpoint_abs_y_mgmt,
             orientation=endpoint_orientation_mgmt
         )
         port_name_api = mgmt0_api_port_info.get('ifName')
         if port_name_api: port_map_for_device[port_name_api] = endpoint_data_mgmt
         if mgmt0_ifindex is not None: port_map_for_device[f"ifindex_{mgmt0_ifindex}"] = endpoint_data_mgmt
         port_descr_api = mgmt0_api_port_info.get('ifDescr')
         if port_descr_api: port_map_for_device[port_descr_api] = endpoint_data_mgmt
         port_map_for_device["mgmt0"] = endpoint_data_mgmt # Fallback

         # Linia pomocnicza i alias dla mgmt0
         alias_text_mgmt = mgmt0_api_port_info.get("ifAlias", "")
         target_id_for_mgmt_aux = dummy_endpoint_id_mgmt
         if alias_text_mgmt:
              label_id_mgmt = f"label_{mgmt0_port_id}"
              target_id_for_mgmt_aux = label_id_mgmt
              label_style_mgmt = styles.label_hor # Etykieta pozioma
              # Oblicz rozmiar
              lines_mgmt = alias_text_mgmt.split('\n')
              max_line_len_mgmt = max(len(line) for line in lines_mgmt) if lines_mgmt else 0
              label_width_mgmt = max(50, max_line_len_mgmt * (LABEL_LINE_HEIGHT * 0.8)) + 2 * LABEL_PADDING
              label_height_mgmt = len(lines_mgmt) * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING
              # Pozycja
              label_abs_x_pos_mgmt = endpoint_abs_x_mgmt + MARGIN_BETWEEN_LINE_AND_LABEL
              label_abs_y_pos_mgmt = endpoint_abs_y_mgmt - label_height_mgmt / 2
              label_cell_mgmt = drawio_utils.create_label_cell(label_id_mgmt, "1", alias_text_mgmt, label_abs_x_pos_mgmt, label_abs_y_pos_mgmt, label_width_mgmt, label_height_mgmt, label_style_mgmt)
              global_root_cell.append(label_cell_mgmt)

         # Rysuj linię pomocniczą
         edge_id_mgmt = f"edge_aux_{mgmt0_port_id}"
         edge_cell_mgmt = drawio_utils.create_edge_cell(edge_id_mgmt, group_id, mgmt0_port_id, target_id_for_mgmt_aux, styles.aux_line)
         edge_geom_mgmt = edge_cell_mgmt.find("./mxGeometry")
         if edge_geom_mgmt is not None:
             ET.SubElement(edge_geom_mgmt, "mxPoint", {"as": "targetPoint", "x": str(endpoint_abs_x_mgmt), "y": str(endpoint_abs_y_mgmt)})
         global_root_cell.append(edge_cell_mgmt)


    # 5. Etykieta Informacyjna (jak w poprzedniej wersji ze stackiem)
    # Wykrywanie stosu na podstawie liczby portów
    is_stack = len(physical_ports_from_api) > STACK_DETECTION_THRESHOLD # Używamy tej samej logiki co w poprzedniej propozycji
    logger.debug(f"Generowanie etykiety informacyjnej dla {current_host_identifier} (stack: {is_stack})...")
    dev_info_id = f"device_info_{group_id_suffix}"
    # ... (cała logika budowania HTML dla etykiety, w tym dodanie "(STACK)"
    #      oraz sekcji "Porty Fizyczne" listującej WSZYSTKIE porty z API
    #      z oznaczeniem [M] dla zamapowanych - **TAKA SAMA JAK W POPRZEDNIEJ ODPOWIEDZI**)

    # --- Początek logiki etykiety (skopiowana i dostosowana) ---
    dev_id_val = device_info.get('device_id', 'N/A'); hostname_raw = device_info.get('hostname', ''); ip_raw = device_info.get('ip', ''); purpose_raw = device_info.get('purpose', '')
    temp_display_ip = ip_raw if ip_raw else 'N/A'; hostname_looks_like_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', str(hostname_raw)))
    if hostname_looks_like_ip and not ip_raw: temp_display_ip = hostname_raw
    display_name_main = "(Brak Nazwy)"
    if purpose_raw and purpose_raw.strip(): display_name_main = purpose_raw.strip()
    elif hostname_raw and not hostname_looks_like_ip: display_name_main = hostname_raw
    elif ip_raw: display_name_main = ip_raw
    elif hostname_raw and hostname_looks_like_ip: display_name_main = hostname_raw
    elif dev_id_val != 'N/A': display_name_main = f"Urządzenie ID: {dev_id_val}"
    if is_stack: display_name_main += " <b>(STACK)</b>"
    display_extra_info = []
    if hostname_raw and hostname_raw != display_name_main.replace(" <b>(STACK)</b>","") and not hostname_looks_like_ip: display_extra_info.append(f"Host: {hostname_raw}")
    if purpose_raw and purpose_raw.strip() and purpose_raw.strip() != display_name_main.replace(" <b>(STACK)</b>",""): display_extra_info.append(f"Cel: {purpose_raw.strip()}")
    base_device_label_html = f"<div style='text-align:left;'><b>{display_name_main}</b><br/>ID: {dev_id_val}"
    if display_extra_info: base_device_label_html += "<br/>" + "<br/>".join(display_extra_info)
    base_device_label_html += f"<br/>IP: {temp_display_ip}</div>"
    physical_ports_html = f"<b>Porty Fizyczne ({len(physical_ports_from_api)}):</b><br/><div style='margin:0; padding-left:5px; max-height:{PHYSICAL_PORT_LIST_MAX_HEIGHT}px; overflow:auto;'>"
    if physical_ports_from_api:
        try: physical_ports_from_api.sort(key=lambda p: natsort_keygen()(p.get('ifName', '')))
        except Exception as e: logger.warning(f"Błąd sortowania listy portów fizycznych w etykiecie: {e}"); physical_ports_from_api.sort(key=lambda p: p.get('ifName', ''))
        for phys_port in physical_ports_from_api:
            name = phys_port.get('ifName', 'N/A'); descr = phys_port.get('ifDescr', ''); alias = phys_port.get('ifAlias', ''); status = phys_port.get('ifOperStatus', 'unknown').lower(); ifindex = phys_port.get('ifIndex')
            status_color = "green" if status == "up" else ("red" if status == "down" else "orange"); extra_info = alias if alias else (descr if descr != name else ''); extra_info_str = f" <i>({extra_info})</i>" if extra_info else ""
            mapped_marker = " [M]" if ifindex in processed_api_ports_indices else ""
            physical_ports_html += f"<font color='{status_color}'>•</font>&nbsp;{name}{extra_info_str}&nbsp;({status}){mapped_marker}<br/>"
    else: physical_ports_html += "(brak)<br/>"
    physical_ports_html += "</div>";
    logical_interface_list_html = f"<b>Inne Interfejsy ({len(logical_interfaces)}):</b><br/><div style='margin:0; padding-left:5px; max-height:{LOGICAL_IF_LIST_MAX_HEIGHT}px; overflow:auto;'>"
    if logical_interfaces:
        logical_interfaces.sort(key=lambda p: p.get('ifName', ''));
        for logical_if in logical_interfaces:
            name = logical_if.get('ifName') or logical_if.get('ifDescr', 'N/A'); status = logical_if.get('ifOperStatus', 'unknown').lower()
            status_color = "green" if status == "up" else ("red" if status == "down" else "orange"); if_type_str = logical_if.get('_ifType_iana', ''); type_info = f" ({if_type_str})" if if_type_str else ""
            logical_interface_list_html += f"<font color='{status_color}'>•</font>&nbsp;{name}{type_info}&nbsp;({status})<br/>"
    else: logical_interface_list_html += "(brak)<br/>"
    logical_interface_list_html += "</div>";
    full_device_label_html = f"{base_device_label_html}<hr size='1'/>{physical_ports_html}<hr size='1'/>{logical_interface_list_html}"
    # Obliczanie wysokości etykiety (bardzo uproszczone)
    info_width = max(chassis_width, INFO_LABEL_MIN_WIDTH) # Etykieta co najmniej tak szeroka jak obudowa
    num_base_lines = 3 + len(display_extra_info); base_height = num_base_lines * (LABEL_LINE_HEIGHT + 2) + 10
    phys_port_section_height = min(PHYSICAL_PORT_LIST_MAX_HEIGHT, max(15, len(physical_ports_from_api) * (LABEL_LINE_HEIGHT + 1))) + 20
    logical_if_section_height = min(LOGICAL_IF_LIST_MAX_HEIGHT, max(15, len(logical_interfaces) * (LABEL_LINE_HEIGHT + 1))) + 20
    info_height = base_height + phys_port_section_height + logical_if_section_height + 15
    label_abs_x_pos = offset_x + INFO_LABEL_X_OFFSET; label_abs_y_pos = offset_y + ROW_OFFSET_Y; # Wyrównaj do górnych portów
    label_parent_id = "1"
    dev_info_cell = drawio_utils.create_label_cell(dev_info_id, label_parent_id, full_device_label_html, label_abs_x_pos, label_abs_y_pos, info_width, info_height, styles.info_label)
    global_root_cell.append(dev_info_cell)
    # --- Koniec logiki etykiety ---

    logger.info(f"✓ Urządzenie {current_host_identifier} dynamicznie przetworzone.")
    return port_map_for_device

# Usuwamy funkcję load_and_prepare_template, bo nie jest już potrzebna w tej formie

# NOWA funkcja pomocnicza do ustawiania wartości stylu (prostsza niż regex)
def set_style_value(style_string: str, key: str, value: str) -> str:
    """Ustawia lub zastępuje wartość klucza w stringu stylu Draw.io."""
    parts = style_string.split(';')
    new_parts = []
    found = False
    key_prefix = f"{key}="
    for part in parts:
        if not part: continue
        if part.startswith(key_prefix):
            new_parts.append(f"{key_prefix}{value}")
            found = True
        else:
            new_parts.append(part)
    if not found:
        new_parts.append(f"{key_prefix}{value}")
    # Upewnij się, że kończy się średnikiem, jeśli nie jest pusty
    result = ";".join(new_parts)
    if result and not result.endswith(';'):
        result += ';'
    return result

# Dodajmy funkcję do drawio_utils, jeśli jej tam nie ma
if not hasattr(drawio_utils, 'set_style_value'):
     drawio_utils.set_style_value = set_style_value