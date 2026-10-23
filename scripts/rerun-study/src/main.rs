//! Read-only AutoEQ exact-DE checkpoint → Rerun recording adapter.
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    error::Error,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const MAX_BYTES: u64 = 32 * 1024 * 1024;
const MAX_INPUTS: usize = 128;

#[derive(Debug, Deserialize)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
enum Fitness {
    Finite(f64),
    PositiveInfinity,
}
impl Fitness {
    fn finite(&self) -> Option<f64> {
        match self {
            Self::Finite(v) => Some(*v),
            Self::PositiveInfinity => None,
        }
    }
}

// This is a viewing projection, not a checkpoint restore implementation.
// Unused continuation fields remain bound by the original file SHA-256.
#[derive(Debug, Deserialize)]
struct Checkpoint {
    checkpoint_version: u32,
    run_identity: String,
    implementation_identity: String,
    solver_source_identity: String,
    target_identity: String,
    build_identity: String,
    configuration_fingerprint: String,
    seed: u64,
    generation: i64,
    evaluations: u64,
    population: Vec<Vec<f64>>,
    population_fitness: Vec<Fitness>,
    best_index: usize,
    best_params: Vec<f64>,
    best_fitness: Fitness,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct State {
    schema_version: u32,
    run_identity: String,
    checkpoint: Checkpoint,
    timestamp: String,
}
struct Snapshot {
    state: State,
    digest: String,
    source: String,
    terminal: Value,
    bytes: u64,
    core_digest: String,
}

impl State {
    fn validate(&self) -> Result<()> {
        let c = &self.checkpoint;
        if self.schema_version != 1 || c.checkpoint_version != 1 {
            return Err("only ExactOptimizerState v1 / DECheckpoint v1 supported".into());
        }
        if self.run_identity != c.run_identity
            || self.timestamp.trim().is_empty()
            || [
                &c.run_identity,
                &c.implementation_identity,
                &c.solver_source_identity,
                &c.target_identity,
                &c.build_identity,
                &c.configuration_fingerprint,
            ]
            .iter()
            .any(|v| v.trim().is_empty())
        {
            return Err("missing or inconsistent checkpoint identity".into());
        }
        let n = c.best_params.len();
        if c.generation < 0
            || n == 0
            || c.population.is_empty()
            || c.best_index >= c.population.len()
            || c.population.len() != c.population_fitness.len()
            || c.population
                .iter()
                .any(|p| p.len() != n || p.iter().any(|v| !v.is_finite()))
            || c.best_params.iter().any(|v| !v.is_finite())
            || c.population_fitness
                .iter()
                .chain([&c.best_fitness])
                .any(|f| f.finite().is_some_and(|v| !v.is_finite()))
        {
            return Err("invalid population, parameter or fitness arrays".into());
        }
        Ok(())
    }
    fn identity(&self) -> Value {
        let c = &self.checkpoint;
        json!({"run_identity":c.run_identity,"implementation_identity":c.implementation_identity,
            "solver_source_identity":c.solver_source_identity,"target_identity":c.target_identity,
            "build_identity":c.build_identity,"configuration_fingerprint":c.configuration_fingerprint,
            "seed":c.seed,"parameter_count":c.best_params.len()})
    }
}

fn load(path: &Path) -> Result<Snapshot> {
    if !std::fs::metadata(path)?.is_file() {
        return Err("checkpoint input must be a regular file".into());
    }
    let mut bytes = Vec::new();
    let file = File::open(path)?;
    if !file.metadata()?.is_file() {
        return Err("checkpoint input must be a regular file".into());
    }
    file.take(MAX_BYTES + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err("checkpoint exceeds 32 MiB".into());
    }
    // serde_json rejects NaN/Infinity/overflow rather than silently converting them.
    let raw: Value = serde_json::from_slice(&bytes)?;
    let state: State = serde_json::from_value(raw.clone())?;
    state.validate()?;
    validate_terminal(
        &raw["checkpoint"]["terminal"],
        state.checkpoint.best_params.len(),
    )?;
    let mut core = raw["checkpoint"].clone();
    core.as_object_mut()
        .ok_or("checkpoint must be an object")?
        .remove("terminal");
    Ok(Snapshot {
        state,
        bytes: bytes.len() as u64,
        digest: format!("sha256:{:x}", Sha256::digest(&bytes)),
        source: path.display().to_string(),
        terminal: raw["checkpoint"]["terminal"].clone(),
        core_digest: format!("{:x}", Sha256::digest(core.to_string().as_bytes())),
    })
}

fn validate_terminal(value: &Value, dimension: usize) -> Result<()> {
    if value.is_null() {
        return Ok(());
    }
    #[derive(Deserialize)]
    struct Terminal {
        success: bool,
        message: String,
        finalized: bool,
        final_params: Option<Vec<f64>>,
        final_fitness: Option<Fitness>,
        polish_evaluations: u64,
    }
    let t: Terminal = serde_json::from_value(value.clone())?;
    // Outcome/message are preserved verbatim, without assuming success.
    let _ = (t.success, t.message);
    if t.finalized {
        let p = t
            .final_params
            .ok_or("finalized terminal missing parameters")?;
        let f = t
            .final_fitness
            .ok_or("finalized terminal missing fitness")?;
        if p.len() != dimension
            || p.iter().any(|v| !v.is_finite())
            || f.finite().is_some_and(|v| !v.is_finite())
        {
            return Err("invalid finalized terminal arrays".into());
        }
    } else if t.final_params.is_some() || t.final_fitness.is_some() || t.polish_evaluations != 0 {
        return Err("unfinalized terminal carries final report values".into());
    }
    Ok(())
}

fn log_snapshot(rec: &rerun::RecordingStream, s: &Snapshot) -> Result<()> {
    let c = &s.state.checkpoint;
    rec.set_time_sequence("generation", c.generation);
    rec.log(
        "checkpoint",
        &rerun::TextDocument::new(
            json!({"source":s.source,
        "input_sha256":s.digest,"timestamp":s.state.timestamp,
        "evaluations":c.evaluations,"terminal":s.terminal})
            .to_string(),
        ),
    )?;
    if let Some(v) = c.best_fitness.finite() {
        rec.log("objective/best", &rerun::Scalars::new([v]))?;
    } else {
        rec.log("objective/best", &rerun::Clear::new(false))?;
    }
    for (i, v) in c.best_params.iter().enumerate() {
        rec.log(format!("parameters/{i}"), &rerun::Scalars::new([*v]))?;
    }
    let finite: Vec<f64> = c
        .population_fitness
        .iter()
        .filter_map(Fitness::finite)
        .collect();
    for (name, value) in [
        ("minimum", finite.iter().copied().reduce(f64::min)),
        ("maximum", finite.iter().copied().reduce(f64::max)),
    ] {
        let path = format!("objective/population/{name}");
        if let Some(v) = value {
            rec.log(path, &rerun::Scalars::new([v]))?;
        } else {
            rec.log(path, &rerun::Clear::new(false))?;
        }
    }
    rec.log("objective/status", &rerun::TextDocument::new(json!({
        "best_finite":c.best_fitness.finite().is_some(),
        "positive_infinity_population_count":c.population_fitness.iter().filter(|f| f.finite().is_none()).count()
    }).to_string()))?;
    Ok(())
}

fn validate_series(snapshots: &[Snapshot]) -> Result<()> {
    let first = snapshots
        .first()
        .ok_or("at least one checkpoint required")?;
    for pair in snapshots.windows(2) {
        let a = &pair[0].state.checkpoint;
        let b = &pair[1].state.checkpoint;
        if pair[1].state.identity() != first.state.identity() {
            return Err("checkpoint run/config/build/seed or dimension mismatch".into());
        }
        if b.generation <= a.generation || b.evaluations < a.evaluations {
            return Err("checkpoints must have strictly increasing generations and nondecreasing evaluations; same-generation finalization is ambiguous".into());
        }
    }
    Ok(())
}

fn export(output: &Path, snapshots: &[Snapshot]) -> Result<()> {
    validate_series(snapshots)?;
    if output.try_exists()? {
        return Err("output exists; refusing overwrite".into());
    }
    // Publish atomically without replacing an output created by another process.
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let temp = tempfile::NamedTempFile::new_in(parent)?;
    {
        let rec = rerun::RecordingStreamBuilder::new("adeze_autoeq_checkpoint")
            .enabled(true)
            .save(temp.path())?;
        rec.set_log_time_enabled(false);
        rec.set_log_tick_enabled(false);
        let mode = if snapshots.len() == 1 {
            "single snapshot; no iteration history"
        } else {
            "sparse retained checkpoints; intervening generations unavailable"
        };
        let meta = json!({"format":"AutoEQ ExactOptimizerState v1 / DECheckpoint v1",
            "interpretation":mode,"identity":snapshots[0].state.identity(),
            "units":"objective and parameter units unspecified by source schema",
            "spatial_coordinates":"unavailable","elapsed_solver_time":"unavailable"});
        rec.log_static("provenance", &rerun::TextDocument::new(meta.to_string()))?;
        for s in snapshots {
            log_snapshot(&rec, s)?;
        }
        rec.flush_blocking()?;
    }
    temp.as_file().sync_all()?;
    temp.persist_noclobber(output)?;
    Ok(())
}

#[derive(Debug)]
struct WatchOptions {
    timeout: Duration,
    poll: Duration,
    viewer_port: Option<u16>,
    cancel_file: Option<PathBuf>,
}
impl WatchOptions {
    fn parse(args: &[std::ffi::OsString]) -> Result<Self> {
        let mut opts = Self {
            timeout: Duration::from_secs(600),
            poll: Duration::from_millis(100),
            viewer_port: None,
            cancel_file: None,
        };
        if !args.len().is_multiple_of(2) {
            return Err("watch options require flag/value pairs".into());
        }
        for pair in args.as_chunks::<2>().0 {
            match pair[0].to_str().ok_or("invalid watch flag")? {
                "--timeout-secs" => {
                    opts.timeout =
                        Duration::from_secs(pair[1].to_str().ok_or("invalid timeout")?.parse()?)
                }
                "--poll-ms" => {
                    opts.poll = Duration::from_millis(
                        pair[1].to_str().ok_or("invalid poll interval")?.parse()?,
                    )
                }
                "--viewer-port" => {
                    opts.viewer_port = Some(pair[1].to_str().ok_or("invalid port")?.parse()?)
                }
                "--cancel-file" => opts.cancel_file = Some(PathBuf::from(&pair[1])),
                _ => return Err("unknown watch option".into()),
            }
        }
        if !(1..=86400).contains(&opts.timeout.as_secs())
            || !(20..=5000).contains(&opts.poll.as_millis())
            || opts.viewer_port == Some(0)
        {
            return Err(
                "timeout must be 1..86400 seconds, poll 20..5000 ms, viewer port 1..65535".into(),
            );
        }
        Ok(opts)
    }
}

// Watching can retain a terminal-only update at the same generation. This is
// explicitly a new observation, never another completed solver generation.
fn update_kind(previous: Option<&Snapshot>, current: &Snapshot) -> Result<Option<&'static str>> {
    let Some(old) = previous else {
        return Ok(Some("first_observed_snapshot"));
    };
    if old.state.identity() != current.state.identity() {
        return Err("watched checkpoint changed run/config/build/seed or dimension".into());
    }
    let a = &old.state.checkpoint;
    let b = &current.state.checkpoint;
    if b.generation < a.generation || b.evaluations < a.evaluations {
        return Err("watched checkpoint generation/evaluations regressed".into());
    }
    if b.generation > a.generation {
        return Ok(Some("generation_snapshot"));
    }
    if old.core_digest != current.core_digest {
        return Err("same-generation checkpoint changed non-terminal solver state".into());
    }
    if old.terminal == current.terminal {
        return Ok(None);
    }
    if current.terminal.is_null()
        || (!old.terminal.is_null()
            && (old.terminal["finalized"].as_bool() != Some(false)
                || current.terminal["finalized"].as_bool() != Some(true)))
    {
        return Err("invalid same-generation terminal transition".into());
    }
    Ok(Some("terminal_only_update"))
}

