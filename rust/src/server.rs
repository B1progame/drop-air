//! HTTP API shared word for word with the Python edition (app.py).

use crate::state::*;
use crate::{update, Ui};
use axum::{
    body::{Body, Bytes},
    extract::{ConnectInfo, DefaultBodyLimit, Path as UrlPath, Query, Request, State},
    http::{header, HeaderMap, HeaderValue, Method, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{delete, get, post},
    Json, Router,
};
use futures_util::StreamExt;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt, BufWriter};

const INDEX: &str = include_str!("../../web/index.html");
const CSS: &str = include_str!("../../web/app.css");
const JS: &str = include_str!("../../web/app.js");
const LOGO: &[u8] = include_bytes!("../../web/logo.png");
const BUFFER: usize = 1024 * 1024;
const COOKIE: &str = "dropair_session_recovery";

#[derive(Clone)]
pub struct App {
    pub store: Arc<Store>,
    pub ui: Ui,
}

type Params = Query<HashMap<String, String>>;
type Peer = ConnectInfo<SocketAddr>;

struct ApiError(u16, String);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = StatusCode::from_u16(self.0).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        (status, Json(json!({ "error": self.1 }))).into_response()
    }
}

fn fail(status: u16, message: impl Into<String>) -> ApiError {
    ApiError(status, message.into())
}

fn ok() -> Json<Value> {
    Json(json!({ "ok": true }))
}

fn header_str<'a>(headers: &'a HeaderMap, name: &str) -> &'a str {
    headers.get(name).and_then(|v| v.to_str().ok()).unwrap_or("")
}

fn is_loopback_authority(value: &str) -> bool {
    let host = value.rsplit_once(':').filter(|(_, p)| p.parse::<u16>().is_ok()).map(|(h, _)| h).unwrap_or(value);
    matches!(host, "127.0.0.1" | "localhost" | "[::1]")
}

/// Host controls need a loopback socket AND a loopback page. Checking Host and
/// Origin stops other websites open on this PC from driving the admin API.
fn is_admin(address: SocketAddr, headers: &HeaderMap) -> bool {
    let origin = header_str(headers, "origin");
    address.ip().is_loopback()
        && is_loopback_authority(header_str(headers, "host"))
        && (origin.is_empty() || origin.strip_prefix("http://").is_some_and(is_loopback_authority))
}

fn require_admin(address: SocketAddr, headers: &HeaderMap) -> Result<(), ApiError> {
    if is_admin(address, headers) { Ok(()) } else { Err(fail(403, "admin access is only available on the host machine")) }
}

fn has_cookie(store: &Store, headers: &HeaderMap) -> bool {
    let wanted = format!("{COOKIE}={}", lock(&store.recovery_secret));
    header_str(headers, "cookie").split(';').any(|part| part.trim() == wanted)
}

/// Checks the session key and the optional access code.
fn authorize(store: &Store, address: SocketAddr, headers: &HeaderMap, query: &HashMap<String, String>) -> Result<(), ApiError> {
    if is_admin(address, headers) {
        return Ok(());
    }
    if lock(&store.blocked).contains_key(&address.ip()) {
        return Err(fail(403, "removed by host"));
    }
    if store.is_asleep() {
        return Err(fail(503, "sharing is off"));
    }
    let key = query.get("k").map(String::as_str).unwrap_or_else(|| header_str(headers, "x-drop-air-key"));
    if !(store.is_valid_key(key) || has_cookie(store, headers)) {
        return Err(fail(404, "invalid session link"));
    }
    let code = store.settings().access_code;
    let supplied = query.get("code").cloned().unwrap_or_else(|| {
        percent_encoding::percent_decode_str(header_str(headers, "x-drop-air-code")).decode_utf8_lossy().into_owned()
    });
    if !code.is_empty() && supplied != code {
        return Err(fail(404, "code not found"));
    }
    Ok(())
}

