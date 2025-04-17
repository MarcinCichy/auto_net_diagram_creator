# librenms_api.py

import requests
from requests.exceptions import HTTPError

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
        # Dodajemy port_id, żeby potem móc wywołać /ports/{port_id}/fdb
        COLUMNS = (
            "port_id,"
            "ifName,ifIndex,ifDescr,ifSpeed,"
            "ifAdminStatus,ifOperStatus,ifPhysAddress,ifAlias"
        )
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
        data = self._get(f"ports/{port_id}/description")
        if isinstance(data, dict):
            return data.get("port_description", "")
        return ""

    def get_port_alias(self, port_id):
        data = self._get(f"ports/{port_id}/ifAlias")
        if isinstance(data, dict):
            return data.get("ifAlias", "")
        return ""

    def get_port_fdb(self, device_id, port_id):
        """
        Zwraca listę wpisów FDB (MAC table) dla danego device_id i port_id.
        Jeśli API zwróci 400 (brak FDB na tym porcie), zwracamy [].
        """
        endpoint = f"devices/{device_id}/ports/{port_id}/fdb"
        try:
            data = self._get(endpoint)
            return data.get("fdb", [])
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                print(f"⚠ Brak FDB dla {device_id}/{port_id}, pomijam...")
                return []
            raise