fn watch_loop(
    rec: &rerun::RecordingStream,
    input: &Path,
    opts: &WatchOptions,
) -> Result<(&'static str, usize)> {
    let start = Instant::now();
    let mut previous: Option<Snapshot> = None;
    let mut count = 0;
    let mut total_bytes = 0;
    loop {
        if opts.cancel_file.as_ref().is_some_and(|p| p.exists()) {
            return Ok(("cancelled", count));
        }
        if start.elapsed() >= opts.timeout {
            return Ok(("timeout", count));
        }
        let snapshot = match load(input) {
            Ok(s) => Some(s),
            Err(e)
                if e.downcast_ref::<std::io::Error>()
                    .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound) =>
            {
                None
            }
            Err(e) => return Err(e),
        };
        if let Some(s) = snapshot
            && let Some(kind) = update_kind(previous.as_ref(), &s)?
        {
            count += 1;
            total_bytes += s.bytes;
            if count > 4096 || total_bytes > 128 * 1024 * 1024 {
                return Ok(("observation_budget_reached", count - 1));
            }
            let skipped = previous.as_ref().map(|old| {
                (s.state.checkpoint.generation - old.state.checkpoint.generation - 1).max(0)
            });
            rec.set_time_sequence("observation", count as i64);
            rec.set_duration_secs("adapter_elapsed", start.elapsed().as_secs_f64());
            log_snapshot(rec, &s)?;
            rec.log("watch/update", &rerun::TextDocument::new(json!({"kind":kind,
                "missed_intermediate_generations":skipped,"history":"sampled atomic checkpoint file; producer updates may be missed",
                "adapter_elapsed_is_solver_time":false}).to_string()))?;
            // Bound transport flush; viewer failure must not prevent file retention.
            rec.flush_with_timeout(Duration::from_secs(2))?;
            let finalized = s.terminal["finalized"].as_bool() == Some(true);
            previous = Some(s);
            if finalized {
                return Ok(("finalized_checkpoint_observed", count));
            }
        }
        std::thread::sleep(opts.poll.min(opts.timeout.saturating_sub(start.elapsed())));
    }
}

