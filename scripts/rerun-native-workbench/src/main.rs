use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use re_viewer_context::{CommandSender, SystemCommand, SystemCommandSender as _};
use rerun::external::{eframe, egui, re_viewer, tokio};

mod data;
mod decay_render;
mod impulse_plot;
mod live_solver;
#[path = "../../../math-audio/crates/math-dsp/src/rir_waterfall.rs"]
pub mod rir_waterfall;
mod room_render;
pub mod solver_plot;
mod workflow_ui;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum View {
    MeasuredDecay,
    Impulse,
    Room,
    Pressure,
    Rays,
    Arrivals,
    Solver,
    Workflow,
}

impl View {
    const ALL: [Self; 8] = [
        Self::MeasuredDecay,
        Self::Impulse,
        Self::Room,
        Self::Pressure,
        Self::Rays,
        Self::Arrivals,
        Self::Solver,
        Self::Workflow,
    ];

    fn label(self) -> &'static str {
        match self {
            Self::MeasuredDecay => "Spectrogram + waterfall",
            Self::Impulse => "Impulse + energy decay",
            Self::Room => "Room layout",
            Self::Pressure => "Pressure field",
            Self::Rays => "Reflection paths",
            Self::Arrivals => "Arrival timing replay",
            Self::Solver => "Solver response",
            Self::Workflow => "Study walkthrough",
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct Availability {
    pub decay: bool,
    pub room: bool,
    pub pressure: bool,
    pub rays: bool,
    pub solver: bool,
    pub workflow: bool,
}

impl Availability {
    fn supports(self, view: View) -> bool {
        match view {
            View::MeasuredDecay => self.decay,
            View::Impulse => self.decay,
            View::Room => self.room,
            View::Pressure => self.pressure,
            View::Rays => self.rays,
            View::Arrivals => self.rays,
            View::Solver => self.solver,
            View::Workflow => self.workflow,
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct Controls {
    pub channel_index: usize,
    pub repeat_index: usize,
    pub min_hz: f64,
    pub max_hz: f64,
    pub start_ms: f64,
    pub end_ms: f64,
    pub floor_db: f64,
    pub ceiling_db: f64,
    pub window_ms: f64,
    pub view: View,
}

impl Default for Controls {
    fn default() -> Self {
        Self {
            channel_index: 0,
            repeat_index: 0,
            min_hz: 20.0,
            max_hz: 2_000.0,
            start_ms: 0.0,
            end_ms: 300.0,
            floor_db: -80.0,
            ceiling_db: 0.0,
            window_ms: 128.0,
            view: View::MeasuredDecay,
        }
    }
}

impl Controls {
    fn validate(&self) -> Result<()> {
        if [
            self.min_hz,
            self.max_hz,
            self.start_ms,
            self.end_ms,
            self.floor_db,
            self.ceiling_db,
            self.window_ms,
        ]
        .iter()
        .any(|v| !v.is_finite())
            || self.min_hz <= 0.0
            || self.max_hz <= self.min_hz
            || self.start_ms < 0.0
            || self.end_ms <= self.start_ms
            || self.ceiling_db <= self.floor_db
            || !(8.0..=512.0).contains(&self.window_ms)
        {
            bail!(
                "Use ordered frequency, time and level ranges; analysis window must be 8–512 ms."
            );
        }
        Ok(())
    }
}

fn main() -> Result<()> {
    let mut args = std::env::args_os().skip(1);
    let Some(path) = args.next() else {
        bail!("Usage: rerun-native-workbench /absolute/path/to/capture.json");
    };
    if path == "--help" || path == "-h" {
        println!(
            "Usage: rerun-native-workbench CAPTURE.json [--room-study STUDY.json] [--recording FILE.rrd ...] [--solver-study DIRECTORY] [--solver-engine EXECUTABLE --solver-config PROJECT.json --solver-output NEW_DIRECTORY --solver-channels L,R]\nNative Rerun acoustic workbench. Offline studies only; no chat or receiver connection."
        );
        return Ok(());
    }
    let mut room_study = None;
    let mut recordings = Vec::new();
    let mut solver_study = None;
    let mut engine = None;
    let mut project = None;
    let mut output = None;
    let mut channels = "L,R".to_owned();
    while let Some(flag) = args.next() {
        let value = args.next().context("Expected a path after an option")?;
        if flag == "--room-study" && room_study.is_none() {
            room_study = Some(PathBuf::from(value));
        } else if flag == "--solver-study" && solver_study.is_none() {
            solver_study = Some(PathBuf::from(value));
        } else if flag == "--recording" {
            let recording = recording_path(Path::new(&value))?;
            if !recordings.contains(&recording) {
                recordings.push(recording);
            }
        } else if flag == "--solver-engine" && engine.is_none() {
            engine = Some(PathBuf::from(value).canonicalize()?);
        } else if flag == "--solver-config" && project.is_none() {
            project = Some(PathBuf::from(value).canonicalize()?);
        } else if flag == "--solver-output" && output.is_none() {
            output = Some(PathBuf::from(value));
        } else if flag == "--solver-channels" {
            channels = value.to_string_lossy().into_owned();
        } else {
            bail!("Unknown or repeated option: {}", flag.to_string_lossy());
        }
    }
    let live_config = match (engine, project, output) {
        (None, None, None) => None,
        (Some(engine), Some(project), Some(output)) => {
            let config = live_solver::Config {
                engine,
                project,
                output,
                channels,
            };
            config.validate().map_err(anyhow::Error::msg)?;
            Some(config)
        }
        _ => bail!(
            "Live study requires --solver-engine, --solver-config and --solver-output together"
        ),
    };
    let mut data = data::load_data(Path::new(&path))?;
    if let Some(room_study) = room_study {
        data::attach_room(&mut data, &room_study)?;
    }
    let solver_plot = solver_study
        .as_deref()
        .map(solver_plot::SolverPlot::load)
        .transpose()
        .map_err(anyhow::Error::msg)?;
    let controls = Controls::default();
    controls.validate()?;
    let (recording, storage) =
        rerun::RecordingStreamBuilder::new("adeze_acoustic_workbench").memory()?;
    data::refresh(&data, &controls, &recording)?;
    let (tx, rx) = re_log_channel::log_channel(re_log_channel::LogSource::Sdk);
    let flush_to_viewer = Box::new(move || -> Result<()> {
        for message in storage.take() {
            tx.send(message.into())
                .map_err(|_| anyhow::anyhow!("The native viewer disconnected."))?;
        }
        Ok(())
    });
    flush_to_viewer()?;
    let runtime = tokio::runtime::Runtime::new().context("Create native viewer runtime")?;
    let (commands, command_receiver) = re_viewer_context::command_channel();
    let main_thread = rerun::MainThreadToken::i_promise_i_am_on_the_main_thread();
    let mut options = re_viewer::native::eframe_options(None);
    options.viewport = options
        .viewport
        .with_app_id("adeze_acoustic_workbench")
        .with_inner_size([1440.0, 940.0]);
    eframe::run_native(
        "Adeze · Acoustic Workbench",
        options,
        Box::new(move |cc| {
            re_viewer::customize_eframe_and_setup_renderer(cc)?;
            let mut viewer = re_viewer::App::with_commands(
                main_thread,
                re_viewer::build_info(),
                re_viewer::AppEnvironment::Custom("Adeze Acoustic Workbench".into()),
                re_viewer::StartupOptions::default(),
                cc,
                None,
                re_viewer::AsyncRuntimeHandle::new_native(runtime.handle().clone()),
                re_viewer::register_text_log_receiver(),
                (commands.clone(), command_receiver),
            );
            viewer.add_log_receiver(rx);
            Ok(Box::new(Workbench {
                viewer,
                data,
                controls,
                recording,
                flush_to_viewer,
                error: None,
                commands,
                recordings,
                solver_plot,
                live_config,
                live: None,
                live_status: "Not running".into(),
                impulse_plot: None,
                walkthrough: workflow_ui::Walkthrough::default(),
                previous_stage: None,
                show_saved_recording: false,
                _runtime: runtime,
            }))
        }),
    )
    .map_err(|error| anyhow::anyhow!("Native viewer: {error}"))
}

struct Workbench {
    viewer: re_viewer::App,
    data: data::NativeData,
    controls: Controls,
    recording: rerun::RecordingStream,
    flush_to_viewer: Box<dyn FnMut() -> Result<()>>,
    error: Option<String>,
    commands: CommandSender,
    recordings: Vec<PathBuf>,
    solver_plot: Option<solver_plot::SolverPlot>,
    live_config: Option<live_solver::Config>,
    live: Option<live_solver::LiveSolver>,
    live_status: String,
    impulse_plot: Option<(usize, usize, impulse_plot::ImpulsePlot)>,
    walkthrough: workflow_ui::Walkthrough,
    previous_stage: Option<usize>,
    show_saved_recording: bool,
    _runtime: tokio::runtime::Runtime,
}

impl Workbench {
    fn controls(&mut self, ui: &mut egui::Ui) {
        ui.add_space(12.0);
        ui.heading("Acoustic Workbench");
        ui.label("Saved measurements · offline studies");
        ui.add_space(16.0);
        ui.strong("Current source views");
        let mut view_changed = false;
        for view in View::ALL {
            let supported = if view == View::Solver {
                self.solver_plot.is_some() || self.live_config.is_some()
            } else if view == View::Workflow {
                self.solver_plot.is_some()
            } else if view == View::Arrivals {
                self.data.has_arrival_animation()
            } else {
                self.data.availability.supports(view)
            };
            let clicked = ui
                .add_enabled(
                    supported,
                    egui::Button::selectable(
                        !self.show_saved_recording && self.controls.view == view,
                        view.label(),
                    ),
                )
                .on_disabled_hover_text("No compatible data loaded for this view.")
                .clicked();
            if clicked {
                self.controls.view = view;
                self.show_saved_recording = false;
                view_changed = true;
            }
        }
        ui.add_space(16.0);
        ui.separator();
        if let Some(config) = &self.live_config {
            ui.strong("Offline optimization");
            ui.small(format!("Channels {} · separate CSV study", config.channels));
            ui.small("2 cut-only PEQs · 20–300 Hz · 48 generations");
            if ui
                .add_enabled(
                    self.live.is_none() && !config.output.exists(),
                    egui::Button::new("Run offline study"),
                )
                .clicked()
            {
                match live_solver::LiveSolver::start(config, ui.ctx().clone()) {
                    Ok(live) => {
                        self.live = Some(live);
                        self.live_status = "Running · waiting for first callback".into();
                        self.solver_plot = None;
                        self.controls.view = View::Solver;
                        self.show_saved_recording = false;
                        self.error = None;
                    }
                    Err(error) => self.error = Some(error),
                }
            }
            if self.live.is_some() && ui.button("Stop study").clicked() {
                self.live = None;
                self.live_status = "Stopped · partial output retained".into();
            }
            ui.label(&self.live_status);
            if let Some(error) = &self.error {
                ui.colored_label(ui.visuals().error_fg_color, error);
            }
            ui.separator();
        }
        if !self.recordings.is_empty() {
            ui.strong("Saved recordings");
            for path in &self.recordings {
                let label = path.file_name().unwrap_or_default().to_string_lossy();
                if ui
                    .button(label.as_ref())
                    .on_hover_text(path.display().to_string())
                    .clicked()
                {
                    show_recording(&self.commands, path);
                    self.show_saved_recording = true;
                }
            }
            ui.small("Opens the recording's own views. Format compatibility is checked by Rerun.");
            ui.add_space(12.0);
        }
        if self.show_saved_recording || matches!(self.controls.view, View::Solver | View::Workflow)
        {
            ui.small("Use the selected recording or solver panel's own display controls.");
            return;
        }
        if !matches!(self.controls.view, View::MeasuredDecay | View::Impulse) {
            if view_changed {
                self.apply_view();
            }
            if self.controls.view == View::Arrivals {
                ui.label("Saved FL → MLP direct and reflected paths.");
                ui.small("Slow geometric replay · physical milliseconds shown. Single speaker; no pressure interference.");
                egui::Grid::new("arrival_times")
                    .striped(true)
                    .show(ui, |ui| {
                        for (label, time) in self.data.arrival_times_ms() {
                            ui.label(label);
                            ui.label(format!("{time:.2} ms"));
                            ui.end_row();
                        }
                    });
            } else {
                ui.small("Use Rerun's camera and timeline controls for this saved room study.");
            }
            if let Some(error) = &self.error {
                ui.colored_label(ui.visuals().error_fg_color, error);
            }
            return;
        }
        ui.strong("Measurement");
        selector(
            ui,
            "Speaker",
            "channel",
            &self.data.channels,
            &mut self.controls.channel_index,
        );
        selector(
            ui,
            "Repeat",
            "repeat",
            &self.data.repeats,
            &mut self.controls.repeat_index,
        );
        ui.add_space(12.0);
        ui.strong("Shared display ranges");
        if self.controls.view != View::Impulse {
            range(
                ui,
                "Frequency",
                &mut self.controls.min_hz,
                &mut self.controls.max_hz,
                " Hz",
                1.0,
            );
        }
        range(
            ui,
            "Time",
            &mut self.controls.start_ms,
            &mut self.controls.end_ms,
            " ms",
            1.0,
        );
        if self.controls.view == View::Impulse {
            ui.horizontal(|ui| {
                ui.label("Decay floor");
                ui.add(
                    egui::DragValue::new(&mut self.controls.floor_db)
                        .range(-160.0..=-1.0)
                        .suffix(" dB"),
                );
            });
            ui.small("Impulse is peak-normalized; decay is relative integrated energy.");
        } else {
            range(
                ui,
                "Level",
                &mut self.controls.floor_db,
                &mut self.controls.ceiling_db,
                " dB",
                0.5,
            );
            ui.label("Relative to the measured response reference; not calibrated SPL.");
        }
        if self.controls.view != View::Impulse {
            ui.add_space(12.0);
            ui.strong("Analysis");
            ui.horizontal(|ui| {
                ui.label("Window");
                ui.add(
                    egui::DragValue::new(&mut self.controls.window_ms)
                        .range(8.0..=512.0)
                        .suffix(" ms")
                        .speed(1.0),
                );
            });
            ui.small("Longer windows resolve bass more finely and smear changes in time.");
        }
        ui.add_space(12.0);
        if ui
            .button(if self.controls.view == View::Impulse {
                "Apply to impulse views"
            } else {
                "Apply to both views"
            })
            .clicked()
            || view_changed
        {
            self.apply_view();
        }
        if self.controls.view == View::MeasuredDecay && ui.button("Reset decay framing").clicked() {
            data::reset_view(&self.data);
            self.apply_view();
        }
        if let Some(error) = &self.error {
            ui.colored_label(ui.visuals().error_fg_color, error);
        }
        ui.add_space(16.0);
        ui.small(
            "Calibration unchanged. Chat disabled. Missing simulation data stays unavailable.",
        );
    }

    fn update_live(&mut self) -> Result<(), String> {
        let Some(live) = &mut self.live else {
            return Ok(());
        };
        // Bound UI work per frame; the pipe applies backpressure rather than dropping callbacks.
        for _ in 0..4 {
            let Some(event) = live.next()? else {
                break;
            };
            if live.completion_received {
                return Err("Solver sent data after completion".into());
            }
            match event["type"].as_str() {
                Some("chart") if self.solver_plot.is_none() => {
                    self.solver_plot = Some(solver_plot::SolverPlot::from_chart(&event["chart"])?);
                }
                Some("callback") => {
                    self.solver_plot
                        .as_mut()
                        .ok_or("Callback arrived before chart metadata")?
                        .append(&event["callback"])?;
                    self.live_status = format!(
                        "Running · {} callback {} · generation {}",
                        event["callback"]["channel"].as_str().unwrap_or("?"),
                        event["callback"]["callback_index"],
                        event["callback"]["generation"]
                    );
                }
                Some("complete") => {
                    self.solver_plot
                        .as_ref()
                        .ok_or("Completion arrived before chart metadata")?
                        .verify_complete(&event["studies"])?;
                    live.completion_received = true;
                    self.live_status = "Final callbacks verified · finishing output".into();
                }
                _ => return Err("Unexpected or duplicate solver metadata".into()),
            }
        }
        if live.finished()? {
            self.live = None;
            self.live_status = "Complete · all callbacks and terminal receipts verified".into();
        }
        Ok(())
    }

    fn apply_view(&mut self) {
        if self.controls.view == View::Impulse {
            self.error = self
                .controls
                .validate()
                .and_then(|()| {
                    let (samples, rate, peak) = self.data.selected_impulse(&self.controls)?;
                    let plot = impulse_plot::ImpulsePlot::new(samples, rate, peak)
                        .map_err(anyhow::Error::msg)?;
                    self.impulse_plot = Some((
                        self.controls.channel_index,
                        self.controls.repeat_index,
                        plot,
                    ));
                    Ok(())
                })
                .err()
                .map(|e| e.to_string());
            return;
        }
        self.error = self
            .controls
            .validate()
            .and_then(|()| data::refresh(&self.data, &self.controls, &self.recording))
            .and_then(|()| (self.flush_to_viewer)())
            .map(|()| {
                if let Some(info) = self.recording.store_info() {
                    self.commands.send_system(SystemCommand::SetRoute(
                        re_viewer_context::Route::LocalRecording {
                            recording_id: info.store_id,
                        },
                    ));
                }
            })
            .err()
            .map(|error| format!("{error:#}"));
    }
}

impl eframe::App for Workbench {
    fn ui(&mut self, ui: &mut egui::Ui, frame: &mut eframe::Frame) {
        if let Err(error) = self.update_live() {
            self.live = None;
            self.live_status = "Failed · partial output retained".into();
            self.error = Some(error);
        }
        if self.live.is_some() {
            ui.ctx()
                .request_repaint_after(std::time::Duration::from_millis(16));
        }
        // Keep the embedded viewer's optional agent panel disabled too.
        self.viewer.app_options_mut().experimental.agent_panel = false;
        egui::Panel::left("adeze_controls")
            .default_size(292.0)
            .resizable(true)
            .show(ui, |ui| {
                egui::ScrollArea::vertical().show(ui, |ui| self.controls(ui));
            });
        if self.controls.view == View::Solver && !self.show_saved_recording {
            egui::CentralPanel::default().show(ui, |ui| {
                if let Some(plot) = &mut self.solver_plot {
                    plot.ui(ui);
                }
            });
        } else if self.controls.view == View::Workflow && !self.show_saved_recording {
            egui::CentralPanel::default().show(ui, |ui| {
                let stage = self.walkthrough.ui(ui);
                let changed = self.previous_stage != Some(stage);
                self.previous_stage = Some(stage);
                if let Some(plot) = &mut self.solver_plot {
                    plot.stage_ui(ui, stage, changed);
                }
            });
        } else if self.controls.view == View::Impulse && !self.show_saved_recording {
            egui::CentralPanel::default().show(ui, |ui| {
                if let Some((channel, repeat, plot)) = &mut self.impulse_plot {
                    ui.label(format!(
                        "{} · repeat {}",
                        self.data.channels[*channel], self.data.repeats[*repeat]
                    ));
                    plot.ui(
                        ui,
                        self.controls.start_ms,
                        self.controls.end_ms,
                        self.controls.floor_db,
                    );
                }
            });
        } else {
            self.viewer.ui(ui, frame);
        }
    }

    fn logic(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        self.viewer.logic(ctx, frame);
    }

    fn save(&mut self, storage: &mut dyn eframe::Storage) {
        self.viewer.save(storage);
    }
}

fn recording_path(path: &Path) -> Result<PathBuf> {
    if path.extension().and_then(|v| v.to_str()) != Some("rrd") {
        bail!("Expected a local .rrd recording: {}", path.display());
    }
    let metadata = path
        .metadata()
        .with_context(|| format!("Read recording: {}", path.display()))?;
    if !metadata.is_file() || metadata.len() > 512 * 1024 * 1024 {
        bail!(
            "Recording must be a regular file no larger than 512 MiB: {}",
            path.display()
        );
    }
    path.canonicalize().context("Resolve recording path")
}

fn show_recording(commands: &CommandSender, path: &Path) {
    commands.send_system(SystemCommand::LoadDataSource(
        re_data_source::LogDataSource::File {
            file_source: re_log_msg::FileSource::Cli,
            path: path.to_path_buf(),
            assets: Vec::new(),
        },
    ));
}

fn selector(ui: &mut egui::Ui, label: &str, id: &str, values: &[String], index: &mut usize) {
    ui.label(label);
    egui::ComboBox::from_id_salt(id)
        .selected_text(values.get(*index).map_or("Unavailable", String::as_str))
        .width(ui.available_width())
        .show_ui(ui, |ui| {
            for (i, value) in values.iter().enumerate() {
                ui.selectable_value(index, i, value);
            }
        });
}

fn range(ui: &mut egui::Ui, label: &str, low: &mut f64, high: &mut f64, suffix: &str, speed: f64) {
    ui.label(label);
    ui.horizontal(|ui| {
        ui.add(egui::DragValue::new(low).speed(speed).suffix(suffix));
        ui.label("to");
        ui.add(egui::DragValue::new(high).speed(speed).suffix(suffix));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recording_paths_require_bounded_local_files() {
        let directory = std::env::temp_dir().join(format!(
            "adeze-native-recording-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
        ));
        std::fs::create_dir(&directory).unwrap();
        let path = directory.join("fixture.rrd");
        let file = std::fs::File::create(&path).unwrap();
        assert_eq!(recording_path(&path).unwrap(), path.canonicalize().unwrap());
        assert!(recording_path(&directory.join("missing.rrd")).is_err());
        assert!(recording_path(&directory.join("wrong.json")).is_err());
        file.set_len(512 * 1024 * 1024 + 1).unwrap();
        assert!(recording_path(&path).is_err());
        drop(file);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn shared_ranges_reject_non_finite_and_reversed_values() {
        let mut controls = Controls::default();
        assert!(controls.validate().is_ok());
        controls.max_hz = controls.min_hz;
        assert!(controls.validate().is_err());
        controls = Controls::default();
        controls.start_ms = controls.end_ms;
        assert!(controls.validate().is_err());
        controls = Controls::default();
        controls.floor_db = f64::NAN;
        assert!(controls.validate().is_err());
        controls = Controls::default();
        controls.window_ms = 0.0;
        assert!(controls.validate().is_err());
    }
}
