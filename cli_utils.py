# cli_utils.py
import re
import logging
from typing import List, Dict, Any, Optional
from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException

logger = logging.getLogger(__name__)

# --- Prekompilowane wyrażenia regularne ---
RE_LLDP_HEADER_CANDIDATE = re.compile(r'(Device ID\s+Local Intf\s+Hold-time|Chassis id:)', re.IGNORECASE)
RE_LLDP_BLOCK_SPLIT = re.compile(r'\n\s*(?=Chassis id:)', flags=re.IGNORECASE)
RE_LLDP_LOCAL_PORT_ID = re.compile(r'^Local Port id:\s*(.+?)\s*$', re.MULTILINE | re.IGNORECASE)
RE_LLDP_SYS_NAME = re.compile(r'^System Name:\s*(.+?)\s*$', re.MULTILINE | re.IGNORECASE)
RE_LLDP_REMOTE_PORT_ID = re.compile(r'^Port id:\s*(.+?)\s*$', re.MULTILINE | re.IGNORECASE)
RE_LLDP_REMOTE_PORT_DESC = re.compile(r'^Port Description:\s*(.+?)\s*$', re.MULTILINE | re.IGNORECASE)
RE_LLDP_VLAN_ID = re.compile(r'^(?:Port and )?Vlan ID:\s*([0-9]+)\s*$', re.MULTILINE | re.IGNORECASE)

RE_CDP_BLOCK_SPLIT = re.compile(r'^-{10,}\s*$', flags=re.MULTILINE)
RE_CDP_DEVICE_ID = re.compile(r'Device ID:\s*(\S+)', re.IGNORECASE)
RE_CDP_LOCAL_IF = re.compile(r'Interface:\s*([^,]+(?:,\s*port\s+\S+)?)', re.IGNORECASE)
RE_CDP_REMOTE_IF = re.compile(r'(?:Port ID|Outgoing Port):\s*(\S+)', re.IGNORECASE)

RE_SLOT_SYS_PROMPT = re.compile(r'(?:\*\s*)?Slot-\d+\s+[\w.-]+\s*#\s*$')
RE_SIMPLE_PROMPT = re.compile(r"^[>#]\s*$")


def _normalize_interface_name(if_name: str) -> str:
    if_name = if_name.strip()
    replacements = {"GigabitEthernet": "Gi", "TenGigabitEthernet": "Te", "FastEthernet": "Fa", "Ethernet": "Eth",
                    "mgmt": "mgmt"}
    for long, short in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if if_name.lower().startswith(long.lower()):
            return short + if_name[len(long):]
    return if_name


