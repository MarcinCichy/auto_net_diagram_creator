# --- config_loader.py ---

import json
import os
import logging
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)
load_dotenv() # Wczytuje zmienne z .env do środowiska

DEFAULT_CREDENTIALS_FILE = "credentials.json" # Można przenieść do globalnych stałych jeśli używane gdzie indziej

def get_env_config() -> Dict[str, Any]:
    """
    Pobiera konfigurację podstawową ze zmiennych środowiskowych (.env)
    oraz wczytuje konfigurację poświadczeń CLI z pliku JSON.
    """
    base_url = os.getenv("BASE_URL")
    api_key = os.getenv("API_KEY")
    snmp_communities_str = os.getenv("SNMP_COMMUNITIES")
    default_snmp_communities: List[str] = []
    if snmp_communities_str:
        default_snmp_communities = [comm.strip() for comm in snmp_communities_str.split(',') if comm.strip()]
        logger.info(f"Znaleziono {len(default_snmp_communities)} community SNMP w .env: {', '.join(default_snmp_communities)}")

    if not base_url or not api_key:
        # Ten błąd powinien być krytyczny i zatrzymać aplikację,
        # więc rzucenie wyjątku jest tutaj odpowiednie.
        logger.critical("Brakuje wymaganych zmiennych BASE_URL lub API_KEY w pliku .env lub są one puste.")
        raise ValueError("Brakuje wymaganych zmiennych BASE_URL lub API_KEY w pliku .env lub są one puste.")

    device_credentials = load_device_credentials()

    if not default_snmp_communities:
        logger.warning("Brak lub pusta lista SNMP_COMMUNITIES w .env. Metody SNMP mogą nie działać poprawnie.")
    if not device_credentials.get("devices") and not device_credentials.get("defaults", {}).get("cli_user"):
         logger.info(f"Brak specyficznych poświadczeń CLI w '{DEFAULT_CREDENTIALS_FILE}' oraz brak domyślnych ('defaults'). Metoda CLI nie będzie używana, chyba że poświadczenia zostaną dodane.")

    config = {
        "base_url": base_url,
        "api_key": api_key,
        "default_snmp_communities": default_snmp_communities,
        "cli_credentials": device_credentials,
    }
    return config

def load_device_credentials(filepath: str = DEFAULT_CREDENTIALS_FILE) -> Dict[str, Any]:
    """Wczytuje poświadczenia CLI (domyślne i specyficzne dla urządzeń) z pliku JSON."""
    credentials: Dict[str, Any] = {"defaults": {}, "devices": []}
    if not os.path.exists(filepath):
        logger.warning(f"Plik poświadczeń '{filepath}' nie znaleziony. Metoda CLI będzie działać tylko jeśli zostaną zdefiniowane poświadczenia (w 'devices' lub 'defaults').")
        return credentials # Zwróć domyślną strukturę
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
            if not isinstance(loaded_data, dict):
                 logger.error(f"Główna struktura w pliku '{filepath}' nie jest słownikiem (JSON object). Zwracam domyślne puste poświadczenia.")
                 return credentials # Zwróć domyślną strukturę

            # Walidacja i przypisanie sekcji 'defaults'
            defaults_data = loaded_data.get("defaults")
            if isinstance(defaults_data, dict):
                credentials["defaults"] = defaults_data
            elif defaults_data is not None: # Jeśli klucz istnieje, ale nie jest słownikiem
                logger.warning(f"Sekcja 'defaults' w '{filepath}' nie jest słownikiem (typ: {type(defaults_data)}). Używam pustych domyślnych.")
            # Jeśli klucza "defaults" nie ma, credentials["defaults"] pozostanie {}

            # Walidacja i przypisanie sekcji 'devices'
            devices_data = loaded_data.get("devices")
            if isinstance(devices_data, list):
                credentials["devices"] = devices_data
            elif devices_data is not None: # Jeśli klucz istnieje, ale nie jest listą
                logger.warning(f"Sekcja 'devices' w '{filepath}' nie jest listą (typ: {type(devices_data)}). Używam pustej listy urządzeń.")
            # Jeśli klucza "devices" nie ma, credentials["devices"] pozostanie []

            logger.info(f"Pomyślnie wczytano dane poświadczeń CLI z '{filepath}'. Defaults: {bool(credentials['defaults'])}, Devices: {len(credentials['devices'])}")

    except json.JSONDecodeError as e:
        logger.error(f"Błąd parsowania pliku JSON z poświadczeniami '{filepath}': {e}. Zwracam domyślne puste poświadczenia.")
        return {"defaults": {}, "devices": []} # Zwróć domyślną strukturę przy błędzie parsowania
    except Exception as e:
        logger.error(f"Nieoczekiwany błąd podczas odczytu pliku poświadczeń '{filepath}': {e}. Zwracam domyślne puste poświadczenia.")
        return {"defaults": {}, "devices": []} # Zwróć domyślną strukturę przy innym błędzie

    return credentials

def get_communities_to_try(default_communities_list: List[str]) -> List[str]: # Zmieniono Optional[List[str]] na List[str]
    """
    Zwraca listę domyślnych community SNMP. Jeśli lista wejściowa jest pusta, zwraca pustą listę.
    """
    if default_communities_list:
        logger.debug(f"Będę próbował domyślnych community SNMP z .env: {len(default_communities_list)} communities.")
        return default_communities_list
    else:
        # To już jest logowane w get_env_config, więc tutaj może być debug
        logger.debug("Brak domyślnych community SNMP w konfiguracji do wypróbowania.")
        return []