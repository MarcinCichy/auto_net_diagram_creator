# cli_utils.py
import re
import logging
from typing import List, Dict, Any, Optional
from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException
from typing import List, Dict, Any, Optional # <<< DODANO TEN IMPORT

logger = logging.getLogger(__name__)

# --- Prekompilowane wyrażenia regularne ---
# LLDP
RE_LLDP_HEADER_CANDIDATE = re.compile(r'(Device ID\s+Local Intf\s+Hold-time|Chassis id:)',
                                      re.IGNORECASE)  # Ogólniejsze do znalezienia początku danych
RE_LLDP_BLOCK_SPLIT = re.compile(r'\n\s*(?=Chassis id:)',
                                 flags=re.IGNORECASE)  # Upewnia się, że "Chassis id:" jest na początku linii (z opcjonalnymi białymi znakami przed)

RE_LLDP_LOCAL_PORT_ID = re.compile(r'^Local Port id:\s*(.+?)\s*$', re.MULTILINE | re.IGNORECASE)
RE_LLDP_SYS_NAME = re.compile(r'^System Name:\s*(.+?)\s*$', re.MULTILINE | re.IGNORECASE)
RE_LLDP_REMOTE_PORT_ID = re.compile(r'^Port id:\s*(.+?)\s*$', re.MULTILINE | re.IGNORECASE)
RE_LLDP_REMOTE_PORT_DESC = re.compile(r'^Port Description:\s*(.+?)\s*$', re.MULTILINE | re.IGNORECASE)
RE_LLDP_VLAN_ID = re.compile(r'^(?:Port and )?Vlan ID:\s*([0-9]+)\s*$',
                             re.MULTILINE | re.IGNORECASE)  # Tylko cyfry dla VLAN ID

# CDP
RE_CDP_BLOCK_SPLIT = re.compile(r'^-{10,}\s*$', flags=re.MULTILINE)  # Linia separatora -----
RE_CDP_DEVICE_ID = re.compile(r'Device ID:\s*(\S+)', re.IGNORECASE)
RE_CDP_LOCAL_IF = re.compile(r'Interface:\s*([^,]+(?:,\s*port\s+\S+)?)',
                             re.IGNORECASE)  # Obsługa "Interface: GigabitEthernet1/0/1, port G1/0/1"
RE_CDP_REMOTE_IF = re.compile(r'(?:Port ID|Outgoing Port):\s*(\S+)',
                              re.IGNORECASE)  # Port ID (outgoing port) lub samo Port ID


def _normalize_interface_name(if_name: str) -> str:
    """Normalizuje popularne skróty nazw interfejsów."""
    if_name = if_name.strip()
    # Można dodać więcej mapowań w miarę potrzeb
    # To jest proste mapowanie, bardziej zaawansowane mogłoby używać regexów
    # lub być zależne od platformy.
    replacements = {
        "GigabitEthernet": "Gi",
        "TenGigabitEthernet": "Te",
        "FastEthernet": "Fa",
        "Ethernet": "Eth",
        "mgmt": "mgmt",  # Czasem mgmt0
        # ... inne ...
    }
    # Sprawdź dłuższe nazwy najpierw
    for long, short in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if if_name.lower().startswith(long.lower()):
            # Zachowaj numerację, np. GigabitEthernet1/0/1 -> Gi1/0/1
            return short + if_name[len(long):]
    return if_name


