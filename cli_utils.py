# cli_utils.py
import re
import logging
from typing import List, Dict, Any, Optional, Pattern

from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException

logger = logging.getLogger(__name__)


class CliConfig:
    """Przechowuje konfigurację dla operacji CLI, w tym prekompilowane regexy."""

    def __init__(self, config: Dict[str, Any]):
        self.global_delay_factor = config.get('cli_global_delay_factor', 5.0)
        self.session_log_file_mode = config.get('cli_session_log_file_mode', 'append')
        self.conn_timeout = config.get('cli_conn_timeout', 75)
        self.auth_timeout = config.get('cli_auth_timeout', 90)
        self.banner_timeout = config.get('cli_banner_timeout', 75)
        self.read_timeout_general = config.get('cli_read_timeout_general', 60)
        self.read_timeout_lldp_cdp = config.get('cli_read_timeout_lldp_cdp', 180)
        self.default_expect_string_pattern = config.get('cli_default_expect_string_pattern', r"[a-zA-Z0-9\S\.\-]*[#>]")
        self.netmiko_session_log_template = config.get('cli_netmiko_session_log_template', "{host}_netmiko_session.log")

        self.RE_LLDP_HEADER_CANDIDATE: Pattern[str] = re.compile(
            config.get('lldp_regex_header_candidate', r'(Device ID\s+Local Intf\s+Hold-time|Chassis id:)'),
            re.IGNORECASE)
        self.RE_LLDP_BLOCK_SPLIT: Pattern[str] = re.compile(
            config.get('lldp_regex_block_split', r'\n\s*(?=Chassis id:)'), flags=re.IGNORECASE)
        self.RE_LLDP_LOCAL_PORT_ID: Pattern[str] = re.compile(
            config.get('lldp_regex_local_port_id', r'^Local Port id:\s*(.+?)\s*$'), re.MULTILINE | re.IGNORECASE)
        self.RE_LLDP_SYS_NAME: Pattern[str] = re.compile(
            config.get('lldp_regex_sys_name', r'^System Name:\s*(.+?)\s*$'), re.MULTILINE | re.IGNORECASE)
        self.RE_LLDP_REMOTE_PORT_ID: Pattern[str] = re.compile(
            config.get('lldp_regex_remote_port_id', r'^Port id:\s*(.+?)\s*$'), re.MULTILINE | re.IGNORECASE)
        self.RE_LLDP_REMOTE_PORT_DESC: Pattern[str] = re.compile(
            config.get('lldp_regex_remote_port_desc', r'^Port Description:\s*(.+?)\s*$'), re.MULTILINE | re.IGNORECASE)
        self.RE_LLDP_VLAN_ID: Pattern[str] = re.compile(
            config.get('lldp_regex_vlan_id', r'^(?:Port and )?Vlan ID:\s*([0-9]+)\s*$'), re.MULTILINE | re.IGNORECASE)

        self.RE_CDP_BLOCK_SPLIT: Pattern[str] = re.compile(config.get('cdp_regex_block_split', r'-{10,}\s*$'),
                                                           flags=re.MULTILINE)
        self.RE_CDP_DEVICE_ID: Pattern[str] = re.compile(config.get('cdp_regex_device_id', r'Device ID:\s*(\S+)'),
                                                         re.IGNORECASE)
        self.RE_CDP_LOCAL_IF: Pattern[str] = re.compile(
            config.get('cdp_regex_local_if', r'Interface:\s*([^,]+(?:,\s*port\s+\S+)?)'), re.IGNORECASE)
        self.RE_CDP_REMOTE_IF: Pattern[str] = re.compile(
            config.get('cdp_regex_remote_if', r'(?:Port ID|Outgoing Port):\s*(\S+)'), re.IGNORECASE)

        self.RE_SLOT_SYS_PROMPT: Pattern[str] = re.compile(
            config.get('prompt_regex_slot_sys', r'(?:\*\s*)?Slot-\d+\s+[\w.-]+\s*#\s*$'))
        self.RE_SIMPLE_PROMPT: Pattern[str] = re.compile(
            config.get('prompt_regex_simple', r"^[a-zA-Z0-9][\w.-]*[>#]\s*$"))
        self.RE_NXOS_PROMPT: Pattern[str] = re.compile(config.get('prompt_regex_nxos', r"^[a-zA-Z0-9][\w.-]*#\s*$"))
        self.RE_IOS_PROMPT: Pattern[str] = re.compile(config.get('prompt_regex_ios', r"^[a-zA-Z0-9][\w.-]*[>#]\s*$"))

        self.interface_name_replacements: Dict[str, str] = config.get('interface_name_replacements',
                                                                      {"GigabitEthernet": "Gi"})
        if not isinstance(self.interface_name_replacements, dict):  # Jeśli z config.ini przyszło jako string
            self.interface_name_replacements = {k.strip(): v.strip() for k, v in (item.split('=') for item in
                                                                                  self.interface_name_replacements.split(
                                                                                      ',')) if '=' in item}


