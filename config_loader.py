# config_loader.py
import configparser
import os
import logging
from typing import Dict, Any, List, Optional, Union
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "config.ini"

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

def _get_typed_value(config: configparser.ConfigParser, section: str, option: str,
                     expected_type: type, default_value: Optional[Any] = None) -> Any:
    """Pobiera wartość z konfiguracji i konwertuje na oczekiwany typ."""
    try:
        if expected_type == bool:
            return config.getboolean(section, option)
        elif expected_type == int:
            return config.getint(section, option)
        elif expected_type == float:
            return config.getfloat(section, option)
        elif expected_type == list: # Dla list stringów oddzielonych przecinkami
            value_str = config.get(section, option)
            return [item.strip() for item in value_str.split(',') if item.strip()]
        elif expected_type == dict and option == "interface_name_replacements": # Specjalna obsługa
             value_str = config.get(section, option)
             return _parse_interface_replacements(value_str)
        else: # Domyślnie string
            return config.get(section, option)
    except (configparser.NoSectionError, configparser.NoOptionError):
        if default_value is not None:
            logger.debug(f"Brak opcji '{option}' w sekcji '{section}'. Używam domyślnej wartości: {default_value}")
            return default_value
        else:
            logger.warning(f"Brak opcji '{option}' w sekcji '{section}' i brak wartości domyślnej.")
            if expected_type == list: return []
            if expected_type == dict: return {}
            return None
    except ValueError as e:
        logger.error(f"Błąd konwersji wartości dla {section}/{option} na typ {expected_type}: {e}. Używam wartości domyślnej {default_value} lub None.")
        if default_value is not None:
            return default_value
        if expected_type == list: return []
        if expected_type == dict: return {}
        return None


def load_config(config_path: str = DEFAULT_CONFIG_FILE) -> Dict[str, Any]:
    """Wczytuje konfigurację z pliku .ini i zmiennych środowiskowych."""
    config = configparser.ConfigParser(allow_no_value=True)
    parsed_config: Dict[str, Any] = {}

    if not os.path.exists(config_path):
        logger.error(f"Plik konfiguracyjny '{config_path}' nie został znaleziony.")
        # Można tu zwrócić pusty dict lub rzucić wyjątek, w zależności od wymagań
        # Na razie zwracamy pusty, ale aplikacja powinna to obsłużyć.
        return parsed_config
    try:
        config.read(config_path, encoding='utf-8')
        logger.info(f"Pomyślnie wczytano plik konfiguracyjny: {config_path}")
    except configparser.Error as e:
        logger.error(f"Błąd parsowania pliku konfiguracyjnego '{config_path}': {e}")
        return parsed_config # Zwróć pusty lub częściowo wczytany, jeśli to możliwe

    # Mapowanie sekcji i opcji do typów i wartości domyślnych
    # Klucz to nazwa opcji w wynikowym słowniku config
    # Wartość to krotka (sekcja_ini, opcja_ini, typ, wartość_domyślna_jeśli_brak)
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

        "cli_global_delay_factor": ("CLI", "global_delay_factor", float, 5.0), # float dla GDF
        "cli_session_log_file_mode": ("CLI", "session_log_file_mode", str, "append"),
        "cli_conn_timeout": ("CLI", "conn_timeout", int, 75),
        "cli_auth_timeout": ("CLI", "auth_timeout", int, 90),
        "cli_banner_timeout": ("CLI", "banner_timeout", int, 75),
        "cli_read_timeout_general": ("CLI", "read_timeout_general", int, 60),
        "cli_read_timeout_lldp_cdp": ("CLI", "read_timeout_lldp_cdp", int, 180),
        "cli_default_expect_string_pattern": ("CLI", "default_expect_string_pattern", str, r"[a-zA-Z0-9\S\.\-]*[#>]"),
        "cli_netmiko_session_log_template": ("CLI", "netmiko_session_log_template", str, "{host}_netmiko_session.log"),

        "prompt_regex_slot_sys": ("CLI", "prompt_regex_slot_sys", str, r'(?:\*\s*)?Slot-\d+\s+[\w.-]+\s*#\s*$'),
        "prompt_regex_simple": ("CLI", "prompt_regex_simple", str, r"^[a-zA-Z0-9][\w.-]*[>#]\s*$"),
        "prompt_regex_nxos": ("CLI", "prompt_regex_nxos", str, r"^[a-zA-Z0-9][\w.-]*#\s*$"),
        "prompt_regex_ios": ("CLI", "prompt_regex_ios", str, r"^[a-zA-Z0-9][\w.-]*[>#]\s*$"),

        "lldp_regex_header_candidate": ("CLI", "lldp_regex_header_candidate", str, r'(Device ID\s+Local Intf\s+Hold-time|Chassis id:)'),
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
        "interface_name_replacements": ("CLI", "interface_name_replacements", dict, {"GigabitEthernet": "Gi", "TenGigabitEthernet": "Te"}),


        "devices_per_row": ("DiagramLayout", "devices_per_row", int, 3),
        "grid_margin_x": ("DiagramLayout", "grid_margin_x", int, 450),
        "grid_margin_y": ("DiagramLayout", "grid_margin_y", int, 350),
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
        "port_alias_label_x_offset_from_line_center": ("DiagramElements", "port_alias_label_x_offset_from_line_center", float, 5.0),
        "info_label_margin_from_chassis": ("DiagramElements", "info_label_margin_from_chassis", float, 30.0),
        "info_label_min_width": ("DiagramElements", "info_label_min_width", float, 180.0),
        "info_label_max_width": ("DiagramElements", "info_label_max_width", float, 280.0),

        "svg_info_label_padding": ("SVGSpecific", "svg_info_label_padding", str, "5px"),
    }

    for key_name, (section, option, exp_type, default_val) in config_map.items():
        parsed_config[key_name] = _get_typed_value(config, section, option, exp_type, default_val)

    # Wczytywanie danych logowania CLI (może być bardziej złożone, np. z osobnego pliku)
    # Na razie, zakładamy, że może być w .env lub jako zmienne środowiskowe.
    # Funkcja get_env_config zajmie się tym osobno.

    logger.debug("Konfiguracja po wczytaniu z INI: %s", parsed_config)
    return parsed_config


