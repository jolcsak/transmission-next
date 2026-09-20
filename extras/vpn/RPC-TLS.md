# Titkosított RPC a jelenlegi Windows/WSL telepítésben

A dedikált `transmission-rpc-tls.service` egy nginx TLS-előtétet indít a WSL `127.0.0.1:9091` címén. A `127.0.0.1:19091` relay csak a helyi HTTP backend. A LAN `192.168.50.247:9091` porttovábbítása változatlanul a WSL 9091 portjára mutat. A VPN-konfiguráció belső RPC-portja és a helyi telemetria RPC URL-je 19091-re módosult. A LAN-tűzfalszabály és annak helyi alhálózati korlátozása változatlan marad.

Az átállított webcím: **https://192.168.50.247:9091/transmission/web/**

Natív Transmission kliens: szerver `192.168.50.247`, port `9091`, SSL/HTTPS bekapcsolva, RPC útvonal `/transmission/rpc`, a meglévő RPC-felhasználónév és jelszó. A csak HTTP-t támogató klienshez titkosított alagút vagy HTTPS-képes kliens szükséges; a LAN-on a régi HTTP-végpontot nem kell visszanyitni.

## Tanúsítvány

A helyi, önaláírt szervertanúsítvány SAN mezői: `192.168.50.247`, `127.0.0.1`, `localhost`. Érvényessége a létrehozástól 365 nap. Nyilvános példánya: `outputs/transmission-rpc-server.crt`. A privát kulcs csak WSL-ben, `/etc/transmission-rpc-tls/server.key` alatt, root jogosultsággal olvasható. A kulcs nem része a csomagnak és nem került a munkakönyvtárba.

A tanúsítványt minden kliensen megbízhatóként kell felvenni vagy explicit ehhez a szerverhez rögzíteni. A kliens és böngésző tanúsítványtára eltérhet. Ne általános tanúsítványellenőrzés-kikapcsolást használj. Windows alatt a felhasználói tanúsítványtárba történő import a tanúsítványkezelőből végezhető el; az alkalmazás saját tár esetén külön importot kérhet. Ehhez a helyi szerverhez csak a mellékelt tanúsítványt használd.

Ellenőrzés WSL-ben:

```sh
curl --cacert /etc/transmission-rpc-tls/server.crt https://127.0.0.1:9091/transmission/rpc
```

Hitelesítő adatok nélkül a 401 válasz az elvárt eredmény. A TLS-kiszolgáló legalább TLS 1.2-t használ. A tanúsítvány megújításáról lejárat előtt gondoskodni kell; új önaláírt tanúsítvány esetén a klienseken is frissíteni kell a bizalmat. Az IP megváltozásakor a SAN mezőt és a LAN porttovábbítását is módosítani kell.

## Működés

- Nginx: egy worker, legfeljebb 8 egyidejű aktív kérés egy látott forrásról; a relay összesen 16 kapcsolatot enged. A Windows portproxy mögött több kliens is közös forrásnak látszhat.
- 64 MiB kérésméret-korlát, 30 másodperces kliens body- és backend HTTP-időkorlát, korlátos TLS-session cache. A nagyon nagy torrent metainfo-feltöltéseket a 64 MiB korlát visszautasítja.
- Az RPC-tiltás a backend által látott forrásra vonatkozik és 30 másodperc után feloldódik. A relay mögött ez lehet közös tiltás. Nem megbízható kliensfejlécek alapján nem történik IP-azonosítás.
- Az új TLS-szolgáltatás konfigurációja nem tartalmaz RPC- vagy VPN-jelszót; HTTP access log nincs bekapcsolva.
- A TLS-eszközök itt a helyi telepítéshez készültek. Más gépen saját szervercímmel és saját privát kulccsal kell őket telepíteni; a privát kulcsot nem szabad átmásolni csomagként.
