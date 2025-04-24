# file_io.py
import os
import json
import xml.etree.ElementTree as ET
import pprint # Dodano do debugowania

DEFAULT_IP_LIST_FILE = "ip_list.txt"
DEFAULT_CONNECTIONS_TXT_FILE = "connections.txt"
DEFAULT_CONNECTIONS_JSON_FILE = "connections.json"
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
        sorted_conns = sorted(connections, key=lambda x: (x.get('local_device',''), x.get('local_port','')))
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# Wygenerowana mapa połączeń sieciowych\n")
            f.write("# Format: UrządzenieLokalne:PortLokalny -> UrządzenieZdalne:PortZdalny (VLAN X) via Metoda\n\n")
            for c in sorted_conns:
                vlan_str = f"(VLAN {c.get('vlan')})" if c.get('vlan') is not None else ""
                f.write(f"{c.get('local_device','?')}:{c.get('local_port','?')} -> "
                        f"{c.get('remote_device','?')}:{c.get('remote_port','?')} "
                        f"{vlan_str} via {c.get('discovery_method','?')}\n")
        print(f"✓ Połączenia tekstowe zapisane w {filepath}")
        return True
    except Exception as e:
        print(f"⚠ Błąd zapisu do pliku tekstowego {filepath}: {e}")
        return False

# *** ZMODYFIKOWANA FUNKCJA ZAPISU JSON ***
def save_connections_json(connections, filepath=DEFAULT_CONNECTIONS_JSON_FILE):
    """Zapisuje znalezione połączenia do pliku JSON."""
    if not connections:
        print(f"ⓘ Brak połączeń do zapisania w {filepath}.")
        return False
    try:
        # Sortuj listę połączeń (nadal dobra praktyka)
        # Upewnijmy się, że sortowanie obsługuje None
        sorted_conns = sorted(connections, key=lambda x: (str(x.get('local_device','')), str(x.get('local_port',''))))

        # *** DODANO DEBUG PRINT: Sprawdź dane tuż przed zapisem ***
        print(f"\n--- DEBUG: Dane przekazane do save_connections_json (pierwsze 2) ---")
        if sorted_conns:
            print(f"Liczba połączeń do zapisania: {len(sorted_conns)}")
            for idx, conn_debug in enumerate(sorted_conns[:2]):
                 print(f"Połączenie #{idx}:")
                 pprint.pprint(conn_debug)
        else:
            print("Lista połączeń do zapisania jest pusta.")
        print("--- KONIEC DEBUG ---")
        # **********************************************************

        with open(filepath, "w", encoding="utf-8") as f:
            # *** ZMIANA: Zapisz bezpośrednio posortowaną listę 'sorted_conns' ***
            # Nie tworzymy już nowej listy 'json_data'
            json.dump(sorted_conns, f, indent=4, ensure_ascii=False)
            # *******************************************************************
        print(f"✓ Połączenia JSON zapisane w {filepath}")
        return True
    except Exception as e:
        print(f"⚠ Błąd zapisu do pliku JSON {filepath}: {e}")
        return False
# *****************************************

def load_connections_json(filepath=DEFAULT_CONNECTIONS_JSON_FILE):
    """Wczytuje dane o połączeniach z pliku JSON."""
    if not os.path.exists(filepath):
        print(f"⚠ Plik połączeń JSON '{filepath}' nie istnieje.")
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            connections = json.load(f)
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
         try: ET.indent(xml_tree, space="  ", level=0)
         except AttributeError: pass
         xml_string = ET.tostring(xml_tree.getroot(), encoding="utf-8", method="xml").decode("utf-8")
         if not xml_string.startswith('<?xml'):
              xml_string = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_string
         with open(filepath, "w", encoding="utf-8") as f: f.write(xml_string)
         print(f"✓ Diagram Draw.io zapisany jako {filepath}")
         return True
     except Exception as e:
         print(f"⚠ Błąd zapisu diagramu Draw.io do pliku {filepath}: {e}")
         return False
