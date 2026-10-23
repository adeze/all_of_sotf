use crate::{Availability, Controls, View, decay_render, rir_waterfall};
use anyhow::{Context, Result, bail, ensure};
use rerun::blueprint::components::PanelState;
use rerun::blueprint::{
    Blueprint, BlueprintActivation, BlueprintPanel, ContainerLike, Horizontal, SelectionPanel,
    Spatial2DView, Spatial3DView, TimePanel,
};
use rir_waterfall::{SharedDisplayControls, WaterfallAnalysisConfig, WaterfallGrid};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    cell::{Cell, RefCell},
    collections::BTreeSet,
    fs,
    path::Path,
};

struct Impulse {
    channel: String,
    repeat: String,
    samples: Vec<f32>,
    peak: usize,
}

pub struct NativeData {
    pub channels: Vec<String>,
    pub repeats: Vec<String>,
    pub availability: Availability,
    sample_rate: f64,
    position: String,
    digest: String,
    impulses: Vec<Impulse>,
    cached: RefCell<Option<(usize, f64, WaterfallGrid)>>,
    room: Option<crate::room_render::RoomStudy>,
    room_logged: Cell<bool>,
    active_view: Cell<Option<View>>,
}

impl NativeData {
    pub fn selected_impulse(&self, controls: &Controls) -> Result<(&[f32], f64, usize)> {
        let channel = self
            .channels
            .get(controls.channel_index)
            .context("Invalid channel selection")?;
        let repeat = self
            .repeats
            .get(controls.repeat_index)
            .context("Invalid repeat selection")?;
        let impulse = self
            .impulses
            .iter()
            .find(|impulse| &impulse.channel == channel && &impulse.repeat == repeat)
            .context("Selected channel/repeat unavailable")?;
        Ok((&impulse.samples, self.sample_rate, impulse.peak))
    }
}

pub fn reset_view(data: &NativeData) {
    data.active_view.set(None);
}

pub fn load_data(path: &Path) -> Result<NativeData> {
    ensure!(
        fs::metadata(path)?.len() <= 16 * 1024 * 1024,
        "Capture exceeds 16 MiB"
    );
    let bytes = fs::read(path)?;
    ensure!(bytes.len() <= 16 * 1024 * 1024, "Capture exceeds 16 MiB");
    let value: Value = serde_json::from_slice(&bytes).context("Invalid native capture JSON")?;
    parse_capture(&value, format!("{:x}", Sha256::digest(&bytes)))
}

fn parse_capture(value: &Value, digest: String) -> Result<NativeData> {
    let sample_rate = value["sampleRateHz"]
        .as_f64()
        .context("Missing sampleRateHz")?;
    ensure!(
        sample_rate.is_finite() && (8000. ..=192000.).contains(&sample_rate),
        "Invalid sample rate"
    );
    let position = value["positionID"]
        .as_str()
        .context("Missing positionID")?
        .to_owned();
    let records = value["records"]
        .as_array()
        .context("Missing native impulse records")?;
    ensure!(
        !records.is_empty() && records.len() <= 256,
        "Capture requires 1–256 records"
    );
    let mut impulses = Vec::new();
    let mut identities = BTreeSet::new();
    let mut channels = Vec::new();
    let mut repeats = Vec::new();
    for record in records {
        let channel = record["channelID"]
            .as_str()
            .context("Missing channelID")?
            .to_owned();
        ensure!(
            !channel.is_empty() && channel.len() <= 64,
            "Invalid channelID"
        );
        let repeat = record["repeatNumber"]
            .as_u64()
            .filter(|r| (1..=256).contains(r))
            .context("Invalid repeatNumber")?
            .to_string();
        ensure!(
            identities.insert((channel.clone(), repeat.clone())),
            "Duplicate channel/repeat"
        );
        let samples = record["impulseResponse"]
            .as_array()
            .context("Missing impulseResponse")?;
        ensure!(
            (16..=1_048_576).contains(&samples.len()),
            "Invalid impulse length"
        );
        let samples = samples
            .iter()
            .map(|sample| {
                let value = sample.as_f64().context("Invalid impulse sample")? as f32;
                ensure!(value.is_finite(), "Nonfinite impulse sample");
                Ok(value)
            })
            .collect::<Result<Vec<_>>>()?;
        let peak = samples
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.abs().total_cmp(&b.1.abs()))
            .unwrap()
            .0;
        if !channels.contains(&channel) {
            channels.push(channel.clone());
        }
        if !repeats.contains(&repeat) {
            repeats.push(repeat.clone());
        }
        // Native capture samples already include the receiver response coefficient.
        impulses.push(Impulse {
            channel,
            repeat,
            samples,
            peak,
        });
    }
    Ok(NativeData {
        channels,
        repeats,
        availability: Availability {
            decay: true,
            room: false,
            pressure: false,
            rays: false,
            solver: false,
            workflow: false,
        },
        sample_rate,
        position,
        digest,
        impulses,
        cached: RefCell::new(None),
        room: None,
        room_logged: Cell::new(false),
        active_view: Cell::new(None),
    })
}

