//! Updates from GitHub releases. Only 2.x and newer are Rust builds.

use crate::state::{lock, Store, UpdateStatus, VERSION};
use crate::Ui;
use serde_json::Value;
use std::io::{Read, Write};
use std::sync::Arc;

const RELEASES: &str = "https://api.github.com/repos/B1progame/drop-air/releases?per_page=30";

pub struct Release {
    pub version: String,
    url: String,
    name: String,
    size: u64,
}

fn parse_version(value: &str) -> Vec<u64> {
    value.trim_start_matches('v').split('.').map(|part| part.parse().unwrap_or(0)).collect()
}

fn newest(releases: &Value, current: &str) -> Option<Release> {
    releases
        .as_array()?
        .iter()
        .filter(|r| r["draft"] != true && r["prerelease"] != true)
        .filter_map(|r| {
            let version = r["tag_name"].as_str()?.trim_start_matches('v').to_string();
            let asset = r["assets"].as_array()?.iter().find(|a| {
                a["name"].as_str().is_some_and(|n| n.to_lowercase().ends_with(".exe") && n.to_lowercase().contains("setup"))
            })?;
            let url = asset["browser_download_url"].as_str()?.to_string();
            if !url.starts_with("https://github.com/B1progame/drop-air/") {
                return None;
            }
            let name = asset["name"].as_str()?.rsplit(['/', '\\']).next()?.to_string();
            Some(Release { version, url, name, size: asset["size"].as_u64().unwrap_or(0) })
        })
        .filter(|r| parse_version(&r.version) > parse_version(current) && parse_version(&r.version)[0] >= 2)
        .max_by_key(|r| parse_version(&r.version))
}

pub fn check() -> Result<Option<Release>, String> {
    let releases: Value = ureq::get(RELEASES)
        .set("User-Agent", "DropAir")
        .set("Accept", "application/vnd.github+json")
        .timeout(std::time::Duration::from_secs(15))
        .call()
        .map_err(|e| e.to_string())?
        .into_json()
        .map_err(|e| e.to_string())?;
    Ok(newest(&releases, VERSION))
}

fn download_and_run(store: &Store) -> Result<(), String> {
    let release = check()?.ok_or("No update available.")?;
    let folder = std::env::temp_dir().join("drop-air-update");
    std::fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
    let target = folder.join(&release.name);
    let response = ureq::get(&release.url).set("User-Agent", "DropAir").call().map_err(|e| e.to_string())?;
    let mut reader = response.into_reader();
    let mut file = std::fs::File::create(&target).map_err(|e| e.to_string())?;
    let mut buffer = vec![0u8; 256 * 1024];
    let mut done = 0u64;
    loop {
        let read = reader.read(&mut buffer).map_err(|e| e.to_string())?;
        if read == 0 {
            break;
        }
        file.write_all(&buffer[..read]).map_err(|e| e.to_string())?;
        done += read as u64;
        lock(&store.update).percent = (done * 100 / release.size.max(1)).min(100) as u8;
    }
    drop(file);
    if release.size > 0 && done != release.size {
        return Err("The download was incomplete.".into());
    }
    crate::shell_open(&target)
}

pub fn start(store: Arc<Store>, ui: Ui) {
    {
        let mut status = lock(&store.update);
        if status.running {
            return;
        }
        *status = UpdateStatus { running: true, message: "Downloading the update…".into(), ..Default::default() };
    }
    std::thread::spawn(move || match download_and_run(&store) {
        Ok(()) => {
            *lock(&store.update) = UpdateStatus { running: false, percent: 100, message: "Starting the installer…".into(), error: String::new() };
            ui.quit_soon();
        }
        Err(error) => {
            *lock(&store.update) = UpdateStatus { running: false, percent: 0, message: format!("Update failed: {error}"), error };
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn python_releases_are_never_offered_to_rust() {
        let asset = |v: &str| json!([{ "name": format!("DropAir-Setup-{v}.exe"), "size": 1,
            "browser_download_url": format!("https://github.com/B1progame/drop-air/releases/download/{v}/x.exe") }]);
        let releases = json!([
            { "tag_name": "1.9.0", "assets": asset("1.9.0") },
            { "tag_name": "2.1.0", "assets": asset("2.1.0") },
            { "tag_name": "2.0.5", "assets": asset("2.0.5") },
        ]);
        assert_eq!(newest(&releases, "2.0.0").unwrap().version, "2.1.0");
        assert!(newest(&releases, "2.1.0").is_none());
        assert!(newest(&releases, "1.0.0").unwrap().version.starts_with('2'));
    }
}
