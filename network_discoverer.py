# --- network_discoverer.py ---
import time
import logging
import re
import pprint
from typing import Dict, List, Any, Optional, Tuple, Callable

from librenms_client import LibreNMSAPI

try:
    import snmp_utils

    SNMP_UTILS_AVAILABLE = True
except ImportError:
    SNMP_UTILS_AVAILABLE = False
    logging.getLogger(__name__).warning("Moduł snmp_utils.py nie znaleziony. Funkcje SNMP nie będą działać.")


    class snmp_utils:
        @staticmethod
        def snmp_get_lldp_neighbors(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[int, str, str]]]: return None

        @staticmethod
        def snmp_get_cdp_neighbors(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[int, str, str]]]: return None

        @staticmethod
        def snmp_get_bridge_baseport_ifindex(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            Dict[int, int]]: return None

        @staticmethod
        def snmp_get_fdb_entries(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[str, int]]]: return None

        @staticmethod
        def snmp_get_qbridge_fdb(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[str, int, int]]]: return None

        @staticmethod
        def snmp_get_arp_entries(h: str, c: str, timeout: int = 0, retries: int = 0) -> Optional[
            List[Tuple[str, str, int]]]: return None

import cli_utils
import config_loader
import data_processing
import discovery
import file_io
from utils import find_device_in_list, get_canonical_identifier, normalize_interface_name  # Zmieniony import

logger = logging.getLogger(__name__)