def get_env_config(env_file_path: str = ".env", config_ini_path: str = DEFAULT_CONFIG_FILE) -> Dict[str, Any]:
    """
    Wczytuje konfigurację z pliku .env, a następnie z pliku .ini,
    łącząc je. Wartości z .env mają pierwszeństwo dla kluczy, które mogą tam być.
    Specjalnie obsługuje dane logowania CLI.
    """
    # Najpierw wczytaj konfigurację z pliku .ini jako bazę
    app_config = load_config(config_ini_path)

    # Następnie wczytaj zmienne środowiskowe (mogą nadpisać niektóre wartości z .ini)
    load_dotenv(dotenv_path=env_file_path, override=True) # override=True oznacza, że .env nadpisze zmienne systemowe

    # Klucze, które mają być pobrane bezpośrednio ze środowiska
    env_keys_map = {
        "LIBRENMS_BASE_URL": "base_url",
        "LIBRENMS_API_KEY": "api_key",
        "CLI_USER_DEFAULT": "cli_user_default", # Dla domyślnych poświadczeń CLI
        "CLI_PASS_DEFAULT": "cli_pass_default",
        # Można dodać więcej, np. ścieżki do plików, jeśli chcemy je nadpisywać przez .env
    }

    for env_var, config_key in env_keys_map.items():
        value = os.getenv(env_var)
        if value is not None: # Akceptuj pusty string, jeśli tak jest ustawiony
            app_config[config_key] = value
            logger.debug(f"Wczytano '{config_key}' ze zmiennej środowiskowej '{env_var}'.")
        # Jeśli nie ma w .env, wartość z .ini (jeśli była) pozostaje.

    # Sprawdzenie wymaganych wartości
    if not app_config.get("base_url"):
        msg = "Krytyczny błąd: LIBRENMS_BASE_URL (base_url) nie jest ustawiony w .env ani nie zdefiniowany w konfiguracji."
        logger.critical(msg)
        raise ValueError(msg)
    if not app_config.get("api_key"):
        msg = "Krytyczny błąd: LIBRENMS_API_KEY (api_key) nie jest ustawiony w .env ani nie zdefiniowany w konfiguracji."
        logger.critical(msg)
        raise ValueError(msg)


    # Struktura dla poświadczeń CLI z wieloma wpisami
    # (To jest uproszczone, w rzeczywistości mogłoby to być ładowane z osobnego JSON/YAML)
    cli_credentials_structure = {
        "defaults": {},
        "devices": [] # Lista słowników: {"identifier": "xxx", "match": "exact|regex", "cli_user": "u", "cli_pass": "p"}
    }

    # Domyślne poświadczenia CLI
    if app_config.get("cli_user_default") and app_config.get("cli_pass_default"):
        cli_credentials_structure["defaults"]["cli_user"] = app_config["cli_user_default"]
        cli_credentials_structure["defaults"]["cli_pass"] = app_config["cli_pass_default"]

    # Przykładowa logika wczytywania specyficznych poświadczeń CLI (może być rozbudowana)
    # Zakładamy, że są w formacie CLI_DEVICE_1_ID, CLI_DEVICE_1_USER, CLI_DEVICE_1_PASS, CLI_DEVICE_1_MATCH
    i = 1
    while True:
        dev_id = os.getenv(f"CLI_DEVICE_{i}_ID")
        dev_user = os.getenv(f"CLI_DEVICE_{i}_USER")
        dev_pass = os.getenv(f"CLI_DEVICE_{i}_PASS")
        dev_match = os.getenv(f"CLI_DEVICE_{i}_MATCH", "exact") # Domyślnie exact

        if dev_id and dev_user and dev_pass:
            cli_credentials_structure["devices"].append({
                "identifier": dev_id,
                "match": dev_match.lower(),
                "cli_user": dev_user,
                "cli_pass": dev_pass
            })
            i += 1
        else:
            break # Koniec listy urządzeń w .env

    app_config['cli_credentials'] = cli_credentials_structure
    logger.info(f"Wczytano {len(cli_credentials_structure['devices'])} specyficznych wpisów poświadczeń CLI i {'domyślne ' if cli_credentials_structure['defaults'] else 'brak domyślnych '}poświadczeń.")

    # Usuń tymczasowe klucze z app_config, jeśli były
    app_config.pop("cli_user_default", None)
    app_config.pop("cli_pass_default", None)

    logger.info("Konfiguracja ze zmiennych środowiskowych (.env) i pliku .ini wczytana i połączona.")
    logger.debug("Finalna konfiguracja aplikacji: %s", app_config)
    return app_config

