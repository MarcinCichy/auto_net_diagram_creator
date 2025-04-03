# config.py
import os
from dotenv import load_dotenv

load_dotenv()  # Ładuje zmienne z pliku .env

def get_config():
    """
    Zwraca słownik z konfiguracją: base_url oraz api_key.
    """
    base_url = os.getenv("base_url")
    api_key = os.getenv("api_key")
    if not base_url or not api_key:
        raise ValueError("Nie skonfigurowano base_url lub api_key w pliku .env")
    return {
        "base_url": base_url,
        "api_key": api_key
    }
