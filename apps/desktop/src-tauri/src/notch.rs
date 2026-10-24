//! The notch: while a meeting is being recorded, a black island grows out of the top center of the screen with
//! the last three lines of the transcript. On a Mac with a notch it comes out of the notch itself (same black, so
//! there is no seam); elsewhere it drops from the top edge. Settings → "Show the live transcript in the notch"
//! turns it off.
//!
//! The same idea as the notch of brain: the growing is done by the page (`ui/notch.js`, CSS transitions), not by
//! resizing the window frame by frame. This side only polls the server (`/api/notch`), puts a transparent
//! window at the top center, big enough for the island to grow inside it, and closes it after the island shrank.
//! The page has no network access; everything reaches it through events.

use crate::Backend;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::Duration;
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, WebviewUrl, WebviewWindowBuilder,
};

/// Width of the physical notch (14"/16" MacBooks are 185–200 pt). The island is wider, so it covers it.
const NOTCH_W: f64 = 190.0;
/// Height of the header row where there is no notch: a menu bar high.
const HEADER_H: f64 = 34.0;
/// Room around the island inside the window, for the spring overshoot of the animation.
const PAD: f64 = 20.0;
/// The island: wide enough for the notch plus a few words on each side, tall enough for three lines of text.
const ISLAND: (f64, f64) = (440.0, 74.0);
/// How long the island needs to finish growing or closing (the CSS transition is .6 s).
const SETTLE: Duration = Duration::from_millis(720);

struct Live {
    shown: bool,
    /// The last payload from the server, for a page that loads after it was sent.
    last: Value,
}

fn live() -> &'static Mutex<Live> {
    static LIVE: Mutex<Live> = Mutex::new(Live {
        shown: false,
        last: Value::Null,
    });
    &LIVE
}

static BACKEND: OnceLock<Arc<Backend>> = OnceLock::new();

/// A line in desktop.log (next to the server's own).
fn note(line: impl AsRef<str>) {
    if let Some(b) = BACKEND.get() {
        b.log(line.as_ref());
    }
}

fn generation() -> &'static AtomicU64 {
    static GEN: AtomicU64 = AtomicU64::new(0);
    &GEN
}

struct Layout {
    origin_x: f64,
    top: f64,
    screen_w: f64,
    /// A physical notch is here: the island grows out of it.
    notch: bool,
    header: f64,
}

/// What the page needs to size and style the island.
fn info(lay: &Layout) -> Value {
    json!({
        "os": std::env::consts::OS,
        "notch": lay.notch,
        "notch_w": NOTCH_W,
        "header": lay.header,
        "size": [ISLAND.0, lay.header + ISLAND.1],
    })
}

fn window(app: &AppHandle) -> Result<tauri::WebviewWindow, String> {
    if let Some(w) = app.get_webview_window("notch") {
        return Ok(w);
    }
    let w = WebviewWindowBuilder::new(app, "notch", WebviewUrl::App("notch.html".into()))
        .title("Lumina")
        .inner_size(ISLAND.0 + 2.0 * PAD, ISLAND.1 + HEADER_H + PAD)
        .decorations(false)
        .transparent(true)
        // resizable on purpose: GTK ignores set_size on a non-resizable window, and the island must
        // shrink to its content (the margin around it is transparent and keeps the edge grips away)
        .resizable(true)
        .maximizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .focused(false)
        .visible(false)
        .shadow(false)
        .visible_on_all_workspaces(true)
        .build()
        .map_err(|e| e.to_string())?;
    lift_over_menu_bar(app, &w);
    Ok(w)
}

