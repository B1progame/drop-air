//! Settings, shared files, text items, session keys and pairing requests.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::net::{IpAddr, Ipv4Addr, UdpSocket};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const STAGING_PREFIX: &str = ".dropair-";
const SESSION_TTL: u64 = 300;
const SESSION_GRACE: u64 = 120;
const CLIENT_TTL: u64 = 30;
const PAIR_TTL: u64 = 120;
const MAX_TEXT_ITEMS: usize = 40;
const MAX_TEXT_CHARS: usize = 20_000;

/// A poisoned lock still holds usable data; never take the whole server down for it.
pub fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

pub fn random_hex(bytes: usize) -> String {
    let mut buffer = vec![0u8; bytes];
    getrandom::getrandom(&mut buffer).expect("the system random source failed");
    buffer.iter().map(|b| format!("{b:02x}")).collect()
}

fn env_number(name: &str, fallback: u64) -> u64 {
    std::env::var(name).ok().and_then(|v| v.trim().parse().ok()).unwrap_or(fallback)
}

pub fn clean_device_name(value: &str) -> String {
    let name: String = value.chars().filter(|c| !c.is_control()).collect();
    let name: String = name.trim().chars().take(32).collect();
    if name.is_empty() { "Drop Air device".into() } else { name }
}

/// Turn an uploaded name into a plain Windows-safe file name, keeping Unicode.
pub fn safe_file_name(raw: &str) -> String {
    let last = raw.rsplit(['/', '\\']).next().unwrap_or("");
    let cleaned: String = last
        .chars()
        .map(|c| if c.is_control() || "<>:\"|?*".contains(c) { '_' } else { c })
        .collect();
    let trimmed = cleaned.trim_start_matches(['.', ' ']).trim_end_matches(['.', ' ']);
    let mut name: String = trimmed.chars().take(180).collect();
    let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
    let reserved = matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || (stem.len() == 4 && (stem.starts_with("COM") || stem.starts_with("LPT")) && stem.ends_with(|c: char| c.is_ascii_digit()));
    if reserved {
        name.insert(0, '_');
    }
    if name.is_empty() { format!("file_{}", now()) } else { name }
}

/// `report.pdf`, then `report_1.pdf`, `report_2.pdf`, ...
pub fn numbered_name(name: &str, index: u32) -> String {
    if index == 0 {
        return name.to_string();
    }
    match name.rfind('.') {
        Some(dot) if dot > 0 => format!("{}_{index}{}", &name[..dot], &name[dot..]),
        _ => format!("{name}_{index}"),
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub access_code: String,
    pub auto_cleanup_minutes: u64,
    pub auto_cleanup_days: u64,
    pub auto_cleanup_max_files: u64,
    pub max_upload_gb: f64,
    pub launch_browser_on_start: bool,
    pub device_name: String,
    pub discoverable: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            access_code: std::env::var("DROP_AIR_CODE").unwrap_or_default().trim().to_string(),
            auto_cleanup_minutes: env_number("DROP_AIR_AUTO_CLEANUP_MINUTES", 10),
            auto_cleanup_days: env_number("DROP_AIR_AUTO_CLEANUP_DAYS", 0),
            auto_cleanup_max_files: env_number("DROP_AIR_AUTO_CLEANUP_MAX_FILES", 0),
            max_upload_gb: std::env::var("DROP_AIR_MAX_UPLOAD_GB").ok().and_then(|v| v.parse().ok()).unwrap_or(10.0),
            launch_browser_on_start: true,
            device_name: "Drop Air device".into(),
            discoverable: false,
        }
    }
}

impl Settings {
    pub fn limit_bytes(&self) -> u64 {
        (self.max_upload_gb * 1024.0 * 1024.0 * 1024.0) as u64
    }

    pub fn limit_label(&self) -> String {
        if self.max_upload_gb >= 1.0 {
            format!("{} GB", self.max_upload_gb)
        } else {
            format!("{} MB", (self.max_upload_gb * 1024.0).round())
        }
    }
}

