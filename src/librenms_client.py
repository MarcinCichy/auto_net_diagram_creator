import requests
import json
from . import config # Use relative import within the package

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
        try:
            response = requests.get(url, headers=self.headers, timeout=20)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            return response.json()
        except requests.exceptions.Timeout:
            print(f"ERROR: Request timed out for {endpoint}")
            return None
        except requests.exceptions.HTTPError as http_err:
            print(f"ERROR: HTTP error occurred for {endpoint}: {http_err} - {response.text}")
            return None
        except requests.exceptions.RequestException as req_err:
            print(f"ERROR: Network error occurred for {endpoint}: {req_err}")
            return None
        except json.JSONDecodeError:
            print(f"ERROR: Invalid JSON response from {endpoint}. Response: {response.text}")
            return None

    def get_device_ports(self, device_hostname_or_ip):
        """
        Fetches port information for a specific device.
        Uses the hostname or IP address as the device identifier.
        """
        print(f"INFO: Fetching ports for device: {device_hostname_or_ip}")
        endpoint = f"devices/{requests.utils.quote(device_hostname_or_ip)}/ports"
        data = self._make_request(endpoint)

        if data and data.get('status') == 'ok':
            print(f"INFO: Successfully retrieved {len(data.get('ports', []))} ports for {device_hostname_or_ip}")
            # Return the list of port dictionaries
            return data.get('ports', [])
        elif data:
            print(f"WARN: Could not retrieve ports for {device_hostname_or_ip}. API Status: {data.get('status')}, Message: {data.get('message')}")
            return None
        else:
            # Error already logged by _make_request
            return None

# Example Usage (for testing this module directly)
if __name__ == '__main__':
    print("Testing LibreNMSClient...")
    # You need a valid device IP/hostname from your ip_list.txt for this test
    test_device_ip = "YOUR_SWITCH_IP_FOR_TESTING" # Replace with a real IP from your list
    if test_device_ip == "YOUR_SWITCH_IP_FOR_TESTING":
         print("Please replace 'YOUR_SWITCH_IP_FOR_TESTING' in librenms_client.py for testing.")
    else:
        try:
            client = LibreNMSClient()
            ports = client.get_device_ports(test_device_ip)
            if ports:
                print(f"\nSample ports data for {test_device_ip}:")
                for port in ports[:5]: # Print first 5 ports
                    print(f"  - ID: {port.get('port_id')}, Name: {port.get('ifName')}, "+
                          f"Alias: {port.get('ifAlias')}, OperStatus: {port.get('ifOperStatus')}, "+
                          f"AdminStatus: {port.get('ifAdminStatus')}")
            else:
                print(f"Could not fetch ports for {test_device_ip}.")
        except ValueError as e:
            print(f"Configuration Error: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")