pub fn attach_room(data: &mut NativeData, path: &Path) -> Result<()> {
    let room = crate::room_render::load(path).map_err(anyhow::Error::msg)?;
    data.availability.room = room.has_geometry();
    data.availability.pressure = room.has_pressure();
    data.availability.rays = room.has_rays();
    data.room = Some(room);
    Ok(())
}

impl NativeData {
    pub fn has_arrival_animation(&self) -> bool {
        self.room
            .as_ref()
            .and_then(|room| room.arrival_duration())
            .is_some()
    }
    pub fn arrival_times_ms(&self) -> Vec<(String, f64)> {
        self.room
            .as_ref()
            .map(|room| room.arrival_times_ms())
            .unwrap_or_default()
    }
}

fn select_view(data: &NativeData, controls: &Controls, rec: &rerun::RecordingStream) -> Result<()> {
    let view = controls.view;
    if view == View::Impulse {
        return Ok(());
    }
    if data.active_view.get() == Some(view) {
        return Ok(());
    }
    let mut camera = None;
    let root: ContainerLike = match view {
        View::MeasuredDecay => {
            let waterfall: ContainerLike =
                Spatial3DView::new("Waterfall · log Hz / ms / relative dB")
                    .with_contents(["/decay/waterfall/**"])
                    .into();
            if let ContainerLike::View(view) = &waterfall {
                let center = [
                    (15.0 * (controls.min_hz.log10() + controls.max_hz.log10())) as f32,
                    ((controls.floor_db + controls.ceiling_db) * 0.5) as f32,
                    ((controls.start_ms + controls.end_ms) * 0.05) as f32,
                ];
                let span = (30.0 * (controls.max_hz / controls.min_hz).log10())
                    .max(controls.ceiling_db - controls.floor_db)
                    .max(0.1 * (controls.end_ms - controls.start_ms))
                    as f32;
                camera = Some((
                    format!("{}/EyeControls3D", view.blueprint_path()),
                    rerun::external::re_sdk_types::blueprint::archetypes::EyeControls3D::new()
                        .with_position([
                            center[0] + 0.8 * span,
                            center[1] + 1.2 * span,
                            center[2] + 2.2 * span,
                        ])
                        .with_look_target(center)
                        .with_eye_up([0.0, 1.0, 0.0]),
                ));
            }
            Horizontal::new([
                Spatial2DView::new("Spectrogram · log Hz / ms · shared relative dB")
                    .with_contents(["/decay/spectrogram", "/decay/spectrogram/**"])
                    .into(),
                waterfall,
            ])
            .into()
        }
        View::Room => Spatial3DView::new("Saved room geometry · metres")
            .with_contents(["/room/**"])
            .into(),
        View::Rays => Spatial3DView::new("Simulated first reflections · metres")
            .with_contents(["/rays/**"])
            .into(),
        View::Arrivals => Spatial3DView::new("FL → MLP arrivals · saved geometric simulation")
            .with_contents(["/timing/**"])
            .into(),
        _ => bail!("Select the loaded recording for this visualization"),
    };
    let blueprint = Blueprint::new(root)
        .with_auto_layout(false)
        .with_auto_views(false)
        .with_blueprint_panel(BlueprintPanel::new().with_state(PanelState::Hidden))
        .with_selection_panel(SelectionPanel::new().with_state(PanelState::Hidden))
        .with_time_panel(
            TimePanel::new()
                .with_timeline(if view == View::Arrivals {
                    "arrival_frame"
                } else {
                    "reflection_arrival"
                })
                .with_fps(30.0)
                .with_state(if matches!(view, View::Rays | View::Arrivals) {
                    PanelState::Expanded
                } else {
                    PanelState::Hidden
                }),
        );
    if let Some((path, eye)) = camera {
        send_with_camera(rec, &blueprint, &path, &eye)?;
    } else {
        blueprint.send(rec, BlueprintActivation::default())?;
    }
    data.active_view.set(Some(view));
    Ok(())
}

