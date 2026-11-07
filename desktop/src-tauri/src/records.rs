//! "核验聊天记录": per-UTC-day digests over locally stored packets, in the
//! exact canonical form of the gateway's record_audit (yichat-record-digests-v1),
//! plus read-only pages of verified packets. Nothing is uploaded except the
//! digests the web UI sends to /api/records/verify itself.
use crate::history::{s, verify_packet, HistoryDb, HistoryError};
use base64::{engine::general_purpose::STANDARD, Engine};
use rusqlite::params;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub const FORMAT: &str = "yichat-record-digests-v1";
const DAY_MS: i64 = 86_400_000;

/// Python `json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False) + "\n"`.
/// Keys are sorted explicitly so serde_json's map ordering features cannot matter.
pub fn canonical(fields: BTreeMap<&str, Value>) -> Vec<u8> {
    let mut out = String::from("{");
    for (i, (k, v)) in fields.iter().enumerate() {
        if i > 0 { out.push(','); }
        out.push_str(&serde_json::to_string(k).unwrap());
        out.push(':');
        out.push_str(&serde_json::to_string(v).unwrap());
    }
    out.push_str("}\n");
    out.into_bytes()
}

/// Parse "YYYY-MM-DD" to UTC midnight in ms (proleptic Gregorian).
pub fn day_start(day: &str) -> Option<i64> {
    let b = day.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' { return None; }
    let (y, m, d): (i64, i64, i64) = (day[0..4].parse().ok()?, day[5..7].parse().ok()?, day[8..10].parse().ok()?);
    if !(1..=12).contains(&m) || d < 1 { return None; }
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let dim = [31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][(m - 1) as usize];
    if d > dim { return None; }
    // Days from civil (Howard Hinnant).
    let y2 = if m <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some((era * 146_097 + doe - 719_468) * DAY_MS)
}

pub fn day_string(ms: i64) -> String {
    let z = ms.div_euclid(DAY_MS) + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + if m <= 2 { 1 } else { 0 };
    format!("{y:04}-{m:02}-{d:02}")
}

fn range(start: &str, end: &str) -> Result<Vec<String>, HistoryError> {
    let (a, b) = (day_start(start), day_start(end));
    let (Some(a), Some(b)) = (a, b) else { return Err(HistoryError("请选择连续1至5个UTC日期".into())) };
    let count = (b - a) / DAY_MS + 1;
    if !(1..=5).contains(&count) { return Err(HistoryError("请选择连续1至5个UTC日期".into())); }
    Ok((0..count).map(|i| day_string(a + i * DAY_MS)).collect())
}

fn valid_room(room: &str) -> Result<(), HistoryError> {
    if room.starts_with('!') && room.len() <= 255 { Ok(()) } else { Err(HistoryError("会话无效".into())) }
}

struct Row { event: String, sequence: i64, received_ms: i64, sha256: String, kind: String, sender: String, raw: Option<Vec<u8>>, withdrawn: bool }

