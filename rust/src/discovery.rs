//! mDNS discovery, compatible with the Python edition. No keys or codes are announced.

use crate::state::{clean_device_name, lan_ip, lock, Peer, Store};
use mdns_sd::{ServiceDaemon, ServiceEvent, ServiceInfo};
use std::net::IpAddr;
use std::sync::Arc;
use std::time::Duration;

const SERVICE: &str = "_dropair._tcp.local.";

pub fn start(store: Arc<Store>) {
    std::thread::spawn(move || loop {
        if let Err(error) = run(&store) {
            *lock(&store.discovery_status) = format!("Wi-Fi discovery unavailable: {error}");
            std::thread::sleep(Duration::from_secs(5));
        }
    });
}

/// Browses and advertises until a rediscover is requested or the network changes.
fn run(store: &Store) -> Result<(), mdns_sd::Error> {
    let daemon = ServiceDaemon::new()?;
    let events = daemon.browse(SERVICE)?;
    let address = lan_ip();
    let mut registered: Option<(String, String)> = None;
    *lock(&store.discovery_status) = "Searching local Wi-Fi".into();
    lock(&store.peers).clear();
    let mut ticks = 0u32;
    loop {
        while let Ok(event) = events.try_recv() {
            handle(store, event);
        }
        let settings = store.settings();
        let wanted = settings.discoverable.then(|| clean_device_name(&settings.device_name));
        if wanted.as_deref() != registered.as_ref().map(|r| r.0.as_str()) {
            if let Some((_, fullname)) = registered.take() {
                let _ = daemon.unregister(&fullname);
            }
            if let Some(name) = wanted {
                let properties = [("protocol", "1"), ("id", store.device_id.as_str()), ("name", name.as_str())];
                let host = format!("dropair-{}.local.", &store.device_id[..8]);
                let info = ServiceInfo::new(SERVICE, &store.device_id, &host, IpAddr::V4(address), store.port(), &properties[..])?;
                let fullname = info.get_fullname().to_string();
                daemon.register(info)?;
                registered = Some((name, fullname));
            }
        }
        std::thread::sleep(Duration::from_secs(1));
        ticks += 1;
        let changed = ticks % 10 == 0 && lan_ip() != address;
        if std::mem::take(&mut *lock(&store.rediscover)) || changed {
            if let Some((_, fullname)) = registered {
                let _ = daemon.unregister(&fullname);
            }
            let _ = daemon.shutdown();
            return Ok(());
        }
    }
}

fn handle(store: &Store, event: ServiceEvent) {
    match event {
        ServiceEvent::ServiceResolved(info) => {
            let property = |key| info.get_property_val_str(key).unwrap_or("");
            let device_id = property("id").to_lowercase();
            let valid = property("protocol") == "1"
                && (16..=32).contains(&device_id.len())
                && device_id.bytes().all(|b| b.is_ascii_hexdigit())
                && device_id != store.device_id
                && info.get_port() >= 1024;
            let address = info.get_addresses().iter().find(|a| matches!(a, IpAddr::V4(v4) if v4.is_private()));
            if let (true, Some(address)) = (valid, address) {
                let peer = Peer {
                    device_id: device_id.clone(),
                    device_name: clean_device_name(property("name")),
                    address: address.to_string(),
                    port: info.get_port(),
                    transports: vec!["Wi-Fi"],
                    fullname: info.get_fullname().to_string(),
                };
                lock(&store.peers).insert(device_id, peer);
            }
        }
        ServiceEvent::ServiceRemoved(_, fullname) => lock(&store.peers).retain(|_, p| p.fullname != fullname),
        _ => {}
    }
}
