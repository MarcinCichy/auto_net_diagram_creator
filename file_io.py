# file_io.py
import os
import json
import xml.etree.ElementTree as ET

DEFAULT_IP_LIST_FILE = "ip_list.txt"
DEFAULT_CONNECTIONS_TXT_FILE = "connections.txt"
DEFAULT_CONNECTIONS_JSON_FILE = "connections.json" # Domyślna ścieżka do pliku JSON
DEFAULT_DIAGRAM_FILE = "network_diagram.drawio"

def load_ip_list(filepath=DEFAULT_IP_LIST_FILE):
    """Wczytuje listę IP/hostname z pliku, ignorując puste linie i komentarze."""
    if not os.path.exists(filepath):
        print(f"⚠ Plik {filepath} nie istnieje.")
        return []
    lines = []
    try:
        with open(filepath, 'r', encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
            if not lines:
                 print(f"ⓘ Plik {filepath} jest pusty lub zawiera tylko komentarze/puste linie.")
    except Exception as e:
        print(f"⚠ Błąd odczytu pliku {filepath}: {e}")
    return lines

def save_connections_txt(connections, filepath=DEFAULT_CONNECTIONS_TXT_FILE):
    """Zapisuje znalezione połączenia do pliku tekstowego."""
    if not connections:
        print(f"ⓘ Brak połączeń do zapisania w {filepath}.")
        return False
    try:
        sorted_conns = sorted(connections, key=lambda x: (x.get('local_host',''), x.get('local_if','')))
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# Wygenerowana mapa połączeń sieciowych\n")
            f.write("# Format: UrządzenieLokalne:PortLokalny -> UrządzenieZdalne:PortZdalny (VLAN X) via Metoda\n\n")
            for c in sorted_conns:
                vlan_str = f"(VLAN {c.get('vlan')})" if c.get('vlan') is not None else ""
                f.write(f"{c.get('local_host','?')}:{c.get('local_if','?')} -> "
                        f"{c.get('neighbor_host','?')}:{c.get('neighbor_if','?')} "
                        f"{vlan_str} via {c.get('via','?')}\n")
        print(f"✓ Połączenia tekstowe zapisane w {filepath}")
        return True
    except Exception as e:
        print(f"⚠ Błąd zapisu do pliku tekstowego {filepath}: {e}")
        return False

def save_connections_json(connections, filepath=DEFAULT_CONNECTIONS_JSON_FILE):
    """Zapisuje znalezione połączenia do pliku JSON."""
    if not connections:
        print(f"ⓘ Brak połączeń do zapisania w {filepath}.")
        return False
    try:
        sorted_conns = sorted(connections, key=lambda x: (x.get('local_host',''), x.get('local_if','')))
        json_data = []
        for c in sorted_conns:
             json_data.append({
                "local_device": c.get('local_host'),
                "local_port": c.get('local_if'),
                "remote_device": c.get('neighbor_host'),
                "remote_port": c.get('neighbor_if'),
                "vlan": c.get('vlan'),
                "discovery_method": c.get('via')
             })
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=4, ensure_ascii=False)
        print(f"✓ Połączenia JSON zapisane w {filepath}")
        return True
    except Exception as e:
        print(f"⚠ Błąd zapisu do pliku JSON {filepath}: {e}")
        return False

# *** NOWA FUNKCJA ***
def load_connections_json(filepath=DEFAULT_CONNECTIONS_JSON_FILE):
    """Wczytuje dane o połączeniach z pliku JSON."""
    if not os.path.exists(filepath):
        print(f"⚠ Plik połączeń JSON '{filepath}' nie istnieje.")
        return [] # Zwróć pustą listę, jeśli plik nie istnieje
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            connections = json.load(f)
            # Podstawowa walidacja - sprawdź czy to lista
            if isinstance(connections, list):
                 print(f"✓ Wczytano {len(connections)} połączeń z {filepath}.")
                 return connections
            else:
                 print(f"⚠ Nieprawidłowy format danych w {filepath} - oczekiwano listy.")
                 return []
    except json.JSONDecodeError as e:
        print(f"⚠ Błąd parsowania pliku JSON z połączeniami {filepath}: {e}")
        return []
    except Exception as e:
        print(f"⚠ Błąd odczytu pliku JSON z połączeniami {filepath}: {e}")
        return []

def save_diagram_xml(xml_tree: ET.ElementTree, filepath=DEFAULT_DIAGRAM_FILE):
     """Zapisuje drzewo XML diagramu Draw.io do pliku."""
     if xml_tree is None:
         print("⚠ Próba zapisu pustego drzewa XML diagramu.")
         return False
     try:
         try:
             ET.indent(xml_tree, space="  ", level=0)
         except AttributeError:
             pass
         xml_string = ET.tostring(xml_tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
         if not xml_string.startswith('<?xml'):
              xml_string = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_string
         with open(filepath, "w", encoding="utf-8") as f:
             f.write(xml_string)
         print(f"✓ Diagram Draw.io zapisany jako {filepath}")
         return True
     except Exception as e:
         print(f"⚠ Błąd zapisu diagramu Draw.io do pliku {filepath}: {e}")
         return False