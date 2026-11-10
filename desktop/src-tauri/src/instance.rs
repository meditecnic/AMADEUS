//! Instance preflight is independent of Tauri so it can be tested without a WebView.
use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};
#[cfg(windows)]
use std::os::windows::fs::OpenOptionsExt;

// Keep this handle alive until the desktop exits. The file itself may remain on disk.
pub(crate) struct InstanceLock {
    _file: Option<File>,
}

pub(crate) fn acquire_instance_lock(data_dir: &Path) -> io::Result<InstanceLock> {
    fs::create_dir_all(data_dir)?;
    #[cfg(windows)]
    let file = Some(fs::OpenOptions::new().read(true).write(true).create(true)
        .truncate(false).share_mode(0).open(data_dir.join("desktop.lock"))?);
    #[cfg(not(windows))]
    let file = None;
    Ok(InstanceLock { _file: file })
}

// Logs and the instance lock share one root, including isolated DATA_DIR runs.
pub(crate) fn backend_runtime_dir(data_override: Option<&Path>, default_root: &Path) -> PathBuf {
    data_override.filter(|path| !path.as_os_str().is_empty())
        .unwrap_or(default_root).to_path_buf()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lock_preflight_does_not_create_data_or_webview_profile() {
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let root = std::env::temp_dir().join(format!("amadeus-preflight-{}-{nonce}", std::process::id()));
        let lock = acquire_instance_lock(&root).unwrap();
        assert!(root.is_dir());
        assert!(!root.join("data").exists());
        assert!(!root.join("EBWebView").exists());
        drop(lock);
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn rejected_second_instance_keeps_the_original_runtime_lock() {
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let root = std::env::temp_dir().join(format!("amadeus-second-{}-{nonce}", std::process::id()));
        let first = acquire_instance_lock(&root).unwrap();
        let error = match acquire_instance_lock(&root) {
            Ok(_) => panic!("a second instance acquired the live instance lock"),
            Err(error) => error,
        };
        assert_eq!(error.raw_os_error(), Some(32));
        assert!(!root.join("EBWebView").exists());
        drop(first);
        let next = acquire_instance_lock(&root).unwrap();
        drop(next);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn runtime_paths_follow_explicit_data_directories() {
        let default = Path::new("default-app-root");
        let first = backend_runtime_dir(Some(Path::new("isolated-a/data")), default);
        let second = backend_runtime_dir(Some(Path::new("isolated-b/data")), default);
        assert_eq!(first.join("logs/backend-stdout.log"), Path::new("isolated-a/data/logs/backend-stdout.log"));
        assert_eq!(first.join("logs/backend-stderr.log"), Path::new("isolated-a/data/logs/backend-stderr.log"));
        assert_eq!(first.join("logs/backend-startup.log"), Path::new("isolated-a/data/logs/backend-startup.log"));
        assert_eq!(first.join("desktop.lock"), Path::new("isolated-a/data/desktop.lock"));
        assert_ne!(first, second);
    }

    #[test]
    fn runtime_paths_stay_at_default_root_without_override() {
        let default = Path::new("default-app-root");
        for value in [None, Some(Path::new(""))] {
            let root = backend_runtime_dir(value, default);
            assert_eq!(root.join("logs"), default.join("logs"));
            assert_eq!(root.join("desktop.lock"), default.join("desktop.lock"));
            assert!(!root.join("logs").starts_with(default.join("data")));
        }
    }

}
