# config.py
import os
from dotenv import load_dotenv

load_dotenv()  # ładuje .env

def get_config():
    """
    Zwraca konfigurację:
      - base_url          : URL LibreNMS
      - api_key           : klucz API LibreNMS
      - default_snmp_comm : Domyślny SNMP community string (z .env, opcjonalny)
      - cli_username      : login SSH (opcjonalny)
      - cli_password      : hasło SSH (opcjonalne)
    """
    base_url   = os.getenv("BASE_URL")
    api_key    = os.getenv("API_KEY")
    # Zmieniono nazwę zmiennej dla jasności - to jest fallback
    default_snmp_comm = os.getenv("SNMP_COMMUNITY")
    cli_user   = os.getenv("CLI_USER")
    cli_pass   = os.getenv("CLI_PASS")

    missing = []
    if not base_url or not api_key:
        missing.append("BASE_URL/API_KEY")

    # Ostrzeżenia o braku opcjonalnych danych
    if not default_snmp_comm:
        # To już nie jest krytyczne, jeśli używamy pliku JSON
        print("Informacja: Brak domyślnego SNMP_COMMUNITY w .env. Skrypt polegać będzie na pliku device_credentials.json.")
    if not cli_user or not cli_pass:
        print("Ostrzeżenie: Brak CLI_USER/CLI_PASS w .env - metoda CLI nie zadziała.")

    if missing: # Tylko krytyczne zmienne
         raise ValueError(f"Brakuje podstawowych zmiennych w .env: {', '.join(missing)}")

    return {
        "base_url":          base_url,
        "api_key":           api_key,
        "default_snmp_comm": default_snmp_comm, # Zwracamy domyślny community
        "cli_username":      cli_user,
        "cli_password":      cli_pass,
    }