pub fn router(app: App) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/assets/{name}", get(asset))
        .route("/favicon.ico", get(|| async { ([(header::CONTENT_TYPE, "image/png")], LOGO) }))
        .route("/qr.svg", get(qr))
        .route("/api/state", get(api_state))
        .route("/api/upload", post(upload))
        .route("/files/{name}", get(download))
        .route("/api/files/{name}", delete(delete_file))
        .route("/api/text", post(add_text))
        .route("/api/text/{id}", delete(delete_text))
        .route("/api/settings", post(save_settings))
        .route("/api/pair/requests", post(pair_create))
        .route("/api/pair/requests/{id}", get(pair_status))
        .route("/api/discovery/{device}/pair", post(relay_pair))
        .route("/api/discovery/{device}/pair/{id}", get(relay_status))
        .route("/api/admin/pair/{id}", post(pair_decide))
        .route("/api/admin/save/{name}", post(save_to_downloads))
        .route("/api/admin/kick/{id}", post(kick))
        .route("/api/admin/unblock", post(unblock))
        .route("/api/admin/reveal", post(reveal))
        .route("/api/admin/cleanup", post(cleanup))
        .route("/api/admin/rediscover", post(rediscover))
        .route("/api/admin/firewall", post(firewall))
        .route("/api/admin/show", post(show))
        .route("/api/admin/quit", post(quit))
        .route("/api/admin/update", get(update_info).post(update_start))
        .route("/api/admin/update/status", get(update_status))
        .layer(DefaultBodyLimit::max(256 * 1024))
        .layer(middleware::from_fn(common_headers))
        .with_state(app)
}

async fn common_headers(request: Request, next: Next) -> Response {
    let mut response = if request.method() == Method::OPTIONS {
        StatusCode::NO_CONTENT.into_response()
    } else {
        next.run(request).await
    };
    let headers = response.headers_mut();
    headers.insert("access-control-allow-origin", HeaderValue::from_static("*"));
    headers.insert("access-control-allow-headers", HeaderValue::from_static("Content-Type, X-Drop-Air-Code, X-Drop-Air-Key"));
    headers.insert("access-control-allow-methods", HeaderValue::from_static("GET, POST, DELETE, OPTIONS"));
    headers.insert("x-content-type-options", HeaderValue::from_static("nosniff"));
    headers.insert("referrer-policy", HeaderValue::from_static("no-referrer"));
    headers.entry(header::CACHE_CONTROL).or_insert(HeaderValue::from_static("no-store"));
    response
}

async fn index(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, Query(query): Params) -> Response {
    // A removed device gets the page without a cookie, so it can say what happened.
    if (app.store.is_asleep() || lock(&app.store.blocked).contains_key(&address.ip())) && !is_admin(address, &headers) {
        return ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], INDEX).into_response();
    }
    // A missing code is handled by the page itself, which then asks for it.
    let allowed = match authorize(&app.store, address, &headers, &query) {
        Ok(()) => true,
        Err(error) => error.1 == "code not found",
    };
    if !allowed {
        let page = "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>\
            <title>Link not found</title><body style='font:16px system-ui;display:grid;place-items:center;min-height:90vh;text-align:center'>\
            <div><h1>Link not found</h1><p>Scan the current QR code from the host PC.</p></div>";
        return (StatusCode::NOT_FOUND, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], page).into_response();
    }
    let cookie = format!("{COOKIE}={}; Max-Age=2592000; Path=/; HttpOnly; SameSite=Lax", lock(&app.store.recovery_secret));
    ([(header::CONTENT_TYPE, "text/html; charset=utf-8".to_string()), (header::SET_COOKIE, cookie)], INDEX).into_response()
}

async fn asset(UrlPath(name): UrlPath<String>) -> Response {
    let (kind, body): (&str, &'static [u8]) = match name.as_str() {
        "app.css" => ("text/css; charset=utf-8", CSS.as_bytes()),
        "app.js" => ("text/javascript; charset=utf-8", JS.as_bytes()),
        "logo.png" => ("image/png", LOGO),
        _ => return StatusCode::NOT_FOUND.into_response(),
    };
    ([(header::CONTENT_TYPE, kind), (header::CACHE_CONTROL, "no-cache")], body).into_response()
}

