# librenms_client.py
import requests
import json
import urllib3
from requests.exceptions import HTTPError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class LibreNMSAPI:
    def __init__(self, base_url, api_key, verify_ssl=False, timeout=15):
        if not base_url or not api_key:
            raise ValueError("Base URL and API Key cannot be empty.")
        self.base_url = base_url.rstrip('/') + '/api/v0'
        self.headers = {'X-Auth-Token': api_key}
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        # print(f"LibreNMS Client initialized for URL: {self.base_url}")

    def _get(self, endpoint, params=None):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = requests.get(url, headers=self.headers, params=params, verify=self.verify_ssl, timeout=self.timeout)
            response.raise_for_status()
            if not response.content: return None
            content_type = response.headers.get('content-type', '')
            if 'application/json' not in content_type:
                 print(f"⚠ Ostrzeżenie API: Nieoczekiwany Content-Type ({content_type}) dla {url}. Treść: {response.text[:100]}...")
                 return None
            data = response.json()
            if isinstance(data, dict) and data.get('status') == 'error':
                 print(f"⚠ Błąd API LibreNMS ({url}): {data.get('message', 'Brak wiadomości')}")
                 return None
            return data
        except HTTPError as e:
            if e.response is not None and e.response.status_code != 404:
                 print(f"⚠ Błąd HTTP API LibreNMS ({url}): {e.response.status_code} {e.response.reason}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"⚠ Błąd połączenia z API LibreNMS ({url}): {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"⚠ Błąd dekodowania JSON z API LibreNMS ({url}): {e}")
            try: print(f"  Otrzymano treść (fragment): {response.text[:200]}...")
            except NameError: pass
            return None
        except Exception as e:
             print(f"⚠ Nieoczekiwany błąd podczas komunikacji z API ({url}): {e}")
             return None

    def get_devices(self, columns=None):
        """
        Pobiera listę urządzeń. Domyślnie pobiera ID, hostname, IP i PURPOSE.
        """
        # *** ZMIANA: Dodano 'purpose' do domyślnych kolumn ***
        default_cols = "device_id,hostname,ip,sysName,purpose" # Dodano purpose
        final_columns = columns if columns else default_cols
        params = {'columns': final_columns}
        data = self._get('devices', params=params)
        return data.get('devices', []) if isinstance(data, dict) else []

    def get_device(self, identifier, by_hostname=False):
        """Pobiera pojedyncze urządzenie po ID, IP lub hostname."""
        # Używamy get_devices i filtrujemy, aby mieć pewność, że mamy pole 'purpose'
        print(f"Wyszukiwanie urządzenia '{identifier}' przez filtrowanie...")
        # Pobierz więcej kolumn, w tym purpose
        all_devices = self.get_devices(columns="device_id,hostname,ip,sysName,purpose")
        if not all_devices: return None

        # Szukaj po ID (jeśli 'identifier' jest numeryczny)
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
             for d in all_devices:
                  if str(d.get('device_id')) == str(identifier):
                       return d # Zwróć pełny słownik

        # Szukaj po IP
        for d in all_devices:
            if d.get("ip") == identifier:
                return d
        # Szukaj po hostname (dokładne dopasowanie, ignorując wielkość liter)
        if isinstance(identifier, str):
            identifier_lower = identifier.lower()
            for d in all_devices:
                hostname_api = d.get("hostname")
                if hostname_api and hostname_api.lower() == identifier_lower:
                    return d
        print(f"  Nie znaleziono urządzenia dla identyfikatora '{identifier}'.")
        return None


    def get_ports(self, device_id, columns=None):
        """Pobiera porty, domyślnie włączając ifType."""
        default_cols = "port_id,ifName,ifIndex,ifDescr,ifSpeed,ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias,ifType"
        final_columns = columns if columns else default_cols
        params = {'columns': final_columns}
        data = self._get(f'devices/{device_id}/ports', params=params)
        return data.get('ports', []) if isinstance(data, dict) else []

    def get_port_fdb(self, device_id, port_id):
        """Pobiera wpisy FDB, obsługując błąd 400."""
        url = f"{self.base_url}/devices/{device_id}/ports/{port_id}/fdb"
        try:
            response = requests.get(url, headers=self.headers, verify=self.verify_ssl, timeout=self.timeout)
            response.raise_for_status()
            if not response.content: return []
            content_type = response.headers.get('content-type', '')
            if 'application/json' not in content_type:
                 print(f"⚠ Ostrzeżenie API FDB: Nieoczekiwany Content-Type ({content_type}) dla {url}.")
                 return None
            data = response.json()
            if isinstance(data, dict) and data.get('status') == 'error': return []
            return data.get("fdb", []) if isinstance(data, dict) else []
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 400: return []
            else:
                 print(f"⚠ Błąd HTTP API LibreNMS (FDB @ {url}): {e.response.status_code if e.response else 'N/A'} {e.response.reason if e.response else e}")
                 return None
        except requests.exceptions.RequestException as e: print(f"⚠ Błąd połączenia z API LibreNMS (FDB @ {url}): {e}"); return None
        except json.JSONDecodeError as e: print(f"⚠ Błąd dekodowania JSON z API LibreNMS (FDB @ {url}): {e}"); return None
        except Exception as e: print(f"⚠ Nieoczekiwany błąd podczas pobierania FDB ({url}): {e}"); return None