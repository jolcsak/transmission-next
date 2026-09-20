# Transmission Next — linux/amd64

Image: `jolcsak/transmission-next:amd64`. Web/RPC: `https://localhost:9091/transmission/web/`.
A `https://localhost:9091/` gyökércím automatikusan erre az útvonalra irányít át,
így DSM reverse proxy vagy Web Station site mögül a domain gyökere is használható.
DSM HTTPS site esetén a backend protokoll legyen HTTP, a konténerport pedig `9092`.
A `9092` portot csak localhostra vagy a megbízható reverse-proxy hálózatra publikáld;
a kliensek közvetlen HTTPS elérésére továbbra is a `9091` való.

## Indulás kézi beállítások nélkül

A `compose.yaml` semmilyen kötelező környezeti változót vagy secret-fájlt nem kér.
Másold egy saját könyvtárba, majd futtasd: `docker compose up -d`.
A `config` és `downloads` könyvtár tartós adatokat tárol; frissítéskor tartsd meg őket.

- VPN-profil nélkül közvetlen hálózaton indul. MQTT/Influx export alapból kikapcsolva.
- RPC-felhasználó: `transmission`. Ha még nincs jelszó, egyedi, erős jelszó készül
  a `config/rpc-initial-password.txt` fájlba (konténerben `/config/rpc-initial-password.txt`, 600 jogosultság).
  A jelszó nem kerül naplóba. Első belépés után a Beállítások / RPC hitelesítés résznél módosítható.
  A fájl az eredeti kezdeti jelszót tartalmazza; a felületi módosítás nem írja át.
- Meglévő RPC-jelszó/hash megmarad. A generált jelszó újraindításkor sem változik.
- Saját tanúsítvány nélkül gépenként új önaláírt HTTPS-tanúsítvány készül.
- A weben állíthatók a letöltési beállítások, a VPN-profil, MQTT és Influx.
- A hiányzó beállítások alapértéket kapnak. A megadott, de hibás értékek javítandó
  konfigurációs hibát jelentenek, nem kapcsoljuk ki helyettük a védelmet.

## Opcionális VPN

`VPN_ENABLED` alapértéke `auto` (elhagyható):

- Profil/hitelesítés nélküli vagy régi, csak alapértékeket tartalmazó vpn.json: VPN nélkül indul.
- VPN-profil vagy VPN-hitelesítés megadása: VPN szükséges; csak sikeres kapcsolat után indul a daemon.
- Részleges, hibás, hiányzó fájlra mutató VPN-konfiguráció: nincs közvetlen hálózati fallback.
- `VPN_ENABLED=true`: kifejezetten megköveteli a VPN-t.
- `VPN_ENABLED=false`: kifejezett, szándékos kikapcsolás; közvetlen hálózati üzem.

VPN-es telepítéshez külön példa: **compose.vpn.yaml**. A kezelt VPN-hez szükséges:
NET_ADMIN és SYS_ADMIN capability, `/dev/net/tun`, IPv4 forwarding, és a hálózati
névterekhez szükséges mount-engedély (a példa AppArmor unconfined beállítást használ).
Nincs privileged mód, host network, host PID vagy Docker socket mount.
A supervisor rootként kezeli a hálózatot; a daemon és a telemetria UID/GID 1000-ként,
eldobott capabilitykkel fut. SYS_ADMIN széles jogosultság, megbízható image-hez használd.

PureVPN-profil: például `config/provider.ovpn`. A külső CA-fájlok legyenek mellette,
vagy használj inline CA-t. Profil/hitelesítés megadható a felületen, vpn.json-ban,
vagy a TRANSMISSION_VPN_* env változókkal. A _USERNAME_FILE és _PASSWORD_FILE is támogatott.
A webes kapcsolatteszt VPN nélküli induláskor is elérhető, ha a konténer már megkapta
az ehhez szükséges eszközt és capabilityket. Egyébként ezeket előbb hozzá kell adni.
A Docker image-ben nincs külön `transmission-vpn-test` konténer vagy systemd szolgáltatás:
az entrypoint automatikusan elindítja a tétlenül várakozó tesztsegédet, ha a `/dev/net/tun`,
`NET_ADMIN`, `SYS_ADMIN`, IPv4 forwarding és DSM-en az AppArmor unconfined beállítás elérhető.
A VPN-beállítás mentése önmagában nem kapcsolja át a futó forgalmat; újraindítás kell.