async fn qr(State(app): State<App>, Query(query): Params) -> Response {
    let url = query.get("url").cloned().unwrap_or_else(|| app.store.public_url());
    let Ok(code) = qrcode::QrCode::new(url.as_bytes()) else { return StatusCode::BAD_REQUEST.into_response() };
    let svg = code.render::<qrcode::render::svg::Color>().min_dimensions(240, 240).build();
    ([(header::CONTENT_TYPE, "image/svg+xml")], svg).into_response()
}

async fn api_state(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, Query(query): Params) -> Result<Json<Value>, ApiError> {
    let store = &app.store;
    authorize(store, address, &headers, &query)?;
    let admin = is_admin(address, &headers);
    let client_id: String = query.get("client_id").map(|v| v.chars().filter(char::is_ascii_alphanumeric).take(64).collect()).unwrap_or_default();
    // The host window is not counted as a connected device.
    if !admin && !client_id.is_empty() {
        let name = clean_device_name(query.get("name").map(String::as_str).unwrap_or("Browser"));
        let text = |key: &str| query.get(key).map(|v| v.chars().filter(|c| !c.is_control()).take(48).collect::<String>()).unwrap_or_default();
        let mut clients = lock(&store.clients);
        if clients.len() < 64 || clients.contains_key(&client_id) {
            let since = clients.get(&client_id).map_or_else(now, |c| c.since);
            let client = Client { name, seen: now(), since, ip: address.ip(), os: text("os"), browser: text("browser"), screen: text("screen") };
            clients.insert(client_id.clone(), client);
        }
    }
    store.cleanup();
    let settings = store.settings();
    let (key, remaining) = store.session();
    let files: Vec<_> = store.files().into_iter().filter(|f| admin || f.to.is_empty() || f.to == client_id || f.from == client_id).collect();
    let peers: Vec<_> = {
        let mut peers: Vec<_> = lock(&store.peers).values().cloned().collect();
        peers.sort_by_key(|p| p.device_name.to_lowercase());
        peers
    };
    let mut body = json!({
        "version": VERSION,
        "edition": "rust",
        "admin": admin,
        "device_id": store.device_id,
        "device_name": settings.device_name,
        "session_key": key,
        "seconds_remaining": remaining,
        "public_url": store.public_url(),
        "limit_bytes": settings.limit_bytes(),
        "limit_label": settings.limit_label(),
        "cleanup_minutes": settings.auto_cleanup_minutes,
        "text_ttl_minutes": text_ttl_minutes(),
        "files": files,
        "texts": *lock(&store.texts),
        "clients": store.clients_json(admin),
        "peers": peers,
        "discovery_status": *lock(&store.discovery_status),
    });
    if admin {
        body["settings"] = json!(settings);
        body["requests"] = json!(store.pending_pairs());
        body["blocked"] = json!(lock(&store.blocked).iter().map(|(ip, name)| json!({ "ip": ip.to_string(), "name": name })).collect::<Vec<_>>());
    }
    Ok(Json(body))
}