fn send_with_camera(
    recording: &rerun::RecordingStream,
    blueprint: &Blueprint,
    path: &str,
    eye: &rerun::external::re_sdk_types::blueprint::archetypes::EyeControls3D,
) -> Result<()> {
    // The 0.40 view builder has no camera setter; append its public archetype before activation.
    let application = recording
        .store_info()
        .context("Native recording unavailable")?
        .application_id()
        .clone();
    let (staging, storage) = rerun::RecordingStreamBuilder::new(application).memory()?;
    blueprint.send(&staging, BlueprintActivation::default())?;
    let staged = storage.take();
    let activation = staged
        .iter()
        .find_map(|message| match message {
            re_log_msg::LogMsg::BlueprintActivationCommand(command) => Some(command.clone()),
            _ => None,
        })
        .context("Decay blueprint did not produce an activation command")?;
    let mut messages: Vec<_> = staged
        .into_iter()
        .filter(|message| {
            !matches!(message, re_log_msg::LogMsg::BlueprintActivationCommand(_))
                && message.store_id() == &activation.blueprint_id
        })
        .collect();
    let (properties, property_storage) =
        rerun::RecordingStreamBuilder::from_store_id(&activation.blueprint_id).memory()?;
    properties.set_time_sequence("blueprint", 0);
    properties.log(path, eye)?;
    messages.extend(
        property_storage
            .take()
            .into_iter()
            .filter(|message| !matches!(message, re_log_msg::LogMsg::SetStoreInfo(_))),
    );
    recording.send_blueprint(messages, activation);
    Ok(())
}

