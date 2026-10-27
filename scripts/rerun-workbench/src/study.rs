//! Offline studies use the canonical RoomEQ workflow; presentation never runs DSP.
use roomeq_model::DesignerLimits;
use roomeq_workflow::{CallbackAction, RoomOptimizationProgress};
use serde_json::{Value, json};
use sotf_rerun_viewer::{SolverResponse, ViewerSession};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
};

pub struct Study {
    pub state: Arc<Mutex<Value>>,
    cancel: Arc<AtomicBool>,
    source: Value,
    source_dir: PathBuf,
    output: PathBuf,
    sequence: u64,
    worker: Option<std::thread::JoinHandle<()>>,
}

fn write_new(path: &Path, value: &Value) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut file, value).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())
}

impl Study {
    pub fn new(source: Value, source_dir: PathBuf, output: PathBuf) -> Self {
        Self {
            state: Arc::new(Mutex::new(json!({"status":"idle","results_current":false,
            "iteration":0,"progress":0.0,"loss":null,"response":null,"revision":null,
            "error":null,"callback_count":0,"id":null}))),
            cancel: Arc::new(AtomicBool::new(false)),
            source,
            source_dir,
            output,
            sequence: 0,
            worker: None,
        }
    }

    pub fn snapshot(&self, revision: &str) -> Result<Value, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "Study state unavailable")?
            .clone();
        state["results_current"] =
            json!(state["status"] == "completed" && state["revision"] == revision);
        Ok(state)
    }

    pub fn running(&self) -> Result<bool, String> {
        Ok(self.state.lock().map_err(|_| "Study state unavailable")?["status"] == "running")
    }

    pub fn cancel(&self) -> Result<(), String> {
        let state = self.state.lock().map_err(|_| "Study state unavailable")?;
        if state["status"] != "running" {
            return Err("No running study to cancel".into());
        }
        self.cancel.store(true, Ordering::Release);
        Ok(())
    }

    pub fn start(
        &mut self,
        limits: DesignerLimits,
        revision: String,
        session: Option<Arc<ViewerSession>>,
    ) -> Result<(), String> {
        if self.running()? {
            return Err("A study is already running".into());
        }
        if let Some(worker) = self.worker.take() {
            worker
                .join()
                .map_err(|_| "Previous study worker panicked")?;
        }
        self.sequence += 1;
        let directory = self.output.join(format!("study-{:04}", self.sequence));
        fs::create_dir(&directory).map_err(|e| format!("Study requires fresh output: {e}"))?;
        let directory = directory.canonicalize().map_err(|e| e.to_string())?;
        let mut source = self.source.clone();
        source["optimizer"]["min_freq"] = json!(limits.min_freq);
        source["optimizer"]["max_freq"] = json!(limits.max_freq);
        source["optimizer"]["min_db"] = json!(limits.min_db);
        source["optimizer"]["max_db"] = json!(limits.max_db);
        // The pilot is deliberately a bounded offline DE study, without remote resources.
        if source["optimizer"]["algorithm"] != "autoeq:de"
            || source["optimizer"]["num_filters"]
                .as_u64()
                .is_none_or(|n| n > 8)
            || source["optimizer"]["max_iter"]
                .as_u64()
                .is_none_or(|n| n > 2000)
            || source["optimizer"]["population"]
                .as_u64()
                .is_none_or(|n| n > 64)
            || source["optimizer"]
                .get("cea2034_correction")
                .is_some_and(|v| !v.is_null())
        {
            return Err(
                "Pilot requires local autoeq:de, at most 8 filters/2000 iterations/64 population"
                    .into(),
            );
        }
        let mut inputs = Vec::new();
        freeze_paths(&mut source, &self.source_dir, &directory, &mut inputs)?;
        let config = roomeq_workflow::deserialize_room_config_strict(source.clone())
            .map_err(|e| e.to_string())?;
        write_new(&directory.join("config.json"), &source)?;
        let identity = json!({"designer_revision":revision,"config":source,"inputs":inputs});
        let identity_hash =
            sotf_dev_api::canonical_json_hash(&identity).map_err(|e| e.to_string())?;
        write_new(
            &directory.join("identity.json"),
            &json!({"study_hash":identity_hash,"identity":identity}),
        )?;
        let callback_file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(directory.join("callbacks.jsonl"))
            .map_err(|e| e.to_string())?;
        if let Some(session) = &session {
            session.begin_solver_study(&identity_hash)?;
        }
        self.cancel.store(false, Ordering::Release);
        *self.state.lock().map_err(|_| "Study state unavailable")? = json!({"status":"running",
            "results_current":false,"iteration":0,"progress":0.0,"loss":null,"response":null,
            "revision":revision,"error":null,"callback_count":0,"study_hash":identity_hash,
            "output":directory,"id":directory.file_name().and_then(|v| v.to_str())});
        let state = self.state.clone();
        let cancel = self.cancel.clone();
        let spawn = std::thread::Builder::new().name("offline-room-study".into()).spawn(move || {
            let errors = Arc::new(Mutex::new(None));
            let observer_errors = errors.clone();
            let observer_state = state.clone();
            let observer_cancel = cancel.clone();
            let observer_session = session.clone();
            let mut file = callback_file;
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                roomeq_workflow::optimize_room(&config, 48_000.0, Some(Box::new(move |p| {
                    let result = record_callback(p, &observer_state, &mut file, observer_session.as_deref());
                    if let Err(error) = result {
                        *observer_errors.lock().unwrap_or_else(|e| e.into_inner()) = Some(error);
                        return CallbackAction::Stop;
                    }
                    if observer_cancel.load(Ordering::Acquire) { CallbackAction::Stop } else { CallbackAction::Continue }
                })), Some(&directory.join("artifacts")))
            }));
            let finish = || -> Result<(), String> {
                if let Some(error) = errors.lock().map_err(|_| "Callback error state unavailable")?.take() { return Err(error); }
                if cancel.load(Ordering::Acquire) { return Ok(()); }
                let result = result.map_err(|_| "Offline solver panicked".to_string())?.map_err(|e| e.to_string())?;
                if let Some(session) = &session {
                    for (channel, result) in &result.channel_results {
                        if result.initial_curve.freq != result.final_curve.freq { return Err("Final response grids differ".into()); }
                        session.log_final_response(channel, &result.final_curve.freq.to_vec(),
                            &result.initial_curve.spl.to_vec(), &result.final_curve.spl.to_vec())?;
                    }
                }
                let curves: std::collections::BTreeMap<_, _> = result.channel_results.iter().map(|(channel, result)| (channel,
                    json!({"frequency_hz":result.final_curve.freq.to_vec(),"measurement_db":result.initial_curve.spl.to_vec(),
                        "predicted_db":result.final_curve.spl.to_vec(),"pre_score":result.pre_score,"post_score":result.post_score}))).collect();
                write_new(&directory.join("result.json"), &json!({"dsp":result.to_dsp_chain_output(),"curves":curves,
                    "combined_pre_score":result.combined_pre_score,"combined_post_score":result.combined_post_score}))?;
                let mut guard = state.lock().map_err(|_| "Study state unavailable")?;
                guard["final_responses"] = json!(curves);
                Ok(())
            };
            let failure = finish().err();
            let mut guard = state.lock().unwrap_or_else(|e| e.into_inner());
            guard["status"] = json!(if failure.is_some() { "failed" } else if cancel.load(Ordering::Acquire) { "cancelled" } else { "completed" });
            guard["error"] = json!(failure);
            if guard["status"] == "completed" { guard["progress"] = json!(1.0); }
            if let Some(session) = session {
                if let Err(error) = session.log_solver_progress("", guard["iteration"].as_u64().unwrap_or(0) as usize, None, guard["progress"].as_f64().unwrap_or(0.0), guard["status"].as_str().unwrap_or("failed"), guard["error"].as_str(), None).and_then(|_| session.flush()) {
                    guard["status"] = json!("failed"); guard["error"] = json!(error);
                }
            }
            if let Err(error) = write_new(&directory.join("terminal.json"), &guard) {
                guard["status"] = json!("failed"); guard["error"] = json!(error);
            }
        });
        match spawn {
            Ok(worker) => self.worker = Some(worker),
            Err(error) => {
                let mut state = self.state.lock().map_err(|_| "Study state unavailable")?;
                state["status"] = json!("failed");
                state["error"] = json!(error.to_string());
                return Err(error.to_string());
            }
        }
        Ok(())
    }
}

