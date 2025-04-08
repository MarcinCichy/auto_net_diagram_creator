# config.py
import os
from dotenv import load_dotenv

load_dotenv()  # Ładuje zmienne z pliku .env

def get_config():
    """
    Zwraca konfigurację jako słownik.
    Klucze: base_url, api_key, default_snmp_community (opcjonalny)
    """
    base_url = os.getenv("LIBRENMS_BASE_URL") # Zmieniono nazwę zmiennej dla jasności
    api_key = os.getenv("LIBRENMS_API_KEY")   # Zmieniono nazwę zmiennej dla jasności
    default_snmp_community = os.getenv("DEFAULT_SNMP_COMMUNITY", None) # Dodano domyślne community

    if not base_url or not api_key:
        raise ValueError("Nie skonfigurowano LIBRENMS_BASE_URL lub LIBRENMS_API_KEY w pliku .env")

    config = {
        "base_url": base_url,
        "api_key": api_key
    }
    if default_snmp_community:
        config["default_snmp_community"] = default_snmp_community

    return config

# --- Upewnij się, że Twój plik .env zawiera: ---
# LIBRENMS_BASE_URL=http://twoj-librenms.example.com
# LIBRENMS_API_KEY=twoj_api_key
# DEFAULT_SNMP_COMMUNITY=public # Opcjonalnie, jeśli nie ma w LibreNMS per urządzenie