def _normalize_interface_name(if_name: str, replacements: Dict[str, str]) -> str:
    if_name = if_name.strip()
    # Sortuj wg długości klucza malejąco, aby np. "TenGigabitEthernet" było sprawdzane przed "GigabitEthernet"
    for long, short in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if if_name.lower().startswith(long.lower()):
            return short + if_name[len(long):]
    return if_name


def _parse_lldp_output(lldp_output: str, local_hostname: str, cli_cfg: CliConfig) -> List[Dict[str, Any]]:
    connections: List[Dict[str, Any]] = []
    if not lldp_output: return connections
    logger.debug(f"CLI-LLDP: Próba parsowania danych LLDP dla {local_hostname} (długość: {len(lldp_output)})...")
    data_to_parse = lldp_output
    first_marker = cli_cfg.RE_LLDP_HEADER_CANDIDATE.search(lldp_output)
    if first_marker:
        if "chassis id:" in first_marker.group(0).lower():
            data_to_parse = lldp_output[first_marker.start():]
        else:
            first_chassis_after_header = cli_cfg.RE_LLDP_BLOCK_SPLIT.search(lldp_output, first_marker.end())
            if first_chassis_after_header:
                data_to_parse = lldp_output[first_chassis_after_header.start():]
            else:
                logger.warning(
                    f"CLI-LLDP: Znaleziono nagłówek, ale brak bloków 'Chassis id:' w {local_hostname}.");
                return connections
    elif not data_to_parse.strip().lower().startswith('chassis id:'):
        logger.info(f"CLI-LLDP: Brak nagłówka LLDP i dane nie zaczynają się od 'Chassis id:' dla {local_hostname}.")

    blocks = cli_cfg.RE_LLDP_BLOCK_SPLIT.split(data_to_parse)
    if not blocks or (len(blocks) == 1 and not (blocks[0].strip().lower().startswith('chassis id:') or first_marker)):
        logger.info(f"CLI-LLDP: Nie udało się podzielić danych LLDP na bloki 'Chassis id:' dla {local_hostname}.")
        if lldp_output.strip(): logger.debug(
            f"CLI-LLDP: Niesparsowane dane LLDP dla {local_hostname}:\n{lldp_output[:500]}...")
        return connections

    parsed_count = 0
    for block_idx, block_content in enumerate(blocks):
        block_strip = block_content.strip()
        if not block_strip or (block_idx == 0 and not block_strip.lower().startswith('chassis id:')):
            if block_strip and block_idx == 0: logger.debug(
                f"CLI-LLDP: Pomijam pierwszy blok (nie 'Chassis id:') dla {local_hostname}.")
            continue
        if block_idx > 0 and not block_strip.lower().startswith('chassis id:'): logger.debug(
            f"CLI-LLDP: Pomijam blok #{block_idx} (nie 'Chassis id:') dla {local_hostname}:\n{block_strip[:100]}..."); continue

        local_if_match = cli_cfg.RE_LLDP_LOCAL_PORT_ID.search(block_strip)
        remote_sys_match = cli_cfg.RE_LLDP_SYS_NAME.search(block_strip)
        remote_port_id_match = cli_cfg.RE_LLDP_REMOTE_PORT_ID.search(block_strip)

        if not (local_if_match and remote_sys_match and remote_port_id_match):
            logger.debug(f"CLI-LLDP: Pominięto blok {block_idx} - brak kluczowych danych w {local_hostname}.")
            continue

        local_if_raw = local_if_match.group(1).strip()
        if not local_if_raw or 'not advertised' in local_if_raw.lower(): continue

        local_if = _normalize_interface_name(local_if_raw, cli_cfg.interface_name_replacements)
        remote_sys = remote_sys_match.group(1).strip()
        remote_port_raw = remote_port_id_match.group(1).strip()

        remote_port_desc_match = cli_cfg.RE_LLDP_REMOTE_PORT_DESC.search(block_strip)
        remote_port_desc_val = remote_port_desc_match.group(1).strip() if remote_port_desc_match else ""

        chosen_remote_port = remote_port_raw
        if (not chosen_remote_port or 'not advertised' in chosen_remote_port.lower() or ':' in chosen_remote_port) and \
                remote_port_desc_val and 'not advertised' not in remote_port_desc_val.lower():
            chosen_remote_port = remote_port_desc_val

        if not chosen_remote_port or 'not advertised' in chosen_remote_port.lower(): continue

        remote_if = _normalize_interface_name(chosen_remote_port, cli_cfg.interface_name_replacements)

        vlan_match = cli_cfg.RE_LLDP_VLAN_ID.search(block_strip)
        vlan_id_str = vlan_match.group(1).strip() if vlan_match and vlan_match.group(1).strip() else None

        connections.append({
            "local_host": local_hostname, "local_if": local_if,
            "neighbor_host": remote_sys, "neighbor_if": remote_if,
            "vlan": vlan_id_str, "via": "CLI-LLDP"
        })
        parsed_count += 1

    if parsed_count > 0:
        logger.info(f"✓ CLI-LLDP: Sparsowano {parsed_count} połączeń LLDP dla {local_hostname}.")
    elif lldp_output and lldp_output.strip():
        logger.info(
            f"ⓘ CLI-LLDP: Otrzymano dane LLDP ({len(lldp_output)} znaków), ale nie sparsowano użytecznych połączeń dla {local_hostname}.")
    return connections