/// Removes the half-written file if the upload fails or the sender disconnects.
struct Staging(PathBuf);
impl Drop for Staging {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

async fn upload(
    State(app): State<App>,
    ConnectInfo(address): Peer,
    headers: HeaderMap,
    Query(query): Params,
    body: Body,
) -> Result<Json<Value>, ApiError> {
    let store = app.store.clone();
    authorize(&store, address, &headers, &query)?;
    let settings = store.settings();
    let limit = settings.limit_bytes();
    let too_large = || fail(413, format!("Upload is larger than {}.", settings.limit_label()));
    if header_str(&headers, "content-length").parse::<u64>().is_ok_and(|n| n > limit) {
        return Err(too_large());
    }
    let name = safe_file_name(query.get("name").filter(|n| !n.is_empty()).ok_or_else(|| fail(400, "missing file"))?);
    let disk = |error: std::io::Error| fail(500, format!("Could not save upload: {error}"));

    let staging = Staging(store.upload_dir.join(format!("{STAGING_PREFIX}staging-{}", random_hex(8))));
    let file = tokio::fs::File::create(&staging.0).await.map_err(disk)?;
    let mut writer = BufWriter::with_capacity(BUFFER, file);
    let mut stream = body.into_data_stream();
    let mut size = 0u64;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| fail(400, "Upload was interrupted."))?;
        size += chunk.len() as u64;
        if size > limit {
            return Err(too_large());
        }
        writer.write_all(&chunk).await.map_err(disk)?;
    }
    writer.flush().await.map_err(disk)?;
    drop(writer);

    // Publish under the first free name. A hard link appears atomically and
    // avoids copying the data a second time; other file systems get a rename.
    let mut index = 0;
    let stored = loop {
        let candidate = numbered_name(&name, index);
        let target = store.upload_dir.join(&candidate);
        match std::fs::hard_link(&staging.0, &target) {
            Ok(()) => break candidate,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => index += 1,
            Err(_) if !target.exists() => {
                std::fs::rename(&staging.0, &target).map_err(disk)?;
                break candidate;
            }
            Err(_) => index += 1,
        }
    };
    drop(staging);
    let field = |key: &str| query.get(key).map(|v| v.chars().filter(char::is_ascii_alphanumeric).take(64).collect::<String>()).unwrap_or_default();
    let to = field("to");
    if to.is_empty() {
        lock(&store.routes).remove(&stored);
    } else {
        lock(&store.routes).insert(stored.clone(), (to, field("from")));
    }
    store.cleanup();
    Ok(Json(json!({ "ok": true, "filename": stored, "size": size })))
}

/// A single `bytes=` range, so interrupted downloads can resume.
fn parse_range(value: &str, size: u64) -> Option<Result<(u64, u64), ()>> {
    let spec = value.strip_prefix("bytes=")?;
    if spec.contains(',') {
        return None;
    }
    let (start, end) = spec.split_once('-')?;
    let range = match (start.parse::<u64>().ok(), end.parse::<u64>().ok()) {
        (Some(s), Some(e)) => (s, e.min(size.saturating_sub(1))),
        (Some(s), None) => (s, size.saturating_sub(1)),
        (None, Some(suffix)) => (size.saturating_sub(suffix), size.saturating_sub(1)),
        (None, None) => return None,
    };
    Some(if range.0 < size && range.0 <= range.1 { Ok(range) } else { Err(()) })
}

async fn download(
    State(app): State<App>,
    ConnectInfo(address): Peer,
    headers: HeaderMap,
    Query(query): Params,
    UrlPath(name): UrlPath<String>,
) -> Result<Response, ApiError> {
    authorize(&app.store, address, &headers, &query)?;
    let path = app.store.file_path(&name).ok_or_else(|| fail(404, "file not found"))?;
    let missing = |_| fail(404, "file not found");
    let mut file = tokio::fs::File::open(&path).await.map_err(missing)?;
    let size = file.metadata().await.map_err(missing)?.len();
    let mut builder = Response::builder()
        .header(header::CONTENT_TYPE, "application/octet-stream")
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CONTENT_DISPOSITION, format!("attachment; filename*=UTF-8''{}", encode(&name)));
    let mut length = size;
    match parse_range(header_str(&headers, "range"), size) {
        Some(Ok((start, end))) => {
            file.seek(std::io::SeekFrom::Start(start)).await.map_err(missing)?;
            length = end - start + 1;
            builder = builder.status(206).header(header::CONTENT_RANGE, format!("bytes {start}-{end}/{size}"));
        }
        Some(Err(())) => {
            builder = builder.status(416).header(header::CONTENT_RANGE, format!("bytes */{size}"));
            length = 0;
        }
        None => {}
    }
    let stream = tokio_util::io::ReaderStream::with_capacity(file.take(length), BUFFER);
    builder
        .header(header::CONTENT_LENGTH, length)
        .body(Body::from_stream(stream))
        .map_err(|e| fail(500, e.to_string()))
}

