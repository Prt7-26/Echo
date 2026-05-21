// Echo desktop shell — library entry, callable from main.rs and from
// mobile entry points.

mod clipboard;

use tauri::Manager;

const ECHO_API: &str = "http://127.0.0.1:9119/api/plugins/echo_signals";

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_clipboard_manager::init())
        .setup(|app| {
            // Kick off the clipboard-polling background task. We pass an
            // AppHandle so it can read the clipboard and emit events
            // back into the webview if we want to surface them.
            let handle = app.handle().clone();
            tokio::spawn(async move {
                clipboard::watch_clipboard(handle).await;
            });

            // Window focus / blur — wire events for every window the app
            // creates. We only have one (label "main") at startup.
            if let Some(window) = app.get_webview_window("main") {
                let api_base = ECHO_API.to_string();
                window.on_window_event(move |event| {
                    match event {
                        tauri::WindowEvent::Focused(focused) => {
                            let event_type = if *focused { "window_focus" } else { "window_blur" };
                            let url = format!("{}/clipboard-signal", api_base);
                            let body = serde_json::json!({"event_type": event_type});
                            tokio::spawn(async move {
                                let _ = reqwest::Client::new()
                                    .post(&url)
                                    .json(&body)
                                    .send()
                                    .await;
                            });
                        }
                        _ => {}
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            clipboard::report_clipboard_text,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Echo Tauri shell");
}
