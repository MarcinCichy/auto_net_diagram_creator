# config_loader.py
import os
from dotenv import load_dotenv

# Ładuje zmienne z pliku .env do zmiennych środowiskowych
load_dotenv()

# Usunięto DEFAULT_DEVICE_CREDENTIALS_FILE

def get_env_config():
    """
    Pobiera konfigurację podstawową ze zmiennych środowiskowych (.env).
    Wczytuje listę domyślnych community SNMP.
    """
    base_url = os.getenv("BASE_URL")
    api_key = os.getenv("API_KEY")
    snmp_communities_str = os.getenv("SNMP_COMMUNITIES") # Wczytaj jako string
    default_snmp_communities = [] # Inicjalizuj jako pustą listę
    if snmp_communities_str:
        # Podziel string po przecinkach i usuń białe znaki
        default_snmp_communities = [comm.strip() for comm in snmp_communities_str.split(',') if comm.strip()]
        print(f"ⓘ Znaleziono {len(default_snmp_communities)} community w .env: {', '.join(default_snmp_communities)}")
    cli_user = os.getenv("CLI_USER")
    cli_pass = os.getenv("CLI_PASS")

    if not base_url or not api_key:
        raise ValueError("Brakuje wymaganych zmiennych BASE_URL lub API_KEY w pliku .env")

    if not default_snmp_communities:
        print("⚠ Ostrzeżenie: Brak lub pusta lista SNMP_COMMUNITIES w .env. Metody SNMP nie będą działać.")
    if not cli_user or not cli_pass:
        print("Informacja: Brak CLI_USER/CLI_PASS w .env - metoda CLI nie będzie dostępna.")

    return {
        "base_url": base_url,
        "api_key": api_key,
        # *** POPRAWKA: Przechowuj całą listę community ***
        "default_snmp_communities": default_snmp_communities,
        # ***********************************************
        "cli_username": cli_user,
        "cli_password": cli_pass,
    }

# Usunięto funkcję load_device_credentials

# Funkcja get_communities_to_try (bez zmian, oczekuje listy)
def get_communities_to_try(default_communities_list):
    """
    Zwraca listę domyślnych community z .env lub None, jeśli lista jest pusta.
    """
    if default_communities_list:
        print(f"  Będę próbował domyślnych community z .env: {len(default_communities_list)} communities.")
        return default_communities_list # Zwróć listę
    else:
        print(f"  ⓘ Brak domyślnych community SNMP w .env do wypróbowania.")
        return None # Zwróć None