#[derive(Clone, Serialize, Deserialize)]
pub struct TextItem {
    #[serde(deserialize_with = "id_as_string")]
    pub id: String,
    pub text: String,
    pub created: u64,
}

// Older Python versions wrote numeric ids.
fn id_as_string<'de, D: serde::Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    Ok(match Value::deserialize(d)? {
        Value::String(s) => s,
        other => other.to_string(),
    })
}

#[derive(Clone, Serialize)]
pub struct FileEntry {
    pub name: String,
    pub size: u64,
    pub modified: u64,
    pub to: String,
    #[serde(skip)]
    pub from: String,
}

#[derive(Serialize, Deserialize)]
struct SessionState {
    key: String,
    expires_at: u64,
    previous: HashMap<String, u64>,
}

pub struct Client {
    pub name: String,
    pub seen: u64,
}

#[derive(Clone, Serialize)]
pub struct PairRequest {
    pub id: String,
    pub device_id: String,
    pub device_name: String,
    pub address: String,
    pub created_at: u64,
    pub expires_at: u64,
    pub decision: &'static str,
}

#[derive(Clone, Serialize)]
pub struct Peer {
    pub device_id: String,
    pub device_name: String,
    pub address: String,
    pub port: u16,
    pub transports: Vec<&'static str>,
    #[serde(skip)]
    pub fullname: String,
}

#[derive(Clone, Serialize, Default)]
pub struct UpdateStatus {
    pub running: bool,
    pub percent: u8,
    pub message: String,
    pub error: String,
}

pub struct Store {
    pub data_dir: PathBuf,
    pub upload_dir: PathBuf,
    pub device_id: String,
    pub recovery_secret: String,
    pub settings: Mutex<Settings>,
    pub texts: Mutex<Vec<TextItem>>,
    session: Mutex<SessionState>,
    pub clients: Mutex<HashMap<String, Client>>,
    /// file name -> (recipient client id, sender client id) for private sends
    pub routes: Mutex<HashMap<String, (String, String)>>,
    pub pairs: Mutex<Vec<PairRequest>>,
    pub pair_attempts: Mutex<HashMap<String, Vec<Instant>>>,
    pub peers: Mutex<HashMap<String, Peer>>,
    pub discovery_status: Mutex<String>,
    pub rediscover: Mutex<bool>,
    /// names currently being published, hidden from listings until complete
    pub publishing: Mutex<HashSet<String>>,
    pub update: Mutex<UpdateStatus>,
    pub port: std::sync::atomic::AtomicU16,
}

pub fn data_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("DROP_AIR_DATA_DIR").filter(|v| !v.is_empty()) {
        return dir.into();
    }
    let app_dir = std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf));
    if let Some(dir) = app_dir.filter(|d| d.join("portable.mode").is_file()) {
        return dir.join("data");
    }
    dirs::data_local_dir().unwrap_or_else(std::env::temp_dir).join("DropAir")
}

fn read_or_create(path: &Path, valid: impl Fn(&str) -> bool, make: impl Fn() -> String) -> String {
    if let Ok(value) = std::fs::read_to_string(path) {
        let value = value.trim().to_string();
        if valid(&value) {
            return value;
        }
    }
    let value = make();
    let _ = std::fs::write(path, &value);
    value
}

fn write_atomic(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, data)?;
    std::fs::rename(&temporary, path)
}