impl Drop for Study {
    fn drop(&mut self) {
        self.cancel.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn record_callback(
    p: &RoomOptimizationProgress,
    state: &Mutex<Value>,
    file: &mut File,
    session: Option<&ViewerSession>,
) -> Result<(), String> {
    let count = state.lock().map_err(|_| "Study state unavailable")?["callback_count"]
        .as_u64()
        .unwrap_or(0)
        + 1;
    let response = p.response.as_ref().map(|r| json!({"channel":p.current_speaker,"frequency_hz":r.frequency_hz,
        "prepared_measurement_db":r.prepared_measurement_db,"target_db":r.target_db,"filter_db":r.filter_db,"predicted_db":r.predicted_db}));
    let loss = p
        .response
        .is_some()
        .then_some(p.loss)
        .filter(|v| v.is_finite());
    let event = json!({"callback_index":count,"iteration":p.iteration,"progress":p.overall_progress,
        "loss":loss,"response":response,"channel":p.current_speaker,"stage":format!("{:?}/{:?}",p.step_id,p.step_status),"message":p.message});
    let bytes = serde_json::to_vec(&event).map_err(|e| e.to_string())?;
    if file.metadata().map_err(|e| e.to_string())?.len() + bytes.len() as u64 + 1
        > 512 * 1024 * 1024
    {
        return Err("Callback recording reached the 512 MiB study budget; study stopped without dropping history".into());
    }
    file.write_all(&bytes)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_data())
        .map_err(|e| e.to_string())?;
    {
        let mut guard = state.lock().map_err(|_| "Study state unavailable")?;
        guard["callback_count"] = json!(count);
        guard["progress"] = json!(p.overall_progress.clamp(0.0, 1.0));
        if let Some(response) = response {
            guard["iteration"] = json!(p.iteration);
            guard["loss"] = json!(loss);
            guard["response"] = response;
        }
    }
    if let Some(session) = session {
        session.log_solver_progress(
            &p.current_speaker,
            p.iteration,
            loss,
            p.overall_progress,
            event["stage"].as_str().unwrap_or("optimizer"),
            p.message.as_deref(),
            p.response.as_ref().map(|r| SolverResponse {
                frequency_hz: &r.frequency_hz,
                prepared_measurement_db: &r.prepared_measurement_db,
                target_db: &r.target_db,
                filter_db: &r.filter_db,
                predicted_db: &r.predicted_db,
            }),
        )?;
    }
    Ok(())
}

fn freeze_paths(
    value: &mut Value,
    base: &Path,
    directory: &Path,
    inputs: &mut Vec<Value>,
) -> Result<(), String> {
    match value {
        Value::Object(map) => {
            if let Some(path) = map.get_mut("path") {
                if inputs.len() >= 32 {
                    return Err("Pilot supports at most 32 local inputs".into());
                }
                let source = path
                    .as_str()
                    .ok_or("Measurement path must be a local string")?;
                let source = base
                    .join(source)
                    .canonicalize()
                    .map_err(|e| e.to_string())?;
                let mut bytes = Vec::new();
                File::open(&source)
                    .map_err(|e| e.to_string())?
                    .take(64 * 1024 * 1024 + 1)
                    .read_to_end(&mut bytes)
                    .map_err(|e| e.to_string())?;
                let prior_bytes: u64 = inputs.iter().filter_map(|i| i["bytes"].as_u64()).sum();
                if bytes.len() > 64 * 1024 * 1024
                    || prior_bytes + bytes.len() as u64 > 128 * 1024 * 1024
                {
                    return Err("Study inputs exceed 64 MiB per file or 128 MiB total".into());
                }
                let extension = source.extension().and_then(|e| e.to_str()).unwrap_or("bin");
                let frozen = directory.join(format!("input-{}.{extension}", inputs.len()));
                let mut file = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&frozen)
                    .map_err(|e| e.to_string())?;
                file.write_all(&bytes)
                    .and_then(|_| file.sync_all())
                    .map_err(|e| e.to_string())?;
                inputs.push(json!({"source":source,"sha256":crate::digest(&bytes),"frozen":frozen,"bytes":bytes.len()}));
                *path = json!(frozen);
            }
            for (key, value) in map {
                if key != "path" {
                    freeze_paths(value, base, directory, inputs)?;
                }
            }
        }
        Value::Array(values) => {
            for value in values {
                freeze_paths(value, base, directory, inputs)?;
            }
        }
        _ => {}
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wait(study: &Study) -> Value {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
        while study.running().unwrap() {
            assert!(
                std::time::Instant::now() < deadline,
                "study did not terminate"
            );
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        study.snapshot("revision-1").unwrap()
    }

    #[test]
    fn real_offline_callbacks_final_output_cancellation_and_failure() {
        let viewer_source = include_str!("../../../sotf/crates/sotf-rerun-viewer/src/lib.rs");
        let reset = viewer_source
            .split("pub fn begin_solver_study")
            .nth(1)
            .unwrap()
            .split("pub fn log_final_response")
            .next()
            .unwrap();
        assert!(reset.contains("set_time_sequence(\"callback\""));
        assert!(reset.contains(".log(\"solver/channels\""));
        assert!(
            !reset.contains("log_static(\"solver/channels\""),
            "static clears hide temporal response histories"
        );
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap();
        let input = root.join("autoeq/data_tests/roomeq/measured/2.0_8361a/L.csv");
        let source_bytes = fs::read(&input).unwrap();
        let directory = std::env::temp_dir().join(format!(
            "rerun-offline-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&directory).unwrap();
        let source = json!({"version":"3.0.0","speakers":{"L":{"path":input,"name":"L"}},
            "optimizer":{"algorithm":"autoeq:de","seed":91827,"num_filters":2,"max_iter":400,
                "population":12,"loss_type":"flat","min_freq":20.0,"max_freq":300.0,
                "min_q":0.5,"max_q":3.0,"min_db":-12.0,"max_db":0.0,
                "psychoacoustic":false,"asymmetric_loss":false}});
        let mut study = Study::new(source, root.into(), directory.clone());
        let limits = DesignerLimits {
            min_freq: 20.0,
            max_freq: 300.0,
            min_db: -12.0,
            max_db: 0.0,
        };
        assert!(study.cancel().is_err());
        study.start(limits, "revision-1".into(), None).unwrap();
        assert!(study.start(limits, "revision-1".into(), None).is_err());
        let completed = wait(&study);
        assert_eq!(completed["status"], "completed", "{completed}");
        assert_eq!(completed["results_current"], true);
        assert_eq!(
            study.snapshot("different-revision").unwrap()["results_current"],
            false
        );
        let callbacks: Vec<Value> =
            fs::read_to_string(directory.join("study-0001/callbacks.jsonl"))
                .unwrap()
                .lines()
                .map(|line| serde_json::from_str(line).unwrap())
                .collect();
        assert_eq!(
            callbacks.len() as u64,
            completed["callback_count"].as_u64().unwrap()
        );
        assert!(
            callbacks
                .iter()
                .filter(|event| event["response"].is_null())
                .all(|event| event["loss"].is_null())
        );
        let responses: Vec<_> = callbacks
            .iter()
            .filter_map(|event| event["response"]["predicted_db"].as_array())
            .collect();
        assert!(responses.len() > 2 && responses.windows(2).any(|pair| pair[0] != pair[1]));
        assert!(
            completed["final_responses"]["L"]["predicted_db"]
                .as_array()
                .is_some()
        );
        assert!(write_new(&directory.join("study-0001/result.json"), &json!({})).is_err());
        assert_eq!(fs::read(&input).unwrap(), source_bytes);
        study.start(limits, "revision-1".into(), None).unwrap();
        study.cancel().unwrap();
        assert_eq!(wait(&study)["status"], "cancelled");
        study.source["optimizer"]["min_q"] = json!(-1.0);
        study.start(limits, "revision-1".into(), None).unwrap();
        let failed = wait(&study);
        assert_eq!(failed["status"], "failed");
        assert!(failed["error"].is_string());
        study.source["optimizer"]["min_q"] = json!(0.5);
        study.start(limits, "revision-1".into(), None).unwrap();
        drop(study);
        let stopped: Value =
            serde_json::from_slice(&fs::read(directory.join("study-0004/terminal.json")).unwrap())
                .unwrap();
        assert_eq!(stopped["status"], "cancelled");
        fs::remove_dir_all(directory).unwrap();
    }
}
