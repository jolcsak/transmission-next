# VPN-kapcsolatteszt a webes adminból (v16)

A **VPN-profil ellenőrzése** helyi formai ellenőrzés. Az új **VPN-kapcsolat tesztelése** a szerkesztőben szereplő, akár még el nem mentett adatokkal valóban kapcsolódik az OpenVPN-szerverhez: ellenőrzi a szerver tanúsítványát, hitelesít, megvárja a CONNECTED/SUCCESS állapotot és az IPv4-es TUN interfészt, majd bontja a tesztkapcsolatot.

Ez nem sebesség-, DNS- vagy teljes internetelérési teszt. A sikeres eredmény az alagút létrejöttét igazolja. Az időadat a kapcsolódást és a bontást is tartalmazza.

## Telepítés Linuxon

A szokásos `iproute2`, `nftables`, `openvpn`, `util-linux`, Python 3 és `/dev/net/tun` szükséges, engedélyezett IPv4 forwarding mellett. A webes felügyelő továbbra is a daemon nem-root felhasználójaként fut.

1. A csomagot root tulajdonú, mások által nem írható könyvtárba telepítsd, például `/opt/transmission` alá. A teljes `extras/vpn` kód és szülőkönyvtárai legyenek védettek; ne futtasd rootként egy webes felhasználó által módosítható munkapéldányból.
2. Másold az `extras/vpn/transmission-vpn-test.service` egységet az `/etc/systemd/system/` könyvtárba. Az `ExecStart` útvonalát és `--user transmission` értékét igazítsd a telepítéshez és a daemon valódi felhasználójához.
3. `systemctl daemon-reload`, majd `systemctl enable --now transmission-vpn-test`.

Kézi indítás: `sudo python3 /opt/transmission/extras/vpn/probe_service.py --user transmission`.

## Működés és korlátok

- A szolgáltatás tétlenül blokkoló Unix socketen vár; nincs periodikus VPN-próba vagy internetes IP-lekérdezés. Egyszerre egy tesztet végez.
- A socket jogosultsága 0600, a Linux peer UID-jét is ellenőrzi. A root segéd nem fogad fájlútvonalakat vagy futtatandó parancsot. A profil külső tanúsítványait a nem-root admin olvassa be; a root oldalon kizárólag inline tanúsítvány és kulcs engedélyezett.
- Maximum 64 KiB kérés, legfeljebb 60 másodperces kapcsolódási kísérlet (a beállított rövidebb időkorlát érvényes), utána bontás. A webes várakozás a takarítás idejét is lefedi.
- Elkülönített `transmission-vpn-test` névtér, `trvpntest0` interfész és `transmission_vpn_test` tűzfaltábla. A segéd a meglévő útvonalakkal nem ütköző teszt-alhálózatot választ. A kill switch már az interfész aktiválása előtt él.
- Nem módosítja a daemon beállításait, a mentett `vpn.json` fájlt vagy az aktív VPN állapotát; nem indít új daemont. A szolgáltató azonban korlátozhatja az egy fiókhoz tartozó párhuzamos kapcsolatokat.
- A privát tesztprofil és hitelesítési fájl a végén törlődik. Hiba esetén a webes válasz nem tartalmaz jelszót, profiladatot vagy szervernaplót. Rootként a `/run/transmission-vpn-test/openvpn.log` segíthet a hibakeresésben.

API: `telemetry_config_apply`, `target: "vpn"`, `action: "connect_test"`, a megszokott `job_id` és JSON szövegként küldött `configuration`. Eredmény: `telemetry_config_get` → `job`, `state: "tested"`, `checks.authentication/tunnel: "ok"`, `elapsed_ms`; sikertelenségnél `state: "error"`. Az RPC hitelesítése ugyanaz, mint a többi adminműveleté.
