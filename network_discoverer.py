# --- network_discoverer.py ---
import time
import logging
import re
import pprint  # <<< DODANO IMPORT
from typing import Dict, List, Any, Optional, Tuple, Callable  # Dodano Callable

from librenms_client import LibreNMSAPI

try:
    import snmp_utils

    SNMP_UTILS_AVAILABLE = True
except ImportError:
    SNMP_UTILS_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Moduł snmp_utils.py nie został znaleziony lub nie można go zaimportować. "
        "Funkcje SNMP nie będą działać. Upewnij się, że pysnmp jest zainstalowane i snmp_utils.py jest w PYTHONPATH."
    )


    class snmp_utils:  # type: ignore
        @staticmethod
        def snmp_get_lldp_neighbors(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[int, str, str]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_lldp_neighbors({h}, ***)")
            return None

        @staticmethod
        def snmp_get_cdp_neighbors(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[int, str, str]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_cdp_neighbors({h}, ***)")
            return None

        @staticmethod
        def snmp_get_bridge_baseport_ifindex(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            Dict[int, int]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_bridge_baseport_ifindex({h}, ***)")
            return None

        @staticmethod
        def snmp_get_fdb_entries(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[List[Tuple[str, int]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_fdb_entries({h}, ***)")
            return None

        @staticmethod
        def snmp_get_qbridge_fdb(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[str, int, int]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_qbridge_fdb({h}, ***)")
            return None

        @staticmethod
        def snmp_get_arp_entries(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[str, str, int]]]:
            logging.getLogger(__name__).debug(f"  SNMP STUB: snmp_get_arp_entries({h}, ***)")
            return None

import cli_utils
import config_loader
import data_processing
import discovery  # Zmieniono na import discovery (powinno być ok, jeśli discovery.py jest w tym samym katalogu)
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

        self.phys_mac_map: Dict[str, Dict[str, Any]] = {}
        self.all_devices_from_api: List[Dict[str, Any]] = []
        self.port_name_to_ifindex_map: Dict[Tuple[str, str], Any] = {}

        self.cli_credentials: Dict[str, Any] = config.get('cli_credentials', {"defaults": {}, "devices": []})
        logger.debug("NetworkDiscoverer zainicjalizowany.")

    def discover_connections(self) -> None:
        """Główna metoda uruchamiająca proces odkrywania."""
        logger.info("=== Rozpoczynanie Fazy Odkrywania Połączeń ===")
        discovery_start_time = time.time()

        logger.info("[Odkrywanie 1/5] Budowanie globalnej mapy MAC adresów...")
        self.phys_mac_map = data_processing.build_phys_mac_map(self.api_client)
        if not self.phys_mac_map:
            logger.warning(
                "Nie udało się zbudować mapy MAC lub mapa jest pusta. Odkrywanie oparte na MAC może być ograniczone.")

        logger.info(f"[Odkrywanie 2/5] Wczytywanie listy urządzeń docelowych z '{self.ip_list_path}'...")
        target_ips_or_hosts = file_io.load_ip_list(self.ip_list_path)
        if not target_ips_or_hosts:
            logger.warning("Lista urządzeń docelowych jest pusta. Kończę fazę odkrywania.")
            self._save_empty_connections()
            return

        logger.info("[Odkrywanie 3/5] Pobieranie pełnej listy urządzeń z API LibreNMS...")
        self.all_devices_from_api = self.api_client.get_devices(
            columns="device_id,hostname,ip,sysName,purpose,os,hardware,version,serial,type,status"
        )
        if not self.all_devices_from_api:
            logger.error("Nie udało się pobrać listy urządzeń z API lub lista jest pusta. Kończę fazę odkrywania.")
            self._save_empty_connections()
            return
        logger.info(f"Pobrano informacje o {len(self.all_devices_from_api)} urządzeniach z API.")

        self._build_port_name_to_ifindex_map()

        logger.info("[Odkrywanie 4/5] Przetwarzanie urządzeń docelowych i odkrywanie surowych połączeń...")
        all_found_connections_raw = self._process_all_target_devices(target_ips_or_hosts)

        logger.info("\n[Odkrywanie 5/5] Wzbogacanie danych, normalizacja, deduplikacja i zapisywanie wyników...")
        if all_found_connections_raw:
            logger.info(
                f"Zebrano {len(all_found_connections_raw)} surowych wpisów połączeń. Rozpoczynam wzbogacanie...")
            enriched_connections = self._enrich_connections(all_found_connections_raw)

            logger.info(f"Po wzbogaceniu uzyskano {len(enriched_connections)} wpisów. Rozpoczynam deduplikację...")
            final_connections = data_processing.deduplicate_connections(enriched_connections)

            logger.info(f"Zapisywanie {len(final_connections)} unikalnych połączeń...")
            file_io.save_connections_txt(final_connections, self.conn_txt_path)
            file_io.save_connections_json(final_connections, self.conn_json_path)
        else:
            logger.info("Nie znaleziono żadnych surowych połączeń. Zapisuję puste pliki wyników.")
            self._save_empty_connections()

        discovery_end_time = time.time()
        logger.info(
            f"=== Zakończono Fazę Odkrywania Połączeń (czas: {discovery_end_time - discovery_start_time:.2f} sek.) ===")

    def _save_empty_connections(self) -> None:
        file_io.save_connections_txt([], self.conn_txt_path)
        file_io.save_connections_json([], self.conn_json_path)

    def _get_cli_credentials_for_device(self, device_info: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        if not device_info: return None
        canonical_id_device = get_canonical_identifier(device_info)
        logger.debug(f"Wyszukiwanie poświadczeń CLI dla urządzenia: {canonical_id_device or device_info.get('ip')}")
        identifiers_to_check_exact: List[str] = []
        if device_info.get('ip'): identifiers_to_check_exact.append(str(device_info['ip']).lower())
        if device_info.get('hostname'): identifiers_to_check_exact.append(str(device_info['hostname']).lower())
        if device_info.get('sysName'): identifiers_to_check_exact.append(str(device_info['sysName']).lower())
        if device_info.get('purpose'): identifiers_to_check_exact.append(str(device_info['purpose']).lower())
        if canonical_id_device: identifiers_to_check_exact.append(canonical_id_device.lower())
        identifiers_to_check_exact = list(set(filter(None, identifiers_to_check_exact)))
        identifiers_for_regex: List[str] = list(set(filter(None, [
            device_info.get('hostname'), device_info.get('sysName'), device_info.get('purpose'), canonical_id_device
        ])))
        device_creds_list = self.cli_credentials.get("devices", [])
        for cred_entry in device_creds_list:
            identifier_cred = str(cred_entry.get("identifier", "")).lower()
            match_type = cred_entry.get("match", "exact")
            if match_type == "exact" and identifier_cred in identifiers_to_check_exact:
                user, password = cred_entry.get("cli_user"), cred_entry.get("cli_pass")
                if user and password:
                    logger.info(
                        f"  CLI Creds: Znaleziono dokładne dopasowanie ('{identifier_cred}') dla {canonical_id_device}.")
                    return user, password
        for cred_entry in device_creds_list:
            pattern_cred = cred_entry.get("identifier")
            match_type = cred_entry.get("match")
            if match_type == "regex" and pattern_cred:
                try:
                    regex = re.compile(pattern_cred, re.IGNORECASE)
                    for val_to_check in identifiers_for_regex:
                        if regex.fullmatch(str(val_to_check)):
                            user, password = cred_entry.get("cli_user"), cred_entry.get("cli_pass")
                            if user and password:
                                logger.info(
                                    f"  CLI Creds: Znaleziono dopasowanie regex ('{pattern_cred}') dla '{val_to_check}' urządzenia {canonical_id_device}.")
                                return user, password
                            break
                except re.error as e_re:
                    logger.warning(f"  CLI Creds: Błąd w regex '{pattern_cred}': {e_re}")
                except Exception as e_re_match:
                    logger.warning(f"  CLI Creds: Błąd dopasowania regex '{pattern_cred}': {e_re_match}",
                                   exc_info=False)
        defaults = self.cli_credentials.get("defaults", {})
        default_user, default_pass = defaults.get("cli_user"), defaults.get("cli_pass")
        if default_user and default_pass:
            logger.info(f"  CLI Creds: Używanie domyślnych poświadczeń dla {canonical_id_device}.")
            return default_user, default_pass
        logger.info(
            f"  CLI Creds: Nie znaleziono poświadczeń dla {canonical_id_device} (sprawdzano: {identifiers_to_check_exact}).")
        return None

    def _process_all_target_devices(self, target_ips_or_hosts: List[str]) -> List[Dict[str, Any]]:
        all_connections_raw: List[Dict[str, Any]] = []
        total_targets = len(target_ips_or_hosts)
        for i, ip_or_host_target in enumerate(target_ips_or_hosts):
            logger.info(
                f"\n--- Przetwarzanie urządzenia docelowego ({i + 1}/{total_targets}): '{ip_or_host_target}' ---")
            target_device_api_info = find_device_in_list(ip_or_host_target, self.all_devices_from_api)
            if not target_device_api_info or not target_device_api_info.get("device_id"):
                logger.warning(
                    f"Nie znaleziono urządzenia '{ip_or_host_target}' w danych z API lub brak device_id. Pomijam.")
                continue
            canonical_id = get_canonical_identifier(target_device_api_info, ip_or_host_target)
            logger.info(
                f"Rozpoczynam odkrywanie dla: {canonical_id} (ID API: {target_device_api_info.get('device_id')})")
            device_connections = self._process_single_target_device(target_device_api_info)
            if device_connections:
                logger.info(
                    f"✓ Znaleziono {len(device_connections)} potencjalnych surowych połączeń dla {canonical_id}.")
                all_connections_raw.extend(device_connections)
            else:
                logger.info(f"  Nie wykryto żadnych surowych połączeń dla {canonical_id}.")
        return all_connections_raw

    def _process_single_target_device(self, target_device_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        device_id_api = str(target_device_info['device_id'])
        canonical_id = get_canonical_identifier(target_device_info) or f"Nieznane_urządzenie_ID_{device_id_api}"
        device_raw_connections: List[Dict[str, Any]] = []
        idx_to_name_map = data_processing.build_ifindex_to_name_map(self.api_client, device_id_api, canonical_id)
        if not idx_to_name_map:
            logger.warning(
                f"Nie udało się zbudować mapy ifIndex->nazwa dla {canonical_id}. Odkrywanie SNMP może być niedokładne.")
        snmp_communities = config_loader.get_communities_to_try(self.config.get("default_snmp_communities", []))
        if snmp_communities:
            logger.info(f"  Próba metod SNMP dla {canonical_id} (communities: {len(snmp_communities)})...")
            device_raw_connections.extend(
                discovery.find_via_lldp_cdp_snmp(target_device_info, snmp_communities, idx_to_name_map))
            device_raw_connections.extend(
                discovery.find_via_qbridge_snmp(self.phys_mac_map, target_device_info, snmp_communities,
                                                idx_to_name_map))
            device_raw_connections.extend(
                discovery.find_via_snmp_fdb(self.phys_mac_map, target_device_info, snmp_communities, idx_to_name_map))
            device_raw_connections.extend(
                discovery.find_via_arp_snmp(self.phys_mac_map, target_device_info, snmp_communities, idx_to_name_map))
        else:
            logger.info(f"  Brak skonfigurowanych community SNMP. Pomijam metody SNMP dla {canonical_id}.")
        logger.info(f"  Próba metody API-FDB dla {canonical_id}...")
        device_raw_connections.extend(
            discovery.find_via_api_fdb(self.api_client, self.phys_mac_map, target_device_info))
        cli_creds_tuple = self._get_cli_credentials_for_device(target_device_info)
        if cli_creds_tuple:
            cli_user, cli_pass = cli_creds_tuple
            host_for_cli = target_device_info.get('hostname') or target_device_info.get('ip')
            if host_for_cli:
                if cli_user and cli_pass:
                    logger.info(f"  Próba metody CLI dla {canonical_id} (adres: {host_for_cli})...")
                    device_raw_connections.extend(discovery.find_via_cli(host_for_cli, cli_user, cli_pass))
                else:
                    logger.warning(f"  Pominięto CLI dla {canonical_id} - brak pełnych poświadczeń.")
            else:
                logger.warning(f"  Pominięto CLI dla {canonical_id} - brak adresu IP/hostname.")
        else:
            logger.info(f"  Pominięto CLI dla {canonical_id} - brak poświadczeń.")
        return device_raw_connections

    def _build_port_name_to_ifindex_map(self) -> None:
        logger.info("Rozpoczynam budowanie globalnej mapy NazwaPortu->ifIndex ze wszystkich urządzeń API...")
        self.port_name_to_ifindex_map = {}
        total_api_devices = len(self.all_devices_from_api)
        if total_api_devices == 0:
            logger.warning("Brak urządzeń z API do zbudowania mapy NazwaPortu->ifIndex.")
            return
        for i, device_api_entry in enumerate(self.all_devices_from_api):
            if (i + 1) % max(1, total_api_devices // 10) == 0 or (i + 1) == total_api_devices:
                logger.info(
                    f"  Budowanie mapy NazwaPortu->ifIndex: Przetworzono {i + 1}/{total_api_devices} urządzeń API...")
            dev_id_api = device_api_entry.get("device_id")
            canonical_id_api_dev = get_canonical_identifier(device_api_entry)
            if not dev_id_api or not canonical_id_api_dev:
                logger.debug(
                    f"  Mapa NazwaPortu->ifIndex: Pomijam urządzenie bez ID/kanonicznego ID: {device_api_entry.get('hostname') or device_api_entry}")
                continue
            try:
                ports_for_device = self.api_client.get_ports(str(dev_id_api), columns="ifIndex,ifName,ifDescr,ifAlias")
                if ports_for_device:
                    for p_info in ports_for_device:
                        ifindex = p_info.get("ifIndex")
                        if ifindex is None: continue
                        port_identifiers = [
                            str(p_info.get("ifName", "")).strip().lower(),
                            str(p_info.get("ifAlias", "")).strip().lower(),
                            str(p_info.get("ifDescr", "")).strip().lower()
                        ]
                        for port_name_key_part in set(filter(None, port_identifiers)):
                            map_key = (canonical_id_api_dev, port_name_key_part)
                            if map_key in self.port_name_to_ifindex_map and self.port_name_to_ifindex_map[
                                map_key] != ifindex:
                                logger.warning(
                                    f"  Mapa NazwaPortu->ifIndex: Konflikt dla klucza '{map_key}'. "
                                    f"Istniejący ifIndex: {self.port_name_to_ifindex_map[map_key]}, nowy ifIndex: {ifindex} "
                                    f"(dla portu ifName: '{p_info.get('ifName')}', ifDescr: '{p_info.get('ifDescr')}'). Nadpisuję nowym."
                                )
                            self.port_name_to_ifindex_map[map_key] = ifindex
            except Exception as e:
                logger.error(f"  Mapa NazwaPortu->ifIndex: Błąd dla {canonical_id_api_dev} (ID: {dev_id_api}): {e}",
                             exc_info=False)
        logger.info(
            f"✓ Zakończono budowę mapy NazwaPortu->ifIndex. Liczba wpisów: {len(self.port_name_to_ifindex_map)}.")

    def _get_ifindex_for_port(self, device_canonical_id: str, port_identifier_raw: Optional[str]) -> Optional[Any]:
        if not device_canonical_id or not port_identifier_raw: return None
        port_id_lower = str(port_identifier_raw).strip().lower()
        if not port_id_lower: return None

        ifindex = self.port_name_to_ifindex_map.get((device_canonical_id, port_id_lower))
        if ifindex is not None: return ifindex

        if port_id_lower.startswith("ifindex "):
            try:
                return int(port_id_lower.split("ifindex ")[1])
            except (ValueError, IndexError):
                pass

        logger.debug(f"Nie znaleziono ifIndex w mapie dla {device_canonical_id} : {port_id_lower}")
        return None

    def _enrich_connections(self, raw_connections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        logger.info(f"Rozpoczynam wzbogacanie {len(raw_connections)} surowych połączeń...")
        enriched_connections: List[Dict[str, Any]] = []
        for i, conn_raw in enumerate(raw_connections):
            # Użyj pprint.pformat dla czytelniejszego logowania słowników
            logger.debug(f"  Wzbogacanie [{i + 1}/{len(raw_connections)}]: Surowe: {pprint.pformat(conn_raw)}")

            local_original_id_raw = conn_raw.get('local_host')
            remote_original_id_raw = conn_raw.get('neighbor_host')
            local_if_raw = conn_raw.get('local_if')
            remote_if_raw = conn_raw.get('neighbor_if')

            local_device_api_details = find_device_in_list(local_original_id_raw, self.all_devices_from_api)
            remote_device_api_details = find_device_in_list(remote_original_id_raw, self.all_devices_from_api)

            local_canonical_id = get_canonical_identifier(local_device_api_details, local_original_id_raw)
            remote_canonical_id = get_canonical_identifier(remote_device_api_details, remote_original_id_raw)

            if not local_canonical_id or not remote_canonical_id:
                logger.debug(
                    f"    Pominięto (wzbogacanie): Brak kanonicznego ID dla hosta L:('{local_original_id_raw}' -> '{local_canonical_id}') "
                    f"lub Z:('{remote_original_id_raw}' -> '{remote_canonical_id}'). Połączenie: {conn_raw}")
                continue
            if local_canonical_id == remote_canonical_id:
                logger.debug(
                    f"    Pominięto (wzbogacanie): Self-connection ('{local_canonical_id}'). Połączenie: {conn_raw}")
                continue

            local_ifindex = conn_raw.get('local_ifindex')
            if local_ifindex is None: local_ifindex = self._get_ifindex_for_port(local_canonical_id, local_if_raw)
            remote_ifindex = conn_raw.get('remote_ifindex')
            if remote_ifindex is None: remote_ifindex = self._get_ifindex_for_port(remote_canonical_id, remote_if_raw)

            enriched_conn_data = {
                "local_device": local_canonical_id,
                "local_port": str(local_if_raw).strip() if local_if_raw is not None else None,
                "local_ifindex": int(local_ifindex) if local_ifindex is not None else None,
                "remote_device": remote_canonical_id,
                "remote_port": str(remote_if_raw).strip() if remote_if_raw is not None else None,
                "remote_ifindex": int(remote_ifindex) if remote_ifindex is not None else None,
                "vlan": conn_raw.get('vlan'),
                "discovery_method": conn_raw.get('via'),
                "local_device_ip": local_device_api_details.get('ip') if local_device_api_details else None,
                "local_device_hostname": local_device_api_details.get('hostname') if local_device_api_details else None,
                "local_device_sysname": local_device_api_details.get('sysName') if local_device_api_details else None,
                "local_device_purpose": local_device_api_details.get('purpose') if local_device_api_details else None,
                "local_device_os": local_device_api_details.get('os') if local_device_api_details else None,
                "remote_device_ip": remote_device_api_details.get('ip') if remote_device_api_details else None,
                "remote_device_hostname": remote_device_api_details.get(
                    'hostname') if remote_device_api_details else None,
                "remote_device_sysname": remote_device_api_details.get(
                    'sysName') if remote_device_api_details else None,
                "remote_device_purpose": remote_device_api_details.get(
                    'purpose') if remote_device_api_details else None,
                "remote_device_os": remote_device_api_details.get('os') if remote_device_api_details else None,
                "remote_device_original_identifier": remote_original_id_raw if not remote_device_api_details else None
            }
            final_enriched_conn = {k: v for k, v in enriched_conn_data.items() if v is not None}
            enriched_connections.append(final_enriched_conn)
            logger.debug(f"    Wzbogacone: {pprint.pformat(final_enriched_conn)}")
        logger.info(f"Zakończono wzbogacanie. Uzyskano {len(enriched_connections)} potencjalnie użytecznych połączeń.")
        return enriched_connections