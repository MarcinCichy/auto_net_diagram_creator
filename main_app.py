#!/usr/bin/env python3
# --- main_app.py ---

import sys  # sys jest potrzebny do sys.exit() i parsera argumentów
import time
import argparse
import os  # Potrzebny do os.path.exists
import logging  # Potrzebny do inicjalizacji loggera modułu
from typing import List, Dict, Any, Optional # <<< DODANO TEN IMPORT

# --- Konfiguracja logowania jako pierwsza rzecz ---
# utils.py musi być w PYTHONPATH
try:
    from utils import setup_logging

    # Użyj poziomu INFO dla mniej szczegółowych logów produkcyjnych, DEBUG dla developmentu
    # Można to też ustawić przez argument CLI w przyszłości
    LOG_LEVEL = logging.INFO  # lub logging.DEBUG
    LOG_TO_FILE = True
    LOG_FILE_NAME = "auto_net_diagram_creator.log"  # Centralna nazwa pliku logów
    setup_logging(level=LOG_LEVEL, log_to_file=LOG_TO_FILE, log_file=LOG_FILE_NAME)
except ImportError as e_utils:
    # Krytyczny błąd, jeśli nie można załadować utils.py do skonfigurowania logowania
    print(f"KRYTYCZNY BŁĄD: Nie można zaimportować 'utils' do konfiguracji logowania: {e_utils}", file=sys.stderr)
    print("Upewnij się, że plik utils.py znajduje się w PYTHONPATH.", file=sys.stderr)
    sys.exit(1)
except Exception as e_setup_log:
    print(f"KRYTYCZNY BŁĄD: Nieoczekiwany błąd podczas konfiguracji logowania: {e_setup_log}", file=sys.stderr)
    sys.exit(1)

# Główny logger dla tego modułu
logger = logging.getLogger(__name__)  # Teraz logger jest już skonfigurowany przez setup_logging

# --- Importy pozostałych modułów aplikacji ---
try:
    import config_loader
    import file_io
    from librenms_client import LibreNMSAPI
    from network_discoverer import NetworkDiscoverer
    from diagram_generator import DiagramGenerator
except ImportError as e_mod:
    logger.critical(
        f"Błąd importu modułu aplikacji: {e_mod}. Upewnij się, że wszystkie pliki .py znajdują się w odpowiednim miejscu i zależności są zainstalowane.",
        exc_info=True)
    sys.exit(1)
except FileNotFoundError as e_fnf:  # Rzadziej, ale możliwe jeśli plik jest dynamicznie ładowany
    logger.critical(f"Błąd: Brak pliku modułu {e_fnf.filename}. Upewnij się, że wszystkie pliki .py istnieją.",
                    exc_info=True)
    sys.exit(1)
except Exception as e_imp_other:
    logger.critical(f"Nieoczekiwany błąd podczas importowania modułów aplikacji: {e_imp_other}", exc_info=True)
    sys.exit(1)

# --- Stałe ścieżek plików (mogą być też wczytywane z konfiguracji) ---
DEFAULT_IP_LIST_FILE = "ip_list.txt"
DEFAULT_CONNECTIONS_TXT_FILE = "connections.txt"
DEFAULT_CONNECTIONS_JSON_FILE = "connections.json"
DEFAULT_DIAGRAM_TEMPLATE_FILE = "switch.drawio"  # Dla stylów Draw.io
DEFAULT_DIAGRAM_OUTPUT_DRAWIO_FILE = "network_diagram.drawio"
DEFAULT_DIAGRAM_OUTPUT_SVG_FILE = "network_diagram.svg"


