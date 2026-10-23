//! Saved simulation geometry only; missing spatial snapshots stay unavailable.
use std::{fs, io::Read, path::Path};

use rerun::RecordingStream;
use serde_json::Value;
use sha2::{Digest, Sha256};

pub struct RoomStudy {
    dimensions: [f32; 3],
    source: [f32; 3],
    listener: [f32; 3],
    paths: Vec<([f32; 3], f64)>,
    direct_time_s: Option<f64>,
}

impl RoomStudy {
    pub fn has_geometry(&self) -> bool {
        true
    }
    pub fn has_rays(&self) -> bool {
        !self.paths.is_empty()
    }
    pub fn has_pressure(&self) -> bool {
        false
    }

    pub fn arrival_duration(&self) -> Option<f64> {
        self.direct_time_s.map(|direct| {
            self.paths
                .iter()
                .fold(direct, |end, (_, time)| end.max(*time))
        })
    }
    pub fn arrival_times_ms(&self) -> Vec<(String, f64)> {
        self.direct_time_s
            .into_iter()
            .map(|t| ("Direct".into(), t * 1000.0))
            .chain(
                self.paths
                    .iter()
                    .enumerate()
                    .map(|(i, (_, t))| (format!("Reflection {}", i + 1), t * 1000.0)),
            )
            .collect()
    }

    /// Replay saved geometric travel times, not pressure, phase, or calibrated speaker timing.
    pub fn log_arrival_animation(&self, rec: &RecordingStream) -> Result<(), String> {
        let direct = self
            .direct_time_s
            .ok_or("Saved direct arrival is unavailable")?;
        let duration = self
            .arrival_duration()
            .ok_or("Saved arrival duration is unavailable")?;
        if self.paths.iter().any(|(_, time)| *time <= 0.0) {
            return Err("Arrival replay requires positive saved travel times".into());
        }
        log(rec, self)?;
        let mut paths = vec![(vec![self.source, self.listener], direct)];
        paths.extend(
            self.paths
                .iter()
                .map(|(hit, time)| (vec![self.source, *hit, self.listener], *time)),
        );
        let frames = (120 - paths.len()).min(96);
        let mut times: Vec<f64> = (0..frames)
            .map(|i| duration * i as f64 / (frames - 1) as f64)
            .collect();
        times.extend(paths.iter().map(|(_, arrival)| *arrival));
        times.sort_by(f64::total_cmp);
        times.dedup();
        for (frame, time) in times.into_iter().enumerate() {
            rec.set_time_sequence("arrival_frame", frame as i64);
            rec.set_time("arrival_time", std::time::Duration::from_secs_f64(time));
            rec.log("timing/elapsed_ms", &rerun::Scalars::single(time * 1000.0))
                .map_err(|e| e.to_string())?;
            rec.log(
                "timing/clock",
                &rerun::Points3D::new([[
                    self.dimensions[0] * 0.5,
                    self.dimensions[1] * 0.5,
                    self.dimensions[2] + 0.2,
                ]])
                .with_radii([0.0])
                .with_show_labels(true)
                .with_colors([0xD5DCE5FF_u32])
                .with_labels([format!(
                    "Elapsed {:.2} ms · direct {:.2} ms · frame {} (slow replay)",
                    time * 1000.0,
                    direct * 1000.0,
                    frame
                )]),
            )
            .map_err(|e| e.to_string())?;
            for (index, (vertices, arrival)) in paths.iter().enumerate() {
                let (point, trail) = travel(vertices, time / arrival);
                let arrived = time >= *arrival;
                let color = if arrived {
                    0x39BE83FF_u32
                } else if index == 0 {
                    0xF7B54AFF
                } else {
                    0x398FDFFF
                };
                rec.log(
                    format!("timing/travellers/{index}"),
                    &rerun::Points3D::new([point])
                        .with_colors([color])
                        .with_radii([0.06])
                        .with_labels([if index != 0 {
                            String::new()
                        } else if arrived {
                            format!(
                                "{} · arrived {:.2} ms",
                                if index == 0 {
                                    "Direct".into()
                                } else {
                                    format!("Reflection {index}")
                                },
                                arrival * 1000.0
                            )
                        } else {
                            format!("{:.2} ms", time * 1000.0)
                        }]),
                )
                .map_err(|e| e.to_string())?;
                rec.log(
                    format!("timing/trails/{index}"),
                    &rerun::LineStrips3D::new([trail]).with_colors([color]),
                )
                .map_err(|e| e.to_string())?;
            }
        }
        rec.disable_timeline("arrival_time");
        rec.disable_timeline("arrival_frame");
        rec.log_static("timing/scope", &rerun::TextDocument::new("Saved single FL simulation source → simulated MLP. Direct and first-order paths animate at constant distance per saved travel time. Green means geometric arrival, not measured onset, pressure interference, or verified calibration timing."))
            .map_err(|e| e.to_string())?;
        Ok(())
    }
}

