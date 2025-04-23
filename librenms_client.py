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
        # Usunięto print dla mniejszej gadatliwości
        # print(f"LibreNMS Client initialized for URL: {self.base_url}")

    def _get(self, endpoint, params=None):
        """Pomocnicza metoda do wykonywania żądań GET z lepszą obsługą błędów."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = requests.get(url, headers=self.headers, params=params, verify=self.verify_ssl, timeout=self.timeout)
            response.raise_for_status()
            if not response.content:
                return None
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
            try:
                print(f"  Otrzymano treść (fragment): {response.text[:200]}...")
            except NameError: pass
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
         if by_hostname or (isinstance(identifier, str) and ('.' in identifier or not identifier.isdigit())):
              # print(f"Wyszukiwanie urządzenia po hostname/nazwie '{identifier}' przez filtrowanie...")
              all_devices = self.get_devices(columns="device_id,hostname,ip")
              if not all_devices: return None

              found_host = [d for d in all_devices if d.get('hostname', '').lower() == identifier.lower()]
              if len(found_host) == 1:
                   return self._get(f'devices/{found_host[0]["device_id"]}')
              elif len(found_host) > 1:
                   print(f"  Znaleziono wiele urządzeń pasujących do hostname '{identifier}'. Zwracam None.")
                   return None

              found_ip = [d for d in all_devices if d.get('ip', '') == identifier]
              if len(found_ip) == 1:
                   # print(f"  Nie znaleziono po hostname, ale znaleziono po IP dla '{identifier}'.")
                   return self._get(f'devices/{found_ip[0]["device_id"]}')

              # print(f"  Nie znaleziono urządzenia dla identyfikatora '{identifier}' (ani hostname ani IP).")
              return None
         else:
              return self._get(endpoint)


    def get_ports(self, device_id, columns=None):
        """
        Pobiera porty dla danego device_id. Opcjonalnie można podać kolumny.
        Domyślnie pobiera kolumny potrzebne do odkrywania i rysowania, W TYM ifType.
        """
        # *** JEDYNA ZMIANA TUTAJ: Dodano 'ifType' do default_cols ***
        default_cols = "port_id,ifName,ifIndex,ifDescr,ifSpeed,ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias,ifType"
        final_columns = columns if columns else default_cols
        params = {'columns': final_columns}
        data = self._get(f'devices/{device_id}/ports', params=params)
        return data.get('ports', []) if isinstance(data, dict) else []

    def get_port_fdb(self, device_id, port_id):
        """
        Pobiera wpisy FDB (tablicy MAC) dla danego portu na urządzeniu.
        Używa bezpośredniego requestu do obsługi błędu 400 specyficznie.
        Zwraca [] dla braku FDB lub błędu 400, None dla innych błędów.
        """
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
            if isinstance(data, dict) and data.get('status') == 'error':
                return []
            return data.get("fdb", []) if isinstance(data, dict) else []
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                return []
            else:
                 # Loguj tylko raz błąd dla danego URL podczas działania skryptu? (Zaawansowane)
                 # Na razie loguj zawsze:
                 print(f"⚠ Błąd HTTP API LibreNMS (FDB @ {url}): {e.response.status_code if e.response else 'N/A'} {e.response.reason if e.response else e}")
                 return None
        except requests.exceptions.RequestException as e:
            print(f"⚠ Błąd połączenia z API LibreNMS (FDB @ {url}): {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"⚠ Błąd dekodowania JSON z API LibreNMS (FDB @ {url}): {e}")
            try: print(f"  Otrzymano treść (fragment): {response.text[:200]}...")
            except: pass
            return None
        except Exception as e:
             print(f"⚠ Nieoczekiwany błąd podczas pobierania FDB ({url}): {e}")
             return None