//! Fixed native chart shared by saved history and synchronous solver callbacks.
use std::{
    fs::File,
    io::Read,
    path::Path,
    time::{Duration, Instant},
};

use egui_plot::{Legend, Line, Plot, Polygon, VLine};
use rerun::external::egui::{self, Color32};
use serde_json::Value;

const MODES: [&str; 7] = [
    "raw",
    "octaveThird",
    "octaveSixth",
    "octaveTwelfth",
    "octaveTwentyFourth",
    "octaveFortyEighth",
    "psychoacoustic",
];
const LABELS: [&str; 7] = [
    "Raw",
    "1/3 octave",
    "1/6 octave",
    "1/12 octave",
    "1/24 octave",
    "1/48 octave",
    "Psychoacoustic",
];

struct Band {
    best: Vec<f64>,
    min: Vec<f64>,
    max: Vec<f64>,
    p10: Vec<f64>,
    p90: Vec<f64>,
}
struct Callback {
    channel: usize,
    generation: usize,
    index: usize,
    loss: f64,
    modes: Vec<Band>,
    terminal: bool,
    params: [f64; 6],
}

pub struct SolverPlot {
    channels: Vec<String>,
    selected: Vec<bool>,
    frequencies: Vec<f64>,
    callbacks: Vec<Callback>,
    domain: [f64; 4],
    target: f64,
    mode: usize,
    generation: usize,
    max_generation: usize,
    playing: bool,
    last_frame: Instant,
    reset: bool,
    following: bool,
}

fn bounded_text(path: &Path) -> Result<String, String> {
    let mut text = String::new();
    File::open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?
        .take(64 * 1024 * 1024 + 1)
        .read_to_string(&mut text)
        .map_err(|e| e.to_string())?;
    if text.len() > 64 * 1024 * 1024 {
        return Err("Solver study input exceeds 64 MiB".into());
    }
    Ok(text)
}
fn finite(value: &Value) -> Result<f64, String> {
    value
        .as_f64()
        .filter(|v| v.is_finite())
        .ok_or_else(|| "Expected finite solver value".into())
}
fn values(value: &Value, expected: Option<usize>) -> Result<Vec<f64>, String> {
    let array = value.as_array().ok_or("Expected solver numeric array")?;
    if array.len() < 2 || array.len() > 4096 || expected.is_some_and(|n| n != array.len()) {
        return Err("Solver numeric array length mismatch".into());
    }
    array.iter().map(finite).collect()
}
fn integer(value: &Value) -> Result<usize, String> {
    value
        .as_u64()
        .filter(|v| *v <= 1_000_000)
        .map(|v| v as usize)
        .ok_or_else(|| "Invalid solver generation or callback index".into())
}

fn band_cell(frequencies: &[f64], low: &[f64], high: &[f64], i: usize) -> [[f64; 2]; 4] {
    let x0 = frequencies[i].log10();
    let x1 = frequencies[i + 1].log10();
    [
        [x0, low[i]],
        [x1, low[i + 1]],
        [x1, high[i + 1]],
        [x0, high[i]],
    ]
}
fn visible_band(low: &[f64], high: &[f64], i: usize) -> bool {
    // Subpixel-thin convex fills have unstable anti-alias miters; retain data, omit only their fill.
    (high[i] - low[i]).max(high[i + 1] - low[i + 1]) >= 0.01
}

