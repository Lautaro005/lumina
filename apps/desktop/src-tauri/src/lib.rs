//! Lumina desktop: finds the Lumina install, starts its local server if nobody did, shows it in a
//! window and keeps a tray icon. The app never touches your data directly; everything goes
//! through the server on 127.0.0.1.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_opener::OpenerExt;

mod notch;

const DEFAULT_PORT: u16 = 47770;

fn user_home() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn is_lumina(dir: &Path) -> bool {
    dir.join("pyproject.toml").is_file() && dir.join("lumina").join("server.py").is_file()
}

/// `LUMINA_HOME`, then `~/.lumina` (where the installer puts it), then a checkout above the executable.
pub fn find_home() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(p) = std::env::var_os("LUMINA_HOME") {
        candidates.push(PathBuf::from(p));
    }
    if let Some(h) = user_home() {
        candidates.push(h.join(".lumina"));
    }
    if let Ok(exe) = std::env::current_exe() {
        candidates.extend(exe.ancestors().skip(1).map(Path::to_path_buf));
    }
    candidates.into_iter().find(|d| is_lumina(d))
}

/// A GUI app starts with a bare PATH (macOS especially): add where uv and the tools usually live.
fn fixed_path() -> std::ffi::OsString {
    let mut parts: Vec<PathBuf> = Vec::new();
    if let Some(h) = user_home() {
        parts.push(h.join(".local/bin"));
        parts.push(h.join(".cargo/bin"));
    }
    for p in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] {
        parts.push(PathBuf::from(p));
    }
    let own = std::env::var_os("APPDIR");
    if let Some(cur) = std::env::var_os("PATH") {
        parts.extend(
            std::env::split_paths(&cur)
                .filter(|p| !own.as_deref().is_some_and(|d| p.starts_with(d))),
        );
    }
    std::env::join_paths(parts).unwrap_or_default()
}

/// AppImage runtime variables must not leak into Python (it would look for its stdlib inside the AppImage).
const APPIMAGE_VARS: [&str; 8] = [
    "APPDIR",
    "APPIMAGE",
    "ARGV0",
    "OWD",
    "PYTHONHOME",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "GTK_PATH",
];

pub struct Backend {
    pub home: Option<PathBuf>,
    pub port: u16,
    child: Mutex<Option<Child>>,
    started_here: AtomicBool,
}

impl Backend {
    pub fn discover() -> Backend {
        let port = std::env::var("LUMINA_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
            .unwrap_or(DEFAULT_PORT);
        Backend {
            home: find_home(),
            port,
            child: Mutex::new(None),
            started_here: AtomicBool::new(false),
        }
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port)
    }

    fn agent() -> ureq::Agent {
        ureq::AgentBuilder::new()
            .timeout(Duration::from_secs(3))
            .build()
    }

    /// True only when Lumina itself answers on the port (another program could hold it).
    pub fn healthy(&self) -> bool {
        Self::agent()
            .get(&format!("http://127.0.0.1:{}/api/settings", self.port))
            .call()
            .ok()
            .and_then(|r| r.into_string().ok())
            .is_some_and(|body| body.contains("\"stt_engine\""))
    }

    fn log_path(&self) -> Option<PathBuf> {
        let data = std::env::var_os("LUMINA_DATA")
            .map(PathBuf::from)
            .or_else(|| self.home.as_ref().map(|h| h.join("data")))?;
        let dir = data.join("logs");
        std::fs::create_dir_all(&dir).ok()?;
        Some(dir.join("desktop.log"))
    }

    pub fn log(&self, line: &str) {
        if let Some(p) = self.log_path() {
            if let Ok(mut f) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(p)
            {
                let _ = writeln!(f, "{line}");
            }
        }
    }

    #[cfg(unix)]
    fn command(&self, home: &Path) -> Command {
        use std::os::unix::process::CommandExt;
        let mut c = Command::new("/bin/sh");
        c.arg(home.join("lumina.sh"))
            .args(["serve", "--port", &self.port.to_string()])
            .process_group(0);
        c
    }

