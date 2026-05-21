// Echo desktop shell — clipboard polling + HTTP reporting.
//
// Polls the OS clipboard at a fixed interval; when the visible text
// changes we POST a clipboard_copy signal to Echo's dashboard API.
// Privacy posture: we send only the *length* and the first 200 chars
// (used by Echo's backend for analytics on what kinds of content the
// agent's output produces). Full text is never persisted server-side
// — see plugins/echo_signals/dashboard/plugin_api.py for the storage
// contract.
//
// The poll interval is intentionally generous (2s). Aggressive
// polling burns battery on macOS and shows up in Activity Monitor
// disproportionately for a "background signal collector" reputation.

use serde::Serialize;
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_clipboard_manager::ClipboardExt;

const POLL_INTERVAL: Duration = Duration::from_secs(2);
const TEXT_PREVIEW_LIMIT: usize = 200;
const API_PATH: &str = "/api/plugins/echo_signals/clipboard-signal";
const API_BASE: &str = "http://127.0.0.1:9119";

#[derive(Serialize)]
struct ClipboardSignal<'a> {
    event_type: &'a str,
    text: Option<String>,
    text_length: Option<usize>,
}

pub async fn watch_clipboard(app: AppHandle) {
    let mut last_seen: Option<String> = None;
    let url = format!("{}{}", API_BASE, API_PATH);
    let http = reqwest::Client::new();

    loop {
        tokio::time::sleep(POLL_INTERVAL).await;

        let current = match app.clipboard().read_text() {
            Ok(text) => text,
            Err(_) => continue, // clipboard may be empty or contain non-text data
        };

        if current.is_empty() {
            continue;
        }
        if last_seen.as_deref() == Some(current.as_str()) {
            continue;
        }
        last_seen = Some(current.clone());

        // Truncate to the preview limit so the body stays bounded.
        let preview: String = current.chars().take(TEXT_PREVIEW_LIMIT).collect();
        let payload = ClipboardSignal {
            event_type: "clipboard_copy",
            text_length: Some(current.chars().count()),
            text: Some(preview),
        };

        let _ = http.post(&url).json(&payload).send().await;
    }
}

/// Manual report invoked from JS — useful when the webview wants to
/// signal "the user just hit my /copy button" without going through
/// the OS clipboard polling.
#[tauri::command]
pub fn report_clipboard_text(text: String) -> Result<(), String> {
    let url = format!("{}{}", API_BASE, API_PATH);
    let preview: String = text.chars().take(TEXT_PREVIEW_LIMIT).collect();
    let payload = ClipboardSignal {
        event_type: "clipboard_copy",
        text_length: Some(text.chars().count()),
        text: Some(preview),
    };
    tokio::spawn(async move {
        let _ = reqwest::Client::new().post(&url).json(&payload).send().await;
    });
    Ok(())
}
