# cli_utils.py (Wersja Diagnostyczna v4)
import re
import logging
import os
from typing import List, Dict, Any, Optional, Pattern

from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException

logger = logging.getLogger(__name__)

# --- Stałe awaryjne ---
EMERGENCY_DEFAULT_EXPECT_PATTERN = r"[a-zA-Z0-9\S\.\-]*[#>]"  # Bardzo ogólny prompt
EMERGENCY_NETMIKO_LOG_TEMPLATE = "{host}_netmiko_diagnostic_emergency.log"


def _compile_regex(pattern_str: Optional[str], flags: int = 0, context: str = "unknown regex") -> Optional[
    Pattern[str]]:
    """
    Kompiluje regex.
    Jeśli pattern_str jest None, pusty, lub niepoprawny, loguje błąd i zwraca None.
    """
    if not pattern_str or not pattern_str.strip():
        logger.error(
            f"Błąd kompilacji regex ({context}): Otrzymano pusty lub None pattern_str ('{pattern_str}'). To powinno być obsłużone przez config_loader. Zwracam None.")
        return None
    try:
        compiled_regex = re.compile(pattern_str, flags)
        logger.debug(f"Pomyślnie skompilowano regex ({context}): '{pattern_str}' z flagami {flags}")
        return compiled_regex
    except re.error as e:
        logger.error(
            f"Błąd kompilacji regex ({context}) dla wzorca '{pattern_str}' z flagami {flags}: {e}. Zwracam None.")
        return None
    except Exception as e_generic_compile:  # Złap inne nieoczekiwane błędy podczas kompilacji
        logger.error(
            f"Nieoczekiwany błąd podczas kompilacji regex ({context}) dla wzorca '{pattern_str}': {e_generic_compile}. Zwracam None.",
            exc_info=True)
        return None


def _normalize_interface_name(if_name: str, replacements: Dict[str, str]) -> str:
    if_name = if_name.strip()
    # Sortuj wg długości klucza malejąco, aby np. "TenGigabitEthernet" było sprawdzane przed "GigabitEthernet"
    for long, short in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if if_name.lower().startswith(long.lower()):  # Porównuj case-insensitive
            return short + if_name[len(long):]
    return if_name