async fn delete_file(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, Query(query): Params, UrlPath(name): UrlPath<String>) -> Result<Json<Value>, ApiError> {
    authorize(&app.store, address, &headers, &query)?;
    if app.store.delete_file(&name) { Ok(ok()) } else { Err(fail(404, "file not found")) }
}

fn parse_json(body: &Bytes) -> Result<Value, ApiError> {
    serde_json::from_slice::<Value>(body).ok().filter(Value::is_object).ok_or_else(|| fail(400, "invalid payload"))
}

async fn add_text(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, Query(query): Params, body: Bytes) -> Result<Json<Value>, ApiError> {
    authorize(&app.store, address, &headers, &query)?;
    let payload = parse_json(&body)?;
    let item = app.store.add_text(payload["text"].as_str().unwrap_or("")).ok_or_else(|| fail(400, "Paste text before sharing."))?;
    Ok(Json(json!({ "ok": true, "item": item })))
}

async fn delete_text(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, Query(query): Params, UrlPath(id): UrlPath<String>) -> Result<Json<Value>, ApiError> {
    authorize(&app.store, address, &headers, &query)?;
    if app.store.retain_texts(|t| t.id != id) { Ok(ok()) } else { Err(fail(404, "text item not found")) }
}

async fn save_settings(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, body: Bytes) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    let payload = parse_json(&body)?;
    let mut settings = app.store.settings();
    if let Some(code) = payload["access_code"].as_str() {
        settings.access_code = code.trim().to_string();
    }
    for (key, field) in [
        ("auto_cleanup_minutes", &mut settings.auto_cleanup_minutes),
        ("auto_cleanup_days", &mut settings.auto_cleanup_days),
        ("auto_cleanup_max_files", &mut settings.auto_cleanup_max_files),
        ("tray_sleep_minutes", &mut settings.tray_sleep_minutes),
    ] {
        if let Some(value) = payload.get(key) {
            *field = value.as_u64().filter(|v| *v <= 1_000_000).ok_or_else(|| fail(400, format!("{key} must be a non-negative integer")))?;
        }
    }
    if let Some(value) = payload.get("max_upload_gb") {
        let gb = value.as_f64().filter(|v| (0.01..=1024.0).contains(v)).ok_or_else(|| fail(400, "max_upload_gb must be between 0.01 and 1024"))?;
        settings.max_upload_gb = (gb * 1000.0).round() / 1000.0;
    }
    if let Some(value) = payload["launch_browser_on_start"].as_bool() {
        settings.launch_browser_on_start = value;
    }
    if let Some(value) = payload.get("device_name") {
        let name = value.as_str().map(str::trim).filter(|n| !n.is_empty() && n.chars().count() <= 32);
        settings.device_name = clean_device_name(name.ok_or_else(|| fail(400, "device_name must contain 1 to 32 characters"))?);
    }
    if let Some(value) = payload["discoverable"].as_bool() {
        settings.discoverable = value;
    }
    app.store.save_settings(settings.clone()).map_err(|_| fail(500, "Settings could not be saved to disk."))?;
    app.store.cleanup();
    Ok(Json(json!({ "ok": true, "settings": settings })))
}

async fn pair_create(State(app): State<App>, ConnectInfo(address): Peer, body: Bytes) -> Result<Response, ApiError> {
    if lock(&app.store.blocked).contains_key(&address.ip()) {
        return Err(fail(403, "removed by host"));
    }
    if app.store.is_asleep() {
        return Err(fail(503, "sharing is off"));
    }
    if !app.store.settings().discoverable {
        return Err(fail(404, "this Drop Air device is not discoverable"));
    }
    let payload = parse_json(&body)?;
    let device_id = payload["device_id"].as_str().unwrap_or("").to_lowercase();
    let request = app
        .store
        .create_pair(address.ip(), &device_id, payload["device_name"].as_str().unwrap_or(""))
        .map_err(|(status, message)| fail(status, message))?;
    app.ui.attention();
    Ok((StatusCode::ACCEPTED, Json(json!({ "request": request }))).into_response())
}

