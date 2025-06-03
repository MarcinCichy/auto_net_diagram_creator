# config_loader.py
import configparser
import os
import logging
import sys
import json
from typing import Dict, Any, List, Optional, Union, Set
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "config.ini"
DEFAULT_CLI_CREDENTIALS_JSON_FILE = "cli_credentials.json"


def _parse_interface_replacements(value: str) -> Dict[str, str]:
    """Parsuje string 'LongName1=Short1,LongName2=Short2' na słownik."""
    replacements = {}
    if not value:
        return replacements
    try:
        pairs = value.split(',')
        for pair in pairs:
            if '=' in pair:
                long, short = pair.split('=', 1)
                replacements[long.strip()] = short.strip()
    except Exception as e:
        logger.error(f"Błąd parsowania interface_name_replacements '{value}': {e}")
    return replacements


def _parse_string_set(value: str) -> Set[str]:
    """Parsuje string 'item1,item2, item3' na zbiór stringów."""
    if not value:
        return set()
    return {item.strip().lower() for item in value.split(',') if item.strip()}


def _get_typed_value(config_parser: configparser.ConfigParser, section: str, option: str,
                     expected_type: type, default_value: Optional[Any] = None) -> Any:
    """
    Pobiera wartość z konfiguracji, konwertuje na oczekiwany typ.
    Jeśli opcja nie istnieje w pliku .ini, używa default_value.
    Jeśli opcja istnieje w .ini ale jest pusta (dla stringów), a default_value jest
    niepustym stringiem, używa default_value (ważne dla regexów i szablonów).
    Automatycznie usuwa cudzysłowy otaczające wartości stringów z .ini.
    """
    source_log = f"z pliku .ini ({section}/{option})"
    try:
        if expected_type == bool:
            val = config_parser.getboolean(section, option)
        elif expected_type == int:
            val = config_parser.getint(section, option)
        elif expected_type == float:
            val = config_parser.getfloat(section, option)
        elif expected_type == list:
            value_str_raw = config_parser.get(section, option)
            # Usuń cudzysłowy, jeśli są, przed podziałem
            value_str = value_str_raw.strip()
            if (value_str.startswith('"') and value_str.endswith('"')) or \
                    (value_str.startswith("'") and value_str.endswith("'")):
                value_str = value_str[1:-1]
            val = [item.strip() for item in value_str.split(',') if item.strip()]
        elif expected_type == dict and option == "interface_name_replacements":
            value_str_raw = config_parser.get(section, option)
            value_str = value_str_raw.strip()
            if (value_str.startswith('"') and value_str.endswith('"')) or \
                    (value_str.startswith("'") and value_str.endswith("'")):
                value_str = value_str[1:-1]
            val = _parse_interface_replacements(value_str)
        elif expected_type == set:
            value_str_raw = config_parser.get(section, option)
            value_str = value_str_raw.strip()
            if (value_str.startswith('"') and value_str.endswith('"')) or \
                    (value_str.startswith("'") and value_str.endswith("'")):
                value_str = value_str[1:-1]
            val = _parse_string_set(value_str)
        elif expected_type == str:
            val_str_raw = config_parser.get(section, option)
            val_str = val_str_raw.strip()
            if (val_str.startswith('"') and val_str.endswith('"')) or \
                    (val_str.startswith("'") and val_str.endswith("'")):
                val_str = val_str[1:-1]
                logger.debug(
                    f"Usunięto cudzysłowy otaczające dla opcji '{option}' w sekcji '{section}'. Oryginalna wartość z .ini: '{val_str_raw}', po usunięciu: '{val_str}'")

            if not val_str.strip() and default_value is not None and isinstance(default_value,
                                                                                str) and default_value.strip():
                logger.debug(
                    f"Opcja '{option}' w sekcji '{section}' pliku .ini jest pusta (po ew. usunięciu cudzysłowów). Używam wartości domyślnej z kodu: '{default_value}'")
                return default_value
            val = val_str
        else:
            val = config_parser.get(section, option)
        return val
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default_value
    except ValueError as e:
        logger.error(
            f"Błąd konwersji wartości dla {section}/{option} {source_log} na typ {expected_type}: {e}. Używam wartości domyślnej: {default_value}")
        return default_value
    except Exception as e_get_typed:
        logger.error(
            f"Nieoczekiwany błąd w _get_typed_value dla {section}/{option}: {e_get_typed}. Używam wartości domyślnej: {default_value}",
            exc_info=True)
        return default_value


