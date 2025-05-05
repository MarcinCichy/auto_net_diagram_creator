# --- config_loader.py ---

import json
import os
import logging
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)
load_dotenv() # Wczytuje zmienne z .env do środowiska

DEFAULT_CREDENTIALS_FILE = "credentials.json"

def get_env_config() -> Dict[str, Any]:
    """
    Pobiera konfigurację podstawową ze zmiennych środowiskowych (.env)
    oraz wczytuje konfigurację poświadczeń CLI z pliku JSON.
    """
    base_url = os.getenv("BASE_URL")
    api_key = os.getenv("API_KEY")
    snmp_communities_str = os.getenv("SNMP_COMMUNITIES") # Wczytaj jako string
    default_snmp_communities: List[str] = [] # Inicjalizuj jako pustą listę
    if snmp_communities_str:
        # Podziel string po przecinkach i usuń białe znaki
        default_snmp_communities = [comm.strip() for comm in snmp_communities_str.split(',') if comm.strip()]
        logger.info(f"Znaleziono {len(default_snmp_communities)} community SNMP w .env: {', '.join(default_snmp_communities)}")

    if not base_url or not api_key:
        raise ValueError("Brakuje wymaganych zmiennych BASE_URL lub API_KEY w pliku .env")

    # Wczytaj konfigurację poświadczeń CLI z pliku JSON
    device_credentials = load_device_credentials()

    # Logowanie ostrzeżeń o braku konfiguracji
    if not default_snmp_communities:
        logger.warning("Brak lub pusta lista SNMP_COMMUNITIES w .env. Metody SNMP nie będą działać poprawnie.")
    if not device_credentials.get("devices") and not device_credentials.get("defaults", {}).get("cli_user"):
         logger.info(f"Brak specyficznych poświadczeń CLI w '{DEFAULT_CREDENTIALS_FILE}' oraz brak domyślnych ('defaults'). Metoda CLI nie będzie używana, chyba że poświadczenia zostaną dodane.")


    config = {
        "base_url": base_url,
        "api_key": api_key,
        "default_snmp_communities": default_snmp_communities,
        # Przekaż całą strukturę poświadczeń wczytaną z JSON
        "cli_credentials": device_credentials,
    }

    return config

def load_device_credentials(filepath: str = DEFAULT_CREDENTIALS_FILE) -> Dict[str, Any]:
    """Wczytuje poświadczenia CLI (domyślne i specyficzne dla urządzeń) z pliku JSON."""
    # Struktura domyślna, jeśli plik nie istnieje lub jest pusty/błędny
    credentials: Dict[str, Any] = {"defaults": {}, "devices": []}
    if not os.path.exists(filepath):
        logger.warning(f"Plik poświadczeń '{filepath}' nie znaleziony. Metoda CLI będzie działać tylko jeśli w tym pliku zostaną zdefiniowane poświadczenia (w sekcji 'devices' lub 'defaults').")
        return credentials
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
            # Prosta walidacja struktury i przypisanie
            if isinstance(loaded_data, dict):
                 if isinstance(loaded_data.get("defaults"), dict):
                     credentials["defaults"] = loaded_data["defaults"]
                 else:
                      logger.warning(f"Sekcja 'defaults' w '{filepath}' nie jest słownikiem lub nie istnieje.")

                 if isinstance(loaded_data.get("devices"), list):
                     credentials["devices"] = loaded_data["devices"]
                 else:
                      logger.warning(f"Sekcja 'devices' w '{filepath}' nie jest listą lub nie istnieje.")
                 logger.info(f"Pomyślnie wczytano dane poświadczeń CLI z '{filepath}'.")
            else:
                 logger.error(f"Główna struktura w pliku '{filepath}' nie jest słownikiem (JSON object).")

    except json.JSONDecodeError as e:
        logger.error(f"Błąd parsowania pliku JSON z poświadczeniami '{filepath}': {e}")
    except Exception as e:
        logger.error(f"Nieoczekiwany błąd podczas odczytu pliku poświadczeń '{filepath}': {e}")

    return credentials

def get_communities_to_try(default_communities_list: List[str]) -> Optional[List[str]]:
    """
    Zwraca listę domyślnych community SNMP lub None, jeśli lista jest pusta.
    (Funkcja bez zmian, ale jej argument pochodzi z nowej struktury config)
    """
    if default_communities_list:
        logger.info(f"Będę próbował domyślnych community SNMP z .env: {len(default_communities_list)} communities.")
        return default_communities_list
    else:
        logger.warning("Brak domyślnych community SNMP w konfiguracji do wypróbowania.")
        return None