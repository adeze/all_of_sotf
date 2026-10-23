//! Existing DE solver and PEQ objective, with read-only population telemetry.
extern crate autoeq_optim as autoeq;
extern crate self as autoeq_core;
#[cfg(not(test))]
#[path = "../../../autoeq/crates/autoeq-cli/src/autoeq/rerun_telemetry.rs"]
mod canonical_telemetry;
#[path = "../../../autoeq/crates/autoeq-core/src/display_smoothing.rs"]
pub mod display_smoothing;
#[path = "../../../autoeq/crates/autoeq-cli/src/autoeq/envelope_view.rs"]
mod envelope_view;
#[path = "../../../autoeq/crates/autoeq-cli/src/autoeq/response_envelope.rs"]
mod response_envelope;
mod workflow;

use autoeq::{
    de::{CheckpointFitness, DECheckpoint, DifferentialEvolution},
    optim::{ObjectiveDataBuilder, loss::ObjectiveContext},
};
use display_smoothing::Mode;
use rerun::blueprint::{
    Blueprint, GraphView, Spatial2DView, Tabs, TextDocumentView, TimeSeriesView,
};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Write,
    net::TcpListener,
    path::{Path, PathBuf},
    time::Duration,
};
const MODES: [Mode; 7] = [
    Mode::Raw,
    Mode::OctaveThird,
    Mode::OctaveSixth,
    Mode::OctaveTwelfth,
    Mode::OctaveTwentyFourth,
    Mode::OctaveFortyEighth,
    Mode::Psychoacoustic,
];
const COLORS: [[u8; 3]; 8] = [
    [65, 150, 240],
    [235, 135, 65],
    [175, 110, 225],
    [65, 180, 160],
    [220, 100, 145],
    [160, 170, 75],
    [100, 180, 220],
    [195, 145, 200],
];
fn mode_label(mode: Mode) -> &'static str {
    match mode {
        Mode::Raw => "Raw",
        Mode::OctaveThird => "1/3 octave",
        Mode::OctaveSixth => "1/6 octave",
        Mode::OctaveTwelfth => "1/12 octave",
        Mode::OctaveTwentyFourth => "1/24 octave",
        Mode::OctaveFortyEighth => "1/48 octave",
        Mode::Psychoacoustic => "Psychoacoustic",
    }
}

fn write_new(path: &Path, value: &serde_json::Value) -> Result<(), Box<dyn std::error::Error>> {
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    serde_json::to_writer(&mut f, value)?;
    f.flush()?;
    Ok(())
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn selected_channels(value: &str) -> Result<Vec<String>, String> {
    let channels = value.split(',').map(str::to_owned).collect::<Vec<_>>();
    if channels.is_empty()
        || channels.len() > 8
        || channels.iter().any(|s| {
            s.is_empty()
                || s.len() > 32
                || !s
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        })
        || channels
            .iter()
            .collect::<std::collections::BTreeSet<_>>()
            .len()
            != channels.len()
    {
        return Err(
            "Select 1..8 distinct speaker names (letters, digits, underscore or dash)".into(),
        );
    }
    Ok(channels)
}
fn native_stream_mode(args: &[String]) -> Result<bool, &'static str> {
    match args {
        [_, _, _] => Ok(false),
        [_, _, _, flag] if flag == "--native-stream" => Ok(true),
        _ => Err(
            "usage: solver-envelope-pilot ROOM_CONFIG_JSON CHANNELS_COMMA_SEPARATED NEW_OUTPUT_DIR [--native-stream]",
        ),
    }
}