def _load_cli_credentials_from_json(filepath: str) -> Optional[Dict[str, Any]]:
    """Wczytuje poświadczenia CLI z pliku JSON."""
    logger.info(f"Próba wczytania poświadczeń CLI z pliku JSON: '{filepath}'")
    if not os.path.exists(filepath):
        logger.warning(f"Plik JSON z poświadczeniami CLI '{filepath}' nie został znaleziony.")
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            creds = json.load(f)

        if not isinstance(creds, dict):
            logger.error(
                f"Główny element w pliku JSON z poświadczeniami '{filepath}' nie jest słownikiem (otrzymano {type(creds)}).")
            return None

        defaults_creds = creds.get("defaults")
        if defaults_creds is None:
            logger.debug(f"Brak sekcji 'defaults' w pliku poświadczeń JSON '{filepath}'. Ustawiam na pusty słownik.")
            creds["defaults"] = {}
        elif not isinstance(defaults_creds, dict):
            logger.warning(
                f"Sekcja 'defaults' w pliku poświadczeń JSON '{filepath}' nie jest słownikiem (jest {type(defaults_creds)}). Ignoruję ją.")
            creds["defaults"] = {}

        devices_list = creds.get("devices")
        if devices_list is None:
            logger.debug(f"Brak sekcji 'devices' w pliku poświadczeń JSON '{filepath}'. Ustawiam na pustą listę.")
            creds["devices"] = []
        elif not isinstance(devices_list, list):
            logger.warning(
                f"Sekcja 'devices' w pliku poświadczeń JSON '{filepath}' nie jest listą (jest {type(devices_list)}). Ignoruję ją.")
            creds["devices"] = []
        else:
            valid_devices = []
            for i, device_entry in enumerate(devices_list):
                if not isinstance(device_entry, dict) or \
                        not device_entry.get("identifier") or \
                        not device_entry.get("cli_user") or \
                        not device_entry.get("cli_pass"):
                    logger.warning(
                        f"Niekompletny lub nieprawidłowy wpis dla urządzenia #{i} w pliku '{filepath}'. Wymagane pola: identifier, cli_user, cli_pass. Pomijam wpis: {device_entry}")
                    continue

                match_val = str(device_entry.get("match", "exact")).lower().strip()
                if match_val not in ["exact", "regex"]:
                    logger.warning(
                        f"Nieprawidłowa wartość 'match' ('{device_entry.get('match')}') dla urządzenia '{device_entry.get('identifier')}'. Ustawiam na 'exact'.")
                    match_val = "exact"
                device_entry["match"] = match_val
                valid_devices.append(device_entry)
            creds["devices"] = valid_devices

        logger.info(
            f"Pomyślnie wczytano i przetworzono poświadczenia CLI z pliku JSON: '{filepath}'. Domyślne: {'TAK' if creds.get('defaults') else 'NIE'}, Wpisy urządzeń: {len(creds.get('devices', []))}.")
        return creds
    except json.JSONDecodeError as e:
        logger.error(
            f"Błąd parsowania pliku JSON z poświadczeniami CLI '{filepath}': {e}. Poświadczenia CLI nie zostaną wczytane z tego pliku.")
        return None
    except Exception as e:
        logger.error(
            f"Nieoczekiwany błąd podczas odczytu pliku JSON z poświadczeniami CLI '{filepath}': {e}. Poświadczenia CLI nie zostaną wczytane z tego pliku.",
            exc_info=True)
        return None