fn rows(db: &HistoryDb, room: &str, after: i64, low: i64, high: i64, limit: i64) -> Result<Vec<Row>, HistoryError> {
    let c = db.conn.lock().unwrap();
    let mut q = c.prepare("SELECT r.event, r.sequence, r.received_ms, r.sha256, r.type, r.sender, p.raw,
        EXISTS(SELECT 1 FROM tombstones t WHERE t.room=r.room AND t.event=r.event)
        FROM receipts r LEFT JOIN packets p ON p.room=r.room AND p.event=r.event
        WHERE r.room=?1 AND r.sequence>?2 AND r.received_ms>=?3 AND r.received_ms<?4 ORDER BY r.sequence LIMIT ?5")?;
    let out = q.query_map(params![room, after, low, high, limit], |r| Ok(Row {
        event: r.get(0)?, sequence: r.get(1)?, received_ms: r.get(2)?, sha256: r.get(3)?, kind: r.get(4)?,
        sender: r.get(5)?, raw: r.get(6)?, withdrawn: r.get(7)?,
    }))?.collect::<Result<Vec<_>, _>>()?;
    Ok(out)
}

pub fn rooms(db: &HistoryDb) -> Result<Value, HistoryError> {
    let c = db.conn.lock().unwrap();
    let mut q = c.prepare("SELECT DISTINCT room FROM receipts ORDER BY room")?;
    let list: Vec<String> = q.query_map([], |r| r.get(0))?.collect::<Result<_, _>>()?;
    Ok(json!(list))
}

pub fn days(db: &HistoryDb, room: &str, start: &str, end: &str) -> Result<Value, HistoryError> {
    valid_room(room)?;
    let dates = range(start, end)?;
    let low = day_start(&dates[0]).unwrap();
    let high = day_start(dates.last().unwrap()).unwrap() + DAY_MS;
    let mut digests: BTreeMap<String, Sha256> = dates.iter().map(|d| {
        let mut h = Sha256::new();
        h.update(canonical(BTreeMap::from([("day", json!(d)), ("format", json!(FORMAT)), ("room_id", json!(room)), ("version", json!(1))])));
        (d.clone(), h)
    }).collect();
    let mut counts: BTreeMap<String, i64> = dates.iter().map(|d| (d.clone(), 0)).collect();
    let mut missing = counts.clone();
    let mut after = 0;
    loop {
        let batch = rows(db, room, after, low, high, 500)?;
        if batch.is_empty() { break; }
        for row in batch {
            after = row.sequence;
            let day = day_string(row.received_ms);
            *counts.get_mut(&day).unwrap() += 1;
            let ok = row.raw.as_deref().and_then(|raw| {
                let event = verify_packet(raw, &row.sha256, room, &row.event).ok()?;
                (s(&event, "type") == row.kind && s(&event, "sender") == row.sender).then_some(event)
            });
            let Some(event) = ok else { *missing.get_mut(&day).unwrap() += 1; continue };
            let redacts = if s(&event, "type") == "m.room.redaction" {
                let r = event.get("content").map(|c| s(c, "redacts")).filter(|r| !r.is_empty()).unwrap_or_else(|| s(&event, "redacts"));
                if r.is_empty() { Value::Null } else { json!(r) }
            } else { Value::Null };
            digests.get_mut(&day).unwrap().update(canonical(BTreeMap::from([
                ("event_id", json!(row.event)), ("received_ms", json!(row.received_ms)), ("redacts", redacts),
                ("room_id", json!(room)), ("sender", json!(s(&event, "sender"))), ("sequence", json!(row.sequence)),
                ("sha256", json!(hex::encode(Sha256::digest(row.raw.as_deref().unwrap())))), ("type", json!(s(&event, "type"))),
            ])));
        }
    }
    let days: Vec<Value> = dates.iter().map(|d| {
        let gap = missing[d];
        json!({ "day": d, "count": counts[d], "complete": gap == 0, "missing": gap,
                "sha256": if gap == 0 { json!(hex::encode(digests.remove(d).unwrap().finalize())) } else { Value::Null } })
    }).collect();
    Ok(json!({ "format": FORMAT, "room": room, "start": start, "end": end, "days": days }))
}

pub fn page(db: &HistoryDb, room: &str, day: &str, after: &str) -> Result<Value, HistoryError> {
    valid_room(room)?;
    range(day, day)?;
    let mut cursor: i64 = if after.is_empty() { 0 } else { after.parse().map_err(|_| HistoryError("游标无效".into()))? };
    if cursor < 0 { return Err(HistoryError("游标无效".into())); }
    let low = day_start(day).unwrap();
    let (mut out, mut size, mut more) = (Vec::new(), 0usize, false);
    'outer: loop {
        let batch = rows(db, room, cursor, low, low + DAY_MS, 200)?;
        if batch.is_empty() { break; }
        for row in batch {
            if out.len() >= 30 { more = true; break 'outer; }
            let raw = row.raw.ok_or(HistoryError("原包缺失，请重新核验".into()))?;
            verify_packet(&raw, &row.sha256, room, &row.event)?;
            let item = json!({ "event_id": row.event, "received_ms": row.received_ms, "sequence": row.sequence.to_string(),
                "sha256": row.sha256, "withdrawn": row.withdrawn, "raw": STANDARD.encode(&raw) });
            let bytes = item.to_string().len();
            if !out.is_empty() && size + bytes > 260_000 { more = true; break 'outer; }
            size += bytes; cursor = row.sequence; out.push(item);
        }
    }
    Ok(json!({ "rows": out, "next_after": if more { json!(cursor.to_string()) } else { Value::Null } }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::history::tests::{memory_db, packet, record};

    #[test]
    fn dates() {
        assert_eq!(day_start("1970-01-01"), Some(0));
        assert_eq!(day_start("2026-11-06"), Some(1_793_923_200_000));
        assert_eq!(day_string(1_793_923_200_000 + DAY_MS - 1), "2026-11-06");
        assert_eq!(day_string(day_start("2024-02-29").unwrap()), "2024-02-29");
        assert!(day_start("2023-02-29").is_none() && day_start("2026-13-01").is_none() && day_start("2026-1-01").is_none());
        assert!(range("2026-11-01", "2026-11-05").is_ok() && range("2026-11-01", "2026-11-06").is_err() && range("2026-11-02", "2026-11-01").is_err());
    }

    #[test]
    fn canonical_matches_python() {
        // python3 -c 'import json;print(json.dumps({"room_id":"!r:x","day":"2026-11-06","version":1,"format":"yichat-record-digests-v1","n":None,"u":"中"},ensure_ascii=False,sort_keys=True,separators=(",",":")))'
        let got = canonical(BTreeMap::from([("room_id", json!("!r:x")), ("day", json!("2026-11-06")), ("version", json!(1)),
            ("format", json!(FORMAT)), ("n", Value::Null), ("u", json!("中"))]));
        assert_eq!(String::from_utf8(got).unwrap(),
            "{\"day\":\"2026-11-06\",\"format\":\"yichat-record-digests-v1\",\"n\":null,\"room_id\":\"!r:x\",\"u\":\"中\",\"version\":1}\n");
    }

    #[test]
    fn days_and_pages() {
        let db = memory_db();
        let base = day_start("2026-11-05").unwrap();
        let (raw1, h1) = packet("!r:x", "$e1", "m.room.message", "one");
        let (_, h2) = packet("!r:x", "$e2", "m.room.message", "two");
        db.save_records(&[record("!r:x", "$e1", 1, base + 10, &h1), record("!r:x", "$e2", 2, base + DAY_MS + 5, &h2)]).unwrap();
        db.accept(&json!({ "event_id": "$e1", "raw": STANDARD.encode(&raw1) }), "!r:x").unwrap();
        let v = days(&db, "!r:x", "2026-11-05", "2026-11-06").unwrap();
        let d = v["days"].as_array().unwrap();
        assert_eq!((d[0]["count"].as_i64(), d[0]["complete"].as_bool()), (Some(1), Some(true)));
        assert_eq!((d[1]["count"].as_i64(), d[1]["missing"].as_i64(), d[1]["sha256"].is_null()), (Some(1), Some(1), true));
        assert_eq!(d[0]["sha256"].as_str().unwrap().len(), 64);
        let p = page(&db, "!r:x", "2026-11-05", "0").unwrap();
        assert_eq!(p["rows"].as_array().unwrap().len(), 1);
        assert!(p["next_after"].is_null());
        assert!(page(&db, "!r:x", "2026-11-06", "0").is_err()); // packet missing
        assert!(days(&db, "bad", "2026-11-05", "2026-11-05").is_err());
    }
}
