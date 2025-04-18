import sys
import config_loader
from librenms_client import LibreNMSClient
from drawio_utils import DrawioTemplate
from diagram_builder import DiagramBuilder

def read_ip_list(filepath):
    """Reads IP addresses from the specified file."""
    try:
        with open(filepath, 'r') as f:
            ips = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        if not ips:
            print(f"ERROR: No IP addresses found in {filepath}.")
            return None
        print(f"INFO: Read {len(ips)} IP addresses from {filepath}.")
        return ips
    except FileNotFoundError:
        print(f"ERROR: IP list file not found: {filepath}")
        return None

def run():
    """Main execution function."""
    print("--- Starting Network Diagram Generation ---")

    try:
        # 1. Load Configuration & IP List
        ip_list_file = config.get_ip_list_file()
        ip_addresses = read_ip_list(ip_list_file)
        if not ip_addresses:
            sys.exit(1)

        template_file = config_loader.get_switch_template_file()
        output_file = config_loader.get_output_diagram_file()

        # 2. Initialize Components
        print("INFO: Initializing components...")
        nms_client = LibreNMSClient()
        template = DrawioTemplate(template_file)  # Load Draw.io template
        builder = DiagramBuilder(template)

        # 3. Process Each Device
        total_devices = len(ip_addresses)
        for i, device in enumerate(ip_addresses):
            print(f"\n--- Processing device {i+1}/{total_devices}: {device} ---")
            # Pobieramy porty z extended API
            port_list = nms_client.get_device_ports(device)
            if not port_list:
                print(f"WARN: Skipping device {device} due to failure fetching port list.")
                continue

            print(f"INFO: Device {device} - uzyskano szczegółowe dane dla {len(port_list)} portów.")
            builder.add_switch(device, port_list)

        # 4. Save Diagram
        builder.save_diagram(output_file)

    except FileNotFoundError as e:
         print(f"ERROR: Required file not found: {e}")
         sys.exit(1)
    except ValueError as e:
         print(f"ERROR: Configuration or setup error: {e}")
         sys.exit(1)
    except Exception as e:
         print(f"FATAL: An unexpected error occurred: {e}")
         import traceback
         traceback.print_exc()
         sys.exit(1)

    print("\n--- Diagram Generation Finished ---")

if __name__ == "__main__":
    run()
