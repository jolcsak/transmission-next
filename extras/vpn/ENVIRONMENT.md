# VPN környezeti változók

Elsőbbség: **env → JSON → alapértékek**. Az indítás, a helyi profilteszt és a valódi kapcsolatteszt ugyanazt a felülírást használja. A webes szerkesztő, mentés és export a JSON-t kezeli; az env-ből érkező értékek nem kerülnek vissza a `vpn.json` fájlba. Az OpenVPN működéséhez szükséges privát profil/hitelesítőfájl indításkor továbbra is létrejöhet.

| Környezeti változó | JSON-mező |
| --- | --- |
| `TRANSMISSION_VPN_PROVIDER` | `provider` (purevpn) |
| `TRANSMISSION_VPN_OPENVPN_CONFIG` | `openvpn_config` (abszolút .ovpn útvonal) |
| `TRANSMISSION_VPN_OPENVPN_PROFILE` | `openvpn_profile` (teljes, többsoros profil) |
| `TRANSMISSION_VPN_CREDENTIALS_FILE` | `credentials_file` (privát, kétsoros fájl) |
| `TRANSMISSION_VPN_USERNAME` | `username` |
| `TRANSMISSION_VPN_PASSWORD` | `password` |
| `TRANSMISSION_VPN_DNS` | `dns` (1–3 IPv4-cím, vesszővel elválasztva) |
| `TRANSMISSION_VPN_RPC_PORT` | `rpc_port` (egész szám) |
| `TRANSMISSION_VPN_START_TIMEOUT` | `start_timeout` (5–600 másodperc; kapcsolatteszt maximum 60) |
| `TRANSMISSION_VPN_SUBNET` | `subnet` (privát /30 hálózat) |
| `TRANSMISSION_VPN_RUN_USER` | `run_user` (nem root Linux-felhasználó) |
| `TRANSMISSION_VPN_DAEMON` | `daemon` (abszolút útvonal) |
| `TRANSMISSION_VPN_CONFIG_DIR` | `config_dir` |
| `TRANSMISSION_VPN_DOWNLOAD_DIR` | `download_dir` |

Példa a meglevő JSON kiegészítésére, shellben:

```sh
export TRANSMISSION_VPN_PROVIDER=purevpn
export TRANSMISSION_VPN_OPENVPN_CONFIG=/config/purevpn.ovpn
export TRANSMISSION_VPN_CREDENTIALS_FILE=/config/vpn-credentials.txt
export TRANSMISSION_VPN_DNS=1.1.1.1,1.0.0.1
export TRANSMISSION_VPN_START_TIMEOUT=45
```

A profilnál vagy az inline tartalmat, vagy az útvonalat add meg env-ben. A hitelesítésnél vagy a hitelesítőfájlt, vagy a felhasználó/jelszó változókat használd. Az ütköző env-források hibát adnak. Az env-fájlútvonal a JSON inline változatát is lecseréli; az env inline adat pedig a JSON fájlos változatát. Egyetlen jelszó env-felülírása megtarthatja a JSON felhasználónevét.

A már létező `TRANSMISSION_VPN_CONFIG` a csomag indítójában a **vpn.json fájl helyét** választja ki; nem az OpenVPN-profilt. A `TRANSMISSION_CONFIG_DIR` a csomag/admin config könyvtárát választja. Webes tesztelésnél az effektív VPN `config_dir`-nek ehhez a daemonhoz kell tartoznia.

Az **admin- és VPN-felügyelőnek azonos env-et adj**, majd env-változtatáskor indítsd újra őket. A szolgáltatásmintában a VPN-felügyelő `/etc/transmission-vpn/vpn.env` fájlból is olvas; ugyanezt az `EnvironmentFile` beállítást add az admin systemd egységéhez. A fájlt csak a szükséges felhasználók olvashassák. A root kapcsolatteszt-segéd nem igényel env-beállítást: a nem-root admin az effektív, ellenőrzött profilból készíti a tesztkérést.

A webes panel az aktív env-változók neveit jelzi, értékek nélkül. Az admin futása alatt az env pillanatképe változatlan; nincs periodikus újraolvasás vagy többlet hálózati forgalom.