class Application:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config: Dict[str, Any] = {}
        self.api_client: Optional[LibreNMSAPI] = None
        logger.debug("Application zainicjalizowana z argumentami: %s", args)

    def setup(self) -> bool:
        logger.info("--- Konfiguracja Aplikacji ---")
        try:
            self.config = config_loader.get_env_config()
            logger.info("Konfiguracja środowiskowa (.env) i poświadczenia CLI wczytane pomyślnie.")
        except ValueError as e_val_conf:  # Rzucane przez get_env_config przy braku BASE_URL/API_KEY
            logger.critical(f"Krytyczny błąd konfiguracji .env: {e_val_conf}")
            return False
        except FileNotFoundError:  # Jeśli .env nie istnieje (python-dotenv może nie rzucać tego, ale na wszelki wypadek)
            logger.critical(
                "Krytyczny błąd: Plik .env nie został znaleziony. Upewnij się, że istnieje i zawiera BASE_URL oraz API_KEY.")
            return False
        except Exception as e_conf_load:
            logger.critical(f"Nieoczekiwany błąd ładowania konfiguracji: {e_conf_load}", exc_info=True)
            return False

        base_url = self.config.get("base_url")
        api_key = self.config.get("api_key")

        # get_env_config już rzuca ValueError, ale dla pewności
        if not base_url or not api_key:
            logger.critical("Krytyczny błąd: Brak base_url lub api_key w konfiguracji po jej wczytaniu.")
            return False

        try:
            self.api_client = LibreNMSAPI(
                base_url,
                api_key,
                verify_ssl=(not self.args.no_verify_ssl),
                timeout=self.args.api_timeout  # Dodajmy argument CLI dla timeoutu API
            )
            logger.info("Klient API LibreNMS zainicjalizowany pomyślnie.")
        except ValueError as e_api_init:  # Rzucane przez LibreNMSAPI jeśli base_url/api_key są puste
            logger.critical(f"Błąd inicjalizacji klienta API LibreNMS: {e_api_init}")
            return False
        except Exception as e_api_other:
            logger.critical(f"Nieoczekiwany błąd podczas inicjalizacji klienta API LibreNMS: {e_api_other}",
                            exc_info=True)
            return False

        return True

    def run(self) -> None:
        app_start_time = time.time()
        logger.info("=== Uruchamianie Głównej Aplikacji ===")

        if not self.setup():
            logger.critical("Nie udało się skonfigurować aplikacji. Zamykanie.")
            sys.exit(1)

        run_discovery_flag = self.args.discover
        run_diagram_flag = self.args.diagram

        # Jeśli żadna flaga nie jest podana, uruchom obie fazy
        if not run_discovery_flag and not run_diagram_flag:
            logger.info("Nie podano flagi --discover ani --diagram. Domyślnie uruchamiam obie fazy.")
            run_discovery_flag = True
            run_diagram_flag = True

        if run_discovery_flag:
            self._run_discovery_phase()
        else:
            logger.info("Pomijanie fazy odkrywania połączeń (--discover nie ustawione).")

        if run_diagram_flag:
            if not os.path.exists(self.args.conn_json) and run_discovery_flag == False:
                logger.warning(
                    f"Faza generowania diagramu uruchomiona, ale faza odkrywania była pominięta, a plik połączeń '{self.args.conn_json}' nie istnieje. Diagram może być pusty lub nie zawierać połączeń.")
            self._run_diagram_phase()
        else:
            logger.info("Pomijanie fazy generowania diagramów (--diagram nie ustawione).")

        app_end_time = time.time()
        logger.info(f"=== Zakończono Działanie Aplikacji. Całkowity czas: {app_end_time - app_start_time:.2f} sek. ===")

    def _run_discovery_phase(self) -> None:
        if not self.api_client:  # Powinno być już obsłużone w setup
            logger.error("Klient API nie został zainicjalizowany. Nie można uruchomić fazy odkrywania.")
            return

        logger.info("--- Rozpoczynanie Fazy Odkrywania Połączeń ---")
        start_time = time.time()
        try:
            discoverer = NetworkDiscoverer(
                api_client=self.api_client,
                config=self.config,
                ip_list_path=self.args.ip_list,
                conn_txt_path=self.args.conn_txt,
                conn_json_path=self.args.conn_json
            )
            discoverer.discover_connections()
        except Exception as e_discover:
            logger.error(f"Wystąpił nieoczekiwany błąd podczas fazy odkrywania: {e_discover}", exc_info=True)

        end_time = time.time()
        logger.info(f"--- Zakończono Fazę Odkrywania Połączeń (czas: {end_time - start_time:.2f} sek.) ---")

    def _run_diagram_phase(self) -> None:
        if not self.api_client:
            logger.error("Klient API nie został zainicjalizowany. Nie można uruchomić fazy generowania diagramu.")
            return

        # Sprawdzenie pliku szablonu Draw.io (potrzebny do stylów, nawet jeśli tylko SVG jest generowane)
        if not os.path.exists(self.args.template):
            logger.warning(f"Plik szablonu DrawIO '{self.args.template}' nie istnieje lub jest niedostępny. "
                           "Użyte zostaną domyślne style wbudowane w kodzie (jeśli zdefiniowane).")
            # Kontynuujemy, generatory powinny mieć wbudowane domyślne StyleInfo

        if not os.path.exists(self.args.conn_json):
            logger.warning(
                f"Plik połączeń JSON '{self.args.conn_json}' nie istnieje. Diagramy zostaną wygenerowane bez linii połączeń.")
            # Kontynuujemy, diagramy urządzeń mogą być nadal użyteczne

        logger.info("--- Rozpoczynanie Fazy Generowania Diagramów (DrawIO i SVG) ---")
        start_time = time.time()
        try:
            generator = DiagramGenerator(
                api_client=self.api_client,
                config=self.config,
                ip_list_path=self.args.ip_list,
                template_path=self.args.template,  # Przekaż ścieżkę, generator obsłuży brak pliku
                output_path_drawio=self.args.diagram_out_drawio,
                output_path_svg=self.args.diagram_out_svg,
                connections_json_path=self.args.conn_json
            )
            generator.generate_diagram()
        except Exception as e_diagram:
            logger.error(f"Wystąpił nieoczekiwany błąd podczas fazy generowania diagramów: {e_diagram}", exc_info=True)

        end_time = time.time()
        logger.info(f"--- Zakończono Fazę Generowania Diagramów (czas: {end_time - start_time:.2f} sek.) ---")


