//! Native message-packet backup (port of Android HistoryRepository).
//!
//! Server-authenticated receipts come from /api/history/updates; the raw
//! canonical event bytes come from /api/history/packets and are accepted only
//! if their SHA-256 matches the receipt. Everything is stored in a SQLCipher
//! database whose random key lives in the OS secure store.
//!
//! Unlike Android, the desktop never *offers* stored packets to other members
//! through /api/history/exchange: message plaintext does not leave this
//! machine. It may still *receive* packets that peers relay.
use crate::config::ORIGIN;
use base64::{engine::general_purpose::STANDARD, Engine};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;
use zeroize::Zeroizing;

/// Same epoch marker as the shared web client and Android (message-policy.ts).
pub const MESSAGE_EPOCH: &str = "plain-20261029-v1";
pub const MAX_PACKET: usize = 160_000;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(room TEXT NOT NULL, event TEXT NOT NULL, sequence INTEGER NOT NULL, sha256 TEXT NOT NULL,
  received_ms INTEGER NOT NULL, type TEXT NOT NULL, sender TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(room, event));
CREATE INDEX IF NOT EXISTS receipts_room_sequence ON receipts(room, sequence);
CREATE TABLE IF NOT EXISTS packets(room TEXT NOT NULL, event TEXT NOT NULL, sequence INTEGER NOT NULL, sha256 TEXT NOT NULL,
  raw BLOB NOT NULL, PRIMARY KEY(room, event));
CREATE TABLE IF NOT EXISTS tombstones(room TEXT NOT NULL, event TEXT NOT NULL, sequence INTEGER NOT NULL, PRIMARY KEY(room, event));
CREATE TABLE IF NOT EXISTS missing(event TEXT PRIMARY KEY, room TEXT NOT NULL, state TEXT NOT NULL, updated INTEGER NOT NULL);
";

#[derive(Debug)]
pub struct HistoryError(pub String);
impl<E: std::fmt::Display> From<E> for HistoryError {
    fn from(e: E) -> Self { HistoryError(e.to_string()) }
}
fn err<T>(m: &str) -> Result<T, HistoryError> { Err(HistoryError(m.into())) }

/// The account's encrypted packet database.
pub struct HistoryDb {
    pub storage: String,
    pub conn: Mutex<Connection>,
    pub key: Zeroizing<Vec<u8>>,
    pub persistent: bool,
}

impl HistoryDb {
    /// `path = None` keeps everything in memory (no OS secure store available).
    pub fn open(storage: &str, path: Option<&Path>, key: Zeroizing<Vec<u8>>) -> Result<Self, HistoryError> {
        let conn = match path {
            Some(p) => {
                if let Some(dir) = p.parent() { std::fs::create_dir_all(dir)?; }
                Connection::open(p)?
            }
            None => Connection::open_in_memory()?,
        };
        if path.is_some() {
            // Raw 256-bit key: no KDF, the key is already random.
            conn.pragma_update(None, "key", format!("x'{}'", hex::encode(&*key)))?;
        }
        conn.execute_batch("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA secure_delete=ON;")?;
        // Fails with "file is not a database" if the key is wrong.
        conn.query_row("SELECT count(*) FROM sqlite_master", [], |_| Ok(()))?;
        conn.execute_batch(SCHEMA)?;
        let db = Self { storage: storage.into(), conn: Mutex::new(conn), key, persistent: path.is_some() };
        db.reset_epoch()?;
        Ok(db)
    }

    /// Explicit server-wide chat reset (same rule as Android resetChatEpoch).
    fn reset_epoch(&self) -> Result<(), HistoryError> {
        let mut c = self.conn.lock().unwrap();
        if meta(&c, "message-epoch")?.as_deref() == Some(MESSAGE_EPOCH) { return Ok(()); }
        let tx = c.transaction()?;
        tx.execute_batch("DELETE FROM packets; DELETE FROM receipts; DELETE FROM tombstones; DELETE FROM missing; DELETE FROM meta;")?;
        tx.execute("INSERT INTO meta VALUES('message-epoch', ?1)", [MESSAGE_EPOCH])?;
        tx.commit()?;
        Ok(())
    }

