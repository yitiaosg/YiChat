//! Native new-message notifications: Windows Toast (WinRT) and Linux
//! org.freedesktop.Notifications (libnotify protocol over D-Bus).
//! One notification per conversation, replaced in place. Message text exists
//! only in memory on its way to the OS; it is never logged or stored.
use serde::Deserialize;
use sha2::{Digest, Sha256};

pub const GENERIC_TITLE: &str = "YiChat";
pub const GENERIC_BODY: &str = "你有新消息";
const MAX_LINES: usize = 4;
const MAX_BODY: usize = 400;

#[derive(Deserialize)]
pub struct Message { #[serde(default)] pub sender: String, #[serde(default)] pub body: String }

/// Strip control characters (keep newlines) and cap length.
pub fn clean(text: &str, max: usize) -> String {
    let t: String = text.chars().map(|c| if c == '\n' { ' ' } else { c }).filter(|c| !c.is_control()).collect();
    let t = t.trim();
    if t.chars().count() > max { format!("{}…", t.chars().take(max).collect::<String>()) } else { t.to_string() }
}

/// Body for a preview notification from the verified messages the page returned.
pub fn preview_body(kind: &str, messages: &[Message]) -> Option<String> {
    let lines: Vec<String> = messages.iter().filter(|m| !m.body.trim().is_empty()).map(|m| {
        let body = clean(&m.body, 160);
        if kind == "group" && !m.sender.trim().is_empty() { format!("{}: {body}", clean(&m.sender, 40)) } else { body }
    }).collect();
    if lines.is_empty() { return None; }
    let mut out = Vec::new();
    if lines.len() > MAX_LINES { out.push(format!("{} 条新消息", lines.len())); }
    out.extend(lines[lines.len().saturating_sub(MAX_LINES)..].iter().cloned());
    let body = out.join("\n");
    Some(if body.chars().count() > MAX_BODY { format!("{}…", body.chars().take(MAX_BODY).collect::<String>()) } else { body })
}

/// Stable, short per-conversation tag (Windows tag limit is 64 chars).
pub fn tag(room: &str) -> String {
    crate::shell::hex(&Sha256::digest(room.as_bytes())[..8])
}

pub fn valid_room(room: &str) -> bool { room.starts_with('!') && room.len() <= 255 && !room.chars().any(char::is_control) }
pub fn valid_event(event: &str) -> bool { event.starts_with('$') && event.len() <= 255 && !event.chars().any(char::is_control) }

/// URL Windows passes back when a toast is clicked (protocol activation).
#[cfg_attr(not(windows), allow(dead_code))]
pub fn activation_url(room: &str) -> String {
    let mut url = url::Url::parse(&format!("{}://room", crate::config::OPEN_SCHEME)).unwrap();
    url.query_pairs_mut().append_pair("id", room);
    url.into()
}

/// Room from an activation URL, if it is exactly `yichat-open://room?id=!…`.
pub fn room_from_activation(arg: &str) -> Option<String> {
    let url = url::Url::parse(arg).ok()?;
    if url.scheme() != crate::config::OPEN_SCHEME || url.host_str() != Some("room") || !url.path().is_empty() && url.path() != "/" { return None; }
    let pairs: Vec<_> = url.query_pairs().collect();
    match pairs.as_slice() {
        [(k, v)] if k == "id" && valid_room(v) => Some(v.to_string()),
        _ => None,
    }
}

/// `yichat-open://return` (binding done) or `yichat-open://return?order=<32>`
/// (payment approved/cancelled), produced by the gateway's /app/desktop-return.
/// Returns Some(None) / Some(Some(order)).
pub fn return_from_activation(arg: &str) -> Option<Option<String>> {
    let url = url::Url::parse(arg).ok()?;
    if url.scheme() != crate::config::OPEN_SCHEME || url.host_str() != Some("return") || !(url.path().is_empty() || url.path() == "/") || url.fragment().is_some() { return None; }
    let pairs: Vec<_> = url.query_pairs().collect();
    match pairs.as_slice() {
        [] if url.query().is_none_or(str::is_empty) => Some(None),
        [(k, v)] if k == "order" && v.len() == 32 && v.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') => Some(Some(v.to_string())),
        _ => None,
    }
}

#[cfg(target_os = "linux")]
pub use linux::{cancel, show, available};
#[cfg(windows)]
pub use windows_toast::{cancel, show, available, register};

#[cfg(target_os = "linux")]
mod linux {
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};
    use std::time::Duration;

