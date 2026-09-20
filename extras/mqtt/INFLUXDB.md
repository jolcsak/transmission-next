# Opcionális folyamatos InfluxDB-export (v11)

Az `exporter.py` közös API-mintából szolgálja ki az MQTT-t és az InfluxDB-t. Mindkettő külön is használható. InfluxDB nélkül a korábbi MQTT-konfiguráció változatlanul működik. InfluxDB-only módban Python 3.10+ elegendő, nincs külső Python-függőség és nincs MQTT-kapcsolat.

## Bekapcsolás

A csomag `mqtt/config.influx.example.json` fájlját másold saját konfigurációként, majd add meg a saját `url`, `org`, `bucket`, `instance` és `token_file` értékeket. Az export csak `influxdb.enabled:true` esetén aktív; a szervert, bucketet és a buckethez írási jogú tokent előzetesen hozd létre. A token külön, korlátozottan olvasható fájlban legyen, ne a JSON-ban.

```sh
python3 /opt/transmission/mqtt/exporter.py --config /etc/transmission-mqtt/config.json
```

MQTT mellett az eddigi konfigurációhoz add hozzá az `influxdb` objektumot; a `mqtt` beállításokat tartsd meg. A két cél egy folyamatból, egy RPC-mintából kapja az adatokat. Egyik sem indítja vagy kezeli az adatbázis-szervert.

A mellékelt systemd service használható: add hozzá a `LoadCredential=influx-token:/etc/transmission-mqtt/influx-token` sort, és a JSON `token_file` értékét állítsd `${CREDENTIALS_DIRECTORY}/influx-token` értékre. InfluxDB-only módban töröld a service MQTT-jelszóhoz tartozó `LoadCredential=mqtt-password:...` sorát, és ha nincs venv, az ExecStart Python útvonalát állítsd `/usr/bin/python3`-ra. A többi korlátozás megtartható.

HTTPS tanúsítvány- és hostnévellenőrzéssel működik; saját CA-hoz `ca_file` adható meg. HTTP is választható helyi, megbízható hálózaton. Nincs átirányításkövetés vagy környezeti proxyhasználat, a token nem kerül naplóba.

## Adatok és erőforráskorlátok

- `interval_seconds`: közös mintavétel, alapból 30 másodperc. InfluxDB minden sikeres mintát eltárol, változatlan értékeket is; az MQTT továbbra is kihagyja a változatlan állapotot.
- `flush_seconds`: alapból 60 másodperc (10–3600). Egy tartós HTTP/HTTPS-kapcsolat és egy várakozó író szál, kötegenként egy POST. Az adatbázis válasza nem blokkolja a mintavételező szálat.
- `buffer_bytes`: alapból 262144, engedélyezett 4096–4194304. A várakozó payload bájtkorlátja; küldés közben legfeljebb még egy ekkora köteg lehet folyamatban, továbbá a Python-objektumok és a soronkénti kódolás kis többlete. Nem a teljes folyamat RAM-korlátja.
- Nincs lemezes spool. Túlcsorduláskor régi minták elveszhetnek; kilövéskor a memória-puffer elveszik. Normál leálláskor egy utolsó küldési próbálkozás történik.
- Hálózati/HTTP-hibánál legfeljebb 300 másodpercig ritkuló újrapróbálkozás (hosszabb flush-idő esetén az a felső korlát). A puffer közben is korlátos. HTTP-időkorlát 5 másodperc. Hibás token vagy hiányzó bucket esetén is ritkított újrapróbálkozás történik; javítsd a konfigurációt és indítsd újra az exportert.
- `include_directories`: alapból false a kisebb CPU-, tárolási és idősorszám miatt; true esetén könyvtárankénti profil, terhelés és torrentszám is bekerül.
- A minták eredeti `sampled_at` ideje kerül az adatbázisba, másodperces pontossággal. Újraküldéskor ugyanaz az időbélyeg marad. RPC-hibakor nem keletkezik hamis nullás vagy korábbi adatból másolt új minta.

`transmission` measurement, `instance` tag (gépenként egyedi, stabil érték): sebességek bájt/s-ban, torrentdarabszámok, kumulatív letöltési/feltöltési bájtszámlálók, cache-méretek bájtban, PSI várakozási százalékok, `system_state`, VPN-állapot/protokoll/provider/kill-switch. A hiányzó PSI-mérések kimaradnak, nem nullák.

Opcionális `transmission_directory` measurement: `instance`, `directory` (útvonal SHA-256 azonosítójának első 128 bitje), `profile_id` tagek; `path`, `profile`, `load`, `torrents`, `active` mezők. A teljes útvonal mezőként szerepel. Torrentnevet/hash-t és időbélyeget nem használunk tagként. Törölt könyvtárak korábbi idősorai a bucket retention szabálya szerint maradnak meg.

A korábbi napi/órás előzményeket nem tölti fel újra: az InfluxDB élő idősorokat kap, a napi/heti/havi forgalom a számlálók változásából számolható. Daemon-újraindítás utáni számlálócsökkenést a lekérdezésben resetként kell kezelni.

Protokoll: [InfluxDB v2 `/api/v2/write`](https://docs.influxdata.com/influxdb/v2/write-data/developer-tools/api/), `precision=ns`, line protocol, `Authorization: Token`. InfluxDB 1.x nem támogatott közvetlenül. Az InfluxDB 3 v2-kompatibilis végpontja külön szerverbeállítást igényelhet; ezzel a kiadással nem teszteltük.
