# src/librenms_client.py
import requests
import json
# Używamy importu względnego, zakładając uruchamianie przez "python -m src.main"
import config

class LibreNMSClient:
    """Handles communication with the LibreNMS API."""

    def __init__(self):
        self.base_url = config.get_librenms_url()
        self.token = config.get_librenms_token()
        self.headers = {'X-Auth-Token': self.token}
        if not self.base_url or not self.token:
            raise ValueError("LibreNMS URL or Token is missing in configuration.")

    def _make_request(self, endpoint):
        """Performs a GET request to a given API endpoint."""
        url = f"{self.base_url}/{endpoint}"
        response = None # Zdefiniuj poza try, aby mieć dostęp w except
        try:
            print(f"DEBUG: Making API request to: {url}")
            response = requests.get(url, headers=self.headers, timeout=20)
            print(f"DEBUG: API response status code: {response.status_code}")
            # Sprawdź status HTTP i zgłoś błąd jeśli to 4xx lub 5xx
            response.raise_for_status()
            # Próbuj zdekodować JSON - to może rzucić requests.exceptions.JSONDecodeError
            # Jeśli status to 200 OK, ale treść jest pusta, response.json() też rzuci błąd
            return response.json()

        except requests.exceptions.Timeout:
            print(f"ERROR: Request timed out for {endpoint}")
            return None
        except requests.exceptions.HTTPError as http_err:
            # Błędy HTTP (np. 4xx, 5xx)
            print(f"ERROR: HTTP error occurred for {endpoint}: {http_err}")
            if response is not None:
                try:
                    # Spróbuj zalogować treść odpowiedzi przy błędach HTTP
                    print(f"ERROR: Response Text (HTTP Error): >>>\n{response.text[:1000]}...\n<<<")
                except Exception:
                    print("ERROR: Could not read response text on HTTP Error.")
            return None
        # --- POPRAWIONA KOLEJNOŚĆ: NAJPIERW JSONDecodeError ---
        except requests.exceptions.JSONDecodeError as json_err:
            # Specyficzny błąd dekodowania JSON (nawet przy statusie 200 OK)
            print(f"ERROR: Failed to decode JSON response from {endpoint}. Error: {json_err}")
            if response is not None:
                # ZALOGUJ DOKŁADNĄ TREŚĆ ODPOWIEDZI, KTÓRA NIE JEST JSON-EM
                print(f"ERROR: Response Text Received (Invalid JSON): >>>\n{response.text[:1000]}...\n<<<") # Pokaż do 1000 znaków
            else:
                print("ERROR: No response object available to print text.")
            return None
        # --- INNE BŁĘDY SIECIOWE/REQUESTÓW NA KOŃCU ---
        except requests.exceptions.RequestException as req_err:
            # Inne błędy (np. problem z połączeniem, SSL)
            print(f"ERROR: A general network or request error occurred for {endpoint}: {req_err}")
            if response is not None:
                try:
                    print(f"ERROR: Response Text (RequestException): {response.text[:500]}...")
                except Exception:
                    pass # Unikaj kaskady błędów
            return None

    def get_device_ports(self, device_hostname_or_ip):
        """
        Fetches port information for a specific device.
        Uses the hostname or IP address as the device identifier.
        """
        print(f"INFO: Fetching ports for device: {device_hostname_or_ip}")
        # Upewnij się, że znaki specjalne w identyfikatorze są zakodowane (np. spacje)
        safe_device_id = requests.utils.quote(device_hostname_or_ip)
        endpoint = f"devices/{safe_device_id}/ports"
        data = self._make_request(endpoint)

        # Sprawdzenie, czy dane zostały poprawnie pobrane (nie są None)
        if data and isinstance(data, dict) and data.get('status') == 'ok':
            count = len(data.get('ports', []))
            print(f"INFO: Successfully retrieved {count} ports for {device_hostname_or_ip}")
            # Zwróć listę słowników portów
            return data.get('ports', [])
        elif data and isinstance(data, dict):
            # Błąd API zgłoszony przez LibreNMS (np. status='error')
            print(f"WARN: Could not retrieve ports for {device_hostname_or_ip}. API Status: {data.get('status')}, Message: {data.get('message')}")
            return None
        else:
            # Błąd połączenia, dekodowania JSON lub inny problem został już zgłoszony w _make_request
            # `data` będzie None w tym przypadku
            print(f"WARN: Failed to get valid data from API for {device_hostname_or_ip} (data is None or not a dict).")
            return None