def _parse_cdp_output(cdp_output: str, local_hostname: str, cli_cfg: CliConfig) -> List[Dict[str, Any]]:
    connections: List[Dict[str, Any]] = []
    if not cdp_output or "Device ID" not in cdp_output: return connections
    logger.debug(f"CLI-CDP: Próba parsowania danych CDP dla {local_hostname}...")

    cdp_blocks = [block.strip() for block in cli_cfg.RE_CDP_BLOCK_SPLIT.split(cdp_output) if block.strip()]
    parsed_count_cdp = 0
    for block_idx, block_content in enumerate(cdp_blocks):
        dev_id_match = cli_cfg.RE_CDP_DEVICE_ID.search(block_content)
        local_if_match = cli_cfg.RE_CDP_LOCAL_IF.search(block_content)
        remote_if_match = cli_cfg.RE_CDP_REMOTE_IF.search(block_content)

        if dev_id_match and local_if_match and remote_if_match:
            local_if = _normalize_interface_name(local_if_match.group(1).strip().split(',')[0].strip(),
                                                 cli_cfg.interface_name_replacements)
            neighbor_host_val_raw = dev_id_match.group(1).strip()
            neighbor_host_val = neighbor_host_val_raw.split('.')[
                0] if '.' in neighbor_host_val_raw else neighbor_host_val_raw
            remote_if = _normalize_interface_name(remote_if_match.group(1).strip(), cli_cfg.interface_name_replacements)

            if local_if and neighbor_host_val and remote_if:
                connections.append({
                    "local_host": local_hostname, "local_if": local_if,
                    "neighbor_host": neighbor_host_val, "neighbor_if": remote_if,
                    "vlan": None, "via": "CLI-CDP"
                })
                parsed_count_cdp += 1

    if parsed_count_cdp > 0:
        logger.info(f"✓ CLI-CDP: Sparsowano {parsed_count_cdp} połączeń CDP dla {local_hostname}.")
    elif cdp_output and cdp_output.strip() and "cdp not enabled" not in cdp_output.lower():
        logger.info(f"ⓘ CLI-CDP: Otrzymano dane CDP, ale nie sparsowano połączeń dla {local_hostname}.")
    return connections


