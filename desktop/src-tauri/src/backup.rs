//! Backups of the packet database.
//! - Internal snapshot: an encrypted copy of the SQLCipher database
//!   (sqlcipher_export, same random key) next to it, refreshed every 6 h.
//! - External export: Android-compatible YCB1 file
//!   ("YCB1" | salt(32) | iv(12) | AES-256-GCM(ciphertext||tag)), key from
//!   scrypt(N=32768, r=8, p=1) over a separate user password, fixed AAD.
//!   Plain JSON "yichat-app-packets" v1, identical to Android's content.
use crate::history::{now_ms, HistoryDb, HistoryError};
use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use base64::{engine::general_purpose::STANDARD, Engine};
use rand::RngCore;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use zeroize::Zeroizing;

const MAGIC: &[u8; 4] = b"YCB1";
const AAD: &[u8] = b"YiChat-App-Backup-v1;scrypt:N32768:r8:p1;AES256-GCM";
pub const SNAPSHOT_INTERVAL_MS: i64 = 6 * 3_600_000;
pub const MAX_EXPORT: usize = 100_000_000;

fn derive(password: &[u8], salt: &[u8]) -> Result<Zeroizing<[u8; 32]>, HistoryError> {
    let mut key = Zeroizing::new([0u8; 32]);
    let params = scrypt::Params::new(15, 8, 1, 32).map_err(|e| HistoryError(e.to_string()))?;
    scrypt::scrypt(password, salt, &params, &mut *key).map_err(|e| HistoryError(e.to_string()))?;
    Ok(key)
}

pub fn encrypt(plain: &[u8], password: &[u8]) -> Result<Vec<u8>, HistoryError> {
    let (mut salt, mut iv) = ([0u8; 32], [0u8; 12]);
    rand::rngs::OsRng.fill_bytes(&mut salt);
    rand::rngs::OsRng.fill_bytes(&mut iv);
    let key = derive(password, &salt)?;
    let cipher = Aes256Gcm::new_from_slice(&*key).unwrap();
    let sealed = cipher.encrypt(Nonce::from_slice(&iv), Payload { msg: plain, aad: AAD }).map_err(|_| HistoryError("加密失败".into()))?;
    Ok([MAGIC.as_slice(), &salt, &iv, &sealed].concat())
}

/// Used by restore (not exposed to the UI yet, as on Android) and tests.
#[allow(dead_code)]
pub fn decrypt(packed: &[u8], password: &[u8]) -> Result<Zeroizing<Vec<u8>>, HistoryError> {
    if packed.len() < 64 || &packed[..4] != MAGIC { return Err(HistoryError("不支持的备份格式".into())); }
    let key = derive(password, &packed[4..36])?;
    let cipher = Aes256Gcm::new_from_slice(&*key).unwrap();
    cipher.decrypt(Nonce::from_slice(&packed[36..48]), Payload { msg: &packed[48..], aad: AAD })
        .map(Zeroizing::new).map_err(|_| HistoryError("口令错误或文件已损坏".into()))
}

/// Android-compatible plain content (kept only in zeroized memory).
pub fn packet_content(db: &HistoryDb, uid: &str, mxid: &str) -> Result<Zeroizing<Vec<u8>>, HistoryError> {
    let c = db.conn.lock().unwrap();
    let mut packets = Vec::new();
    {
        let mut q = c.prepare("SELECT room, event, sha256, raw FROM packets ORDER BY sequence")?;
        let mut rows = q.query([])?;
        while let Some(r) = rows.next()? {
            let raw: Zeroizing<Vec<u8>> = Zeroizing::new(r.get(3)?);
            packets.push(json!({ "room": r.get::<_, String>(0)?, "event": r.get::<_, String>(1)?, "sha256": r.get::<_, String>(2)?, "raw": STANDARD.encode(&*raw) }));
        }
    }
    let mut tombstones = Vec::new();
    {
        let mut q = c.prepare("SELECT room, event FROM tombstones ORDER BY room, event")?;
        for t in q.query_map([], |r| Ok(json!({ "room": r.get::<_, String>(0)?, "event": r.get::<_, String>(1)? })))? { tombstones.push(t?); }
    }
    let content = json!({ "format": "yichat-app-packets", "version": 1, "uid": uid, "mxid": mxid, "packets": packets, "tombstones": tombstones });
    Ok(Zeroizing::new(serde_json::to_vec(&content)?))
}

/// Encrypted copy of the database with the same key (no plaintext on disk).
pub fn snapshot(db: &HistoryDb, target: &Path) -> Result<(), HistoryError> {
    if !db.persistent { return Ok(()); }
    let tmp = target.with_extension("tmp");
    let _ = std::fs::remove_file(&tmp);
    let c = db.conn.lock().unwrap();
    let key = Zeroizing::new(format!("x'{}'", hex::encode(&*db.key)));
    c.execute("ATTACH DATABASE ?1 AS snapshot KEY ?2", rusqlite::params![tmp.to_string_lossy(), &*key])?;
    let result = c.query_row("SELECT sqlcipher_export('snapshot')", [], |_| Ok(()));
    c.execute("DETACH DATABASE snapshot", [])?;
    result?;
    drop(c);
    std::fs::rename(&tmp, target)?;
    db.set_meta("snapshot-last", &now_ms().to_string())?;
    Ok(())
}

