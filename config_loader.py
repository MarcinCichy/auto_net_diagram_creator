# config_loader.py
import os
import json
from dotenv import load_dotenv

# Ładuje zmienne z pliku .env do zmiennych środowiskowych
load_dotenv()

DEFAULT_DEVICE_CREDENTIALS_FILE = "device_credentials.json"

def get_env_config():
    """
    Pobiera konfigurację podstawową ze zmiennych środowiskowych (.env).
    """
    base_url = os.getenv("BASE_URL")
    api_key = os.getenv("API_KEY")
    default_snmp_comm = os.getenv("SNMP_COMMUNITY") # Może być None
    cli_user = os.getenv("CLI_USER") # Może być None
    cli_pass = os.getenv("CLI_PASS") # Może być None

    if not base_url or not api_key:
        raise ValueError("Brakuje wymaganych zmiennych BASE_URL lub API_KEY w pliku .env")

    # Informacje o brakujących opcjonalnych danych
    if not default_snmp_comm:
        print("Informacja: Brak domyślnego SNMP_COMMUNITY w .env. Skrypt polegać będzie na pliku device_credentials.json.")
    if not cli_user or not cli_pass:
        print("Informacja: Brak CLI_USER/CLI_PASS w .env - metoda CLI nie będzie dostępna.")

    return {
        "base_url": base_url,
        "api_key": api_key,
        "default_snmp_comm": default_snmp_comm,
        "cli_username": cli_user,
        "cli_password": cli_pass,
    }

def load_device_credentials(filepath=DEFAULT_DEVICE_CREDENTIALS_FILE):
    """
    Wczytuje specyficzne dla urządzeń dane SNMP z pliku JSON.
    Zwraca słownik {identyfikator: community_string}.
    """
    device_credentials = {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            creds_list = json.load(f)
            # Użyj słownika dla szybkiego dostępu, ignoruj wpisy bez wymaganych kluczy
            device_credentials = {
                cred['identifier']: cred['snmp_community']
                for cred in creds_list
                if isinstance(cred, dict) and 'identifier' in cred and 'snmp_community' in cred
            }
        print(f"✓ Wczytano dane SNMP dla {len(device_credentials)} urządzeń z {filepath}")
    except FileNotFoundError:
        print(f"ⓘ Informacja: Plik {filepath} nie znaleziony. Nie użyto specyficznych danych SNMP.")
    except json.JSONDecodeError as e:
        print(f"⚠ Błąd parsowania pliku JSON {filepath}: {e}. Nie użyto specyficznych danych SNMP.")
    except Exception as e:
        print(f"⚠ Błąd wczytywania pliku {filepath}: {e}. Nie użyto specyficznych danych SNMP.")
    return device_credentials

def get_specific_snmp_community(device_credentials, default_community, primary_id, secondary_id=None):
    """
    Ustala community string SNMP dla urządzenia, sprawdzając najpierw
    specyficzne dane, a potem domyślne.
    Zwraca krotkę: (community_string, źródło_informacji)
    """
    specific_comm = device_credentials.get(primary_id)
    comm_source = f"JSON dla '{primary_id}'"

    if not specific_comm and secondary_id:
        specific_comm = device_credentials.get(secondary_id)
        if specific_comm:
            comm_source = f"JSON dla '{secondary_id}'"

    if not specific_comm:
        specific_comm = default_community
        if specific_comm:
            comm_source = "domyślny z .env"
        else:
            comm_source = "brak"

    return specific_comm, comm_source