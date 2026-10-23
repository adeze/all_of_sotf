//! Bounded direct callback pipe from the existing offline solver process.
use rerun::external::egui;
use serde_json::Value;
use std::{
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex,
        mpsc::{self, Receiver, TryRecvError},
    },
    thread,
};

pub struct Config {
    pub engine: PathBuf,
    pub project: PathBuf,
    pub channels: String,
    pub output: PathBuf,
}
impl Config {
    pub fn validate(&self) -> Result<(), String> {
        if !self.engine.is_file() || !self.project.is_file() {
            return Err(
                "Offline solver executable and project must be existing local files".into(),
            );
        }
        let channels: Vec<_> = self.channels.split(',').collect();
        if channels.is_empty()
            || channels.len() > 8
            || channels.iter().enumerate().any(|(i, c)| {
                c.is_empty()
                    || c.len() > 32
                    || channels[..i].contains(c)
                    || !c
                        .chars()
                        .all(|v| v.is_ascii_alphanumeric() || v == '_' || v == '-')
            })
        {
            return Err("Select 1–8 unique solver channels".into());
        }
        if self.output.exists() || !self.output.parent().is_some_and(Path::is_dir) {
            return Err("Solver output must be a new directory under an existing parent".into());
        }
        Ok(())
    }
}

pub struct LiveSolver {
    child: Child,
    receiver: Receiver<Result<Value, String>>,
    pub completion_received: bool,
    drained: bool,
    diagnostics: Arc<Mutex<Vec<u8>>>,
}
impl LiveSolver {
    pub fn start(config: &Config, ctx: egui::Context) -> Result<Self, String> {
        config.validate()?;
        let mut child = Command::new(&config.engine)
            .arg(&config.project)
            .arg(&config.channels)
            .arg(&config.output)
            .arg("--native-stream")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| e.to_string())?;
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");
        let (sender, receiver) = mpsc::sync_channel(8);
        thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                match read_event(&mut reader) {
                    Ok(Some(event)) => {
                        if sender.send(Ok(event)).is_err() {
                            break;
                        }
                        ctx.request_repaint();
                    }
                    Ok(None) => break,
                    Err(e) => {
                        let _ = sender.send(Err(e));
                        ctx.request_repaint();
                        break;
                    }
                }
            }
            ctx.request_repaint();
        });
        // Retain bounded diagnostics while draining so a dependency cannot block the callback pipe.
        let diagnostics = Arc::new(Mutex::new(Vec::new()));
        let stderr_tail = diagnostics.clone();
        thread::spawn(move || {
            let mut reader = BufReader::new(stderr);
            let mut bytes = [0; 4096];
            while let Ok(n) = reader.read(&mut bytes) {
                if n == 0 {
                    break;
                }
                if let Ok(mut tail) = stderr_tail.lock() {
                    append_tail(&mut tail, &bytes[..n]);
                }
            }
        });
        Ok(Self {
            child,
            receiver,
            completion_received: false,
            drained: false,
            diagnostics,
        })
    }
    pub fn next(&mut self) -> Result<Option<Value>, String> {
        match self.receiver.try_recv() {
            Ok(event) => event.map(Some),
            Err(TryRecvError::Empty) => Ok(None),
            Err(TryRecvError::Disconnected) => {
                self.drained = true;
                Ok(None)
            }
        }
    }
    pub fn finished(&mut self) -> Result<bool, String> {
        if !self.drained {
            return Ok(false);
        }
        match self.child.try_wait().map_err(|e| e.to_string())? {
            None => Ok(false),
            Some(status) if !status.success() => {
                let tail = self
                    .diagnostics
                    .lock()
                    .map(|v| String::from_utf8_lossy(&v).into_owned())
                    .unwrap_or_default();
                Err(format!(
                    "Offline solver exited with {status}: {}",
                    tail.trim()
                ))
            }
            Some(_) if !self.completion_received => {
                Err("Solver exited without verified completion".into())
            }
            Some(_) => Ok(true),
        }
    }
}
impl Drop for LiveSolver {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
fn append_tail(tail: &mut Vec<u8>, bytes: &[u8]) {
    tail.extend_from_slice(bytes);
    let excess = tail.len().saturating_sub(8192);
    tail.drain(..excess);
}
fn read_event(reader: &mut impl BufRead) -> Result<Option<Value>, String> {
    const LIMIT: usize = 4 * 1024 * 1024;
    let mut bytes = Vec::new();
    let count = reader
        .take((LIMIT + 1) as u64)
        .read_until(b'\n', &mut bytes)
        .map_err(|e| e.to_string())?;
    if count == 0 {
        return Ok(None);
    }
    if count > LIMIT || bytes.last() != Some(&b'\n') {
        return Err("Solver event exceeds 4 MiB or is truncated".into());
    }
    let event: Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("Invalid solver event: {e}"))?;
    if !matches!(
        event["type"].as_str(),
        Some("chart" | "callback" | "complete")
    ) {
        return Err("Unknown solver event type".into());
    }
    Ok(Some(event))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn callback_pipe_preserves_events_and_rejects_invalid_frames() {
        let mut tail = Vec::new();
        append_tail(&mut tail, &vec![b'a'; 8192]);
        append_tail(&mut tail, b"failure");
        assert_eq!(tail.len(), 8192);
        assert!(tail.ends_with(b"failure"));
        let project = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml");
        let mut config = Config {
            engine: std::env::current_exe().unwrap(),
            project: project.clone(),
            channels: "L,R".into(),
            output: project,
        };
        assert!(config.validate().is_err()); // Never overwrite existing input/output.
        config.output =
            std::env::temp_dir().join(format!("unused-solver-study-{}", std::process::id()));
        assert!(config.validate().is_ok());
        config.channels = "L,L".into();
        assert!(config.validate().is_err());
        let mut reader = std::io::Cursor::new(
            b"{\"type\":\"chart\"}\n{\"type\":\"callback\",\"callback\":{\"callback_index\":1}}\n",
        );
        assert_eq!(read_event(&mut reader).unwrap().unwrap()["type"], "chart");
        assert_eq!(
            read_event(&mut reader).unwrap().unwrap()["callback"]["callback_index"],
            1
        );
        assert!(read_event(&mut reader).unwrap().is_none());
        for input in [
            b"{}\n".as_slice(),
            b"{\"type\":\"chart\"}".as_slice(),
            b"garbage\n".as_slice(),
        ] {
            assert!(read_event(&mut std::io::Cursor::new(input)).is_err());
        }
        assert!(read_event(&mut std::io::Cursor::new(vec![b'x'; 4 * 1024 * 1024 + 1])).is_err());
    }
}