def load_config(config_path: str = DEFAULT_CONFIG_FILE) -> Dict[str, Any]:
    config_parser = configparser.ConfigParser(allow_no_value=True, inline_comment_prefixes=('#', ';'))
    parsed_config: Dict[str, Any] = {}
    config_file_found_and_parsed = False

    if not os.path.exists(config_path):
        logger.warning(
            f"Plik konfiguracyjny '{config_path}' nie został znaleziony. Użyte zostaną wartości domyślne z kodu.")
    else:
        try:
            files_read = config_parser.read(config_path, encoding='utf-8')
            if not files_read:
                logger.warning(
                    f"Plik konfiguracyjny '{config_path}' jest pusty lub nie można go było odczytać. Użyte zostaną wartości domyślne.")
            else:
                logger.info(f"Pomyślnie wczytano plik konfiguracyjny: {config_path}")
                config_file_found_and_parsed = True
        except configparser.Error as e:
            logger.error(
                f"Błąd parsowania pliku konfiguracyjnego '{config_path}': {e}. Użyte zostaną wartości domyślne.")

    config_map = {
        "log_level": ("DEFAULT", "log_level", str, "INFO"),
        "log_to_file": ("DEFAULT", "log_to_file", bool, True),
        "log_file_name": ("DEFAULT", "log_file_name", str, "auto_net_diagram_creator.log"),
        "ip_list_file": ("DEFAULT", "ip_list_file", str, "ip_list.txt"),
        "connections_txt_file": ("DEFAULT", "connections_txt_file", str, "connections.txt"),
        "connections_json_file": ("DEFAULT", "connections_json_file", str, "connections.json"),
        "diagram_template_file": ("DEFAULT", "diagram_template_file", str, "switch.drawio"),
        "diagram_output_drawio_file": ("DEFAULT", "diagram_output_drawio_file", str, "network_diagram.drawio"),
        "diagram_output_svg_file": ("DEFAULT", "diagram_output_svg_file", str, "network_diagram.svg"),
        "api_timeout": ("LibreNMS", "api_timeout", int, 20),
        "verify_ssl": ("LibreNMS", "verify_ssl", bool, False),
        "default_snmp_communities": ("Discovery", "default_snmp_communities", list, ["public"]),
        "snmp_timeout": ("Discovery", "snmp_timeout", int, 5),
        "snmp_retries": ("Discovery", "snmp_retries", int, 1),
        "enable_cli_discovery": ("Discovery", "enable_cli_discovery", bool, True),
        "cli_credentials_json_file": ("Discovery", "cli_credentials_json_file", str, DEFAULT_CLI_CREDENTIALS_JSON_FILE),
        "cli_global_delay_factor": ("CLI", "global_delay_factor", float, 5.0),
        "cli_session_log_file_mode": ("CLI", "session_log_file_mode", str, "append"),
        "cli_conn_timeout": ("CLI", "conn_timeout", int, 75),
        "cli_auth_timeout": ("CLI", "auth_timeout", int, 90),
        "cli_banner_timeout": ("CLI", "banner_timeout", int, 75),
        "cli_read_timeout_general": ("CLI", "read_timeout_general", int, 60),
        "cli_read_timeout_lldp_cdp": ("CLI", "read_timeout_lldp_cdp", int, 180),
        "cli_default_expect_string_pattern": ("CLI", "default_expect_string_pattern", str, r"[a-zA-Z0-9\S\.\-]*[#>]"),
        "cli_netmiko_session_log_template": ("CLI", "netmiko_session_log_template", str, "{host}_netmiko_session.log"),
        "cli_junos_try_cdp": ("CLI", "cli_junos_try_cdp", bool, False),
        "prompt_regex_slot_sys": ("CLI", "prompt_regex_slot_sys", str, r'(?:\*\s*)?Slot-\d+\s+[\w.-]+\s*#\s*$'),
        "prompt_regex_simple": ("CLI", "prompt_regex_simple", str, r"^[a-zA-Z0-9][\w.-]*[>#]\s*$"),
        "prompt_regex_nxos": ("CLI", "prompt_regex_nxos", str, r"^[a-zA-Z0-9][\w.-]*#\s*$"),
        "prompt_regex_ios": ("CLI", "prompt_regex_ios", str, r"^[a-zA-Z0-9][\w.-]*[>#]\s*$"),
        "lldp_regex_block_split": ("CLI", "lldp_regex_block_split", str, r'\n\s*(?=Chassis id:)'),
        "lldp_regex_local_port_id": ("CLI", "lldp_regex_local_port_id", str, r'^Local Port id:\s*(.+?)\s*$'),
        "lldp_regex_sys_name": ("CLI", "lldp_regex_sys_name", str, r'^System Name:\s*(.+?)\s*$'),
        "lldp_regex_remote_port_id": ("CLI", "lldp_regex_remote_port_id", str, r'^Port id:\s*(.+?)\s*$'),
        "lldp_regex_remote_port_desc": ("CLI", "lldp_regex_remote_port_desc", str, r'^Port Description:\s*(.+?)\s*$'),
        "lldp_regex_vlan_id": ("CLI", "lldp_regex_vlan_id", str, r'^(?:Port and )?Vlan ID:\s*([0-9]+)\s*$'),
        "cdp_regex_block_split": ("CLI", "cdp_regex_block_split", str, r'-{10,}\s*$'),
        "cdp_regex_device_id": ("CLI", "cdp_regex_device_id", str, r'Device ID:\s*(\S+)'),
        "cdp_regex_local_if": ("CLI", "cdp_regex_local_if", str, r'Interface:\s*([^,]+(?:,\s*port\s+\S+)?)'),
        "cdp_regex_remote_if": ("CLI", "cdp_regex_remote_if", str, r'(?:Port ID|Outgoing Port):\s*(\S+)'),
        "interface_name_replacements": ("CLI", "interface_name_replacements", dict,
                                        {"GigabitEthernet": "Gi", "TenGigabitEthernet": "Te", "FastEthernet": "Fa",
                                         "Port-channel": "Po"}),
        "physical_name_patterns_re": ("PortClassification", "physical_name_patterns_re", str,
                                      r'^(Eth|Gi|Te|Fa|Hu|Twe|Fo|mgmt|Management|Serial|Port\s?\d|SFP|XFP|QSFP|em\d|ens\d|eno\d|enp\d+s\d+|ge-|xe-|et-|bri|lan\d|po\d+|Stk|Stack|CHASSIS|StackPort)'),
        "stack_port_pattern_re": ("PortClassification", "stack_port_pattern_re", str, r'^[a-zA-Z]+[-]?\d+/\d+(/\d+)+$'),
        "logical_name_patterns_re": ("PortClassification", "logical_name_patterns_re", str,
                                     r'^(Vlan|vl|Loopback|Lo|lo\d*|Port-channel|Po|Bundle-Ether|ae|Tunnel|Tun|Null|Nu|Cpu|Fabric|Voice|Async|Group-Async|ipsec|gre|sit|pimreg|mgmt[1-9]|Irq|Service-Engine|Dialer|Virtual-Access|Virtual-Template|Subinterface|BVI|BV|Cellular)|.*\.\d+$'),
        "physical_types_iana_set": ("PortClassification", "physical_types_iana_set", set,
                                    {'ethernetcsmacd', 'fastether', 'gigabitethernet', 'fastetherfx', 'infinitiband',
                                     'sonet', 'sdsl', 'hdsl', 'shdsl', 'adsl', 'radsl', 'vdsl', 'ieee80211',
                                     'opticalchannel', 'fibrechannel', 'propvirtual', 'proppointtopointserial', 'ppp',
                                     'eon', 'tokenring', 'atm', 'framerelay', 'hssi', 'hippi', 'isdn', 'x25', 'aal5',
                                     'voiceem', 'voicefxo', 'voicefxs', 'digitalpowerline', 'modem', 'serial',
                                     'docscablemaclayer', 'docscabledownstream', 'docscableupstream', 'ieee8023adlag'}),
        "logical_types_iana_set": ("PortClassification", "logical_types_iana_set", set,
                                   {'l3ipvlan', 'softwareloopback', 'tunnel', 'propmultiplexor', 'bridge', 'other',
                                    'l2vlan', 'voiceoverip', 'atmsubinterface', 'virtualipaddress', 'mpovalink',
                                    'ianavielf'}),
        "devices_per_row": ("DiagramLayout", "devices_per_row", int, 3),
        "grid_margin_x": ("DiagramLayout", "grid_margin_x", int, 450),
        "grid_margin_y": ("DiagramLayout", "grid_margin_y", int, 350),
        "grid_start_offset_x": ("DiagramLayout", "grid_start_offset_x", float, 200.0),
        "grid_start_offset_y": ("DiagramLayout", "grid_start_offset_y", float, 100.0),
        "drawio_grid_size": ("DiagramLayout", "drawio_grid_size", int, 10),
        "port_horizontal_spacing": ("DiagramLayout", "port_horizontal_spacing", float, 10.0),
        "port_vertical_spacing": ("DiagramLayout", "port_vertical_spacing", float, 15.0),
        "port_row_offset_y": ("DiagramLayout", "port_row_offset_y", float, 7.0),
        "chassis_padding_x": ("DiagramLayout", "chassis_padding_x", float, 15.0),
        "chassis_padding_y": ("DiagramLayout", "chassis_padding_y", float, 7.0),
        "min_chassis_width": ("DiagramLayout", "min_chassis_width", float, 100.0),
        "min_chassis_height": ("DiagramLayout", "min_chassis_height", float, 60.0),
        "default_chassis_height_no_ports": ("DiagramLayout", "default_chassis_height_no_ports", float, 40.0),
        "max_physical_ports_for_chassis_display": ("DiagramLayout", "max_physical_ports_for_chassis_display", int, 110),
        "default_ports_per_row_normal": ("DiagramLayout", "default_ports_per_row_normal", int, 28),
        "default_ports_per_row_large_device": ("DiagramLayout", "default_ports_per_row_large_device", int, 55),
        "stack_detection_threshold_factor": ("DiagramLayout", "stack_detection_threshold_factor", int, 2),
        "stack_detection_threshold_offset": ("DiagramLayout", "stack_detection_threshold_offset", int, 4),
        "port_width": ("DiagramElements", "port_width", float, 20.0),
        "port_height": ("DiagramElements", "port_height", float, 20.0),
        "waypoint_offset": ("DiagramElements", "waypoint_offset", float, 20.0),
        "logical_if_list_max_height": ("DiagramElements", "logical_if_list_max_height", float, 150.0),
        "physical_port_list_max_height": ("DiagramElements", "physical_port_list_max_height", float, 200.0),
        "label_line_height": ("DiagramElements", "label_line_height", float, 10.0),
        "label_padding": ("DiagramElements", "label_padding", float, 4.0),
        "port_alias_line_extension": ("DiagramElements", "port_alias_line_extension", float, 30.0),
        "port_alias_label_offset_from_line": ("DiagramElements", "port_alias_label_offset_from_line", float, 2.0),
        "port_alias_label_x_offset_from_line_center": ("DiagramElements", "port_alias_label_x_offset_from_line_center",
                                                       float, 5.0),
        "info_label_margin_from_chassis": ("DiagramElements", "info_label_margin_from_chassis", float, 30.0),
        "info_label_min_width": ("DiagramElements", "info_label_min_width", float, 180.0),
        "info_label_max_width": ("DiagramElements", "info_label_max_width", float, 280.0),
        "svg_default_font_family": ("SVGSpecific", "svg_default_font_family", str, "Arial, Helvetica, sans-serif"),
        "svg_info_label_padding": ("SVGSpecific", "svg_info_label_padding", str, "5px"),
        "svg_default_text_color": ("SVGSpecific", "default_text_color", str, "black"),
        "svg_port_label_font_size": ("SVGSpecific", "port_label_font_size", str, "8px"),
        "svg_alias_font_size": ("SVGSpecific", "alias_font_size", str, "7.5px"),
        "svg_info_title_font_size": ("SVGSpecific", "info_title_font_size", str, "8.5px"),
        "svg_info_text_font_size": ("SVGSpecific", "info_text_font_size", str, "8px"),
        "svg_connection_label_font_size": ("SVGSpecific", "connection_label_font_size", str, "7.5px"),
        "svg_info_hr_color": ("SVGSpecific", "info_hr_color", str, "#D0D0D0"),
    }

    for key_name, (section_ini, option_ini, exp_type, default_val_code) in config_map.items():
        parsed_config[key_name] = _get_typed_value(config_parser, section_ini, option_ini, exp_type, default_val_code)

    if config_file_found_and_parsed:
        config_to_log_debug = {
            k: (f"{type(v).__name__} (len:{len(v)})" if isinstance(v, (list, dict, set)) and len(str(v)) > 60 else v)
            for k, v in parsed_config.items()
            if not (isinstance(v, str) and ('regex' in k or len(v) > 100))
        }
        logger.debug("Konfiguracja po wczytaniu z .ini (fragment, przed .env): %s", config_to_log_debug)

    return parsed_config


