# librenms_client.py
import requests
import json
import urllib3
from requests.exceptions import HTTPError

# Opcjonalnie: Wyłącz ostrzeżenia o niezweryfikowanym certyfikacie SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class LibreNMSAPI:
    """
    Klient do interakcji z API LibreNMS v0.
    """
    def __init__(self, base_url, api_key, verify_ssl=False, timeout=15):
        """
        Inicjalizuje klienta API.

        Args:
            base_url (str): URL instancji LibreNMS (np. "https://librenms.example.com").
            api_key (str): Klucz API tokena do autoryzacji.
            verify_ssl (bool): Czy weryfikować certyfikat SSL. Domyślnie False.
            timeout (int): Timeout dla żądań HTTP w sekundach. Domyślnie 15.
        """
        if not base_url or not api_key:
            raise ValueError("Base URL and API Key cannot be empty.")
        self.base_url = base_url.rstrip('/') + '/api/v0'
        self.headers = {'X-Auth-Token': api_key}
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        print(f"LibreNMS Client initialized for URL: {self.base_url}") # Log inicjalizacji

    def _get(self, endpoint, params=None):
        """Pomocnicza metoda do wykonywania żądań GET."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            # print(f"DEBUG: Calling API GET {url} with params {params}") # Debug log
            response = requests.get(url, headers=self.headers, params=params, verify=self.verify_ssl, timeout=self.timeout)
            response.raise_for_status()  # Rzuci wyjątkiem dla błędów HTTP (4xx, 5xx)
            if not response.content:
                # print(f"DEBUG: Empty response content from API ({url})") # Debug log
                return None
            data = response.json()
            # print(f"DEBUG: API Response OK from {url}: {str(data)[:200]}...") # Debug log
            return data
        except HTTPError as e:
            print(f"⚠ Błąd HTTP API LibreNMS ({url}): {e.response.status_code} {e.response.reason}")
            if e.response.status_code == 400:
                 print(f"  (Może to oznaczać brak danych dla tego zasobu, np. FDB na porcie)")
            return None
        except requests.exceptions.RequestException as e:
            print(f"⚠ Błąd połączenia z API LibreNMS ({url}): {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"⚠ Błąd dekodowania JSON z API LibreNMS ({url}): {e}")
            try:
                print(f"  Otrzymano treść (fragment): {response.text[:200]}...")
            except NameError:
                pass
            return None
        except Exception as e:
             print(f"⚠ Nieoczekiwany błąd podczas komunikacji z API ({url}): {e}")
             return None

    def get_devices(self, columns=None):
        """
        Pobiera listę urządzeń. Opcjonalnie można podać kolumny do pobrania.
        """
        params = {'columns': columns} if columns else None
        data = self._get('devices', params=params)
        return data.get('devices', []) if isinstance(data, dict) else []

    def get_device(self, identifier, by_hostname=False):
         """Pobiera pojedyncze urządzenie po ID, IP lub hostname."""
         endpoint = f'devices/{identifier}'
         if by_hostname or '.' in identifier or not identifier.replace('.','').isdigit():
              print(f"Wyszukiwanie urządzenia po hostname '{identifier}' przez filtrowanie...")
              all_devices = self.get_devices(columns="device_id,hostname,ip")
              found = [d for d in all_devices if d.get('hostname', '').lower() == identifier.lower()]
              if len(found) == 1:
                  return self._get(f'devices/{found[0]["device_id"]}')
              elif len(found) > 1:
                  print(f"Znaleziono wiele urządzeń pasujących do hostname '{identifier}'.")
                  return None
              else:
                   # Spróbuj wyszukać po IP jako fallback jeśli hostname nie zadziałało
                   found_ip = [d for d in all_devices if d.get('ip', '') == identifier]
                   if len(found_ip) == 1:
                        return self._get(f'devices/{found_ip[0]["device_id"]}')
                   return None # Ostatecznie nie znaleziono
         else:
              # Wyszukiwanie po ID lub IP
              data = self._get(endpoint)
              # Sprawdź, czy API zwróciło błąd w strukturze odpowiedzi
              if isinstance(data, dict) and data.get('status') == 'error':
                   print(f"API zwróciło błąd dla urządzenia '{identifier}': {data.get('message')}")
                   return None
              return data


    def get_ports(self, device_id, columns=None):
        """
        Pobiera porty dla danego device_id. Opcjonalnie można podać kolumny.
        """
        default_cols = "port_id,ifName,ifIndex,ifDescr,ifSpeed,ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias"
        final_columns = columns if columns else default_cols
        params = {'columns': final_columns}
        data = self._get(f'devices/{device_id}/ports', params=params)
        return data.get('ports', []) if isinstance(data, dict) else []

    def get_port_fdb(self, device_id, port_id):
        """
        Pobiera wpisy FDB (tablicy MAC) dla danego portu na urządzeniu.
        Zwraca pustą listę w przypadku błędu HTTP 400 (brak FDB).
        Zwraca None w przypadku innych błędów.
        """
        url = f"{self.base_url}/devices/{device_id}/ports/{port_id}/fdb"
        try:
            response = requests.get(url, headers=self.headers, verify=self.verify_ssl, timeout=self.timeout)
            response.raise_for_status()
            if not response.content: return []
            data = response.json()
            # Sprawdź status w odpowiedzi JSON, jeśli istnieje
            if isinstance(data, dict) and data.get('status') == 'error':
                # LibreNMS czasami zwraca 200 OK ale z błędem w JSON
                # print(f"  ⓘ API FDB zwróciło błąd dla {device_id}/{port_id}: {data.get('message')}")
                return [] # Traktuj jak brak FDB
            return data.get("fdb", []) if isinstance(data, dict) else []
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                # print(f"  ⓘ Brak FDB dla {device_id}/{port_id} (kod 400).")
                return []
            else:
                 print(f"⚠ Błąd HTTP API LibreNMS ({url}): {e.response.status_code} {e.response.reason}")
                 return None
        except requests.exceptions.RequestException as e:
            print(f"⚠ Błąd połączenia z API LibreNMS ({url}): {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"⚠ Błąd dekodowania JSON z API LibreNMS ({url}): {e}")
            try: print(f"  Otrzymano treść (fragment): {response.text[:200]}...")
            except: pass
            return None
        except Exception as e:
             print(f"⚠ Nieoczekiwany błąd podczas pobierania FDB ({url}): {e}")
             return None