pub fn refresh(data: &NativeData, controls: &Controls, rec: &rerun::RecordingStream) -> Result<()> {
    if !matches!(controls.view, View::MeasuredDecay) {
        let room = data.room.as_ref().context("No room study loaded")?;
        if !data.room_logged.get() {
            if room.arrival_duration().is_some() {
                room.log_arrival_animation(rec)
                    .map_err(anyhow::Error::msg)?;
            } else {
                crate::room_render::log(rec, room).map_err(anyhow::Error::msg)?;
            }
            data.room_logged.set(true);
        }
        return select_view(data, controls, rec);
    }
    let channel = data
        .channels
        .get(controls.channel_index)
        .context("Invalid channel selection")?;
    let repeat = data
        .repeats
        .get(controls.repeat_index)
        .context("Invalid repeat selection")?;
    let (index, impulse) = data
        .impulses
        .iter()
        .enumerate()
        .find(|(_, ir)| &ir.channel == channel && &ir.repeat == repeat)
        .context("Selected channel/repeat unavailable")?;
    let shared = SharedDisplayControls {
        min_hz: controls.min_hz,
        max_hz: controls.max_hz,
        start_ms: controls.start_ms,
        end_ms: controls.end_ms,
        floor_db: controls.floor_db,
        ceiling_db: controls.ceiling_db,
    };
    shared.validate()?;
    ensure!(
        shared.max_hz <= data.sample_rate / 2.,
        "Frequency exceeds native Nyquist"
    );
    let mut cache = data.cached.borrow_mut();
    if !cache
        .as_ref()
        .is_some_and(|(id, window, _)| *id == index && *window == controls.window_ms)
    {
        let config = WaterfallAnalysisConfig {
            window_ms: controls.window_ms,
            ..Default::default()
        };
        let grid = rir_waterfall::analyze_waterfall_at(
            &impulse.samples,
            data.sample_rate,
            impulse.peak,
            &config,
        )?;
        *cache = Some((index, controls.window_ms, grid));
    }
    let label = format!("{channel} · repeat {repeat} · {}", data.position);
    decay_render::log_views(rec, &cache.as_ref().unwrap().2, &shared, &label)
        .map_err(anyhow::Error::msg)?;
    let metadata = json!({"source_sha256":data.digest,"position":data.position,"channel":channel,"repeat":repeat,
        "sample_rate_hz":data.sample_rate,"samples":impulse.samples.len(),"native_duration_ms":1000.*impulse.samples.len() as f64/data.sample_rate,
        "display_time_zero":"absolute impulse peak; display reference only, not physical time of flight",
        "display_zero_native_sample":impulse.peak,"analysis_window_ms":controls.window_ms,"hop_ms":4.,
        "level_reference":"analysis grid peak; not calibrated SPL; unchanged when display ranges change",
        "processing":"saved native IR; response coefficient already applied; no deconvolution or correction",
        "calibration_changed":false,"chat_enabled":false});
    rec.log_static(
        "decay/source",
        &rerun::TextDocument::from_markdown(format!(
            "## {label}\n```json\n{}\n```",
            serde_json::to_string_pretty(&metadata)?
        )),
    )?;
    select_view(data, controls, rec)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_capture_validation_preserves_samples_and_rejects_ambiguous_identity() {
        let record = json!({"channelID":"FL","repeatNumber":1,"channelStart":{"responseCoefficient":1000},"impulseResponse":[0.,0.,1.,0.,0.,0.,0.,0.,0.,0.,0.,0.,0.,0.,0.,0.]});
        let input = json!({"sampleRateHz":48000,"positionID":"fixture","records":[record.clone()]});
        let data = parse_capture(&input, "fixture".into()).unwrap();
        assert_eq!(data.impulses[0].samples[2], 1.);
        assert_eq!(data.impulses[0].peak, 2);
        assert_eq!(
            data.selected_impulse(&Controls::default()).unwrap().1,
            48000.0
        );
        assert!(
            data.selected_impulse(&Controls {
                channel_index: usize::MAX,
                ..Default::default()
            })
            .is_err()
        );
        let (recording, storage) = rerun::RecordingStreamBuilder::new("camera_test")
            .memory()
            .unwrap();
        select_view(&data, &Controls::default(), &recording).unwrap();
        let messages = storage.take();
        assert!(
            messages.iter().any(|message| matches!(
                message,
                re_log_msg::LogMsg::BlueprintActivationCommand(_)
            ))
        );
        assert_eq!(data.active_view.get(), Some(View::MeasuredDecay));
        select_view(&data, &Controls::default(), &recording).unwrap();
        assert!(
            storage.take().is_empty(),
            "Ordinary edits must not reset the camera"
        );
        let mut invalid = input.clone();
        invalid["records"] = json!([record.clone(), record]);
        assert!(parse_capture(&invalid, "fixture".into()).is_err());
        invalid = input.clone();
        invalid["sampleRateHz"] = json!(0);
        assert!(parse_capture(&invalid, "fixture".into()).is_err());
        invalid = input;
        invalid["records"][0]["impulseResponse"][0] = json!(1e100);
        assert!(parse_capture(&invalid, "fixture".into()).is_err());
    }
}
