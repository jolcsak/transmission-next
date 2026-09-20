# Moduláris, kezelt VPN – első szolgáltató: PureVPN

Linuxra készült, külön indítható integráció a meglévő Transmission-csomaghoz.
A csomag indítja és felügyeli az OpenVPN-t, majd sikeres TLS-kapcsolat után
a Transmissiont. A korábbi CPU-, cache-, HDD-/SSD- és hálózati optimalizálások
változatlan daemonban maradnak. Nem a PureVPN asztali alkalmazását vezérli.

## Felépítés

- `providers/purevpn.py`: PureVPN kézi OpenVPN-profil beolvasása és normalizálása.
- `providers/__init__.py`: kódban rögzített szolgáltatóregiszter. További OpenVPN-
  szolgáltató saját adapterrel vehető fel; a JSON nem tölthet be futtatható plugint.
- `controller.py`: közös névtér, tűzfal, titokkezelés, felügyelet, RPC és leállítás.
  Az első átviteli háttér OpenVPN; WireGuard még nincs megvalósítva.
- `transmission-vpn.service`: választható systemd szolgáltatásminta.

## Beállítás

Szükséges: Linux hálózati névterek, TUN, nftables, Python 3.10+, iproute2,
OpenVPN 2.6+ és util-linux/setpriv. A felügyelő rootként indul;
a Transmission és az RPC-proxy külön, nem root felhasználóként fut,
képességek és jogosultságnövelés nélkül. A gép tűzfalának engednie kell
a `trvpn0` interfészről a kiválasztott VPN-szerver felé történő továbbítást.
Más tűzfalak szabályait a modul nem írja át.

Debian/Ubuntu előkészítés, `/opt/transmission` telepítési hellyel:

```sh
sudo apt-get install python3 openvpn nftables iproute2 util-linux
sudo useradd --system --user-group --home-dir /var/lib/transmission-vpn transmission-vpn
sudo install -d -m 0755 -o transmission-vpn -g transmission-vpn /var/lib/transmission-vpn
sudo install -d -m 0700 /etc/transmission-vpn/purevpn
sudo cp /opt/transmission/extras/vpn/example.json /etc/transmission-vpn/vpn.json
sudo chmod 600 /etc/transmission-vpn/vpn.json
sudo sysctl -w net.ipv4.ip_forward=1
```

Az IPv4-továbbítás gépszintű rendszergazdai beállítás; a felügyelő csak ellenőrzi,
nem kapcsolja át automatikusan és nem módosítja a gép alapértelmezett útvonalát.
Ha újraindítás után is szükséges, a rendszergazda saját sysctl-beállításában rögzítse.
Konténerben/WSL-ben a kernel és a jogosultságok is korlátozhatják a működést.

1. Töltsd le a PureVPN hivatalos, kívánt szerverhez tartozó OpenVPN-profilt.
   Tedd a `server.ovpn` fájlt és a hivatkozott CA/TLS-kulcsfájlokat az
   `/etc/transmission-vpn/purevpn/` könyvtárba. Az inline tanúsítványokat is kezeli.
2. Hozd létre a `credentials.txt` fájlt **két sorral**: VPN-felhasználónév,
   VPN-jelszó. A PureVPN tagi felületének kézi VPN-hitelesítő adatai kellenek,
   nem a weboldalas belépés. A fájl legyen `chmod 600`; jelszót ne adj meg
   parancssori argumentumban, forráskódban vagy chatben.
3. Szerkeszd a `vpn.json` elérési útjait. A megadott `run_user` nem lehet root.
   Meglévő letöltéseknél használhatod azok jelenlegi tulajdonosát; a modul nem
   végez rekurzív tulajdonosváltást. A megadott config- és letöltési könyvtárak
   ennek a felhasználónak legyenek írhatók. Állítsd le az azonos configot használó
   korábbi daemont, és mentsd a konfigurációját, mielőtt VPN-módban indítod.
4. A `dns` alapértéke Cloudflare (`1.1.1.1`, `1.0.0.1`), kizárólag az alagúton.
   Megadhatsz más elérhető IPv4 DNS-szervereket is. A gazdagép helyi DNS-címe
   és az underlay-hálózat nem használható. Az ütköző `/30` alhálózatot módosítsd.

```sh
sudo python3 /opt/transmission/extras/vpn/controller.py check --config /etc/transmission-vpn/vpn.json
sudo python3 /opt/transmission/extras/vpn/controller.py run --config /etc/transmission-vpn/vpn.json
```

A `check` a konfigurációt ellenőrzi, nem próbál bejelentkezni. A `run` a szükséges
rendszerfeltételeket is ellenőrzi. Az új VPN-csomag `run-daemon.sh` indítója alapból
ezt a VPN-útvonalat használja, és hiányzó/hibás VPN-beállításkor **nem vált át**
védelem nélküli indításra. `--config /etc/transmission-vpn/vpn.json` megadható;
enélkül a csomag gyökerében lévő `vpn.json` a minta. A korábbi indító külön,
egyértelműen `run-without-vpn.sh` néven érhető el.