impl Store {
    pub fn open(data_dir: PathBuf) -> std::io::Result<Store> {
        let upload_dir = data_dir.join("uploads");
        std::fs::create_dir_all(&upload_dir)?;
        let device_id = read_or_create(&data_dir.join("device-id"), |v| v.len() == 32 && v.bytes().all(|b| b.is_ascii_hexdigit()), || random_hex(16));
        let recovery_secret = read_or_create(&data_dir.join("session-recovery.secret"), |v| v.len() >= 48, || random_hex(32));
        let mut settings: Settings = std::fs::read(data_dir.join("settings.json"))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
        settings.device_name = clean_device_name(&settings.device_name);
        let texts = std::fs::read(data_dir.join("text_items.json"))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
        let session = std::fs::read(data_dir.join("session-state.json"))
            .ok()
            .and_then(|b| serde_json::from_slice::<SessionState>(&b).ok())
            .filter(|s| s.key.len() == 32)
            .unwrap_or_else(|| SessionState { key: random_hex(16), expires_at: now() + SESSION_TTL, previous: HashMap::new() });
        let store = Store {
            data_dir,
            upload_dir,
            device_id,
            recovery_secret,
            settings: Mutex::new(settings),
            texts: Mutex::new(texts),
            session: Mutex::new(session),
            clients: Default::default(),
            routes: Default::default(),
            pairs: Default::default(),
            pair_attempts: Default::default(),
            peers: Default::default(),
            discovery_status: Mutex::new("Searching local Wi-Fi".into()),
            rediscover: Mutex::new(false),
            publishing: Default::default(),
            update: Default::default(),
            port: Default::default(),
        };
        store.persist_session(&lock(&store.session));
        Ok(store)
    }

    pub fn port(&self) -> u16 {
        self.port.load(std::sync::atomic::Ordering::Relaxed)
    }

    pub fn settings(&self) -> Settings {
        lock(&self.settings).clone()
    }

    pub fn save_settings(&self, settings: Settings) -> std::io::Result<()> {
        let data = serde_json::to_vec_pretty(&settings)?;
        write_atomic(&self.data_dir.join("settings.json"), &data)?;
        *lock(&self.settings) = settings;
        Ok(())
    }

    fn persist_session(&self, session: &SessionState) {
        if let Ok(data) = serde_json::to_vec(session) {
            let _ = write_atomic(&self.data_dir.join("session-state.json"), &data);
        }
    }

    /// Current key and seconds left, rotating it when it has expired.
    pub fn session(&self) -> (String, u64) {
        let mut session = lock(&self.session);
        let time = now();
        session.previous.retain(|_, expires| *expires > time);
        if time >= session.expires_at {
            let old = std::mem::replace(&mut session.key, random_hex(16));
            session.previous.insert(old, time + SESSION_GRACE);
            session.expires_at = time + SESSION_TTL;
            self.persist_session(&session);
        }
        (session.key.clone(), session.expires_at.saturating_sub(time))
    }

    pub fn is_valid_key(&self, supplied: &str) -> bool {
        let (current, _) = self.session();
        supplied == current || lock(&self.session).previous.get(supplied).is_some_and(|e| *e > now())
    }

    pub fn public_url(&self) -> String {
        let mut url = format!("http://{}:{}/?k={}", lan_ip(), self.port(), self.session().0);
        let code = self.settings().access_code;
        if !code.is_empty() {
            url.push_str("&code=");
            url.push_str(&encode(&code));
        }
        url + "&view=transfers"
    }

    pub fn local_url(&self) -> String {
        format!("http://127.0.0.1:{}/?desktop=1", self.port())
    }

    pub fn files(&self) -> Vec<FileEntry> {
        let publishing = lock(&self.publishing).clone();
        let routes = lock(&self.routes).clone();
        let Ok(entries) = std::fs::read_dir(&self.upload_dir) else { return Vec::new() };
        let mut files: Vec<(SystemTime, FileEntry)> = entries
            .flatten()
            .filter_map(|entry| {
                let name = entry.file_name().into_string().ok()?;
                if name.starts_with(STAGING_PREFIX) || publishing.contains(&name) {
                    return None;
                }
                let meta = entry.metadata().ok().filter(|m| m.is_file())?;
                let modified = meta.modified().ok()?;
                let (to, from) = routes.get(&name).cloned().unwrap_or_default();
                let seconds = modified.duration_since(UNIX_EPOCH).ok()?.as_secs();
                Some((modified, FileEntry { name, size: meta.len(), modified: seconds, to, from }))
            })
            .collect();
        files.sort_by(|a, b| b.0.cmp(&a.0));
        files.into_iter().map(|(_, f)| f).collect()
    }

