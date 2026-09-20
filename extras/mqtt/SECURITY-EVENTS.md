# Security incidensek MQTT- és InfluxDB-exportja

Az incidensek automatikusan az engedélyezett telemetria-kimenetekre kerülnek. Az MQTT és InfluxDB továbbra is külön-külön opcionális; kikapcsolt exporthoz nem indul új folyamat. A beállításokat a meglévő adminfelület/config JSON/env mechanizmus kezeli. A célpontok mellett az exporter helyi RPC-hitelesítését is be kell állítani.

## MQTT

Topic: `<topic_prefix>/security/events`, alapértelmezetten `transmission/security/events`.

QoS 1, `retain=false`. Egy üzenet legfeljebb 64 incidenst tartalmaz:

```json
{
  "schema_version": 1,
  "events": [{
    "event_id": "stabil-sha256-azonosito",
    "event_code": "rpc_auth_failed",
    "time_ms": 1789889673000,
    "level": 3,
    "count": 1
  }],
  "dropped": 0
}
```

A `dropped` az MQTT várólistából helyhiány miatt eldobott incidensrekordok száma az exporter indulása óta. Az `event_id` használható fogyasztóoldali duplikációszűrésre. Egy incidensrekord több összesített elutasítást is jelenthet (`count`), illetve tartalmazhat `previous_window_suppressed` számlálót.

## InfluxDB

Measurement: `transmission_security`.

Tag-ek: `instance`, `event_code`. Az eseményazonosító mező, nem tag, így nem hoz létre incidensenként új idősor-sorozatot. Mezők: `event_id`, `time_ms`, `level`, `count`, és ha elérhető: `status`, `type`, `length`, `errno`, `previous_window_suppressed`.

A közös writer ezentúl `precision=ns` pontosságot használ. A normál metrikák másodperces időbélyegét ennek megfelelően átszámolja, így azok időbeli helye nem változik. Az incidensek külön pontot kapnak akkor is, ha azonos milliszekundumban történtek; a `time_ms` mindig megőrzi az eredeti naplóidőt. Az adatbázis retention szabályait a kiválasztott bucket kezeli.

## Erőforrás- és kézbesítési korlátok

- Mintavételenként egy további `log_get` hívás történik, a meglévő tartós RPC-kapcsolaton. Csak a `Security` forrás ismert eseménykódjai exportálhatók.
- Legfeljebb 4096 eseményazonosító marad a memóriabeli duplikációszűrőben. MQTT: legfeljebb 256 várakozó incidens, kötegenként legfeljebb 64, törlés csak sikeres PUBACK után. InfluxDB: a meglévő, bájtban korlátozott queue és retry writer, új szál nélkül.
- MQTT QoS 1 miatt bizonytalan visszaigazoláskor lehet ismétlés. Újraindításkor a még megőrzött naplóbejegyzések újra exportálhatók; a duplikációszűrő nem ír checkpointot lemezre. Ez nem tartós, veszteségmentes auditcsatorna. Retenció, hosszú kiesés vagy túlterhelés során események elveszhetnek. Exporter-újraindítás után Influxban is lehetséges ismétlés; az `event_id` alapján azonosítható.
- Az export nem tartalmaz nyers naplóüzenetet, URL-t, IP-címet, fájlútvonalat, Authorization fejlécet, jelszót vagy tokent. Csak az eseménykód, idő és engedélyezett numerikus adatok hagyják el a gépet.
- Egyik kimenet hibája nem törli a másik kimenet várakozó eseményeit. Az incidenslekérdezés hibája nem állítja le a normál metrikák exportját.

## Ellenőrzés és telepítés

A tesztcsomag valódi Mosquitto brokerrel ellenőrzi a küldést és az ismételt lekérdezések kiszűrését. Az InfluxDB v2 írási API formátumát helyi HTTP tesztszerver ellenőrzi, beleértve az időbélyegeket és a kiesési puffert. Ez nem külső, éles InfluxDB-átvételi igazolás.

A jelenlegi telepítésben mindkét kimenet ki van kapcsolva és nincs kitöltött célpont. Az új modulok telepítése után a célpontok és hitelesítési adatok beállításával, majd a kimenetek engedélyezésével indul az éles publikálás.