Web/RPC: `http://127.0.0.1:9091/transmission/web/` (egyéni `rpc_port` esetén az).
Távoli eléréshez használj SSH-porttovábbítást; a modul nem nyit LAN/internetes RPC-t.
A meglévő RPC-hitelesítést megőrzi. Állapot:

```sh
sudo python3 /opt/transmission/extras/vpn/controller.py status
```

A leállítás Ctrl+C vagy systemd stop. A felügyelő előbb az RPC-proxyt és a
Transmissiont állítja le (cache-ürítésre vár), majd a VPN-t és a saját hálózatát.
A systemd minta telepíthető `/etc/systemd/system/transmission-vpn.service` alá;
előbb igazítsd az `ExecStart` útvonalait, majd `daemon-reload` és `enable --now`.
A példa legfeljebb három hibás indulást enged öt percen belül.

## Forgalomvédelem

A Transmission külön `transmission-vpn` hálózati névtérben fut. Az alapból tiltó
tűzfal már az underlay és a folyamatok indulása **előtt** létrejön:

- IPv4 torrent/tracker/DHT/webseed/DNS-forgalom csak `tun0` felé mehet.
- Az underlay kivétele a rootként futó OpenVPN egyetlen rögzített szerver-IP/portja.
  A nem root torrentfolyamat ezt a kivételt sem használhatja.
- Az RPC-válaszok külön, szűk szabályt kapnak a host felé. Nincs általános
  `ct state established accept`, ami útvonalváltáskor régi kapcsolatot kiengedne.
- IPv6-forgalom a loopback kivételével tiltott. Nincs IPv6 VPN-támogatás ebben a verzióban.
- Az RPC a VPN-alagút felől is tiltott. A hoston csak `127.0.0.1` figyel,
  a proxy legfeljebb 16 párhuzamos RPC-kapcsolatot kezel.
- UPnP/NAT-PMP és helyi peer-felderítés VPN-módban kikapcsolt.

A szolgáltató végpontjának DNS-feloldása egyszer, induláskor a **host resolverével**
történik. Ez a VPN bootstrap kivétele; torrent/tracker-domain nem kerül oda.
Utána a VPN-végpont IPv4-címe rögzített. Több `remote` sorból jelenleg az elsőt
használja; másik szerverhez vagy megváltozott végpont-IP-hez újraindítás szükséges.

Az újracsatlakozást az OpenVPN kezeli, a Transmission közben futhat, de csak az
alagúton forgalmazhat. VPN-folyamathalál vagy managementkapcsolat-vesztés esetén
a felügyelő leállítja a Transmissiont. A névtér tűzfala a felügyelő esetleges
összeomlásától függetlenül fennmarad. SIGKILL/áramszünet utáni elhagyott állapotot
nem töröl vakon: meglévő névtér/interfész/NAT-tábla esetén megtagadja az új indítást.
Ilyenkor rendszergazdai ellenőrzés vagy újraindítás kell; ne törölj aktív névteret.

## Erőforrások és korlátok

Nincs periodikus nyilvános IP-lekérdezés, payload-proxy vagy torrentenkénti VPN.
Egy OpenVPN-folyamat szolgálja ki az összes torrentet. A felügyelet management-
eseményekre és folyamatjelzésekre vár; nincs másodpercenkénti lekérdezés.
A helyi tartalék keepalive 20 s, kiesési újracsatlakozás 60 s. A szerver által
küldött időzítők elsőbbséget élveznek. Az OpenVPN csak kimenő forgalom nélküli
időszakban küld keepalive-ot: a tényleges csomagmegtakarítás a forgalomtól és a
szervertől függ; a teljes helyi tesztben nem mértünk csomagszámcsökkenést.

Az RPC-relé egyetlen, eseményvezérelt natív folyamat, kapcsolatindításonkénti
fork nélkül. Legfeljebb 16 kapcsolat, irányonként 16 KiB lusta pufferfoglalás,
visszaterhelés, 5 s kapcsolódási és 120 s inaktivitási időkorlát védi az erőforrásokat.
A bináris csomag tartalmazza az `rpc-relay` programot. Forrásból telepítéskor
C fordítóval futtasd a `bash build-relay.sh` parancsot ebben a könyvtárban.
UDP esetén a generált konfiguráció `fast-io` módot használ a TUN/UDP írások
rendszerhívásainak csökkentésére. TCP esetén ezt nem kapcsolja be; a profilban
megadott átviteli protokoll és titkosítás megmarad.