/// On macOS the island has to paint *over* the menu bar, in the notch's own black. Below the bar it
/// reads as a square stuck underneath.
#[cfg(target_os = "macos")]
fn lift_over_menu_bar(app: &AppHandle, window: &tauri::WebviewWindow) {
    let app = app.clone();
    let window = window.clone();
    let _ = app.run_on_main_thread(move || unsafe {
        use objc2::runtime::AnyObject;
        use objc2::{class, msg_send};
        let ptr = match window.ns_window() {
            Ok(p) if !p.is_null() => p,
            _ => return,
        };
        let ns_window = ptr as *mut AnyObject;
        // NSStatusWindowLevel (25) sits just above the menu bar.
        let _: () = msg_send![ns_window, setLevel: 25isize];
        let current: usize = msg_send![ns_window, collectionBehavior];
        // CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary
        let behavior = current | 1usize | (1 << 4) | (1 << 6) | (1 << 8);
        let _: () = msg_send![ns_window, setCollectionBehavior: behavior];
        let _: () = msg_send![ns_window, setOpaque: false];
        let _: () = msg_send![ns_window, setHasShadow: false];
        let _: () = msg_send![ns_window, setHidesOnDeactivate: false];
        let color: *mut AnyObject = msg_send![class!(NSColor), clearColor];
        let _: () = msg_send![ns_window, setBackgroundColor: color];
    });
}

#[cfg(not(target_os = "macos"))]
fn lift_over_menu_bar(_app: &AppHandle, _window: &tauri::WebviewWindow) {}

/// Height of the notch of the menu-bar screen in points, 0 when it has none (or before macOS 12).
#[cfg(target_os = "macos")]
fn notch_height() -> f64 {
    use objc2::encode::{Encode, Encoding};
    use objc2::runtime::{AnyObject, Bool};
    use objc2::{class, msg_send, sel};

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct Insets {
        top: f64,
        left: f64,
        bottom: f64,
        right: f64,
    }
    // SAFETY: NSEdgeInsets is four CGFloats (f64 on every supported Mac), in this order.
    unsafe impl Encode for Insets {
        const ENCODING: Encoding = Encoding::Struct(
            "NSEdgeInsets",
            &[
                Encoding::Double,
                Encoding::Double,
                Encoding::Double,
                Encoding::Double,
            ],
        );
    }

    unsafe {
        let screens: *mut AnyObject = msg_send![class!(NSScreen), screens];
        if screens.is_null() {
            return 0.0;
        }
        // the first screen is the one with the menu bar
        let first: *mut AnyObject = msg_send![screens, firstObject];
        if first.is_null() {
            return 0.0;
        }
        let ok: Bool = msg_send![first, respondsToSelector: sel!(safeAreaInsets)];
        if !ok.as_bool() {
            return 0.0;
        }
        let insets: Insets = msg_send![first, safeAreaInsets];
        insets.top.max(0.0)
    }
}

#[cfg(not(target_os = "macos"))]
fn notch_height() -> f64 {
    0.0
}

/// The screen to grow on: the main one.
fn layout(app: &AppHandle) -> Option<Layout> {
    let m = app.primary_monitor().ok().flatten().or_else(|| {
        app.get_webview_window("notch")
            .and_then(|w| w.current_monitor().ok().flatten())
    })?;
    let scale = m.scale_factor();
    let origin_x = m.position().x as f64 / scale;
    let screen_w = m.size().width as f64 / scale;
    // macOS: the physical top, so the island paints over the menu bar. Elsewhere: below whatever
    // panel the system already put at the top.
    let top = {
        #[cfg(target_os = "macos")]
        {
            m.position().y as f64 / scale
        }
        #[cfg(not(target_os = "macos"))]
        {
            m.work_area().position.y as f64 / scale
        }
    };
    let on_main_screen = m.position().x == 0 && m.position().y == 0;
    let nh = if on_main_screen { notch_height() } else { 0.0 };
    Some(Layout {
        origin_x,
        top,
        screen_w,
        notch: nh > 0.0,
        header: if nh > 0.0 { nh.max(30.0) } else { HEADER_H },
    })
}

/// Runs `f` on the main thread and waits a moment for it (window calls belong there).
fn on_main<F: FnOnce() + Send + 'static>(app: &AppHandle, f: F) {
    let (tx, rx) = std::sync::mpsc::sync_channel(1);
    let _ = app.run_on_main_thread(move || {
        f();
        let _ = tx.send(());
    });
    let _ = rx.recv_timeout(Duration::from_millis(800));
}