    #[cfg(windows)]
    fn command(&self, home: &Path) -> Command {
        use std::os::windows::process::CommandExt;
        let mut c = Command::new("powershell.exe");
        c.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(home.join("lumina.ps1"))
            .args(["serve", "--port", &self.port.to_string()])
            .creation_flags(0x0800_0000);
        c
    }

    /// Starts the server unless one already answers on the port (then it is used as is).
    pub fn ensure_running(self: &Arc<Self>) -> Result<(), String> {
        if self.healthy() {
            self.log("server already running: using it");
            return Ok(());
        }
        let home = self.home.clone().ok_or_else(|| {
            "Lumina is not installed on this computer. Install it with the command on lautaro005.github.io/lumina".to_string()
        })?;
        let mut cmd = self.command(&home);
        if std::env::var_os("APPDIR").is_some() {
            for v in APPIMAGE_VARS {
                cmd.env_remove(v);
            }
        }
        cmd.current_dir(&home)
            .env("LUMINA_APP", "1")
            .env("PATH", fixed_path())
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        self.log(&format!("starting Lumina from {}", home.display()));
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("could not start Lumina: {e}"))?;
        for stream in [
            child
                .stdout
                .take()
                .map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
            child
                .stderr
                .take()
                .map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
        ]
        .into_iter()
        .flatten()
        {
            let me = Arc::clone(self);
            std::thread::spawn(move || {
                for line in BufReader::new(stream).lines().map_while(Result::ok) {
                    me.log(&line);
                }
            });
        }
        self.started_here.store(true, Ordering::SeqCst);
        *self.child.lock().unwrap() = Some(child);
        Ok(())
    }

    pub fn wait_ready(&self, limit: Duration) -> Result<(), String> {
        let start = Instant::now();
        while start.elapsed() < limit {
            if self.healthy() {
                return Ok(());
            }
            if let Some(child) = self.child.lock().unwrap().as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    return Err(format!(
                        "Lumina stopped while starting ({status}). Run `lumina doctor` in a terminal."
                    ));
                }
            }
            std::thread::sleep(Duration::from_millis(400));
        }
        Err("Lumina took too long to start. Run `lumina doctor` in a terminal.".into())
    }

    /// Quit: ask the server we started to close (it stops its own helpers), then make sure it did.
    pub fn stop(&self) {
        if !self.started_here.swap(false, Ordering::SeqCst) {
            return;
        }
        let _ = Self::agent()
            .post(&format!("http://127.0.0.1:{}/api/quit", self.port))
            .set("X-Lumina", "1")
            .set("Content-Type", "application/json")
            .send_string("{}");
        let mut guard = self.child.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            let start = Instant::now();
            while start.elapsed() < Duration::from_secs(10) {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
            if !matches!(child.try_wait(), Ok(Some(_))) {
                kill_tree(child.id());
                let _ = child.kill();
            }
        }
        *guard = None;
    }
}

#[cfg(unix)]
fn kill_tree(pid: u32) {
    let _ = Command::new("kill")
        .args(["-TERM", &format!("-{pid}")])
        .status();
}

#[cfg(windows)]
fn kill_tree(pid: u32) {
    use std::os::windows::process::CommandExt;
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(0x0800_0000)
        .status();
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn open_route(app: &AppHandle, backend: &Backend, route: &str) {
    if let Some(w) = app.get_webview_window("main") {
        if let Ok(url) = Url::parse(&format!("{}#{route}", backend.url())) {
            let _ = w.navigate(url);
        }
    }
    show_main(app);
}

fn error_page(app: &AppHandle, message: &str) {
    if let Some(w) = app.get_webview_window("main") {
        let encoded: String = message
            .bytes()
            .map(|b| match b {
                b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                    (b as char).to_string()
                }
                _ => format!("%{b:02X}"),
            })
            .collect();
        let _ = w.eval(format!(
            "window.location.hash = 'error=' + '{encoded}'; window.dispatchEvent(new HashChangeEvent('hashchange'));"
        ));
    }
}

/// The notch asks for the state it missed while its page was loading.
#[tauri::command]
fn notch_state() -> serde_json::Value {
    notch::state_json()
}