def get_env_config(env_file_path: str = ".env", config_ini_path: str = DEFAULT_CONFIG_FILE) -> Dict[str, Any]:
    app_config = load_config(config_ini_path)
    logger.debug(f"Konfiguracja bazowa załadowana z '{config_ini_path}' (lub domyślnych).")

    dotenv_path_actual = env_file_path if os.path.exists(env_file_path) else None
    if dotenv_path_actual:
        load_dotenv(dotenv_path=dotenv_path_actual, override=True)
        logger.info(f"Pomyślnie załadowano zmienne środowiskowe z pliku: {dotenv_path_actual}")
    else:
        logger.info(
            f"Plik .env ('{env_file_path}') nie został znaleziony. Używam tylko zmiennych systemowych (jeśli istnieją) i konfiguracji z .ini.")

    env_vars_to_override_ini = {
        "LIBRENMS_BASE_URL": "base_url",
        "LIBRENMS_API_KEY": "api_key",
        "LOG_LEVEL": "log_level",
        "API_TIMEOUT": "api_timeout",
        "VERIFY_SSL": "verify_ssl",
    }

    for env_var_name, config_key_name in env_vars_to_override_ini.items():
        env_value = os.getenv(env_var_name)
        if env_value is not None:
            original_value_from_ini_or_default = app_config.get(config_key_name)
            try:
                if isinstance(original_value_from_ini_or_default, str) and \
                        original_value_from_ini_or_default.strip() and \
                        not env_value.strip():
                    logger.debug(
                        f"Zmienna środowiskowa '{env_var_name}' jest pusta, ale wartość z .ini/domyślna dla '{config_key_name}' ('{original_value_from_ini_or_default}') nie jest. Zachowuję wartość z .ini/domyślną.")
                    converted_value = original_value_from_ini_or_default
                elif config_key_name == "verify_ssl":
                    converted_value = env_value.lower() == 'true'
                elif config_key_name == "api_timeout" and env_value.isdigit():
                    converted_value = int(env_value)
                else:
                    converted_value = env_value

                if converted_value != original_value_from_ini_or_default:
                    logger.info(
                        f"Nadpisano '{config_key_name}' wartością ze zmiennej środowiskowej '{env_var_name}'. Nowa wartość: '{converted_value}', poprzednia: '{original_value_from_ini_or_default}'.")
                    app_config[config_key_name] = converted_value
            except Exception as e_conv:
                logger.error(
                    f"Błąd konwersji wartości '{env_value}' dla zmiennej '{env_var_name}' (klucz config: '{config_key_name}'): {e_conv}. Pozostawiono wartość z .ini/domyślną.")

    if not app_config.get("base_url"):
        msg = "KRYTYCZNY BŁĄD: LIBRENMS_BASE_URL (klucz: base_url) nie jest ustawiony w .env ani poprawnie zdefiniowany w konfiguracji."
        logger.critical(msg)
        raise ValueError(msg)
    if not app_config.get("api_key"):
        msg = "KRYTYCZNY BŁĄD: LIBRENMS_API_KEY (klucz: api_key) nie jest ustawiony w .env ani poprawnie zdefiniowany w konfiguracji."
        logger.critical(msg)
        raise ValueError(msg)

    # --- Ładowanie poświadczeń CLI ---
    cli_credentials_json_file_path_raw = app_config.get("cli_credentials_json_file", DEFAULT_CLI_CREDENTIALS_JSON_FILE)
    cli_credentials_json_file_path = str(
        cli_credentials_json_file_path_raw or "").strip()  # Upewnij się, że to string i usuń białe znaki

    # Usuń cudzysłowy, jeśli są częścią odczytanej ścieżki
    if (cli_credentials_json_file_path.startswith('"') and cli_credentials_json_file_path.endswith('"')) or \
            (cli_credentials_json_file_path.startswith("'") and cli_credentials_json_file_path.endswith("'")):
        cli_credentials_json_file_path = cli_credentials_json_file_path[1:-1]
        logger.debug(
            f"Usunięto cudzysłowy otaczające ze ścieżki pliku poświadczeń CLI. Nowa ścieżka: '{cli_credentials_json_file_path}'")

    logger.info(
        f"Próba wczytania poświadczeń CLI z pliku JSON skonfigurowanego jako: '{cli_credentials_json_file_path}'")

    cli_creds_from_json = _load_cli_credentials_from_json(cli_credentials_json_file_path)

    if cli_creds_from_json:
        app_config['cli_credentials'] = cli_creds_from_json
        logger.info(f"Poświadczenia CLI załadowane z pliku JSON: '{cli_credentials_json_file_path}'.")
    else:
        logger.warning(
            f"Nie udało się załadować poświadczeń CLI z pliku JSON ('{cli_credentials_json_file_path}'). Próba załadowania z zmiennych środowiskowych .env jako fallback...")
        cli_credentials_structure_env = {"defaults": {}, "devices": []}
        default_cli_user_env = os.getenv("CLI_USER_DEFAULT")
        default_cli_pass_env = os.getenv("CLI_PASS_DEFAULT")
        if default_cli_user_env and default_cli_pass_env:
            cli_credentials_structure_env["defaults"]["cli_user"] = default_cli_user_env
            cli_credentials_structure_env["defaults"]["cli_pass"] = default_cli_pass_env
            logger.debug("Wczytano domyślne poświadczenia CLI z .env (fallback).")

        i = 1
        while True:
            dev_id_env = os.getenv(f"CLI_DEVICE_{i}_ID")
            dev_user_env = os.getenv(f"CLI_DEVICE_{i}_USER")
            dev_pass_env = os.getenv(f"CLI_DEVICE_{i}_PASS")
            dev_match_env = os.getenv(f"CLI_DEVICE_{i}_MATCH", "exact")

            if dev_id_env and dev_user_env and dev_pass_env:
                cli_credentials_structure_env["devices"].append({
                    "identifier": dev_id_env,
                    "match": dev_match_env.lower(),
                    "cli_user": dev_user_env,
                    "cli_pass": dev_pass_env
                })
                i += 1
            else:
                if dev_id_env and not (dev_user_env and dev_pass_env):
                    logger.warning(
                        f"Niekompletne specyficzne poświadczenia CLI z .env dla CLI_DEVICE_{i}_ID='{dev_id_env}'. Pomijam ten wpis.")
                if i == 1 and not dev_id_env:
                    logger.debug("Nie znaleziono żadnych specyficznych wpisów CLI_DEVICE_* w .env (fallback).")
                break

        app_config['cli_credentials'] = cli_credentials_structure_env
        if cli_credentials_structure_env['devices'] or cli_credentials_structure_env['defaults']:
            logger.info(
                f"Wczytano {len(cli_credentials_structure_env['devices'])} specyficznych wpisów poświadczeń CLI i {'domyślne ' if cli_credentials_structure_env['defaults'] else 'brak domyślnych '}poświadczeń z .env (fallback).")
        else:
            logger.info("Nie znaleziono żadnych poświadczeń CLI ani w pliku JSON, ani w .env (fallback).")
    # --- Koniec ładowania poświadczeń CLI ---

    logger.info("Konfiguracja aplikacji wczytana i połączona.")
    config_to_log_final = {
        k: (f"{type(v).__name__} (len:{len(v)})" if isinstance(v, (list, dict, set)) and len(str(v)) > 60 else v)
        for k, v in app_config.items()
        if not (isinstance(v, str) and ('regex' in k or len(v) > 100)) and k not in ['api_key', 'cli_credentials']
    }
    cli_creds_log = app_config.get('cli_credentials', {})
    if cli_creds_log.get('defaults'):
        config_to_log_final['cli_credentials_defaults_user'] = cli_creds_log['defaults'].get('cli_user', 'N/A')
    else:
        config_to_log_final['cli_credentials_defaults_user'] = 'Brak (z JSON/env)'

    if cli_creds_log.get('devices'):
        config_to_log_final['cli_credentials_devices_count'] = len(cli_creds_log['devices'])
    else:
        config_to_log_final['cli_credentials_devices_count'] = 0

    logger.debug("Finalna konfiguracja aplikacji (fragment, bez sekretów): %s", config_to_log_final)
    return app_config


