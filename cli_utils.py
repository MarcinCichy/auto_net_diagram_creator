# cli_utils.py
import re
import logging
from typing import List, Dict, Any, Optional
from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException

logger = logging.getLogger(__name__)

# --- Prekompilowane wyrażenia regularne (POPRAWIONE) ---
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
                logger.warning(
                    f"CLI-LLDP: Znaleziono nagłówek, ale brak bloków 'Chassis id:' w {local_hostname}."); return connections
    elif not data_to_parse.strip().lower().startswith('chassis id:'):
        logger.info(f"CLI-LLDP: Brak nagłówka LLDP i dane nie zaczynają się od 'Chassis id:' dla {local_hostname}.")

    blocks = RE_LLDP_BLOCK_SPLIT.split(data_to_parse)
    if not blocks or (len(blocks) == 1 and not (blocks[0].strip().lower().startswith('chassis id:') or first_marker)):
        logger.info(f"CLI-LLDP: Nie udało się podzielić danych LLDP na bloki 'Chassis id:' dla {local_hostname}.")
        if lldp_output.strip(): logger.debug(
            f"CLI-LLDP: Niesparsowane dane LLDP dla {local_hostname}:\n{lldp_output[:500]}...")
        return connections

    parsed_count = 0
    for block_idx, block_content in enumerate(blocks):
        block_strip = block_content.strip()
        if not block_strip:
            if block_idx == 0 and len(blocks) > 1:
                logger.debug(f"CLI-LLDP: Pomijam pusty pierwszy blok dla {local_hostname}.")
            elif block_idx > 0:
                logger.warning(f"CLI-LLDP: Pusty blok LLDP (idx: {block_idx}) dla {local_hostname}.")
            continue
        if not block_strip.lower().startswith('chassis id:'): logger.debug(
            f"CLI-LLDP: Pomijam blok #{block_idx} (nie 'Chassis id:') dla {local_hostname}:\n{block_strip[:100]}..."); continue
        local_if_match, remote_sys_match, remote_port_id_match = RE_LLDP_LOCAL_PORT_ID.search(
            block_strip), RE_LLDP_SYS_NAME.search(block_strip), RE_LLDP_REMOTE_PORT_ID.search(block_strip)
        if not (local_if_match and remote_sys_match and remote_port_id_match): logger.debug(
            f"CLI-LLDP: Pominięto blok {block_idx} - brak kluczowych danych w {local_hostname}."); continue
        local_if_raw = local_if_match.group(1).strip()
        if not local_if_raw or 'not advertised' in local_if_raw.lower(): continue
        local_if, remote_sys, remote_port_raw = _normalize_interface_name(local_if_raw), remote_sys_match.group(
            1).strip(), remote_port_id_match.group(1).strip()
        remote_port_desc_match = RE_LLDP_REMOTE_PORT_DESC.search(block_strip)
        remote_port_desc_val = remote_port_desc_match.group(1).strip() if remote_port_desc_match else ""
        chosen_remote_port = remote_port_raw
        if (not chosen_remote_port or 'not advertised' in chosen_remote_port.lower() or ':' in chosen_remote_port) and \
                remote_port_desc_val and 'not advertised' not in remote_port_desc_val.lower(): chosen_remote_port = remote_port_desc_val
        if not chosen_remote_port or 'not advertised' in chosen_remote_port.lower(): continue
        remote_if = _normalize_interface_name(chosen_remote_port)
        vlan_match = RE_LLDP_VLAN_ID.search(block_strip)
        vlan_id_str = vlan_match.group(1).strip() if vlan_match and vlan_match.group(1).strip() else None
        connections.append(
            {"local_host": local_hostname, "local_if": local_if, "neighbor_host": remote_sys, "neighbor_if": remote_if,
             "vlan": vlan_id_str, "via": "CLI-LLDP"});
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
        dev_id_match, local_if_match, remote_if_match = RE_CDP_DEVICE_ID.search(block_content), RE_CDP_LOCAL_IF.search(
            block_content), RE_CDP_REMOTE_IF.search(block_content)
        if dev_id_match and local_if_match and remote_if_match:
            local_if = _normalize_interface_name(local_if_match.group(1).strip().split(',')[0].strip())
            neighbor_host_val_raw = dev_id_match.group(1).strip();
            neighbor_host_val = neighbor_host_val_raw.split('.')[
                0] if '.' in neighbor_host_val_raw else neighbor_host_val_raw
            remote_if = _normalize_interface_name(remote_if_match.group(1).strip())
            if local_if and neighbor_host_val and remote_if: connections.append(
                {"local_host": local_hostname, "local_if": local_if, "neighbor_host": neighbor_host_val,
                 "neighbor_if": remote_if, "vlan": None, "via": "CLI-CDP"}); parsed_count_cdp += 1
    if parsed_count_cdp > 0:
        logger.info(f"✓ CLI-CDP: Sparsowano {parsed_count_cdp} połączeń CDP dla {local_hostname}.")
    elif cdp_output and cdp_output.strip():
        logger.info(f"ⓘ CLI-CDP: Otrzymano dane CDP, ale nie sparsowano połączeń dla {local_hostname}.")
    return connections


