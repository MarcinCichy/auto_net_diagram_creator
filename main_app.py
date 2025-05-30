#!/usr/bin/env python3
# --- main_app.py ---

import sys
import time
import argparse
import os
import logging
from typing import List, Dict, Any, Optional

# --- Konfiguracja Loggera jako pierwsza rzecz ---
# Krok 1: Zaimportuj config_loader i utils (bezpośrednio, bez try-except na tym etapie,
# błędy importu tutaj są krytyczne i powinny zatrzymać aplikację)
import config_loader
from utils import setup_logging

# Domyślne ścieżki, jeśli argumenty CLI nie zostaną podane
DEFAULT_ENV_FILE_PATH = ".env"
# DEFAULT_CONFIG_FILE_PATH jest już zdefiniowany w config_loader.DEFAULT_CONFIG_FILE

# Wstępne parsowanie argumentów CLI tylko dla ścieżek do plików konfiguracyjnych
# aby móc skonfigurować logowanie na podstawie config.ini przed pełnym parsowaniem.
# To jest bardziej zaawansowane i może nie być konieczne, jeśli akceptujemy,
# że log_level z .env nie wpłynie na *inicjalne* logi config_loadera.
# Dla uproszczenia, użyjemy domyślnej ścieżki do config.ini dla setup_logging.
try:
    temp_config_for_logging = config_loader.load_config(config_loader.DEFAULT_CONFIG_FILE)
    log_level_from_ini = temp_config_for_logging.get("log_level", "INFO")
    log_to_file_from_ini = temp_config_for_logging.get("log_to_file", True)
    log_file_name_from_ini = temp_config_for_logging.get("log_file_name", "auto_net_diagram_creator.log")

    # Użyj bezpośrednio wartości z .ini (lub domyślnych z kodu, jeśli .ini nie ma)
    # do skonfigurowania logowania. LOG_LEVEL z .env nie wpłynie na to początkowe ustawienie.
    setup_logging(
        level_str=log_level_from_ini,
        log_to_file=log_to_file_from_ini,
        log_file=log_file_name_from_ini
    )
    logger = logging.getLogger(__name__) # Logger dla tego modułu
except Exception as e_setup_log_init:
    # Użyj print, bo logger mógł się nie zainicjować
    print(f"KRYTYCZNY BŁĄD: Nieoczekiwany błąd podczas wstępnej konfiguracji logowania: {e_setup_log_init}", file=sys.stderr)
    sys.exit(1)

# --- Importy pozostałych modułów aplikacji ---
try:
    import file_io # Już zaimportowane, ale dla jasności
    from librenms_client import LibreNMSAPI
    from network_discoverer import NetworkDiscoverer
    from diagram_generator import DiagramGenerator
except ImportError as e_mod:
    logger.critical(
        f"Błąd importu modułu aplikacji: {e_mod}. Upewnij się, że wszystkie pliki .py znajdują się w odpowiednim miejscu i zależności są zainstalowane.",
        exc_info=True)
    sys.exit(1)
except Exception as e_imp_other:
    logger.critical(f"Nieoczekiwany błąd podczas importowania modułów aplikacji: {e_imp_other}", exc_info=True)
    sys.exit(1)