    /// Path of an existing shared file; rejects anything that is not a plain file name.
    pub fn file_path(&self, name: &str) -> Option<PathBuf> {
        if name.is_empty() || name.contains(['/', '\\', ':']) || name == ".." || name.starts_with(STAGING_PREFIX) {
            return None;
        }
        let path = self.upload_dir.join(name);
        std::fs::symlink_metadata(&path).ok().filter(|m| m.is_file())?;
        Some(path)
    }

    pub fn delete_file(&self, name: &str) -> bool {
        let removed = self.file_path(name).is_some_and(|path| std::fs::remove_file(path).is_ok());
        if removed {
            lock(&self.routes).remove(name);
        }
        removed
    }

    pub fn clear_uploads(&self) -> usize {
        let Ok(entries) = std::fs::read_dir(&self.upload_dir) else { return 0 };
        lock(&self.routes).clear();
        entries.flatten().filter(|e| std::fs::remove_file(e.path()).is_ok()).count()
    }

    fn save_texts(&self, texts: &[TextItem]) {
        if let Ok(data) = serde_json::to_vec(texts) {
            let _ = write_atomic(&self.data_dir.join("text_items.json"), &data);
        }
    }

    pub fn add_text(&self, text: &str) -> Option<TextItem> {
        let text: String = text.chars().take(MAX_TEXT_CHARS).collect();
        if text.trim().is_empty() {
            return None;
        }
        let item = TextItem { id: random_hex(8), text, created: now() };
        let mut texts = lock(&self.texts);
        texts.insert(0, item.clone());
        texts.truncate(MAX_TEXT_ITEMS);
        self.save_texts(&texts);
        Some(item)
    }

    pub fn retain_texts(&self, keep: impl Fn(&TextItem) -> bool) -> bool {
        let mut texts = lock(&self.texts);
        let before = texts.len();
        texts.retain(|t| keep(t));
        let changed = texts.len() != before;
        if changed {
            self.save_texts(&texts);
        }
        changed
    }

    pub fn cleanup(&self) {
        let settings = self.settings();
        let time = now();
        let ages = [settings.auto_cleanup_minutes * 60, settings.auto_cleanup_days * 86_400];
        let max_age = ages.into_iter().filter(|a| *a > 0).min();
        let mut kept = 0;
        for file in self.files() {
            let expired = max_age.is_some_and(|age| time.saturating_sub(file.modified) > age);
            let overflow = settings.auto_cleanup_max_files > 0 && kept >= settings.auto_cleanup_max_files;
            if expired || overflow {
                self.delete_file(&file.name);
            } else {
                kept += 1;
            }
        }
        let ttl = text_ttl_minutes() * 60;
        if ttl > 0 {
            self.retain_texts(|t| t.created + ttl >= time);
        }
        lock(&self.clients).retain(|_, c| c.seen + CLIENT_TTL >= time);
    }

    pub fn clients_json(&self) -> Vec<Value> {
        let time = now();
        let mut clients: Vec<(String, String)> = lock(&self.clients)
            .iter()
            .filter(|(_, c)| c.seen + CLIENT_TTL >= time)
            .map(|(id, c)| (id.clone(), c.name.clone()))
            .collect();
        clients.sort();
        clients.into_iter().map(|(id, name)| json!({ "id": id, "name": name })).collect()
    }

