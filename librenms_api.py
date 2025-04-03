# librenms_api.py
import requests


class LibreNMSAPI:
    def __init__(self, base_url, api_key):
        """
        base_url: URL Twojego serwera LibreNMS, np. "http://lnms.debacom.pl"
        api_key: Klucz API do autoryzacji
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key

    def _get(self, endpoint, params=None):
        url = f"{self.base_url}/api/v0/{endpoint}"
        headers = {"X-Auth-Token": self.api_key}
        response = requests.get(url, headers=headers, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        # print(f"DEBUG _get [{url}]: {data}")
        return data

    def get_devices(self):
        data = self._get("devices")
        devices = data.get("devices", [])
        # print("DEBUG get_devices:", devices)
        return devices

    def get_ports(self, device_id):
        # Wywołujemy endpoint: /devices/{device_id}/ports?columns=ifName,ifIndex,ifDescr,ifSpeed,ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias
        COLUMNS = "ifName,ifIndex,ifDescr,ifSpeed,ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias"
        endpoint = f"devices/{device_id}/ports?columns={COLUMNS}"
        data = self._get(endpoint)
        if isinstance(data, dict):
            ports = data.get("ports", [])
            # print(f"DEBUG get_ports dla device_id {device_id}:", ports)
            return ports
        else:
            print("Otrzymano nieoczekiwany format danych w get_ports:", data)
            return []

    def get_port_description(self, port_id):
        """
        Pobiera opis portu (ifAlias) dla danego port_id.
        Używa endpointu: /ports/{port_id}/description
        Przykładowa odpowiedź: {"status": "ok", "port_description": "GigabitEthernet14"}
        """
        data = self._get(f"ports/{port_id}/description")
        if isinstance(data, dict):
            description = data.get("port_description", "")
            print(f"DEBUG get_port_description dla port_id {port_id}: {description}")
            return description
        return ""

    def get_port_alias(self, port_id):
        """
        Pobiera alias portu (ifAlias) dla danego port_id, używając endpointu: /ports/{port_id}/ifAlias.
        Przykładowa odpowiedź: {"status": "ok", "ifAlias": "GigabitEthernet14"}
        """
        data = self._get(f"ports/{port_id}/ifAlias")
        if isinstance(data, dict):
            alias = data.get("ifAlias", "")
            print(f"DEBUG get_port_alias dla port_id {port_id}: {alias}")
            return alias
        return ""