if __name__ == '__main__':
    # Prosty test wczytywania konfiguracji
    # Najpierw skonfiguruj logger, aby zobaczyć komunikaty z config_loader
    from utils import setup_logging # Załóżmy, że utils.py jest w tym samym katalogu lub PYTHONPATH
    setup_logging(level=logging.DEBUG)

    logger.info("Testowanie config_loader.py...")
    # Utwórz przykładowy plik .env do testów
    with open(".env.test", "w") as f_env:
        f_env.write("LIBRENMS_BASE_URL=http://test-librenms.example.com\n")
        f_env.write("LIBRENMS_API_KEY=testapikeyfromenv\n")
        f_env.write("CLI_USER_DEFAULT=env_default_user\n")
        f_env.write("CLI_PASS_DEFAULT=env_default_pass\n")
        f_env.write("CLI_DEVICE_1_ID=router1.example.com\n")
        f_env.write("CLI_DEVICE_1_USER=env_user1\n")
        f_env.write("CLI_DEVICE_1_PASS=env_pass1\n")
        f_env.write("CLI_DEVICE_1_MATCH=exact\n")
        f_env.write("CLI_DEVICE_2_ID=switch-.*-core\n")
        f_env.write("CLI_DEVICE_2_USER=env_regex_user\n")
        f_env.write("CLI_DEVICE_2_PASS=env_regex_pass\n")
        f_env.write("CLI_DEVICE_2_MATCH=regex\n")


    # Utwórz przykładowy config.ini do testów
    with open("config.test.ini", "w") as f_ini:
        f_ini.write("[DEFAULT]\n")
        f_ini.write("log_level = DEBUG\n")
        f_ini.write("ip_list_file = test_ips.txt\n")
        f_ini.write("[LibreNMS]\n")
        f_ini.write("api_timeout = 30\n") # Powinno być nadpisane przez .env jeśli tam jest
        f_ini.write("[Discovery]\n")
        f_ini.write("default_snmp_communities = test_public, test_private\n")
        f_ini.write("[CLI]\n")
        f_ini.write("interface_name_replacements = GigabitEthernet=Gi,TenGigabitEthernet=Te,FastEthernet=Fa\n")


    loaded_app_config = get_env_config(env_file_path=".env.test", config_ini_path="config.test.ini")
    logger.info("--- Załadowana konfiguracja (test) ---")
    import pprint
    pprint.pprint(loaded_app_config)

    # Sprawdzenia
    assert loaded_app_config.get("base_url") == "http://test-librenms.example.com"
    assert loaded_app_config.get("api_key") == "testapikeyfromenv"
    assert loaded_app_config.get("log_level") == "DEBUG" # z config.ini
    assert loaded_app_config.get("ip_list_file") == "test_ips.txt" # z config.ini
    assert loaded_app_config.get("api_timeout") == 30 # z config.ini, bo nie ma w .env
    assert "test_public" in loaded_app_config.get("default_snmp_communities", [])
    assert loaded_app_config.get("cli_credentials", {}).get("defaults", {}).get("cli_user") == "env_default_user"
    assert len(loaded_app_config.get("cli_credentials", {}).get("devices", [])) == 2
    assert loaded_app_config.get("cli_credentials", {}).get("devices", [])[0].get("identifier") == "router1.example.com"
    assert loaded_app_config.get("cli_credentials", {}).get("devices", [])[1].get("match") == "regex"
    assert loaded_app_config.get("interface_name_replacements", {}).get("GigabitEthernet") == "Gi"


    logger.info("Testowanie zakończone pomyślnie.")

    # Usuń pliki testowe
    os.remove(".env.test")
    os.remove("config.test.ini")