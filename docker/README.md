# Transmission Next — linux/amd64

A jelenlegi módosított Transmission teljes daemon/web/VPN/telemetria funkcióival.
Image: `jolcsak/transmission-next:amd64`. HTTPS: `https://localhost:9091/transmission/web/`.

## Indítás

1. Másold a compose.yaml fájlt egy új könyvtárba.
2. Hozd létre a `config`, `downloads`, `secrets` könyvtárakat.
3. PureVPN OpenVPN-profil: `config/provider.ovpn`. A profil által hivatkozott
   tanúsítványfájlokat is tedd mellé, vagy használj inline CA-t tartalmazó profilt.
4. `secrets/rpc_password.txt`: saját RPC-jelszó, legalább 8 karakter.
   `secrets/vpn_username.txt`, `secrets/vpn_password.txt`: PureVPN OpenVPN-hitelesítés.
   Ezeket ne tedd Gitbe és ne építsd bele image-be; Linuxon jogosultságuk legyen 600.
5. `docker compose up -d`.
6. Saját TLS-tanúsítvány esetén indulás előtt helyezd el a `config/tls/server.crt`
   és `config/tls/server.key` fájlokat. Egyébként az első indítás önaláírt,
   gépenként új tanúsítványt készít localhost/transmission névre. LAN-használathoz
   telepíts a kiszolgáló nevére kiadott tanúsítványt, és bízz meg a kibocsátóban.
   A kliensben SSL/HTTPS és RPC-hitelesítés szükséges.

A supervisor rootként kezeli a hálózati névtereket, a Transmission és az admin/
MQTT/Influx folyamat UID/GID 1000-ként, eldobott capabilitykkel fut. A config és
letöltési gyökérkönyvtár tulajdonosát induláskor ehhez igazítjuk, rekurzív chown
nincs. Meglévő letöltések jogosultságait előzetesen ehhez kell beállítani.

A névterekhez NET_ADMIN, SYS_ADMIN, TUN eszköz és AppArmor mount-engedély kell;
a példa AppArmor unconfined beállítást használ. Nincs privileged mód, host network,
host PID vagy Docker socket mount. SYS_ADMIN ettől még széles jogosultság,
csak megbízható image-et futtass ilyen beállítással.

## Konfiguráció és hálózat

- `/config`: settings.json, vpn.json, telemetry.json, torrentállapotok, TLS.
- `/downloads`: tartós letöltések. További lemezek külön volume-ként csatolhatók.
- A saját futó szerver konfigurációja, jelszavai, torrentjei és naplói nincsenek az image-ben.
- A daemon útvonala/felhasználója/config és belső RPC-portja konténerfüggő, automatikus.
- VPN alapértelmezés szerint kötelező. Hibánál a konténer leáll, nincs közvetlen fallback.
- Kizárólag szándékos, tiszta VPN nélküli telepítéshez `VPN_ENABLED=false`.
  Meglévő vpn.json vagy VPN env mellett ez hibát ad. Ilyenkor nem szükségesek
  a VPN capabilityk/eszközök, de a peer-portot szükség esetén külön publikálni kell.
- A 9091 port HTTPS admin/RPC. A 19091 belső backend portot ne publikáld.
- VPN-módban a torrent, tracker, DHT, DNS és a telemetriaexport ugyanabban a védett
  hálózati névtérben fut. A VPN felépítéséhez a VPN-kiszolgáló kezdeti DNS-feloldása
  a konténer alap hálózatán történik. Az admin/RPC a publikált porton érhető el.
- MQTT/Influx opcionális, a Beállítások oldalon vagy meglévő környezeti változókkal
  állítható. VPN-módban csak az alagúton elérhető szerverek használhatók;
  nincs automatikus LAN-kivétel, amely megkerülné a kill switchet.
- Az RPC exporthitelesítést a telemetry.json rpc részében kell megadni,
  URL: `http://127.0.0.1:19091/transmission/rpc`.
- RPC_USERNAME, RPC_PASSWORD vagy RPC_PASSWORD_FILE az induláskor érvényesül.
  Ha a felületen módosított jelszót szeretnéd megtartani, a régi indítási felülírást
  vedd ki a compose-ból a következő indítás előtt.
- A megszokott TRANSMISSION_VPN_* env változók megmaradtak; új opció a
  TRANSMISSION_VPN_USERNAME_FILE és TRANSMISSION_VPN_PASSWORD_FILE.
- A VPN profil/secret változtatása után `docker compose restart transmission`.
- A daemon indulásához működő VPN-profil kell; hibás első konfigurációt fájlban/env-ben
  javíts, mert a web/RPC daemon sem indul el védelem nélkül.

## Optimalizálás

Natív amd64, GCC Release -O3, linkeléskori optimalizálás (LTO), stripped binárisok,
x86-64 alaputasításkészlet és generic CPU-hangolás. Nincs `-march=native`, így az
image nem függ a buildgép CPU-jától. OpenSSL saját futásidejű CPU-detektálása
használja az elérhető AES-gyorsítást. A VPN-felügyelő nem kérdezget külső IP-szolgáltatást; a Transmission saját IP-felderítése megmarad.
A build és Node eszközök külön stage-ben maradnak. A healthcheck nem küld hamis
bejelentkezési kísérleteket. Runtime naplók /run tmpfs-ben, Docker logok rotálva.
HDD/SSD automatikus profilok megmaradnak, de Docker Desktop virtuális lemezeknél
nem feltétlenül látszik a fizikai adathordozó típusa; a mért terhelés is számít.

## Build és forrás

A forrásgyökérben:

```sh
docker buildx build --platform linux/amd64 --build-arg BUILD_JOBS=2 \
  --load -t jolcsak/transmission-next:amd64 .
```

Az image-ben `/usr/share/transmission/source/transmission-source.tar.gz` tartalmazza
az ehhez a buildhez használt módosított forrást és a buildreceptet.
Kinyerés: `docker create --name tr-source jolcsak/transmission-next:amd64`,
`docker cp tr-source:/usr/share/transmission/source/transmission-source.tar.gz .`,
`docker rm -v tr-source`. Licencfájlok: `/usr/share/doc/transmission/`.

A Docker minták alapjai: https://docs.docker.com/engine/containers/run/
és https://docs.docker.com/build/building/multi-stage/.
