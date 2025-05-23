#!/usr/bin/env python3
# --- main_app.py ---

import sys
import time
import argparse
import os
import logging
from typing import Dict, Any, List, Optional # Dodano Optional
from utils import setup_logging

import sys
print(">>> Interpreter:", sys.executable)
print(">>> sys.path:")
for p in sys.path: print("   ", p)

# --- Importy z naszych modułów ---
try:
    import config_loader
    import file_io
    from librenms_client import LibreNMSAPI
    from network_discoverer import NetworkDiscoverer
    from diagram_generator import DiagramGenerator
    # from utils import setup_logging # Już zaimportowane wyżej
except ImportError as e:
    print(f"Błąd importu modułu: {e}. Upewnij się, że wszystkie pliki .py znajdują się w odpowiednim miejscu.")
    sys.exit(1)
except FileNotFoundError as e:
    print(f"Błąd: Brak pliku {e.filename}. Upewnij się, że wszystkie pliki .py istnieją.")
    sys.exit(1)

# --- Stałe ---
DEFAULT_IP_LIST_FILE = "ip_list.txt"
DEFAULT_CONNECTIONS_TXT_FILE = "connections.txt"
DEFAULT_CONNECTIONS_JSON_FILE = "connections.json"
DEFAULT_DIAGRAM_TEMPLATE_FILE = "switch.drawio" # Dla stylów Draw.io
DEFAULT_DIAGRAM_OUTPUT_DRAWIO_FILE = "network_diagram.drawio"
DEFAULT_DIAGRAM_OUTPUT_SVG_FILE = "network_diagram.svg"

logger = logging.getLogger(__name__)

class Application:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config: Dict[str, Any] = {}
        self.api_client: Optional[LibreNMSAPI] = None

    def setup(self) -> bool:
        logger.info("--- Uruchamianie Aplikacji ---")
        try:
            self.config = config_loader.get_env_config()
        except ValueError as e:
            logger.critical(f"Błąd krytyczny konfiguracji .env: {e}")
            return False
        except FileNotFoundError: # Obsługa braku .env
             logger.critical("Błąd krytyczny: Plik .env nie został znaleziony. Upewnij się, że istnieje i zawiera BASE_URL oraz API_KEY.")
             return False
        except Exception as e:
             logger.critical(f"Nieoczekiwany błąd ładowania konfiguracji .env: {e}")
             return False


        base_url = self.config.get("base_url")
        api_key = self.config.get("api_key")

        if not base_url or not api_key: # Podwójne sprawdzenie, get_env_config powinien już to rzucić
            logger.critical("Błąd krytyczny: Brak base_url lub api_key w konfiguracji .env")
            return False

        self.api_client = LibreNMSAPI(
            base_url,
            api_key,
            verify_ssl=(not self.args.no_verify_ssl)
        )
        logger.info("Klient API LibreNMS zainicjalizowany.")
        return True

    def run(self) -> None:
        app_start_time = time.time()

        if not self.setup():
            sys.exit(1)

        run_discovery_flag = self.args.discover
        run_diagram_flag = self.args.diagram

        if not run_discovery_flag and not run_diagram_flag:
            logger.info("Nie podano flagi --discover ani --diagram. Domyślnie uruchamiam obie fazy.")
            run_discovery_flag = True
            run_diagram_flag = True

        if run_discovery_flag:
            self._run_discovery_phase()

        if run_diagram_flag:
            self._run_diagram_phase()

        app_end_time = time.time()
        logger.info(f"\n--- Zakończono. Całkowity czas: {app_end_time - app_start_time:.2f} sek. ---")

    def _run_discovery_phase(self) -> None:
        if not self.api_client:
             logger.error("Klient API nie został zainicjalizowany. Pomijanie fazy odkrywania.")
             return

        logger.info("\n=== Rozpoczynanie Fazy Odkrywania Połączeń ===")
        start_time = time.time()
        discoverer = NetworkDiscoverer(
            api_client=self.api_client,
            config=self.config,
            ip_list_path=self.args.ip_list,
            conn_txt_path=self.args.conn_txt,
            conn_json_path=self.args.conn_json
        )
        discoverer.discover_connections()
        end_time = time.time()
        logger.info(f"=== Zakończono Fazę Odkrywania Połączeń (czas: {end_time - start_time:.2f} sek.) ===")


    def _run_diagram_phase(self) -> None:
        if not self.api_client:
             logger.error("Klient API nie został zainicjalizowany. Pomijanie fazy generowania diagramu.")
             return

        # Sprawdzenie szablonu Draw.io (potrzebny do stylów, nawet dla SVG jako referencja)
        if not os.path.exists(self.args.template):
            logger.warning(f"Błąd: Plik szablonu DrawIO '{self.args.template}' nie istnieje. Style mogą być domyślne.")
            # Kontynuujemy, używając domyślnych stylów w StyleInfo

        if not os.path.exists(self.args.conn_json):
             logger.warning(f"Plik połączeń '{self.args.conn_json}' nie istnieje. Diagramy zostaną wygenerowane bez linii połączeń.")

        logger.info("\n=== Rozpoczynanie Fazy Generowania Diagramów (DrawIO i SVG) ===")
        start_time = time.time()

        generator = DiagramGenerator(
            api_client=self.api_client,
            config=self.config,
            ip_list_path=self.args.ip_list,
            template_path=self.args.template,
            output_path_drawio=self.args.diagram_out_drawio,
            output_path_svg=self.args.diagram_out_svg,
            connections_json_path=self.args.conn_json
        )
        generator.generate_diagram()
        end_time = time.time()
        logger.info(f"=== Zakończono Fazę Generowania Diagramów (czas: {end_time - start_time:.2f} sek.) ===")


