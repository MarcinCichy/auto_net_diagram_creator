# cli_utils.py
import re
import logging
from typing import List, Dict, Any, Optional, Pattern

from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException

logger = logging.getLogger(__name__)


def _compile_regex(pattern_str: Optional[str], flags: int = 0, default_pattern: str = ".*") -> Pattern[str]:
    """Kompiluje regex; jeśli pattern_str jest None lub pusty, używa default_pattern."""
    try:
        if pattern_str and pattern_str.strip(): # Dodano strip() dla pewności
            return re.compile(pattern_str, flags)
    except re.error as e:
        logger.error(f"Błąd kompilacji regex '{pattern_str}': {e}. Używam domyślnego '{default_pattern}'.")
    logger.debug(f"Regex pattern is None or empty, or failed to compile ('{pattern_str}'). Using default: '{default_pattern}'")
    return re.compile(default_pattern, flags)


def _normalize_interface_name(if_name: str, replacements: Dict[str, str]) -> str:
    if_name = if_name.strip()
    # Sortuj wg długości klucza malejąco, aby np. "TenGigabitEthernet" było sprawdzane przed "GigabitEthernet"
    for long, short in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if if_name.lower().startswith(long.lower()): # Porównuj case-insensitive
            return short + if_name[len(long):]
    return if_name


