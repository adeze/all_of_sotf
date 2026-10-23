//! Display-only impulse and finite-record energy decay from unchanged saved samples.

use egui_plot::{Line, Plot, PlotPoint};
use rerun::external::egui::{self, Color32};

pub struct ImpulsePlot {
    impulse: Vec<PlotPoint>,
    decay: Vec<PlotPoint>,
    reset: bool,
    ranges: Option<[f64; 3]>,
}

impl ImpulsePlot {
    pub fn new(samples: &[f32], sample_rate_hz: f64, peak_sample: usize) -> Result<Self, String> {
        if samples.is_empty()
            || samples.len() > 1_048_576
            || peak_sample >= samples.len()
            || !sample_rate_hz.is_finite()
            || sample_rate_hz <= 0.0
            || samples.iter().any(|sample| !sample.is_finite())
        {
            return Err(
                "Impulse plot requires bounded finite samples, sample rate and peak".into(),
            );
        }
        let peak = samples
            .iter()
            .map(|sample| f64::from(*sample).abs())
            .fold(0.0, f64::max);
        if peak == 0.0 {
            return Err("Impulse plot unavailable: saved response is silent".into());
        }
        let time = |index: usize| 1000.0 * (index as f64 - peak_sample as f64) / sample_rate_hz;
        let impulse = samples
            .iter()
            .enumerate()
            .map(|(index, sample)| PlotPoint::new(time(index), f64::from(*sample) / peak))
            .collect();
        let mut energy = 0.0;
        let mut decay = Vec::with_capacity(samples.len() - peak_sample);
        for index in (peak_sample..samples.len()).rev() {
            energy += f64::from(samples[index]).powi(2);
            decay.push(PlotPoint::new(time(index), energy));
        }
        if energy == 0.0 {
            return Err("Energy decay unavailable: no energy at or after display reference".into());
        }
        decay.reverse();
        // Zero remaining energy is undefined in dB; omit it rather than inventing a noise floor.
        decay.retain_mut(|point| {
            if point.y == 0.0 {
                return false;
            }
            point.y = 10.0 * (point.y / energy).log10();
            true
        });
        Ok(Self {
            impulse,
            decay,
            reset: true,
            ranges: None,
        })
    }

    pub fn ui(&mut self, ui: &mut egui::Ui, start_ms: f64, end_ms: f64, floor_db: f64) {
        if !start_ms.is_finite()
            || !end_ms.is_finite()
            || start_ms >= end_ms
            || !floor_db.is_finite()
            || floor_db >= 0.0
        {
            ui.label("Impulse plot requires ordered time limits and a negative dB floor.");
            return;
        }
        ui.heading("Impulse and energy decay");
        ui.label(
            "Broadband saved response. Time zero is the impulse peak, not physical time of flight.",
        );
        ui.small("Schroeder integration ends at the saved record boundary; no noise correction, RT60 fit or frequency filtering.");
        if ui.button("Reset axes").clicked() {
            self.reset = true;
        }
        let ranges = [start_ms, end_ms, floor_db];
        self.reset |= self.ranges != Some(ranges);
        self.ranges = Some(ranges);
        let height = ((ui.available_height() - 34.0) / 2.0).max(120.0);
        for (id, label, values, bounds, color) in [
            (
                "native_saved_impulse",
                "Signed amplitude / saved absolute peak",
                &self.impulse,
                [-1.05, 1.05],
                Color32::from_rgb(65, 155, 235),
            ),
            (
                "native_saved_energy_decay",
                "Remaining energy (dB relative to time zero)",
                &self.decay,
                [floor_db, 0.0],
                Color32::from_rgb(240, 153, 59),
            ),
        ] {
            let mut plot = Plot::new(id)
                .height(height)
                .default_x_bounds(start_ms, end_ms)
                .default_y_bounds(bounds[0], bounds[1])
                .auto_bounds(false)
                .x_axis_label("Time from impulse peak (ms)")
                .y_axis_label(label)
                .link_axis(egui::Id::unique("saved_impulse_time"), [true, false])
                .link_cursor(egui::Id::unique("saved_impulse_cursor"), [true, false]);
            if self.reset {
                plot = plot.reset();
            }
            plot.show(ui, |plot| {
                plot.line(Line::new(label, values.as_slice()).color(color).width(1.25));
            });
        }
        self.reset = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finite_record_decay_preserves_sign_reference_and_inclusive_energy() {
        let plot = ImpulsePlot::new(&[0.0, -2.0, 1.0, 0.0], 1000.0, 1).unwrap();
        assert_eq!(
            plot.impulse
                .iter()
                .map(|point| [point.x, point.y])
                .collect::<Vec<_>>(),
            vec![[-1.0, 0.0], [0.0, -1.0], [1.0, 0.5], [2.0, 0.0]]
        );
        assert_eq!(plot.decay.len(), 2);
        assert_eq!(plot.decay[0], PlotPoint::new(0.0, 0.0));
        assert!((plot.decay[1].y - 10.0 * 0.2_f64.log10()).abs() < 1e-12);
        let scaled = ImpulsePlot::new(&[0.0, -20.0, 10.0, 0.0], 1000.0, 1).unwrap();
        assert_eq!(plot.impulse, scaled.impulse);
        assert_eq!(plot.decay, scaled.decay);
        for (samples, rate, peak) in [
            (vec![], 1000.0, 0),
            (vec![0.0], 1000.0, 0),
            (vec![1.0], f64::NAN, 0),
            (vec![1.0], 0.0, 0),
            (vec![f32::INFINITY], 1000.0, 0),
            (vec![1.0], 1000.0, 1),
            (vec![1.0, 0.0], 1000.0, 1),
        ] {
            assert!(ImpulsePlot::new(&samples, rate, peak).is_err());
        }
    }
}