/// A click on the notch: back to the meeting being recorded.
#[tauri::command]
fn notch_open_meeting(app: AppHandle, id: String) {
    if id.is_empty() || !id.chars().all(|c| c.is_ascii_alphanumeric()) {
        return;
    }
    let backend = app.state::<Arc<Backend>>().inner().clone();
    open_route(&app, &backend, &format!("/meeting/{id}"));
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend = Arc::new(Backend::discover());
    let for_exit = Arc::clone(&backend);

    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![notch_state, notch_open_meeting])
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main(app);
        }))
        .plugin(tauri_plugin_opener::init())
        .setup({
            let backend = Arc::clone(&backend);
            move |app| {
                let handle = app.handle().clone();
                let port = backend.port;
                app.manage(Arc::clone(&backend));
                let opener_handle = handle.clone();
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("Lumina")
                    .inner_size(1320.0, 860.0)
                    .min_inner_size(720.0, 520.0)
                    .on_navigation(move |url| {
                        let local = matches!(url.host_str(), Some("127.0.0.1") | Some("localhost"))
                            && url.port() == Some(port);
                        let own =
                            url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost");
                        if local || own {
                            return true;
                        }
                        if matches!(url.scheme(), "http" | "https" | "mailto") {
                            let _ = opener_handle.opener().open_url(url.as_str(), None::<&str>);
                        }
                        false
                    })
                    .build()?;

                let open_i = MenuItem::with_id(app, "open", "Open Lumina", true, None::<&str>)?;
                let new_i = MenuItem::with_id(app, "new", "New meeting", true, None::<&str>)?;
                let quit_i = MenuItem::with_id(app, "quit", "Quit Lumina", true, None::<&str>)?;
                let sep = PredefinedMenuItem::separator(app)?;
                let menu = Menu::with_items(app, &[&open_i, &new_i, &sep, &quit_i])?;
                let b2 = Arc::clone(&backend);
                let mut tray = TrayIconBuilder::with_id("lumina")
                    .tooltip("Lumina")
                    .menu(&menu)
                    .show_menu_on_left_click(false)
                    .on_menu_event(move |app, event| match event.id.as_ref() {
                        "open" => show_main(app),
                        "new" => open_route(app, &b2, "/new"),
                        "quit" => app.exit(0),
                        _ => {}
                    })
                    .on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        } = event
                        {
                            show_main(tray.app_handle());
                        }
                    });
                if let Some(icon) = app.default_window_icon() {
                    tray = tray.icon(icon.clone());
                }
                tray.build(app)?;

                notch::watch(app.handle().clone(), Arc::clone(&backend));

                let b3 = Arc::clone(&backend);
                std::thread::spawn(move || {
                    let result = b3
                        .ensure_running()
                        .and_then(|_| b3.wait_ready(Duration::from_secs(120)));
                    match result {
                        Ok(()) => {
                            if let Some(w) = handle.get_webview_window("main") {
                                if let Ok(url) = Url::parse(&b3.url()) {
                                    let _ = w.navigate(url);
                                }
                            }
                        }
                        Err(e) => {
                            b3.log(&e);
                            error_page(&handle, &e);
                        }
                    }
                });
                Ok(())
            }
        })
        .on_window_event(|window, event| {
            // closing the window keeps Lumina in the tray; Quit is in the tray menu
            if window.label() != "main" {
                return;
            }
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Lumina");

    app.run(move |handle, event| match event {
        RunEvent::ExitRequested { .. } | RunEvent::Exit => for_exit.stop(),
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => show_main(handle),
        _ => {
            let _ = handle;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognises_an_install_folder() {
        let dir = std::env::temp_dir().join(format!("lumina-test-{}", std::process::id()));
        std::fs::create_dir_all(dir.join("lumina")).unwrap();
        assert!(!is_lumina(&dir));
        std::fs::write(dir.join("pyproject.toml"), "").unwrap();
        std::fs::write(dir.join("lumina").join("server.py"), "").unwrap();
        assert!(is_lumina(&dir));
        let _ = std::fs::remove_dir_all(dir);
    }
}
