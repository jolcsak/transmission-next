# Környezeti változók – v13

Prioritás: **explicit env változó → config/telemetry.json → alapérték**. Ugyanez érvényes a webes Mentés és alkalmazás műveletre és a kapcsolattesztre. Az adminfelület a mentett JSON-t szerkeszti, és kiírja az aktív env-felülírások nevét. Az env értékeit – különösen a jelszavakat/tokeneket – nem írjuk a JSON-ba, a műveleti eredménybe vagy a JSON-exportba.

A változókat a telemetria-felügyelő/exporter folyamatnak kell átadni; a daemon saját környezete önmagában nem elég. Változtatás után indítsd újra a felügyelőt. Nincs mintánkénti env-feldolgozás; csak indításkor és adminműveletkor történik.

## InfluxDB példa, JSON nélkül is

```sh
export TRANSMISSION_CONFIG_DIR=/var/lib/transmission-vpn/config
export TRANSMISSION_MQTT_ENABLED=false
export TRANSMISSION_INFLUXDB_ENABLED=true
export TRANSMISSION_INFLUXDB_URL=https://influx.example.com:8086
export TRANSMISSION_INFLUXDB_ORG=home
export TRANSMISSION_INFLUXDB_BUCKET=transmission
export TRANSMISSION_INFLUXDB_TOKEN_FILE=/etc/transmission/influx-token
export TRANSMISSION_TELEMETRY_INTERVAL_SECONDS=30
export TRANSMISSION_INFLUXDB_FLUSH_SECONDS=60
./run-telemetry-admin.sh
```

A `TRANSMISSION_INFLUXDB_TOKEN` közvetlenül is megadható; ha tokent és tokenfájlt is beállítasz envben, konfigurációs hibát kapsz. Ugyanez érvényes a jelszó/jelszófájl párokra. Az envből érkező `*_FILE` felülírja a JSON inline titkát is, és fordítva. Az explicit üres env érték is felülírás, nem esik vissza egy korábban mentett titokra. Ha ez hiányos hitelesítést eredményez, validálási hiba lehet.

## Változók

| Változó | JSON mező |
| --- | --- |
| `TRANSMISSION_TELEMETRY_INTERVAL_SECONDS` | `interval_seconds` |
| `TRANSMISSION_TELEMETRY_HEARTBEAT_SECONDS` | `heartbeat_seconds` |
| `TRANSMISSION_TELEMETRY_INCLUDE_HISTORY` | `include_history` |
| `TRANSMISSION_TELEMETRY_RPC_URL` | `rpc.url` |
| `TRANSMISSION_TELEMETRY_RPC_USERNAME` | `rpc.username` |
| `TRANSMISSION_TELEMETRY_RPC_PASSWORD` | `rpc.password` |
| `TRANSMISSION_TELEMETRY_RPC_PASSWORD_FILE` | `rpc.password_file` |
| `TRANSMISSION_MQTT_ENABLED` | `mqtt.enabled` |
| `TRANSMISSION_MQTT_HOST` | `mqtt.host` |
| `TRANSMISSION_MQTT_PORT` | `mqtt.port` |
| `TRANSMISSION_MQTT_TLS` | `mqtt.tls` |
| `TRANSMISSION_MQTT_USERNAME` | `mqtt.username` |
| `TRANSMISSION_MQTT_PASSWORD` | `mqtt.password` |
| `TRANSMISSION_MQTT_PASSWORD_FILE` | `mqtt.password_file` |
| `TRANSMISSION_MQTT_CLIENT_ID` | `mqtt.client_id` |
| `TRANSMISSION_MQTT_TOPIC_PREFIX` | `mqtt.topic_prefix` |
| `TRANSMISSION_MQTT_CA_FILE` | `mqtt.ca_file` |
| `TRANSMISSION_MQTT_CERT_FILE` | `mqtt.cert_file` |
| `TRANSMISSION_MQTT_KEY_FILE` | `mqtt.key_file` |
| `TRANSMISSION_INFLUXDB_ENABLED` | `influxdb.enabled` |
| `TRANSMISSION_INFLUXDB_URL` | `influxdb.url` |
| `TRANSMISSION_INFLUXDB_ORG` | `influxdb.org` |
| `TRANSMISSION_INFLUXDB_BUCKET` | `influxdb.bucket` |
| `TRANSMISSION_INFLUXDB_TOKEN` | `influxdb.token` |
| `TRANSMISSION_INFLUXDB_TOKEN_FILE` | `influxdb.token_file` |
| `TRANSMISSION_INFLUXDB_INSTANCE` | `influxdb.instance` |
| `TRANSMISSION_INFLUXDB_CA_FILE` | `influxdb.ca_file` |
| `TRANSMISSION_INFLUXDB_FLUSH_SECONDS` | `influxdb.flush_seconds` |
| `TRANSMISSION_INFLUXDB_BUFFER_BYTES` | `influxdb.buffer_bytes` |
| `TRANSMISSION_INFLUXDB_INCLUDE_DIRECTORIES` | `influxdb.include_directories` |

A kapcsolók értéke: `true/false`, `1/0`, `yes/no`, `on/off`, kis- vagy nagybetűvel. Az időközök, port és pufferméret egész számok; a meglévő érvényességi korlátok megmaradnak. Hibás értéknél nincs csendes visszaesés a JSON-ra.

## Indítási útvonalak és systemd

- `admin.py`: `--config-dir` elsőbbséget élvez a `TRANSMISSION_CONFIG_DIR` változóval szemben. A csomag `run-telemetry-admin.sh` indítója argumentum nélkül először ezt az env változót használja, ennek hiányában a csomag `config` könyvtárát.
- Önálló `exporter.py`: `--config` elsőbbséget élvez a `TRANSMISSION_TELEMETRY_CONFIG` változóval szemben. Ha egyik sincs megadva, csak az env és az alapértékek alapján indul. Kifejezetten megadott, de nem olvasható JSON esetén hibával leáll.
- A systemd-minták opcionálisan betöltik az `/etc/transmission-telemetry.env` fájlt. Formátum: `NÉV=érték`, **export nélkül**. A fájlt tartsd root tulajdonban, 0600 jogosultsággal. A service-ben megadott `--config-dir`/`--config` továbbra is felülírja az envben megadott fájlútvonalat; másik config könyvtárhoz az `ExecStart` és a `ReadWritePaths` értékét is igazítsd hozzá.

```ini
TRANSMISSION_MQTT_ENABLED=false
TRANSMISSION_INFLUXDB_ENABLED=true
TRANSMISSION_INFLUXDB_URL=https://influx.example.com:8086
TRANSMISSION_INFLUXDB_ORG=home
TRANSMISSION_INFLUXDB_BUCKET=transmission
TRANSMISSION_INFLUXDB_TOKEN_FILE=/etc/transmission/influx-token
```

Majd `sudo systemctl restart transmission-telemetry-admin`. A tokenfájlnak a service felhasználója számára olvashatónak kell lennie; systemd credentials használatával a már támogatott `${CREDENTIALS_DIRECTORY}/...` útvonal is megadható.

Ez a változás a telemetria (MQTT/InfluxDB és az ezekhez használt RPC-kliens) konfigurációjára vonatkozik, nem a Transmission összes torrent-/VPN-motorbeállításának új env-kezelésére.