/// Puts the window (the island plus its padding) centered at the top of the screen and shows it.
fn place(app: &AppHandle, lay: &Layout) {
    let (ww, wh) = (ISLAND.0 + 2.0 * PAD, lay.header + ISLAND.1 + PAD);
    let (x, y) = (lay.origin_x + (lay.screen_w - ww) / 2.0, lay.top);
    let app2 = app.clone();
    on_main(app, move || {
        let Some(win) = app2.get_webview_window("notch") else {
            return;
        };
        let _ = win.set_size(LogicalSize::new(ww, wh));
        let _ = win.set_position(LogicalPosition::new(x, y));
        let _ = win.show();
    });
}

/// Grows the island and fills it with `state`.
fn open(app: &AppHandle, state: &Value) {
    generation().fetch_add(1, Ordering::SeqCst);
    if let Err(e) = window(app) {
        return note(format!("notch: could not create the window: {e}"));
    }
    let Some(lay) = layout(app) else {
        return note("notch: no screen to anchor to");
    };
    let mut payload = info(&lay);
    payload["state"] = state.clone();
    if let Ok(mut l) = live().lock() {
        l.shown = true;
        l.last = payload.clone();
    }
    place(app, &lay);
    let _ = app.emit_to("notch", "notch-show", payload);
    note("notch shown");
}

/// New words for an island that is already open.
fn update(app: &AppHandle, state: &Value) {
    if let Ok(mut l) = live().lock() {
        l.last["state"] = state.clone();
    }
    let _ = app.emit_to("notch", "notch-update", state.clone());
}

/// Shrinks the island back into the notch and closes its window once it has settled.
fn dismiss(app: &AppHandle) {
    let was = live()
        .lock()
        .map(|mut l| std::mem::replace(&mut l.shown, false))
        .unwrap_or(false);
    if !was {
        return;
    }
    let gen = generation().fetch_add(1, Ordering::SeqCst) + 1;
    let _ = app.emit_to("notch", "notch-hide", json!({}));
    let app = app.clone();
    thread::spawn(move || {
        thread::sleep(SETTLE);
        if generation().load(Ordering::SeqCst) != gen {
            return; // it was opened again meanwhile
        }
        let app2 = app.clone();
        on_main(&app, move || {
            if let Some(w) = app2.get_webview_window("notch") {
                let _ = w.destroy();
            }
        });
        note("notch hidden");
    });
}

/// The page asks for the current state when it finishes loading (it may load after the first event).
pub fn state_json() -> Value {
    live().lock().map(|l| l.last.clone()).unwrap_or(Value::Null)
}

fn fetch(backend: &Backend) -> Option<Value> {
    let body = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(2))
        .build()
        .get(&format!("http://127.0.0.1:{}/api/notch", backend.port))
        .call()
        .ok()?
        .into_string()
        .ok()?;
    serde_json::from_str(&body).ok()
}

/// True when the server says a meeting is being recorded and the setting is on.
fn wanted(state: &Value) -> bool {
    state["enabled"].as_bool() == Some(true) && state["recording"].as_bool() == Some(true)
}

/// Polls the server and opens, updates or closes the island. Runs for the life of the app.
pub fn watch(app: AppHandle, backend: Arc<Backend>) {
    let _ = BACKEND.set(Arc::clone(&backend));
    thread::spawn(move || {
        let mut open_now = false;
        loop {
            thread::sleep(Duration::from_millis(if open_now { 450 } else { 1200 }));
            match fetch(&backend) {
                Some(state) if wanted(&state) => {
                    if open_now {
                        update(&app, &state);
                    } else {
                        open(&app, &state);
                        open_now = true;
                    }
                }
                Some(_) | None => {
                    if open_now {
                        dismiss(&app);
                        open_now = false;
                    }
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_recording_with_the_setting_on_opens_the_island() {
        assert!(wanted(&json!({"enabled": true, "recording": true})));
        assert!(!wanted(&json!({"enabled": false, "recording": true})));
        assert!(!wanted(&json!({"enabled": true, "recording": false})));
        assert!(!wanted(&json!({})));
    }

    #[test]
    fn the_island_is_taller_than_the_header() {
        let lay = Layout {
            origin_x: 0.0,
            top: 0.0,
            screen_w: 1512.0,
            notch: true,
            header: 38.0,
        };
        let i = info(&lay);
        assert_eq!(i["size"][1].as_f64(), Some(38.0 + ISLAND.1));
        assert_eq!(i["notch"], json!(true));
    }
}