    pub fn meta(&self, key: &str) -> Option<String> { meta(&self.conn.lock().unwrap(), key).ok().flatten() }
    pub fn set_meta(&self, key: &str, value: &str) -> Result<(), HistoryError> {
        self.conn.lock().unwrap().execute("INSERT INTO meta VALUES(?1, ?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value", params![key, value])?;
        Ok(())
    }
    pub fn packet_count(&self) -> i64 {
        self.conn.lock().unwrap().query_row("SELECT count(*) FROM packets", [], |r| r.get(0)).unwrap_or(0)
    }
    pub fn pending_missing(&self) -> i64 {
        self.conn.lock().unwrap().query_row("SELECT count(*) FROM missing WHERE state='pending'", [], |r| r.get(0)).unwrap_or(0)
    }

    /// Store authenticated receipts; withdrawals first, as Android does.
    pub fn save_records(&self, records: &[Value]) -> Result<(), HistoryError> {
        let mut c = self.conn.lock().unwrap();
        let tx = c.transaction()?;
        for r in records {
            let room = s(r, "room"); let eid = s(r, "event_id");
            let seq = r.get("sequence").and_then(Value::as_i64).ok_or(HistoryError("invalid record".into()))?;
            if !room.starts_with('!') || !eid.starts_with('$') { return err("invalid record"); }
            let target = if !s(r, "redacts").is_empty() { s(r, "redacts") } else if r.get("tombstone").is_some_and(Value::is_object) { eid.clone() } else { String::new() };
            if !target.is_empty() {
                tx.execute("INSERT INTO tombstones VALUES(?1, ?2, ?3) ON CONFLICT DO UPDATE SET sequence=excluded.sequence", params![room, target, seq])?;
            }
            tx.execute("INSERT INTO receipts VALUES(?1,?2,?3,?4,?5,?6,?7,?8) ON CONFLICT(room,event) DO UPDATE SET sequence=excluded.sequence,
                sha256=excluded.sha256, received_ms=excluded.received_ms, type=excluded.type, sender=excluded.sender, record=excluded.record",
                params![room, eid, seq, s(r, "sha256"), r.get("received_ms").and_then(Value::as_i64).unwrap_or(0), s(r, "type"), s(r, "sender"), r.to_string()])?;
        }
        tx.commit()?;
        Ok(())
    }

    /// Accept one /api/history/packets entry for `room`.
    pub fn accept(&self, p: &Value, room: &str) -> Result<(), HistoryError> {
        let eid = s(p, "event_id");
        let now = now_ms();
        let c = self.conn.lock().unwrap();
        if p.get("tombstone").is_some_and(Value::is_object) {
            c.execute("INSERT OR IGNORE INTO tombstones VALUES(?1, ?2, 0)", params![room, eid])?;
            c.execute("UPDATE missing SET state='withdrawn', updated=?2 WHERE event=?1", params![eid, now])?;
            return Ok(());
        }
        let raw = s(p, "raw");
        if raw.is_empty() {
            // Server copy gone: remember the ID so it is requested again later.
            c.execute("INSERT INTO missing VALUES(?1, ?2, 'pending', ?3) ON CONFLICT(event) DO NOTHING", params![eid, room, now])?;
            return Ok(());
        }
        let (sequence, hash, kind): (i64, String, String) = c.query_row(
            "SELECT sequence, sha256, type FROM receipts WHERE room=?1 AND event=?2", params![room, eid],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).optional()?.ok_or(HistoryError("缺少认证摘要".into()))?;
        let raw = Zeroizing::new(STANDARD.decode(raw)?);
        let event = verify_packet(&raw, &hash, room, &eid)?;
        if event.get("type").and_then(Value::as_str) != Some(kind.as_str()) { return err("原消息包类型不匹配"); }
        c.execute("INSERT OR IGNORE INTO packets VALUES(?1, ?2, ?3, ?4, ?5)", params![room, eid, sequence, hash, &*raw])?;
        c.execute("UPDATE missing SET state='restored', updated=?2 WHERE event=?1", params![eid, now])?;
        Ok(())
    }
}

fn meta(c: &Connection, key: &str) -> rusqlite::Result<Option<String>> {
    c.query_row("SELECT value FROM meta WHERE key=?1", [key], |r| r.get(0)).optional()
}
pub fn s(v: &Value, k: &str) -> String { v.get(k).and_then(Value::as_str).unwrap_or("").to_string() }
pub fn now_ms() -> i64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0) }

