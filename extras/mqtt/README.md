# Telemetria API és opcionális MQTT-export

## v10 optimalizálások

- Tartós HTTP/HTTPS-kapcsolat az API-hoz, újracsatlakozással és a 409-es tokenfrissítés megtartásával. A kapcsolat csak olvasó kérését szállítási hibánál egyszer megismétli.
- A változásvizsgálat közvetlen adat-összehasonlítást használ: JSON-szerializálás csak tényleges MQTT-küldéskor történik. Az API- és MQTT-adatformátum változatlan.
- Tartós RPC/publikálási hiba esetén exponenciálisan ritkuló próbálkozás, alapbeállítással 30–300 másodperc között. Ha a beállított mintavétel hosszabb, az lesz a felső korlát is. Siker után visszaáll a szokásos ütem.
- A daemon könyvtár-összesítése elkerüli a torrentenkénti ideiglenes útvonalmásolatokat, és előre lefoglalja az eredménytárolók szükséges kapacitását.

Helyi, 1000 könyvtáras szintetikus mérésben a változásvizsgálat 1000 ismétlésének medián CPU-ideje 548,5 ms-ról 35,1 ms-ra csökkent (7 futás). A mérés külön létrehozott, azonos tartalmú mintákat hasonlít össze; nem tartalmazza a JSON-feldolgozást vagy hálózati I/O-t. A részfeladat átmeneti Python-allokációs csúcsa 96 200-ról 208 bájtra csökkent; ez **nem a folyamat teljes memóriafogyasztása**. Helyi HTTP/1.1 tesztszerveren 100 lekérdezéshez 101 helyett 1 kapcsolat kellett, a tokenkézfogással együtt.

Ellenőrzés: 227 C++ regressziós teszt, 5 valódi Mosquitto/daemon integrációs teszt és 3 HTTP-kapcsolat/adat-összehasonlítás teszt. A korábbi webfelület és profilok megmaradtak.

Az export külön, nem root Python-folyamatként fut a hoston. Nem változtatja meg a VPN kill switchét vagy a torrentmotor működését. Alapból nincs elindítva. A VPN-csomagnál a hostoldali RPC-relayhez kapcsolódik; a broker elérhetőségét a host hálózata adja, nem a VPN-névtér.

## API

Az új, csak olvasó `telemetry_get` JSON-RPC 2.0 metódus a meglévő `/transmission/rpc` végponton érhető el, annak hitelesítésével, hozzáférési szabályaival és `X-Transmission-Session-Id` / HTTP 409 kézfogásával.

```json
{"jsonrpc":"2.0","id":1,"method":"telemetry_get","params":{"include_history":true}}
```

A `result` objektum:

| Mező | Jelentés |
| --- | --- |
| `schema_version` | Jelenleg `1` |
| `sampled_at` | Mintavétel, Unix UTC másodperc |
| `storage_status.directories` | Könyvtáranként `path`, `profile` (`hdd`, `ssd`, `manual`, `unknown`), `load` (`normal`, `busy`, `critical`, `unknown`), `torrents`, `active` |
| `storage_status.system_state` | Összesített `normal`, `busy`, `critical` vagy `unknown` terhelési állapot |
| `storage_status.*_wait_percent` | `cpu`, `memory`, `io` várakozási arányok, Linux PSI avg10 százalék; nem CPU-kihasználtság. Hiányzó mérésnél a mező hiányzik |
| `storage_status.cache_*_bytes` | `reserved`, `pending`, `capacity` cache méretek bájtban |
| `storage_status.cache_congested` | Cache-torlódás |
| `storage_status.automatic`, `manual_batch_bytes` | Automatikus profilválasztás és kézi kötegméret |
| `vpn_status` | `state`, ha elérhető: `provider`, `protocol`, `kill_switch`, `changed_at`. Kezeletlen VPN: `unmanaged`, ismeretlen állapot: `unknown` |
| `download_speed`, `upload_speed` | Torrent-adatforgalom, bájt/másodperc |
| `torrent_count`, `active_torrent_count`, `paused_torrent_count` | Torrentdarabszámok |
| `current_stats`, `cumulative_stats` | A `session_stats` munkamenet- és összesített számlálói |
| `transfer_history` | Csak `include_history:true`: `timezone:UTC`, `started_at`, `now`, `days`, `hours` |

A könyvtárak a torrentek **aktuális adatkönyvtárai**, beleértve az ideiglenes könyvtárat. Az üres, nem használt könyvtárak nem szerepelnek. A profil és lemezterhelés a motor ténylegesen használt, gyorsítótárazott méréséből származik. Hiányzó mérés nem jelent normál terhelést.