def get_communities_to_try(config: Dict[str, Any]) -> List[str]:
    default_communities = config.get("default_snmp_communities", [])
    if not isinstance(default_communities, list):
        logger.warning(
            f"Wartość 'default_snmp_communities' w konfiguracji nie jest listą ({type(default_communities)}). Używam pustej listy.")
        return []
    return default_communities


if __name__ == '__main__':
    test_logger_main = logging.getLogger()
    test_logger_main.setLevel(logging.DEBUG)
    if not test_logger_main.hasHandlers():
        test_handler_main = logging.StreamHandler(sys.stdout)
        test_formatter_main = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(lineno)d)')
        test_handler_main.setFormatter(test_formatter_main)
        test_logger_main.addHandler(test_handler_main)
    else:
        for handler in test_logger_main.handlers:
            if isinstance(handler, logging.StreamHandler):
                test_formatter_main = logging.Formatter(
                    '%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(lineno)d)')
                handler.setFormatter(test_formatter_main)

    logger.info("Testowanie config_loader.py...")

    TEST_CREDS_JSON_FILE_MAIN = "test_cli_creds_main.json"
    TEST_CONFIG_INI_MAIN = "config.test_main.ini"
    TEST_ENV_MAIN = ".env.test_main"

    EXPECTED_DEFAULT_LLDP_BLOCK_SPLIT = r'\n\s*(?=Chassis id:)'
    EXPECTED_DEFAULT_NETMIKO_LOG_TEMPLATE = "{host}_netmiko_session.log"
    EXPECTED_DEFAULT_IP_LIST_FILE = "ip_list.txt"
    EXPECTED_DEFAULT_SNMP_COMMUNITIES = ["public"]

    with open(TEST_CREDS_JSON_FILE_MAIN, "w", encoding="utf-8") as f_json:
        json.dump({
            "defaults": {"cli_user": "json_default_user", "cli_pass": "json_default_pass"},
            "devices": [
                {"identifier": "router.json", "match": "exact", "cli_user": "json_user1", "cli_pass": "json_pass1"}]
        }, f_json)

    with open(TEST_CONFIG_INI_MAIN, "w", encoding="utf-8") as f_ini:
        f_ini.write("[Discovery]\n")
        f_ini.write(f'cli_credentials_json_file = "{TEST_CREDS_JSON_FILE_MAIN}"\n')
        f_ini.write("[DEFAULT]\nlog_level = DEBUG\n")
        f_ini.write("[CLI]\n")
        f_ini.write("lldp_regex_block_split = \n")
        f_ini.write("cli_netmiko_session_log_template = \n")

    with open(TEST_ENV_MAIN, "w", encoding="utf-8") as f_env:
        f_env.write("LIBRENMS_BASE_URL=http://test.com\n")
        f_env.write("LIBRENMS_API_KEY=testkey\n")

    logger.info("--- Test 1: Ładowanie poświadczeń z JSON (plik istnieje, cudzysłowy w .ini) ---")
    config_json = get_env_config(env_file_path=TEST_ENV_MAIN, config_ini_path=TEST_CONFIG_INI_MAIN)
    assert config_json['cli_credentials']['defaults'].get('cli_user') == "json_default_user"
    assert len(config_json['cli_credentials']['devices']) == 1
    assert config_json['cli_credentials']['devices'][0]['identifier'] == "router.json"
    logger.info("  Test 1: Poświadczenia z JSON załadowane poprawnie (cudzysłowy usunięte).")

    assert config_json.get("lldp_regex_block_split") == EXPECTED_DEFAULT_LLDP_BLOCK_SPLIT
    logger.info(
        f"  Test 1 (pusty lldp_regex_block_split w .ini): OK, użyto domyślnego '{EXPECTED_DEFAULT_LLDP_BLOCK_SPLIT}'.")

    assert config_json.get("cli_netmiko_session_log_template") == EXPECTED_DEFAULT_NETMIKO_LOG_TEMPLATE
    logger.info(
        f"  Test 1 (pusty cli_netmiko_session_log_template w .ini, brak w .env): OK, użyto domyślnego '{EXPECTED_DEFAULT_NETMIKO_LOG_TEMPLATE}'.")

    if os.path.exists(TEST_CREDS_JSON_FILE_MAIN): os.remove(TEST_CREDS_JSON_FILE_MAIN)

    with open(TEST_ENV_MAIN, "w", encoding="utf-8") as f_env:
        f_env.write("LIBRENMS_BASE_URL=http://test.com\n")
        f_env.write("LIBRENMS_API_KEY=testkey\n")
        f_env.write("CLI_USER_DEFAULT=env_user_def\n")
        f_env.write("CLI_PASS_DEFAULT=env_pass_def\n")

    logger.info("--- Test 2: Ładowanie poświadczeń (plik JSON nie istnieje, fallback do .env) ---")
    config_env_fallback = get_env_config(env_file_path=TEST_ENV_MAIN, config_ini_path=TEST_CONFIG_INI_MAIN)
    assert config_env_fallback['cli_credentials']['defaults'].get('cli_user') == "env_user_def"
    logger.info("  Test 2: Fallback do .env dla poświadczeń zadziałał poprawnie.")

    if os.path.exists(TEST_CREDS_JSON_FILE_MAIN): os.remove(TEST_CREDS_JSON_FILE_MAIN)
    if os.path.exists(TEST_CONFIG_INI_MAIN): os.remove(TEST_CONFIG_INI_MAIN)
    if os.path.exists(TEST_ENV_MAIN): os.remove(TEST_ENV_MAIN)
    if os.path.exists(DEFAULT_CLI_CREDENTIALS_JSON_FILE): os.remove(DEFAULT_CLI_CREDENTIALS_JSON_FILE)
    logger.info("Testowanie config_loader.py zakończone.")

