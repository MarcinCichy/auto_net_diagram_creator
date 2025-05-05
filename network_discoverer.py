# --- network_discoverer.py ---

import time
import logging
import re
from typing import Dict, List, Any, Optional, Tuple

from librenms_client import LibreNMSAPI
import config_loader # Nadal potrzebne do get_communities_to_try
import data_processing
import discovery
import file_io
from utils import find_device_in_list, get_canonical_identifier

logger = logging.getLogger(__name__)

class NetworkDiscoverer:
    """Klasa odpowiedzialna za proces odkrywania połączeń sieciowych."""

    def __init__(self, api_client: LibreNMSAPI, config: Dict[str, Any],
                 ip_list_path: str, conn_txt_path: str, conn_json_path: str):
        self.api_client = api_client
        self.config = config
        self.ip_list_path = ip_list_path
        self.conn_txt_path = conn_txt_path
        self.conn_json_path = conn_json_path
        self.phys_mac_map: Dict[str, Dict] = {}
        self.all_devices_from_api: List[Dict] = []
        self.port_to_ifindex_map: Dict[Tuple[str, str], Any] = {}
        # Przechowaj strukturę poświadczeń CLI wczytaną przez config_loader
        self.cli_credentials: Dict[str, Any] = config.get('cli_credentials', {"defaults": {}, "devices": []})

    def discover_connections(self) -> None:
        """Główna metoda uruchamiająca proces odkrywania."""
        logger.info("[Odkrywanie 1/5] Budowanie mapy MAC...")
        self.phys_mac_map = data_processing.build_phys_mac_map(self.api_client)
        if not self.phys_mac_map:
            logger.warning("Nie udało się zbudować mapy MAC.")

        logger.info(f"[Odkrywanie 2/5] Wczytywanie listy urządzeń z {self.ip_list_path}...")
        target_ips_or_hosts = file_io.load_ip_list(self.ip_list_path)
        if not target_ips_or_hosts:
            logger.warning("Brak urządzeń docelowych na liście.")
            self._save_empty_connections()
            return

        logger.info("[Odkrywanie 3/5] Pobieranie pełnej listy urządzeń z API...")
        self.all_devices_from_api = self.api_client.get_devices(columns="device_id,hostname,ip,sysName,purpose")
        if not self.all_devices_from_api:
            logger.error("Nie udało się pobrać listy urządzeń z API.")
            self._save_empty_connections()
            return

        logger.info("[Odkrywanie 4/5] Przetwarzanie urządzeń i odkrywanie połączeń...")
        all_found_connections_raw = self._process_all_target_devices(target_ips_or_hosts)

        logger.info("\n[Odkrywanie 5/5] Wzbogacanie danych, normalizacja, deduplikacja i zapisywanie wyników...")
        if all_found_connections_raw:
            self._build_port_ifindex_map()
            enriched_connections = self._enrich_connections(all_found_connections_raw)
            logger.info(f"Zebrano {len(enriched_connections)} wpisów po wzbogaceniu. Deduplikowanie...")
            final_connections = data_processing.deduplicate_connections(enriched_connections)
            file_io.save_connections_txt(final_connections, self.conn_txt_path)
            file_io.save_connections_json(final_connections, self.conn_json_path)
        else:
            logger.info("Nie znaleziono żadnych surowych połączeń.")
            self._save_empty_connections()

    def _save_empty_connections(self) -> None:
        """Zapisuje puste pliki wyników."""
        file_io.save_connections_txt([], self.conn_txt_path)
        file_io.save_connections_json([], self.conn_json_path)

    def _get_cli_credentials_for_device(self, device_info: Dict) -> Optional[Tuple[str, str]]:
        """
        Wyszukuje odpowiednie poświadczenia CLI dla danego urządzenia w strukturze
        wczytanej z pliku credentials.json.
        Kolejność sprawdzania: Dokładne dopasowanie IP/Host/Purpose/CanonID -> Regex -> Domyślne z JSON.
        """
        if not device_info:
            return None

        # Zbierz potencjalne identyfikatory do sprawdzenia
        identifiers_to_check = list(filter(None, [
            device_info.get('ip'),
            device_info.get('hostname'),
            device_info.get('purpose'),
            get_canonical_identifier(device_info) # Sprawdź też kanoniczny ID
        ]))
        # Potrzebujemy wersji lowercase do porównań case-insensitive
        identifiers_to_check_lower = {str(i).lower() for i in identifiers_to_check}

        device_creds_list = self.cli_credentials.get("devices", [])

        # 1. Sprawdź dokładne dopasowania w "devices" (case-insensitive dla stringów)
        for cred_entry in device_creds_list:
            identifier_entry = cred_entry.get("identifier")
            # Pomijamy wpisy regex na tym etapie
            if not identifier_entry or cred_entry.get("match") == "regex":
                continue

            if str(identifier_entry).lower() in identifiers_to_check_lower:
                user = cred_entry.get("cli_user")
                password = cred_entry.get("cli_pass")
                if user and password:
                    logger.debug(f"Znaleziono dokładne dopasowanie CLI dla identyfikatora '{identifier_entry}' dla urządzenia {device_info.get('ip') or device_info.get('hostname')}.")
                    return user, password

        # 2. Sprawdź dopasowania regex w "devices" (na hostname, purpose, canonical ID - case-insensitive)
        for cred_entry in device_creds_list:
            identifier_pattern = cred_entry.get("identifier")
            # Tylko wpisy oznaczone jako regex
            if not identifier_pattern or cred_entry.get("match") != "regex":
                continue

            try:
                # Sprawdzamy wzorzec na wszystkich potencjalnych identyfikatorach (string)
                for identifier_str in identifiers_to_check:
                    if isinstance(identifier_str, str) and re.fullmatch(identifier_pattern, identifier_str, re.IGNORECASE):
                        user = cred_entry.get("cli_user")
                        password = cred_entry.get("cli_pass")
                        if user and password:
                            logger.debug(f"Znaleziono dopasowanie regex CLI '{identifier_pattern}' dla identyfikatora '{identifier_str}' urządzenia {device_info.get('ip') or device_info.get('hostname')}.")
                            return user, password
                        break # Wystarczy pierwsze dopasowanie regex dla danego wpisu
            except re.error as e:
                logger.warning(f"Błąd w wyrażeniu regularnym '{identifier_pattern}' w pliku poświadczeń: {e}")
            except Exception as e:
                 logger.warning(f"Niespodziewany błąd podczas dopasowywania regex '{identifier_pattern}': {e}")


        # 3. Użyj "defaults" z pliku JSON, jeśli istnieją
        defaults = self.cli_credentials.get("defaults", {})
        default_user = defaults.get("cli_user")
        default_pass = defaults.get("cli_pass")
        if default_user and default_pass:
            logger.debug(f"Używanie domyślnych poświadczeń CLI z pliku JSON dla urządzenia {device_info.get('ip') or device_info.get('hostname')}.")
            return default_user, default_pass

        # 4. Brak pasujących poświadczeń
        current_canonical_id = get_canonical_identifier(device_info)
        logger.info(f"Nie znaleziono odpowiednich poświadczeń CLI (ani domyślnych w JSON) dla urządzenia {current_canonical_id} (sprawdzano: {identifiers_to_check}).")
        return None


    def _process_all_target_devices(self, target_ips_or_hosts: List[str]) -> List[Dict]:
        """Iteruje przez listę docelową i uruchamia odkrywanie dla każdego urządzenia."""
        all_connections: List[Dict] = []
        total_targets = len(target_ips_or_hosts)
        for i, ip_or_host in enumerate(target_ips_or_hosts):
            logger.info(f"\n--- Odkrywanie dla ({i+1}/{total_targets}): {ip_or_host} ---")
            target_device = find_device_in_list(ip_or_host, self.all_devices_from_api)

            if not target_device or not target_device.get("device_id"):
                logger.warning(f"Nie znaleziono '{ip_or_host}' w LibreNMS. Pomijam.")
                continue

            device_connections = self._process_target_device(target_device, ip_or_host)
            canonical_id = get_canonical_identifier(target_device, ip_or_host)
            if device_connections:
                 logger.info(f"✓ Znaleziono {len(device_connections)} potencjalnych połączeń dla {canonical_id}.")
                 all_connections.extend(device_connections)
            else:
                 logger.info(f"❌ Nie wykryto połączeń dla {canonical_id}.")

        return all_connections

    def _process_target_device(self, target_device: Dict, original_identifier: str) -> List[Dict]:
        """Wykonuje różne metody odkrywania dla pojedynczego urządzenia docelowego."""
        dev_id = target_device['device_id']
        canonical_id = get_canonical_identifier(target_device, original_identifier)
        logger.info(f"Przetwarzanie jako: {canonical_id} (ID: {dev_id})")

        communities = config_loader.get_communities_to_try(self.config.get("default_snmp_communities", []))
        idx2name = data_processing.build_ifindex_to_name_map(self.api_client, str(dev_id))

        device_connections: List[Dict] = []

        # Metody SNMP
        if communities:
            device_connections.extend(discovery.find_via_lldp_cdp_snmp(target_device, communities, idx2name))
            device_connections.extend(discovery.find_via_qbridge_snmp(self.phys_mac_map, target_device, communities, idx2name))
            device_connections.extend(discovery.find_via_snmp_fdb(self.phys_mac_map, target_device, communities, idx2name))
            device_connections.extend(discovery.find_via_arp_snmp(self.phys_mac_map, target_device, communities, idx2name))
        else:
            logger.info("Brak skonfigurowanych community SNMP do próby dla metod SNMP.")

        # Metoda API FDB
        device_connections.extend(discovery.find_via_api_fdb(self.api_client, self.phys_mac_map, target_device))

        # Metoda CLI - Z użyciem wyszukanych poświadczeń
        cli_credentials = self._get_cli_credentials_for_device(target_device)
        if cli_credentials:
            cli_user, cli_pass = cli_credentials
            # Potrzebujemy IP lub hostname do połączenia Netmiko
            target_for_cli = target_device.get('hostname') or target_device.get('ip')
            if target_for_cli:
                # Sprawdź, czy user i pass nie są puste (dodatkowe zabezpieczenie)
                if cli_user and cli_pass:
                    device_connections.extend(discovery.find_via_cli(target_for_cli, cli_user, cli_pass))
                else:
                     logger.warning(f"Pominięto próbę CLI dla {canonical_id} - znaleziono wpis, ale brak w nim użytkownika lub hasła.")
            else:
                logger.warning(f"CLI: Brak hostname/IP do próby połączenia dla {canonical_id}, mimo znalezienia poświadczeń.")
        else:
            # Logowanie o braku poświadczeń jest już w _get_cli_credentials_for_device
            logger.info(f"Pominięto próbę CLI dla {canonical_id} - brak odpowiednich poświadczeń.")

        return device_connections

    def _build_port_ifindex_map(self) -> None:
        """Buduje mapę (identyfikator_kanoniczny, nazwa_portu) -> ifIndex."""
        logger.info("Budowanie mapy portów (nazwa/opis) -> ifIndex z danych API...")
        self.port_to_ifindex_map = {}
        total_api_devices = len(self.all_devices_from_api)
        for i, device_api_info in enumerate(self.all_devices_from_api):
            if (i + 1) % max(1, total_api_devices // 20) == 0 or (i + 1) == total_api_devices:
                 logger.debug(f"Przetworzono mapę ifIndex dla {i+1}/{total_api_devices} urządzeń API...")

            dev_id = device_api_info.get("device_id")
            canonical_id = get_canonical_identifier(device_api_info)
            if not dev_id or not canonical_id:
                continue

            try:
                ports = self.api_client.get_ports(str(dev_id), columns="ifIndex,ifName,ifDescr,ifAlias")
                if ports:
                    for p in ports:
                        ifindex = p.get("ifIndex")
                        if ifindex is None: continue

                        # Klucz mapy: (canonical_id, nazwa_portu_lub_aliasu_lub_opisu)
                        # Mapujemy różne nazwy na ten sam ifIndex

                        ifname = p.get("ifName")
                        if ifname:
                            self.port_to_ifindex_map[(canonical_id, ifname)] = ifindex
                            # Dodaj też wersję lowercase dla pewności dopasowania
                            self.port_to_ifindex_map[(canonical_id, ifname.lower())] = ifindex

                        ifdescr = p.get("ifDescr")
                        if ifdescr and ifdescr != ifname:
                            self.port_to_ifindex_map[(canonical_id, ifdescr)] = ifindex
                            self.port_to_ifindex_map[(canonical_id, ifdescr.lower())] = ifindex


                        ifalias = p.get("ifAlias")
                        if ifalias and ifalias != ifname and ifalias != ifdescr:
                            self.port_to_ifindex_map[(canonical_id, ifalias)] = ifindex
                            self.port_to_ifindex_map[(canonical_id, ifalias.lower())] = ifindex

            except Exception as e:
                logger.warning(f"Błąd pobierania portów API dla mapy ifIndex (urządzenie ID {dev_id}, nazwa {canonical_id}): {e}")
        logger.info(f"✓ Zbudowano mapę port -> ifIndex dla {len(self.port_to_ifindex_map)} wpisów.")


    def _enrich_connections(self, raw_connections: List[Dict]) -> List[Dict]:
        """Wzbogaca surowe dane o połączeniach o dodatkowe informacje i normalizuje je."""
        logger.info("Wzbogacanie danych o połączeniach (w tym ifIndex)...")
        enriched_connections: List[Dict] = []
        for conn_raw in raw_connections:
            local_original = conn_raw.get('local_host')
            remote_original = conn_raw.get('neighbor_host')
            local_if_raw = conn_raw.get('local_if')
            remote_if_raw = conn_raw.get('neighbor_if')
            via_raw = conn_raw.get('via')
            vlan_raw = conn_raw.get('vlan')
            local_ifindex_cli = conn_raw.get('local_ifindex') # Może pochodzić z CLI

            local_info = find_device_in_list(local_original, self.all_devices_from_api)
            remote_info = find_device_in_list(remote_original, self.all_devices_from_api)

            local_canonical = get_canonical_identifier(local_info, local_original)
            remote_canonical = get_canonical_identifier(remote_info, remote_original)

            # Podstawowe filtrowanie
            if not local_canonical or not remote_canonical:
                 logger.debug(f"Pomijanie połączenia - brak identyfikatora kanonicznego dla hosta lokalnego lub zdalnego: {conn_raw}")
                 continue
            if str(remote_canonical).lower() == 'null' or remote_canonical is None:
                logger.debug(f"Pomijanie połączenia - remote_canonical to null/None: {conn_raw}")
                continue
            # Sprawdzanie self-connection teraz na identyfikatorach kanonicznych
            if local_canonical == remote_canonical:
                logger.debug(f"Pomijanie połączenia - self-connection (kanoniczne): {local_canonical}")
                continue

            # Wzbogacanie o ifIndex - używamy mapy zbudowanej wcześniej
            local_ifindex = local_ifindex_cli # Z CLI ma pierwszeństwo
            if local_ifindex is None and local_if_raw:
                 # Szukamy po nazwie oryginalnej i lowercase
                 local_ifindex = self.port_to_ifindex_map.get((local_canonical, local_if_raw)) or \
                                 self.port_to_ifindex_map.get((local_canonical, local_if_raw.lower()))

            remote_ifindex = None
            if remote_if_raw:
                 remote_ifindex = self.port_to_ifindex_map.get((remote_canonical, remote_if_raw)) or \
                                  self.port_to_ifindex_map.get((remote_canonical, remote_if_raw.lower()))


            enriched_conn_pre_filter = {
                "local_device": local_canonical,
                "local_port": local_if_raw,
                "local_ifindex": local_ifindex,
                "remote_device": remote_canonical,
                "remote_port": remote_if_raw,
                "remote_ifindex": remote_ifindex,
                "vlan": vlan_raw,
                "discovery_method": via_raw,
                "local_device_ip": local_info.get('ip') if local_info else None,
                "local_device_hostname": local_info.get('hostname') if local_info else None,
                "local_device_purpose": local_info.get('purpose') if local_info else None,
                "remote_device_ip": remote_info.get('ip') if remote_info else None,
                "remote_device_hostname": remote_info.get('hostname') if remote_info else None,
                "remote_device_purpose": remote_info.get('purpose') if remote_info else None,
                "remote_device_original": remote_original if not remote_info else None
            }
            # Usuń klucze z wartościami None
            enriched_conn = {k: v for k, v in enriched_conn_pre_filter.items() if v is not None}
            enriched_connections.append(enriched_conn)

        return enriched_connections