def cli_get_neighbors_enhanced(host: str, username: str, password: str) -> List[Dict[str, Any]]:
    if not host or not username or not password: logger.warning(
        f"CLI: Brak danych logowania dla '{host}'. Pomijam."); return []
    logger.info(f"⟶ CLI: Próba odkrycia sąsiadów dla {host}")
    device_params: Dict[str, Any] = {"device_type": "autodetect", "host": host, "username": username,
                                     "password": password, "global_delay_factor": 4,
                                     "session_log": f"{host}_netmiko_session.log", "session_log_file_mode": "append",
                                     "conn_timeout": 60, "auth_timeout": 75, "banner_timeout": 60}
    all_cli_connections: List[Dict[str, Any]] = [];
    net_connect: Optional[ConnectHandler] = None
    effective_device_type = "";
    base_prompt_log = "N/A (nie odczytano)"
    try:
        logger.info(f"  CLI: Łączenie z {host} (gdf={device_params['global_delay_factor']})...")
        net_connect = ConnectHandler(**device_params)
        effective_device_type = net_connect.device_type
        try:
            if net_connect.base_prompt: base_prompt_log = net_connect.base_prompt
        except Exception as e_bp:
            logger.warning(
                f"  CLI: Wyjątek przy odczycie base_prompt dla {host}: {e_bp}"); base_prompt_log = "N/A (błąd)"
        logger.info(f"  CLI: Połączono z {host} (Typ Netmiko: '{effective_device_type}')")
        logger.info(f"  CLI: Netmiko base_prompt: '{base_prompt_log}'")

        lldp_cmd, cdp_cmd = "show lldp neighbors detail", "show cdp neighbors detail"
        lldp_exp_str: Optional[str] = None;
        cdp_exp_str: Optional[str] = None;
        run_cdp = True

        extreme_sigs = ["extreme_exos", "extreme_netiron", "extreme_ers", "extreme_vsp", "extreme_wing"]
        is_extreme_type = any(sig in effective_device_type.lower() for sig in extreme_sigs)
        is_extreme_prompt = base_prompt_log not in ["N/A", "N/A (błąd)"] and bool(
            RE_SLOT_SYS_PROMPT.fullmatch(base_prompt_log))
        is_extreme_like = is_extreme_type or is_extreme_prompt

        logger.info(
            f"  CLI: {host} traktowane jako Extreme-like: {is_extreme_like} (typ: {is_extreme_type} ['{effective_device_type}'], prompt: {is_extreme_prompt} ['{base_prompt_log}'])")

        if is_extreme_like:
            desc_reason = effective_device_type if is_extreme_type else "wykryto po prompcie pasującym do Extreme"
            logger.info(f"  CLI: Ustawienia Extreme dla {host} (powód: {desc_reason}).")
            lldp_cmd, lldp_exp_str, run_cdp = "show lldp neighbors detailed", RE_SLOT_SYS_PROMPT.pattern, False
            logger.info(f"  CLI: LLDP cmd: '{lldp_cmd}', expect: '{lldp_exp_str}', CDP pominięte.")
            if "extreme_exos" in effective_device_type:
                try:
                    logger.debug(f"  CLI: Próba wysłania 'disable clipaging' dla {host} (EXOS).")
                    # Użycie send_command_timing jest bezpieczniejsze dla komend, które nie zwracają wiele wyjścia
                    # lub gdy chcemy uniknąć problemów z detekcją promptu po tej jednej komendzie.
                    net_connect.send_command_timing("disable clipaging", read_timeout=15)
                except Exception as e_paging_exos:
                    logger.warning(f"  CLI: Wyjątek przy 'disable clipaging' dla {host}: {e_paging_exos}")
            elif hasattr(net_connect, 'disable_paging') and callable(net_connect.disable_paging):
                try:
                    logger.debug(f"  CLI: Próba net_connect.disable_paging() dla {host}."); net_connect.disable_paging()
                except Exception as e_dp:
                    logger.warning(f"  CLI: Wyjątek przy disable_paging() dla {host}: {e_dp}")
        elif "junos" in effective_device_type:
            lldp_cmd = "show lldp neighbors interface all detail"
        elif "cisco_nxos" in effective_device_type:
            logger.info(f"  CLI: Ustawienia Cisco NX-OS dla {host}.");
            lldp_cmd = "show lldp neighbors"
        elif base_prompt_log not in ["N/A", "N/A (błąd)"] and not RE_SIMPLE_PROMPT.fullmatch(base_prompt_log):
            logger.info(
                f"  CLI: Używam base_prompt ('{base_prompt_log}') jako expect_string dla {host} (typ: {effective_device_type}).")
            lldp_exp_str = base_prompt_log;
            cdp_exp_str = base_prompt_log

        if base_prompt_log in ["N/A", "N/A (błąd)"] and not is_extreme_like:
            logger.warning(
                f"  CLI: base_prompt nieustalony i nie Extreme dla {host}. Próbuję z RE_SLOT_SYS_PROMPT jako fallback.")
            lldp_exp_str = RE_SLOT_SYS_PROMPT.pattern
            if run_cdp: cdp_exp_str = RE_SLOT_SYS_PROMPT.pattern

        lldp_params = {"read_timeout": 180};
        if lldp_exp_str: lldp_params["expect_string"] = lldp_exp_str
        logger.info(f"  CLI: LLDP dla {host}: cmd='{lldp_cmd}', params={lldp_params}")
        try:
            lldp_raw = net_connect.send_command(lldp_cmd, **lldp_params)
            if lldp_raw and isinstance(lldp_raw, str):
                logger.debug(f"  CLI-LLDP: Otrzymano surowe wyjście LLDP dla {host} (długość: {len(lldp_raw)}).")
                if not lldp_raw.strip():
                    logger.info(f"  CLI-LLDP: Puste wyjście LLDP dla {host}.")
                else:
                    conns = _parse_lldp_output(lldp_raw, host);
                    all_cli_connections.extend(conns)
                    if not conns: logger.info(f"  CLI-LLDP: Otrzymano wyjście, ale nie sparsowano połączeń.")
            elif not lldp_raw:
                logger.info(f"  CLI-LLDP: Brak danych LLDP (None) dla {host}.")
            else:
                logger.warning(f"  CLI-LLDP: Nieoczekiwany typ danych LLDP ({type(lldp_raw)}) dla {host}.")
        except Exception as e:
            logger.warning(f"  CLI-LLDP: Błąd komendy LLDP dla {host}: {e}", exc_info=False); logger.debug(
                "Traceback LLDP:", exc_info=True)

        if not all_cli_connections and run_cdp:
            cdp_params = {"read_timeout": 180};
            if cdp_exp_str:
                cdp_params["expect_string"] = cdp_exp_str
            elif is_extreme_like:
                cdp_params[
                    "expect_string"] = RE_SLOT_SYS_PROMPT.pattern  # Powinno być run_cdp=False, ale dla bezpieczeństwa

            logger.info(f"  CLI: CDP dla {host}: cmd='{cdp_cmd}', params={cdp_params}")
            try:
                cdp_raw = net_connect.send_command(cdp_cmd, **cdp_params)
                if cdp_raw and isinstance(cdp_raw, str):
                    logger.debug(f"  CLI-CDP: Otrzymano surowe wyjście CDP dla {host} (długość: {len(cdp_raw)}).")
                    if not cdp_raw.strip():
                        logger.info(f"  CLI-CDP: Puste wyjście CDP dla {host}.")
                    else:
                        conns = _parse_cdp_output(cdp_raw, host);
                        all_cli_connections.extend(conns)
                        if not conns: logger.info(f"  CLI-CDP: Otrzymano wyjście, ale nie sparsowano połączeń.")
                elif not cdp_raw:
                    logger.info(f"  CLI-CDP: Brak danych CDP (None) dla {host}.")
                else:
                    logger.warning(f"  CLI-CDP: Nieoczekiwany typ danych CDP ({type(cdp_raw)}) dla {host}.")
            except Exception as e:
                logger.warning(f"  CLI-CDP: Błąd komendy CDP dla {host}: {e}", exc_info=False); logger.debug(
                    "Traceback CDP:", exc_info=True)
        elif not run_cdp:
            logger.info(f"  CLI: Pominięto CDP dla {host}.")
    except NetmikoAuthenticationException as e:
        logger.error(f"⚠ CLI Auth Error: {host}: {e}")
    except NetmikoTimeoutException as e:
        logger.error(f"⚠ CLI Timeout Error: {host}: {e}")
    except Exception as e:
        logger.error(f"⚠ CLI General Error: {host}: {e}", exc_info=True)
    finally:
        if net_connect and net_connect.is_alive():
            try:
                net_connect.disconnect(); logger.info(f"  CLI: Rozłączono z {host}")
            except Exception as e:
                logger.error(f"  CLI Disconnect Error: {host}: {e}", exc_info=True)
    if not all_cli_connections:
        logger.info(f"⟶ CLI: Brak sąsiadów CLI dla {host}.")
    else:
        logger.info(f"✓ CLI: Znaleziono {len(all_cli_connections)} sąsiadów dla {host}.")
    return all_cli_connections