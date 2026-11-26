//! Drop Air: local file and text sharing with a small Windows tray app.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod discovery;
mod server;
mod state;
mod update;

use state::Store;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tao::dpi::LogicalSize;
use tao::event::{Event, StartCause, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy};
use tao::window::{Icon, Window, WindowBuilder};
use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use wry::{WebContext, WebView, WebViewBuilder};

const LOGO: &[u8] = include_bytes!("../../web/logo.png");

pub enum UserEvent {
    Show,
    Attention,
    Quit,
    Menu(String),
}

/// Lets server threads talk to the window; does nothing when running headless.
#[derive(Clone)]
pub struct Ui(Option<EventLoopProxy<UserEvent>>);

impl Ui {
    fn send(&self, event: UserEvent) {
        if let Some(proxy) = &self.0 {
            let _ = proxy.send_event(event);
        }
    }
    pub fn show(&self) {
        self.send(UserEvent::Show);
    }
    pub fn attention(&self) {
        self.send(UserEvent::Attention);
    }
    /// Quit after the current HTTP response had time to leave.
    pub fn quit_soon(&self) {
        let ui = self.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(500));
            match &ui.0 {
                Some(_) => ui.send(UserEvent::Quit),
                None => std::process::exit(0),
            }
        });
    }
}

pub fn shell_open(path: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let verb: Vec<u16> = "open\0".encode_utf16().collect();
    // ShellExecute shows the elevation prompt the installer needs.
    let result = unsafe {
        ShellExecuteW(std::ptr::null_mut(), verb.as_ptr(), wide.as_ptr(), std::ptr::null(), std::ptr::null(), SW_SHOWNORMAL)
    };
    if result as isize > 32 { Ok(()) } else { Err("Windows could not start the installer.".into()) }
}

/// Ask an already running Drop Air (either edition) to show its window.
fn activate_running(data_dir: &Path) -> bool {
    let Ok(port) = std::fs::read_to_string(data_dir.join("port")) else { return false };
    let Ok(port) = port.trim().parse::<u16>() else { return false };
    ureq::post(&format!("http://127.0.0.1:{port}/api/admin/show"))
        .timeout(Duration::from_millis(700))
        .call()
        .is_ok()
}

/// Each PC gets its own random port and keeps it, so links survive restarts.
/// `PORT` overrides it.
fn port_candidates(data_dir: &Path) -> Vec<u16> {
    if let Some(port) = std::env::var("PORT").ok().and_then(|p| p.parse::<u16>().ok()) {
        return (port..port.saturating_add(50)).collect();
    }
    let saved = std::fs::read_to_string(data_dir.join("preferred-port")).ok().and_then(|p| p.trim().parse::<u16>().ok());
    let random = (0..50).map(|_| {
        let mut bytes = [0u8; 4];
        getrandom::getrandom(&mut bytes).expect("the system random source failed");
        20_000 + (u32::from_le_bytes(bytes) % 29_000) as u16
    });
    saved.into_iter().chain(random).collect()
}

fn serve(store: Arc<Store>, ui: Ui) -> std::io::Result<tokio::runtime::Runtime> {
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build()?;
    let candidates = port_candidates(&store.data_dir);
    let listener = runtime.block_on(async {
        let mut last = std::io::Error::other("no free port");
        for port in candidates {
            match tokio::net::TcpListener::bind(("0.0.0.0", port)).await {
                Ok(listener) => return Ok(listener),
                Err(error) => last = error,
            }
        }
        Err(last)
    })?;
    let port = listener.local_addr()?.port();
    store.port.store(port, std::sync::atomic::Ordering::Relaxed);
    std::fs::write(store.data_dir.join("port"), port.to_string())?;
    if std::env::var_os("PORT").is_none() {
        std::fs::write(store.data_dir.join("preferred-port"), port.to_string())?;
    }
    let router = server::router(server::App { store: store.clone(), ui });
    runtime.spawn(async move {
        let _ = axum::serve(listener, router.into_make_service_with_connect_info::<SocketAddr>()).await;
    });
    // Expire files even while no page is polling.
    runtime.spawn(async move {
        loop {
            store.cleanup();
            tokio::time::sleep(Duration::from_secs(15)).await;
        }
    });
    Ok(runtime)
}

fn shutdown(store: &Store) {
    store.clear_uploads();
    store.retain_texts(|_| false);
    let _ = std::fs::remove_file(store.data_dir.join("port"));
}

/// Old Python releases kept a ~430 MB rollback copy and a browser profile.
/// Never delete the folder we run from: some old installs lived in `rollback`.
fn remove_leftovers(data_dir: std::path::PathBuf) {
    std::thread::spawn(move || {
        // Unknown program location means nothing can safely be ruled out.
        let Ok(program) = std::env::current_exe().and_then(std::fs::canonicalize) else { return };
        for name in ["rollback", "browser-profile"] {
            let Ok(folder) = std::fs::canonicalize(data_dir.join(name)) else { continue };
            if !program.starts_with(&folder) {
                let _ = std::fs::remove_dir_all(folder);
            }
        }
    });
}

/// When sharing should turn off, if the window is hidden and the setting asks for it.
fn sleep_at(store: &Store, hidden_since: Option<Instant>) -> Option<Instant> {
    let minutes = store.settings().tray_sleep_minutes;
    hidden_since.filter(|_| minutes > 0 && !store.is_asleep()).map(|since| since + Duration::from_secs(minutes * 60))
}