def _parse_lldp_output(lldp_output: str, local_hostname: str) -> List[Dict[str, Any]]:
    """Parsuje wyjście 'show lldp neighbors detail'."""
    connections: List[Dict[str, Any]] = []
    if not lldp_output:
        return connections

    logger.debug(f"CLI-LLDP: Próba parsowania danych LLDP dla {local_hostname}...")

    # Znajdź początek rzeczywistych danych (po potencjalnym nagłówku tabeli lub pierwszym "Chassis id:")
    data_to_parse = lldp_output
    first_marker = RE_LLDP_HEADER_CANDIDATE.search(lldp_output)
    if first_marker:
        # Jeśli to "Chassis id:", zacznij od tego. Jeśli to nagłówek, przesuń się za niego.
        if "chassis id:" in first_marker.group(0).lower():
            data_to_parse = lldp_output[first_marker.start():]
        else:  # Prawdopodobnie nagłówek tabelaryczny
            # Spróbuj znaleźć pierwsze "Chassis id:" po tym nagłówku
            first_chassis_after_header = RE_LLDP_BLOCK_SPLIT.search(lldp_output, first_marker.end())
            if first_chassis_after_header:
                data_to_parse = lldp_output[first_chassis_after_header.start():]
            else:  # Jeśli nie ma "Chassis id:" po nagłówku, ale był nagłówek, to coś jest nie tak
                logger.warning(
                    f"CLI-LLDP: Znaleziono nagłówek, ale brak bloków 'Chassis id:' w danych dla {local_hostname}.")
                return connections
    else:  # Nie znaleziono ani nagłówka, ani "Chassis id:"
        logger.info(
            f"CLI-LLDP: Nie znaleziono znacznika początku danych LLDP (nagłówka lub 'Chassis id:') dla {local_hostname}.")
        return connections

    blocks = RE_LLDP_BLOCK_SPLIT.split(data_to_parse)
    parsed_count = 0

    for block_idx, block_content in enumerate(blocks):
        block_strip = block_content.strip()
        # Pierwszy "blok" po splicie może być pusty, jeśli dane zaczynały się od "Chassis id:"
        if not block_strip or (block_idx == 0 and not block_strip.lower().startswith('chassis id:')):
            if block_strip and not block_strip.lower().startswith(
                    'chassis id:'):  # Jeśli pierwszy blok nie jest pusty i nie jest chassis
                logger.debug(
                    f"CLI-LLDP: Pomijam pierwszy blok, który nie zaczyna się od 'Chassis id:' dla {local_hostname}:\n{block_strip[:100]}...")
            continue

        # Upewnij się, że blok rzeczywiście zaczyna się od "Chassis id:" (poza pierwszym blokiem, który jest sprawdzany wyżej)
        if block_idx > 0 and not block_strip.lower().startswith('chassis id:'):
            logger.debug(
                f"CLI-LLDP: Pomijam blok, który nie zaczyna się od 'Chassis id:' dla {local_hostname} (blok #{block_idx}):\n{block_strip[:100]}...")
            continue

        local_if_match = RE_LLDP_LOCAL_PORT_ID.search(block_strip)
        remote_sys_match = RE_LLDP_SYS_NAME.search(block_strip)
        remote_port_id_match = RE_LLDP_REMOTE_PORT_ID.search(block_strip)
        remote_port_desc_match = RE_LLDP_REMOTE_PORT_DESC.search(block_strip)
        vlan_match = RE_LLDP_VLAN_ID.search(block_strip)

        if not (local_if_match and remote_sys_match and remote_port_id_match):
            logger.debug(
                f"CLI-LLDP: Pominięto blok {block_idx} - brak podstawowych danych (LocalPort, SysName, RemotePort) dla {local_hostname}.")
            continue

        local_if_raw = local_if_match.group(1).strip()
        if not local_if_raw or 'not advertised' in local_if_raw.lower():
            logger.debug(
                f"CLI-LLDP: Pominięto sąsiada w bloku {block_idx} - brak poprawnego Local Port id dla {local_hostname}.")
            continue
        local_if = _normalize_interface_name(local_if_raw)

        remote_sys = remote_sys_match.group(1).strip()
        remote_port_raw = remote_port_id_match.group(1).strip()
        remote_port_desc_val = remote_port_desc_match.group(1).strip() if remote_port_desc_match else ""

        chosen_remote_port = remote_port_raw
        if (not chosen_remote_port or 'not advertised' in chosen_remote_port.lower() or ':' in chosen_remote_port) and \
                remote_port_desc_val and 'not advertised' not in remote_port_desc_val.lower():
            chosen_remote_port = remote_port_desc_val

        if not chosen_remote_port or 'not advertised' in chosen_remote_port.lower():
            logger.debug(
                f"CLI-LLDP: Pominięto sąsiada w bloku {block_idx} - brak poprawnego Remote Port id/desc dla {local_hostname}.")
            continue
        remote_if = _normalize_interface_name(chosen_remote_port)

        vlan_id_str = None
        if vlan_match:
            vlan_value = vlan_match.group(1).strip()  # Regex łapie tylko cyfry
            if vlan_value:  # 'not advertised' nie powinno tu pasować, ale dla pewności
                vlan_id_str = vlan_value

        connections.append({
            "local_host": local_hostname, "local_if": local_if,
            "neighbor_host": remote_sys, "neighbor_if": remote_if,
            "vlan": vlan_id_str, "via": "CLI-LLDP"
        })
        parsed_count += 1

    if parsed_count > 0:
        logger.info(f"✓ CLI-LLDP: Sparsowano {parsed_count} połączeń LLDP dla {local_hostname}.")
    elif lldp_output:
        logger.info(
            f"ⓘ CLI-LLDP: Otrzymano dane LLDP, ale nie udało się sparsować żadnych użytecznych połączeń dla {local_hostname}.")

    return connections


