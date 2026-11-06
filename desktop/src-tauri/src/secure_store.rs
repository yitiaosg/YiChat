//! Secrets live only in the OS secure store: Windows Credential Manager
//! (DPAPI-protected) or the freedesktop Secret Service on Linux. When no
//! secure store is reachable, secrets stay in memory for this run only and
//! nothing is persisted (the webview also runs ephemerally).
use crate::config::SERVICE;
use keyring::{Entry, Error};
use std::collections::HashMap;
use std::sync::Mutex;

pub struct SecureStore {
    persistent: bool,
    reason: Option<String>,
    memory: Mutex<HashMap<String, String>>,
}

const KEYS: [&str; 3] = ["session", "device-resume", "pending-logout"];

impl SecureStore {
    /// Blocking: probes the platform store once at startup.
    pub fn open() -> Self {
        let probe = Entry::new(SERVICE, "probe").and_then(|e| match e.get_password() {
            Ok(_) | Err(Error::NoEntry) => Ok(()),
            Err(e) => Err(e),
        });
        match probe {
            Ok(()) => Self { persistent: true, reason: None, memory: Mutex::default() },
            Err(e) => Self { persistent: false, reason: Some(describe(&e)), memory: Mutex::default() },
        }
    }
    pub fn persistent(&self) -> bool { self.persistent }
    pub fn reason(&self) -> Option<&str> { self.reason.as_deref() }

    pub fn get(&self, key: &str) -> Option<String> {
        debug_assert!(KEYS.contains(&key));
        if !self.persistent { return self.memory.lock().unwrap().get(key).cloned(); }
        Entry::new(SERVICE, key).ok()?.get_password().ok()
    }
    pub fn put(&self, key: &str, value: &str) -> Result<(), String> {
        debug_assert!(KEYS.contains(&key));
        if !self.persistent { self.memory.lock().unwrap().insert(key.into(), value.into()); return Ok(()); }
        Entry::new(SERVICE, key).and_then(|e| e.set_password(value)).map_err(|e| describe(&e))
    }
    pub fn delete(&self, key: &str) {
        debug_assert!(KEYS.contains(&key));
        if !self.persistent { self.memory.lock().unwrap().remove(key); return; }
        if let Ok(e) = Entry::new(SERVICE, key) { let _ = e.delete_credential(); }
    }
}

/// Category only; never includes secret values.
fn describe(e: &Error) -> String {
    match e {
        Error::NoStorageAccess(_) => "系统密钥服务已锁定或拒绝访问".into(),
        Error::PlatformFailure(_) => if cfg!(target_os = "linux") {
            "未检测到可用的 Secret Service（如 GNOME Keyring 或 KWallet）".into()
        } else { "系统凭据管理器不可用".into() },
        _ => "系统安全存储不可用".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Needs a running, unlocked Secret Service (or Windows): `cargo test -- --ignored`.
    #[test]
    #[ignore]
    fn round_trip_in_os_store() {
        let store = SecureStore::open();
        assert!(store.persistent(), "{:?}", store.reason());
        store.put("pending-logout", "value-1").unwrap();
        assert_eq!(store.get("pending-logout").as_deref(), Some("value-1"));
        store.delete("pending-logout");
        assert_eq!(store.get("pending-logout"), None);
    }

    #[test]
    fn memory_fallback_does_not_touch_os_store() {
        let store = SecureStore { persistent: false, reason: Some("x".into()), memory: Mutex::default() };
        store.put("session", "s").unwrap();
        assert_eq!(store.get("session").as_deref(), Some("s"));
        store.delete("session");
        assert_eq!(store.get("session"), None);
    }
}