fn logo_rgba() -> (Vec<u8>, u32, u32) {
    let mut reader = png::Decoder::new(std::io::Cursor::new(LOGO)).read_info().expect("embedded logo");
    let mut pixels = vec![0; reader.output_buffer_size().expect("embedded logo")];
    let info = reader.next_frame(&mut pixels).expect("embedded logo");
    pixels.truncate(info.buffer_size());
    (pixels, info.width, info.height)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--version") {
        println!("Drop Air {}", state::VERSION);
        return;
    }
    let headless = args.iter().any(|a| a == "--headless");
    let data_dir = state::data_dir();
    if !headless && activate_running(&data_dir) {
        return;
    }
    let store = Arc::new(Store::open(data_dir).expect("the Drop Air data folder is not writable"));
    remove_leftovers(store.data_dir.clone());

    if headless {
        let runtime = serve(store.clone(), Ui(None)).expect("could not start the local server");
        discovery::start(store.clone());
        println!("Drop Air {} on {}", state::VERSION, store.local_url());
        println!("{}", store.public_url());
        runtime.block_on(std::future::pending::<()>());
        return;
    }

    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let ui = Ui(Some(event_loop.create_proxy()));
    let _runtime = serve(store.clone(), ui.clone()).expect("could not start the local server");
    discovery::start(store.clone());

    let open_item = MenuItem::with_id("open", "Open Drop Air", true, None);
    let quit_item = MenuItem::with_id("quit", "Quit Drop Air", true, None);
    let menu = Menu::new();
    let _ = menu.append_items(&[&open_item, &PredefinedMenuItem::separator(), &quit_item]);
    let proxy = event_loop.create_proxy();
    MenuEvent::set_event_handler(Some(Arc::new(move |event: MenuEvent| {
        let _ = proxy.send_event(UserEvent::Menu(event.id.0));
    })));
    let proxy = event_loop.create_proxy();
    TrayIconEvent::set_event_handler(Some(move |event: TrayIconEvent| {
        if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
            let _ = proxy.send_event(UserEvent::Show);
        }
    }));

    let mut tray = None;
    let mut web_context = WebContext::new(Some(store.data_dir.join("webview")));
    // The window and its browser engine only exist while visible, so hiding to
    // the tray frees their memory.
    let mut view: Option<(Window, WebView)> = None;
    let mut hidden_since: Option<Instant> = None;

    event_loop.run(move |event, target, control_flow| {
        *control_flow = match sleep_at(&store, hidden_since) {
            Some(time) => ControlFlow::WaitUntil(time),
            None => ControlFlow::Wait,
        };
        let mut show = false;
        let mut quit = false;
        match event {
            Event::NewEvents(StartCause::Init) => {
                let (pixels, width, height) = logo_rgba();
                tray = TrayIconBuilder::new()
                    .with_tooltip(format!("Drop Air {}", state::VERSION))
                    .with_menu(Box::new(menu.clone()))
                    .with_menu_on_left_click(false)
                    .with_icon(tray_icon::Icon::from_rgba(pixels, width, height).expect("tray icon"))
                    .build()
                    .ok();
                show = true;
            }
            // Hidden long enough: stop sharing, but stay in the tray.
            Event::NewEvents(StartCause::ResumeTimeReached { .. }) if view.is_none() => {
                store.sleep();
                hidden_since = None;
                *control_flow = ControlFlow::Wait;
            }
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => {
                view = None;
                if tray.is_some() {
                    hidden_since = Some(Instant::now());
                    if let Some(time) = sleep_at(&store, hidden_since) {
                        *control_flow = ControlFlow::WaitUntil(time);
                    }
                } else {
                    quit = true;
                }
            }
            Event::UserEvent(UserEvent::Show) => show = true,
            Event::UserEvent(UserEvent::Attention) => show = view.is_none(),
            Event::UserEvent(UserEvent::Quit) => quit = true,
            Event::UserEvent(UserEvent::Menu(id)) => match id.as_str() {
                "open" => show = true,
                "quit" => quit = true,
                _ => {}
            },
            _ => {}
        }
        if show {
            store.wake();
            hidden_since = None;
            *control_flow = ControlFlow::Wait;
            if let Some((window, _)) = &view {
                window.set_minimized(false);
                window.set_visible(true);
                window.set_focus();
            } else {
                let (pixels, width, height) = logo_rgba();
                let window = WindowBuilder::new()
                    .with_title("Drop Air")
                    .with_inner_size(LogicalSize::new(1080.0, 860.0))
                    .with_min_inner_size(LogicalSize::new(420.0, 560.0))
                    .with_theme(Some(tao::window::Theme::Dark))
                    .with_background_color((18, 19, 22, 255))
                    .with_window_icon(Icon::from_rgba(pixels, width, height).ok())
                    .build(target);
                if let Ok(window) = window {
                    let web = WebViewBuilder::new_with_web_context(&mut web_context)
                        .with_url(store.local_url())
                        .with_background_color((18, 19, 22, 255))
                        .with_navigation_handler(|url| url.starts_with("http://127.0.0.1:"))
                        .build(&window);
                    match web {
                        Ok(web) => view = Some((window, web)),
                        Err(error) => {
                            // No WebView2 runtime: the same page works in any browser.
                            eprintln!("WebView2 unavailable: {error}");
                            let _ = std::process::Command::new("explorer").arg(store.local_url()).spawn();
                        }
                    }
                }
            }
        }
        if quit {
            view = None;
            tray = None;
            shutdown(&store);
            *control_flow = ControlFlow::Exit;
        }
    });
}