async fn pair_status(State(app): State<App>, ConnectInfo(address): Peer, UrlPath(id): UrlPath<String>) -> Result<Json<Value>, ApiError> {
    let decision = lock(&app.store.pairs)
        .iter()
        .find(|p| p.id == id && p.address == address.ip().to_string() && p.expires_at > now())
        .map(|p| p.decision)
        .ok_or_else(|| fail(404, "request expired or not found"))?;
    let mut body = json!({ "decision": decision });
    if decision == "accepted" {
        body["share_url"] = json!(app.store.public_url());
    }
    Ok(Json(body))
}

async fn pair_decide(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, UrlPath(id): UrlPath<String>, body: Bytes) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    let decision = match parse_json(&body)?["decision"].as_str() {
        Some("accept") => "accepted",
        Some("decline") => "declined",
        _ => return Err(fail(400, "decision must be accept or decline")),
    };
    let mut pairs = lock(&app.store.pairs);
    let request = pairs
        .iter_mut()
        .find(|p| p.id == id && p.decision == "pending" && p.expires_at > now())
        .ok_or_else(|| fail(404, "This request expired or was already handled."))?;
    request.decision = decision;
    Ok(ok())
}

async fn kick(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, UrlPath(id): UrlPath<String>) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    if app.store.kick(&id) { Ok(ok()) } else { Err(fail(404, "That device is no longer connected.")) }
}

async fn unblock(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, body: Bytes) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    let ip = parse_json(&body)?["ip"].as_str().and_then(|v| v.parse::<std::net::IpAddr>().ok()).ok_or_else(|| fail(400, "invalid address"))?;
    lock(&app.store.blocked).remove(&ip);
    Ok(ok())
}

/// Forward a pairing call to another Drop Air host that discovery found.
async fn relay(app: App, device: String, path: String, payload: Option<Value>) -> Result<Response, ApiError> {
    let peer = lock(&app.store.peers).get(&device).cloned().ok_or_else(|| fail(404, "That device is no longer nearby."))?;
    let url = format!("http://{}:{}{path}", peer.address, peer.port);
    let result = tokio::task::spawn_blocking(move || {
        let agent = ureq::AgentBuilder::new().timeout(std::time::Duration::from_secs(4)).redirects(0).build();
        let response = match payload {
            Some(body) => agent.post(&url).send_json(body),
            None => agent.get(&url).call(),
        };
        match response {
            Ok(r) => Some((r.status(), r.into_json::<Value>().ok()?)),
            Err(ureq::Error::Status(status, r)) => Some((status, r.into_json::<Value>().ok()?)),
            Err(_) => None,
        }
    })
    .await
    .ok()
    .flatten();
    let (status, body) = result.ok_or_else(|| fail(502, "Could not reach that device. Check Wi-Fi and Windows Firewall."))?;
    Ok((StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY), Json(body)).into_response())
}

async fn relay_pair(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, Query(query): Params, UrlPath(device): UrlPath<String>, body: Bytes) -> Result<Response, ApiError> {
    authorize(&app.store, address, &headers, &query)?;
    let payload = parse_json(&body)?;
    relay(app, device, "/api/pair/requests".into(), Some(payload)).await
}

async fn relay_status(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, Query(query): Params, UrlPath((device, id)): UrlPath<(String, String)>) -> Result<Response, ApiError> {
    authorize(&app.store, address, &headers, &query)?;
    if id.len() != 48 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(fail(400, "Invalid connection request."));
    }
    relay(app, device, format!("/api/pair/requests/{id}"), None).await
}

