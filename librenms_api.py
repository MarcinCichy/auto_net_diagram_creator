import requests

class LibreNMSAPI:
    def __init__(self, base_url, api_key):
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
        COLUMNS = "ifName,ifIndex,ifDescr,ifSpeed,ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias"
        endpoint = f"devices/{device_id}/ports?columns={COLUMNS}"
        data = self._get(endpoint)
        return data.get("ports", [])

    def get_device_info(self, device_id):
        endpoint = f"devices/{device_id}"
        data = self._get(endpoint)
        return data.get("device", {})
