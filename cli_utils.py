# cli_utils.py
import re
from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException

def cli_get_neighbors_enhanced(host, username, password):
    """
    Próbuje pobrać sąsiadów LLDP (preferowane) lub CDP przez CLI (SSH).
    Parsuje wynik poleceń 'show lldp neighbors detail' lub 'show cdp neighbors detail'.
    Wyciąga również VLAN ID z LLDP, jeśli jest dostępny.

    Args:
        host (str): Adres IP lub nazwa hosta urządzenia.
        username (str): Nazwa użytkownika do logowania CLI.
        password (str): Hasło do logowania CLI.

    Returns:
        list: Lista słowników reprezentujących znalezione połączenia,
              lub pusta lista w przypadku błędu lub braku znalezisk.
              Format słownika: {"local_host", "local_if", "neighbor_host", "neighbor_if", "vlan", "via"}
    """
    if not host or not username or not password:
         print("⚠ CLI: Brak adresu hosta, nazwy użytkownika lub hasła. Pomijam próbę CLI.")
         return []

    print(f"⟶ CLI fallback na {host}")
    device_params = {
        "device_type": "autodetect",
        "host": host,
        "username": username,
        "password": password,
        "global_delay_factor": 2,
        "session_log": f"{host}_netmiko_session.log",
        "session_log_file_mode": "append"
    }
    conns = []; conn = None

    try:
        print(f"  CLI: Łączenie z {host}...");
        conn = ConnectHandler(**device_params)
        print(f"  CLI: Połączono z {host} ({conn.device_type})")

        # --- Próba LLDP przez CLI ---
        lldp_success = False
        try:
            lldp_command = "show lldp neighbors detail"
            print(f"  CLI: Wykonywanie '{lldp_command}'...")
            lldp_output = conn.send_command_timing(lldp_command, delay_factor=5, max_loops=1000)

            if lldp_output:
                print(f"  CLI: Otrzymano dane LLDP, próba parsowania (format z 'Local Port id')...")
                parsed_count = 0
                header_line_match = re.search(r'Device ID\s+Local Intf\s+Hold-time', lldp_output, re.IGNORECASE)
                if header_line_match:
                    lldp_data_start_index = header_line_match.end()
                    lldp_data = lldp_output[lldp_data_start_index:]
                else:
                    first_chassis_match = re.search(r'Chassis id:', lldp_output, re.IGNORECASE)
                    if first_chassis_match:
                         lldp_data = lldp_output[first_chassis_match.start():]
                    else:
                         lldp_data = lldp_output

                blocks = re.split(r'\n(?=Chassis id:)', lldp_data, flags=re.IGNORECASE)

                for block in blocks:
                    if not block.strip() or not block.lower().startswith('chassis id:'):
                        continue

                    local_if_match = re.search(r'^Local Port id:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)
                    remote_sys_match = re.search(r'^System Name:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)
                    remote_port_id_match = re.search(r'^Port id:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)
                    remote_port_desc_match = re.search(r'^Port Description:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)
                    vlan_match = re.search(r'^Vlan ID:\s*(.+)$', block, re.MULTILINE | re.IGNORECASE)

                    if local_if_match and remote_sys_match and remote_port_id_match:
                        local_if = local_if_match.group(1).strip()
                        if not local_if or 'not advertised' in local_if.lower():
                             # print(f"  CLI-LLDP: Pominięto sąsiada - brak poprawnego Local Port id w bloku:\n{block[:200]}...")
                             continue

                        remote_sys = remote_sys_match.group(1).strip()
                        remote_port = remote_port_id_match.group(1).strip()

                        if (not remote_port or 'not advertised' in remote_port.lower()) and remote_port_desc_match:
                             remote_port_desc = remote_port_desc_match.group(1).strip()
                             if remote_port_desc and 'not advertised' not in remote_port_desc.lower():
                                 remote_port = remote_port_desc

                        vlan_id = None
                        if vlan_match:
                            vlan_value = vlan_match.group(1).strip()
                            if vlan_value and 'not advertised' not in vlan_value.lower():
                                vlan_id = vlan_value

                        if local_if and remote_sys and remote_port:
                             conns.append({
                                 "local_host": host, "local_if": local_if,
                                 "neighbor_host": remote_sys, "neighbor_if": remote_port,
                                 "vlan": vlan_id, "via": "CLI-LLDP"
                             })
                             parsed_count += 1
                        # else:
                             # print(f"  CLI-LLDP: Pominięto sąsiada - brak kompletu danych (local_if, remote_sys, remote_port) w bloku:\n{block[:200]}...")

                if parsed_count > 0:
                    print(f"✓ CLI-LLDP: Sparsowano {parsed_count} połączeń LLDP dla {host}.")
                    lldp_success = True
                elif lldp_output :
                    print(f"ⓘ CLI-LLDP: Otrzymano dane, ale nie udało się sparsować połączeń (format 'Local Port id') dla {host}.")
            else:
                 print(f"ⓘ CLI-LLDP: Brak danych wyjściowych LLDP dla {host}.")

        except Exception as e_lldp:
            print(f"⚠ Błąd CLI-LLDP: Nie udało się uzyskać/sparsować danych LLDP dla {host}: {e_lldp}")

        # --- Próba CDP przez CLI (tylko jeśli LLDP nie dało wyników) ---
        if not lldp_success:
            try:
                cdp_command = "show cdp neighbors detail"
                print(f"  CLI: Wykonywanie '{cdp_command}'...")
                cdp_output = conn.send_command_timing(cdp_command, delay_factor=4, max_loops=1000)

                if cdp_output and "Device ID" in cdp_output:
                    print(f"  CLI: Otrzymano dane CDP, próba parsowania...")
                    cdp_blocks = re.split(r'^-{10,}', cdp_output, flags=re.MULTILINE)
                    parsed_count_cdp = 0
                    for block in cdp_blocks:
                        dev_id_match = re.search(r'Device ID:\s*(\S+)', block, re.IGNORECASE)
                        local_if_match = re.search(r'Interface:\s*([^,]+),', block, re.IGNORECASE)
                        remote_if_match = re.search(r'Port ID \(outgoing port\):\s*(\S+)', block, re.IGNORECASE)
                        if dev_id_match and local_if_match and remote_if_match:
                            local_if_val = local_if_match.group(1).strip()
                            neighbor_host_val = dev_id_match.group(1).strip().split('.')[0] # Proste czyszczenie
                            neighbor_if_val = remote_if_match.group(1).strip()

                            conns.append({
                                "local_host": host, "local_if": local_if_val,
                                "neighbor_host": neighbor_host_val, "neighbor_if": neighbor_if_val,
                                "vlan": None, "via": "CLI-CDP"
                            })
                            parsed_count_cdp += 1
                    if parsed_count_cdp > 0:
                        print(f"✓ CLI-CDP: Sparsowano {parsed_count_cdp} połączeń CDP dla {host}")
                    elif cdp_output :
                        print(f"ⓘ CLI-CDP: Otrzymano dane, ale nie udało się sparsować połączeń dla {host}.")
                else:
                     print(f"ⓘ CLI-CDP: Brak danych wyjściowych CDP dla {host}.")

            except Exception as e_cdp:
                print(f"ⓘ CLI-CDP: Nie udało się uzyskać/sparsować danych CDP dla {host}: {e_cdp}")

        conn.disconnect()
        print(f"  CLI: Rozłączono z {host}")

    except (NetmikoTimeoutException, NetmikoAuthenticationException) as e_auth:
        print(f"⚠ Błąd CLI: Problem z połączeniem/autoryzacją SSH do {host}: {e_auth}")
    except Exception as e_conn:
        print(f"⚠ Błąd CLI: Ogólny błąd SSH/Netmiko dla {host}: {e_conn}")
        if conn and conn.is_alive():
            try: conn.disconnect()
            except: pass

    return conns