class Application:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config: Dict[str, Any] = {}
        self.api_client: Optional[LibreNMSAPI] = None
        logger.debug("Application zainicjalizowana z argumentami: %s", args)

    def setup(self) -> bool:
        logger.info("--- Konfiguracja Aplikacji ---")
        try:
            # Użyj ścieżek z argumentów CLI dla plików .env i config.ini
            self.config = config_loader.get_env_config(
                env_file_path=self.args.env_file,
                config_ini_path=self.args.config_file
            )
            logger.info("Konfiguracja z plików .ini i .env wczytana pomyślnie.")

            # Po załadowaniu pełnej konfiguracji, dostosuj poziom logowania, jeśli .env go nadpisał
            final_log_level_str = self.config.get("log_level", "INFO")
            current_root_logger_level = logging.getLogger().getEffectiveLevel()
            numeric_final_log_level = getattr(logging, final_log_level_str.upper(), None)

            if numeric_final_log_level is not None and numeric_final_log_level != current_root_logger_level:
                logger.info(f"Zmiana poziomu logowania na '{final_log_level_str}' zgodnie z konfiguracją (.env lub .ini).")
                logging.getLogger().setLevel(numeric_final_log_level)
                # Można by też zaktualizować poziom handlerów, ale zmiana poziomu roota zwykle wystarcza
                for handler in logging.getLogger().handlers:
                    handler.setLevel(numeric_final_log_level) # Zaktualizuj też poziom handlerów
            elif numeric_final_log_level is None:
                logger.warning(f"Nieprawidłowy poziom logowania '{final_log_level_str}' w konfiguracji. Pozostawiono obecny poziom.")

        except ValueError as e_val_conf: # Np. brak kluczowych zmiennych z .env
            logger.critical(f"Krytyczny błąd konfiguracji: {e_val_conf}")
            return False
        except Exception as e_conf_load:
            logger.critical(f"Nieoczekiwany błąd ładowania konfiguracji: {e_conf_load}", exc_info=True)
            return False

        base_url = self.config.get("base_url")
        api_key = self.config.get("api_key")

        # config_loader.get_env_config już powinien zgłosić błąd, jeśli ich nie ma, ale dla pewności
        if not base_url or not api_key:
            logger.critical("Krytyczny błąd: Brak base_url lub api_key w finalnej konfiguracji.")
            return False

        try:
            # Ustalanie finalnych wartości dla verify_ssl i api_timeout
            # 1. Wartość z config (która jest wynikiem .ini nadpisanego przez .env)
            verify_ssl_conf = self.config.get("verify_ssl") # config_loader dał już poprawny typ bool
            api_timeout_conf = self.config.get("api_timeout") # config_loader dał już poprawny typ int

            # 2. Nadpisanie przez argumenty CLI
            if self.args.no_verify_ssl is True: # Jeśli podano --no-verify-ssl
                verify_ssl_final = False
                logger.info("Wymuszono brak weryfikacji SSL przez argument CLI (--no-verify-ssl).")
            elif self.args.verify_ssl is True: # Jeśli podano --verify-ssl
                verify_ssl_final = True
                logger.info("Wymuszono weryfikację SSL przez argument CLI (--verify-ssl).")
            else: # Żadna flaga SSL nie została podana, użyj wartości z konfiguracji
                verify_ssl_final = verify_ssl_conf

            if self.args.api_timeout is not None:
                api_timeout_final = self.args.api_timeout
                logger.info(f"Użyto api_timeout z argumentu CLI: {api_timeout_final}s.")
            else:
                api_timeout_final = api_timeout_conf

            self.api_client = LibreNMSAPI(
                base_url,
                api_key,
                verify_ssl=verify_ssl_final,
                timeout=api_timeout_final
            )
            logger.info(f"Klient API LibreNMS zainicjalizowany (SSL Verify: {verify_ssl_final}, Timeout: {api_timeout_final}s).")
        except ValueError as e_api_init: # Błędy z konstruktora LibreNMSAPI
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

        if not run_discovery_flag and not run_diagram_flag:
            logger.info("Nie podano flagi --discover ani --diagram. Domyślnie uruchamiam obie fazy.")
            run_discovery_flag = True
            run_diagram_flag = True

        # Użyj ścieżek z argumentów CLI, jeśli podane; w przeciwnym razie z self.config (z .ini)
        ip_list_p = self.args.ip_list if self.args.ip_list is not None else self.config.get("ip_list_file")
        conn_txt_p = self.args.conn_txt if self.args.conn_txt is not None else self.config.get("connections_txt_file")
        conn_json_p = self.args.conn_json if self.args.conn_json is not None else self.config.get("connections_json_file")
        template_p = self.args.template if self.args.template is not None else self.config.get("diagram_template_file")
        diag_out_drawio_p = self.args.diagram_out_drawio if self.args.diagram_out_drawio is not None else self.config.get("diagram_output_drawio_file")
        diag_out_svg_p = self.args.diagram_out_svg if self.args.diagram_out_svg is not None else self.config.get("diagram_output_svg_file")

        logger.debug(f"Finalne ścieżki plików: IP List='{ip_list_p}', Conn Txt='{conn_txt_p}', Conn Json='{conn_json_p}', Template='{template_p}', DrawIO Out='{diag_out_drawio_p}', SVG Out='{diag_out_svg_p}'")

        if run_discovery_flag:
            self._run_discovery_phase(ip_list_p, conn_txt_p, conn_json_p)
        else:
            logger.info("Pomijanie fazy odkrywania połączeń (--discover nie ustawione).")

        if run_diagram_flag:
            if not os.path.exists(conn_json_p) and not run_discovery_flag :
                logger.warning(
                    f"Faza generowania diagramu uruchomiona, ale faza odkrywania była pominięta, a plik połączeń '{conn_json_p}' nie istnieje. Diagram może być pusty lub nie zawierać połączeń.")
            self._run_diagram_phase(ip_list_p, template_p, diag_out_drawio_p, diag_out_svg_p, conn_json_p)
        else:
            logger.info("Pomijanie fazy generowania diagramów (--diagram nie ustawione).")

        app_end_time = time.time()
        logger.info(f"=== Zakończono Działanie Aplikacji. Całkowity czas: {app_end_time - app_start_time:.2f} sek. ===")

    def _run_discovery_phase(self, ip_list_path:str, conn_txt_path:str, conn_json_path:str) -> None:
        if not self.api_client: # Powinno być już obsłużone przez self.setup()
            logger.error("Klient API nie został zainicjalizowany. Nie można uruchomić fazy odkrywania.")
            return

        logger.info("--- Rozpoczynanie Fazy Odkrywania Połączeń ---")
        start_time = time.time()
        try:
            discoverer = NetworkDiscoverer(
                api_client=self.api_client,
                config=self.config, # Przekaż pełny obiekt config
                ip_list_path=ip_list_path,
                conn_txt_path=conn_txt_path,
                conn_json_path=conn_json_path
            )
            discoverer.discover_connections()
        except Exception as e_discover:
            logger.error(f"Wystąpił nieoczekiwany błąd podczas fazy odkrywania: {e_discover}", exc_info=True)

        end_time = time.time()
        logger.info(f"--- Zakończono Fazę Odkrywania Połączeń (czas: {end_time - start_time:.2f} sek.) ---")

    def _run_diagram_phase(self, ip_list_path:str, template_path:str, output_path_drawio:str, output_path_svg:str, connections_json_path:str) -> None:
        if not self.api_client:
            logger.error("Klient API nie został zainicjalizowany. Nie można uruchomić fazy generowania diagramu.")
            return

        if not os.path.exists(template_path):
            logger.warning(f"Plik szablonu DrawIO '{template_path}' nie istnieje lub jest niedostępny. Użyte zostaną domyślne style.")
        # Sprawdzenie existence connections_json_path jest już robione w DiagramGenerator i file_io

        logger.info("--- Rozpoczynanie Fazy Generowania Diagramów (DrawIO i SVG) ---")
        start_time = time.time()
        try:
            generator = DiagramGenerator(
                api_client=self.api_client,
                config=self.config, # Przekaż pełny obiekt config
                ip_list_path=ip_list_path,
                template_path=template_path,
                output_path_drawio=output_path_drawio,
                output_path_svg=output_path_svg,
                connections_json_path=connections_json_path
            )
            generator.generate_diagram()
        except Exception as e_diagram:
            logger.error(f"Wystąpił nieoczekiwany błąd podczas fazy generowania diagramów: {e_diagram}", exc_info=True)

        end_time = time.time()
        logger.info(f"--- Zakończono Fazę Generowania Diagramów (czas: {end_time - start_time:.2f} sek.) ---")


