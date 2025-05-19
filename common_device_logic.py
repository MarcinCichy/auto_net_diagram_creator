# common_device_logic.py
import logging
import re
import math
from typing import List, Dict, Tuple, Optional, Any, NamedTuple

from librenms_client import LibreNMSAPI  # Zakładamy, że jest w PYTHONPATH
from utils import get_canonical_identifier  # Zakładamy, że jest w PYTHONPATH


# Przeniesione i dostosowane NamedTuple oraz stałe
# StyleInfo pozostaje w builderach, bo jest specyficzne dla Draw.io (choć SVG może z niego czerpać)

class PortEndpointData(NamedTuple):
    """
    Przechowuje informacje o punkcie końcowym portu, do którego można podłączyć linię.
    cell_id: ID elementu (np. dummy vertex w Draw.io, kształt portu w SVG), do którego linia się łączy.
    x, y: Absolutne współrzędne X, Y punktu zaczepienia linii połączenia (często poza samym portem).
    orientation: Kierunek wychodzenia linii połączenia ('up', 'down', 'left', 'right').
    """
    cell_id: str
    x: float
    y: float
    orientation: str


class DynamicLayoutInfo(NamedTuple):
    """Informacje o obliczonym layoucie portów na urządzeniu."""
    width: float  # Szerokość chassis
    height: float  # Wysokość chassis
    num_rows: int  # Liczba rzędów portów
    ports_per_row: int  # Liczba portów w najszerszym rzędzie (lub domyślna)


# --- Stałe Definiujące Wygląd i Układ (wspólne wartości odniesienia) ---
# Te wartości są używane do obliczeń, konkretne style graficzne są w builderach.
DEFAULT_PORTS_PER_ROW = 26
PORT_WIDTH = 20.0
PORT_HEIGHT = 20.0
HORIZONTAL_SPACING = 10.0
VERTICAL_SPACING = 15.0
ROW_OFFSET_Y = 7.0
CHASSIS_PADDING_X = 15.0
CHASSIS_PADDING_Y = 7.0
MIN_CHASSIS_WIDTH = 100.0
MIN_CHASSIS_HEIGHT = 60.0
DEFAULT_CHASSIS_HEIGHT_NO_PORTS = 40.0

WAYPOINT_OFFSET = 20.0  # Dla linii połączeń, odległość od portu do punktu pośredniego

# Stałe dla etykiet
LOGICAL_IF_LIST_MAX_HEIGHT = 150.0
PHYSICAL_PORT_LIST_MAX_HEIGHT = 200.0
LABEL_LINE_HEIGHT = 10.0
LABEL_PADDING = 4.0
STACK_DETECTION_THRESHOLD = DEFAULT_PORTS_PER_ROW * 2 + 4  # Jeśli urządzenie ma więcej portów fiz.

logger = logging.getLogger(__name__)

try:
    import natsort

    natsort_keygen = natsort.natsort_keygen()
    logger.debug("Moduł 'natsort' zaimportowany pomyślnie dla common_device_logic.")
except ImportError:
    logger.warning(
        "Moduł 'natsort' nie znaleziony w common_device_logic. "
        "Sortowanie nazw portów będzie standardowe. Zainstaluj: pip install natsort"
    )


    def natsort_keygen():  # Stub
        return lambda x: str(x)