fn watch(output: &Path, input: &Path, opts: &WatchOptions) -> Result<()> {
    if output.try_exists()? {
        return Err("output exists; refusing overwrite".into());
    }
    let viewer_problem = if let Some(port) = opts.viewer_port {
        // Loopback only; fail early rather than silently queueing to absent viewer.
        std::net::TcpStream::connect_timeout(
            &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
            Duration::from_millis(500),
        )
        .err()
        .map(|e| format!("viewer loopback TCP connection unavailable; recording file only: {e}"))
    } else {
        None
    };
    if let Some(problem) = &viewer_problem {
        eprintln!("{problem}");
    }
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let temp = tempfile::NamedTempFile::new_in(parent)?;
    let mut result;
    {
        let file = rerun::sink::FileSink::new(temp.path())?;
        let builder =
            rerun::RecordingStreamBuilder::new("adeze_autoeq_checkpoint_watch").enabled(true);
        let rec = if let Some(port) = opts.viewer_port.filter(|_| viewer_problem.is_none()) {
            let grpc =
                rerun::sink::GrpcSink::new(format!("rerun+http://127.0.0.1:{port}/proxy").parse()?);
            builder.set_sinks((file, grpc))?
        } else {
            builder.set_sinks((file,))?
        };
        rec.set_log_time_enabled(false);
        rec.set_log_tick_enabled(false);
        rec.log_static("provenance", &rerun::TextDocument::new(json!({
            "format":"AutoEQ ExactOptimizerState v1 / DECheckpoint v1",
            "mode":"callback -> atomic checkpoint file -> polling adapter; sparse observations, not direct solver instrumentation",
            "units":"objective/parameter units unspecified; parameter indices are not spatial coordinates",
            "elapsed":"adapter wall time only; source timestamp retained unparsed",
            "same_generation_updates":"terminal-only updates use observation timeline",
            "viewer_connection_problem":viewer_problem,
            "viewer_delivery":"not inferred from TCP probe; bounded SDK flush required",
            "source":input.display().to_string()}).to_string()))?;
        result = watch_loop(&rec, input, opts);
        let status = match &result {
            Ok((reason, count)) => json!({"reason":reason,"observations":count}),
            Err(e) => json!({"reason":"adapter_error","error":e.to_string()}),
        };
        if let Err(e) = rec.log(
            "watch/outcome",
            &rerun::TextDocument::new(status.to_string()),
        ) {
            eprintln!("outcome logging failed; preserving local recording: {e}");
            if result.is_ok() {
                result = Err(e.into());
            }
        }
        if let Err(e) = rec.flush_with_timeout(Duration::from_secs(2)) {
            eprintln!("viewer flush incomplete; preserving local recording: {e}");
            if result.is_ok() {
                result = Err(e.into());
            }
        }
    }
    temp.as_file().sync_all()?;
    temp.persist_noclobber(output)?;
    let (reason, count) = result?;
    println!(
        "watch stopped: {reason}; {count} observations; recording {}",
        output.display()
    );
    if reason != "finalized_checkpoint_observed" {
        return Err(format!(
            "watch incomplete ({reason}); partial recording retained at {}",
            output.display()
        )
        .into());
    }
    if let Some(problem) = viewer_problem {
        return Err(problem.into());
    }
    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.first().is_some_and(|v| v == "watch") {
        if args.len() < 3 {
            return Err("usage: rerun-study watch OUTPUT.rrd CHECKPOINT.json [--timeout-secs N] [--poll-ms N] [--viewer-port PORT] [--cancel-file PATH]".into());
        }
        return watch(
            Path::new(&args[1]),
            Path::new(&args[2]),
            &WatchOptions::parse(&args[3..])?,
        );
    }
    if args.len() < 2 || args.len() > MAX_INPUTS + 1 {
        return Err("usage: rerun-study OUTPUT.rrd CHECKPOINT.json [CHECKPOINT.json ...] (max 128 inputs, generation order)".into());
    }
    let mut snapshots = Vec::new();
    let mut bytes = 0;
    for path in &args[1..] {
        let snapshot = load(Path::new(path))?;
        bytes += snapshot.bytes;
        if bytes > MAX_BYTES {
            return Err("combined checkpoint input exceeds 32 MiB".into());
        }
        snapshots.push(snapshot);
    }
    export(Path::new(&args[0]), &snapshots)?;
    println!(
        "wrote {} checkpoint(s) to {}",
        snapshots.len(),
        Path::new(&args[0]).display()
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // Handcrafted schema fixture; not a measured acoustic or solver result.
    fn fixture(generation: i64) -> Value {
        json!({"schema_version":1,"run_identity":"test-objective", "timestamp":"2026-10-22T00:00:00Z",
            "checkpoint":{"checkpoint_version":1,"run_identity":"test-objective",
            "implementation_identity":"test-implementation","solver_source_identity":"sha256:source",
            "target_identity":"test-target","build_identity":"sha256:build",
            "configuration_fingerprint":"sha256:config","seed":42,"generation":generation,
            "evaluations":generation * 4,"population":[[1.0,2.0],[3.0,4.0]],
            "population_fitness":[{"kind":"finite","value":5.0},{"kind":"positive_infinity"}],
            "best_index":0,"best_params":[1.0,2.0],"best_fitness":{"kind":"finite","value":5.0},
            "rng_state":[1,2,3],"adaptive":null,"archive":null,"terminal":null}})
    }
    fn snapshot(value: Value) -> Snapshot {
        let state: State = serde_json::from_value(value.clone()).unwrap();
        let mut core = value["checkpoint"].clone();
        core.as_object_mut().unwrap().remove("terminal");
        Snapshot {
            state,
            digest: "sha256:test".into(),
            source: "test fixture".into(),
            terminal: value["checkpoint"]["terminal"].clone(),
            bytes: 1,
            core_digest: format!("{:x}", Sha256::digest(core.to_string().as_bytes())),
        }
    }
    fn terminal(finalized: bool) -> Value {
        json!({"success":true,"message":"test fixture only","finalized":finalized,
            "final_params":if finalized {json!([1.0,2.0])} else {Value::Null},
            "final_fitness":if finalized {json!({"kind":"finite","value":5.0})} else {Value::Null},
            "polish_evaluations":0})
    }
    #[test]
    fn validates_dimensions_indices_and_nonfinite_arrays() {
        let mut s = snapshot(fixture(1));
        assert!(s.state.validate().is_ok());
        s.state.checkpoint.population[0].pop();
        assert!(s.state.validate().is_err());
        s = snapshot(fixture(1));
        s.state.checkpoint.best_index = 2;
        assert!(s.state.validate().is_err());
        s = snapshot(fixture(1));
        s.state.checkpoint.best_params[0] = f64::NAN;
        assert!(s.state.validate().is_err());
        s = snapshot(fixture(1));
        s.state.checkpoint.population_fitness[0] = Fitness::Finite(f64::INFINITY);
        assert!(s.state.validate().is_err());
        let overflow = fixture(1).to_string().replace("[1.0,2.0]", "[1e999,2.0]");
        assert!(serde_json::from_str::<State>(&overflow).is_err());
    }
    #[test]
    fn rejects_incompatible_or_ambiguous_history() {
        let mut series = vec![snapshot(fixture(1)), snapshot(fixture(2))];
        assert!(validate_series(&series).is_ok());
        series[1].state.checkpoint.seed += 1;
        assert!(validate_series(&series).is_err());
        series[1] = snapshot(fixture(1));
        assert!(validate_series(&series).is_err());
        series[1] = snapshot(fixture(2));
        series[1].state.checkpoint.evaluations = 0;
        assert!(validate_series(&series).is_err());
    }
    #[test]
    fn hashes_original_bytes_and_rejects_bad_schema() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("checkpoint.json");
        let bytes = fixture(1).to_string();
        std::fs::write(&path, &bytes).unwrap();
        assert_eq!(
            load(&path).unwrap().digest,
            format!("sha256:{:x}", Sha256::digest(bytes.as_bytes()))
        );
        let mut value = fixture(1);
        value["schema_version"] = json!(2);
        std::fs::write(&path, value.to_string()).unwrap();
        assert!(load(&path).is_err());
        assert!(!dir.path().join("output.rrd").exists());
    }
    #[test]
    fn recording_decodes_and_never_overwrites() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("checkpoint.rrd");
        let mut second = fixture(2);
        second["checkpoint"]["best_fitness"] = json!({"kind":"positive_infinity"});
        second["checkpoint"]["population_fitness"] = json!([
            {"kind":"positive_infinity"},{"kind":"positive_infinity"}]);
        export(&path, &[snapshot(fixture(1)), snapshot(second)]).unwrap();
        let bytes = std::fs::read(&path).unwrap();
        let messages: Vec<re_log_types::LogMsg> =
            re_log_encoding::Decoder::decode_eager(std::io::Cursor::new(&bytes))
                .unwrap()
                .collect::<std::result::Result<_, _>>()
                .unwrap();
        assert!(messages.len() > 2);
        let debug = format!("{messages:?}");
        assert!(debug.contains("objective/best"));
        assert!(debug.contains("Clear"));
        assert!(debug.contains("generation"));
        assert!(export(&path, &[snapshot(fixture(1))]).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), bytes);
    }

    #[test]
    fn watch_transition_distinguishes_duplicates_finalization_and_regression() {
        let a = snapshot(fixture(3));
        assert_eq!(update_kind(Some(&a), &snapshot(fixture(3))).unwrap(), None);
        let mut final_value = fixture(3);
        final_value["checkpoint"]["terminal"] = terminal(true);
        let b = snapshot(final_value);
        assert_eq!(
            update_kind(Some(&a), &b).unwrap(),
            Some("terminal_only_update")
        );
        let mut changed = fixture(3);
        changed["checkpoint"]["best_params"] = json!([9.0, 2.0]);
        assert!(update_kind(Some(&a), &snapshot(changed)).is_err());
        assert!(update_kind(Some(&a), &snapshot(fixture(2))).is_err());
        assert!(update_kind(Some(&b), &a).is_err());
        assert!(validate_terminal(&terminal(true), 2).is_ok());
        assert!(validate_terminal(&terminal(true), 3).is_err());
        let mut bad = terminal(false);
        bad["final_params"] = json!([1.0, 2.0]);
        assert!(validate_terminal(&bad, 2).is_err());
    }

    #[test]
    fn watch_missing_input_times_out_and_cancel_file_stops() {
        let dir = tempfile::tempdir().unwrap();
        let (rec, _) = rerun::RecordingStreamBuilder::new("watch_test")
            .memory()
            .unwrap();
        let mut opts = WatchOptions {
            timeout: Duration::from_millis(25),
            poll: Duration::from_millis(5),
            viewer_port: None,
            cancel_file: None,
        };
        assert_eq!(
            watch_loop(&rec, &dir.path().join("absent.json"), &opts).unwrap(),
            ("timeout", 0)
        );
        let cancel = dir.path().join("cancel");
        std::fs::write(&cancel, []).unwrap();
        opts.cancel_file = Some(cancel);
        assert_eq!(
            watch_loop(&rec, &dir.path().join("absent.json"), &opts).unwrap(),
            ("cancelled", 0)
        );
        for args in [
            vec!["--poll-ms", "0"],
            vec!["--timeout-secs", "0"],
            vec!["--viewer-port", "0"],
            vec!["--unknown", "1"],
        ] {
            assert!(
                WatchOptions::parse(&args.into_iter().map(Into::into).collect::<Vec<_>>()).is_err()
            );
        }
    }

    #[test]
    fn atomic_file_watch_records_sparse_and_same_generation_terminal_updates() {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("checkpoint.json");
        let output = dir.path().join("live.rrd");
        let replace = |path: &Path, value: Value| {
            let temp = path.with_extension("next");
            std::fs::write(&temp, value.to_string()).unwrap();
            std::fs::rename(temp, path).unwrap();
        };
        replace(&input, fixture(1));
        let writer_input = input.clone();
        let writer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(200));
            replace(&writer_input, fixture(3));
            std::thread::sleep(Duration::from_millis(200));
            let mut final_value = fixture(3);
            final_value["checkpoint"]["terminal"] = terminal(true);
            replace(&writer_input, final_value);
        });
        watch(
            &output,
            &input,
            &WatchOptions {
                timeout: Duration::from_secs(3),
                poll: Duration::from_millis(20),
                viewer_port: None,
                cancel_file: None,
            },
        )
        .unwrap();
        writer.join().unwrap();
        let messages: Vec<re_log_types::LogMsg> = re_log_encoding::Decoder::decode_eager(
            std::io::BufReader::new(File::open(&output).unwrap()),
        )
        .unwrap()
        .collect::<std::result::Result<_, _>>()
        .unwrap();
        let debug = format!("{messages:?}");
        assert!(debug.contains("terminal_only_update"));
        assert!(debug.contains("missed_intermediate_generations"));
        assert!(debug.contains("finalized_checkpoint_observed"));
        assert!(debug.contains("observation"));
        assert!(debug.contains("adapter_elapsed"));
        assert!(
            watch(
                &output,
                &input,
                &WatchOptions {
                    timeout: Duration::from_secs(1),
                    poll: Duration::from_millis(20),
                    viewer_port: None,
                    cancel_file: None
                }
            )
            .is_err()
        );
    }

    #[test]
    fn malformed_live_input_retains_recording_with_failure_status() {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("invalid.json");
        let output = dir.path().join("failed.rrd");
        std::fs::write(&input, "{}").unwrap();
        assert!(
            watch(
                &output,
                &input,
                &WatchOptions {
                    timeout: Duration::from_secs(1),
                    poll: Duration::from_millis(20),
                    viewer_port: None,
                    cancel_file: None
                }
            )
            .is_err()
        );
        let messages: Vec<re_log_types::LogMsg> = re_log_encoding::Decoder::decode_eager(
            std::io::BufReader::new(File::open(&output).unwrap()),
        )
        .unwrap()
        .collect::<std::result::Result<_, _>>()
        .unwrap();
        assert!(format!("{messages:?}").contains("adapter_error"));
    }
}
