import os
from dotenv import load_dotenv

# Load environment variables from .env file in the project root
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- Getters for configuration values ---

def get_env_variable(var_name, default=None):
    """Gets an environment variable or returns a default."""
    return os.getenv(var_name, default)

def get_librenms_url():
    url = get_env_variable("LIBRENMS_URL")
    if not url:
        raise ValueError("LIBRENMS_URL not set in .env file")
    return url.rstrip('/')  # Remove trailing slash if present

def get_librenms_token():
    token = get_env_variable("LIBRENMS_TOKEN")
    if not token:
        raise ValueError("LIBRENMS_TOKEN not set in .env file")
    return token

def get_ip_list_file():
    return os.path.join(project_root, get_env_variable("IP_LIST_FILE", "ip_list.txt"))

def get_switch_template_file():
    return os.path.join(project_root, get_env_variable("SWITCH_TEMPLATE_FILE", "switch_template.drawio"))

def get_output_diagram_file():
    return os.path.join(project_root, get_env_variable("OUTPUT_DIAGRAM_FILE", "network_diagram.drawio"))

def get_port_identifier_field():
    # Ustawienie tego pola decyduje, z którego klucza bierzemy wartość do mapowania portu.
    # Jeśli nazw portów nie zawierają numeru (np. "Gi0/0/0"), warto ustawić to na "ifIndex".
    return get_env_variable("PORT_IDENTIFIER_FIELD", "ifName")

def get_port_number_regex():
    return get_env_variable("PORT_NUMBER_REGEX", r'(\d+)$')

def get_int_setting(var_name, default):
    try:
        return int(get_env_variable(var_name, default))
    except (ValueError, TypeError):
        print(f"Warning: Invalid integer value for {var_name} in .env. Using default: {default}")
        return default

def get_bool_setting(var_name, default):
    val = get_env_variable(var_name, str(default)).lower()
    return val in ['true', '1', 't', 'y', 'yes']

def get_use_port_info_api():
    """Czy używać rozszerzonego endpointu API do pobierania informacji o portach.
       Ustaw flagę USE_PORT_INFO_API w pliku .env (np. USE_PORT_INFO_API=true)."""
    return get_bool_setting("USE_PORT_INFO_API", True)

# --- Layout and Style ---
SWITCH_SPACING_X = get_int_setting("SWITCH_SPACING_X", 250)
SWITCH_SPACING_Y = get_int_setting("SWITCH_SPACING_Y", 200)
SWITCHES_PER_ROW = get_int_setting("SWITCHES_PER_ROW", 4)
START_X = get_int_setting("START_X", 50)
START_Y = get_int_setting("START_Y", 50)
PORT_IDENTIFIER_FIELD = os.getenv("PORT_IDENTIFIER_FIELD", "ifName")
PORT_NUMBER_REGEX = os.getenv("PORT_NUMBER_REGEX", r"(\d+)$")
PORT_UP_COLOR = "#00FF00"
PORT_DOWN_COLOR = "#FF0000"
PORT_DEFAULT_COLOR = "#D3D3D3"  # Light gray for unknown/other status
ADD_DEVICE_LABEL = get_bool_setting("ADD_DEVICE_LABEL", True)

if __name__ == '__main__':
    print("Testing configuration loading:")
    print(f"LibreNMS URL: {get_librenms_url()}")
    print(f"Token loaded: {'Yes' if get_librenms_token() else 'No'}")
    print(f"IP List File: {get_ip_list_file()}")
    print(f"Template File: {get_switch_template_file()}")
    print(f"Output File: {get_output_diagram_file()}")
    print(f"Port Identifier: {get_port_identifier_field()}")
    print(f"Port Regex: {get_port_number_regex()}")
    print(f"Spacing X: {SWITCH_SPACING_X}")
    print(f"Use extended port info API: {get_use_port_info_api()}")