Tömörítésmentes profilnál `allow-compression no` kerül a konfigurációba, így
nem tiltjuk le szükségtelenül az OpenVPN automatikus DCO-gyorsítását.
Régi `compress`/`comp-lzo` profilnál megmarad a kompatibilis keretezés és az
aszimmetrikus fogadás. Ezeknél a DCO általában nem használható; a direktívát
ne töröld vakon, a szerverrel összeillő profil szükséges.
A DCO tényleges használatához megfelelő kernelmodul, AEAD algoritmusokat tartalmazó
`data-ciphers` lista és kompatibilis szerver is kell. A mellékelt WSL-mérésben
DCO nem volt elérhető, ezért a gyorsulási mérés kizárólag a userspace adatútra vonatkozik.

A root-only hitelesítési másolat és az állapot `/run/transmission-vpn` alatt van;
a titkos másolat leállításkor törlődik. A naplók 30 másodpercenként ellenőrzöttek:
1 MiB fölött a legutóbbi 256 KiB marad, ezért rövid ideig túlléphetik az 1 MiB-ot.
Szokásos Linuxon a `/run` tmpfs. Állapotfájl csak állapotváltáskor íródik.
A naplóellenőrzés fájlonként egy metadata-lekérdezést használ. Csonkoláskor
a megőrzött 256 KiB-os részt közös 64 KiB-os munkapufferrel másolja át;
az író folyamatok nyitott append fájlleírói használhatók maradnak.

A profilimport nem futtat `up/down` scripteket, plugineket, további configot,
proxyt vagy megadott külső programot; nem ismert direktívánál hibával megáll.
Az eredeti profil `script-security 2`, `route-method exe` és alapértelmezett
`route` sorait a saját beállításaira cseréli, nem hajtja végre őket.
TLS 1.2 minimum és szervertanúsítvány-ellenőrzés kötelező. Régi `comp-lzo`
profilnál a kimenő tömörítés letiltott, bejövő kompatibilitás megmarad.

Ebben a verzióban nincs PureVPN-porttovábbítási API, automatikus országválasztó,
fiókbelépési GUI vagy WireGuard. A szolgáltató által biztosított bejövő portot
szükség esetén külön kell beállítani. A root rendszergazda természetesen képes
a tűzfalat módosítani; a védelem nem a kompromittált host ellen készült.

## Források és ellenőrzés

- [PureVPN: kézi Linux/OpenVPN konfiguráció és VPN-hitelesítő adatok](https://support.purevpn.com/en_US/manual-connection-setup/how-to-setup-command-line-openvpn-on-linux)
- [OpenVPN 2.6 kézikönyv](https://openvpn.net/community-docs/community-articles/openvpn-2-6-manual.html)
- [OpenVPN management interfész](https://openvpn.net/community-docs/management-interface.html)
- [nftables socket UID illesztés](https://wiki.nftables.org/wiki-nftables/index.php/Matching_packet_metainformation)

Unit és socket tesztek: `python3 -m unittest -v test_vpn test_relay` ebből a könyvtárból
(előbb fordítsd le a relét). Összesen 25 teszt.
A mellékelt integrációs teszt izolált mount- és hálózati névtérben saját CA-val
és helyi OpenVPN-szerverrel fut. A mérési jelentés külön jelzi a valóban ellenőrzött
viselkedést. **Élő PureVPN-bejelentkezés nem történt**, ehhez a felhasználó érvényes
profilja és kézi VPN-hitelesítő fájlja szükséges.

Az integrációs teszt további függősége az `openssl` parancs. Reprodukció, még nem
létező kimeneti könyvtárral (a TCP-próbához másik könyvtár és utolsó argumentumként `tcp`):

```sh
sudo unshare --mount --net bash /opt/transmission/extras/vpn/run-integration-tests.sh \
  /opt/transmission/bin/transmission-daemon /var/tmp/transmission-vpn-test udp
```

Az izolált próba `sudo env VPN_BENCH_TRANSFER=1 unshare ...` indítással további
1 GiB letöltést és 1 GiB feltöltést végez SHA-256 ellenőrzéssel; az OpenVPN kliens
CPU-idejét külön méri. `VPN_TEST_NO_COMPRESSION=1` a helyi tesztszervert és profilt
tömörítésmentesre állítja. Ezek kizárólag tesztopciók, nem éles VPN-beállítások.

`VPN_BENCH_FAST_IO_ABBA=1` UDP-n ugyanazon kliensfolyamatot és helyi TLS-szervert
használva nyolc mérési szakaszt futtat (kétszer ABBA). Szakaszonként 1 GiB le és
1 GiB fel, bájtpontos ellenőrzéssel; kizárólag a `fast-io` beállítás változik.
A teszt végén visszaállítja a generált konfigurációt, majd végrehajtja a
szokásos kapcsolatvesztési és tűzfalteszteket is.
A külön csomagverziók összevetésére a `run-cpu-comparison.sh` használható:
`sudo bash run-cpu-comparison.sh RÉGI/extras/vpn ÚJ/extras/vpn DAEMON ÚJ_KIMENET`.
