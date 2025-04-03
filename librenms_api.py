# librenms_api.py
import requests

class LibreNMSAPI:
    def __init__(self, base_url, api_key):
        """
        base_url: URL Twojego serwera LibreNMS"
        api_key: Klucz API do autoryzacji
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key

    def _get(self, endpoint, params=None):
        url = f"{self.base_url}/api/v0/{endpoint}"
        headers = {"X-Auth-Token": self.api_key}
        response = requests.get(url, headers=headers, params=params, timeout=5)
        response.raise_for_status()
        return response.json()

    def get_devices(self):
        data = self._get("devices")
        return data.get("devices", [])

    def get_ports(self, device_id):
        data = self._get(f"devices/{device_id}/ports")
        if isinstance(data, dict):
            return data.get("ports", [])
        else:
            print("Otrzymano nieoczekiwany format danych w get_ports:", data)
            return []

    def get_port_description(self, port_id):
        # Jeśli chcielibyśmy wywołać dodatkowy endpoint, ale w tej wersji opieramy się na danych z get_ports
        data = self._get(f"ports/{port_id}/description")
        if isinstance(data, dict):
            return data.get("port_description", "")
        return ""