VPN-módban torrent, tracker, DHT, DNS és telemetriaexport a védett névtérben fut.
Kivétel a VPN-kiszolgáló kezdeti DNS-feloldása és a publikált admin/RPC elérés.
MQTT/Influx esetén alagúton elérhető célpont szükséges; nincs automatikus LAN-kivétel.

## Könyvtárak, portok és felülírások

- `/config`: settings.json, vpn.json, telemetry.json, torrentállapotok és TLS.
- `/downloads`: letöltések; további lemezek külön volume-ként csatolhatók.
- A config/downloads gyökér tulajdonosa induláskor UID/GID 1000 lesz; rekurzív chown nincs.
- 9091: közvetlen HTTPS admin/RPC. 9092: HTTP backend DSM TLS-lezáráshoz, csak
  localhostra vagy megbízható proxyhálózatra publikáld. 19091: belső daemon backend,
  ne publikáld.
- A közvetlen mód peer-portja alapból 51413 TCP/UDP; a minimális Compose publikálja.
- VPN esetén ne publikálj közvetlen peer-portot; a szolgáltató oldali elérhetőség számít.
- Saját TLS: `/config/tls/server.crt` és `/config/tls/server.key` együtt.
  A generált tanúsítvány localhost/transmission névre szól; LAN-névhez saját tanúsítvány kell.
- RPC_USERNAME, RPC_PASSWORD vagy RPC_PASSWORD_FILE: opcionális indulási felülírások.
  Ha a felületen módosítasz jelszót, a régi env felülírást vedd ki a következő indítás előtt.
- DOWNLOAD_DIR: opcionális; egyébként a meglévő settings.json értéke, majd `/downloads` érvényes.
- Env-módosítás után újralétrehozás kell: `docker compose up -d`; sima restart nem veszi át az új env-t.
- A konténer saját daemonútvonala/felhasználója/config és belső RPC-portja automatikus.
- Telemetria API URL konténeren belül: `http://127.0.0.1:19091/transmission/rpc`;
  az RPC-exporthoz szükséges hitelesítést a telemetriabeállításokban kell megadni.

## Optimalizálás és build

Natív amd64, Release -O3, LTO és stripped binárisok, x86-64 alaputasításkészlet,
generic CPU-hangolás; nincs buildgéphez kötő march=native. OpenSSL saját CPU-detektálása
használja az elérhető AES-gyorsítást. A VPN-felügyelő nem kérdezget külső IP-szolgáltatást;
a Transmission saját IP-felderítése megmarad. Build/Node eszközök nem kerülnek a runtime-ba.
A healthcheck nem készít login-eseményeket. Runtime logok /run tmpfs-ben, Docker logok rotálva.
A HDD/SSD profilok megmaradnak; virtuális lemeznél a fizikai típus nem mindig látszik.
Az alap belső írási cache 16 MiB, az új profilok 64 globális és torrentenként 24 peerrel,
másodpercenként legfeljebb 3 új kapcsolódással indulnak. A Compose-minták 512 MiB
memórialimitet és 192 MiB soft reservation értéket használnak. A DSM teljes
konténermemóriája a folyamatok RSS-e mellett a visszanyerhető Linux fájlcache-t is
tartalmazza; terhelés alatt a limit ezt automatikusan visszaszorítja.

```sh
docker buildx build --platform linux/amd64 --build-arg BUILD_JOBS=2 \
  --load -t jolcsak/transmission-next:amd64 .
```

A pontos forrás és buildrecept: `/usr/share/transmission/source/transmission-source.tar.gz`.
Licencfájlok: `/usr/share/doc/transmission/`. Személyes config/torrent/VPN-adatok nincsenek az image-ben.

## Hibák

A napló titokmentes hibakódot és javítási útmutatót ad. Például:
`rpc_password_invalid`, `conflicting_secret_sources`, `secret_file_unreadable`,
`invalid_json`, `vpn_enabled_invalid`, `vpn_profile_missing`, `tls_pair_missing`.
A hibásan konfigurált VPN nem indít védelem nélküli torrentforgalmat.