class NetworkDiscoverer:
    def __init__(self, api_client: LibreNMSAPI, config: Dict[str, Any],
                 ip_list_path: str, conn_txt_path: str, conn_json_path: str):
        self.api_client = api_client
        self.config = config
        self.ip_list_path = ip_list_path
        self.conn_txt_path = conn_txt_path
        self.conn_json_path = conn_json_path
        self.phys_mac_map: Dict[str, Dict[str, Any]] = {}
        self.all_devices_from_api: List[Dict[str, Any]] = []
        # Mapa: (canonical_id_urządzenia_lower, nazwa_portu_lower_lub_alias_lower) -> ifIndex
        self.port_name_to_ifindex_map: Dict[Tuple[str, str], Any] = {}
        self.cli_credentials: Dict[str, Any] = config.get('cli_credentials', {"defaults": {},
                                                                              "devices": []})  # Upewnij się, że zawsze jest struktura
        logger.debug("NetworkDiscoverer zainicjalizowany.")

    def discover_connections(self) -> None:
        logger.info("=== Rozpoczynanie Fazy Odkrywania Połączeń ===")
        discovery_start_time = time.time()
        logger.info("[Odkrywanie 1/5] Budowanie globalnej mapy MAC adresów...")
        self.phys_mac_map = data_processing.build_phys_mac_map(self.api_client)
        if not self.phys_mac_map: logger.warning("Nie udało się zbudować mapy MAC lub jest pusta.")

        logger.info(f"[Odkrywanie 2/5] Wczytywanie listy urządzeń docelowych z '{self.ip_list_path}'...")
        target_ips_or_hosts = file_io.load_ip_list(self.ip_list_path)
        if not target_ips_or_hosts:
            logger.warning("Lista urządzeń docelowych jest pusta. Kończę fazę odkrywania.");
            self._save_empty_connections();
            return

        logger.info("[Odkrywanie 3/5] Pobieranie pełnej listy urządzeń z API LibreNMS...")
        self.all_devices_from_api = self.api_client.get_devices(
            columns="device_id,hostname,ip,sysName,purpose,os,hardware,version,serial,type,status")
        if not self.all_devices_from_api:
            logger.error("Nie udało się pobrać listy urządzeń z API. Kończę fazę odkrywania.");
            self._save_empty_connections();
            return
        logger.info(f"Pobrano informacje o {len(self.all_devices_from_api)} urządzeniach z API.")

        self._build_port_name_to_ifindex_map()  # Zbuduj mapę PRZED przetwarzaniem urządzeń

        logger.info("[Odkrywanie 4/5] Przetwarzanie urządzeń docelowych i odkrywanie surowych połączeń...")
        all_found_connections_raw = self._process_all_target_devices(target_ips_or_hosts)

        logger.info("\n[Odkrywanie 5/5] Wzbogacanie, normalizacja, deduplikacja i zapis wyników...")
        if all_found_connections_raw:
            logger.info(f"Zebrano {len(all_found_connections_raw)} surowych wpisów. Rozpoczynam wzbogacanie...")
            enriched_connections = self._enrich_connections(all_found_connections_raw)
            logger.info(f"Po wzbogaceniu {len(enriched_connections)} wpisów. Rozpoczynam deduplikację...")
            final_connections = data_processing.deduplicate_connections(enriched_connections)
            logger.info(f"Zapisywanie {len(final_connections)} unikalnych połączeń...")
            file_io.save_connections_txt(final_connections, self.conn_txt_path)
            file_io.save_connections_json(final_connections, self.conn_json_path)
        else:
            logger.info("Nie znaleziono żadnych surowych połączeń. Zapisuję puste pliki.");
            self._save_empty_connections()
        discovery_end_time = time.time()
        logger.info(f"=== Zakończono Fazę Odkrywania (czas: {discovery_end_time - discovery_start_time:.2f} sek.) ===")

    def _save_empty_connections(self) -> None:
        file_io.save_connections_txt([], self.conn_txt_path)
        file_io.save_connections_json([], self.conn_json_path)

    def _get_cli_credentials_for_device(self, device_info: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        if not device_info: return None
        canonical_id_device = get_canonical_identifier(device_info)

        logger.debug(f"Wyszukiwanie poświadczeń CLI dla urządzenia: {canonical_id_device or device_info.get('ip')}")

        # Lista identyfikatorów do sprawdzenia dla dopasowania "exact"
        ids_to_check_exact: List[str] = []
        if device_info.get('ip'): ids_to_check_exact.append(str(device_info['ip']).lower().strip())
        if device_info.get('hostname'): ids_to_check_exact.append(str(device_info['hostname']).lower().strip())
        if device_info.get('sysName'): ids_to_check_exact.append(str(device_info['sysName']).lower().strip())
        if device_info.get('purpose'): ids_to_check_exact.append(str(device_info['purpose']).lower().strip())
        if canonical_id_device: ids_to_check_exact.append(canonical_id_device.lower().strip())
        ids_to_check_exact = list(set(filter(None, ids_to_check_exact)))  # Usuń duplikaty i puste
        logger.debug(f"  Identyfikatory dla 'exact' match: {ids_to_check_exact}")

        # Lista identyfikatorów do sprawdzenia dla dopasowania "regex" (zazwyczaj nazwy)
        identifiers_for_regex: List[str] = list(set(filter(None, [
            str(s).strip() for s in
            [device_info.get('hostname'), device_info.get('sysName'), device_info.get('purpose'), canonical_id_device]
            if s
        ])))
        logger.debug(f"  Identyfikatory dla 'regex' match: {identifiers_for_regex}")

        device_creds_list = self.cli_credentials.get("devices", [])
        # Najpierw szukaj dopasowań "exact"
        for cred_entry in device_creds_list:
            identifier_cred = str(
                cred_entry.get("identifier", "")).strip()  # Nie rób tu lower(), bo regex może być case-sensitive
            match_type = cred_entry.get("match", "exact")  # Domyślnie "exact"

            if match_type == "exact":
                if identifier_cred.lower() in ids_to_check_exact:  # Porównanie lower() dla exact
                    user, password = cred_entry.get("cli_user"), cred_entry.get("cli_pass")
                    if user and password:
                        logger.info(
                            f"  CLI Creds: Znaleziono dokładne dopasowanie ('{identifier_cred}') dla {canonical_id_device or device_info.get('ip')}.")
                        return user, password

        # Następnie szukaj dopasowań "regex"
        for cred_entry in device_creds_list:
            pattern_cred = cred_entry.get("identifier")  # Dla regex, bierzemy oryginalny case
            match_type = cred_entry.get("match")

            if match_type == "regex" and pattern_cred:
                try:
                    # Użytkownik może chcieć regex wrażliwy na wielkość liter, więc nie kompiluj z re.IGNORECASE domyślnie
                    # chyba że wzorzec sam to specyfikuje np. (?i)
                    regex = re.compile(pattern_cred)
                    for val_to_check in identifiers_for_regex:
                        if regex.fullmatch(str(val_to_check)):  # fullmatch, aby cały string pasował
                            user, password = cred_entry.get("cli_user"), cred_entry.get("cli_pass")
                            if user and password:
                                logger.info(
                                    f"  CLI Creds: Znaleziono dopasowanie regex ('{pattern_cred}') dla '{val_to_check}' urządzenia {canonical_id_device or device_info.get('ip')}.")
                                return user, password
                except re.error as e_re:
                    logger.warning(f"  CLI Creds: Błąd kompilacji regex '{pattern_cred}': {e_re}")
                except Exception as e_re_match:
                    logger.warning(f"  CLI Creds: Błąd dopasowania regex '{pattern_cred}': {e_re_match}",
                                   exc_info=False)

        defaults = self.cli_credentials.get("defaults", {})
        default_user, default_pass = defaults.get("cli_user"), defaults.get("cli_pass")
        if default_user and default_pass:
            logger.info(
                f"  CLI Creds: Używanie domyślnych poświadczeń dla {canonical_id_device or device_info.get('ip')}.")
            return default_user, default_pass

        logger.info(
            f"  CLI Creds: Nie znaleziono specyficznych ani domyślnych poświadczeń dla {canonical_id_device or device_info.get('ip')}.")
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
            if not canonical_id:  # Dodatkowe zabezpieczenie
                logger.warning(f"Nie można ustalić kanonicznego ID dla '{ip_or_host_target}'. Pomijam.")
                continue
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
        if not idx_to_name_map: logger.warning(
            f"Nie udało się zbudować mapy ifIndex->nazwa dla {canonical_id}. Odkrywanie SNMP może być niedokładne.")

        snmp_communities = config_loader.get_communities_to_try(self.config)

        if SNMP_UTILS_AVAILABLE and snmp_communities:
            logger.info(f"  Próba metod SNMP dla {canonical_id} (communities: {len(snmp_communities)})...")
            device_raw_connections.extend(
                discovery.find_via_lldp_cdp_snmp(target_device_info, snmp_communities, idx_to_name_map, self.config))
            device_raw_connections.extend(
                discovery.find_via_qbridge_snmp(self.phys_mac_map, target_device_info, snmp_communities,
                                                idx_to_name_map, self.config))
            device_raw_connections.extend(
                discovery.find_via_snmp_fdb(self.phys_mac_map, target_device_info, snmp_communities, idx_to_name_map,
                                            self.config))
            device_raw_connections.extend(
                discovery.find_via_arp_snmp(self.phys_mac_map, target_device_info, snmp_communities, idx_to_name_map,
                                            self.config))
        elif not SNMP_UTILS_AVAILABLE:
            logger.warning("  Moduł snmp_utils niedostępny. Pomijam metody SNMP.")
        else:
            logger.info(f"  Brak skonfigurowanych community SNMP. Pomijam metody SNMP dla {canonical_id}.")

        logger.info(f"  Próba metody API-FDB dla {canonical_id}...")
        device_raw_connections.extend(
            discovery.find_via_api_fdb(self.api_client, self.phys_mac_map, target_device_info))

        enable_cli = self.config.get('enable_cli_discovery', True)
        if enable_cli:
            cli_creds_tuple = self._get_cli_credentials_for_device(target_device_info)
            if cli_creds_tuple:
                cli_user, cli_pass = cli_creds_tuple
                host_for_cli = target_device_info.get('hostname') or target_device_info.get('ip')
                if host_for_cli:
                    logger.info(f"  Próba metody CLI dla {canonical_id} (adres: {host_for_cli})...");
                    cli_neighbors = cli_utils.cli_get_neighbors_enhanced(host_for_cli, cli_user, cli_pass, self.config)
                    device_raw_connections.extend(cli_neighbors)
                else:
                    logger.warning(f"  Pominięto CLI dla {canonical_id} - brak adresu IP/hostname do połączenia.")
            else:
                logger.info(f"  Pominięto CLI dla {canonical_id} - brak skonfigurowanych/dopasowanych poświadczeń.")
        else:
            logger.info(
                f"  Odkrywanie przez CLI jest wyłączone w konfiguracji (enable_cli_discovery=False). Pomijam dla {canonical_id}.")

        return device_raw_connections

    def _build_port_name_to_ifindex_map(self) -> None:
        logger.info("Rozpoczynam budowanie globalnej mapy NazwaPortu->ifIndex...")
        self.port_name_to_ifindex_map = {}  # (device_canonical_id_lower, port_name_lower) -> ifIndex
        interface_replacements = self.config.get('interface_name_replacements', {})
        total_api_devices = len(self.all_devices_from_api)
        if total_api_devices == 0:
            logger.warning("Brak urządzeń z API do zbudowania mapy NazwaPortu->ifIndex.");
            return

        for i, device_api_entry in enumerate(self.all_devices_from_api):
            if (i + 1) % max(1, total_api_devices // 20) == 0 or (i + 1) == total_api_devices:
                logger.info(
                    f"  Budowanie mapy NazwaPortu->ifIndex: Przetworzono {i + 1}/{total_api_devices} urządzeń API...")

            dev_id_api = device_api_entry.get("device_id")
            # Użyj get_canonical_identifier, aby mieć spójny identyfikator urządzenia
            canonical_id_api_dev = get_canonical_identifier(device_api_entry)

            if not dev_id_api or not canonical_id_api_dev:
                logger.debug(
                    f"  Mapa NazwaPortu->ifIndex: Pomijam urządzenie bez ID API lub kanonicznego ID: {device_api_entry.get('hostname') or device_api_entry.get('ip') or 'Nieznane'}");
                continue

            dev_id_lower_for_map = canonical_id_api_dev.lower()

            try:
                ports_for_device = self.api_client.get_ports(str(dev_id_api),
                                                             columns="port_id,ifIndex,ifName,ifDescr,ifAlias")
                if not ports_for_device: continue

                for p_info in ports_for_device:
                    ifindex = p_info.get("ifIndex")
                    if ifindex is None: continue

                    # Klucz główny: ifName (znormalizowany)
                    if_name_raw = str(p_info.get("ifName", "")).strip()
                    if if_name_raw:
                        # Normalizuj ifName przed dodaniem do mapy
                        normalized_if_name = normalize_interface_name(if_name_raw, interface_replacements)
                        map_key_ifname = (dev_id_lower_for_map, normalized_if_name.lower())
                        if map_key_ifname in self.port_name_to_ifindex_map and self.port_name_to_ifindex_map[
                            map_key_ifname] != ifindex:
                            logger.warning(
                                f"  Mapa NazwaPortu->ifIndex: Konflikt dla znormalizowanego ifName '{map_key_ifname}' (oryginalny ifName: '{if_name_raw}'). Istniejący ifIndex: {self.port_name_to_ifindex_map[map_key_ifname]}, nowy ifIndex: {ifindex}. Nadpisuję nowym.")
                        self.port_name_to_ifindex_map[map_key_ifname] = ifindex

                    # Klucze dodatkowe: ifAlias, ifDescr (znormalizowane, jeśli nie są takie same jak ifName)
                    for alias_type_key, alias_val_raw_any in [("ifAlias", p_info.get("ifAlias")),
                                                              ("ifDescr", p_info.get("ifDescr"))]:
                        alias_val_raw = str(alias_val_raw_any or "").strip()
                        if alias_val_raw and (not if_name_raw or alias_val_raw.lower() != if_name_raw.lower()):
                            normalized_alias = normalize_interface_name(alias_val_raw, interface_replacements)
                            map_key_alias = (dev_id_lower_for_map, normalized_alias.lower())
                            if map_key_alias not in self.port_name_to_ifindex_map:
                                self.port_name_to_ifindex_map[map_key_alias] = ifindex
                            elif self.port_name_to_ifindex_map[map_key_alias] != ifindex:
                                logger.debug(  # Zmieniono na DEBUG, bo może być dużo takich przypadków
                                    f"  Mapa NazwaPortu->ifIndex: Konflikt dla klucza {alias_type_key} '{map_key_alias}' (oryginalny alias: '{alias_val_raw}'). "
                                    f"Istniejący ifIndex: {self.port_name_to_ifindex_map[map_key_alias]}, nowy ifIndex: {ifindex} "
                                    f"(dla portu ifName: '{if_name_raw}'). Nie nadpisuję aliasu/opisu, jeśli ifName już zmapował ten ifIndex lub inny alias.")
            except Exception as e:
                logger.error(f"  Mapa NazwaPortu->ifIndex: Błąd dla {canonical_id_api_dev} (ID: {dev_id_api}): {e}",
                             exc_info=False)
                logger.debug(f"  Mapa NazwaPortu->ifIndex: Pełny traceback dla {canonical_id_api_dev}:", exc_info=True)
        logger.info(
            f"✓ Zakończono budowę mapy NazwaPortu->ifIndex. Liczba wpisów: {len(self.port_name_to_ifindex_map)}.")

    def _enrich_connections(self, raw_connections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        logger.info(f"Rozpoczynam wzbogacanie {len(raw_connections)} surowych połączeń...")
        enriched_connections: List[Dict[str, Any]] = []
        interface_replacements = self.config.get('interface_name_replacements', {})

        for i, conn_raw in enumerate(raw_connections):
            logger.debug(f"  Wzbogacanie [{i + 1}/{len(raw_connections)}]: Surowe: {pprint.pformat(conn_raw)}")

            local_original_id_raw = conn_raw.get('local_host')
            remote_original_id_raw = conn_raw.get('neighbor_host')
            local_if_raw = str(conn_raw.get('local_if') or "").strip()  # Upewnij się, że jest stringiem
            remote_if_raw = str(conn_raw.get('neighbor_if') or "").strip()  # Upewnij się, że jest stringiem

            local_device_api_details = find_device_in_list(local_original_id_raw, self.all_devices_from_api)
            remote_device_api_details = find_device_in_list(remote_original_id_raw, self.all_devices_from_api)

            local_canonical_id = get_canonical_identifier(local_device_api_details, local_original_id_raw)
            remote_canonical_id = get_canonical_identifier(remote_device_api_details, remote_original_id_raw)

            if not local_canonical_id:
                logger.warning(
                    f"    Pominięto (wzbogacanie): Brak kanonicznego ID dla urządzenia lokalnego. Surowe ID: '{local_original_id_raw}'. Połączenie: {conn_raw}");
                continue
            if not remote_canonical_id:
                # To może być normalne, jeśli urządzenie zdalne nie jest w LibreNMS
                logger.debug(
                    f"    Informacja (wzbogacanie): Brak kanonicznego ID dla urządzenia zdalnego. Surowe ID: '{remote_original_id_raw}'. Połączenie: {conn_raw}");
                # Kontynuujemy, ale remote_ifindex prawdopodobnie będzie None

            # Normalizuj nazwy portów PRZED próbą znalezienia ifIndex, jeśli ifIndex nie jest już dostępny
            normalized_local_if = normalize_interface_name(local_if_raw,
                                                           interface_replacements) if local_if_raw else None
            normalized_remote_if = normalize_interface_name(remote_if_raw,
                                                            interface_replacements) if remote_if_raw else None

            logger.debug(
                f"    Normalizacja portów: Lokalny: '{local_if_raw}' -> '{normalized_local_if}', Zdalny: '{remote_if_raw}' -> '{normalized_remote_if}'")

            local_ifindex = conn_raw.get('local_ifindex')
            if local_ifindex is None and local_canonical_id and normalized_local_if:
                local_ifindex = self._get_ifindex_for_port(local_canonical_id,
                                                           normalized_local_if)  # Użyj znormalizowanej nazwy
                if local_ifindex is None and local_if_raw != normalized_local_if:  # Jeśli normalizacja pomogła, a surowa nie, spróbuj surowej
                    local_ifindex = self._get_ifindex_for_port(local_canonical_id, local_if_raw)

            remote_ifindex = conn_raw.get('remote_ifindex')
            if remote_ifindex is None and remote_canonical_id and normalized_remote_if:
                remote_ifindex = self._get_ifindex_for_port(remote_canonical_id, normalized_remote_if)
                if remote_ifindex is None and remote_if_raw != normalized_remote_if:
                    remote_ifindex = self._get_ifindex_for_port(remote_canonical_id, remote_if_raw)

            # Sprawdzenie self-connection po ustaleniu kanonicznych ID
            if local_canonical_id and remote_canonical_id and local_canonical_id == remote_canonical_id:
                # Dla self-connection, sprawdź czy porty (po normalizacji) są różne
                # lub czy ifIndexy są różne (jeśli dostępne)
                ports_are_different = True  # Załóż, że są różne, chyba że udowodnimy inaczej
                if normalized_local_if and normalized_remote_if and normalized_local_if.lower() == normalized_remote_if.lower():
                    ports_are_different = False

                if local_ifindex is not None and remote_ifindex is not None and local_ifindex == remote_ifindex:
                    ports_are_different = False

                if not ports_are_different:
                    logger.debug(
                        f"    Pominięto (wzbogacanie): Self-connection na tym samym porcie ('{local_canonical_id}':'{normalized_local_if}' lub ifIndex: {local_ifindex}). Połączenie: {conn_raw}");
                    continue

            enriched_conn_data = {
                "local_device": local_canonical_id,
                "local_port": normalized_local_if or local_if_raw,
                # Użyj znormalizowanej, jeśli istnieje, inaczej surowej
                "local_ifindex": int(local_ifindex) if local_ifindex is not None else None,
                "remote_device": remote_canonical_id,  # Może być None, jeśli urządzenie zdalne nie jest znane
                "remote_port": normalized_remote_if or remote_if_raw,
                "remote_ifindex": int(remote_ifindex) if remote_ifindex is not None else None,
                "vlan": conn_raw.get('vlan'),
                "discovery_method": conn_raw.get('via'),
                "local_device_ip": local_device_api_details.get('ip') if local_device_api_details else None,
                "remote_device_ip": remote_device_api_details.get('ip') if remote_device_api_details else None,
            }
            # Usuń klucze z wartością None, z wyjątkiem tych, które mogą być celowo None (vlan, ifindexy)
            final_enriched_conn = {k: v for k, v in enriched_conn_data.items() if
                                   v is not None or k in ["vlan", "local_ifindex", "remote_ifindex", "remote_device",
                                                          "remote_device_ip"]}
            enriched_connections.append(final_enriched_conn)
            logger.debug(f"    Wzbogacone: {pprint.pformat(final_enriched_conn)}")
        logger.info(f"Zakończono wzbogacanie. Uzyskano {len(enriched_connections)} potencjalnie użytecznych połączeń.")
        return enriched_connections