def _parse_cdp_output(cdp_output: str, local_hostname: str) -> List[Dict[str, Any]]:
    """Parsuje wyjście 'show cdp neighbors detail'."""
    connections: List[Dict[str, Any]] = []
    if not cdp_output or "Device ID" not in cdp_output:
        return connections

    logger.debug(f"CLI-CDP: Próba parsowania danych CDP dla {local_hostname}...")
    # Dziel bloki po separatorze "------"
    # Usuń pierwszy element jeśli jest pusty (wynik splitu, jeśli dane zaczynają się od separatora)
    cdp_blocks_raw = RE_CDP_BLOCK_SPLIT.split(cdp_output)
    cdp_blocks = [block.strip() for block in cdp_blocks_raw if block.strip()]
    parsed_count_cdp = 0

    for block_idx, block_content in enumerate(cdp_blocks):
        dev_id_match = RE_CDP_DEVICE_ID.search(block_content)
        local_if_match = RE_CDP_LOCAL_IF.search(block_content)
        remote_if_match = RE_CDP_REMOTE_IF.search(block_content)

        if dev_id_match and local_if_match and remote_if_match:
            local_if_raw = local_if_match.group(1).strip()
            # Czasem lokalny interfejs jest podawany jako "Interface: GigabitEthernet1/0/1, port G1/0/1 jest ..."
            # Bierzemy pierwszą część.
            local_if = _normalize_interface_name(local_if_raw.split(',')[0].strip())

            neighbor_host_val_raw = dev_id_match.group(1).strip()
            neighbor_host_val = neighbor_host_val_raw.split('.')[
                0] if '.' in neighbor_host_val_raw else neighbor_host_val_raw

            remote_if_raw = remote_if_match.group(1).strip()
            remote_if = _normalize_interface_name(remote_if_raw)

            if local_if and neighbor_host_val and remote_if:
                connections.append({
                    "local_host": local_hostname, "local_if": local_if,
                    "neighbor_host": neighbor_host_val, "neighbor_if": remote_if,
                    "vlan": None, "via": "CLI-CDP"  # CDP rzadko przenosi VLAN ID w standardowym 'detail'
                })
                parsed_count_cdp += 1
            else:
                logger.debug(
                    f"CLI-CDP: Pominięto blok {block_idx} - niekompletne dane po ekstrakcji dla {local_hostname}.")
        else:
            logger.debug(
                f"CLI-CDP: Nie udało się sparsować podstawowych danych z bloku {block_idx} dla {local_hostname}:\n{block_content[:150]}...")

    if parsed_count_cdp > 0:
        logger.info(f"✓ CLI-CDP: Sparsowano {parsed_count_cdp} połączeń CDP dla {local_hostname}.")
    elif cdp_output:
        logger.info(
            f"ⓘ CLI-CDP: Otrzymano dane CDP, ale nie udało się sparsować żadnych użytecznych połączeń dla {local_hostname}.")

    return connections