fn emit_event(writer: &mut impl Write, event: &serde_json::Value) -> std::io::Result<()> {
    serde_json::to_writer(&mut *writer, event)?;
    writer.write_all(b"\n")?;
    writer.flush()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let native_stream = native_stream_mode(&args)?;
    let config_path = PathBuf::from(&args[0]).canonicalize()?;
    let config_bytes = fs::read(&config_path)?;
    let config: serde_json::Value = serde_json::from_slice(&config_bytes)?;
    let channels = selected_channels(&args[1])?;
    let mut inputs = Vec::new();
    for channel in &channels {
        let path = config["speakers"][channel]["path"]
            .as_str()
            .ok_or("Selected speaker must have an existing CSV path")?;
        let path = config_path.parent().unwrap().join(path).canonicalize()?;
        if fs::metadata(&path)?.len() > 16 * 1024 * 1024 {
            return Err("Measurement exceeds 16 MiB budget".into());
        }
        let bytes = fs::read(&path)?;
        let curve = autoeq::read::read_curve_from_csv(&path)?;
        if curve.freq.len() < 2 || curve.freq.len() > 4096 {
            return Err("Measurement requires 2..4096 frequency bins".into());
        }
        display_smoothing::smooth(
            curve.freq.as_slice().ok_or("Noncontiguous frequency")?,
            curve.spl.as_slice().ok_or("Noncontiguous levels")?,
            Mode::Raw,
        )?;
        inputs.push((channel.clone(), path, curve, digest(&bytes)));
    }
    let frequency = inputs[0].2.freq.to_vec();
    if inputs.iter().any(|x| x.2.freq.to_vec() != frequency) {
        return Err(
            "Comparison requires identical source frequency grids; resampling is not implicit"
                .into(),
        );
    }
    if frequency[0] > 20. || *frequency.last().unwrap() < 300. {
        return Err("Study requires source coverage across 20..300 Hz".into());
    }
    if !frequency.iter().any(|f| *f >= 20. && *f <= 300.) {
        return Err("No measurement samples inside correction band".into());
    }
    let common_target = inputs[0]
        .2
        .spl
        .iter()
        .zip(&frequency)
        .filter(|(_, f)| **f >= 20. && **f <= 300.)
        .map(|(v, _)| *v)
        .sum::<f64>()
        / frequency
            .iter()
            .filter(|f| **f >= 20. && **f <= 300.)
            .count() as f64;
    let minimum = inputs
        .iter()
        .flat_map(|x| x.2.spl.iter())
        .copied()
        .fold(common_target, f64::min)
        - 16.;
    let maximum = inputs
        .iter()
        .flat_map(|x| x.2.spl.iter())
        .copied()
        .fold(common_target, f64::max)
        + 4.;
    let chart = envelope_view::Chart {
        min_hz: frequency[0],
        max_hz: *frequency.last().unwrap(),
        min_db: minimum,
        max_db: maximum,
    };
    let out = PathBuf::from(&args[2]);
    fs::create_dir(&out)?;
    let stage_specs = workflow::panel_specs(&channels, &MODES, common_target)?;
    let chart_metadata = json!({
        "domain":{"minHz":chart.min_hz,"maxHz":chart.max_hz,"minDb":chart.min_db,"maxDb":chart.max_db},
        "channels":channels,"target_db":common_target,
        "smoothing_modes":MODES.map(|mode| mode.as_str()),
        "rerun_version":"0.40.0","transfer_prohibited":true
    });
    write_new(&out.join("chart.json"), &chart_metadata)?;
    let blueprint = || {
        let mut views = MODES
            .iter()
            .map(|mode| {
                Spatial2DView::new(format!("Compare · {}", mode_label(*mode)))
                    .with_origin(format!("response/{}", mode.as_str()))
                    .with_contents([format!("/response/{}/**", mode.as_str())])
                    .into()
            })
            .collect::<Vec<rerun::blueprint::ContainerLike>>();
        views.push(
            GraphView::new("Workflow · algorithm connections")
                .with_contents(["/workflow/graph"])
                .into(),
        );
        views.push(workflow::parameter_tree(&stage_specs, &channels));
        views.push(
            TimeSeriesView::new("Actual objective · generation")
                .with_contents(["/objective/**"])
                .into(),
        );
        views.push(
            TextDocumentView::new("Scope and speaker selection")
                .with_contents(["/scope"])
                .into(),
        );
        Blueprint::new(Tabs::new(views))
            .with_auto_layout(false)
            .with_auto_views(false)
    };
    let store = rerun::StoreId::random(
        rerun::StoreKind::Recording,
        "adeze_solver_response_envelopes",
    );
    let make =
        || rerun::RecordingStreamBuilder::from_store_id(&store).with_default_blueprint(blueprint());
    let file = make().save(out.join("envelopes.rrd"))?;
    let (live, web) = if native_stream {
        (rerun::RecordingStream::disabled(), None)
    } else {
        let reservation = TcpListener::bind(("127.0.0.1", 0))?;
        let port = reservation.local_addr()?.port();
        drop(reservation);
        let live = make().serve_grpc_opts(
            "127.0.0.1",
            port,
            rerun::ServerOptions {
                memory_limit: rerun::MemoryLimit::from_bytes(128 * 1024 * 1024),
                ..Default::default()
            },
        )?;
        let web = rerun::web_viewer::WebViewerConfig {
            bind_ip: "127.0.0.1".into(),
            open_browser: false,
            ..Default::default()
        }
        .host_web_viewer()?;
        let url = format!(
            "{}?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A{port}%2Fproxy&renderer=webgl",
            web.bound_url()
        );
        (live, Some((web, url)))
    };
    let scope = json!({"source_config_sha256":digest(&config_bytes),"channels":channels,"smoothing_algorithm":display_smoothing::ALGORITHM_VERSION,"band_semantics":"finite-fitness population min/max and interpolated p10/p90; candidate spread, not confidence","study":"two cut-only PEQs per speaker,20..300Hz,24 population,48 generations,seed91827; unchanged canonical DE and FlatStrategy; no coupled multi-speaker optimization","target":"demonstration common flat level: first selected measurement mean in correction band; not an acoustically selected target","level_reference":"source CSV dB reference retained; no absolute SPL claim or per-speaker renormalization","selection":"View Blueprint tree: toggle each speaker group in Compare views, or edit view entity filter for any subset. CLI accepts arbitrary speaker subset; no averaging of pairs.","telemetry":"every completed generation barrier and terminal callback; no polling; synchronous logging may slow wall time","transfer_prohibited":true});
    for rec in [&file, &live] {
        rec.set_log_time_enabled(false);
        workflow::log_static(rec, &channels, &MODES, common_target)?;
        rec.log_static(
            "scope",
            &rerun::TextDocument::new(serde_json::to_string_pretty(&scope)?),
        )?;
        for mode in MODES {
            chart.axes(rec, &format!("response/{}", mode.as_str()))?;
            rec.log_static(
                format!("response/{}/speakers", mode.as_str()),
                &rerun::Points2D::new(
                    (0..channels.len())
                        .map(|i| [1. + (i % 4) as f32 * 3., -0.6 - (i / 4) as f32 * 0.5]),
                )
                .with_labels(channels.iter().map(|s| format!("{s} · best / spread")))
                .with_colors(COLORS[..channels.len()].iter().copied())
                .with_show_labels(true)
                .with_radii([0.025]),
            )?;
        }
    }
    if let Some((_, url)) = &web {
        write_new(
            &out.join("viewer.json"),
            &json!({"viewer_url":url,"seconds":1800,"scope":scope}),
        )?;
        println!("{}", json!({"viewer_url":url,"output":out}));
        std::io::stdout().flush()?;
    } else {
        emit_event(
            &mut std::io::stdout().lock(),
            &json!({"type":"chart","chart":chart_metadata}),
        )?;
    }
    let mut callbacks = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(out.join("callbacks.jsonl"))?;
    let mut receipts = Vec::new();
    for (channel_index, (channel, path, curve, measurement_hash)) in inputs.iter().enumerate() {
        let target = vec![common_target; frequency.len()];
        let baseline = curve.spl.to_vec();
        let deviation = target
            .iter()
            .zip(&baseline)
            .map(|(t, b)| t - b)
            .collect::<Vec<_>>();
        let data = ObjectiveDataBuilder::new(
            frequency.clone().into(),
            target.clone().into(),
            deviation.into(),
            48000.,
            autoeq::PeqModel::Pk,
            autoeq::LossType::SpeakerFlat,
        )
        .freq_range(20., 300.)
        .max_db(0.)
        .min_db(-12.)
        .build()?;
        let ctx = ObjectiveContext {
            freqs: &data.freqs,
            target: &data.target,
            deviation: &data.deviation,
            srate: data.srate,
            peq_model: data.peq_model,
            min_freq: data.min_freq,
            max_freq: data.max_freq,
            smooth: data.smooth,
            smooth_n: data.smooth_n,
            audibility_deadband: data.audibility_deadband.as_ref(),
            smoothness_penalty: data.smoothness_penalty.as_ref(),
        };
        let strategy = data.build_objective();
        let objective = |x: &ndarray::Array1<f64>| strategy.compute(x.as_slice().unwrap(), &ctx);
        let lower = vec![20f64.log10(), 0.5, -12., 20f64.log10(), 0.5, -12.];
        let upper = vec![300f64.log10(), 3., 0., 300f64.log10(), 3., 0.];
        let mut solver = DifferentialEvolution::new(&objective, lower.into(), upper.into())?;
        {
            let c = solver.config_mut();
            c.seed = Some(91827);
            c.maxiter = 48;
            c.min_convergence_iter = 48;
            c.popsize = 4;
            c.polish = None;
        }
        let identity = format!(
            "envelope:{channel}:{measurement_hash}:{}",
            digest(&config_bytes)
        );
        #[cfg(not(test))]
        canonical_telemetry::validate_population(24)?;
        #[cfg(not(test))]
        let mut canonical = canonical_telemetry::Telemetry::new(
            &out.join(format!("{channel}-canonical.rrd")),
            None,
            data.clone(),
            &identity,
            measurement_hash,
        )?;
        let mut count = 0;
        let mut last = None;
        let mut first_loss = None;
        let mut last_loss = None;
        let mut observer = |c: &DECheckpoint| -> Result<(), String> {
            #[cfg(not(test))]
            canonical.record(c).map_err(|e| e.to_string())?;
            if last.is_some_and(|g| c.generation != g && c.generation != g + 1) {
                return Err("Missing generation callback".into());
            }
            if c.population.len() != c.population_fitness.len() {
                return Err("Population fitness length mismatch".into());
            }
            let mut responses = Vec::new();
            for (params, fitness) in c.population.iter().zip(&c.population_fitness) {
                if matches!(fitness,CheckpointFitness::Finite(v) if v.is_finite()) {
                    if params.len() != 6 || params.iter().any(|v| !v.is_finite()) {
                        return Err("Invalid candidate parameters".into());
                    }
                    responses.push(
                        ctx.peq_spl(params)
                            .iter()
                            .zip(&baseline)
                            .map(|(f, b)| f + b)
                            .collect::<Vec<_>>(),
                    );
                }
            }
            let best = ctx
                .peq_spl(&c.best_params)
                .iter()
                .zip(&baseline)
                .map(|(f, b)| f + b)
                .collect::<Vec<_>>();
            count += 1;
            last = Some(c.generation);
            let loss = match c.best_fitness {
                CheckpointFitness::Finite(v) => v,
                _ => return Err("Nonfinite best fitness".into()),
            };
            first_loss.get_or_insert(loss);
            last_loss = Some(loss);
            let mut summaries = serde_json::Map::new();
            let mut display_best = serde_json::Map::new();
            for mode in MODES {
                let envelope =
                    response_envelope::Envelope::from_candidates(&frequency, &responses, mode)?;
                let root = format!("response/{}/{channel}", mode.as_str());
                let color = COLORS[channel_index];
                for rec in [&file, &live] {
                    rec.set_time_sequence("generation", c.generation as i64);
                    rec.set_time_sequence("callback_index", count as i64);
                    chart.log(
                        rec, &root, &frequency, &baseline, &target, &best, &envelope, mode, color,
                    )?;
                }
                summaries.insert(mode.as_str().into(),json!({"min":envelope.min,"max":envelope.max,"p10":envelope.p10,"p90":envelope.p90,"count":envelope.count}));
                display_best.insert(
                    mode.as_str().into(),
                    json!(
                        display_smoothing::smooth(&frequency, &best, mode)
                            .map_err(|e| e.to_string())?
                    ),
                );
            }
            let event = json!({"channel":channel,"callback_index":count,"generation":c.generation,"loss":loss,"checkpoint_sha256":digest(&serde_json::to_vec(c).map_err(|e|e.to_string())?),"best_params":c.best_params,"best_response_db":best,"display_best_db":display_best,"frequency_hz":frequency,"envelopes":summaries,"terminal":c.terminal});
            let bytes = serde_json::to_vec(&event).map_err(|e| e.to_string())?;
            if callbacks.metadata().map_err(|e| e.to_string())?.len() + bytes.len() as u64
                > 64 * 1024 * 1024
            {
                return Err("Telemetry reached 64 MiB budget".into());
            }
            callbacks
                .write_all(&bytes)
                .and_then(|_| callbacks.write_all(b"\n"))
                .map_err(|e| e.to_string())?;
            for rec in [&file, &live] {
                workflow::log_callback(rec, channel, c.generation, &c.best_params, loss)?;
                rec.log(format!("objective/{channel}"), &rerun::Scalars::new([loss]))
                    .map_err(|e| e.to_string())?;
            }
            file.flush_with_timeout(Duration::from_secs(5))
                .map_err(|e| e.to_string())?;
            if native_stream {
                emit_event(
                    &mut std::io::stdout().lock(),
                    &json!({"type":"callback","callback":event}),
                )
                .map_err(|e| e.to_string())?;
            }
            Ok(())
        };
        let result = solver.solve_with_checkpoint(None, &identity, Some(&mut observer))?;
        drop(observer);
        #[cfg(not(test))]
        canonical.finish(true)?;
        receipts.push(json!({"channel":channel,"measurement_path":path,"measurement_sha256":measurement_hash,"callbacks":count,"last_generation":last,"first_loss":first_loss,"last_loss":last_loss,"final_loss":result.fun,"final_params":result.x.to_vec()}));
    }
    callbacks.sync_all()?;
    file.flush_with_timeout(Duration::from_secs(5))?;
    live.flush_with_timeout(Duration::from_secs(5))?;
    write_new(
        &out.join("receipt.json"),
        &json!({"studies":receipts,"transfer_prohibited":true,"scope":scope}),
    )?;
    if native_stream {
        emit_event(
            &mut std::io::stdout().lock(),
            &json!({"type":"complete","studies":receipts}),
        )?;
    } else {
        println!("{}", json!({"complete":true,"studies":receipts}));
        std::thread::sleep(Duration::from_secs(1800));
    }
    Ok(())
}

#[cfg(test)]
mod pilot_tests {
    use super::*;
    #[test]
    fn speaker_selection_is_explicit_and_unique() {
        assert_eq!(selected_channels("L,R").unwrap(), ["L", "R"]);
        for s in ["", "L,L", "../L", "L,"] {
            assert!(selected_channels(s).is_err());
        }
        let args = ["config", "L,R", "output"].map(str::to_owned);
        assert!(!native_stream_mode(&args).unwrap());
        let mut args = args.to_vec();
        args.push("--native-stream".into());
        assert!(native_stream_mode(&args).unwrap());
        args[3] = "--unknown".into();
        assert!(native_stream_mode(&args).is_err());
        assert!(native_stream_mode(&args[..2]).is_err());
        let mut bytes = Vec::new();
        emit_event(&mut bytes, &json!({"type":"chart","chart":{}})).unwrap();
        emit_event(&mut bytes, &json!({"type":"complete","studies":[]})).unwrap();
        let lines = String::from_utf8(bytes).unwrap();
        assert!(lines.ends_with('\n'));
        let events = lines
            .lines()
            .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0]["type"], "chart");
        assert_eq!(events[1]["type"], "complete");
    }
}