    /// room tag -> D-Bus notification id (for in-place replacement and closing).
    fn ids() -> &'static Mutex<HashMap<String, (u32, bool)>> {
        static IDS: OnceLock<Mutex<HashMap<String, (u32, bool)>>> = OnceLock::new();
        IDS.get_or_init(Mutex::default)
    }

    fn escape(text: &str) -> String {
        // Servers with body-markup interpret a small HTML subset.
        text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
    }

    pub fn available() -> bool { notify_rust::get_server_information().is_ok() }

    /// D-Bus handles are not Send, so each notification is shown and waited
    /// on from its own thread. One listener per notification id.
    pub fn show(room: &str, title: &str, body: &str, on_click: impl FnOnce() + Send + 'static) {
        let (tag, title, body) = (super::tag(room), title.to_string(), escape(body));
        std::thread::spawn(move || {
            let mut map = ids().lock().unwrap();
            let existing = map.get(&tag).copied();
            let mut n = notify_rust::Notification::new();
            n.appname("YiChat").summary(&title).body(&body).icon("yichat-desktop")
                .action("default", "打开").hint(notify_rust::Hint::Category("im.received".into()));
            if let Some((id, _)) = existing { n.id(id); }
            let Ok(handle) = n.show() else { return };
            let id = handle.id();
            let listening = existing.is_some_and(|(old, l)| old == id && l);
            map.insert(tag.clone(), (id, true));
            drop(map);
            if listening { return; } // The first thread for this id handles the click.
            let mut clicked = false;
            handle.wait_for_action(|action| clicked = action == "default");
            let mut map = ids().lock().unwrap();
            if map.get(&tag).is_some_and(|(i, _)| *i == id) { map.remove(&tag); }
            drop(map);
            if clicked { on_click(); }
        });
    }

    pub fn cancel(room: Option<&str>) {
        let targets: Vec<u32> = {
            let mut map = ids().lock().unwrap();
            match room {
                Some(r) => map.remove(&super::tag(r)).map(|(id, _)| vec![id]).unwrap_or_default(),
                None => map.drain().map(|(_, (id, _))| id).collect(),
            }
        };
        if targets.is_empty() { return; }
        std::thread::spawn(move || {
            let Ok(c) = dbus::blocking::Connection::new_session() else { return };
            let p = c.with_proxy("org.freedesktop.Notifications", "/org/freedesktop/Notifications", Duration::from_secs(2));
            for id in targets {
                let _: Result<(), _> = p.method_call("org.freedesktop.Notifications", "CloseNotification", (id,));
            }
        });
    }
}

#[cfg(windows)]
mod windows_toast {
    use crate::config::{AUMID, OPEN_SCHEME};
    // OPEN_SCHEME is used in register().
    use windows::core::HSTRING;
    use windows::Data::Xml::Dom::XmlDocument;
    use windows::UI::Notifications::{ToastNotification, ToastNotificationManager};

    const GROUP: &str = "rooms";

    fn xml_escape(text: &str) -> String {
        text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&apos;")
    }

    /// Per-user registration (no admin): AppUserModelID for toasts and the
    /// yichat-open: protocol used for toast activation. The installer writes
    /// the same keys; doing it at startup also covers portable/dev runs.
    pub fn register() {
        use winreg::{enums::HKEY_CURRENT_USER, RegKey};
        let Ok(exe) = std::env::current_exe() else { return };
        let exe = exe.display().to_string();
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        if let Ok((k, _)) = hkcu.create_subkey(format!(r"Software\Classes\AppUserModelId\{AUMID}")) {
            let _ = k.set_value("DisplayName", &"YiChat");
        }
        if let Ok((k, _)) = hkcu.create_subkey(format!(r"Software\Classes\{OPEN_SCHEME}")) {
            let _ = k.set_value("", &"URL:YiChat");
            let _ = k.set_value("URL Protocol", &"");
            if let Ok((icon, _)) = k.create_subkey("DefaultIcon") { let _ = icon.set_value("", &format!("\"{exe}\",0")); }
            if let Ok((cmd, _)) = k.create_subkey(r"shell\open\command") { let _ = cmd.set_value("", &format!("\"{exe}\" \"%1\"")); }
        }
        unsafe { let _ = windows::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID(&HSTRING::from(AUMID)); }
    }

