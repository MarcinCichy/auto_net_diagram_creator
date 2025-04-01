import asyncio
from pysnmp.entity.engine import SnmpEngine
from pysnmp.hlapi import (
    CommunityData,
    UdpTransportTarget,
    ContextData,
    ObjectType,
    ObjectIdentity,
    nextCmd
)

class SNMPDevice:
    def __init__(self, ip_address, community="public", port=161):
        self.ip = ip_address
        self.community = community
        self.port = port

    async def get_interfaces(self):
        """
        Asynchronicznie pobiera informacje o interfejsach.
        Korzysta z funkcji walk_oid uruchomionej w osobnym wątku.
        """
        data = {}
        # Lista OID-ów: etykieta i odpowiadający OID
        oids = [
            ("name", "1.3.6.1.2.1.2.2.1.2"),         # Nazwa interfejsu
            ("admin_status", "1.3.6.1.2.1.2.2.1.7"), # Status administracyjny (1=up, 2=down)
            ("oper_status", "1.3.6.1.2.1.2.2.1.8")     # Status operacyjny (1=up, 2=down)
        ]
        # Dla każdego OID wykonaj asynchroniczny "walk" – funkcja walk_oid uruchomiona w osobnym wątku
        for label, oid in oids:
            result = await asyncio.to_thread(self.walk_oid, oid)
            for idx, value in result.items():
                if idx not in data:
                    data[idx] = {}
                data[idx][label] = value
        return data

    def walk_oid(self, oid):
        """
        Blokująca funkcja wykonująca iterację po danym OID przy użyciu nextCmd.
        Zwraca słownik wyników dla danego OID.
        """
        results = {}
        for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
                SnmpEngine(),
                CommunityData(self.community),
                UdpTransportTarget((self.ip, self.port)),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
                lexicographicMode=False
        ):
            if errorIndication:
                raise Exception(f"SNMP error: {errorIndication}")
            if errorStatus:
                raise Exception(f"SNMP error: {errorStatus.prettyPrint()}")
            for varBind in varBinds:
                # Indeks (ostatnia część OID) identyfikuje interfejs
                idx = str(varBind[0]).split('.')[-1]
                value = str(varBind[1])
                results[idx] = value
        return results

    async def display_interfaces(self):
        """
        Asynchronicznie pobiera dane o interfejsach i wyświetla je w konsoli.
        """
        interfaces = await self.get_interfaces()
        for idx, iface in interfaces.items():
            name = iface.get("name", "n/a")
            admin = "up" if iface.get("admin_status") == "1" else "down"
            oper = "up" if iface.get("oper_status") == "1" else "down"
            print(f"[{idx}] {name:<20} | Admin: {admin:<4} | Oper: {oper:<4}")

if __name__ == "__main__":
    # Podmień IP na właściwy adres Twojego urządzenia
    device = SNMPDevice(ip_address="172.16.16.70", community="public")
    asyncio.run(device.display_interfaces())
