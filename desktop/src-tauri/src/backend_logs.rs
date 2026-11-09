//! Bounded, best-effort logging; never let a full/broken log block pipe draining.
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;

pub(crate) const MAX_BYTES: u64 = 5 * 1024 * 1024;
const NAMES: [&str; 3] = ["backend-stdout.log", "backend-stderr.log", "backend-startup.log"];

pub(crate) fn rotate(logs: &Path) {
    let _ = fs::create_dir_all(logs);
    for name in NAMES {
        for generation in (1..=3).rev() {
            let source = logs.join(if generation == 1 { name.into() } else { format!("{name}.{}", generation - 1) });
            let target = logs.join(format!("{name}.{generation}"));
            let _ = fs::remove_file(&target);
            let _ = fs::rename(source, target);
        }
        // Also bound oversized files left by versions without a size limit.
        for suffix in ["", ".1", ".2", ".3"] {
            if let Ok(file) = OpenOptions::new().write(true).open(logs.join(format!("{name}{suffix}"))) {
                if file.metadata().map(|m| m.len() > MAX_BYTES).unwrap_or(false) {
                    let _ = file.set_len(MAX_BYTES);
                }
            }
        }
    }
}

pub(crate) struct CappedLog {
    file: Option<File>,
    remaining: u64,
}

impl CappedLog {
    pub(crate) fn open(path: &Path) -> Self {
        let file = OpenOptions::new().create(true).append(true).open(path).ok();
        let size = file.as_ref().and_then(|f| f.metadata().ok()).map(|m| m.len()).unwrap_or(0);
        Self { file, remaining: MAX_BYTES.saturating_sub(size) }
    }
}

impl Write for CappedLog {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let count = bytes.len().min(self.remaining as usize);
        self.remaining -= count as u64;
        if let Some(file) = self.file.as_mut() {
            if file.write_all(&bytes[..count]).is_err() {
                self.file = None;
            }
        }
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> { Ok(()) }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn root() -> std::path::PathBuf {
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let path = std::env::temp_dir().join(format!("amadeus-logs-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn rotation_keeps_three_previous_runs_for_each_stream() {
        let root = root();
        for run in 0..6 {
            rotate(&root);
            for name in NAMES { fs::write(root.join(name), run.to_string()).unwrap(); }
        }
        for name in NAMES {
            for generation in 1..=3 {
                assert_eq!(fs::read_to_string(root.join(format!("{name}.{generation}"))).unwrap(), (5 - generation).to_string());
            }
            assert!(!root.join(format!("{name}.4")).exists());
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn full_logs_still_consume_input_and_remain_bounded_after_reopen() {
        let root = root();
        let path = root.join(NAMES[0]);
        let mut log = CappedLog::open(&path);
        let input = vec![b'x'; MAX_BYTES as usize + 19];
        io::copy(&mut &input[..], &mut log).unwrap();
        drop(log);
        CappedLog::open(&path).write_all(b"discard").unwrap();
        assert_eq!(fs::metadata(&path).unwrap().len(), MAX_BYTES);
        fs::write(&path, input).unwrap();
        rotate(&root);
        assert_eq!(fs::metadata(root.join(format!("{}.1", NAMES[0]))).unwrap().len(), MAX_BYTES);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rotation_and_open_failures_do_not_prevent_draining() {
        let root = root();
        fs::create_dir(root.join(format!("{}.1", NAMES[0]))).unwrap();
        fs::write(root.join(NAMES[0]), "previous").unwrap();
        rotate(&root);
        CappedLog::open(&root).write_all(b"discard").unwrap();
        CappedLog::open(&root.join(NAMES[0])).write_all(b"new").unwrap();
        assert!(fs::read_to_string(root.join(NAMES[0])).unwrap().ends_with("new"));
        fs::remove_dir_all(root).unwrap();
    }
}
