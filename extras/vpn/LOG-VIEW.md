# Transmission webes napló (v18)

Az Áttekintés oldal **Transmission napló** paneljén:

- keresés az üzenet és forrás szövegében, kis-/nagybetűtől függetlenül;
- pontos naplószint szerinti szűrés (kritikus, hiba, figyelmeztetés, információ, debug, trace);
- legújabb/legrégebbi időrend, súlyosság vagy forrás szerinti rendezés;
- kézi frissítés, találatszám és az utolsó sikeres frissítés időpontja.

A panel az aktuális daemonfolyamat utolsó **512** rögzített üzenetét, a VPN-felügyelő eseményeit (`VPN`) és a részletes, maszkolt OpenVPN-sorokat (`OpenVPN`) mutatja egy közös, időrendbe tett listában. Alapértelmezésben a legújabb sor van elöl. A Napló fül aktív állapotában két másodpercenként frissül; más fülön nincs időzített naplólekérés. A normál naplózási szint változatlan: debug/trace csak akkor jelenik meg, ha a daemon ezeket is naplózza. A folyamat újraindításakor a daemon memóriabeli előzménye kiürül; korábbi vagy forgatott logfájlokat nem olvas be.

A daemon külön puffere nem fogyasztja el a rendes naplóüzeneteket, nem módosítja a logfájlt és nem okoz további lemezírást. A pufferben az üzenet legfeljebb 1024, a forrás 128 UTF-8 bájt, szükség esetén ellipszissel; a normál log teljes szövege változatlan. A szövegtartalom összesen legfeljebb kb. 579 KiB, ezen felül a bejegyzések és allokátor metaadatai. Lekéréskor ideiglenesen másolat és JSON-válasz is készül.

Az OpenVPN-fájl új sorait a VPN-felügyelő növekményesen olvassa, ezért nem olvassa újra a teljes fájlt. A frissítési időköz `vpn_log_flush_seconds` (1–30, alapérték 2 másodperc); csak új sor esetén írja az atomikus `vpn-log.json` pillanatképet. A `vpn_log_retention` mező 1–2000 sor között állítható (alapérték 256); emellett 60 KiB-os átadási korlát védi az RPC-t, így extrém hosszú soroknál a legrégebbiek hamarabb esnek ki. Egyetlen 4096 bájtnál hosszabb sor biztonsági korlát miatt csonkolódik.

A maszkolás eltávolítja a VPN felhasználónevet, jelszót, URL-ből származó jelszót, valamint a `password`, `passwd`, `token`, `secret`, `username` és `user` névvel jelölt értékeket. A webes listába a root által védett OpenVPN-fájl nyers tartalma nem kerül, csak a maszkolt sorok. A maszkolás védelmi réteg, ezért az adminfelület továbbra is bizalmas: hálózati címek, tanúsítvány-információk és fájlutak előfordulhatnak.

Nincs háttérbeli automatikus naplólekérés. Megnyitáskor és a Frissítés gombbal kérjük le a korlátos pillanatképet. A szűrés és rendezés helyben történik, hálózati kérés nélkül. A találatszám a betöltött részletre vonatkozik, nem a teljes logfájlra.

API: JSON-RPC `log_get`, üres `params`. A válasz `entries` listája a daemon sorait, a `vpn_entries.entries` listája a VPN- és OpenVPN-sorokat tartalmazza, időmezővel `time_ms` (Unix milliszekundum), valamint `level` (1–6), `source` és `message` mezővel. `capacity: 512`, `scope: "current_process"`. Az RPC meglévő hozzáférés-védelme érvényes. A kliens szövegként jeleníti meg az üzeneteket, HTML-t nem értelmez belőlük. A napló tartalmazhat torrentneveket, fájlútvonalakat és hálózati címeket, ugyanúgy, mint a daemon normál logja.