def _parse_lldp_output(lldp_output: str, local_hostname: str) -> List[Dict[str, Any]]:
    connections: List[Dict[str, Any]] = []
    if not lldp_output: return connections
    logger.debug(f"CLI-LLDP: Próba parsowania danych LLDP dla {local_hostname} (długość: {len(lldp_output)})...")

    data_to_parse = lldp_output
    first_marker = RE_LLDP_HEADER_CANDIDATE.search(lldp_output)
    if first_marker:
        if "chassis id:" in first_marker.group(0).lower():
            data_to_parse = lldp_output[first_marker.start():]
        else:
            first_chassis_after_header = RE_LLDP_BLOCK_SPLIT.search(lldp_output, first_marker.end())
            if first_chassis_after_header:
                data_to_parse = lldp_output[first_chassis_after_header.start():]
            else:
                logger.warning(f"CLI-LLDP: Znaleziono nagłówek, ale brak bloków 'Chassis id:' w {local_hostname}.")
                return connections
    elif not data_to_parse.strip().lower().startswith('chassis id:'):
        logger.info(f"CLI-LLDP: Brak nagłówka LLDP i dane nie zaczynają się od 'Chassis id:' dla {local_hostname}.")

    blocks = RE_LLDP_BLOCK_SPLIT.split(data_to_parse)
    if not blocks or (len(blocks) == 1 and not (blocks[0].strip().lower().startswith('chassis id:') or first_marker)):
        logger.info(f"CLI-LLDP: Nie udało się podzielić danych LLDP na bloki 'Chassis id:' dla {local_hostname}.")
        if lldp_output.strip():
            logger.debug(f"CLI-LLDP: Niesparsowane dane LLDP dla {local_hostname}:\n{lldp_output[:500]}...")
        return connections

    parsed_count = 0
    for block_idx, block_content in enumerate(blocks):
        block_strip = block_content.strip()
        if not block_strip:
            if block_idx == 0 and len(blocks) > 1:
                logger.debug(f"CLI-LLDP: Pomijam pusty pierwszy blok (wynik splitu) dla {local_hostname}.")
            elif block_idx > 0:
                logger.warning(f"CLI-LLDP: Napotkano pusty blok LLDP (idx: {block_idx}) dla {local_hostname}.")
            continue

        if not block_strip.lower().startswith('chassis id:'):
            logger.debug(
                f"CLI-LLDP: Pomijam blok #{block_idx}, który nie zaczyna się od 'Chassis id:' dla {local_hostname}:\n{block_strip[:100]}...")
            continue

        local_if_match = RE_LLDP_LOCAL_PORT_ID.search(block_strip)
        remote_sys_match = RE_LLDP_SYS_NAME.search(block_strip)
        remote_port_id_match = RE_LLDP_REMOTE_PORT_ID.search(block_strip)
        if not (local_if_match and remote_sys_match and remote_port_id_match):
            logger.debug(f"CLI-LLDP: Pominięto blok {block_idx} - brak kluczowych danych w {local_hostname}.")
            continue

        local_if_raw = local_if_match.group(1).strip()
        if not local_if_raw or 'not advertised' in local_if_raw.lower(): continue
        local_if = _normalize_interface_name(local_if_raw)
        remote_sys = remote_sys_match.group(1).strip()
        remote_port_raw = remote_port_id_match.group(1).strip()
        remote_port_desc_match = RE_LLDP_REMOTE_PORT_DESC.search(block_strip)
        remote_port_desc_val = remote_port_desc_match.group(1).strip() if remote_port_desc_match else ""
        chosen_remote_port = remote_port_raw
        if (not chosen_remote_port or 'not advertised' in chosen_remote_port.lower() or ':' in chosen_remote_port) and \
                remote_port_desc_val and 'not advertised' not in remote_port_desc_val.lower():
            chosen_remote_port = remote_port_desc_val
        if not chosen_remote_port or 'not advertised' in chosen_remote_port.lower(): continue
        remote_if = _normalize_interface_name(chosen_remote_port)

        vlan_match = RE_LLDP_VLAN_ID.search(block_strip)
        vlan_id_str = vlan_match.group(1).strip() if vlan_match and vlan_match.group(1).strip() else None

        connections.append(
            {"local_host": local_hostname, "local_if": local_if, "neighbor_host": remote_sys, "neighbor_if": remote_if,
             "vlan": vlan_id_str, "via": "CLI-LLDP"})
        parsed_count += 1
    if parsed_count > 0:
        logger.info(f"✓ CLI-LLDP: Sparsowano {parsed_count} połączeń LLDP dla {local_hostname}.")
    elif lldp_output and lldp_output.strip():
        logger.info(
            f"ⓘ CLI-LLDP: Otrzymano dane LLDP ({len(lldp_output)} znaków), ale nie sparsowano użytecznych połączeń dla {local_hostname}.")
    return connections


