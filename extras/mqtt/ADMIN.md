# Webes telemetria-adminisztráció – v12

Az Áttekintés oldal alján az **Adminisztráció · MQTT és InfluxDB beállítások** panelen megadhatók és módosíthatók az API-, MQTT- és InfluxDB-beállítások, beleértve a mintavételt, kötegelést, pufferméretet, TLS-t, jelszavakat és tokent.

**Tárolás: a daemon tényleges config könyvtárában, `telemetry.json` néven.** Nincs böngészős localStorage-ban tárolt konfiguráció. A webpanel a pontos szerveroldali útvonalat is mutatja.

## Egyszeri indítás

A panelhez az új daemon, az új webfelület és a hoston futó `mqtt/admin.py` felügyelő együtt szükséges. A felügyelő a daemon felhasználójával és ugyanazzal a config könyvtárral fusson. Ő kezeli az exportert is; a régi önálló exportert állítsd le, hogy ne legyen kettős export.

VPN nélküli csomagindításnál a config rendszerint a csomag `config` könyvtára:

```sh
./run-telemetry-admin.sh --config-dir /opt/transmission/config
```

A VPN-csomag alapértelmezett config könyvtára `/var/lib/transmission-vpn/config`. Ellenőrizd a saját `vpn.json` `config_dir` és `run_user` beállítását. Az adminfolyamat **a hoston**, a VPN-névtéren kívül fusson, és az API a hostoldali RPC-relay címét használja.

Systemd példa: `mqtt/transmission-telemetry-admin.service`. Módosítsd a `User`, `Group`, `ExecStart --config-dir` és `ReadWritePaths` értékeket a tényleges telepítéshez, majd:

```sh
sudo systemctl disable --now transmission-mqtt  # ha a korábbi önálló exporter szolgáltatást használod
sudo install -m 0644 /opt/transmission/mqtt/transmission-telemetry-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now transmission-telemetry-admin
```

A példa a `/opt/transmission/mqtt/venv/bin/python` környezetet használja, amelyben MQTT-hez `paho-mqtt==2.1.0` szükséges (telepítés a korábbi README szerint). Csak InfluxDB-hez a rendszer `python3` is elegendő. A daemon config könyvtára már létezzen és legyen írható a daemon/felügyelő felhasználójának.

## Használat

- **Újratöltés:** a szerveren mentett JSON-t olvassa vissza.
- **Mentés és alkalmazás:** szerveroldali validálás után atomi fájlcserével írja a `telemetry.json`-t, és újraindítja az exportert az új értékekkel. Hibás beállítás nem írja felül a korábbi konfigurációt; sikertelen alkalmazásnál visszaállítja a korábbi JSON-t.
- **Kapcsolatok tesztelése:** a szerkesztőben levő, akár még nem mentett beállításokkal próbálkozik. Az API-t valóban lekérdezi; MQTT-n `<topic_prefix>/test` üzenetet küld QoS 1, retain=false beállítással, külön kliensazonosítóval; InfluxDB-be `transmission_connection_test` tesztpontot ír. Az éles konfigurációt nem módosítja. A letiltott exportcélokat kihagyja.
- **JSON exportálása:** a szerkesztő aktuális beállításait tölti le `telemetry.json` fájlként, a jelszavakkal és tokenekkel együtt.
- **JSON importálása:** legfeljebb 60 kB-os JSON-objektumot tölt a szerkesztőbe. Csak a Mentés és alkalmazás gomb után kerül szerverre és lép életbe. A külső hitelesítő-/CA-fájlok tartalmát nem importálja/exportálja, csak az útvonalukat.

Az inline jelszó/token elsőbbséget élvez az opcionális `password_file`/`token_file` hivatkozással szemben. A JSON és az ideiglenes kérésfájl jogosultsága Linuxon `0600`; a könyvtár hozzáférését is a daemon felhasználójára korlátozd. Az exportált fájl titkokat tartalmazhat. A webfelület és az RPC minden hozzáférője a meglévő Transmission RPC jogosultságait használja; külön szerepkör-alapú adminjogosultság nem került be. Távoli adminisztrációhoz a meglévő RPC-hitelesítést és HTTPS elérést használd.

## Működés és API

A daemon csak kis, legfeljebb 64 KiB-os JSON-dokumentumokat olvas/ír; nem végez blokkoló külső kapcsolattesztet. A felügyelő 2 másodpercenként ellenőrzi a config könyvtár kérésfájlját. A mentés/teszt idejére a felület letiltja a műveleti gombokat; 60 másodperces válaszhiánynál hibát jelez, nem állítja, hogy sikerült. A még függő kérés a felügyelő későbbi elindításakor feldolgozódhat.

Fájlok ugyanabban a config könyvtárban:

- `telemetry.json`: tartós beállítások.
- `telemetry-request.json`: legfeljebb egy függő adminművelet; feldolgozás után törlődik.
- `telemetry-result.json`: utolsó művelet eredménye, titkok nélkül.

JSON-RPC 2.0 metódusok az eddigi hitelesített `/transmission/rpc` végponton:

```json
{"id":1,"jsonrpc":"2.0","method":"telemetry_config_get","params":{}}
```

Eredmény: `configuration` objektum, `file` útvonal, `job` eredmény. Mentés/teszt indítása:

```json
{"id":2,"jsonrpc":"2.0","method":"telemetry_config_apply","params":{"action":"save","job_id":"egyedi-azonosito","configuration":"{\"mqtt\":{\"enabled\":false},\"influxdb\":{\"enabled\":false}}"}}
```

`action` = `save` vagy `test`. Az eredményt `telemetry_config_get` segítségével, egyező `job.job_id` alapján kell megvárni. Az alkalmazás visszaigazolásakor még nem biztos a külső szerver elérhetősége; ezt a külön kapcsolatteszt mutatja. Kézzel szerkesztett `telemetry.json` betöltéséhez indítsd újra a felügyelőt.

Ellenőrzések: böngészős mentés, módosítás, export/import, újratöltés, hibás beállítás elutasítása és mobil szélesség; valódi daemon RPC és helyi MQTT-broker; InfluxDB HTTP-tesztszerver. Éles külső broker/adatbázis nem kapott tesztadatot.