def main():
    # Użyj log_file zdefiniowany w utils.py, jeśli tam jest, lub podaj konkretną nazwę tutaj
    setup_logging(level=logging.INFO, log_to_file=True, log_file="auto_diagram_app.log")

    parser = argparse.ArgumentParser(description="Narzędzie do odkrywania połączeń sieciowych i generowania diagramów Draw.io/SVG.")
    parser.add_argument("--discover", action="store_true", help="Uruchom tylko fazę odkrywania.")
    parser.add_argument("--diagram", action="store_true", help="Uruchom tylko fazę generowania diagramów (DrawIO i SVG).")

    parser.add_argument("--ip-list", default=DEFAULT_IP_LIST_FILE, help=f"Plik z listą IP/Hostname (dom: {DEFAULT_IP_LIST_FILE}).")
    parser.add_argument("--conn-txt", default=DEFAULT_CONNECTIONS_TXT_FILE, help=f"Plik .txt z połączeniami (dom: {DEFAULT_CONNECTIONS_TXT_FILE}).")
    parser.add_argument("--conn-json", default=DEFAULT_CONNECTIONS_JSON_FILE, help=f"Plik .json z połączeniami (dom: {DEFAULT_CONNECTIONS_JSON_FILE}).")
    parser.add_argument("--template", default=DEFAULT_DIAGRAM_TEMPLATE_FILE, help=f"Plik szablonu .drawio dla stylów (dom: {DEFAULT_DIAGRAM_TEMPLATE_FILE}).")

    parser.add_argument("--diagram-out-drawio", default=DEFAULT_DIAGRAM_OUTPUT_DRAWIO_FILE,
                        help=f"Plik wyjściowy diagramu .drawio (dom: {DEFAULT_DIAGRAM_OUTPUT_DRAWIO_FILE}).")
    parser.add_argument("--diagram-out-svg", default=DEFAULT_DIAGRAM_OUTPUT_SVG_FILE,
                        help=f"Plik wyjściowy diagramu .svg (dom: {DEFAULT_DIAGRAM_OUTPUT_SVG_FILE}).")

    parser.add_argument("--no-verify-ssl", action="store_true", help="Wyłącz weryfikację SSL dla API LibreNMS.")
    args = parser.parse_args()

    app = Application(args)
    app.run()

if __name__ == "__main__":
    main()