def main():
    # Konfiguracja logowania jest już wyżej, przed importami modułów aplikacji

    parser = argparse.ArgumentParser(
        description="Narzędzie do odkrywania połączeń sieciowych i generowania diagramów Draw.io/SVG z danych LibreNMS.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter  # Lepsze formatowanie pomocy
    )

    # Flagi sterujące fazami
    parser.add_argument("--discover", action="store_true", help="Uruchom tylko fazę odkrywania połączeń.")
    parser.add_argument("--diagram", action="store_true",
                        help="Uruchom tylko fazę generowania diagramów (DrawIO i SVG).")

    # Ścieżki plików wejściowych/wyjściowych
    parser.add_argument("--ip-list", default=DEFAULT_IP_LIST_FILE,
                        help="Plik z listą IP/Hostname urządzeń do przetworzenia.")
    parser.add_argument("--conn-txt", default=DEFAULT_CONNECTIONS_TXT_FILE,
                        help="Plik wyjściowy .txt z odkrytymi połączeniami.")
    parser.add_argument("--conn-json", default=DEFAULT_CONNECTIONS_JSON_FILE,
                        help="Plik wejściowy/wyjściowy .json z odkrytymi połączeniami (używany przez generator diagramów).")
    parser.add_argument("--template", default=DEFAULT_DIAGRAM_TEMPLATE_FILE,
                        help="Plik szablonu .drawio używany do wczytywania stylów (np. dla kształtów urządzeń).")
    parser.add_argument("--diagram-out-drawio", default=DEFAULT_DIAGRAM_OUTPUT_DRAWIO_FILE,
                        help="Plik wyjściowy dla diagramu w formacie .drawio.")
    parser.add_argument("--diagram-out-svg", default=DEFAULT_DIAGRAM_OUTPUT_SVG_FILE,
                        help="Plik wyjściowy dla diagramu w formacie .svg.")

    # Ustawienia API i inne
    parser.add_argument("--no-verify-ssl", action="store_true",
                        help="Wyłącz weryfikację certyfikatu SSL dla połączeń z API LibreNMS.")
    parser.add_argument("--api-timeout", type=int, default=20,  # Zwiększono domyślny timeout
                        help="Timeout (w sekundach) dla zapytań do API LibreNMS.")

    # Można dodać argumenty do kontrolowania poziomu logowania, np. --verbose, --debug
    # parser.add_argument("-v", "--verbose", action="store_const", const=logging.INFO, dest="loglevel", help="Ustaw poziom logowania na INFO.")
    # parser.add_argument("--debug", action="store_const", const=logging.DEBUG, dest="loglevel", help="Ustaw poziom logowania na DEBUG (bardzo szczegółowe).")
    # Wtedy setup_logging(level=args.loglevel or LOG_LEVEL_DEFAULT)

    args = parser.parse_args()

    # Sprawdzenie, czy podano .env (choć dotenv robi to cicho)
    if not os.path.exists(".env"):
        logger.warning(
            "Plik .env nie został znaleziony w bieżącym katalogu. Upewnij się, że istnieje i zawiera BASE_URL oraz API_KEY.")
        # Aplikacja może próbować działać dalej, jeśli zmienne są ustawione w środowisku systemowym.

    try:
        app = Application(args)
        app.run()
    except Exception as e_main_app:  # Ogólny "łapacz" dla nieprzewidzianych błędów w głównym przepływie
        logger.critical(f"Nieoczekiwany krytyczny błąd na najwyższym poziomie aplikacji: {e_main_app}", exc_info=True)
        sys.exit(2)  # Inny kod błędu niż 1


if __name__ == "__main__":
    # Upewnij się, że katalog bieżący jest w sys.path, jeśli skrypt jest uruchamiany bezpośrednio
    # i moduły są w tym samym katalogu. Zwykle Python robi to automatycznie.
    # current_dir = os.path.dirname(os.path.abspath(__file__))
    # if current_dir not in sys.path:
    #    sys.path.insert(0, current_dir)
    main()