/// Canonical packet check: trailing LF, size, SHA-256, and event/room identity.
pub fn verify_packet(raw: &[u8], expected: &str, room: &str, event: &str) -> Result<Value, HistoryError> {
    if !(2..=MAX_PACKET).contains(&raw.len()) || raw.last() != Some(&b'\n') { return err("canonical 包缺少末尾 LF"); }
    let actual = hex::encode(Sha256::digest(raw));
    if !bool::from(subtle::ConstantTimeEq::ct_eq(actual.as_bytes(), expected.as_bytes())) { return err("原消息包哈希不匹配"); }
    let e: Value = serde_json::from_slice(raw)?;
    if s(&e, "room_id") != room || s(&e, "event_id") != event { return err("原消息包身份不匹配"); }
    Ok(e)
}

/// Authenticated gateway calls (Bearer, no Origin: the gateway's App transport).
pub struct Gateway<'a> { pub http: &'a reqwest::Client, pub token: &'a str }

#[derive(Debug)]
#[allow(dead_code)] // Failed carries a diagnostic category for debugging only.
pub enum CallError { Unauthorized, Failed(String) }
impl<E: std::fmt::Display> From<E> for CallError { fn from(e: E) -> Self { CallError::Failed(e.to_string()) } }

impl Gateway<'_> {
    async fn call(&self, path: &str, body: Option<Value>) -> Result<Value, CallError> {
        let url = format!("{ORIGIN}{path}");
        let req = match body { Some(b) => self.http.post(url).json(&b), None => self.http.get(url) };
        let r = req.bearer_auth(self.token).timeout(Duration::from_secs(60)).send().await?;
        if r.status() == reqwest::StatusCode::UNAUTHORIZED { return Err(CallError::Unauthorized); }
        if !r.status().is_success() { return Err(CallError::Failed(format!("status {}", r.status().as_u16()))); }
        Ok(r.json().await?)
    }

    /// Catch up receipts and packets since the saved watermark.
    pub async fn catch_up(&self, db: &HistoryDb, relogin: bool) -> Result<(), CallError> {
        let previous: i64 = db.meta("watermark").and_then(|v| v.parse().ok()).unwrap_or(0);
        let since = (previous - if relogin { 600_000 } else { 300_000 }).max(0);
        let (mut after, mut until) = (0i64, 0i64);
        loop {
            let page = self.call(&format!("/api/history/updates?since={since}&after={after}&until={until}"), None).await?;
            until = page.get("until").and_then(Value::as_i64).ok_or(CallError::Failed("until".into()))?;
            let records: Vec<Value> = page.get("records").and_then(Value::as_array).cloned().unwrap_or_default();
            db.save_records(&records).map_err(|e| CallError::Failed(e.0))?;
            let wanted: Vec<&Value> = records.iter().filter(|r| !r.get("tombstone").is_some_and(Value::is_object)).collect();
            for batch in wanted.chunks(20) {
                let ids: Vec<String> = batch.iter().map(|r| s(r, "event_id")).collect();
                let result = self.call("/api/history/packets", Some(json!({ "ids": ids }))).await?;
                for p in result.get("packets").and_then(Value::as_array).into_iter().flatten() {
                    let Some(r) = batch.iter().find(|r| s(r, "event_id") == s(p, "event_id")) else { continue };
                    // A single bad packet must not stall the window; it stays missing.
                    let _ = db.accept(p, &s(r, "room"));
                }
            }
            after = page.get("next_after").and_then(Value::as_i64).unwrap_or(0);
            // Advance only after commits; a crash repeats this window.
            if after == 0 && db.pending_missing() == 0 { db.set_meta("watermark", &until.to_string()).map_err(|e| CallError::Failed(e.0))?; }
            if after == 0 { break; }
        }
        Ok(())
    }

    /// Ask again for packets the server no longer holds; accept peer relays.
    /// This client never answers peers' requests ("asked").
    pub async fn recover(&self, db: &HistoryDb) -> Result<(), CallError> {
        let missing: Vec<(String, String)> = {
            let c = db.conn.lock().unwrap();
            let mut q = c.prepare("SELECT event, room FROM missing WHERE state='pending' ORDER BY updated LIMIT 20")?;
            let rows = q.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_, _>>()?;
            rows
        };
        if missing.is_empty() { return Ok(()); }
        let mut wants = std::collections::HashMap::new();
        for (event, room) in &missing {
            let result = self.call("/api/history/packets", Some(json!({ "ids": [event] }))).await?;
            for p in result.get("packets").and_then(Value::as_array).into_iter().flatten() {
                if !s(p, "request").is_empty() { wants.insert(s(p, "request"), room.clone()); } else { let _ = db.accept(p, room); }
            }
        }
        if wants.is_empty() { return Ok(()); }
        let keys: Vec<&String> = wants.keys().collect();
        let r = self.call("/api/history/exchange", Some(json!({ "requests": keys }))).await?;
        for p in r.get("fulfilled").and_then(Value::as_array).into_iter().flatten() {
            if let Some(room) = wants.get(&s(p, "request")) { let _ = db.accept(p, room); }
        }
        Ok(())
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;

    pub fn packet(room: &str, event: &str, kind: &str, body: &str) -> (Vec<u8>, String) {
        let raw = format!("{}\n", json!({ "content": { "body": body }, "event_id": event, "room_id": room, "sender": "@a:chat.yitiao.org", "type": kind }));
        let hash = hex::encode(Sha256::digest(raw.as_bytes()));
        (raw.into_bytes(), hash)
    }

    pub fn record(room: &str, event: &str, seq: i64, at: i64, hash: &str) -> Value {
        json!({ "room": room, "event_id": event, "sequence": seq, "received_ms": at, "type": "m.room.message",
                "sender": "@a:chat.yitiao.org", "origin_server_ts": at, "sha256": hash, "tombstone": null })
    }

    pub fn memory_db() -> HistoryDb { HistoryDb::open(&"a".repeat(64), None, Zeroizing::new(vec![0; 32])).unwrap() }

    #[test]
    fn verify_packet_rules() {
        let (raw, hash) = packet("!r:x", "$e", "m.room.message", "hi");
        assert!(verify_packet(&raw, &hash, "!r:x", "$e").is_ok());
        assert!(verify_packet(&raw, &hash, "!other:x", "$e").is_err());
        assert!(verify_packet(&raw[..raw.len() - 1], &hash, "!r:x", "$e").is_err());
        assert!(verify_packet(&raw, &"0".repeat(64), "!r:x", "$e").is_err());
    }

    #[test]
    fn accept_verifies_against_receipt_and_tracks_missing() {
        let db = memory_db();
        let (raw, hash) = packet("!r:x", "$e1", "m.room.message", "hi");
        db.save_records(&[record("!r:x", "$e1", 1, 1000, &hash), record("!r:x", "$e2", 2, 1001, &hash)]).unwrap();
        // Forged bytes are refused.
        let (forged, _) = packet("!r:x", "$e1", "m.room.message", "evil");
        assert!(db.accept(&json!({ "event_id": "$e1", "raw": STANDARD.encode(&forged) }), "!r:x").is_err());
        db.accept(&json!({ "event_id": "$e1", "raw": STANDARD.encode(&raw) }), "!r:x").unwrap();
        assert_eq!(db.packet_count(), 1);
        db.accept(&json!({ "event_id": "$e2", "request": "q1" }), "!r:x").unwrap();
        assert_eq!(db.pending_missing(), 1);
        db.accept(&json!({ "event_id": "$e2", "tombstone": {} }), "!r:x").unwrap();
        assert_eq!(db.pending_missing(), 0);
        // Packet without receipt.
        let (raw3, _) = packet("!r:x", "$e3", "m.room.message", "x");
        assert!(db.accept(&json!({ "event_id": "$e3", "raw": STANDARD.encode(&raw3) }), "!r:x").is_err());
    }

    #[test]
    fn sqlcipher_file_needs_the_key() {
        let dir = std::env::temp_dir().join(format!("yc-hist-{}", rand::random::<u64>()));
        let path = dir.join("history.db");
        let key = Zeroizing::new(vec![7u8; 32]);
        {
            let db = HistoryDb::open(&"a".repeat(64), Some(&path), key.clone()).unwrap();
            let (_, hash) = packet("!r:x", "$e1", "m.room.message", "secret-body");
            db.save_records(&[record("!r:x", "$e1", 1, 1000, &hash)]).unwrap();
            db.set_meta("probe", "secret-meta").unwrap();
        }
        let bytes = std::fs::read(&path).unwrap();
        assert!(!bytes.windows(11).any(|w| w == b"secret-meta") && !bytes.windows(15).any(|w| w == b"SQLite format 3"));
        assert!(HistoryDb::open(&"a".repeat(64), Some(&path), Zeroizing::new(vec![8u8; 32])).is_err());
        assert_eq!(HistoryDb::open(&"a".repeat(64), Some(&path), key).unwrap().meta("probe").as_deref(), Some("secret-meta"));
        let _ = std::fs::remove_dir_all(dir);
    }
}