/// Write a YCB1 export into `dir` atomically; returns the file path.
pub fn export(db: &HistoryDb, uid: &str, mxid: &str, dir: &Path, password: &[u8]) -> Result<PathBuf, HistoryError> {
    let content = packet_content(db, uid, mxid)?;
    let packed = encrypt(&content, password)?;
    if packed.len() > MAX_EXPORT + 64 { return Err(HistoryError("导出超过100MB上限".into())); }
    let name = format!("YiChat-{}.ycb", now_ms());
    let target = dir.join(&name);
    let tmp = dir.join(format!(".{name}.part"));
    std::fs::write(&tmp, &packed).and_then(|_| std::fs::rename(&tmp, &target)).inspect_err(|_| { let _ = std::fs::remove_file(&tmp); })?;
    Ok(target)
}

/// Export settings kept in the OS secure store (password included).
#[derive(serde::Serialize, serde::Deserialize, Clone)]
pub struct ExportConfig { pub dir: String, pub password: String, pub days: u32, pub last: i64 }

pub fn local_time(ms: i64) -> String {
    use chrono::TimeZone;
    match chrono::Local.timestamp_millis_opt(ms).single() {
        Some(t) => t.format("%Y-%m-%d %H:%M").to_string(),
        None => String::new(),
    }
}

pub fn status_value(db: Option<&HistoryDb>, export: Option<&ExportConfig>) -> Value {
    let status = match db {
        None => "请先登录后再使用本地备份".to_string(),
        Some(db) if !db.persistent => "未检测到系统安全存储：聊天备份只保存在内存中，退出后不保留".to_string(),
        Some(db) => {
            let missing = db.pending_missing();
            let base = match db.meta("snapshot-last").and_then(|v| v.parse::<i64>().ok()) {
                Some(t) => format!("本地加密备份 · {}", local_time(t)),
                None => "本地加密备份已开启".to_string(),
            };
            if missing > 0 { format!("{base}；有 {missing} 份服务器已清理的原包等待其他成员补齐") } else { base }
        }
    };
    let export_status = match export {
        Some(e) => format!("每 {} 天导出一次{}", e.days, if e.last > 0 { format!("，上次 {}", local_time(e.last)) } else { String::new() }),
        None => "未启用外部导出".to_string(),
    };
    json!({ "status": status, "exportStatus": export_status, "days": export.map(|e| e.days),
            "packets": db.map(|d| d.packet_count()).unwrap_or(0) })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::history::tests::{memory_db, packet, record};

    #[test]
    fn ycb1_round_trip_and_layout() {
        let packed = encrypt(b"{\"x\":1}", b"correct horse battery").unwrap();
        assert_eq!(&packed[..4], b"YCB1");
        assert_eq!(packed.len(), 4 + 32 + 12 + 7 + 16);
        assert_eq!(&*decrypt(&packed, b"correct horse battery").unwrap(), b"{\"x\":1}");
        assert!(decrypt(&packed, b"wrong password!!").is_err());
        assert!(decrypt(b"YCB0", b"x").is_err());
    }

    /// Cross-check against an independent implementation (Python `cryptography`
    /// + hashlib.scrypt), equivalent to Android's BouncyCastle SCrypt + JCE GCM.
    #[test]
    #[ignore]
    fn ycb1_python_interop() {
        let packed = encrypt(b"interop", b"interop-password").unwrap();
        let path = std::env::temp_dir().join("yc-interop.ycb");
        std::fs::write(&path, &packed).unwrap();
        let script = r#"import sys,hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
b=open(sys.argv[1],'rb').read();k=hashlib.scrypt(b'interop-password',salt=b[4:36],n=32768,r=8,p=1,dklen=32,maxmem=64*1024*1024)
print(AESGCM(k).decrypt(b[36:48],b[48:],b'YiChat-App-Backup-v1;scrypt:N32768:r8:p1;AES256-GCM').decode())"#;
        let out = std::process::Command::new(std::env::var("PYTHON").unwrap_or("python3".into())).arg("-c").arg(script).arg(&path).output().unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "interop", "{}", String::from_utf8_lossy(&out.stderr));
    }

    #[test]
    fn content_and_snapshot() {
        let db = memory_db();
        let (raw, hash) = packet("!r:x", "$e1", "m.room.message", "hi");
        db.save_records(&[record("!r:x", "$e1", 1, 5, &hash)]).unwrap();
        db.accept(&json!({ "event_id": "$e1", "raw": STANDARD.encode(&raw) }), "!r:x").unwrap();
        let v: Value = serde_json::from_slice(&packet_content(&db, "u", "@m:x").unwrap()).unwrap();
        assert_eq!(v["format"], "yichat-app-packets");
        assert_eq!(STANDARD.decode(v["packets"][0]["raw"].as_str().unwrap()).unwrap(), raw);

        let dir = std::env::temp_dir().join(format!("yc-snap-{}", rand::random::<u64>()));
        let key = Zeroizing::new(vec![9u8; 32]);
        let disk = HistoryDb::open(&"b".repeat(64), Some(&dir.join("history.db")), key.clone()).unwrap();
        disk.save_records(&[record("!r:x", "$e1", 1, 5, &hash)]).unwrap();
        snapshot(&disk, &dir.join("history.snapshot.db")).unwrap();
        let copy = HistoryDb::open(&"b".repeat(64), Some(&dir.join("history.snapshot.db")), key).unwrap();
        assert_eq!(copy.conn.lock().unwrap().query_row("SELECT count(*) FROM receipts", [], |r| r.get::<_, i64>(0)).unwrap(), 1);
        let file = export(&disk, "u", "@m:x", &dir, b"export-password").unwrap();
        assert!(file.file_name().unwrap().to_string_lossy().ends_with(".ycb"));
        let _ = std::fs::remove_dir_all(dir);
    }
}