def _parse_lldp_output(lldp_output: str, local_hostname: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    connections: List[Dict[str, Any]] = []
    if not lldp_output:
        logger.debug(f"CLI-LLDP: Brak danych LLDP do sparsowania dla {local_hostname}.")
        return connections
    logger.debug(f"CLI-LLDP: Próba parsowania danych LLDP dla {local_hostname} (długość: {len(lldp_output)})...")

    lldp_regex_block_split_pattern = config.get('lldp_regex_block_split')
    re_lldp_block_split = _compile_regex(lldp_regex_block_split_pattern, re.IGNORECASE, context="lldp_block_split")
    if not re_lldp_block_split:
        logger.error(
            f"CLI-LLDP: Krytyczny regex 'lldp_regex_block_split' (wzorzec: '{lldp_regex_block_split_pattern}') nie skompilował się. Przerywam parsowanie LLDP dla {local_hostname}.")
        return connections

    # Kompiluj pozostałe regexy; jeśli się nie skompilują, parsowanie konkretnych pól może zawieść
    re_lldp_local_port_id = _compile_regex(config.get('lldp_regex_local_port_id'), re.MULTILINE | re.IGNORECASE,
                                           context="lldp_local_port_id")
    re_lldp_sys_name = _compile_regex(config.get('lldp_regex_sys_name'), re.MULTILINE | re.IGNORECASE,
                                      context="lldp_sys_name")
    re_lldp_remote_port_id = _compile_regex(config.get('lldp_regex_remote_port_id'), re.MULTILINE | re.IGNORECASE,
                                            context="lldp_remote_port_id")
    re_lldp_remote_port_desc = _compile_regex(config.get('lldp_regex_remote_port_desc'), re.MULTILINE | re.IGNORECASE,
                                              context="lldp_remote_port_desc")
    re_lldp_vlan_id = _compile_regex(config.get('lldp_regex_vlan_id'), re.MULTILINE | re.IGNORECASE,
                                     context="lldp_vlan_id")

    interface_replacements = config.get('interface_name_replacements', {})
    data_to_parse = lldp_output

    if not data_to_parse.strip().lower().startswith('chassis id:'):
        first_chassis_match = re.search(r'Chassis id:', data_to_parse, re.IGNORECASE)
        if first_chassis_match:
            data_to_parse = data_to_parse[first_chassis_match.start():]
        else:
            logger.info(
                f"CLI-LLDP: Dane LLDP dla {local_hostname} nie zaczynają się od 'Chassis id:' i nie znaleziono znacznika.")
            if 'chassis id:' not in data_to_parse.lower():  # Jeśli w ogóle nie ma "Chassis id:"
                logger.warning(
                    f"CLI-LLDP: Słowo kluczowe 'Chassis id:' nie znalezione w danych LLDP dla {local_hostname}. Parsowanie prawdopodobnie się nie powiedzie.")
                return connections

    blocks = re_lldp_block_split.split(data_to_parse)
    if not blocks or (len(blocks) == 1 and not blocks[0].strip()):  # Jeśli split nic nie dał lub tylko pusty string
        logger.warning(
            f"CLI-LLDP: Regex 'lldp_regex_block_split' (wzorzec: '{re_lldp_block_split.pattern if re_lldp_block_split else 'None'}') nie podzielił danych LLDP na użyteczne bloki dla {local_hostname}. Dane wejściowe (fragment):\n{data_to_parse[:300]}")
        return connections

    parsed_count = 0
    for block_content in blocks:
        block_strip = block_content.strip()
        if not block_strip or not block_strip.lower().startswith('chassis id:'):
            if block_strip:
                logger.debug(
                    f"CLI-LLDP: Pomijam blok (nie zaczyna się od 'Chassis id:' lub pusty) dla {local_hostname}:\n{block_strip[:100]}...")
            continue

        # Upewnij się, że kluczowe regexy do ekstrakcji pól są skompilowane
        if not (re_lldp_local_port_id and re_lldp_sys_name and re_lldp_remote_port_id):
            logger.error(
                f"CLI-LLDP: Jeden lub więcej kluczowych regexów do ekstrakcji pól (local_port, sys_name, remote_port) nie jest skompilowany dla {local_hostname}. Pomijam blok.")
            logger.debug(
                f"  Status regexów: local_port_id: {'OK' if re_lldp_local_port_id else 'FAIL'}, sys_name: {'OK' if re_lldp_sys_name else 'FAIL'}, remote_port_id: {'OK' if re_lldp_remote_port_id else 'FAIL'}")
            continue

        local_if_match = re_lldp_local_port_id.search(block_strip)
        remote_sys_match = re_lldp_sys_name.search(block_strip)
        remote_port_id_match = re_lldp_remote_port_id.search(block_strip)

        if not (local_if_match and remote_sys_match and remote_port_id_match):
            logger.debug(f"CLI-LLDP: Pominięto blok - brak kluczowych danych w {local_hostname}.")
            logger.debug(
                f"  Szczegóły dopasowań: local_if={bool(local_if_match)}, remote_sys={bool(remote_sys_match)}, remote_port_id={bool(remote_port_id_match)}")
            logger.debug(f"  Przetwarzany blok (fragment):\n{block_strip[:200]}")
            continue

        local_if_raw = local_if_match.group(1).strip()
        if not local_if_raw or 'not advertised' in local_if_raw.lower(): continue

        local_if = _normalize_interface_name(local_if_raw, interface_replacements)
        remote_sys = remote_sys_match.group(1).strip()
        remote_port_raw = remote_port_id_match.group(1).strip()
        remote_port_desc_val = ""

        if re_lldp_remote_port_desc:  # Sprawdź, czy regex został skompilowany
            remote_port_desc_match = re_lldp_remote_port_desc.search(block_strip)
            if remote_port_desc_match:
                remote_port_desc_val = remote_port_desc_match.group(1).strip()

        chosen_remote_port = remote_port_raw
        if remote_port_desc_val and 'not advertised' not in remote_port_desc_val.lower():
            if (not chosen_remote_port or
                    'not advertised' in chosen_remote_port.lower() or
                    ':' in chosen_remote_port or
                    (len(chosen_remote_port) > 20 and not chosen_remote_port.isalnum())
            ):
                logger.debug(
                    f"CLI-LLDP: Dla {local_hostname} -> {remote_sys}: Port ID ('{remote_port_raw}') jest nieoptymalny. Używam Port Description ('{remote_port_desc_val}').")
                chosen_remote_port = remote_port_desc_val
            elif chosen_remote_port and chosen_remote_port != remote_port_desc_val and len(remote_port_desc_val) < len(
                    chosen_remote_port) and not ':' in remote_port_desc_val:
                logger.debug(
                    f"CLI-LLDP: Dla {local_hostname} -> {remote_sys}: Port ID ('{remote_port_raw}') i Port Description ('{remote_port_desc_val}') są różne. Używam krótszego Port Description.")
                chosen_remote_port = remote_port_desc_val

        if not chosen_remote_port or 'not advertised' in chosen_remote_port.lower(): continue
        remote_if = _normalize_interface_name(chosen_remote_port, interface_replacements)

        vlan_id_str = None
        if re_lldp_vlan_id:  # Sprawdź, czy regex został skompilowany
            vlan_match = re_lldp_vlan_id.search(block_strip)
            if vlan_match and vlan_match.group(1) and vlan_match.group(
                    1).strip():  # Upewnij się, że grupa(1) istnieje przed strip()
                vlan_id_str = vlan_match.group(1).strip()

        connections.append({
            "local_host": local_hostname, "local_if": local_if,
            "neighbor_host": remote_sys, "neighbor_if": remote_if,
            "vlan": vlan_id_str, "via": "CLI-LLDP"
        })
        parsed_count += 1

    if parsed_count > 0:
        logger.info(f"✓ CLI-LLDP: Sparsowano {parsed_count} połączeń LLDP dla {local_hostname}.")
    elif lldp_output and lldp_output.strip():  # Loguj tylko, jeśli było jakieś wyjście
        logger.info(
            f"ⓘ CLI-LLDP: Otrzymano dane LLDP ({len(lldp_output)} znaków), ale nie sparsowano użytecznych połączeń dla {local_hostname}.")
        logger.debug(f"CLI-LLDP: Niesparsowane dane LLDP dla {local_hostname} (fragment):\n{lldp_output[:500]}...")
    return connections


def _parse_cdp_output(cdp_output: str, local_hostname: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    connections: List[Dict[str, Any]] = []
    if not cdp_output or "Device ID" not in cdp_output:
        if cdp_output and "cdp not enabled" in cdp_output.lower():
            logger.info(f"CLI-CDP: CDP nie jest włączone na {local_hostname}.")
        elif cdp_output:  # Loguj tylko, jeśli było jakieś wyjście
            logger.info(f"CLI-CDP: Brak 'Device ID' w wyjściu CDP dla {local_hostname}, lub puste wyjście.")
        return connections
    logger.debug(f"CLI-CDP: Próba parsowania danych CDP dla {local_hostname}...")

    cdp_regex_block_split_pattern = config.get('cdp_regex_block_split')
    re_cdp_block_split = _compile_regex(cdp_regex_block_split_pattern, re.MULTILINE, context="cdp_block_split")
    if not re_cdp_block_split:
        logger.error(
            f"CLI-CDP: Krytyczny regex 'cdp_regex_block_split' (wzorzec: '{cdp_regex_block_split_pattern}') nie skompilował się. Przerywam parsowanie CDP dla {local_hostname}.")
        return connections

    re_cdp_device_id = _compile_regex(config.get('cdp_regex_device_id'), re.IGNORECASE, context="cdp_device_id")
    re_cdp_local_if = _compile_regex(config.get('cdp_regex_local_if'), re.IGNORECASE, context="cdp_local_if")
    re_cdp_remote_if = _compile_regex(config.get('cdp_regex_remote_if'), re.IGNORECASE, context="cdp_remote_if")
    interface_replacements = config.get('interface_name_replacements', {})

    header_match = re.search(r"Device ID\s*:", cdp_output, re.IGNORECASE)
    data_to_parse_cdp = cdp_output
    if header_match:
        line_start_pos = cdp_output.rfind('\n', 0, header_match.start()) + 1
        # Użyj skompilowanego regexa do szukania pierwszego bloku
        first_block_marker_search = re_cdp_block_split.search(cdp_output)
        if first_block_marker_search and first_block_marker_search.start() < line_start_pos:
            data_to_parse_cdp = cdp_output[first_block_marker_search.end():].strip()
            logger.debug(f"CLI-CDP: Usunięto potencjalny nagłówek przed pierwszym blokiem dla {local_hostname}.")

    cdp_blocks = [block.strip() for block in re_cdp_block_split.split(data_to_parse_cdp) if block.strip()]
    if not cdp_blocks:
        logger.warning(
            f"CLI-CDP: Regex 'cdp_regex_block_split' (wzorzec: '{re_cdp_block_split.pattern if re_cdp_block_split else 'None'}') nie podzielił danych CDP na użyteczne bloki dla {local_hostname}.")
        logger.debug(f"CLI-CDP: Dane wejściowe (po ew. usunięciu nagłówka):\n{data_to_parse_cdp[:500]}...")
        return connections

    parsed_count_cdp = 0
    for block_idx, block_content in enumerate(cdp_blocks):
        if not block_content.strip(): continue

        if not (re_cdp_device_id and re_cdp_local_if and re_cdp_remote_if):
            logger.error(
                f"CLI-CDP: Jeden lub więcej kluczowych regexów do ekstrakcji pól (device_id, local_if, remote_if) nie jest skompilowany dla {local_hostname}. Pomijam blok.")
            logger.debug(
                f"  Status regexów: device_id: {'OK' if re_cdp_device_id else 'FAIL'}, local_if: {'OK' if re_cdp_local_if else 'FAIL'}, remote_if: {'OK' if re_cdp_remote_if else 'FAIL'}")
            continue

        dev_id_match = re_cdp_device_id.search(block_content)
        local_if_match = re_cdp_local_if.search(block_content)
        remote_if_match = re_cdp_remote_if.search(block_content)

        if dev_id_match and local_if_match and remote_if_match:
            local_if_raw = local_if_match.group(1).strip().split(',')[0].strip()
            local_if = _normalize_interface_name(local_if_raw, interface_replacements)

            neighbor_host_val_raw = dev_id_match.group(1).strip()
            if '.' in neighbor_host_val_raw and not '(' in neighbor_host_val_raw:
                neighbor_host_val = neighbor_host_val_raw.split('.')[0]
            else:
                neighbor_host_val = neighbor_host_val_raw

            remote_if_raw = remote_if_match.group(1).strip()
            remote_if = _normalize_interface_name(remote_if_raw, interface_replacements)

            if local_if and neighbor_host_val and remote_if:
                connections.append({
                    "local_host": local_hostname, "local_if": local_if,
                    "neighbor_host": neighbor_host_val, "neighbor_if": remote_if,
                    "vlan": None, "via": "CLI-CDP"
                })
                parsed_count_cdp += 1
            else:
                logger.debug(
                    f"CLI-CDP: Pominięto blok {block_idx} - niekompletne dane po normalizacji w {local_hostname}.")
        else:
            logger.debug(f"CLI-CDP: Pominięto blok {block_idx} - brak kluczowych danych w {local_hostname}.")
            logger.debug(
                f"  Szczegóły dopasowań: dev_id={bool(dev_id_match)}, local_if={bool(local_if_match)}, remote_if={bool(remote_if_match)}")
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

    # --- Netmiko Session Log Setup ---
    raw_template_from_config = config.get('cli_netmiko_session_log_template')
    logger.info(f"  CLI: Diagnostyka logów Netmiko dla {host}:")  # Zmieniono na INFO dla widoczności
    logger.info(
        f"    1. Surowa wartość z config['cli_netmiko_session_log_template'] = '{raw_template_from_config}' (typ: {type(raw_template_from_config)})")

    session_log_path = None
    # Upewnij się, że szablon jest stringiem i usuń białe znaki przed sprawdzeniem, czy nie jest pusty
    netmiko_session_log_template_val = str(raw_template_from_config or "").strip()
    logger.info(f"    2. Wartość szablonu po str() i strip(): '{netmiko_session_log_template_val}'")

    if not netmiko_session_log_template_val:
        logger.warning(
            f"  CLI: Szablon logu sesji Netmiko jest PUSTY. Próba użycia awaryjnego szablonu: '{EMERGENCY_NETMIKO_LOG_TEMPLATE}'")
        netmiko_session_log_template_val = EMERGENCY_NETMIKO_LOG_TEMPLATE

    try:
        # Oczyść nazwę hosta dla ścieżki: zamień znaki inne niż alfanumeryczne (bez kropki, myślnika) na podkreślenie
        host_sanitized_for_log_path = re.sub(r'[^\w\.-]', '_', host)
        session_log_path = netmiko_session_log_template_val.format(host=host_sanitized_for_log_path)
        logger.info(f"    3. Potencjalna ścieżka logu po formatowaniu: '{session_log_path}'")

        if session_log_path:  # Sprawdź, czy konstrukcja ścieżki się powiodła
            log_dir = os.path.dirname(session_log_path)
            if log_dir and not os.path.exists(log_dir):  # Jeśli część katalogowa istnieje i katalog nie istnieje
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    logger.info(f"    4. Utworzono katalog dla logów Netmiko: '{log_dir}'")
                except OSError as e_mkdir:
                    logger.error(
                        f"    4. BŁĄD: Nie udało się utworzyć katalogu '{log_dir}': {e_mkdir}. Logowanie sesji Netmiko wyłączone.")
                    session_log_path = None  # Wyłącz, jeśli tworzenie katalogu się nie powiedzie
            elif not log_dir:  # Plik logu w bieżącym katalogu, nie trzeba tworzyć katalogu
                logger.debug(f"    4. Plik logu Netmiko '{session_log_path}' będzie w bieżącym katalogu roboczym.")
        else:  # session_log_path stał się pusty po formatowaniu (mało prawdopodobne z obecną logiką oczyszczania)
            logger.warning(
                f"    3. BŁĄD: session_log_path jest pusty po formatowaniu szablonu '{netmiko_session_log_template_val}'. Logowanie Netmiko wyłączone.")
            session_log_path = None  # Upewnij się, że jest None, jeśli ścieżka jest pusta

        if session_log_path:  # Sprawdź ponownie po potencjalnym niepowodzeniu tworzenia katalogu
            logger.info(f"    5. Finalna ścieżka logu Netmiko: '{session_log_path}'")
        else:
            logger.warning(f"    5. Finalnie logowanie Netmiko jest WYŁĄCZONE dla {host}.")

    except KeyError as e_log_format:
        logger.warning(
            f"  CLI: Błąd formatowania szablonu logu Netmiko ('{netmiko_session_log_template_val}') dla hosta '{host}': {e_log_format}. Logowanie Netmiko wyłączone.")
        session_log_path = None
    except Exception as e_log_path_generic:  # Złap inne nieoczekiwane błędy
        logger.error(
            f"  CLI: Nieoczekiwany błąd przy tworzeniu ścieżki logu Netmiko z szablonu '{netmiko_session_log_template_val}' dla hosta '{host}': {e_log_path_generic}. Logowanie Netmiko wyłączone.",
            exc_info=True)
        session_log_path = None
    # --- Koniec konfiguracji logów sesji Netmiko ---

    device_params: Dict[str, Any] = {
        "device_type": "autodetect",
        "host": host,
        "username": username,
        "password": password,
        "global_delay_factor": config.get('cli_global_delay_factor', 5.0),
        "session_log_file_mode": config.get('cli_session_log_file_mode', 'append'),
        "conn_timeout": config.get('cli_conn_timeout', 75),
        "auth_timeout": config.get('cli_auth_timeout', 90),
        "banner_timeout": config.get('cli_banner_timeout', 75)
    }
    if session_log_path:  # Dodaj tylko, jeśli ścieżka jest prawidłowa i niepusta
        device_params["session_log"] = session_log_path
    else:
        if "session_log" in device_params: del device_params["session_log"]  # Upewnij się, że nie ma, jeśli None
        # Komunikat logowania został już wygenerowany wyżej, jeśli logowanie jest wyłączone

    # Loguj finalne parametry przed połączeniem (pomijając hasło dla bezpieczeństwa)
    params_to_log = {k: v for k, v in device_params.items() if k != 'password'}
    logger.info(f"  CLI: Parametry dla ConnectHandler (hasło pominięte): {params_to_log}")

    all_cli_connections: List[Dict[str, Any]] = []
    net_connect: Optional[ConnectHandler] = None
    effective_device_type = "N/A (przed połączeniem)"
    base_prompt_log = "N/A (przed odczytem)"

    # Skompiluj podstawowy regex do sprawdzania prostego promptu
    re_simple_prompt = _compile_regex(config.get('prompt_regex_simple'), context="prompt_regex_simple")

    # Pobierz domyślny wzorzec expect_string z konfiguracji. KRYTYCZNE, jeśli nie jest ustawiony.
    default_expect_pattern_from_config = str(config.get('cli_default_expect_string_pattern', "")).strip()
    if not default_expect_pattern_from_config:
        logger.critical(
            f"  CLI: KRYTYCZNY PROBLEM - 'cli_default_expect_string_pattern' z konfiguracji jest pusty ('{config.get('cli_default_expect_string_pattern')}'). Może to prowadzić do niekompletnych odpowiedzi z urządzeń. Proszę upewnić się, że jest poprawnie ustawiony w config.ini lub jego domyślna wartość w config_map jest prawidłowa. Używam awaryjnego wzorca: '{EMERGENCY_DEFAULT_EXPECT_PATTERN}'")
        default_expect_pattern_from_config = EMERGENCY_DEFAULT_EXPECT_PATTERN  # Awaryjny fallback
        logger.warning(
            f"  CLI: Używam AWARYJNEGO WZORCA expect_string: '{default_expect_pattern_from_config}' z powodu braku/pustej konfiguracji dla 'cli_default_expect_string_pattern'.")
    logger.info(f"  CLI: Domyślny wzorzec expect_string (po ew. fallbacku): '{default_expect_pattern_from_config}'")

    try:
        logger.info(f"  CLI: Łączenie z {host} (autodetect, gdf={device_params['global_delay_factor']})...")
        net_connect = ConnectHandler(**device_params)
        effective_device_type = net_connect.device_type
        try:
            if net_connect.base_prompt:
                base_prompt_log = net_connect.base_prompt.strip()
        except Exception as e_bp:
            logger.warning(f"  CLI: Wyjątek przy odczycie base_prompt dla {host}: {e_bp}")
            base_prompt_log = "N/A (błąd odczytu)"

        logger.info(f"  CLI: Połączono z {host} (Typ Netmiko: '{effective_device_type}')")
        logger.info(f"  CLI: Netmiko base_prompt: '{base_prompt_log}'")

        system_info_str = ""
        show_ver_expect_str: Optional[str] = None

        # Ustal expect_string dla 'show version'
        is_base_prompt_valid_and_complex = False
        if base_prompt_log and base_prompt_log not in ["N/A (przed odczytem)", "N/A (błąd odczytu)"]:
            # re_simple_prompt może być None, jeśli jego wzorzec nie skompilował się
            if not re_simple_prompt or not re_simple_prompt.fullmatch(base_prompt_log):
                is_base_prompt_valid_and_complex = True

        if is_base_prompt_valid_and_complex:
            show_ver_expect_str = base_prompt_log
            logger.info(
                f"  CLI: Używam złożonego base_prompt ('{base_prompt_log}') jako expect_string dla 'show version'.")
        else:
            show_ver_expect_str = default_expect_pattern_from_config
            logger.info(
                f"  CLI: Używam domyślnego wzorca expect_string ('{show_ver_expect_str}') dla 'show version' (base_prompt: '{base_prompt_log}', re_simple_prompt skompilowany: {bool(re_simple_prompt)}).")

        try:
            show_version_params: Dict[str, Any] = {"read_timeout": config.get('cli_read_timeout_general', 60)}
            if show_ver_expect_str:  # Netmiko obsłuży None lub pusty string dla expect_string używając base_prompt lub swojego domyślnego
                show_version_params["expect_string"] = show_ver_expect_str

            logger.info(f"  CLI: Próba 'show version' na {host} z parametrami: {show_version_params}")
            show_version_output = net_connect.send_command("show version", **show_version_params)

            if show_version_output and isinstance(show_version_output, str):
                system_info_str = show_version_output.lower()
                logger.info(
                    f"  CLI: Otrzymano 'show version' (długość: {len(show_version_output)}, fragment): {system_info_str[:250].replace(chr(10), ' ').replace(chr(13), '')}...")
            else:
                logger.warning(
                    f"  CLI: Nie udało się uzyskać wyjścia 'show version' dla {host} (puste lub zły typ: {type(show_version_output)}). Wyjście (fragment): '{str(show_version_output)[:100]}'")
        except Exception as e_ver:
            logger.warning(
                f"  CLI: Błąd podczas 'show version' na {host} (użyty expect_string: '{show_ver_expect_str}'): {e_ver}",
                exc_info=True)

        # --- UPROSZCZONY expect_string dla LLDP/CDP ---
        # Zawsze używaj default_expect_pattern_from_config, chyba że base_prompt jest złożony.
        final_common_expect_str: Optional[str] = None
        if is_base_prompt_valid_and_complex:  # Ten warunek implikuje, że base_prompt_log jest prawidłowy, a re_simple_prompt był sprawdzony
            final_common_expect_str = base_prompt_log
            logger.info(
                f"  CLI (LLDP/CDP): Używam złożonego base_prompt ('{base_prompt_log}') jako wspólny expect_string.")
        else:
            final_common_expect_str = default_expect_pattern_from_config
            logger.info(
                f"  CLI (LLDP/CDP): Używam domyślnego wzorca ('{default_expect_pattern_from_config}') jako wspólny expect_string (base_prompt: '{base_prompt_log}', re_simple_prompt skompilowany: {bool(re_simple_prompt)}).")

        # Ostateczne sprawdzenie, aby upewnić się, że expect string nie jest pusty, jeśli ma być użyty.
        if final_common_expect_str and not final_common_expect_str.strip():
            logger.error(
                f"  CLI: KRYTYCZNY PROBLEM - final_common_expect_str dla LLDP/CDP stał się pusty dla {host}. To wskazuje na problemy z default_expect_pattern_from_config lub base_prompt. Ustawiam na None, Netmiko użyje swoich wewnętrznych domyślnych.")
            final_common_expect_str = None  # Pozwól Netmiko zdecydować, jeśli jest pusty

        logger.info(f"  CLI: Wspólny expect_string dla komend LLDP/CDP ustalony jako: '{final_common_expect_str}'")
        # --- Koniec UPROSZCZONEGO expect_string ---

        lldp_cmd = "show lldp neighbors detail"
        cdp_cmd = "show cdp neighbors detail"
        run_cdp = True

        # Dostosowanie komend specyficznych dla platformy
        platform_for_log = "Unknown/Default"
        if "extreme" in effective_device_type.lower() or "exos" in system_info_str or "enterasys" in system_info_str:
            platform_for_log = "Extreme"
            lldp_cmd = "show lldp neighbors detailed";
            cdp_cmd = "show cdp neighbor detail"
            try:
                net_connect.send_command_timing("disable clipaging")
            except Exception as e:
                logger.warning(f"  CLI ({platform_for_log}): 'disable clipaging' nie powiodło się: {e}")
        elif "junos" in effective_device_type.lower() or "juniper" in system_info_str:
            platform_for_log = "Junos"
            lldp_cmd = "show lldp neighbors interface all detail"
            run_cdp = config.get("cli_junos_try_cdp", False)
            try:
                net_connect.send_command_timing("set cli screen-length 0", read_timeout=15)
            except Exception as e:
                logger.warning(f"  CLI ({platform_for_log}): 'set cli screen-length 0' nie powiodło się: {e}")
        elif "ios" in effective_device_type.lower() or "catalyst" in system_info_str or "cisco_xe" in effective_device_type.lower() or "nx-os" in system_info_str or "cisco_nxos" in effective_device_type.lower():
            platform_for_log = "Cisco-like (IOS/XE/NX-OS)"
            if "nx-os" not in system_info_str and "cisco_nxos" not in effective_device_type.lower():  # NX-OS zazwyczaj nie potrzebuje 'terminal length 0'
                try:
                    net_connect.send_command_timing("terminal length 0", read_timeout=15)
                except Exception as e:
                    logger.warning(f"  CLI ({platform_for_log}): 'terminal length 0' nie powiodło się: {e}")

        logger.info(
            f"  CLI ({platform_for_log}): Finalne ustawienia komend dla {host} -> LLDP Cmd: '{lldp_cmd}', CDP Cmd: '{cdp_cmd}', Wspólny Expect: '{final_common_expect_str}', Uruchom CDP: {run_cdp}")

        # Wykonanie LLDP
        lldp_params: Dict[str, Any] = {"read_timeout": config.get('cli_read_timeout_lldp_cdp', 180)}
        if final_common_expect_str: lldp_params["expect_string"] = final_common_expect_str
        logger.info(f"  CLI: Wykonywanie LLDP dla {host} z parametrami: {lldp_params}")
        try:
            lldp_raw = net_connect.send_command(lldp_cmd, **lldp_params)
            if lldp_raw and isinstance(lldp_raw, str) and lldp_raw.strip():
                logger.info(f"  CLI-LLDP: Otrzymano surowe dane LLDP dla {host} (długość: {len(lldp_raw)}).")
                conns_lldp = _parse_lldp_output(lldp_raw, host, config)
                all_cli_connections.extend(conns_lldp)
                if not conns_lldp:
                    logger.info(f"  CLI-LLDP: Otrzymano dane LLDP, ale nie sparsowano z nich żadnych połączeń.")
            elif lldp_raw is None or (isinstance(lldp_raw, str) and not lldp_raw.strip()):
                logger.info(f"  CLI-LLDP: Brak danych LLDP (komenda zwróciła None lub pusty string) dla {host}.")
            else:
                logger.warning(
                    f"  CLI-LLDP: Nieoczekiwany typ danych LLDP ({type(lldp_raw)}) dla {host}. Dane (fragment): '{str(lldp_raw)[:100]}'")
        except Exception as e_lldp:
            logger.warning(f"  CLI-LLDP: Błąd podczas komendy LLDP ('{lldp_cmd}') dla {host}: {e_lldp}", exc_info=False)
            logger.debug(f"  CLI-LLDP: Pełny traceback błędu LLDP na {host}:", exc_info=True)

            if ("nx-os" in system_info_str or "cisco_nxos" in effective_device_type.lower()) and \
                    lldp_cmd == "show lldp neighbors detail" and \
                    any(err_keyword in str(e_lldp).lower() for err_keyword in
                        ["invalid", "incomplete", "unrecognized"]):
                logger.info(f"  CLI-LLDP: Ponowna próba LLDP dla NX-OS {host} z komendą 'show lldp neighbors'")
                lldp_cmd_nxos_fallback = "show lldp neighbors"
                try:
                    lldp_raw_fallback = net_connect.send_command(lldp_cmd_nxos_fallback,
                                                                 **lldp_params)  # Użyj tych samych parametrów
                    if lldp_raw_fallback and isinstance(lldp_raw_fallback, str) and lldp_raw_fallback.strip():
                        conns_fb = _parse_lldp_output(lldp_raw_fallback, host, config)
                        all_cli_connections.extend(conns_fb)
                        if not conns_fb: logger.info(
                            f"  CLI-LLDP (fallback NXOS): Otrzymano dane, ale nie sparsowano połączeń.")
                    elif not lldp_raw_fallback or (
                            isinstance(lldp_raw_fallback, str) and not lldp_raw_fallback.strip()):
                        logger.info(f"  CLI-LLDP (fallback NXOS): Brak danych (None lub pusty) dla {host}.")
                except Exception as e_nxos_fallback:
                    logger.warning(
                        f"  CLI-LLDP (fallback NXOS): Błąd komendy '{lldp_cmd_nxos_fallback}' dla {host}: {e_nxos_fallback}",
                        exc_info=False)

        # Wykonanie CDP (warunkowe)
        if not all_cli_connections and run_cdp:
            cdp_params: Dict[str, Any] = {"read_timeout": config.get('cli_read_timeout_lldp_cdp', 180)}
            if final_common_expect_str: cdp_params["expect_string"] = final_common_expect_str
            logger.info(f"  CLI: Wykonywanie CDP dla {host} z parametrami: {cdp_params}")
            try:
                cdp_raw = net_connect.send_command(cdp_cmd, **cdp_params)
                if cdp_raw and isinstance(cdp_raw, str) and cdp_raw.strip():
                    logger.info(f"  CLI-CDP: Otrzymano surowe dane CDP dla {host} (długość: {len(cdp_raw)}).")
                    if "cdp not enabled" in cdp_raw.lower():
                        logger.info(f"  CLI-CDP: CDP nie jest włączone na {host}.")
                    else:
                        conns_cdp = _parse_cdp_output(cdp_raw, host, config)
                        all_cli_connections.extend(conns_cdp)
                        if not conns_cdp:
                            logger.info(f"  CLI-CDP: Otrzymano dane CDP, ale nie sparsowano z nich żadnych połączeń.")
                elif not cdp_raw or (isinstance(cdp_raw, str) and not cdp_raw.strip()):
                    logger.info(f"  CLI-CDP: Brak danych CDP (None lub pusty) dla {host}.")
                else:
                    logger.warning(
                        f"  CLI-CDP: Nieoczekiwany typ danych CDP ({type(cdp_raw)}) dla {host}. Dane (fragment): '{str(cdp_raw)[:100]}'")
            except Exception as e_cdp:
                logger.warning(f"  CLI-CDP: Błąd podczas komendy CDP ('{cdp_cmd}') dla {host}: {e_cdp}", exc_info=False)
                logger.debug(f"  CLI-CDP: Pełny traceback błędu CDP na {host}:", exc_info=True)
        elif not run_cdp:
            logger.info(f"  CLI: CDP pominięte dla {host} (run_cdp jest False).")
        elif all_cli_connections and run_cdp:
            logger.info(f"  CLI: LLDP dostarczyło wyników dla {host}. Pomijam CDP.")

    except NetmikoAuthenticationException as e_auth_main:
        logger.error(f"⚠ Błąd Uwierzytelnienia CLI dla {host}: {e_auth_main}")
    except NetmikoTimeoutException as e_timeout_main:
        logger.error(f"⚠ Błąd Timeoutu CLI dla {host}: {e_timeout_main}")
    except Exception as e_general_main:
        logger.error(f"⚠ Ogólny Błąd CLI z {host}: {e_general_main}", exc_info=True)
    finally:
        if net_connect and net_connect.is_alive():
            try:
                net_connect.disconnect()
                logger.info(f"  CLI: Rozłączono z {host}")
            except Exception as e_disc_final:
                logger.error(f"  CLI Błąd Rozłączenia dla {host}: {e_disc_final}", exc_info=True)
        elif net_connect:
            logger.info(f"  CLI: Sesja Netmiko z {host} nie była aktywna przed próbą rozłączenia.")

    if not all_cli_connections:
        logger.info(f"⟶ CLI: Nie znaleziono sąsiadów CLI (LLDP/CDP) dla {host}.")
    else:
        logger.info(f"✓ CLI: Znaleziono {len(all_cli_connections)} sąsiadów CLI dla {host} przez LLDP/CDP.")
    return all_cli_connections