def cli_get_neighbors_enhanced(host: str, username: str, password: str) -> List[Dict[str, Any]]:
    """
    Próbuje pobrać sąsiadów LLDP (preferowane) lub CDP przez CLI (SSH) za pomocą Netmiko.
    """
    if not host or not username or not password:
        logger.warning(
            f"CLI: Brak adresu hosta ('{host}'), nazwy użytkownika ('{username}') lub hasła. Pomijam próbę CLI.")
        return []

    logger.info(f"⟶ CLI: Próba odkrycia sąsiadów dla {host}")
    device_params = {
        "device_type": "autodetect",
        "host": host,
        "username": username,
        "password": password,
        "global_delay_factor": 2,
        "session_log": f"{host}_netmiko_session.log",  # Rozważ ścieżkę konfigurowalną lub katalog tymczasowy
        "session_log_file_mode": "append",
        "conn_timeout": 30,  # Zwiększony timeout połączenia
        "auth_timeout": 40,  # Zwiększony timeout autoryzacji
        "banner_timeout": 30,  # Zwiększony timeout banera
    }
    all_cli_connections: List[Dict[str, Any]] = []
    net_connect: Optional[ConnectHandler] = None

    try:
        logger.info(f"  CLI: Łączenie z {host}...")
        net_connect = ConnectHandler(**device_params)
        logger.info(f"  CLI: Połączono z {host} (Wykryty typ: {net_connect.device_type})")

        # --- Próba LLDP przez CLI ---
        # Polecenia mogą się różnić w zależności od platformy
        # TODO: Dodać logikę wyboru polecenia na podstawie net_connect.device_type
        lldp_command = "show lldp neighbors detail"  # Domyślne dla Cisco IOS/NX-OS
        if "junos" in net_connect.device_type:
            lldp_command = "show lldp neighbors interface all detail"  # Przykład dla Junos
        elif "iosxr" in net_connect.device_type:
            lldp_command = "show lldp neighbors detail"  # Podobne do IOS
        # Można dodać więcej platform

        logger.info(f"  CLI: Wykonywanie '{lldp_command}' dla LLDP na {host}...")
        try:
            # Użyj send_command z rozsądnym read_timeout, jeśli send_command_timing sprawia problemy
            lldp_output = net_connect.send_command(
                lldp_command,
                read_timeout=90  # Dłuższy timeout na odczyt dla potencjalnie dużego wyjścia
            )
            if lldp_output:
                lldp_conns = _parse_lldp_output(lldp_output, host)
                all_cli_connections.extend(lldp_conns)
            else:
                logger.info(f"  CLI-LLDP: Brak danych wyjściowych LLDP dla {host} (polecenie: '{lldp_command}').")
        except Exception as e_lldp_cmd:
            logger.warning(f"  CLI-LLDP: Błąd podczas wykonywania/parsowania polecenia LLDP dla {host}: {e_lldp_cmd}")

        # --- Próba CDP przez CLI (tylko jeśli LLDP nie dało wyników) ---
        if not all_cli_connections:
            cdp_command = "show cdp neighbors detail"  # Domyślne dla Cisco
            # TODO: Dodać logikę wyboru polecenia CDP dla innych platform, jeśli CDP jest tam wspierane
            logger.info(f"  CLI: Wykonywanie '{cdp_command}' dla CDP na {host} (bo LLDP nie dało wyników)...")
            try:
                cdp_output = net_connect.send_command(
                    cdp_command,
                    read_timeout=90
                )
                if cdp_output:
                    cdp_conns = _parse_cdp_output(cdp_output, host)
                    all_cli_connections.extend(cdp_conns)
                else:
                    logger.info(f"  CLI-CDP: Brak danych wyjściowych CDP dla {host} (polecenie: '{cdp_command}').")
            except Exception as e_cdp_cmd:
                logger.warning(f"  CLI-CDP: Błąd podczas wykonywania/parsowania polecenia CDP dla {host}: {e_cdp_cmd}")

    except NetmikoAuthenticationException as e_auth:
        logger.error(f"⚠ CLI: Błąd autoryzacji SSH do {host}: {e_auth}")
    except NetmikoTimeoutException as e_timeout:
        logger.error(f"⚠ CLI: Timeout podczas próby połączenia SSH z {host}: {e_timeout}")
    except Exception as e_conn:  # Inne błędy Netmiko lub ogólne
        logger.error(f"⚠ CLI: Ogólny błąd SSH/Netmiko dla {host}: {e_conn}", exc_info=True)
    finally:
        if net_connect and net_connect.is_alive():
            try:
                net_connect.disconnect()
                logger.info(f"  CLI: Rozłączono z {host}")
            except Exception as e_disc:
                logger.error(f"  CLI: Błąd podczas próby rozłączenia z {host}: {e_disc}", exc_info=True)

    if not all_cli_connections:
        logger.info(f"⟶ CLI: Nie znaleziono żadnych sąsiadów przez CLI dla {host}.")

    return all_cli_connections