def _parse_lldp_output(lldp_output: str, local_hostname: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    connections: List[Dict[str, Any]] = []
    if not lldp_output: return connections
    logger.debug(f"CLI-LLDP: Próba parsowania danych LLDP dla {local_hostname} (długość: {len(lldp_output)})...")

    re_lldp_header_candidate = _compile_regex(config.get('lldp_regex_header_candidate'), re.IGNORECASE)
    re_lldp_block_split = _compile_regex(config.get('lldp_regex_block_split'), re.IGNORECASE) # Zmieniono flagi, aby były zgodne z nowym regexem, jeśli potrzeba
    re_lldp_local_port_id = _compile_regex(config.get('lldp_regex_local_port_id'), re.MULTILINE | re.IGNORECASE)
    re_lldp_sys_name = _compile_regex(config.get('lldp_regex_sys_name'), re.MULTILINE | re.IGNORECASE)
    re_lldp_remote_port_id = _compile_regex(config.get('lldp_regex_remote_port_id'), re.MULTILINE | re.IGNORECASE)
    re_lldp_remote_port_desc = _compile_regex(config.get('lldp_regex_remote_port_desc'), re.MULTILINE | re.IGNORECASE)
    re_lldp_vlan_id = _compile_regex(config.get('lldp_regex_vlan_id'), re.MULTILINE | re.IGNORECASE)
    interface_replacements = config.get('interface_name_replacements', {})

    data_to_parse = lldp_output
    # Logika nagłówka i dzielenia na bloki
    # Rozważmy uproszczenie: jeśli `re_lldp_block_split` jest efektywny, może nie potrzebujemy skomplikowanej logiki z `re_lldp_header_candidate`
    # Na razie zostawiam oryginalną logikę, ale z poprawionym logowaniem

    # Nowa, uproszczona logika dzielenia na bloki, jeśli `lldp_regex_block_split` jest odpowiednio zdefiniowany (np. `\n\s*(?=Chassis id:)`)
    # Ta logika zakłada, że każdy blok zaczyna się od "Chassis id:"
    if not data_to_parse.strip().lower().startswith('chassis id:'):
        # Spróbuj znaleźć pierwszy "Chassis id:"
        first_chassis_match = re.search(r'Chassis id:', data_to_parse, re.IGNORECASE)
        if first_chassis_match:
            data_to_parse = data_to_parse[first_chassis_match.start():]
        else:
            logger.info(f"CLI-LLDP: Dane LLDP dla {local_hostname} nie zaczynają się od 'Chassis id:' i nie znaleziono znacznika. Próba parsowania od początku.")
            # Nie rób nic, pozwól re_lldp_block_split.split() próbować od początku
            # lub zwróć pustą listę, jeśli to jest niepoprawne.
            # Dla bezpieczeństwa, jeśli nie ma "Chassis id:", a regexy są na nim oparte, to prawdopodobnie nie zadziała.
            if 'chassis id:' not in data_to_parse.lower():
                logger.warning(f"CLI-LLDP: Brak 'Chassis id:' w danych LLDP dla {local_hostname}. Nie można sparsować.")
                return connections


    blocks = re_lldp_block_split.split(data_to_parse)
    # Pierwszy element po splicie może być pusty lub być nagłówkiem, jeśli split był na początku bloku.
    # Musimy upewnić się, że przetwarzamy tylko bloki, które faktycznie zawierają dane sąsiada.

    parsed_count = 0
    for block_content in blocks:
        block_strip = block_content.strip()
        # Każdy użyteczny blok powinien zaczynać się od "Chassis id:" (lub być poprzedzony nim po splicie)
        # Jeśli split jest na początku "Chassis id:", to `block_strip` będzie zawierał "Chassis id:"
        # Jeśli split jest *po* "Chassis id:", to `block_strip` nie będzie go zawierał na początku.
        # Nowy regex `\n\s*(?=Chassis id:)` powinien dać bloki zaczynające się od "Chassis id:"
        if not block_strip or not block_strip.lower().startswith('chassis id:'):
            if block_strip: # Loguj tylko jeśli jest jakaś treść do pominięcia
                logger.debug(f"CLI-LLDP: Pomijam blok (nie zaczyna się od 'Chassis id:') dla {local_hostname}:\n{block_strip[:100]}...")
            continue

        local_if_match = re_lldp_local_port_id.search(block_strip)
        remote_sys_match = re_lldp_sys_name.search(block_strip)
        remote_port_id_match = re_lldp_remote_port_id.search(block_strip)

        if not (local_if_match and remote_sys_match and remote_port_id_match):
            logger.debug(f"CLI-LLDP: Pominięto blok - brak kluczowych danych w {local_hostname}.")
            logger.debug(f"  Szczegóły dopasowań: local_if={bool(local_if_match)}, remote_sys={bool(remote_sys_match)}, remote_port_id={bool(remote_port_id_match)}")
            logger.debug(f"  Przetwarzany blok (fragment):\n{block_strip[:200]}")
            continue

        local_if_raw = local_if_match.group(1).strip()
        if not local_if_raw or 'not advertised' in local_if_raw.lower(): continue

        local_if = _normalize_interface_name(local_if_raw, interface_replacements)
        remote_sys = remote_sys_match.group(1).strip()
        remote_port_raw = remote_port_id_match.group(1).strip()

        remote_port_desc_match = re_lldp_remote_port_desc.search(block_strip)
        remote_port_desc_val = remote_port_desc_match.group(1).strip() if remote_port_desc_match else ""

        chosen_remote_port = remote_port_raw
        # Logika wyboru remote port (ID vs Description)
        if remote_port_desc_val and 'not advertised' not in remote_port_desc_val.lower():
            # Jeśli Port ID jest "not advertised" LUB zawiera MAC (dwukropek), LUB jest nieprzydatne (np. nazwa interfejsu z innego vendora)
            # A Port Description jest dostępny i użyteczny
            if (not chosen_remote_port or
                'not advertised' in chosen_remote_port.lower() or
                ':' in chosen_remote_port or
                (len(chosen_remote_port) > 20 and not chosen_remote_port.isalnum()) # Heurystyka dla "dziwnych" Port ID
               ):
                logger.debug(f"CLI-LLDP: Dla {local_hostname} -> {remote_sys}: Port ID ('{remote_port_raw}') jest nieoptymalny. Używam Port Description ('{remote_port_desc_val}').")
                chosen_remote_port = remote_port_desc_val
            # Jeśli Port ID wygląda jak nazwa interfejsu, a Port Description też, ale jest krótszy/bardziej standardowy
            elif chosen_remote_port and chosen_remote_port != remote_port_desc_val and len(remote_port_desc_val) < len(chosen_remote_port) and not ':' in remote_port_desc_val:
                 logger.debug(f"CLI-LLDP: Dla {local_hostname} -> {remote_sys}: Port ID ('{remote_port_raw}') i Port Description ('{remote_port_desc_val}') są różne. Używam krótszego Port Description.")
                 chosen_remote_port = remote_port_desc_val


        if not chosen_remote_port or 'not advertised' in chosen_remote_port.lower(): continue

        remote_if = _normalize_interface_name(chosen_remote_port, interface_replacements)

        vlan_match = re_lldp_vlan_id.search(block_strip)
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
        logger.debug(f"CLI-LLDP: Niesparsowane dane LLDP dla {local_hostname} (fragment):\n{lldp_output[:500]}...")
    return connections


def _parse_cdp_output(cdp_output: str, local_hostname: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    connections: List[Dict[str, Any]] = []
    if not cdp_output or "Device ID" not in cdp_output: # Podstawowe sprawdzenie
        if cdp_output and "cdp not enabled" in cdp_output.lower():
            logger.info(f"CLI-CDP: CDP nie jest włączone na {local_hostname}.")
        elif cdp_output:
            logger.info(f"CLI-CDP: Brak 'Device ID' w wyjściu CDP dla {local_hostname}, lub puste wyjście.")
        return connections
    logger.debug(f"CLI-CDP: Próba parsowania danych CDP dla {local_hostname}...")

    re_cdp_block_split = _compile_regex(config.get('cdp_regex_block_split'), re.MULTILINE)
    re_cdp_device_id = _compile_regex(config.get('cdp_regex_device_id'), re.IGNORECASE)
    re_cdp_local_if = _compile_regex(config.get('cdp_regex_local_if'), re.IGNORECASE)
    re_cdp_remote_if = _compile_regex(config.get('cdp_regex_remote_if'), re.IGNORECASE)
    interface_replacements = config.get('interface_name_replacements', {})

    # Usuń nagłówek przed dzieleniem na bloki, jeśli to konieczne
    # Można to zrobić przez wyszukanie pierwszej linii z "Device ID"
    header_match = re.search(r"Device ID\s*:", cdp_output, re.IGNORECASE)
    data_to_parse_cdp = cdp_output
    if header_match:
        # Znajdź początek linii, w której jest "Device ID"
        line_start_pos = cdp_output.rfind('\n', 0, header_match.start()) + 1
        # Czasami dane CDP zaczynają się od razu od informacji o sąsiadach, bez ogólnego nagłówka tabeli.
        # Sprawdź, czy przed "Device ID" jest dużo tekstu (co sugeruje nagłóbek)
        # Prosta heurystyka: jeśli "Device ID" nie jest blisko początku i poprzedza je linia z kreskami
        first_block_marker_search = re_cdp_block_split.search(cdp_output)
        if first_block_marker_search and first_block_marker_search.start() < line_start_pos :
             data_to_parse_cdp = cdp_output[first_block_marker_search.end():].strip()
             logger.debug(f"CLI-CDP: Usunięto potencjalny nagłówek przed pierwszym blokiem dla {local_hostname}.")
        # Jeśli nie ma separatora przed pierwszym "Device ID", załóż, że dane zaczynają się od niego.
        # Niektóre urządzenia (np. starsze IOS) mogą nie mieć separatora '-' przed pierwszym wpisem.

    cdp_blocks = [block.strip() for block in re_cdp_block_split.split(data_to_parse_cdp) if block.strip()]

    if not cdp_blocks:
        logger.info(f"CLI-CDP: Nie udało się podzielić danych CDP na bloki dla {local_hostname}.")
        logger.debug(f"CLI-CDP: Dane wejściowe (po ew. usunięciu nagłówka):\n{data_to_parse_cdp[:500]}...")
        return connections

    parsed_count_cdp = 0
    for block_idx, block_content in enumerate(cdp_blocks):
        if not block_content.strip(): continue # Pomiń puste bloki

        dev_id_match = re_cdp_device_id.search(block_content)
        local_if_match = re_cdp_local_if.search(block_content) # Szukaj 'Interface:'
        remote_if_match = re_cdp_remote_if.search(block_content) # Szukaj 'Port ID:' lub 'Outgoing Port:'

        if dev_id_match and local_if_match and remote_if_match:
            # Upewnij się, że pobierasz właściwą grupę z regexa
            local_if_raw = local_if_match.group(1).strip().split(',')[0].strip() # Regex powinien zwracać nazwę interfejsu w grupie 1
            local_if = _normalize_interface_name(local_if_raw, interface_replacements)

            neighbor_host_val_raw = dev_id_match.group(1).strip()
            # Usuń domenę, jeśli jest, ale tylko jeśli zawiera kropkę.
            # Niektóre Device ID to np. "device-name (serial_number)"
            if '.' in neighbor_host_val_raw and not '(' in neighbor_host_val_raw :
                neighbor_host_val = neighbor_host_val_raw.split('.')[0]
            else:
                neighbor_host_val = neighbor_host_val_raw

            remote_if_raw = remote_if_match.group(1).strip() # Regex powinien zwracać nazwę portu w grupie 1
            remote_if = _normalize_interface_name(remote_if_raw, interface_replacements)

            if local_if and neighbor_host_val and remote_if:
                connections.append({
                    "local_host": local_hostname, "local_if": local_if,
                    "neighbor_host": neighbor_host_val, "neighbor_if": remote_if,
                    "vlan": None, "via": "CLI-CDP"
                })
                parsed_count_cdp += 1
            else:
                logger.debug(f"CLI-CDP: Pominięto blok {block_idx} - brak pełnych danych po normalizacji w {local_hostname}.")
        else:
            logger.debug(f"CLI-CDP: Pominięto blok {block_idx} - brak kluczowych danych w {local_hostname}.")
            logger.debug(f"  Szczegóły dopasowań: dev_id={bool(dev_id_match)}, local_if={bool(local_if_match)}, remote_if={bool(remote_if_match)}")
            logger.debug(f"  Przetwarzany blok CDP (fragment):\n{block_content[:200]}")


    if parsed_count_cdp > 0:
        logger.info(f"✓ CLI-CDP: Sparsowano {parsed_count_cdp} połączeń CDP dla {local_hostname}.")
    elif cdp_output and cdp_output.strip() and "cdp not enabled" not in cdp_output.lower():
        logger.info(f"ⓘ CLI-CDP: Otrzymano dane CDP, ale nie sparsowano użytecznych połączeń dla {local_hostname}.")
        logger.debug(f"CLI-CDP: Niesparsowane dane CDP dla {local_hostname} (fragment):\n{cdp_output[:500]}...")

    return connections


def cli_get_neighbors_enhanced(host: str, username: str, password: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not host or not username or not password:
        logger.warning(f"CLI: Brak danych logowania dla '{host}'. Pomijam.")
        return []

    logger.info(f"⟶ CLI: Próba odkrycia sąsiadów dla {host}")

    session_log_path = None
    netmiko_session_log_template = config.get('cli_netmiko_session_log_template')
    if netmiko_session_log_template:
        try:
            # Zabezpieczenie przed błędami formatowania, jeśli host zawiera np. { lub }
            host_sanitized_for_log_path = host.replace('{', '_').replace('}', '_').replace('.', '_')
            session_log_path = netmiko_session_log_template.format(host=host_sanitized_for_log_path)
        except KeyError as e_log_format: # Jeśli szablon ma inne nieznane klucze
             logger.warning(f"  CLI: Błąd formatowania ścieżki logu sesji Netmiko ('{netmiko_session_log_template}'): {e_log_format}. Logowanie sesji wyłączone.")
             session_log_path = None


    device_params: Dict[str, Any] = {
        "device_type": "autodetect", # Netmiko spróbuje odgadnąć typ
        "host": host,
        "username": username,
        "password": password,
        "global_delay_factor": config.get('cli_global_delay_factor', 5.0),
        "session_log_file_mode": config.get('cli_session_log_file_mode', 'append'),
        "conn_timeout": config.get('cli_conn_timeout', 75),
        "auth_timeout": config.get('cli_auth_timeout', 90),
        "banner_timeout": config.get('cli_banner_timeout', 75)
    }
    if session_log_path:
        device_params["session_log"] = session_log_path
        logger.info(f"  CLI: Logowanie sesji Netmiko do pliku: {session_log_path}")


    all_cli_connections: List[Dict[str, Any]] = []
    net_connect: Optional[ConnectHandler] = None
    effective_device_type = "N/A (przed połączeniem)"
    base_prompt_log = "N/A (przed odczytem)"

    re_simple_prompt = _compile_regex(config.get('prompt_regex_simple'))
    re_slot_sys_prompt = _compile_regex(config.get('prompt_regex_slot_sys'))
    re_nxos_prompt = _compile_regex(config.get('prompt_regex_nxos'))
    re_ios_prompt = _compile_regex(config.get('prompt_regex_ios'))

    try:
        logger.info(f"  CLI: Łączenie z {host} (autodetect, gdf={device_params['global_delay_factor']})...")
        net_connect = ConnectHandler(**device_params)
        effective_device_type = net_connect.device_type # To jest ustalany typ przez Netmiko
        try:
            if net_connect.base_prompt: # base_prompt może być None
                base_prompt_log = net_connect.base_prompt.strip()
        except Exception as e_bp:
            logger.warning(f"  CLI: Wyjątek przy odczycie base_prompt dla {host}: {e_bp}")
            base_prompt_log = "N/A (błąd odczytu)"

        logger.info(f"  CLI: Połączono z {host} (Typ Netmiko: '{effective_device_type}')")
        logger.info(f"  CLI: Netmiko base_prompt: '{base_prompt_log}'")

        system_info_str = ""
        show_ver_expect_str: Optional[str] = None

        # Ustalanie expect_string dla 'show version'
        # Jeśli base_prompt jest złożony, użyj go jako expect_string
        if base_prompt_log and base_prompt_log not in ["N/A (przed odczytem)", "N/A (błąd odczytu)"]:
            if not re_simple_prompt.fullmatch(base_prompt_log): # Sprawdź czy prompt jest "prosty"
                show_ver_expect_str = base_prompt_log
                logger.debug(f"  CLI: Używam złożonego base_prompt ('{base_prompt_log}') jako expect_string dla 'show version'.")
        if not show_ver_expect_str: # Jeśli prompt jest prosty lub nie udało się go odczytać
            show_ver_expect_str = config.get('cli_default_expect_string_pattern') # Ogólny fallback z config.ini
            logger.debug(f"  CLI: Używam domyślnego wzorca expect_string ('{show_ver_expect_str}') dla 'show version'.")


        try:
            logger.debug(f"  CLI: Próba pobrania 'show version' z {host} (expect_string: '{show_ver_expect_str}')...")
            show_version_params: Dict[str, Any] = {"read_timeout": config.get('cli_read_timeout_general', 60)}
            if show_ver_expect_str: # Przekaż expect_string tylko jeśli jest zdefiniowany
                show_version_params["expect_string"] = show_ver_expect_str

            show_version_output = net_connect.send_command("show version", **show_version_params)

            if show_version_output and isinstance(show_version_output, str):
                system_info_str = show_version_output.lower()
                logger.info(f"  CLI: Otrzymano 'show version' (fragment): {system_info_str[:250].replace(chr(10), ' ').replace(chr(13), '')}...")
            else:
                logger.warning(f"  CLI: Nie udało się uzyskać wyjścia 'show version' dla {host} (puste lub zły typ).")
        except Exception as e_ver:
            logger.warning(
                f"  CLI: Błąd podczas 'show version' na {host} (użyty expect_string: '{show_ver_expect_str}'): {e_ver}")


        lldp_cmd = "show lldp neighbors detail" # Domyślna komenda
        cdp_cmd = "show cdp neighbors detail"  # Domyślna komenda
        lldp_exp_str: Optional[str] = None
        cdp_exp_str: Optional[str] = None
        run_cdp = True # Domyślnie próbuj CDP jeśli LLDP zawiedzie
        platform_handler_applied = False

        # Logika specyficzna dla platformy (Extreme, Cisco NX-OS, IOS/XE, Junos)
        # Używa base_prompt_log, effective_device_type, system_info_str

        is_extreme_prompt_match = base_prompt_log and base_prompt_log not in ["N/A (przed odczytem)", "N/A (błąd odczytu)"] and bool(
            re_slot_sys_prompt.fullmatch(base_prompt_log))
        is_extreme_type_sig = any(sig in effective_device_type.lower() for sig in
                                  ["extreme", "enterasys"]) # Rozszerzono o enterasys
        is_extreme_sysinfo = "extreme" in system_info_str or "enterasys" in system_info_str or "exos" in system_info_str
        is_extreme_like = is_extreme_prompt_match or is_extreme_type_sig or is_extreme_sysinfo

        logger.info(
            f"  CLI: Ocena platformy dla {host}: Extreme-like: {is_extreme_like} (prompt: {is_extreme_prompt_match}, typ Netmiko: '{effective_device_type}', sysinfo: {is_extreme_sysinfo})")

        if is_extreme_like:
            platform_reason = "wykryto jako Extreme (prompt/typ Netmiko/sysinfo)"
            logger.info(f"  CLI: Ustawienia dla {host} jako Extreme-like (powód: {platform_reason}).")
            lldp_cmd = "show lldp neighbors detailed" # Komenda dla Extreme
            cdp_cmd = "show cdp neighbor detail" # Komenda dla Extreme
            # Dla Extreme, jeśli prompt jest złożony, użyj go. W przeciwnym razie, domyślny regex z config.
            if base_prompt_log and base_prompt_log not in ["N/A (przed odczytem)", "N/A (błąd odczytu)"] and not re_simple_prompt.fullmatch(base_prompt_log):
                lldp_exp_str = base_prompt_log
            else: # Prosty prompt lub nieznany - użyj regexa dla slot/sys, bo to typowe dla Extreme
                lldp_exp_str = re_slot_sys_prompt.pattern if re_slot_sys_prompt.pattern else config.get('cli_default_expect_string_pattern')
            if run_cdp: cdp_exp_str = lldp_exp_str # Użyj tego samego dla CDP
            platform_handler_applied = True
            try:
                logger.debug(f"  CLI: Próba 'disable clipaging' (timing) dla {host} (Extreme-like).")
                net_connect.send_command_timing("disable clipaging") # Domyślny delay_factor Netmiko
                logger.info(f"  CLI: Wysłano 'disable clipaging' (timing) dla {host}.")
            except Exception as e_pg:
                logger.warning(f"  CLI: Wyjątek przy 'disable clipaging' (timing) dla {host}: {e_pg}.")


        elif "nx-os" in system_info_str or "cisco_nxos" in effective_device_type.lower():
            logger.info(f"  CLI: Ustawienia dla {host} jako Cisco NX-OS.")
            # lldp_cmd i cdp_cmd pozostają domyślne ("... detail")
            if base_prompt_log and base_prompt_log not in ["N/A (przed odczytem)", "N/A (błąd odczytu)"]:
                # Dla NX-OS, jeśli prompt jest złożony, użyj go. Inaczej, specyficzny regex dla NX-OS lub domyślny.
                if not re_simple_prompt.fullmatch(base_prompt_log):
                    lldp_exp_str = base_prompt_log
                elif re_nxos_prompt.fullmatch(base_prompt_log): # Sprawdź, czy pasuje do typowego promptu NX-OS
                    lldp_exp_str = re_nxos_prompt.pattern
                else: # Jeśli nie pasuje do typowego NX-OS, ale jest prosty, użyj domyślnego
                    lldp_exp_str = config.get('cli_default_expect_string_pattern')
            else: # Brak base_prompt
                lldp_exp_str = config.get('cli_default_expect_string_pattern')

            if run_cdp: cdp_exp_str = lldp_exp_str
            platform_handler_applied = True

        elif ("ios" in system_info_str and "xr" not in system_info_str and (
                "cisco_ios" in effective_device_type.lower() or "cisco_xe" in effective_device_type.lower())) or "catalyst" in system_info_str:
            logger.info(f"  CLI: Ustawienia dla {host} jako Cisco IOS/XE.")
            # lldp_cmd i cdp_cmd pozostają domyślne
            if base_prompt_log and base_prompt_log not in ["N/A (przed odczytem)", "N/A (błąd odczytu)"]:
                if not re_simple_prompt.fullmatch(base_prompt_log):
                    lldp_exp_str = base_prompt_log
                elif re_ios_prompt.fullmatch(base_prompt_log):
                    lldp_exp_str = re_ios_prompt.pattern
                else:
                    lldp_exp_str = config.get('cli_default_expect_string_pattern')
            else:
                lldp_exp_str = config.get('cli_default_expect_string_pattern')

            if run_cdp: cdp_exp_str = lldp_exp_str
            try:
                logger.debug(f"  CLI: Próba 'terminal length 0' dla {host} (IOS/XE).")
                net_connect.send_command_timing("terminal length 0", read_timeout=15) # Krótki timeout dla tej komendy
            except Exception as e_tl:
                logger.warning(f"  CLI: Wyjątek przy 'terminal length 0' dla {host}: {e_tl}")
            platform_handler_applied = True

        elif "junos" in system_info_str or "juniper" in system_info_str or "junos" in effective_device_type.lower():
            logger.info(f"  CLI: Ustawienia dla {host} jako Junos.")
            lldp_cmd = "show lldp neighbors interface all detail"  # Specyficzna komenda dla Junos
            # CDP rzadko na Junos, ale jeśli jest, to 'show cdp neighbors detail' powinno działać
            run_cdp = config.get("cli_junos_try_cdp", False) # Domyślnie nie próbuj CDP na Junos, można to włączyć w config.ini
            # Junos zwykle nie potrzebuje expect_string, jeśli prompt jest prosty.
            # Jeśli base_prompt jest złożony, użyj go.
            if base_prompt_log and base_prompt_log not in ["N/A (przed odczytem)", "N/A (błąd odczytu)"] and not re_simple_prompt.fullmatch(base_prompt_log):
                lldp_exp_str = base_prompt_log
            else: # Prosty prompt lub brak
                 lldp_exp_str = config.get('cli_default_expect_string_pattern') # Fallback na domyślny z config
            if run_cdp: cdp_exp_str = lldp_exp_str # Użyj tego samego, jeśli CDP jest uruchamiane

            try:
                logger.debug(f"  CLI: Próba 'set cli screen-length 0' dla {host} (Junos).")
                net_connect.send_command_timing("set cli screen-length 0", read_timeout=15)
            except Exception as e_sl:
                logger.warning(f"  CLI: Wyjątek przy 'set cli screen-length 0' dla {host}: {e_sl}")
            platform_handler_applied = True

        if not platform_handler_applied:
            logger.info(
                f"  CLI: Nie zidentyfikowano specyficznej platformy dla {host}. Stosuję logikę fallback dla expect_string.")
            # Jeśli base_prompt jest dostępny i złożony, użyj go jako expect_string.
            # W przeciwnym razie użyj domyślnego wzorca z config.ini.
            if base_prompt_log and base_prompt_log not in ["N/A (przed odczytem)", "N/A (błąd odczytu)"] and not re_simple_prompt.fullmatch(base_prompt_log):
                logger.info(
                    f"  CLI: Platforma nieznana, używam ustalonego złożonego base_prompt ('{base_prompt_log}') jako expect_string.")
                lldp_exp_str = base_prompt_log
            else: # Prosty prompt, brak promptu, lub błąd odczytu promptu
                logger.info(
                    f"  CLI: Platforma nieznana, base_prompt jest prosty lub nieodczytany. Używam domyślnego wzorca expect_string z config.ini.")
                lldp_exp_str = config.get('cli_default_expect_string_pattern')

            if run_cdp: cdp_exp_str = lldp_exp_str

        logger.info(
            f"  CLI: Finalne ustawienia dla {host} - LLDP Cmd: '{lldp_cmd}', LLDP Expect: '{lldp_exp_str}', Uruchom CDP: {run_cdp}, CDP Cmd: '{cdp_cmd}', CDP Expect: '{cdp_exp_str}'")

        # LLDP
        lldp_params: Dict[str, Any] = {"read_timeout": config.get('cli_read_timeout_lldp_cdp', 180)}
        if lldp_exp_str: lldp_params["expect_string"] = lldp_exp_str
        logger.info(f"  CLI: LLDP dla {host}: cmd='{lldp_cmd}', params={lldp_params}")
        try:
            lldp_raw = net_connect.send_command(lldp_cmd, **lldp_params)
            if lldp_raw and isinstance(lldp_raw, str):
                logger.debug(f"  CLI-LLDP: Otrzymano surowe wyjście LLDP dla {host} (długość: {len(lldp_raw)}).")
                if not lldp_raw.strip():
                    logger.info(f"  CLI-LLDP: Puste wyjście LLDP dla {host}.")
                else:
                    conns_lldp = _parse_lldp_output(lldp_raw, host, config)
                    all_cli_connections.extend(conns_lldp)
                    if not conns_lldp and lldp_raw.strip(): # Jeśli było wyjście, ale nic nie sparsowano
                        logger.info(f"  CLI-LLDP: Otrzymano wyjście LLDP, ale nie sparsowano połączeń.")
            elif not lldp_raw: # lldp_raw jest None
                logger.info(f"  CLI-LLDP: Brak danych LLDP (send_command zwróciło None) dla {host}.")
            else: # Nieoczekiwany typ
                logger.warning(f"  CLI-LLDP: Nieoczekiwany typ danych LLDP ({type(lldp_raw)}) dla {host}.")
        except Exception as e_lldp:
            logger.warning(f"  CLI-LLDP: Błąd komendy LLDP ('{lldp_cmd}') dla {host}: {e_lldp}", exc_info=False)
            logger.debug(f"  CLI-LLDP: Pełny traceback błędu LLDP dla {host}:", exc_info=True)

            # Fallback dla NX-OS, jeśli 'detail' zawiedzie
            if ("nx-os" in system_info_str or "cisco_nxos" in effective_device_type.lower()) and \
               lldp_cmd == "show lldp neighbors detail" and \
               ("invalid" in str(e_lldp).lower() or "incomplete" in str(e_lldp).lower() or "unrecognized" in str(e_lldp).lower()):
                logger.info(f"  CLI-LLDP: Ponowna próba LLDP dla NX-OS {host} z komendą 'show lldp neighbors'")
                lldp_cmd_nxos_fallback = "show lldp neighbors"
                # Użyj tych samych parametrów co dla 'detail', w tym expect_string
                try:
                    lldp_raw_fallback = net_connect.send_command(lldp_cmd_nxos_fallback, **lldp_params)
                    if lldp_raw_fallback and isinstance(lldp_raw_fallback, str):
                        if not lldp_raw_fallback.strip():
                            logger.info(f"  CLI-LLDP (fallback NXOS): Puste wyjście dla {host}.")
                        else:
                            conns_fb = _parse_lldp_output(lldp_raw_fallback, host, config)
                            all_cli_connections.extend(conns_fb) # Dodaj, nawet jeśli poprzednie LLDP coś dało (mało prawdopodobne)
                            if not conns_fb: logger.info(
                                f"  CLI-LLDP (fallback NXOS): Otrzymano wyjście, ale nie sparsowano połączeń.")
                    elif not lldp_raw_fallback:
                        logger.info(f"  CLI-LLDP (fallback NXOS): Brak danych (None) dla {host}.")
                except Exception as e_nxos_fallback:
                    logger.warning(
                        f"  CLI-LLDP (fallback NXOS): Błąd komendy '{lldp_cmd_nxos_fallback}' dla {host}: {e_nxos_fallback}",
                        exc_info=False)

        # CDP (uruchom, jeśli LLDP nie dało wyników lub jeśli jest to preferowane/zawsze uruchamiane)
        # Prosta logika: jeśli LLDP nie dało wyników, a CDP jest dozwolone, uruchom CDP.
        # Można dodać flagę w config.ini typu `cli_prefer_cdp` lub `cli_run_both_protocols`.
        # Na razie: uruchom CDP, jeśli `run_cdp` jest True i `all_cli_connections` jest puste.
        if not all_cli_connections and run_cdp:
            cdp_params: Dict[str, Any] = {"read_timeout": config.get('cli_read_timeout_lldp_cdp', 180)}
            if cdp_exp_str: cdp_params["expect_string"] = cdp_exp_str
            logger.info(f"  CLI: CDP dla {host}: cmd='{cdp_cmd}', params={cdp_params}")
            try:
                cdp_raw = net_connect.send_command(cdp_cmd, **cdp_params)
                if cdp_raw and isinstance(cdp_raw, str):
                    logger.debug(f"  CLI-CDP: Otrzymano surowe wyjście CDP dla {host} (długość: {len(cdp_raw)}).")
                    if "cdp not enabled" in cdp_raw.lower(): # Sprawdź czy CDP jest wyłączone
                        logger.info(f"  CLI-CDP: CDP nie jest włączone na {host}.")
                    elif not cdp_raw.strip():
                        logger.info(f"  CLI-CDP: Puste wyjście CDP dla {host}.")
                    else:
                        conns_cdp = _parse_cdp_output(cdp_raw, host, config)
                        all_cli_connections.extend(conns_cdp)
                        if not conns_cdp and cdp_raw.strip():
                             logger.info(f"  CLI-CDP: Otrzymano wyjście CDP, ale nie sparsowano połączeń.")
                elif not cdp_raw:
                    logger.info(f"  CLI-CDP: Brak danych CDP (None) dla {host}.")
                else:
                    logger.warning(f"  CLI-CDP: Nieoczekiwany typ danych CDP ({type(cdp_raw)}) dla {host}.")
            except Exception as e_cdp:
                logger.warning(f"  CLI-CDP: Błąd komendy CDP ('{cdp_cmd}') dla {host}: {e_cdp}", exc_info=False)
                logger.debug(f"  CLI-CDP: Pełny traceback błędu CDP dla {host}:", exc_info=True)
        elif not run_cdp:
            logger.info(f"  CLI: Pominięto CDP dla {host} (zgodnie z logiką platformy lub brakiem LLDP).")
        elif all_cli_connections and run_cdp: # Jeśli LLDP dało wyniki, a CDP jest dozwolone
             logger.info(f"  CLI: LLDP dało wyniki dla {host}. Pomijam CDP (chyba że skonfigurowano inaczej).")


    except NetmikoAuthenticationException as e_auth_main:
        logger.error(f"⚠ CLI Auth Error: {host}: {e_auth_main}")
    except NetmikoTimeoutException as e_timeout_main:
        logger.error(f"⚠ CLI Timeout Error: {host}: {e_timeout_main}")
    except Exception as e_general_main: # Łap ogólne błędy Netmiko lub inne
        logger.error(f"⚠ CLI General Error with {host}: {e_general_main}", exc_info=True)
    finally:
        if net_connect and net_connect.is_alive():
            try:
                net_connect.disconnect()
                logger.info(f"  CLI: Rozłączono z {host}")
            except Exception as e_disc_final: # Błędy przy rozłączaniu są mniej krytyczne
                logger.error(f"  CLI Disconnect Error: {host}: {e_disc_final}", exc_info=True)
        elif net_connect: # Nie jest alive, ale obiekt istnieje
            logger.info(f"  CLI: Sesja Netmiko z {host} nie była aktywna przed próbą rozłączenia.")


    if not all_cli_connections:
        logger.info(f"⟶ CLI: Brak sąsiadów CLI (LLDP/CDP) dla {host}.")
    else:
        logger.info(f"✓ CLI: Znaleziono {len(all_cli_connections)} sąsiadów dla {host} przez LLDP/CDP.")
    return all_cli_connections