def _parse_cdp_output(cdp_output: str, local_hostname: str) -> List[Dict[str, Any]]:
    connections: List[Dict[str, Any]] = []
    if not cdp_output or "Device ID" not in cdp_output: return connections
    logger.debug(f"CLI-CDP: Próba parsowania danych CDP dla {local_hostname}...")
    cdp_blocks = [block.strip() for block in RE_CDP_BLOCK_SPLIT.split(cdp_output) if block.strip()]
    parsed_count_cdp = 0
    for block_idx, block_content in enumerate(cdp_blocks):
        dev_id_match = RE_CDP_DEVICE_ID.search(block_content)
        local_if_match = RE_CDP_LOCAL_IF.search(block_content)
        remote_if_match = RE_CDP_REMOTE_IF.search(block_content)
        if dev_id_match and local_if_match and remote_if_match:
            local_if = _normalize_interface_name(local_if_match.group(1).strip().split(',')[0].strip())
            neighbor_host_val_raw = dev_id_match.group(1).strip()
            neighbor_host_val = neighbor_host_val_raw.split('.')[
                0] if '.' in neighbor_host_val_raw else neighbor_host_val_raw
            remote_if = _normalize_interface_name(remote_if_match.group(1).strip())
            if local_if and neighbor_host_val and remote_if:
                connections.append(
                    {"local_host": local_hostname, "local_if": local_if, "neighbor_host": neighbor_host_val,
                     "neighbor_if": remote_if, "vlan": None, "via": "CLI-CDP"})
                parsed_count_cdp += 1
    if parsed_count_cdp > 0:
        logger.info(f"✓ CLI-CDP: Sparsowano {parsed_count_cdp} połączeń CDP dla {local_hostname}.")
    elif cdp_output and cdp_output.strip():
        logger.info(f"ⓘ CLI-CDP: Otrzymano dane CDP, ale nie sparsowano połączeń dla {local_hostname}.")
    return connections