async fn save_to_downloads(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, UrlPath(name): UrlPath<String>) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    let source = app.store.file_path(&name).ok_or_else(|| fail(404, "file not found"))?;
    let folder = std::env::var_os("DROP_AIR_DOWNLOAD_DIR")
        .map(PathBuf::from)
        .or_else(dirs::download_dir)
        .ok_or_else(|| fail(500, "No Downloads folder found."))?;
    let saved = tokio::task::spawn_blocking(move || -> std::io::Result<String> {
        std::fs::create_dir_all(&folder)?;
        let mut index = 0;
        loop {
            let candidate = numbered_name(&name, index);
            match std::fs::OpenOptions::new().write(true).create_new(true).open(folder.join(&candidate)) {
                Ok(_) => {
                    std::fs::copy(&source, folder.join(&candidate))?;
                    return Ok(candidate);
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => index += 1,
                Err(e) => return Err(e),
            }
        }
    })
    .await
    .map_err(|e| fail(500, e.to_string()))?
    .map_err(|e| fail(500, format!("Could not save the file: {e}")))?;
    Ok(Json(json!({ "ok": true, "filename": saved })))
}

async fn reveal(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    std::process::Command::new("explorer").arg(&app.store.upload_dir).spawn().map_err(|e| fail(500, e.to_string()))?;
    Ok(ok())
}

async fn firewall(ConnectInfo(address): Peer, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    std::process::Command::new("control.exe").arg("firewall.cpl").spawn().map_err(|_| fail(500, "Windows Firewall settings could not be opened."))?;
    Ok(ok())
}

async fn cleanup(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap, body: Bytes) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    let target = parse_json(&body)?["target"].as_str().unwrap_or("").to_string();
    if !matches!(target.as_str(), "uploads" | "text" | "all") {
        return Err(fail(400, "target must be uploads, text, or all"));
    }
    if target != "text" {
        app.store.clear_uploads();
    }
    if target != "uploads" {
        app.store.retain_texts(|_| false);
    }
    Ok(ok())
}

async fn rediscover(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    *lock(&app.store.rediscover) = true;
    Ok(ok())
}

async fn show(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    app.ui.show();
    Ok(ok())
}

async fn quit(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    app.ui.quit_soon();
    Ok(ok())
}

async fn update_info(ConnectInfo(address): Peer, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    let info = tokio::task::spawn_blocking(update::check).await.map_err(|e| fail(500, e.to_string()))?;
    let info = info.map_err(|e| fail(502, format!("Could not check for updates: {e}")))?;
    let message = match &info {
        Some(release) => format!("Version {} is available.", release.version),
        None => "Drop Air is up to date.".to_string(),
    };
    Ok(Json(json!({ "update_available": info.is_some(), "message": message, "current_version": VERSION })))
}

async fn update_start(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    update::start(app.store.clone(), app.ui.clone());
    Ok(ok())
}

async fn update_status(State(app): State<App>, ConnectInfo(address): Peer, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    require_admin(address, &headers)?;
    Ok(Json(json!(*lock(&app.store.update))))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ranges() {
        assert_eq!(parse_range("bytes=0-9", 100), Some(Ok((0, 9))));
        assert_eq!(parse_range("bytes=90-", 100), Some(Ok((90, 99))));
        assert_eq!(parse_range("bytes=-10", 100), Some(Ok((90, 99))));
        assert_eq!(parse_range("bytes=100-", 100), Some(Err(())));
        assert_eq!(parse_range("", 100), None);
    }

    #[test]
    fn other_websites_are_not_the_host() {
        let local: SocketAddr = "127.0.0.1:5000".parse().unwrap();
        let mut headers = HeaderMap::new();
        headers.insert("host", "127.0.0.1:8000".parse().unwrap());
        assert!(is_admin(local, &headers));
        headers.insert("origin", "https://evil.example".parse().unwrap());
        assert!(!is_admin(local, &headers));
        headers.remove("origin");
        headers.insert("host", "evil.example:8000".parse().unwrap());
        assert!(!is_admin(local, &headers));
    }
}