def classify_ports(ports_data_from_api: List[Dict[str, Any]], device_hostname_for_log: str = "Nieznane urządzenie") -> \
Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Klasyfikuje porty na fizyczne i logiczne na podstawie danych z API.
    Zwraca: (lista_portów_fizycznych, lista_interfejsów_logicznych, info_o_porcie_mgmt0_lub_None)
    """
    physical_ports: List[Dict[str, Any]] = []
    logical_interfaces: List[Dict[str, Any]] = []
    mgmt0_port_info: Optional[Dict[str, Any]] = None

    # Wzorce do identyfikacji typów interfejsów (można rozbudowywać)
    # Te wzorce są dość liberalne, aby złapać popularne nazewnictwo.
    physical_name_patterns = re.compile(
        r'^(Eth|Gi|Te|Fa|Hu|Twe|Fo|mgmt|Management|Serial|Port\s?\d|SFP|XFP|QSFP|em\d|ens\d|eno\d|enp\d+s\d+|ge-|xe-|et-|bri|lan\d|po\d+)',
        re.IGNORECASE
    )
    # Stack porty często mają format X/Y/Z
    stack_port_pattern = re.compile(r'^[a-zA-Z]+[-]?\d+/\d+(/\d+)+$', re.IGNORECASE)
    # Logiczne interfejsy
    logical_name_patterns = re.compile(
        r'^(Vlan|vl|Loopback|Lo|lo\d*|Port-channel|Po|Bundle-Ether|ae|Tunnel|Tun|Null|Nu|Cpu|Fabric|Voice|Async|Group-Async|ipsec|gre|sit|pimreg|mgmt[1-9]|Irq|Service-Engine|Dialer|Virtual-Access|Virtual-Template|Subinterface|BVI|BV|Cellular)|.*\.\d+$',
        # Dodano Subinterface, BVI, Cellular
        re.IGNORECASE
    )

    # Typy IANA (zgodnie z https://www.iana.org/assignments/ianaiftype-mib/ianaiftype-mib)
    # To tylko podzbiór, można rozszerzyć
    physical_types_iana = {
        'ethernetcsmacd', 'fastether', 'gigabitethernet', 'fastetherfx', 'infinitiband', 'sonet', 'sdsl',
        'hdsl', 'shdsl', 'adsl', 'radsl', 'vdsl', 'ieee80211', 'opticalchannel', 'fibrechannel',
        'propvirtual', 'proppointtopointserial', 'ppp', 'eon', 'tokenring', 'atm', 'frameRelay',
        # Dodano atm, frameRelay
        'hssi', 'hippi', 'isdn', 'x25', 'aal5', 'voiceem', 'voicefxo', 'voicefxs', 'digitalpowerline',
        'modem', 'serial', 'docsCableMaclayer', 'docsCableDownstream', 'docsCableUpstream',
        'ieee8023adLag'  # LAG jest fizyczny, jeśli ma MAC
    }
    logical_types_iana = {
        'l3ipvlan', 'softwareLoopback', 'tunnel', 'propMultiplexor', 'bridge', 'other',
        'l2vlan', 'voiceoverip', 'atmSubInterface', 'virtualipaddress', 'mp OvaLink',
        'iana vielf'  # Virtual Ethernet Interface List Format
    }

    temp_mgmt0_candidates = []
    other_ports_to_classify = []

    for port in ports_data_from_api:
        if_name_lower = str(port.get('ifName', '')).lower()
        if_descr_lower = str(port.get('ifDescr', '')).lower()
        # Bardziej elastyczne wykrywanie mgmt0
        if 'mgmt0' == if_name_lower or 'management0' == if_name_lower or \
                'mgmt0' == if_descr_lower or 'management0' == if_descr_lower or \
                (if_name_lower.startswith("mgmt") and if_name_lower.endswith("0")) or \
                (if_descr_lower.startswith("mgmt") and if_descr_lower.endswith("0")):
            temp_mgmt0_candidates.append(port)
        else:
            other_ports_to_classify.append(port)

    if temp_mgmt0_candidates:
        # Wybierz kandydata z MAC adresem, jeśli jest ich wielu
        mgmt0_with_mac = [p for p in temp_mgmt0_candidates if p.get('ifPhysAddress')]
        if mgmt0_with_mac:
            mgmt0_port_info = mgmt0_with_mac[0]
        else:  # Jeśli żaden nie ma MAC, weź pierwszy
            mgmt0_port_info = temp_mgmt0_candidates[0]

        logger.debug(
            f"Port mgmt0 zidentyfikowany dla {device_hostname_for_log}: {mgmt0_port_info.get('ifName')} (ID: {mgmt0_port_info.get('port_id')})")
        if mgmt0_port_info not in physical_ports:  # Unikaj duplikatów, jeśli klasyfikacja poniżej też by go dodała
            physical_ports.append(mgmt0_port_info)

    for port_info in other_ports_to_classify:
        if_name = str(port_info.get('ifName', ''))
        if_descr = str(port_info.get('ifDescr', ''))
        if_type_raw = port_info.get('ifType')  # Może być stringiem lub dictem
        if_phys_address = str(port_info.get('ifPhysAddress', ''))
        if_oper_status = str(port_info.get('ifOperStatus', '')).lower()

        # Pomiń porty, które są "notPresent" LUB "lowerLayerDown" I NIE mają adresu MAC
        # chyba że to już zidentyfikowany mgmt0 (który może być down, ale nadal ważny)
        if port_info != mgmt0_port_info and \
                (if_oper_status == "notpresent" or (if_oper_status == "lowerlayerdown" and not if_phys_address)):
            logger.debug(
                f"Pomijanie portu '{if_name}' ({if_descr}) na {device_hostname_for_log} (status: '{if_oper_status}', brak MAC).")
            # Można go dodać do logicznych, jeśli chcemy je gdzieś listować
            # logical_interfaces.append(port_info)
            continue

        has_mac = bool(
            if_phys_address and len(if_phys_address.replace(':', '').replace('-', '').replace('.', '')) >= 12)

        if_type_iana = ''
        if isinstance(if_type_raw, dict) and 'iana' in if_type_raw:  # LibreNMS często zwraca dict
            if_type_iana = str(if_type_raw['iana']).lower()
        elif isinstance(if_type_raw, str):  # Czasem ifType to tylko string
            if_type_iana = if_type_raw.lower()

        # Kryteria klasyfikacji
        is_physical = False

        # 1. Wyraźne typy IANA
        if if_type_iana in physical_types_iana:
            is_physical = True
        elif if_type_iana in logical_types_iana:
            is_physical = False
        # 2. Wzorce nazw
        elif stack_port_pattern.match(if_name) or stack_port_pattern.match(if_descr):
            is_physical = True
        elif physical_name_patterns.match(if_name) or physical_name_patterns.match(if_descr):
            is_physical = True
        elif logical_name_patterns.match(if_name) or logical_name_patterns.match(if_descr):
            is_physical = False
        # 3. Obecność MAC adresu
        elif has_mac:
            is_physical = True  # Jeśli ma MAC i nie został wcześniej sklasyfikowany jako logiczny, jest fizyczny
        # 4. Domyślnie, jeśli nic nie pasuje, można założyć, że jest logiczny lub nieistotny
        else:
            is_physical = False

        # Specjalna obsługa dla Port-channel/LAG: jeśli ma MAC, jest fizyczny (endpoint), inaczej logiczny (definicja)
        if if_type_iana == 'ieee8023adlag' or \
                'port-channel' in if_name.lower() or 'port-channel' in if_descr.lower() or \
                'bundle-ether' in if_name.lower() or 'bundle-ether' in if_descr.lower() or \
                'lag' in if_name.lower() or 'bond' in if_name.lower():  # Dodano lag, bond
            is_physical = has_mac

        # Unikaj dodawania mgmt0 ponownie, jeśli został już dodany
        if port_info == mgmt0_port_info and mgmt0_port_info in physical_ports:
            continue

        if is_physical:
            if port_info not in physical_ports: physical_ports.append(port_info)
        else:
            port_info['_ifType_iana_debug'] = if_type_iana  # Dodaj do debugowania, jeśli nie fizyczny
            if port_info not in logical_interfaces: logical_interfaces.append(port_info)

    logger.info(
        f"Klasyfikacja portów dla '{device_hostname_for_log}': "
        f"{len(physical_ports)} portów fizycznych (w tym mgmt0, jeśli jest), "
        f"{len(logical_interfaces)} interfejsów logicznych/innych."
    )
    return physical_ports, logical_interfaces, mgmt0_port_info


def calculate_device_chassis_layout(num_display_ports: int) -> DynamicLayoutInfo:
    """Oblicza wymiary chassis i układ portów na podstawie liczby portów do wyświetlenia."""
    if num_display_ports <= 0:
        return DynamicLayoutInfo(width=MIN_CHASSIS_WIDTH, height=DEFAULT_CHASSIS_HEIGHT_NO_PORTS, num_rows=0,
                                 ports_per_row=0)

    ports_per_row_config = DEFAULT_PORTS_PER_ROW
    num_rows = max(1, math.ceil(num_display_ports / ports_per_row_config))

    actual_ports_in_widest_row = ports_per_row_config
    if num_rows == 1:  # Wszystkie porty w jednym rzędzie
        actual_ports_in_widest_row = num_display_ports
    elif num_rows == 2:  # Dla dwóch rzędów, rozdziel porty możliwie równo
        ports_in_row1 = math.ceil(num_display_ports / 2.0)
        # ports_in_row2 = num_display_ports - ports_in_row1 # Niepotrzebne do obliczenia szerokości
        actual_ports_in_widest_row = int(ports_in_row1)  # Szerszy rząd decyduje
    # Dla > 2 rzędów, użyj ports_per_row_config

    chassis_content_width = actual_ports_in_widest_row * PORT_WIDTH + \
                            max(0, actual_ports_in_widest_row - 1) * HORIZONTAL_SPACING
    chassis_width = chassis_content_width + 2 * CHASSIS_PADDING_X
    chassis_width = max(MIN_CHASSIS_WIDTH, chassis_width)

    chassis_content_height = num_rows * PORT_HEIGHT + \
                             max(0, num_rows - 1) * VERTICAL_SPACING
    chassis_height = chassis_content_height + ROW_OFFSET_Y + CHASSIS_PADDING_Y  # ROW_OFFSET_Y to padding od góry do pierwszego rzędu
    chassis_height = max(MIN_CHASSIS_HEIGHT, chassis_height)

    return DynamicLayoutInfo(width=chassis_width, height=chassis_height, num_rows=num_rows,
                             ports_per_row=ports_per_row_config)


class DeviceDisplayData(NamedTuple):
    """Przechowuje wszystkie przygotowane dane urządzenia potrzebne do renderowania."""
    device_api_info: Dict[str, Any]  # Oryginalne dane z API
    canonical_identifier: str
    all_physical_ports: List[Dict[str, Any]]  # Wszystkie sklasyfikowane jako fizyczne
    physical_ports_for_chassis_layout: List[Dict[str, Any]]  # Fizyczne bez mgmt0, posortowane
    logical_interfaces: List[Dict[str, Any]]  # Posortowane
    mgmt0_port_info: Optional[Dict[str, Any]]
    chassis_layout: DynamicLayoutInfo  # Wymiary chassis i info o rzędach
    is_stack: bool
    # Opcjonalnie: pre-generowany HTML dla etykiety informacyjnej, jeśli jest identyczny dla obu builderów
    # info_label_html_content: Optional[str] = None


def prepare_device_display_data(
        device_api_info: Dict[str, Any],
        api_client: LibreNMSAPI,
        device_internal_idx: int  # Dla unikalnych ID i logowania
) -> DeviceDisplayData:
    """
    Pobiera dane portów, klasyfikuje je, oblicza layout i przygotowuje
    inne dane potrzebne do wyświetlenia urządzenia.
    """
    canonical_id = get_canonical_identifier(device_api_info) or f"Urządzenie_idx_{device_internal_idx}"
    logger.debug(
        f"Przygotowywanie danych wyświetlania dla: {canonical_id} (ID API: {device_api_info.get('device_id')})")

    device_id_api = device_api_info.get("device_id")
    ports_data_from_api: List[Dict[str, Any]] = []
    if device_id_api:
        try:
            # Kolumny potrzebne do klasyfikacji i wyświetlania
            ports_data_from_api = api_client.get_ports(
                str(device_id_api),
                columns="port_id,ifIndex,ifName,ifDescr,ifType,ifPhysAddress,ifOperStatus,ifAlias"
            ) or []  # get_ports zwraca listę lub [], obsługa None w kliencie
        except Exception as e:
            logger.error(f"Wyjątek podczas pobierania portów API dla ID: {device_id_api} ({canonical_id}): {e}",
                         exc_info=True)
            # Kontynuuj z pustą listą portów

    all_phys, logical_ifs, mgmt0_info = classify_ports(ports_data_from_api, canonical_id)

    # Porty fizyczne do umieszczenia na głównej części chassis (bez mgmt0)
    phys_ports_for_layout = [p for p in all_phys if p != mgmt0_info]
    try:
        phys_ports_for_layout.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
    except Exception:  # Fallback
        logger.warning(
            f"Błąd natsort dla portów fizycznych urządzenia {canonical_id}, używam standardowego sortowania.")
        phys_ports_for_layout.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))

    # Sortowanie wszystkich portów fizycznych i logicznych (do wyświetlania w etykiecie informacyjnej)
    try:
        all_phys.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
        logical_ifs.sort(key=lambda p: natsort_keygen(p.get('ifName', str(p.get('port_id', 'zzzz')))))
    except Exception:
        logger.warning(f"Błąd natsort dla list portów urządzenia {canonical_id}, używam standardowego sortowania.")
        all_phys.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))
        logical_ifs.sort(key=lambda p: str(p.get('ifName', str(p.get('port_id', 'zzzz')))))

    chassis_layout_info = calculate_device_chassis_layout(len(phys_ports_for_layout))

    is_stack_device = len(all_phys) > STACK_DETECTION_THRESHOLD

    return DeviceDisplayData(
        device_api_info=device_api_info,
        canonical_identifier=canonical_id,
        all_physical_ports=all_phys,
        physical_ports_for_chassis_layout=phys_ports_for_layout,
        logical_interfaces=logical_ifs,
        mgmt0_port_info=mgmt0_info,
        chassis_layout=chassis_layout_info,
        is_stack=is_stack_device
    )


def get_device_render_size_from_prepared_data(prepared_display_data: DeviceDisplayData) -> Tuple[float, float]:
    """Zwraca obliczone wymiary chassis na podstawie przygotowanych danych."""
    return prepared_display_data.chassis_layout.width, prepared_display_data.chassis_layout.height