def main():
    # Logowanie jest już skonfigurowane na początku pliku main_app.py
    parser = argparse.ArgumentParser(
        description="Narzędzie do odkrywania połączeń sieciowych i generowania diagramów Draw.io/SVG z danych LibreNMS.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Pomaga wyświetlać wartości domyślne
    )

    parser.add_argument("--discover", action="store_true", help="Uruchom tylko fazę odkrywania połączeń.")
    parser.add_argument("--diagram", action="store_true", help="Uruchom tylko fazę generowania diagramów (DrawIO i SVG).")

    # Użyj domyślnych wartości z config_loader jako fallback dla argparse
    parser.add_argument("--config-file", default=config_loader.DEFAULT_CONFIG_FILE,
                        help=f"Ścieżka do pliku config.ini. Domyślnie: '{config_loader.DEFAULT_CONFIG_FILE}'")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE_PATH,
                        help=f"Ścieżka do pliku .env. Domyślnie: '{DEFAULT_ENV_FILE_PATH}'")

    # Argumenty dla ścieżek plików - nadpisują config.ini
    # Nie ustawiamy tutaj default, bo będą brane z config.ini, jeśli argument nie jest podany
    parser.add_argument("--ip-list", default=None, help="Plik z listą IP/Hostname (nadpisuje config.ini).")
    parser.add_argument("--conn-txt", default=None, help="Plik wyjściowy .txt z połączeniami (nadpisuje config.ini).")
    parser.add_argument("--conn-json", default=None, help="Plik .json z połączeniami (wejście/wyjście, nadpisuje config.ini).")
    parser.add_argument("--template", default=None, help="Plik szablonu .drawio (nadpisuje config.ini).")
    parser.add_argument("--diagram-out-drawio", default=None, help="Plik wyjściowy .drawio (nadpisuje config.ini).")
    parser.add_argument("--diagram-out-svg", default=None, help="Plik wyjściowy .svg (nadpisuje config.ini).")

    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument("--verify-ssl", action="store_true", default=None, help="Wymuś weryfikację SSL (nadpisuje config).")
    ssl_group.add_argument("--no-verify-ssl", action="store_true", default=None, help="Wymuś brak weryfikacji SSL (nadpisuje config).")

    parser.add_argument("--api-timeout", type=int, default=None, help="Timeout API (w sekundach, nadpisuje config).")

    args = parser.parse_args()

    # Sprawdzenie istnienia plików konfiguracyjnych jest teraz robione w config_loader
    # oraz na początku tego pliku dla wstępnego logowania.
    # logger.info(...) o braku plików może być powtórzone, ale to nie jest krytyczne.

    try:
        app = Application(args)
        app.run()
    except Exception as e_main_app:
        logger.critical(f"Nieoczekiwany krytyczny błąd na najwyższym poziomie aplikacji: {e_main_app}", exc_info=True)
        sys.exit(2) # Zakończ z kodem błędu


if __name__ == "__main__":
    main()