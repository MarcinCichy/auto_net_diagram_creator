# file_io.py
import os
import json
import xml.etree.ElementTree as ET
import pprint
import logging
from typing import List, Dict, Any, Optional # <<< DODANO TEN IMPORT

logger = logging.getLogger(__name__)

DEFAULT_IP_LIST_FILE = "ip_list.txt"
DEFAULT_CONNECTIONS_TXT_FILE = "connections.txt"
DEFAULT_CONNECTIONS_JSON_FILE = "connections.json"
DEFAULT_DIAGRAM_FILE = "network_diagram.drawio"

def load_ip_list(filepath: str = DEFAULT_IP_LIST_FILE) -> List[str]: # Teraz 'List' jest zdefiniowane
    """Wczytuje listę IP/hostname z pliku, ignorując puste linie i komentarze."""
    if not os.path.exists(filepath):
        logger.warning(f"Plik listy IP '{filepath}' nie istnieje.")
        return []
    lines_read: List[str] = [] # Użycie 'List' wewnątrz funkcji
    try:
        with open(filepath, 'r', encoding="utf-8") as f:
            lines_read = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        if not lines_read:
            logger.info(f"Plik listy IP '{filepath}' jest pusty lub zawiera tylko komentarze/puste linie.")
        else:
            logger.info(f"Wczytano {len(lines_read)} adresów/hostów z '{filepath}'.")
    except Exception as e:
        logger.error(f"Błąd odczytu pliku listy IP '{filepath}': {e}", exc_info=True)
        return []
    return lines_read

def save_connections_txt(connections: List[Dict[str, Any]], filepath: str = DEFAULT_CONNECTIONS_TXT_FILE) -> bool:
    """Zapisuje znalezione połączenia do pliku tekstowego."""
    if not connections:
        logger.info(f"Brak połączeń do zapisania w pliku tekstowym '{filepath}'.")
        return True
    try:
        sorted_conns = sorted(connections, key=lambda x: (str(x.get('local_device','')), str(x.get('local_port',''))))
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# Wygenerowana mapa połączeń sieciowych\n")
            f.write("# Format: UrządzenieLokalne:PortLokalny -> UrządzenieZdalne:PortZdalny (VLAN X) via Metoda\n\n")
            for c in sorted_conns:
                vlan_str = f"(VLAN {c.get('vlan')})" if c.get('vlan') is not None else ""
                f.write(f"{c.get('local_device','?')}:{c.get('local_port','?')} -> "
                        f"{c.get('remote_device','?')}:{c.get('remote_port','?')} "
                        f"{vlan_str} via {c.get('discovery_method','?')}\n")
        logger.info(f"✓ Połączenia tekstowe ({len(sorted_conns)} wpisów) zapisane w '{filepath}'")
        return True
    except Exception as e:
        logger.error(f"Błąd zapisu połączeń do pliku tekstowego '{filepath}': {e}", exc_info=True)
        return False

def save_connections_json(connections: List[Dict[str, Any]], filepath: str = DEFAULT_CONNECTIONS_JSON_FILE) -> bool:
    """Zapisuje znalezione połączenia do pliku JSON."""
    if not connections:
        logger.info(f"Brak połączeń do zapisania w pliku JSON '{filepath}'.")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump([], f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Błąd zapisu pustego pliku JSON '{filepath}': {e}", exc_info=True)
            return False

    try:
        sorted_conns = sorted(connections, key=lambda x: (str(x.get('local_device','')), str(x.get('local_port',''))))
        logger.debug(f"Przygotowywanie do zapisu {len(sorted_conns)} połączeń do pliku JSON '{filepath}'. Pierwsze połączenie (jeśli istnieje): {pprint.pformat(sorted_conns[0]) if sorted_conns else 'Brak'}")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(sorted_conns, f, indent=4, ensure_ascii=False)
        logger.info(f"✓ Połączenia JSON ({len(sorted_conns)} wpisów) zapisane w '{filepath}'")
        return True
    except Exception as e:
        logger.error(f"Błąd zapisu połączeń do pliku JSON '{filepath}': {e}", exc_info=True)
        return False

def load_connections_json(filepath: str = DEFAULT_CONNECTIONS_JSON_FILE) -> List[Dict[str, Any]]:
    """Wczytuje dane o połączeniach z pliku JSON."""
    if not os.path.exists(filepath):
        logger.warning(f"Plik połączeń JSON '{filepath}' nie istnieje. Zwracam pustą listę.")
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            loaded_connections = json.load(f)
        if isinstance(loaded_connections, list):
            logger.info(f"✓ Wczytano {len(loaded_connections)} połączeń z '{filepath}'.")
            return loaded_connections
        else:
            logger.error(f"Nieprawidłowy format danych w '{filepath}' - oczekiwano listy, otrzymano {type(loaded_connections)}. Zwracam pustą listę.")
            return []
    except json.JSONDecodeError as e:
        logger.error(f"Błąd parsowania pliku JSON z połączeniami '{filepath}': {e}. Zwracam pustą listę.")
        return []
    except Exception as e:
        logger.error(f"Błąd odczytu pliku JSON z połączeniami '{filepath}': {e}", exc_info=True)
        return []

def save_diagram_xml(xml_tree: ET.ElementTree, filepath: str = DEFAULT_DIAGRAM_FILE) -> bool:
    """Zapisuje drzewo XML diagramu Draw.io do pliku."""
    if xml_tree is None or xml_tree.getroot() is None:
        logger.warning(f"Próba zapisu pustego lub nieprawidłowego drzewa XML diagramu do '{filepath}'. Pomijam.")
        return False
    try:
        if hasattr(ET, 'indent'):
            ET.indent(xml_tree.getroot(), space="  ", level=0)
        xml_bytes = ET.tostring(xml_tree.getroot(), encoding="utf-8", method="xml")
        xml_string = xml_bytes.decode("utf-8")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(xml_string)
        logger.info(f"✓ Diagram Draw.io zapisany jako '{filepath}'")
        return True
    except Exception as e:
        logger.error(f"Błąd zapisu diagramu Draw.io do pliku '{filepath}': {e}", exc_info=True)
        return False