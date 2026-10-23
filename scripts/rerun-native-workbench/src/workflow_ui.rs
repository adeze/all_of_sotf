use rerun::external::egui;
use std::time::{Duration, Instant};

const STAGES: [&str; 5] = ["Source", "Goal", "Bounds", "Search", "Prediction"];
const STEP_INTERVAL: Duration = Duration::from_secs(2);

pub struct Walkthrough {
    pub stage: usize,
    playing: bool,
    last: Instant,
}

impl Default for Walkthrough {
    fn default() -> Self {
        Self {
            stage: 0,
            playing: false,
            last: Instant::now(),
        }
    }
}

impl Walkthrough {
    fn advance(&mut self) {
        self.stage = self.stage.saturating_add(1).min(STAGES.len() - 1);
        self.playing &= self.stage < STAGES.len() - 1;
    }

    fn start(&mut self) {
        if self.stage >= STAGES.len() - 1 {
            self.stage = 0;
        }
        self.playing = true;
        self.last = Instant::now();
    }

    pub fn ui(&mut self, ui: &mut egui::Ui) -> usize {
        self.stage = self.stage.min(STAGES.len() - 1);
        let interval = if self.stage == 3 {
            Duration::from_secs(9)
        } else {
            STEP_INTERVAL
        };
        if self.playing && self.last.elapsed() >= interval {
            self.advance();
            self.last = Instant::now();
        }
        ui.heading("Study walkthrough");
        ui.label("Saved bass-EQ study · replay stages and inspect their results");
        ui.horizontal_wrapped(|ui| {
            for (index, name) in STAGES.iter().enumerate() {
                if ui
                    .selectable_label(self.stage == index, format!("{} · {name}", index + 1))
                    .clicked()
                {
                    self.stage = index;
                    self.playing = false;
                }
            }
        });
        ui.horizontal(|ui| {
            if ui
                .add_enabled(self.stage > 0, egui::Button::new("Previous"))
                .clicked()
            {
                self.stage = self.stage.saturating_sub(1);
                self.playing = false;
            }
            if ui
                .add_enabled(self.stage < STAGES.len() - 1, egui::Button::new("Next"))
                .clicked()
            {
                self.advance();
                self.playing = false;
            }
            if ui
                .button(if self.playing {
                    "Pause steps"
                } else {
                    "Play steps"
                })
                .clicked()
            {
                if self.playing {
                    self.playing = false;
                } else {
                    self.start();
                }
            }
        });
        if self.playing {
            ui.ctx()
                .request_repaint_after(interval - self.last.elapsed().min(interval));
        }
        ui.separator();
        ui.strong(STAGES[self.stage]);
        ui.label(match self.stage {
            0 => "Inspect this saved study's speaker selection and initial candidate. Native impulse-response selection remains separate; this chart is not the uncorrected source curve.",
            1 => "Compare against the study's derived flat response goal. This is a comparison target, not a listener preference or acoustic optimum.",
            2 => "Two peaking filters: frequency 20–300 Hz, Q 0.5–3, gain −12–0 dB. These bounds constrain this pilot search.",
            3 => "Deterministic search: seed 91827, population 24, 48 generations. Inspect the recorded solver callbacks and candidate spread below.",
            _ => "Inspect the predicted response and resulting parameters. A lower objective does not establish an audible improvement or an installed calibration.",
        });
        self.stage
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn walkthrough_advances_stops_and_restarts_with_bounded_stages() {
        let mut walkthrough = Walkthrough::default();
        assert_eq!(walkthrough.stage, 0);
        assert!(!walkthrough.playing);
        walkthrough.start();
        for stage in 1..STAGES.len() {
            walkthrough.advance();
            assert_eq!(walkthrough.stage, stage);
            assert_eq!(walkthrough.playing, stage < STAGES.len() - 1);
        }
        walkthrough.advance();
        assert_eq!(walkthrough.stage, STAGES.len() - 1);
        assert!(!walkthrough.playing);
        walkthrough.start();
        assert_eq!(walkthrough.stage, 0);
        assert!(walkthrough.playing);
        walkthrough.stage = usize::MAX;
        walkthrough.advance();
        assert_eq!(walkthrough.stage, STAGES.len() - 1);
        assert!(!walkthrough.playing);
    }
}
