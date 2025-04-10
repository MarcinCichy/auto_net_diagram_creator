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
        Fetches port information for a specific device.
        Uses the hostname or IP address as the device identifier.
        """
        print(f"INFO: Fetching ports for device: {device_hostname_or_ip}")
        safe_device_id = requests.utils.quote(device_hostname_or_ip)
        endpoint = f"devices/{safe_device_id}/ports"
        data = self._make_request(endpoint)

        if data and isinstance(data, dict) and data.get('status') == 'ok':
            count = len(data.get('ports', []))
            print(f"INFO: Successfully retrieved {count} ports for {device_hostname_or_ip}")
            return data.get('ports', [])
        elif data and isinstance(data, dict):
            print(f"WARN: Could not retrieve ports for {device_hostname_or_ip}. API Status: {data.get('status')}, Message: {data.get('message')}")
            return None
        else:
            print(f"WARN: Failed to get valid data from API for {device_hostname_or_ip} (data is None or not a dict).")
            return None