def cli_get_neighbors_enhanced(host: str, username: str, password: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    cli_cfg = CliConfig(config)  # Inicjalizacja obiektu konfiguracji CLI

    if not host or not username or not password:
        logger.warning(f"CLI: Brak danych logowania dla '{host}'. Pomijam.")
        return []

    logger.info(f"⟶ CLI: Próba odkrycia sąsiadów dla {host}")

    session_log_path = None
    if cli_cfg.netmiko_session_log_template:  # Jeśli szablon jest zdefiniowany
        session_log_path = cli_cfg.netmiko_session_log_template.format(
            host=host.replace('.', '_'))  # Prosta zamiana dla nazwy pliku

    device_params: Dict[str, Any] = {
        "device_type": "autodetect",
        "host": host,
        "username": username,
        "password": password,
        "global_delay_factor": cli_cfg.global_delay_factor,
        "session_log_file_mode": cli_cfg.session_log_file_mode,
        "conn_timeout": cli_cfg.conn_timeout,
        "auth_timeout": cli_cfg.auth_timeout,
        "banner_timeout": cli_cfg.banner_timeout
    }
    if session_log_path:
        device_params["session_log"] = session_log_path

    all_cli_connections: List[Dict[str, Any]] = []
    net_connect: Optional[ConnectHandler] = None
    effective_device_type = ""
    base_prompt_log = "N/A (nie odczytano)"

    try:
        logger.info(f"  CLI: Łączenie z {host} (gdf={device_params['global_delay_factor']})...")
        net_connect = ConnectHandler(**device_params)
        effective_device_type = net_connect.device_type
        try:
            if net_connect.base_prompt:
                base_prompt_log = net_connect.base_prompt.strip()
        except Exception as e_bp:
            logger.warning(f"  CLI: Wyjątek przy odczycie base_prompt dla {host}: {e_bp}")
            base_prompt_log = "N/A (błąd)"

        logger.info(f"  CLI: Połączono z {host} (Typ Netmiko: '{effective_device_type}')")
        logger.info(f"  CLI: Netmiko base_prompt: '{base_prompt_log}'")

        system_info_str = ""
        show_ver_expect_str: Optional[str] = None
        if base_prompt_log not in ["N/A", "N/A (nie odczytano)", "N/A (błąd)"]:
            if not cli_cfg.RE_SIMPLE_PROMPT.fullmatch(base_prompt_log):
                show_ver_expect_str = base_prompt_log
        if not show_ver_expect_str:
            show_ver_expect_str = cli_cfg.default_expect_string_pattern  # Ogólny fallback z config.ini

        try:
            logger.debug(f"  CLI: Próba pobrania 'show version' z {host} (expect_string: '{show_ver_expect_str}')...")
            show_version_params: Dict[str, Any] = {"read_timeout": cli_cfg.read_timeout_general}
            if show_ver_expect_str:
                show_version_params["expect_string"] = show_ver_expect_str

            show_version_output = net_connect.send_command("show version", **show_version_params)
            if show_version_output and isinstance(show_version_output, str):
                system_info_str = show_version_output.lower()
                logger.info(f"  CLI: Otrzymano 'show version' (fragment): {system_info_str[:250]}")
            else:
                logger.warning(f"  CLI: Nie udało się uzyskać wyjścia 'show version' dla {host}.")
        except Exception as e_ver:
            logger.warning(
                f"  CLI: Błąd podczas 'show version' na {host} (użyty expect_string: '{show_ver_expect_str}'): {e_ver}")

        lldp_cmd = "show lldp neighbors detail"
        cdp_cmd = "show cdp neighbors detail"
        lldp_exp_str: Optional[str] = None
        cdp_exp_str: Optional[str] = None
        run_cdp = True
        platform_handler_applied = False

        is_extreme_prompt_match = base_prompt_log not in ["N/A", "N/A (nie odczytano)", "N/A (błąd)"] and bool(
            cli_cfg.RE_SLOT_SYS_PROMPT.fullmatch(base_prompt_log))
        is_extreme_type_sig = any(sig in effective_device_type.lower() for sig in
                                  ["extreme_exos", "extreme_vsp", "extreme_netiron", "extreme_ers", "extreme_wing"])
        is_extreme_sysinfo = "extreme" in system_info_str or "extremexos" in system_info_str
        is_extreme_like = is_extreme_prompt_match or is_extreme_type_sig or is_extreme_sysinfo

        logger.info(
            f"  CLI: {host} traktowane jako Extreme-like: {is_extreme_like} (prompt: {is_extreme_prompt_match}, typ: {is_extreme_type_sig}, sysinfo: {is_extreme_sysinfo})")

        if is_extreme_like:
            platform_reason = "wykryto jako Extreme (prompt/typ/sysinfo)"
            logger.info(f"  CLI: Ustawienia Extreme dla {host} (powód: {platform_reason}).")
            lldp_cmd = "show lldp neighbors detailed"
            cdp_cmd = "show cdp neighbor detail"
            # Użyj wzorca regex z obiektu cli_cfg
            lldp_exp_str = cli_cfg.RE_SLOT_SYS_PROMPT.pattern
            cdp_exp_str = cli_cfg.RE_SLOT_SYS_PROMPT.pattern
            run_cdp = True
            platform_handler_applied = True
            try:
                logger.debug(f"  CLI: Próba 'disable clipaging' (timing) dla {host} (Extreme-like).")
                net_connect.send_command_timing("disable clipaging",
                                                delay_factor=device_params.get('global_delay_factor',
                                                                               cli_cfg.global_delay_factor))  # Użyj z cli_cfg jako fallback
                logger.info(f"  CLI: Wysłano 'disable clipaging' (timing) dla {host}.")
            except Exception as e_pg:
                logger.warning(f"  CLI: Wyjątek przy 'disable clipaging' (timing) dla {host}: {e_pg}.")

        elif "nx-os" in system_info_str or "cisco_nxos" in effective_device_type.lower():
            logger.info(f"  CLI: Ustawienia Cisco NX-OS dla {host}.")
            lldp_cmd = "show lldp neighbors detail"  # Domyślna komenda
            if base_prompt_log not in ["N/A", "N/A (nie odczytano)", "N/A (błąd)"]:
                lldp_exp_str = cli_cfg.RE_NXOS_PROMPT.pattern if cli_cfg.RE_NXOS_PROMPT.fullmatch(
                    base_prompt_log) else base_prompt_log
            if run_cdp and lldp_exp_str: cdp_exp_str = lldp_exp_str
            platform_handler_applied = True

        elif ("ios" in system_info_str and "xr" not in system_info_str and (
                "cisco_ios" in effective_device_type.lower() or "cisco_xe" in effective_device_type.lower())) or "catalyst" in system_info_str:
            logger.info(f"  CLI: Ustawienia Cisco IOS/XE dla {host}.")
            if base_prompt_log not in ["N/A", "N/A (nie odczytano)", "N/A (błąd)"]:
                lldp_exp_str = cli_cfg.RE_IOS_PROMPT.pattern if cli_cfg.RE_IOS_PROMPT.fullmatch(
                    base_prompt_log) else base_prompt_log
                if run_cdp and lldp_exp_str: cdp_exp_str = lldp_exp_str
            try:
                logger.debug(f"  CLI: Próba 'terminal length 0' dla {host} (IOS/XE).")
                net_connect.send_command_timing("terminal length 0", read_timeout=15)  # Timeout może być konfigurowalny
            except Exception as e_tl:
                logger.warning(f"  CLI: Wyjątek przy 'terminal length 0' dla {host}: {e_tl}")
            platform_handler_applied = True

        elif "junos" in system_info_str or "juniper" in system_info_str or "junos" in effective_device_type.lower():
            logger.info(f"  CLI: Ustawienia Junos dla {host}.")
            lldp_cmd = "show lldp neighbors interface all detail"
            lldp_exp_str = None
            run_cdp = False
            try:
                logger.debug(f"  CLI: Próba 'set cli screen-length 0' dla {host} (Junos).")
                net_connect.send_command_timing("set cli screen-length 0", read_timeout=15)
            except Exception as e_sl:
                logger.warning(f"  CLI: Wyjątek przy 'set cli screen-length 0' dla {host}: {e_sl}")
            platform_handler_applied = True

        if not platform_handler_applied:
            logger.info(
                f"  CLI: Nie zidentyfikowano specyficznej platformy dla {host}. Stosuję logikę fallback dla expect_string.")
            if base_prompt_log in ["N/A", "N/A (nie odczytano)", "N/A (błąd)"]:
                logger.warning(
                    f"  CLI: Platforma nieznana i Netmiko nie ustaliło base_prompt dla {host}. Używam domyślnego expect_string Netmiko (None).")
                lldp_exp_str = None
                if run_cdp: cdp_exp_str = None
            elif not cli_cfg.RE_SIMPLE_PROMPT.fullmatch(base_prompt_log):  # Użyj regex z config
                logger.info(
                    f"  CLI: Platforma nieznana, używam ustalonego złożonego base_prompt ('{base_prompt_log}') jako expect_string.")
                lldp_exp_str = base_prompt_log
                if run_cdp: cdp_exp_str = base_prompt_log
            # Jeśli prompt jest prosty, lldp_exp_str i cdp_exp_str pozostaną None (Netmiko użyje domyślnego)

        logger.info(
            f"  CLI: Finalne ustawienia dla {host} - LLDP Cmd: '{lldp_cmd}', LLDP Expect: '{lldp_exp_str}', Uruchom CDP: {run_cdp}, CDP Cmd: '{cdp_cmd}', CDP Expect: '{cdp_exp_str}'")

        lldp_params: Dict[str, Any] = {"read_timeout": cli_cfg.read_timeout_lldp_cdp}
        if lldp_exp_str: lldp_params["expect_string"] = lldp_exp_str

        try:
            lldp_raw = net_connect.send_command(lldp_cmd, **lldp_params)
            if lldp_raw and isinstance(lldp_raw, str):
                logger.debug(f"  CLI-LLDP: Otrzymano surowe wyjście LLDP dla {host} (długość: {len(lldp_raw)}).")
                if not lldp_raw.strip():
                    logger.info(f"  CLI-LLDP: Puste wyjście LLDP dla {host}.")
                else:
                    conns = _parse_lldp_output(lldp_raw, host, cli_cfg)
                    all_cli_connections.extend(conns)
                    if not conns: logger.info(f"  CLI-LLDP: Otrzymano wyjście LLDP, ale nie sparsowano połączeń.")
            elif not lldp_raw:
                logger.info(f"  CLI-LLDP: Brak danych LLDP (None) dla {host}.")
            else:
                logger.warning(f"  CLI-LLDP: Nieoczekiwany typ danych LLDP ({type(lldp_raw)}) dla {host}.")
        except Exception as e_lldp:
            logger.warning(f"  CLI-LLDP: Błąd komendy LLDP ('{lldp_cmd}') dla {host}: {e_lldp}", exc_info=False)
            logger.debug(f"  CLI-LLDP: Pełny traceback błędu LLDP dla {host}:", exc_info=True)
            if (
                    "nx-os" in system_info_str or "cisco_nxos" in effective_device_type.lower()) and lldp_cmd == "show lldp neighbors detail" and (
                    "invalid command" in str(e_lldp).lower() or "incomplete command" in str(e_lldp).lower()):
                logger.info(f"  CLI: Ponowna próba LLDP dla NX-OS {host} z komendą 'show lldp neighbors'")
                lldp_cmd_nxos_fallback = "show lldp neighbors"
                lldp_params_nxos_fallback: Dict[str, Any] = {"read_timeout": cli_cfg.read_timeout_lldp_cdp}
                nxos_fallback_exp_str = lldp_exp_str
                if nxos_fallback_exp_str: lldp_params_nxos_fallback["expect_string"] = nxos_fallback_exp_str
                try:
                    lldp_raw_fallback = net_connect.send_command(lldp_cmd_nxos_fallback, **lldp_params_nxos_fallback)
                    if lldp_raw_fallback and isinstance(lldp_raw_fallback, str):
                        if not lldp_raw_fallback.strip():
                            logger.info(f"  CLI-LLDP (fallback NXOS): Puste wyjście dla {host}.")
                        else:
                            conns_fb = _parse_lldp_output(lldp_raw_fallback, host, cli_cfg)
                            all_cli_connections.extend(conns_fb)
                            if not conns_fb: logger.info(
                                f"  CLI-LLDP (fallback NXOS): Otrzymano wyjście, ale nie sparsowano połączeń.")
                    elif not lldp_raw_fallback:
                        logger.info(f"  CLI-LLDP (fallback NXOS): Brak danych (None) dla {host}.")
                except Exception as e_nxos_fallback:
                    logger.warning(
                        f"  CLI-LLDP (fallback NXOS): Błąd komendy '{lldp_cmd_nxos_fallback}' dla {host}: {e_nxos_fallback}",
                        exc_info=False)

        if not all_cli_connections and run_cdp:
            cdp_params: Dict[str, Any] = {"read_timeout": cli_cfg.read_timeout_lldp_cdp}
            if cdp_exp_str: cdp_params["expect_string"] = cdp_exp_str
            logger.info(f"  CLI: CDP dla {host}: cmd='{cdp_cmd}', params={cdp_params}")
            try:
                cdp_raw = net_connect.send_command(cdp_cmd, **cdp_params)
                if cdp_raw and isinstance(cdp_raw, str):
                    logger.debug(f"  CLI-CDP: Otrzymano surowe wyjście CDP dla {host} (długość: {len(cdp_raw)}).")
                    if "cdp not enabled" in cdp_raw.lower():
                        logger.info(f"  CLI-CDP: CDP nie jest włączone na {host}.")
                    elif not cdp_raw.strip():
                        logger.info(f"  CLI-CDP: Puste wyjście CDP dla {host}.")
                    else:
                        conns = _parse_cdp_output(cdp_raw, host, cli_cfg)
                        all_cli_connections.extend(conns)
                        if not conns: logger.info(f"  CLI-CDP: Otrzymano wyjście CDP, ale nie sparsowano połączeń.")
                elif not cdp_raw:
                    logger.info(f"  CLI-CDP: Brak danych CDP (None) dla {host}.")
                else:
                    logger.warning(f"  CLI-CDP: Nieoczekiwany typ danych CDP ({type(cdp_raw)}) dla {host}.")
            except Exception as e_cdp:
                logger.warning(f"  CLI-CDP: Błąd komendy CDP ('{cdp_cmd}') dla {host}: {e_cdp}", exc_info=False)
                logger.debug(f"  CLI-CDP: Pełny traceback błędu CDP dla {host}:", exc_info=True)
        elif not run_cdp:
            logger.info(f"  CLI: Pominięto CDP dla {host} (zgodnie z logiką platformy).")

    except NetmikoAuthenticationException as e_auth_main:
        logger.error(f"⚠ CLI Auth Error: {host}: {e_auth_main}")
    except NetmikoTimeoutException as e_timeout_main:
        logger.error(f"⚠ CLI Timeout Error: {host}: {e_timeout_main}")
    except Exception as e_general_main:
        logger.error(f"⚠ CLI General Error: {host}: {e_general_main}", exc_info=True)
    finally:
        if net_connect and net_connect.is_alive():
            try:
                net_connect.disconnect()
                logger.info(f"  CLI: Rozłączono z {host}")
            except Exception as e_disc_final:
                logger.error(f"  CLI Disconnect Error: {host}: {e_disc_final}", exc_info=True)

    if not all_cli_connections:
        logger.info(f"⟶ CLI: Brak sąsiadów CLI dla {host}.")
    else:
        logger.info(f"✓ CLI: Znaleziono {len(all_cli_connections)} sąsiadów dla {host}.")
    return all_cli_connections