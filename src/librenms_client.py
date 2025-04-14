import requests
import json
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
        response = None
        try:
            print(f"DEBUG: Making API request to: {url}")
            response = requests.get(url, headers=self.headers, timeout=20)
            print(f"DEBUG: API response status code: {response.status_code}")
            response.raise_for_status()

            if not response.text.strip():
                print(f"ERROR: Empty response received from {endpoint}")
                return None

            return response.json()

        except requests.exceptions.Timeout:
            print(f"ERROR: Request timed out for {endpoint}")
            return None
        except requests.exceptions.HTTPError as http_err:
            print(f"ERROR: HTTP error occurred for {endpoint}: {http_err}")
            if response is not None:
                try:
                    print(f"ERROR: Response Text (HTTP Error): >>>\n{response.text[:1000]}...\n<<<")
                except Exception:
                    print("ERROR: Could not read response text on HTTP Error.")
            return None
        except requests.exceptions.JSONDecodeError as json_err:
            print(f"ERROR: Failed to decode JSON response from {endpoint}. Error: {json_err}")
            if response is not None:
                print(f"ERROR: Response Text Received (Invalid JSON): >>>\n{response.text[:1000]}...\n<<<")
            return None
        except requests.exceptions.RequestException as req_err:
            print(f"ERROR: A general network or request error occurred for {endpoint}: {req_err}")
            if response is not None:
                try:
                    print(f"ERROR: Response Text (RequestException): {response.text[:500]}...")
                except Exception:
                    pass
            return None

    def get_device_ports(self, device_hostname_or_ip):
        """
        Fetches extended port information for a specific device.
        Endpoint: GET devices/{device}/ports?extended=1
        Zwraca listę portów (przez klucz "port" lub "ports").
        """
        print(f"INFO: Fetching extended port info for device: {device_hostname_or_ip}")
        safe_device_id = requests.utils.quote(device_hostname_or_ip)
        endpoint = f"devices/{safe_device_id}/ports?extended=1"
        data = self._make_request(endpoint)
        print(f"DEBUG: Surowe dane z API:\n{json.dumps(data, indent=2)}")

        if data and isinstance(data, dict) and data.get('status') == 'ok':
            # Przyjmujemy, że dane są zwracane pod kluczem "port" lub "ports"
            ports_key = "port" if "port" in data else "ports"
            port_list = data.get(ports_key, [])
            count = len(port_list)
            print(f"INFO: Successfully retrieved {count} port(s) for device {device_hostname_or_ip}")
            return port_list
        elif data and isinstance(data, dict):
            print(f"WARN: Could not retrieve extended port info for {device_hostname_or_ip}. API Status: {data.get('status')}, Message: {data.get('message')}")
            return None
        else:
            print(f"WARN: Failed to get valid port list from API for {device_hostname_or_ip} (data is None or not a dict).")
            return None
