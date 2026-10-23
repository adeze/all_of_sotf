//! Standalone read-only projection of canonical RoomEQ projects into Rerun.
use roomeq_model::{MeasurementRef, MeasurementSource, RoomConfig, SpeakerConfig};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sotf_rerun_viewer::{ViewerSession, room::RoomGeometry};
use std::{
    collections::BTreeMap,
    io::Read,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

mod designer_host;
mod study;

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum EntityKind {
    Project,
    Speaker,
    Measurement,
    Constraints,
    Algorithm,
    Config,
    FilterCandidate,
}

#[derive(Serialize)]
struct Entity {
    path: String,
    kind: EntityKind,
    source_sha256: String,
    data: Value,
}

struct Measurement {
    label: String,
    frequencies: Vec<f64>,
    magnitude_db: Vec<f64>,
}

#[derive(Deserialize)]
struct Oca {
    candidate_hash: String,
    channels: Vec<OcaChannel>,
}

#[derive(Deserialize)]
struct OcaChannel {
    #[serde(rename = "commandId")]
    name: String,
    filter: Vec<f64>,
}

#[derive(Serialize)]
struct Candidate {
    id: String,
    name: String,
    kind: &'static str,
    source_digest: String,
    channels: Vec<CandidateChannel>,
}

#[derive(Serialize)]
struct CandidateChannel {
    name: String,
    sample_rate_hz: f64,
    tap_count: usize,
    frequency_hz: Vec<f64>,
    response_db: Vec<f64>,
    sampled_min_db: f64,
    sampled_max_db: f64,
}

fn candidate_response(channel: OcaChannel) -> Result<CandidateChannel, String> {
    // NativeFIRContract.swift defines these generator rates/tap counts; this is read-only inspection.
    let sub = channel.name.starts_with("SW") || channel.name == "Subwoofer";
    let (sample_rate_hz, tap_count) = if sub { (6000., 512) } else { (48000., 128) };
    if channel.name.is_empty()
        || channel.filter.len() != tap_count
        || channel.filter.iter().any(|v| !v.is_finite())
    {
        return Err(format!(
            "{}: invalid native FIR coefficients/tap count",
            channel.name
        ));
    }
    let filter = math_audio_iir_fir::Fir::<f64>::try_new_custom(channel.filter, sample_rate_hz)
        .map_err(|e| e.to_string())?;
    let frequency_hz: Vec<_> = (0..512)
        .map(|i| 20. * (sample_rate_hz * 0.499 / 20.).powf(i as f64 / 511.))
        .collect();
    let response_db: Vec<_> = frequency_hz.iter().map(|&f| filter.log_result(f)).collect();
    if response_db.iter().any(|v| !v.is_finite()) {
        return Err("Nonfinite FIR response".into());
    }
    Ok(CandidateChannel {
        name: channel.name,
        sample_rate_hz,
        tap_count,
        sampled_min_db: response_db.iter().copied().fold(f64::INFINITY, f64::min),
        sampled_max_db: response_db
            .iter()
            .copied()
            .fold(f64::NEG_INFINITY, f64::max),
        frequency_hz,
        response_db,
    })
}

fn candidate(path: &Path) -> Result<Candidate, String> {
    let bytes = read_bounded(path)?;
    let oca: Oca = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    if oca.candidate_hash.is_empty()
        || oca.candidate_hash.len() > 128
        || oca.channels.is_empty()
        || oca.channels.len() > 64
    {
        return Err("OCA requires candidate identity and 1–64 channels".into());
    }
    let mut names = std::collections::BTreeSet::new();
    let mut channels = Vec::new();
    for channel in oca.channels {
        if !names.insert(channel.name.clone()) {
            return Err("Duplicate OCA channel".into());
        }
        channels.push(candidate_response(channel)?);
    }
    let source_digest = digest(&bytes);
    Ok(Candidate {
        name: format!("Retained {}", &source_digest[..12]),
        id: oca.candidate_hash,
        kind: "serialized_electrical_fir",
        source_digest,
        channels,
    })
}

fn read_bounded(path: &Path) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?
        .take(16 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 16 * 1024 * 1024 {
        return Err("Project input exceeds 16 MiB".into());
    }
    Ok(bytes)
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn key(label: &str) -> String {
    label
        .as_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn measurement(
    reference: &MeasurementRef,
    base: &Path,
    label: String,
) -> Result<Measurement, String> {
    match reference.original() {
        MeasurementRef::Inline(curve) => Ok(Measurement {
            label,
            frequencies: curve.frequencies.clone(),
            magnitude_db: curve.magnitude_db.clone(),
        }),
        reference => {
            let path = reference
                .path()
                .ok_or("Unsupported measurement reference")?;
            let path = if path.is_absolute() {
                path.clone()
            } else {
                base.join(path)
            };
            // Bound CSV loading before invoking the existing parser; never reinterpret its units.
            let _ = read_bounded(&path)?;
            let curve =
                autoeq_measurements::read::read_curve_from_csv(&path).map_err(|e| e.to_string())?;
            Ok(Measurement {
                label,
                frequencies: curve.freq.to_vec(),
                magnitude_db: curve.spl.to_vec(),
            })
        }
    }
}

fn project(path: &Path) -> Result<(Vec<Entity>, Vec<Measurement>), String> {
    let bytes = read_bounded(path)?;
    let hash = digest(&bytes);
    let config: RoomConfig = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    config.validate_structure()?;
    let mut entities = Vec::new();
    let mut add = |path: String, kind: EntityKind, data: Value| {
        entities.push(Entity {
            path,
            kind,
            source_sha256: hash.clone(),
            data,
        })
    };
    add(
        "project/source".into(),
        EntityKind::Project,
        json!({
            "format":"RoomConfig", "version":config.version, "role":"read_only_projection",
            "source_sha256":hash, "measurement_qualification":"not inferred by viewer",
        }),
    );
    add(
        "project/config/optimizer".into(),
        EntityKind::Config,
        serde_json::to_value(&config.optimizer).map_err(|e| e.to_string())?,
    );
    add(
        "project/algorithms/optimizer".into(),
        EntityKind::Algorithm,
        json!({
            "algorithm":config.optimizer.algorithm, "loss_type":config.optimizer.loss_type,
            "status":"configured; execution is not inferred",
        }),
    );
    add(
        "project/constraints/optimizer".into(),
        EntityKind::Constraints,
        json!({
            "min_freq_hz":config.optimizer.min_freq, "max_freq_hz":config.optimizer.max_freq,
            "min_gain_db":config.optimizer.min_db, "max_gain_db":config.optimizer.max_db,
            "min_q":config.optimizer.min_q, "max_q":config.optimizer.max_q,
            "max_filters":config.optimizer.num_filters,
        }),
    );
    let mut measurements = Vec::new();
    let speakers: BTreeMap<_, _> = config.speakers.iter().collect();
    for (name, speaker) in speakers {
        let speaker_key = key(name);
        add(
            format!("project/speakers/{speaker_key}"),
            EntityKind::Speaker,
            json!({"channel":name,"source":speaker,"geometry":"not inferred"}),
        );
        let SpeakerConfig::Single(source) = speaker else {
            return Err(format!(
                "{name}: this pilot supports single-driver channels and multiple microphone takes; grouped speaker topologies are not projected yet"
            ));
        };
        let references = match source {
            MeasurementSource::Single(single) => vec![&single.measurement],
            MeasurementSource::Multiple(multiple) => multiple.measurements.iter().collect(),
            _ => return Err("Serialized project cannot contain runtime-only curves".into()),
        };
        for (index, reference) in references.into_iter().enumerate() {
            let label = format!("{name} · take {}", index + 1);
            let curve = measurement(reference, path.parent().unwrap_or(Path::new(".")), label)?;
            validate_curve(&curve)?;
            let numeric_hash = digest(
                &serde_json::to_vec(&json!({"hz":curve.frequencies,"db":curve.magnitude_db}))
                    .map_err(|e| e.to_string())?,
            );
            add(
                format!("project/measurements/{speaker_key}/{index}"),
                EntityKind::Measurement,
                json!({"speaker_entity":format!("project/speakers/{speaker_key}"),"reference":reference,
                    "samples":curve.frequencies.len(), "curve_sha256":numeric_hash,
                    "response_entity_label":curve.label,"processing":"unchanged supplied magnitude"}),
            );
            measurements.push(curve);
        }
    }
    if measurements.is_empty() {
        return Err("Project has no measurements".into());
    }
    Ok((entities, measurements))
}

fn validate_curve(curve: &Measurement) -> Result<(), String> {
    if curve.frequencies.len() < 2
        || curve.frequencies.len() != curve.magnitude_db.len()
        || curve.frequencies.iter().any(|v| !v.is_finite() || *v <= 0.)
        || curve.frequencies.windows(2).any(|v| v[0] >= v[1])
        || curve.magnitude_db.iter().any(|v| !v.is_finite())
    {
        return Err(format!(
            "{}: response requires matching finite dB and positive increasing Hz arrays",
            curve.label
        ));
    }
    Ok(())
}

fn run() -> Result<(), String> {
    let mut options = BTreeMap::new();
    let mut candidate_paths = Vec::new();
    let mut args = std::env::args().skip(1);
    while let Some(flag) = args.next() {
        if flag == "--help" {
            println!(
                "rerun-workbench --config RoomConfig.json --output NEW_DIRECTORY [--room room-model.json] [--candidate-oca PATH ...] [--designer-port PORT] [--seconds 300]"
            );
            return Ok(());
        }
        if !matches!(
            flag.as_str(),
            "--config"
                | "--output"
                | "--room"
                | "--seconds"
                | "--candidate-oca"
                | "--designer-port"
        ) {
            return Err(format!("Unknown option {flag}"));
        }
        let value = args
            .next()
            .ok_or_else(|| format!("Missing value for {flag}"))?;
        if flag == "--candidate-oca" {
            if candidate_paths.len() >= 8 {
                return Err("At most eight candidates per comparison".into());
            }
            candidate_paths.push(PathBuf::from(value));
            continue;
        }
        if options.insert(flag.clone(), value).is_some() {
            return Err(format!("Duplicate option {flag}"));
        }
    }
    let config = PathBuf::from(options.get("--config").ok_or("--config is required")?);
    let output = PathBuf::from(options.get("--output").ok_or("--output is required")?);
    let seconds = options
        .get("--seconds")
        .map(|v| v.parse::<u64>())
        .transpose()
        .map_err(|e| e.to_string())?
        .unwrap_or(300);
    let (mut entities, measurements) = project(&config)?;
    let room = options
        .get("--room")
        .map(|path| RoomGeometry::load_model(Path::new(path)))
        .transpose()?;
    let candidates = candidate_paths
        .iter()
        .map(|path| candidate(path))
        .collect::<Result<Vec<_>, _>>()?;
    let mut candidate_ids = std::collections::BTreeSet::new();
    for candidate in &candidates {
        if !candidate_ids.insert(&candidate.id) {
            return Err("Duplicate candidate identity".into());
        }
        entities.push(Entity {
            path:format!("project/candidates/{}",key(&candidate.id)),
            kind:EntityKind::FilterCandidate,
            source_sha256:candidate.source_digest.clone(),
            data:json!({"candidate_id":candidate.id,"kind":candidate.kind,"channels":candidate.channels.len(),
                "limitations":"Electrical FIR only; no room prediction, trims, crossover or live receiver validation"}),
        });
    }
    let catalog = serde_json::to_vec_pretty(&entities).map_err(|e| e.to_string())?;
    std::fs::create_dir(&output).map_err(|e| format!("Output must be a fresh directory: {e}"))?;
    let session = Arc::new(ViewerSession::new(&output)?);
    for entity in &entities {
        session.log_project_entity(
            &entity.path,
            &serde_json::to_value(entity).map_err(|e| e.to_string())?,
        )?;
    }
    for curve in &measurements {
        session.log_measurement(&curve.label, &curve.frequencies, &curve.magnitude_db)?;
    }
    // Electrical FIR gain and measured pressure have different references; compare FIRs in OpenUI.
    if let Some(room) = room {
        session.log_room_mesh(&room.vertices, &room.indices, &room.provenance)?;
    }
    session.flush()?;
    std::fs::write(output.join("catalog.json"), catalog).map_err(|e| e.to_string())?;
    std::fs::write(output.join("candidate-comparison.json"), serde_json::to_vec_pretty(&json!({
        "schema_version":1,"candidates":candidates,
        "limitations":"Serialized electrical FIR magnitude relative to unity; not a measured or predicted room response. No trims, crossovers or receiver state applied. Sampled extrema are not continuous-band validation. The existing FIR evaluator floors zero amplitude at -200 dB.",
    })).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let designer = options
        .get("--designer-port")
        .map(|port| {
            let port = port.parse::<u16>().map_err(|e| e.to_string())?;
            designer_host::start(port, &config, &output, session.clone())
        })
        .transpose()?;
    println!(
        "{}",
        json!({"viewer_url":format!("{}&renderer=webgl",session.viewer_url()),
        "recording":session.recording_path(),"project_entities":entities.len(),
        "measurements":measurements.len(),"source_data_read_only":true,"serve_seconds":seconds,
        "designer_endpoint":designer.as_ref().map(|host|format!("http://{}/workbench",host.endpoint()))})
    );
    std::thread::sleep(Duration::from_secs(seconds));
    if let Some(designer) = designer {
        designer.shutdown();
    }
    session.flush()
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn entity_identity_and_curve_validation_preserve_boundaries() {
        assert_ne!(key("L/curve"), key("L_curve"));
        assert_ne!(key("L"), key("l"));
        let mut curve = Measurement {
            label: "L".into(),
            frequencies: vec![20., 100.],
            magnitude_db: vec![-3., 0.],
        };
        assert!(validate_curve(&curve).is_ok());
        curve.frequencies.reverse();
        assert!(validate_curve(&curve).is_err());
        curve.frequencies.reverse();
        curve.magnitude_db.pop();
        assert!(validate_curve(&curve).is_err());
    }
    #[test]
    fn native_fir_inspection_uses_correct_rate_and_rejects_bad_taps() {
        for (name, count, rate) in [("FL", 128, 48000.), ("SW1", 512, 6000.)] {
            let mut filter = vec![0.; count];
            filter[0] = 1.;
            let response = candidate_response(OcaChannel {
                name: name.into(),
                filter,
            })
            .unwrap();
            assert_eq!(response.sample_rate_hz, rate);
            assert!(response.response_db.iter().all(|v| v.abs() < 1e-12));
            assert!(response.frequency_hz[511] < rate / 2.);
        }
        assert!(
            candidate_response(OcaChannel {
                name: "FL".into(),
                filter: vec![1.]
            })
            .is_err()
        );
        assert!(
            candidate_response(OcaChannel {
                name: "SW1".into(),
                filter: vec![f64::NAN; 512]
            })
            .is_err()
        );
    }
}
