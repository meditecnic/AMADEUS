use std::fs::{self, File};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

struct BackendProcess(Mutex<Option<Child>>);

impl BackendProcess {
    fn stop(&self) {
        if let Some(mut child) = self.0.lock().expect("backend process lock poisoned").take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn start_backend(app: &tauri::AppHandle) -> Result<BackendProcess, Box<dyn std::error::Error>> {
    let data_dir = match std::env::var_os("AMADEUS_DATA_DIR").filter(|value| !value.is_empty()) {
        Some(path) => PathBuf::from(path),
        None => app.path().app_local_data_dir()?.join("data"),
    };
    let logs = data_dir.join("logs");
    fs::create_dir_all(&logs)?;
    let backend = app.path().resource_dir()?.join("backend/amadeus-backend.exe");
    let mut command = Command::new(backend);
    command
        .env("AMADEUS_DATA_DIR", &data_dir)
        .env("AMADEUS_PARENT_PID", std::process::id().to_string())
        .env("BACKEND_PORT", "8000")
        .env("PYTHON_DOTENV_DISABLED", "1")
        .stdin(Stdio::null())
        .stdout(File::create(logs.join("backend-stdout.log"))?)
        .stderr(File::create(logs.join("backend-stderr.log"))?);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);
    Ok(BackendProcess(Mutex::new(Some(command.spawn()?))))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::Destroyed = event {
                        if handle.webview_windows().is_empty() {
                            handle.exit(0);
                        }
                    }
                });
            }
            app.manage(start_backend(app.handle())?);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Amadeus")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(backend) = app.try_state::<BackendProcess>() {
                    backend.stop();
                }
            }
        });
}