A történeti sorok formája `[időszak_kezdete_UTC, letöltött_bájt, feltöltött_bájt]`; legfeljebb 62 napi és 48 órás elem. Napi/heti/havi összeghez az adott UTC naptári nap, hét (hétfőtől) vagy hónap sorait kell összegezni. A korábbi, nem rögzített forgalom nem rekonstruálható. Az eddigi `session_stats` és `include_history` működése változatlan.

## Telepítés és konfigurálás

A kiadási csomag gyökere legyen `/opt/transmission`. Python 3.10+ és venv szükséges:

```sh
python3 -m venv /opt/transmission/mqtt/venv
/opt/transmission/mqtt/venv/bin/pip install -r /opt/transmission/mqtt/requirements.txt
sudo install -d -m 0755 /etc/transmission-mqtt
sudo install -m 0644 /opt/transmission/mqtt/config.example.json /etc/transmission-mqtt/config.json
```

Állítsd be a broker címét, portját, egyedi `client_id` értékét és a topicot. A példabeli host nem valódi célpont. A jelszót külön, csak a futtató felhasználó által olvasható fájlban tárold (0600); a `password_file` erre mutasson. A JSON nem tartalmaz jelszót. RPC-hitelesítéshez az `rpc` objektumba is megadható `username` és `password_file`. A fájlútvonalak környezeti változói feloldódnak.

Kézi indítás az exportot futtató felhasználóval:

```sh
/opt/transmission/mqtt/venv/bin/python /opt/transmission/mqtt/exporter.py --config /etc/transmission-mqtt/config.json
```

Systemd: a mellékelt service `DynamicUser` és `LoadCredential` használatával olvassa a root tulajdonú `/etc/transmission-mqtt/password` fájlt. Ehhez a JSON-ban az MQTT `password_file` értéke **`${CREDENTIALS_DIRECTORY}/mqtt-password`** legyen. Ha RPC-jelszó is kell, adj a service-hez `LoadCredential=rpc-password:/etc/transmission-mqtt/rpc-password` sort, a JSON-ba pedig `${CREDENTIALS_DIRECTORY}/rpc-password` útvonalat.

```sh
sudo install -m 0644 /opt/transmission/mqtt/transmission-mqtt.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now transmission-mqtt
```

TLS alapból bekapcsolva, tanúsítvány- és hostnévellenőrzéssel. Opcionális `ca_file`, klienshitelesítéshez `cert_file` és `key_file` adható meg. Saját CA nélkül a rendszer CA-tára használatos. A `tls:false` beállítás titkosítatlan kapcsolatot jelent; csak tudatosan használd. Az RPC HTTPS-t is támogat, rendszer CA-ellenőrzéssel; alapból loopback HTTP. Az RPC-kliens nem követ átirányítást és nem használ környezeti proxyt.

## MQTT-szerződés és erőforrások

- `<topic_prefix>/state`: a teljes API `result` JSON, QoS 1, retained.
- `<topic_prefix>/availability`: `online` vagy `offline`, QoS 1, retained; Last Will is `offline`. Az online csak sikeres friss API-olvasás és állapotpublikálás után jelenik meg.
- A fogyasztó **mindig** vegye figyelembe az availability állapotot és a `sampled_at` kort. A retained állapot történeti adat marad, leállításkor nem törlődik. Hálózati szakadás észlelése a 60 másodperces keepalive miatt nem azonnali; célszerű a heartbeat + mintavételi idő + 90 másodperc feletti adatot elavultnak tekinteni.
- `interval_seconds`: alapból 30, 10–3600 között. `heartbeat_seconds`: alapból 300, legalább a mintavételi idő és legfeljebb 86400.
- `include_history`: alapból false; a példában true, hogy a napi/heti/havi statisztikához szükséges sorok is kijussanak.
- Egy tartós brokerkapcsolat; legfeljebb 2 sorban álló és 1 nyugtázatlan MQTT-üzenet. Nincs lemezre írt spool és nincs végtelen visszapótlás; ez állapotmonitorozás, nem eseményarchívum.
- Változatlan adatoknál kimarad a publikálás a következő heartbeatig. Az időbélyeg és aktív másodpercek önmagukban nem okoznak küldést. A tényleges sebesség- vagy PSI-változás igen.
- Brokerkieséskor nincs RPC-polling; MQTT újracsatlakozás 5–300 másodperces növekvő várakozással. Az RPC és publikálási nyugta időkorlátja 5 másodperc. Az API-válasz legfeljebb 1 MiB; nagyobb választ az export hibásnak jelzi.
- Nincsenek MQTT-parancsok vagy távoli vezérlés. A JSON könyvtárútvonalakat is tartalmaz; a broker topicjának olvasását a kívánt fogyasztókra korlátozd.

A kliens a [Paho MQTT 2.1 API-ját](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html) használja. A Python-függőséget a telepítés tölti le; nincs beépítve a daemonba.
