# file_io.py
import os
import json
import xml.etree.ElementTree as ET
import pprint
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Usunięto globalne stałe DEFAULT_..._FILE

def load_ip_list(filepath: str) -> List[str]: # Usunięto wartość domyślną
    """Wczytuje listę IP/hostname z pliku, ignorując puste linie i komentarze."""
    if not os.path.exists(filepath):
        logger.warning(f"Plik listy IP '{filepath}' nie istnieje.")
        return []
    lines_read: List[str] = []
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

def save_connections_txt(connections: List[Dict[str, Any]], filepath: str) -> bool: # Usunięto wartość domyślną
    """Zapisuje znalezione połączenia do pliku tekstowego."""
    if not connections:
        logger.info(f"Brak połączeń do zapisania w pliku tekstowym '{filepath}'.")
        # Zapisz pusty plik z nagłówkiem, aby uniknąć błędów, jeśli użytkownik oczekuje pliku
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("# Wygenerowana mapa połączeń sieciowych\n")
                f.write("# Format: UrządzenieLokalne:PortLokalny -> UrządzenieZdalne:PortZdalny (VLAN X) via Metoda\n\n")
                f.write("# Brak połączeń do zapisania.\n")
            return True
        except Exception as e:
            logger.error(f"Błąd zapisu pustego pliku tekstowego połączeń '{filepath}': {e}", exc_info=True)
            return False

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

def save_connections_json(connections: List[Dict[str, Any]], filepath: str) -> bool: # Usunięto wartość domyślną
    """Zapisuje znalezione połączenia do pliku JSON."""
    # Jeśli nie ma połączeń, zapisz pustą listę JSON
    data_to_save = []
    if connections:
        data_to_save = sorted(connections, key=lambda x: (str(x.get('local_device','')), str(x.get('local_port',''))))
        logger.debug(f"Przygotowywanie do zapisu {len(data_to_save)} połączeń do pliku JSON '{filepath}'.")
    else:
        logger.info(f"Brak połączeń do zapisania w pliku JSON '{filepath}'. Zapisuję pustą listę.")

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=4, ensure_ascii=False)
        logger.info(f"✓ Połączenia JSON ({len(data_to_save)} wpisów) zapisane w '{filepath}'")
        return True
    except Exception as e:
        logger.error(f"Błąd zapisu połączeń do pliku JSON '{filepath}': {e}", exc_info=True)
        return False

def load_connections_json(filepath: str) -> List[Dict[str, Any]]: # Usunięto wartość domyślną
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

def save_diagram_xml(xml_tree: ET.ElementTree, filepath: str) -> bool: # Usunięto wartość domyślną
    """Zapisuje drzewo XML diagramu Draw.io do pliku."""
    if xml_tree is None or xml_tree.getroot() is None:
        logger.warning(f"Próba zapisu pustego lub nieprawidłowego drzewa XML diagramu do '{filepath}'. Pomijam.")
        return False
    try:
        # ET.indent jest dostępne od Python 3.9
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