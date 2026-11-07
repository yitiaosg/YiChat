//! Non-secret preferences (app config dir, settings.json). Never holds
//! tokens, message text or keys.
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Mutex;

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Values {
    /// New-message notifications (web settings toggle).
    pub notifications: bool,
    /// Show sender/text preview instead of only "你有新消息" (tray toggle).
    pub preview: bool,
    /// The one-time "still running in the tray" hint was shown.
    pub tray_hint_shown: bool,
}

impl Default for Values {
    fn default() -> Self { Self { notifications: true, preview: true, tray_hint_shown: false } }
}

pub struct Settings { path: PathBuf, values: Mutex<Values> }

impl Settings {
    pub fn load(path: PathBuf) -> Self {
        let values = std::fs::read(&path).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
        Self { path, values: Mutex::new(values) }
    }
    pub fn get(&self) -> Values { self.values.lock().unwrap().clone() }
    pub fn update(&self, change: impl FnOnce(&mut Values)) {
        let mut v = self.values.lock().unwrap();
        change(&mut v);
        if let Some(dir) = self.path.parent() { let _ = std::fs::create_dir_all(dir); }
        let tmp = self.path.with_extension("tmp");
        if std::fs::write(&tmp, serde_json::to_vec_pretty(&*v).unwrap()).is_ok() { let _ = std::fs::rename(&tmp, &self.path); }
    }
}
