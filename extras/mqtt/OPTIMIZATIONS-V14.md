# v14 – telemetria-adminisztráció optimalizálása

- Azonos JSON ismételt mentése nem írja újra a `config/telemetry.json` fájlt. A kérés és az eredmény továbbra is rögzül, így az adminfelület megbízható visszaigazolást kap.
- Ha az env-felülírások után érvényes konfiguráció nem változott, és az exporter szála él, a mentés nem indítja újra. Az env által elfedett JSON-mező módosítása eltárolódik, de nem bontja a kapcsolatokat.
- Megszűnt a mentés közbeni többszörös exporter-példányosítás. Változáskor egy új példány készül; a szükséges TLS-kontextus és hitelesítőfájlok előkészítése a régi exporter leállítása előtt történik.
- A ténylegesen használt külső jelszó-, token- és tanúsítványfájlok eszköz/inode/méret/mtime/ctime adatait is összevetjük. Változásuk mentéskor új példányt indít. A letiltott kimenet vagy inline titok által felülírt fájl nincs feleslegesen megnyitva/ellenőrizve.
- Ha az exporter szála már leállt, az azonos beállítás mentése is újraindítja. A kapcsolatteszt továbbra is valódi, külön próba, nem gyorsítótárazott sikerjelzés.

A fájlok ellenőrzése adminműveletkor történik; nincs új háttérpolling. A fájlon belüli külső módosítás felismerése fájlrendszer-metaadatokra épül. Ha ugyanazokat a metaadatokat is visszaállítják, a felügyelő kézi újraindítása biztosítja az újratöltést. A működő szál nem feltétlenül jelent elérhető szervert: a hálózati kieséseket továbbra is az MQTT/InfluxDB újracsatlakozási logika kezeli.

## Mérési eredmény

100 azonos, egymást követő mentés után:

| Mért művelet | v13 | v14 |
| --- | ---: | ---: |
| `telemetry.json` írása | 100 | 0 |
| Exporter-példány létrehozása, validálási példányokkal együtt | 300 | 0 |
| Műveleti eredmény írása | 100 | 100 |

A mérés tényleges fájlírásokkal és felügyelőlogikával futott, egy várakozó tesztszállal helyettesített exporterrel. Ez műveletszám-mérés, nem a teljes daemon CPU- vagy RAM-nyereségének mérése. A visszajelzéshez szükséges admin-I/O megmaradt, ezért a teljes fájlírás nem lett nulla.

A daemon, a webfelület, az env/JSON prioritás és az API formátuma változatlan. A meglévő felügyelő újraindítása szükséges az új Python-modul betöltéséhez.
