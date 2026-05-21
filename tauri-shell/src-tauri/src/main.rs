// Echo desktop shell — Tauri 2 entry point.
//
// Wraps the local Hermes Echo dashboard (http://127.0.0.1:9119/echo) in a
// native window and adds two signal sources the browser sandbox cannot
// reach:
//
//   * OS clipboard — polled on a background tokio task at CLIPBOARD_POLL_MS
//     intervals. When the text changes we POST a clipboard_copy signal
//     to the Echo dashboard API. Only the LENGTH and the first 200 chars
//     are sent; Echo does not retain raw clipboard contents by design.
//
//   * Window focus — Tauri's window event stream lets us emit
//     window_focus / window_blur signals so Echo can detect "user
//     bailed out right after the agent replied".
//
// Both routes hit the same Hermes endpoint that the Step 17a backend
// commit added:
//   POST http://127.0.0.1:9119/api/plugins/echo_signals/clipboard-signal
//
// Hermes' session token gate: the dashboard injects a session token
// into the served HTML. For now we reach the API without auth and rely
// on Hermes binding to localhost. If a future Hermes release enforces
// the token on every endpoint, this shell will need to scrape it from
// the loaded HTML — TODO in clipboard.rs once that happens.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod clipboard;

fn main() {
    echo_shell_lib::run()
}