    pub fn create_pair(&self, address: IpAddr, device_id: &str, name: &str) -> Result<PairRequest, (u16, &'static str)> {
        let valid_id = (16..=32).contains(&device_id.len()) && device_id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'));
        if !valid_id {
            return Err((400, "invalid device"));
        }
        if !matches!(address, IpAddr::V4(v4) if v4.is_private()) {
            return Err((400, "invalid network address"));
        }
        let address = address.to_string();
        let mut attempts = lock(&self.pair_attempts);
        attempts.retain(|_, list| {
            list.retain(|t| t.elapsed().as_secs() < 60);
            !list.is_empty()
        });
        let mine = attempts.entry(address.clone()).or_default();
        if mine.len() >= 4 {
            return Err((429, "too many recent connection requests"));
        }
        let mut pairs = lock(&self.pairs);
        let time = now();
        pairs.retain(|p| p.expires_at > time);
        if pairs.len() >= 24 {
            return Err((429, "the host has too many pending requests"));
        }
        if pairs.iter().any(|p| p.address == address && p.device_id == device_id && p.decision == "pending") {
            return Err((429, "a request from this device is already waiting for approval"));
        }
        mine.push(Instant::now());
        let request = PairRequest {
            id: random_hex(24),
            device_id: device_id.into(),
            device_name: clean_device_name(name),
            address,
            created_at: time,
            expires_at: time + PAIR_TTL,
            decision: "pending",
        };
        pairs.push(request.clone());
        Ok(request)
    }

    pub fn pending_pairs(&self) -> Vec<PairRequest> {
        let time = now();
        lock(&self.pairs).iter().filter(|p| p.expires_at > time && p.decision == "pending").cloned().collect()
    }
}

pub fn text_ttl_minutes() -> u64 {
    env_number("DROP_AIR_TEXT_TTL_MINUTES", 10)
}

pub fn encode(value: &str) -> String {
    percent_encoding::utf8_percent_encode(value, percent_encoding::NON_ALPHANUMERIC).to_string()
}

/// The address phones on the same Wi-Fi can reach, skipping VPN and virtual adapters.
pub fn lan_ip() -> Ipv4Addr {
    const SKIP: [&str; 24] = [
        "vpn", "wireguard", "tunnel", "tun", "tap", "wintun", "tailscale", "zerotier", "hamachi", "nordlynx", "mullvad",
        "surfshark", "proton", "warp", "vmware", "virtualbox", "hyper-v", "vethernet", "default switch", "wsl", "docker",
        "loopback", "bluetooth", "wi-fi direct",
    ];
    let routed = UdpSocket::bind("0.0.0.0:0")
        .and_then(|s| {
            s.connect("192.0.2.1:9")?;
            s.local_addr()
        })
        .ok()
        .and_then(|a| match a.ip() {
            IpAddr::V4(v4) => Some(v4),
            _ => None,
        });
    let candidates: Vec<(Ipv4Addr, bool)> = if_addrs::get_if_addrs()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|interface| {
            let IpAddr::V4(ip) = interface.ip() else { return None };
            let label = interface.name.to_lowercase();
            if !ip.is_private() || SKIP.iter().any(|marker| label.contains(marker)) {
                return None;
            }
            let wifi = ["wi-fi", "wifi", "wlan", "wireless"].iter().any(|m| label.contains(m));
            Some((ip, wifi))
        })
        .collect();
    if let Some(ip) = routed.filter(|ip| candidates.iter().any(|c| c.0 == *ip)) {
        return ip;
    }
    candidates
        .iter()
        .find(|c| c.1)
        .or_else(|| candidates.first())
        .map(|c| c.0)
        .or_else(|| routed.filter(Ipv4Addr::is_private))
        .unwrap_or(Ipv4Addr::LOCALHOST)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_names_cannot_escape_the_upload_folder() {
        assert_eq!(safe_file_name("..\\..\\windows\\evil.exe"), "evil.exe");
        assert_eq!(safe_file_name("../.dropair-x"), "dropair-x");
        assert_eq!(safe_file_name("a:b?.txt"), "a_b_.txt");
        assert_eq!(safe_file_name("CON.txt"), "_CON.txt");
        assert_eq!(safe_file_name("Übersicht 2026.pdf"), "Übersicht 2026.pdf");
        assert!(safe_file_name("..").starts_with("file_"));
    }

    #[test]
    fn duplicate_names_get_numbers() {
        assert_eq!(numbered_name("a.tar.gz", 1), "a.tar_1.gz");
        assert_eq!(numbered_name("noext", 2), "noext_2");
    }
}
