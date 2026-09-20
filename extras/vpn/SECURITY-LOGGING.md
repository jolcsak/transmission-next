# Biztonsági események

A webes Napló fülön a `Security` keresőszóval szűrhetők. A daemon eseményei a `log_get.result.entries`, a HTTPS-előtét megőrzött eseményei a már meglévő `log_get.result.vpn_entries.entries` mezőben érkeznek. A webes felület ezeket közös, időrendben rendezhető naplóként jeleníti meg.

| Eseménykód | Jelentés |
| --- | --- |
| rpc_auth_failed | Sikertelen RPC-hitelesítés |
| rpc_rate_limited | Aktív, ideiglenes RPC-tiltás miatti elutasítás |
| rpc_access_denied | RPC IP-/Host-engedélylista miatti elutasítás |
| rpc_path_traversal_rejected | Tiltott webes útvonal |
| p2p_message_rejected | Ismeretlen vagy érvénytelen méretű P2P-üzenet |
| unsafe_file_path_rejected | Symlink, hibás köztes útvonalelem vagy speciális fájl elutasítása |
| http_request_rejected | A HTTPS-előtét által rögzített HTTP 400/403/413/414/429/494 válasz |

Az elutasítás biztonsági szempontból releváns esemény, önmagában nem bizonyít szándékos támadást. A szokásos Transmission 409 session-ID egyeztetés és a sikeres kérések nem incidensek; a meglévő sikeres belépési naplózás megmarad.

## Korlátok és adatvédelem

- A daemon öt kategóriában, kategóriánként legfeljebb 5 részletes bejegyzést ír 60 másodperc alatt. Az ötödik jelzi az összesítés kezdetét; a következő időablak első eseménye megadja az elnyomott ismétlések számát. Ha nincs következő esemény, külön összesítő bejegyzés nem keletkezik.
- A HTTPS-előtét csak státuszkódot és a proxy által látott IP-címet küld, helyi Unix datagram socketen. A gyűjtő kernel által közölt UID alapján kizárólag a www-data feladót fogadja el. Kódonként legfeljebb percenként ír összesítést; a függő összesítést akkor is kiírja, ha közben megszűnik a forgalom.
- Egy feldolgozási körben legfeljebb 256 datagram, korlátos socketpuffer és rögzített eseménykód-készlet használható. Túlterheléskor datagramok elveszhetnek; a `count` a fogadott, nem a garantáltan összes HTTP-esemény száma. Nincs nyers hozzáférési naplófájl. Az nginx diagnosztikájára systemd journal rate limit vonatkozik.
- Jelszó, jelszóhash, Authorization fejléc, token, URL/query, payload vagy fájlútvonal nem része az új incidensüzeneteknek. Peer IP/port, típus, üzenethossz és errno szerepelhet. A LAN-portproxy miatt az eredeti kliens helyett helyi proxy-IP látszhat.
- A daemon naplója a meglévő 512 bejegyzéses folyamaton belüli megőrzést használja; a proxy eseményei a meglévő, konfigurálható VPN/egyesített naplóretencióba számítanak bele. A figyelmeztetési szintű daemon eseményekre az általános naplószint-beállítás is érvényes.

## Ellenőrzés

123 C++ regressziós teszt sikeres, köztük 5000 ismétlődő esemény naplókorlátozásának vizsgálata. Izolált daemonon a sikertelen belépés, tiltás és rossz P2P-fejléc eseményei ténylegesen megjelennek a log_get API-ban. A Python tesztek ellenőrzik a feladóazonosítást, a csonkolt/hibás datagramok elutasítását, az összesítést és a 0077 umask mellett indított valódi www-data feladót.

Az éles LAN HTTPS-végponton egyetlen, payload nélküli túlméretes Content-Length próba 413-at kapott, majd a Security esemény megjelent a megőrzött, webes API által olvasott naplóban. A VPN és a TLS-szolgáltatás fut.
