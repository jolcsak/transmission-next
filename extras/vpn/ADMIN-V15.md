# VPN-beállítások a webes adminfelületen

Az Áttekintés oldal alján megjelent az **Adminisztráció · VPN / PureVPN** panel. A telemetria-felügyelő kezeli a VPN konfigurációs kéréseit is; továbbra is a daemon nem root felhasználójával fusson.

Megadható:

- PureVPN szolgáltató (`purevpn`), a manuális VPN-kapcsolathoz kapott VPN-felhasználónév és jelszó.
- OpenVPN-profil feltöltése `.ovpn` fájlból vagy bemásolása. A szerver, port, UDP/TCP és titkosítás a profilból származik.
- Alternatívaként a szerveren már meglévő profil- és hitelesítőfájl abszolút útvonala.
- DNS-szerverek, RPC-port, indulási időkorlát, privát /30-as belső alhálózat.
- A daemon Linux-felhasználója, futtatható fájlja, adat- és config könyvtára. Root felhasználó nem engedélyezett. A config könyvtárnak meg kell egyeznie a jelenleg használt daemon config könyvtárával.

**Tárolás: `<config könyvtár>/vpn.json`, Linuxon 0600 jogosultsággal.** Az inline profil és jelszó is ebben van. A JSON export a jelszót és a profil esetleges privát kulcsát is tartalmazza; a fájlt ennek megfelelően kezeld. A JSON import csak a szerkesztőt tölti ki, mentésig nem módosítja a szervert.

Mentéskor a rendszer validálja a konfigurációt, atomi fájlcserével ment, azonos tartalomnál kihagyja az újraírást. Hibánál a korábbi JSON megmarad. Az MQTT/InfluxDB konfiguráció külön, a `telemetry.json` fájlban marad.

## Ellenőrzés és alkalmazás

A **VPN-profil ellenőrzése** a meglévő szigorú profilfeldolgozót futtatja: ellenőrzi a támogatott opciókat, a hitelesítés formátumát, útvonalakat, felhasználót és hálózati paramétereket. Tiltott OpenVPN hook, script, plugin vagy beágyazott konfiguráció nem kerülhet át. A feltöltött profilhoz használj beágyazott tanúsítványokat, vagy a szerveren elérhető abszolút tanúsítványútvonalakat.

Ez helyi ellenőrzés: **nem kapcsolódik PureVPN-hez, nem hitelesíti a fiókot, és nem bizonyítja a szervertanúsítvány érvényességét**. A valódi tanúsítvány- és fiókellenőrzést az OpenVPN a kapcsolódáskor végzi.

A **VPN-beállítások mentése** nem állítja le, nem indítja újra és nem helyezi át a jelenleg futó daemont. A VPN-kezelő root jogosultságot igényel a névtérhez és tűzfalhoz; az adminpanel nem kapott általános sudo-jogot, és nem indít root parancsokat.

A mentett konfiguráció a következő VPN-es indításkor használható. Előbb állítsd le az ugyanazt a config könyvtárat/RPC-portot használó VPN nélküli példányt, majd például:

```sh
sudo python3 /opt/transmission/extras/vpn/controller.py check --config /teljes/config/utvonal/vpn.json
sudo python3 /opt/transmission/extras/vpn/controller.py run --config /teljes/config/utvonal/vpn.json
```

A `run-daemon.sh` továbbra is a VPN-es indító: `TRANSMISSION_VPN_CONFIG` esetén azt használja, egyébként először `${TRANSMISSION_CONFIG_DIR:-<csomag>/config}/vpn.json`-t, és ha nincs ilyen, a korábbi `<csomag>/vpn.json`-t. A VPN nélküli indító viselkedése nem változott. A meglévő VPN-szolgáltatás új konfigurációval történő újraindítása megszakíthatja a kapcsolatot és a webes elérést.

Az inline profil/jelszó esetén a VPN-felügyelő indulás/CLI-ellenőrzés előtt létrehozza a privát `vpn-managed.ovpn` és `vpn-credentials.txt` fájlokat a config könyvtárban. A hálózati forgalom továbbra is a meglévő névtér/tűzfal kill switchén halad át; annak szabályai nem változtak.

Az alapértékeket a nem root adminfelügyelő `vpn-defaults.json` fájlban adja át a webnek. Ez nem tartalmaz hitelesítési adatokat. A meglévő konfigurációt nem írja felül.

## API

A `telemetry_config_get` eredménye új mezőket tartalmaz: `vpn_configuration`, `vpn_file`, `vpn_defaults`. A korábbi telemetriamezők változatlanok.

VPN-művelet: `telemetry_config_apply`, `params.target="vpn"`, `action="save"` vagy `"test"`, egyedi `job_id`, és a VPN JSON szövege a `configuration` mezőben. A mentési eredmény `restart_required:true` értéke azt jelzi, hogy a VPN-es indítás/újraindítás külön lépés. Eredménykövetés a meglévő `job_id` mechanizmussal történik.

A v13-ban hozzáadott MQTT/InfluxDB env-kezelés megmaradt. A VPN indítókonfiguráció fájlját a már meglévő `TRANSMISSION_VPN_CONFIG` változóval lehet megadni; a VPN-panel ezen kiadásban a JSON-konfigurációt kezeli.
