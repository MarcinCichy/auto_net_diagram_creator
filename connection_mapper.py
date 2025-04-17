# connection_mapper.py

#!/usr/bin/env python3
import os
from config import get_config
from librenms_api import LibreNMSAPI

IP_LIST_FILE = "ip_list.txt"
OUTPUT_FILE  = "connections.txt"

def load_ip_list(file_path=IP_LIST_FILE):
    ips = []
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    ips.append(line)
    else:
        print(f"Plik {file_path} nie istnieje.")
    return ips

def build_phys_mac_map(api: LibreNMSAPI):
    """
    Zbiera wszystkie porty z ich fizycznymi MAC,
    tworząc mapę: mac_address -> { device_id, hostname, port_id, ifName }.
    """
    phys_map = {}
    devices = api.get_devices()
    for dev in devices:
        dev_id = dev.get("device_id")
        host   = dev.get("hostname", "")
        ports  = api.get_ports(str(dev_id))

        for p in ports:
            mac_raw = p.get("ifPhysAddress") or ""
            mac = mac_raw.lower().strip()
            port_id = p.get("port_id")
            if mac and port_id:
                phys_map[mac] = {
                    "device_id": dev_id,
                    "hostname":  host,
                    "port_id":   port_id,
                    "ifName":    p.get("ifName")
                }
    return phys_map

def find_connections_for_device(api: LibreNMSAPI, phys_map, target):
    """
    Dla każdego portu urządzenia `target` odczytuje FDB i sprawdza,
    czy widziane mac_address są w phys_map.
    """
    conns = []
    dev_id = target.get("device_id")
    host   = target.get("hostname", "")
    ports  = api.get_ports(str(dev_id))

    for p in ports:
        port_id  = p.get("port_id")
        local_if = p.get("ifName", "")
        if not port_id:
            continue

        # Pobranie FDB – jeśli zwróci błąd 400, zostanie zamieniony na []
        fdb = api.get_port_fdb(str(dev_id), str(port_id))
        if not fdb:
            continue

        for entry in fdb:
            mac_raw = entry.get("mac_address") or ""
            mac = mac_raw.lower().strip()
            neigh = phys_map.get(mac)
            if neigh:
                conns.append({
                    "local_host":    host,
                    "local_if":      local_if,
                    "neighbor_host": neigh["hostname"],
                    "neighbor_if":   neigh["ifName"],
                    "vlan":          entry.get("vlan")
                })
    return conns

def main():
    # 1) Wczytanie konfiguracji
    try:
        cfg = get_config()
    except ValueError as e:
        print(e)
        return

    api = LibreNMSAPI(cfg["base_url"], cfg["api_key"])

    # 2) Budowanie mapy MAC → port
    print("Buduję mapę MAC → (device, port)...")
    phys_map = build_phys_mac_map(api)

    # 3) Wczytanie listy IP
    ips = load_ip_list()
    if not ips:
        print("Brak adresów w ip_list.txt")
        return

    all_conns = []
    devices   = api.get_devices()
    for ip in ips:
        tgt = next(
            (d for d in devices if d.get("ip") == ip or ip in d.get("hostname", "")),
            None
        )
        if not tgt:
            print(f"⚠ Nie znaleziono urządzenia dla IP/hosta '{ip}'")
            continue

        print(f"⟶ Analiza urządzenia {tgt.get('hostname')} (ID {tgt.get('device_id')})")
        conns = find_connections_for_device(api, phys_map, tgt)
        all_conns.extend(conns)

    # 4) Wypisanie i zapis wyników
    if all_conns:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            for c in all_conns:
                line = (
                    f"{c['local_host']}:{c['local_if']} "
                    f"→ {c['neighbor_host']}:{c['neighbor_if']} "
                    f"(VLAN {c['vlan']})"
                )
                print(line)
                f.write(line + "\n")
        print(f"\n✅ Połączenia zapisane w {OUTPUT_FILE}")
    else:
        print("Nie wykryto żadnych połączeń.")

if __name__ == "__main__":
    main()