    pub fn available() -> bool { true }

    /// Clicking opens `yichat-open://room?id=…`; single-instance forwards it to
    /// the running app (or the app starts with it), so `on_click` is unused here.
    pub fn show(room: &str, title: &str, body: &str, _on_click: impl FnOnce() + Send + 'static) {
        let tag = &super::tag(room);
        let launch = xml_escape(&super::activation_url(room));
        let xml = format!(
            "<toast activationType=\"protocol\" launch=\"{launch}\"><visual><binding template=\"ToastGeneric\"><text>{}</text><text>{}</text></binding></visual></toast>",
            xml_escape(title), xml_escape(body));
        let result = (|| -> windows::core::Result<()> {
            let doc = XmlDocument::new()?;
            doc.LoadXml(&HSTRING::from(xml))?;
            let toast = ToastNotification::CreateToastNotification(&doc)?;
            toast.SetTag(&HSTRING::from(tag))?;
            toast.SetGroup(&HSTRING::from(GROUP))?;
            ToastNotificationManager::CreateToastNotifierWithId(&HSTRING::from(AUMID))?.Show(&toast)
        })();
        if result.is_err() { log::info!("toast unavailable"); }
    }

    pub fn cancel(room: Option<&str>) {
        let _ = (|| -> windows::core::Result<()> {
            let history = ToastNotificationManager::History()?;
            match room {
                Some(r) => history.RemoveGroupedTagWithId(&HSTRING::from(super::tag(r)), &HSTRING::from(GROUP), &HSTRING::from(AUMID)),
                None => history.ClearWithId(&HSTRING::from(AUMID)),
            }
        })();
    }

}

#[cfg(test)]
mod tests {
    use super::*;

    fn m(sender: &str, body: &str) -> Message { Message { sender: sender.into(), body: body.into() } }

    #[test]
    fn preview_formatting() {
        assert_eq!(preview_body("private", &[m("A", "hi")]).unwrap(), "hi");
        assert_eq!(preview_body("group", &[m("A", "hi"), m("B", "yo")]).unwrap(), "A: hi\nB: yo");
        let many: Vec<_> = (0..6).map(|i| m("A", &format!("m{i}"))).collect();
        assert_eq!(preview_body("private", &many).unwrap(), "6 条新消息\nm2\nm3\nm4\nm5");
        assert!(preview_body("private", &[m("A", "  ")]).is_none());
        assert_eq!(preview_body("private", &[m("A", "a\u{0007}b\nc")]).unwrap(), "ab c");
        assert!(preview_body("private", &[m("A", &"x".repeat(1000))]).unwrap().chars().count() <= 162);
    }

    #[test]
    fn activation_round_trip_and_rejections() {
        let room = "!abc:chat.yitiao.org";
        assert_eq!(room_from_activation(&activation_url(room)).as_deref(), Some(room));
        for bad in ["yichat-open://room?id=abc", "yichat-open://room?id=!a&x=1", "yichat-open://other?id=!a",
                    "https://chat.yitiao.org/?id=!a", "yichat-open://room/x?id=!a", "yichat-open://room", "--autostart"] {
            assert!(room_from_activation(bad).is_none(), "{bad}");
        }
        assert_eq!(tag(room).len(), 16);
        let order = "A".repeat(31) + "_";
        assert_eq!(return_from_activation("yichat-open://return"), Some(None));
        assert_eq!(return_from_activation(&format!("yichat-open://return?order={order}")), Some(Some(order.clone())));
        for bad in [format!("yichat-open://return?order={order}&x=1"), format!("yichat-open://return?order={order}x"),
                    "yichat-open://return?order=%3Cscript%3E".into(), format!("yichat-open://room?order={order}"),
                    format!("https://chat.yitiao.org/app/desktop-return?order={order}"), "yichat-open://return?x=1".into()] {
            assert!(return_from_activation(&bad).is_none(), "{bad}");
        }
        assert!(valid_event("$e") && !valid_event("e") && !valid_room("!a\n"));
    }
}