impl SolverPlot {
    pub fn load(path: &Path) -> Result<Self, String> {
        let chart: Value = serde_json::from_str(&bounded_text(&path.join("chart.json"))?)
            .map_err(|e| e.to_string())?;
        Self::parse(&chart, &bounded_text(&path.join("callbacks.jsonl"))?)
    }
    fn parse(chart: &Value, text: &str) -> Result<Self, String> {
        let mut plot = Self::from_chart(chart)?;
        for line in text.lines().filter(|s| !s.trim().is_empty()) {
            plot.append(&serde_json::from_str(line).map_err(|e| e.to_string())?)?;
        }
        if plot.callbacks.is_empty()
            || (0..plot.channels.len()).any(|i| !plot.callbacks.iter().any(|c| c.channel == i))
        {
            return Err("Missing solver callbacks for a channel".into());
        }
        plot.following = false;
        plot.generation = 0;
        Ok(plot)
    }
    pub fn from_chart(chart: &Value) -> Result<Self, String> {
        if chart["transfer_prohibited"] != true {
            return Err("Expected transfer-prohibited solver study".into());
        }
        let channels = chart["channels"]
            .as_array()
            .filter(|a| !a.is_empty() && a.len() <= 8)
            .ok_or("Expected 1–8 solver channels")?
            .iter()
            .map(|v| {
                v.as_str()
                    .filter(|s| !s.is_empty() && s.len() <= 64)
                    .map(str::to_owned)
                    .ok_or_else(|| "Invalid solver channel".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        if channels
            .iter()
            .enumerate()
            .any(|(i, c)| channels[..i].contains(c))
        {
            return Err("Duplicate solver channel".into());
        }
        let declared = chart["smoothing_modes"]
            .as_array()
            .ok_or("Missing smoothing modes")?;
        if declared.len() != 7
            || declared
                .iter()
                .zip(MODES)
                .any(|(v, m)| v.as_str() != Some(m))
        {
            return Err("Unsupported solver smoothing modes".into());
        }
        let d = &chart["domain"];
        let domain = [
            finite(&d["minHz"])?,
            finite(&d["maxHz"])?,
            finite(&d["minDb"])?,
            finite(&d["maxDb"])?,
        ];
        if domain[0] <= 0. || domain[0] >= domain[1] || domain[2] >= domain[3] {
            return Err("Invalid solver chart domain".into());
        }
        let target = finite(&chart["target_db"])?;
        Ok(Self {
            selected: vec![true; channels.len()],
            channels,
            frequencies: Vec::new(),
            callbacks: Vec::new(),
            domain,
            target,
            mode: 0,
            generation: 0,
            max_generation: 0,
            playing: false,
            last_frame: Instant::now(),
            reset: true,
            following: true,
        })
    }
    pub fn append(&mut self, row: &Value) -> Result<(), String> {
        if self.callbacks.len() >= 10_000 {
            return Err("Solver callbacks exceed 10000".into());
        }
        let channel = self
            .channels
            .iter()
            .position(|c| Some(c.as_str()) == row["channel"].as_str())
            .ok_or("Unknown solver callback channel")?;
        let generation = integer(&row["generation"])?;
        let index = integer(&row["callback_index"])?;
        let terminal = if row["terminal"].is_null() {
            false
        } else {
            row["terminal"]["finalized"]
                .as_bool()
                .ok_or("Invalid canonical solver terminal state")?
        };
        if let Some(previous) = self.callbacks.iter().rev().find(|c| c.channel == channel) {
            if previous.terminal
                || index != previous.index + 1
                || (generation != previous.generation + 1
                    && !(terminal && generation == previous.generation))
            {
                return Err("Solver callbacks must be contiguous per channel".into());
            }
        } else if index != 1 || generation != 0 {
            return Err("Solver callbacks must begin at index 1, generation 0".into());
        }
        let loss = finite(&row["loss"])?;
        let params: [f64; 6] = values(&row["best_params"], Some(6))?
            .try_into()
            .map_err(|_| "Expected six PK parameters")?;
        let f = values(&row["frequency_hz"], None)?;
        if f[0] <= 0. || f.windows(2).any(|w| w[0] >= w[1]) {
            return Err("Invalid solver frequency grid".into());
        }
        if !self.frequencies.is_empty() && self.frequencies != f {
            return Err("Solver frequency grids differ".into());
        }
        if (self.callbacks.len() + 1) * f.len() * 35 > 4_000_000 {
            return Err("Solver study exceeds display value budget".into());
        }
        let mut modes = Vec::with_capacity(7);
        for mode in MODES {
            let e = &row["envelopes"][mode];
            let band = Band {
                best: values(&row["display_best_db"][mode], Some(f.len()))?,
                min: values(&e["min"], Some(f.len()))?,
                max: values(&e["max"], Some(f.len()))?,
                p10: values(&e["p10"], Some(f.len()))?,
                p90: values(&e["p90"], Some(f.len()))?,
            };
            if (0..f.len()).any(|i| {
                band.min[i] > band.p10[i] || band.p10[i] > band.p90[i] || band.p90[i] > band.max[i]
            }) {
                return Err("Solver envelope bands are unordered".into());
            }
            modes.push(band);
        }
        self.frequencies = f;
        self.callbacks.push(Callback {
            channel,
            generation,
            index,
            loss,
            modes,
            terminal,
            params,
        });
        self.max_generation = self.max_generation.max(generation);
        if self.following {
            self.generation = self.max_generation;
        }
        Ok(())
    }
    pub fn verify_complete(&self, studies: &Value) -> Result<(), String> {
        let studies = studies
            .as_array()
            .ok_or("Missing solver completion receipts")?;
        if studies.len() != self.channels.len() {
            return Err("Incomplete solver channel receipts".into());
        }
        for (i, name) in self.channels.iter().enumerate() {
            let last = self
                .callbacks
                .iter()
                .rev()
                .find(|c| c.channel == i)
                .ok_or("Missing solver terminal callback")?;
            let receipt = studies
                .iter()
                .find(|s| s["channel"].as_str() == Some(name))
                .ok_or("Missing solver channel receipt")?;
            if !last.terminal
                || integer(&receipt["callbacks"])? != last.index
                || integer(&receipt["last_generation"])? != last.generation
                || finite(&receipt["final_loss"])? != last.loss
            {
                return Err("Solver receipt does not match terminal callback".into());
            }
        }
        Ok(())
    }

    pub fn ui(&mut self, ui: &mut egui::Ui) {
        self.draw(ui, true);
    }
    fn draw(&mut self, ui: &mut egui::Ui, show_heading: bool) {
        if show_heading {
            ui.heading("Solver response");
            ui.label("Offline solver study; separate from the native impulse-response selection. Display smoothing does not change filters.");
        }
        ui.horizontal_wrapped(|ui| {
            for (i, name) in self.channels.iter().enumerate() {
                ui.checkbox(&mut self.selected[i], name);
            }
            egui::ComboBox::from_id_salt("solver_smoothing")
                .selected_text(LABELS[self.mode])
                .show_ui(ui, |ui| {
                    for (i, label) in LABELS.iter().enumerate() {
                        ui.selectable_value(&mut self.mode, i, *label);
                    }
                });
            if ui
                .button(if self.playing {
                    "Pause generations"
                } else {
                    "Replay generations"
                })
                .clicked()
            {
                if !self.playing && self.generation == self.max_generation {
                    self.generation = 0;
                }
                self.playing = !self.playing;
                self.following = false;
                self.last_frame = Instant::now();
            }
            if ui.checkbox(&mut self.following, "Follow live").changed() && self.following {
                self.generation = self.max_generation;
                self.playing = false;
            }
            ui.label(format!("{} callbacks received", self.callbacks.len()));
            if ui.button("Reset axes").clicked() {
                self.reset = true;
            }
        });
        if self.playing {
            if self.last_frame.elapsed() >= Duration::from_millis(160) {
                self.generation = (self.generation + 1).min(self.max_generation);
                self.playing = self.generation < self.max_generation;
                self.last_frame = Instant::now();
            }
            ui.ctx().request_repaint_after(Duration::from_millis(160));
        }
        if ui
            .add(
                egui::Slider::new(&mut self.generation, 0..=self.max_generation).text("Generation"),
            )
            .changed()
        {
            self.playing = false;
            self.following = false;
        }
        let colors = [
            Color32::from_rgb(65, 155, 235),
            Color32::from_rgb(240, 153, 59),
            Color32::from_rgb(117, 191, 134),
            Color32::from_rgb(190, 130, 220),
            Color32::from_rgb(225, 100, 135),
            Color32::from_rgb(100, 200, 195),
            Color32::from_rgb(200, 190, 100),
            Color32::from_rgb(160, 165, 180),
        ];
        ui.small("Shading: lighter min/max population; darker P10–P90. Fills narrower than 0.01 dB omitted.");
        ui.small("Optimization: 20–300 Hz. Higher frequencies are context; the target applies inside that band.");
        let mut plot = Plot::new("native_solver_response_fixed")
            .default_x_bounds(self.domain[0].log10(), self.domain[1].log10())
            .default_y_bounds(self.domain[2], self.domain[3])
            .auto_bounds(false)
            .x_axis_label("Frequency (Hz)")
            .y_axis_label("Relative level (dB)")
            .x_axis_formatter(|mark, _| {
                let hz = 10f64.powf(mark.value);
                if hz >= 1000. {
                    format!("{:.1}k", hz / 1000.)
                } else {
                    format!("{hz:.0}")
                }
            })
            .legend(Legend::default())
            .height(ui.available_height().max(220.));
        if self.reset {
            plot = plot.reset();
            self.reset = false;
        }
        plot.show(ui, |p| {
            p.vline(VLine::new("Correction begins · 20 Hz", 20f64.log10()).color(Color32::GRAY));
            p.vline(VLine::new("Correction ends · 300 Hz", 300f64.log10()).color(Color32::GRAY));
            p.line(
                Line::new(
                    "Target",
                    vec![[20f64.log10(), self.target], [300f64.log10(), self.target]],
                )
                .color(Color32::GOLD)
                .width(1.5),
            );
            for (channel, name) in self.channels.iter().enumerate() {
                if !self.selected[channel] {
                    continue;
                }
                let Some(c) = self
                    .callbacks
                    .iter()
                    .rev()
                    .find(|c| c.channel == channel && c.generation <= self.generation)
                else {
                    continue;
                };
                let b = &c.modes[self.mode];
                let color = colors[channel];
                for (label, low, high, alpha) in [
                    ("Min/max", &b.min, &b.max, 28),
                    ("P10–P90", &b.p10, &b.p90, 65),
                ] {
                    // Polygon tessellation requires convex input: one adjacent-frequency cell per quad.
                    for cell in 0..self.frequencies.len() - 1 {
                        if !visible_band(low, high, cell) {
                            continue;
                        }
                        p.polygon(
                            Polygon::new(
                                "",
                                band_cell(&self.frequencies, low, high, cell).to_vec(),
                            )
                            .id(("solver_band", channel, label, cell))
                            .fill_color(Color32::from_rgba_unmultiplied(
                                color.r(),
                                color.g(),
                                color.b(),
                                alpha,
                            ))
                            .stroke(egui::Stroke::new(0., color)),
                        );
                    }
                }
                p.line(
                    Line::new(
                        format!("{name} best · loss {:.4} · callback {}", c.loss, c.index),
                        self.frequencies
                            .iter()
                            .zip(&b.best)
                            .map(|(f, v)| [f.log10(), *v])
                            .collect::<Vec<_>>(),
                    )
                    .id(("solver_best", channel))
                    .color(color)
                    .width(2.),
                );
            }
        });
    }

    pub fn stage_ui(&mut self, ui: &mut egui::Ui, stage: usize, changed: bool) {
        if changed {
            self.playing = false;
            self.following = false;
            if stage == 0 {
                self.generation = 0;
            }
            if stage == 4 {
                self.generation = self.max_generation;
            }
            if stage == 3 {
                self.generation = 0;
                self.playing = true;
                self.last_frame = Instant::now();
            }
        }
        match stage {
            0 => {
                ui.label(format!("{} speakers · {} frequency samples · initial candidate shown below, not the uncorrected source.", self.channels.len(), self.frequencies.len()));
            }
            1 => {
                ui.label(format!("Current demonstration target: {:.3} dB. Optimization band: 20–300 Hz; outside-band response remains context.", self.target));
            }
            _ => {
                ui.label("Recorded best candidate parameters · read-only replay");
                egui::Grid::new("recorded_pk_parameters")
                    .striped(true)
                    .show(ui, |ui| {
                        for label in ["Speaker", "Filter", "Frequency (Hz)", "Q", "Gain (dB)"] {
                            ui.strong(label);
                        }
                        ui.end_row();
                        for (channel, name) in self.channels.iter().enumerate() {
                            if !self.selected[channel] {
                                continue;
                            }
                            if let Some(c) =
                                self.callbacks.iter().rev().find(|c| {
                                    c.channel == channel && c.generation <= self.generation
                                })
                            {
                                for (i, params) in c.params.chunks_exact(3).enumerate() {
                                    ui.label(name);
                                    ui.label((i + 1).to_string());
                                    ui.label(format!("{:.2}", 10f64.powf(params[0])));
                                    ui.label(format!("{:.3}", params[1]));
                                    ui.label(format!("{:.3}", params[2]));
                                    ui.end_row();
                                }
                            }
                        }
                    });
            }
        }
        self.draw(ui, false);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn callback_grid_and_band_validation() {
        let cell = band_cell(
            &[10., 100., 1000.],
            &[-40., -50., -30.],
            &[-20., -25., -10.],
            1,
        );
        assert_eq!(cell, [[2., -50.], [3., -30.], [3., -10.], [2., -25.]]);
        assert!(cell.iter().all(|p| (2.0..=3.0).contains(&p[0])));
        assert!(!visible_band(
            &[-30., -30.],
            &[-30. + 1e-8, -30. + 0.0025],
            0
        ));
        assert!(visible_band(&[-40., -40.], &[-30., -30.], 0));
        let chart = serde_json::json!({"transfer_prohibited":true,"channels":["L"],"smoothing_modes":MODES,"domain":{"minHz":20.,"maxHz":20000.,"minDb":-80.,"maxDb":0.},"target_db":-30.});
        let mut row = serde_json::json!({"channel":"L","callback_index":1,"generation":0,"loss":1.,"best_params":[2.,1.,-3.,2.,1.,-3.],"frequency_hz":[20.,20000.],"display_best_db":{},"envelopes":{}});
        for mode in MODES {
            row["display_best_db"][mode] = serde_json::json!([-30., -30.]);
            row["envelopes"][mode] = serde_json::json!({"min":[-40.,-40.],"p10":[-35.,-35.],"p90":[-25.,-25.],"max":[-20.,-20.]});
        }
        assert!(SolverPlot::parse(&chart, &row.to_string()).is_ok());
        let mut live = SolverPlot::from_chart(&chart).unwrap();
        live.append(&row).unwrap();
        assert!(live.append(&row).is_err());
        let mut next = row.clone();
        next["callback_index"] = serde_json::json!(3);
        next["generation"] = serde_json::json!(2);
        assert!(live.append(&next).is_err());
        next["callback_index"] = serde_json::json!(2);
        next["generation"] = serde_json::json!(0);
        next["terminal"] = serde_json::json!({"finalized":true});
        live.append(&next).unwrap();
        let mut receipt =
            serde_json::json!([{"channel":"L","callbacks":2,"last_generation":0,"final_loss":1.}]);
        live.verify_complete(&receipt).unwrap();
        receipt[0]["callbacks"] = serde_json::json!(3);
        assert!(live.verify_complete(&receipt).is_err());
        assert!(live.append(&next).is_err());
        row["envelopes"]["raw"]["min"] = serde_json::json!([-10., -40.]);
        assert!(SolverPlot::parse(&chart, &row.to_string()).is_err());
        row["frequency_hz"] = serde_json::json!([20., 20.]);
        assert!(SolverPlot::parse(&chart, &row.to_string()).is_err());
    }
}
