# librenms_api.py
import requests
import time


class LibreNMSAPI:
    def __init__(self, base_url, api_key, retries=3, delay=1):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.retries = retries
        self.delay = delay

    def _request(self, method, endpoint, params=None, data=None):
        url = f"{self.base_url}/api/v0/{endpoint}"
        headers = {"X-Auth-Token": self.api_key}
        last_exception = None

        for attempt in range(self.retries):
            try:
                response = requests.request(method, url, headers=headers, params=params, json=data,
                                            timeout=15)  # Zwiększony timeout
                response.raise_for_status()  # Rzuci wyjątkiem dla błędów 4xx/5xx
                return response.json()
            except requests.exceptions.RequestException as e:
                print(f"Błąd zapytania API ({attempt + 1}/{self.retries}): {url} - {e}")
                last_exception = e
                time.sleep(self.delay * (attempt + 1))  # Exponential backoff

        # Jeśli wszystkie próby zawiodły
        if last_exception:
            print(f"Nie udało się wykonać zapytania API do {url} po {self.retries} próbach.")
            raise last_exception
        return None  # Powinno się nie zdarzyć jeśli raise_for_status() działa

    def _get(self, endpoint, params=None):
        return self._request('GET', endpoint, params=params)

    def get_devices(self):
        """
        Pobiera listę urządzeń. Próbuje zwrócić również 'snmp_community', jeśli jest dostępne.
        """
        try:
            data = self._get("devices")
            return data.get("devices", []) if data else []
        except requests.exceptions.RequestException as e:
            print(f"Krytyczny błąd podczas pobierania listy urządzeń: {e}")
            return []  # Zwróć pustą listę w razie błędu krytycznego

    def get_ports(self, device_id):
        """
        Pobiera porty dla danego urządzenia.
        """
        # Upewnij się, że device_id jest stringiem
        device_id_str = str(device_id)
        COLUMNS = "port_id,ifName,ifIndex,ifDescr,ifSpeed,ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias"
        endpoint = f"devices/{device_id_str}/ports?columns={COLUMNS}"
        try:
            data = self._get(endpoint)
            return data.get("ports", []) if data else []
        except requests.exceptions.RequestException as e:
            print(f"Błąd podczas pobierania portów dla urządzenia ID {device_id_str}: {e}")
            return []

    def get_device_info(self, device_id):
        """
        Pobiera szczegółowe informacje o jednym urządzeniu.
        Może być potrzebne do uzyskania community string, jeśli get_devices go nie zwraca.
        """
        device_id_str = str(device_id)
        endpoint = f"devices/{device_id_str}"
        try:
            data = self._get(endpoint)
            # Sprawdź, czy odpowiedź zawiera klucz 'devices' (tak, API zwraca listę z jednym elementem)
            if data and "devices" in data and isinstance(data["devices"], list) and len(data["devices"]) > 0:
                return data["devices"][0]  # Zwróć pierwszy (i jedyny) element listy
            elif data and "message" in data:
                print(f"Błąd API przy pobieraniu info dla urządzenia {device_id_str}: {data['message']}")
                return {}
            else:
                print(f"Otrzymano nieoczekiwaną odpowiedź API dla urządzenia {device_id_str}: {data}")
                return {}

        except requests.exceptions.RequestException as e:
            print(f"Błąd podczas pobierania informacji dla urządzenia ID {device_id_str}: {e}")
            return {}
        except IndexError:
            print(f"Otrzymano pustą listę urządzeń w odpowiedzi API dla ID {device_id_str}")
            return {}