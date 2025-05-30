# librenms_client.py
import requests
import json # Nie jest bezpośrednio używany, ale może być przydatny w przyszłości
import urllib3
from requests.exceptions import HTTPError, RequestException, JSONDecodeError
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class LibreNMSAPI:
    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = False, timeout: int = 15):
        if not base_url or not api_key:
            logger.critical("Base URL and API Key cannot be empty for LibreNMSAPI.")
            raise ValueError("Base URL and API Key cannot be empty.")

        cleaned_base_url = base_url.rstrip('/')
        # Sprawdź, czy użytkownik już podał pełną ścieżkę /api/v0
        if cleaned_base_url.endswith('/api/v0'):
            self.base_url = cleaned_base_url
        else:
            self.base_url = cleaned_base_url + '/api/v0'

        self.headers = {'X-Auth-Token': api_key}
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        logger.debug(f"LibreNMS Client initialized for URL: {self.base_url}, SSL Verify: {self.verify_ssl}, Timeout: {self.timeout}s")

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        logger.debug(f"API GET: {url}, Params: {params}")
        try:
            response = requests.get(url, headers=self.headers, params=params, verify=self.verify_ssl, timeout=self.timeout)
            response.raise_for_status()

            if not response.content:
                logger.debug(f"API: Otrzymano pustą odpowiedź (2xx) z {url}")
                return None

            content_type = response.headers.get('content-type', '')
            if 'application/json' not in content_type.lower():
                logger.warning(f"API: Nieoczekiwany Content-Type ({content_type}) dla {url}. Treść (fragment): {response.text[:100]}...")
                return None

            data = response.json()
            if isinstance(data, dict) and data.get('status') == 'error':
                logger.error(f"API LibreNMS zwróciło błąd ({url}): {data.get('message', 'Brak szczegółowej wiadomości')}")
                return None
            return data
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.info(f"API: Zasób nie znaleziony (404) pod adresem {url}: {e}")
            else:
                logger.error(f"API: Błąd HTTP ({e.response.status_code if e.response else 'N/A'}) dla {url}: {e}")
            return None
        except RequestException as e:
            logger.error(f"API: Błąd połączenia z {url}: {e}")
            return None
        except JSONDecodeError as e:
            logger.error(f"API: Błąd dekodowania JSON z {url}: {e}. Otrzymana treść (fragment): {response.text[:200] if 'response' in locals() else 'N/A'}")
            return None
        except Exception as e:
            logger.error(f"API: Nieoczekiwany błąd podczas komunikacji z {url}: {e}", exc_info=True)
            return None

    def get_devices(self, columns: Optional[str] = None) -> List[Dict[str, Any]]:
        default_cols = "device_id,hostname,ip,sysName,purpose"
        final_columns = columns if columns else default_cols
        params = {'columns': final_columns}
        data = self._get('devices', params=params)
        if isinstance(data, dict) and 'devices' in data and isinstance(data['devices'], list):
            return data['devices']
        elif data is None:
             logger.warning("API: Nie udało się pobrać listy urządzeń (get_devices otrzymało None).")
             return []
        else:
            logger.warning(f"API: Nieoczekiwany format danych dla listy urządzeń. Otrzymano: {type(data)}. Zwracam pustą listę.")
            return []


    def get_device(self, identifier: Any, by_hostname: bool = False) -> Optional[Dict[str, Any]]:
        if by_hostname:
            logger.debug("Parametr 'by_hostname' w get_device jest obecnie ignorowany, wyszukiwanie odbywa się po różnych identyfikatorach.")

        logger.debug(f"Wyszukiwanie urządzenia '{identifier}' przez filtrowanie lokalne listy wszystkich urządzeń...")
        all_devices = self.get_devices()
        if not all_devices:
            logger.warning(f"API: Nie można wyszukać urządzenia '{identifier}', ponieważ lista wszystkich urządzeń jest pusta lub wystąpił błąd jej pobrania.")
            return None

        from utils import find_device_in_list
        device = find_device_in_list(identifier, all_devices)
        if device:
            logger.info(f"API: Znaleziono urządzenie dla identyfikatora '{identifier}'.")
        else:
            logger.info(f"API: Nie znaleziono urządzenia dla identyfikatora '{identifier}'.")
        return device


    def get_ports(self, device_id: str, columns: Optional[str] = None) -> List[Dict[str, Any]]:
        default_cols = "port_id,ifIndex,ifName,ifDescr,ifSpeed,ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias,ifType"
        final_columns = columns if columns else default_cols
        params = {'columns': final_columns}
        data = self._get(f'devices/{device_id}/ports', params=params)
        if isinstance(data, dict) and 'ports' in data and isinstance(data['ports'], list):
            return data['ports']
        elif data is None:
            logger.warning(f"API: Nie udało się pobrać portów dla device_id={device_id} (otrzymało None).")
            return []
        else:
            logger.warning(f"API: Nieoczekiwany format danych dla portów (device_id={device_id}). Otrzymano: {type(data)}. Zwracam pustą listę.")
            return []

    def get_port_fdb(self, device_id: str, port_id: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/devices/{device_id}/ports/{port_id}/fdb"
        logger.debug(f"API GET FDB: {url}")
        try:
            response = requests.get(url, headers=self.headers, verify=self.verify_ssl, timeout=self.timeout)
            response.raise_for_status()
            if not response.content:
                logger.debug(f"API FDB: Otrzymano pustą odpowiedź (2xx) z {url}")
                return []
            content_type = response.headers.get('content-type', '')
            if 'application/json' not in content_type.lower():
                logger.warning(f"API FDB: Nieoczekiwany Content-Type ({content_type}) dla {url}.")
                return []
            data = response.json()
            if isinstance(data, dict) and data.get('status') == 'error':
                logger.info(f"API FDB: Serwer zwrócił błąd dla {url} (prawdopodobnie brak FDB): {data.get('message', 'Brak wiadomości')}")
                return []
            if isinstance(data, dict) and 'fdb' in data and isinstance(data['fdb'], list):
                return data['fdb']
            else:
                logger.warning(f"API FDB: Nieoczekiwany format danych FDB dla {url}. Otrzymano: {type(data)}. Zwracam pustą listę.")
                return []
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                logger.info(f"API FDB: Otrzymano błąd 400 dla {url} (prawdopodobnie FDB nie jest dostępne/obsługiwane dla tego portu).")
                return []
            elif e.response is not None and e.response.status_code == 404:
                logger.info(f"API FDB: Zasób (port/urządzenie dla FDB) nie znaleziony (404) pod adresem {url}.")
                return []
            else:
                logger.error(f"API FDB: Błąd HTTP ({e.response.status_code if e.response else 'N/A'}) dla {url}: {e}")
                return []
        except RequestException as e:
            logger.error(f"API FDB: Błąd połączenia z {url}: {e}")
            return []
        except JSONDecodeError as e:
            logger.error(f"API FDB: Błąd dekodowania JSON z {url}: {e}. Treść (fragment): {response.text[:200] if 'response' in locals() else 'N/A'}")
            return []
        except Exception as e:
            logger.error(f"API FDB: Nieoczekiwany błąd podczas pobierania FDB ({url}): {e}", exc_info=True)
            return []