fn distance(a: [f32; 3], b: [f32; 3]) -> f64 {
    a.into_iter()
        .zip(b)
        .map(|(a, b)| (f64::from(a) - f64::from(b)).powi(2))
        .sum::<f64>()
        .sqrt()
}

fn travel(vertices: &[[f32; 3]], fraction: f64) -> ([f32; 3], Vec<[f32; 3]>) {
    let total: f64 = vertices
        .windows(2)
        .map(|pair| distance(pair[0], pair[1]))
        .sum();
    let mut remaining = fraction.clamp(0.0, 1.0) * total;
    let mut trail = vec![vertices[0]];
    for pair in vertices.windows(2) {
        let length = distance(pair[0], pair[1]);
        if remaining <= length && length > 0.0 {
            let point = std::array::from_fn(|axis| {
                pair[0][axis] + (pair[1][axis] - pair[0][axis]) * (remaining / length) as f32
            });
            trail.push(point);
            return (point, trail);
        }
        remaining -= length;
        trail.push(pair[1]);
    }
    (*vertices.last().unwrap(), trail)
}

fn read(path: &Path) -> Result<Vec<u8>, String> {
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > 16 * 1024 * 1024 {
        return Err("Room study input exceeds 16 MiB".into());
    }
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(16 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 16 * 1024 * 1024 {
        return Err("Room study input exceeds 16 MiB".into());
    }
    Ok(bytes)
}

fn vector(value: &Value) -> Result<[f32; 3], String> {
    let v = value
        .as_array()
        .filter(|v| v.len() == 3)
        .ok_or("Expected three room coordinates")?;
    let mut result = [0.; 3];
    for i in 0..3 {
        let n = v[i]
            .as_f64()
            .filter(|n| n.is_finite() && n.abs() <= 100.)
            .ok_or("Invalid room coordinate")?;
        result[i] = n as f32;
    }
    Ok(result)
}

fn parse(comparison: &Value, reference: &Value) -> Result<RoomStudy, String> {
    if comparison["artifact_type"] != "experimental_sonium_swift_room_comparison"
        || comparison["transfer_prohibited"] != true
        || reference["transferProhibited"] != true
    {
        return Err("Expected a transfer-prohibited saved room simulation".into());
    }
    let dimensions = vector(&reference["dimensionsMeters"])?;
    if dimensions.iter().any(|v| *v <= 0.) {
        return Err("Room dimensions must be positive".into());
    }
    let source = vector(&reference["sourceMeters"])?;
    let listener = vector(&reference["listenerMeters"])?;
    let direct_time_s = comparison["rays"]
        .get("direct_time_s")
        .map(|value| {
            value
                .as_f64()
                .filter(|time| time.is_finite() && *time > 0.0 && *time <= 60.0)
                .ok_or("Invalid saved direct arrival time")
        })
        .transpose()?;
    let paths = comparison["rays"]["first_order_paths"]
        .as_array()
        .ok_or("Missing saved reflection paths")?;
    if paths.len() > 64 {
        return Err("Saved reflection paths exceed 64".into());
    }
    let paths = paths
        .iter()
        .map(|p| {
            let time = p["time_s"]
                .as_f64()
                .filter(|t| t.is_finite() && (0.0..=60.).contains(t))
                .ok_or("Invalid reflection arrival time")?;
            Ok((vector(&p["hit_m"])?, time))
        })
        .collect::<Result<Vec<_>, String>>()?;
    if direct_time_s.is_some() && paths.iter().any(|(_, time)| *time <= 0.0) {
        return Err("Arrival animation requires strictly positive path travel times".into());
    }
    Ok(RoomStudy {
        dimensions,
        source,
        listener,
        paths,
        direct_time_s,
    })
}

/// Load the saved finer-grid comparison and its digest-bound Swift reference.
pub fn load(path: &Path) -> Result<RoomStudy, String> {
    let comparison: Value = serde_json::from_slice(&read(path)?).map_err(|e| e.to_string())?;
    // shortcut: the current finer-grid exporter uses this companion filename; add an explicit manifest when another layout is exported.
    let reference_path = path
        .parent()
        .and_then(Path::parent)
        .ok_or("Missing room study parent")?
        .join("swift-finer.json");
    let bytes = read(&reference_path)?;
    let digest = format!("{:x}", Sha256::digest(&bytes));
    if comparison["swift_reference_sha256"].as_str() != Some(digest.as_str()) {
        return Err("Saved Swift room reference digest mismatch".into());
    }
    let reference: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    parse(&comparison, &reference)
}

pub fn log(rec: &RecordingStream, study: &RoomStudy) -> Result<(), String> {
    let result = || -> Result<(), Box<dyn std::error::Error>> {
        for root in ["room", "rays", "timing"] {
            rec.log_static(root, &rerun::ViewCoordinates::RIGHT_HAND_Z_UP())?;
            let [x, y, z] = study.dimensions;
            let corners = [
                [0., 0., 0.],
                [x, 0., 0.],
                [x, y, 0.],
                [0., y, 0.],
                [0., 0., z],
                [x, 0., z],
                [x, y, z],
                [0., y, z],
            ];
            let edges = [
                (0, 1),
                (1, 2),
                (2, 3),
                (3, 0),
                (4, 5),
                (5, 6),
                (6, 7),
                (7, 4),
                (0, 4),
                (1, 5),
                (2, 6),
                (3, 7),
            ];
            rec.log_static(
                format!("{root}/bounds"),
                &rerun::LineStrips3D::new(edges.map(|(a, b)| vec![corners[a], corners[b]]))
                    .with_colors([0x8291A188u32]),
            )?;
            rec.log_static(
                format!("{root}/source_listener"),
                &rerun::Points3D::new([study.source, study.listener])
                    .with_colors([0xDA913CFFu32, 0x27895AFF])
                    .with_radii([0.07])
                    .with_labels(["FL simulation source", "MLP simulation receiver"]),
            )?;
        }
        rec.log_static("room/scope", &rerun::TextDocument::new("Saved nominal VisionPlan rectangular approximation. Registered room mesh is not solver-approved. Source/listener are simulation coordinates. No calibration or receiver changes."))?;
        rec.log_static("rays/scope", &rerun::TextDocument::new("Saved first-order specular reflection geometry; left boundary open. Ray energy is incoherent, not a measured pressure impulse response. Select reflection_arrival to inspect arrival order."))?;
        rec.log_static("pressure/scope", &rerun::TextDocument::new("Spatial pressure snapshots unavailable: saved comparison contains listener pressure only. Original simulation was rigid, sealed and lossless. No interpolated field or synthetic animation is substituted."))?;
        for (index, (hit, time)) in study.paths.iter().enumerate() {
            rec.set_time(
                "reflection_arrival",
                std::time::Duration::from_secs_f64(*time),
            );
            rec.log(
                format!("rays/paths/{index}"),
                &rerun::LineStrips3D::new([vec![study.source, *hit, study.listener]])
                    .with_colors([0x287ACBFFu32]),
            )?;
        }
        rec.disable_timeline("reflection_arrival");
        Ok(())
    };
    result().map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn saved_geometry_requires_valid_scope_and_bounded_finite_paths() {
        let mut c = serde_json::json!({"artifact_type":"experimental_sonium_swift_room_comparison","transfer_prohibited":true,"rays":{"first_order_paths":[{"hit_m":[1.,1.,1.],"time_s":0.02}]}});
        let mut r = serde_json::json!({"transferProhibited":true,"dimensionsMeters":[4.,5.,3.],"sourceMeters":[1.,1.,1.],"listenerMeters":[2.,2.,1.]});
        let s = parse(&c, &r).unwrap();
        assert!(s.has_geometry() && s.has_rays() && !s.has_pressure());
        assert_eq!(s.arrival_duration(), None);
        c["rays"]["direct_time_s"] = serde_json::json!(0.01);
        let animated = parse(&c, &r).unwrap();
        assert_eq!(animated.arrival_duration(), Some(0.02));
        let vertices = [[0., 0., 0.], [1., 0., 0.], [1., 3., 0.]];
        assert_eq!(travel(&vertices, 0.).0, vertices[0]);
        assert_eq!(travel(&vertices, 0.25).0, vertices[1]);
        assert_eq!(travel(&vertices, 0.5).0, [1., 1., 0.]);
        assert_eq!(travel(&vertices, 1.).0, vertices[2]);
        let (rec, _storage) = rerun::RecordingStreamBuilder::new("arrival_test")
            .memory()
            .unwrap();
        animated.log_arrival_animation(&rec).unwrap();
        c["rays"]["direct_time_s"] = serde_json::json!("NaN");
        assert!(parse(&c, &r).is_err());
        c["rays"]["direct_time_s"] = serde_json::json!(0.01);
        c["rays"]["first_order_paths"][0]["time_s"] = serde_json::json!(-1.);
        assert!(parse(&c, &r).is_err());
        c["rays"]["first_order_paths"] = serde_json::json!([]);
        r["dimensionsMeters"] = serde_json::json!([0., 5., 3.]);
        assert!(parse(&c, &r).is_err());
    }
}