def cli_get_neighbors_enhanced(host: str, username: str, password: str) -> List[Dict[str, Any]]:
    if not host or not username or not password:
        logger.warning(f"CLI: Brak danych logowania dla '{host}'. Pomijam.")
        return []

    logger.info(f"⟶ CLI: Próba odkrycia sąsiadów dla {host}")
    device_params: Dict[str, Any] = {
        "device_type": "autodetect", "host": host, "username": username, "password": password,
        "global_delay_factor": 4,
        "session_log": f"{host}_netmiko_session.log", "session_log_file_mode": "append",
        "conn_timeout": 60, "auth_timeout": 75, "banner_timeout": 60,
    }
    all_cli_connections: List[Dict[str, Any]] = []
    net_connect: Optional[ConnectHandler] = None
    effective_device_type = "";
    base_prompt_log = "N/A"  # Inicjalizuj N/A

    try:
        logger.info(f"  CLI: Łączenie z {host} (parametry częściowo: gdf={device_params['global_delay_factor']})...")
        net_connect = ConnectHandler(**device_params)
        effective_device_type = net_connect.device_type  # Może nadal być 'autodetect', jeśli nic lepszego nie znaleziono

        # Próba odczytania base_prompt - może rzucić wyjątek, jeśli find_prompt() zawiedzie głębiej
        try:
            if net_connect.base_prompt:  # Sprawdź, czy base_prompt nie jest None lub pusty
                base_prompt_log = net_connect.base_prompt
        except Exception as e_base_prompt:
            logger.warning(f"  CLI: Wyjątek podczas próby odczytu net_connect.base_prompt dla {host}: {e_base_prompt}")
            base_prompt_log = "N/A (błąd odczytu)"

        logger.info(f"  CLI: Połączono z {host} (Wykryty typ Netmiko: '{effective_device_type}')")
        logger.info(f"  CLI: Netmiko base_prompt (automatycznie określony): '{base_prompt_log}'")

        lldp_command = "show lldp neighbors detail"
        cdp_command = "show cdp neighbors detail"
        lldp_expect_string_val: Optional[str] = None
        cdp_expect_string_val: Optional[str] = None
        run_cdp_check = True

        extreme_device_signatures = ["extreme_exos", "extreme_netiron", "extreme_ers", "extreme_vsp", "extreme_wing"]
        is_extreme_like_by_type = any(
            ext_type in effective_device_type.lower() for ext_type in extreme_device_signatures)

        is_extreme_like_by_prompt = False
        if base_prompt_log != "N/A" and base_prompt_log != "N/A (błąd odczytu)":  # Tylko jeśli base_prompt jest sensowny
            is_extreme_like_by_prompt = bool(RE_SLOT_SYS_PROMPT.fullmatch(base_prompt_log))

        is_extreme_like = is_extreme_like_by_type or is_extreme_like_by_prompt

        logger.info(
            f"  CLI: Urządzenie {host} jest traktowane jako Extreme-like: {is_extreme_like} "
            f"(wg typu: {is_extreme_like_by_type} ['{effective_device_type}'], "
            f"wg promptu: {is_extreme_like_by_prompt} ['{base_prompt_log}'])"
        )

        # --- Ustalanie komendy LLDP i expect_string ---
        if is_extreme_like:
            desc_reason = effective_device_type if is_extreme_like_by_type else "wykryto po prompcie pasującym do Extreme"
            logger.info(f"  CLI: Stosowanie ustawień specyficznych dla Extreme ({desc_reason}).")
            lldp_command = "show lldp neighbors detailed"  # Komenda dla Extreme
            lldp_expect_string_val = RE_SLOT_SYS_PROMPT.pattern
            run_cdp_check = False  # CDP nie jest typowe dla Extreme
        elif base_prompt_log == "N/A" or base_prompt_log == "N/A (błąd odczytu)":
            # Jeśli Netmiko nie mogło ustalić promptu, a urządzenie nie jest typem Extreme,
            # to jest to problematyczne urządzenie. Spróbujmy użyć RE_SLOT_SYS_PROMPT jako "educated guess",
            # bo takie prompty sprawiają problemy. Nadal użyjemy domyślnej komendy LLDP.
            logger.warning(
                f"  CLI: Netmiko nie ustaliło base_prompt dla {host} (typ: {effective_device_type}). "
                f"Próbuję użyć RE_SLOT_SYS_PROMPT ('{RE_SLOT_SYS_PROMPT.pattern}') jako expect_string dla LLDP (domyślna komenda)."
            )
            lldp_expect_string_val = RE_SLOT_SYS_PROMPT.pattern
            # Dla CDP, jeśli będzie uruchamiane, można by rozważyć podobne podejście
            if base_prompt_log == "N/A" or base_prompt_log == "N/A (błąd odczytu)":
                cdp_expect_string_val = RE_SLOT_SYS_PROMPT.pattern
        elif base_prompt_log != "N/A" and not RE_SIMPLE_PROMPT.fullmatch(base_prompt_log):
            # Nie Extreme, ale prompt jest złożony (nie ">" ani "#") i nie jest pusty. Użyj go.
            logger.info(
                f"  CLI: Używam base_prompt ('{base_prompt_log}') jako expect_string dla {host} (typ: {effective_device_type}).")
            lldp_expect_string_val = base_prompt_log
            cdp_expect_string_val = base_prompt_log
        elif "junos" in effective_device_type:
            lldp_command = "show lldp neighbors interface all detail"
        # Domyślnie (np. dla Cisco IOS), lldp_command = "show lldp neighbors detail" i lldp_expect_string_val = None

        # Próba wyłączenia paginacji - ogólna, jeśli net_connect to wspiera
        if hasattr(net_connect, 'disable_paging') and callable(net_connect.disable_paging):
            try:
                logger.debug(f"  CLI: Próba wywołania net_connect.disable_paging() dla {host}")
                net_connect.disable_paging()  # Uniwersalna metoda Netmiko
            except Exception as e_disable_paging:
                logger.warning(
                    f"  CLI: Wyjątek podczas próby net_connect.disable_paging() dla {host}: {e_disable_paging}")
        else:
            logger.debug(
                f"  CLI: Obiekt net_connect nie ma metody disable_paging lub nie jest wywoływalna. Poleganie na domyślnej obsłudze paginacji dla typu '{effective_device_type}'.")

        # --- Wykonywanie komendy LLDP ---
        send_command_args_lldp = {"read_timeout": 180}
        if lldp_expect_string_val:
            send_command_args_lldp["expect_string"] = lldp_expect_string_val

        logger.info(f"  CLI: Wykonywanie '{lldp_command}' dla LLDP na {host} (parametry: {send_command_args_lldp})...")
        try:
            lldp_output_raw = net_connect.send_command(lldp_command, **send_command_args_lldp)

            if lldp_output_raw and isinstance(lldp_output_raw, str):
                logger.debug(f"  CLI-LLDP: Otrzymano surowe wyjście LLDP dla {host} (długość: {len(lldp_output_raw)}).")
                if not lldp_output_raw.strip():
                    logger.info(f"  CLI-LLDP: Otrzymano puste wyjście LLDP dla {host} (polecenie: '{lldp_command}').")
                else:
                    lldp_conns = _parse_lldp_output(lldp_output_raw, host)
                    all_cli_connections.extend(lldp_conns)
                    if not lldp_conns: logger.info(
                        f"  CLI-LLDP: Otrzymano wyjście LLDP, ale nie sparsowano połączeń dla {host}.")
            elif not lldp_output_raw:
                logger.info(f"  CLI-LLDP: Brak danych wyjściowych LLDP (None) dla {host}.")
            else:
                logger.warning(
                    f"  CLI-LLDP: Otrzymano nieoczekiwany typ danych LLDP ({type(lldp_output_raw)}) dla {host}.")
        except Exception as e_lldp_cmd:
            logger.warning(f"  CLI-LLDP: Błąd komendy LLDP dla {host}: {e_lldp_cmd}", exc_info=False)
            logger.debug(f"  CLI-LLDP: Pełny traceback błędu LLDP dla {host}:", exc_info=True)

        # --- Wykonywanie komendy CDP (jeśli potrzeba) ---
        if not all_cli_connections and run_cdp_check:
            send_command_args_cdp = {"read_timeout": 180}
            if cdp_expect_string_val:
                send_command_args_cdp["expect_string"] = cdp_expect_string_val
            # Jeśli is_extreme_like było True, run_cdp_check jest False, więc tu nie wejdziemy.
            # Jeśli base_prompt był N/A, cdp_expect_string_val jest ustawiony na RE_SLOT_SYS_PROMPT.pattern

            logger.info(f"  CLI: Wykonywanie '{cdp_command}' dla CDP na {host} (parametry: {send_command_args_cdp})...")
            try:
                cdp_output_raw = net_connect.send_command(cdp_command, **send_command_args_cdp)
                if cdp_output_raw and isinstance(cdp_output_raw, str):
                    logger.debug(
                        f"  CLI-CDP: Otrzymano surowe wyjście CDP dla {host} (długość: {len(cdp_output_raw)}).")
                    if not cdp_output_raw.strip():
                        logger.info(f"  CLI-CDP: Otrzymano puste wyjście CDP dla {host} (polecenie: '{cdp_command}').")
                    else:
                        cdp_conns = _parse_cdp_output(cdp_output_raw, host)
                        all_cli_connections.extend(cdp_conns)
                        if not cdp_conns: logger.info(
                            f"  CLI-CDP: Otrzymano wyjście CDP, ale nie sparsowano połączeń dla {host}.")
                elif not cdp_output_raw:
                    logger.info(f"  CLI-CDP: Brak danych wyjściowych CDP (None) dla {host}.")
                else:
                    logger.warning(
                        f"  CLI-CDP: Otrzymano nieoczekiwany typ danych CDP ({type(cdp_output_raw)}) dla {host}.")
            except Exception as e_cdp_cmd:
                logger.warning(f"  CLI-CDP: Błąd komendy CDP dla {host}: {e_cdp_cmd}", exc_info=False)
                logger.debug(f"  CLI-CDP: Pełny traceback błędu CDP dla {host}:", exc_info=True)
        elif not run_cdp_check:
            logger.info(f"  CLI: Pominięto sprawdzanie CDP dla {host}.")

    except NetmikoAuthenticationException as e_auth:
        logger.error(f"⚠ CLI: Błąd autoryzacji SSH do {host}: {e_auth}")
    except NetmikoTimeoutException as e_timeout:
        logger.error(f"⚠ CLI: Timeout połączenia SSH z {host}: {e_timeout}")
    except Exception as e_conn:
        logger.error(f"⚠ CLI: Ogólny błąd SSH/Netmiko dla {host}: {e_conn}", exc_info=True)
    finally:
        if net_connect and net_connect.is_alive():
            try:
                net_connect.disconnect(); logger.info(f"  CLI: Rozłączono z {host}")
            except Exception as e_disc:
                logger.error(f"  CLI: Błąd rozłączenia z {host}: {e_disc}", exc_info=True)
    if not all_cli_connections:
        logger.info(f"⟶ CLI: Nie znaleziono sąsiadów przez CLI dla {host}.")
    else:
        logger.info(f"✓ CLI: Znaleziono {len(all_cli_connections)} sąsiadów dla {host}.")
    return all_cli_connections