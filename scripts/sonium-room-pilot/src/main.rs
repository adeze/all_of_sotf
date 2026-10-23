//! Bounded source-module trial, not a full Sonium FEM assembly or calibration path.
extern crate sonium_solvers_sparse as sonium_solvers;
#[allow(dead_code, clippy::all)]
#[path = "../../../sonium/crates/sonium-fem/src/physics/acoustics/pressure/room/ray_tracing.rs"]
mod rays;
#[cfg(feature = "viewer")]
mod render;
#[allow(dead_code, clippy::all)]
#[path = "../../../sonium/crates/sonium-fem/src/physics/acoustics/pressure/time_domain.rs"]
mod wave;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sonium_solvers::CsrMatrix;
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Reference {
    dimensions_meters: [f64; 3],
    source_meters: [f64; 3],
    listener_meters: [f64; 3],
    loss_per_second: f64,
    duration_seconds: f64,
    speed_of_sound_meters_per_second: f64,
    resolutions: Vec<ReferenceWave>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ReferenceWave {
    grid_meters: f64,
    time_step_seconds: f64,
    cell_count: usize,
    pressure: Vec<f64>,
    frequencies_hz: Vec<f64>,
    response_real: Vec<f64>,
    response_imaginary: Vec<f64>,
}
#[derive(Serialize)]
struct WaveResult {
    grid_m: f64,
    dt_s: f64,
    cells: usize,
    samples: usize,
    represented_dimensions_m: [f64; 3],
    max_absolute_pressure_error: f64,
    relative_l2_pressure_error: f64,
    relative_complex_spectrum_error: f64,
    frequencies_hz: Vec<f64>,
    response_real: Vec<f64>,
    response_imaginary: Vec<f64>,
    pressure: Vec<f64>,
    #[serde(skip)]
    #[cfg(feature = "viewer")]
    points: Vec<[f32; 3]>,
    #[serde(skip)]
    #[cfg(feature = "viewer")]
    frames: Vec<(f64, Vec<f64>)>,
}
struct Grid {
    points: Vec<[f64; 3]>,
    stiffness: CsrMatrix<f64>,
    h: f64,
}
impl Grid {
    fn new(size: [f64; 3], h: f64) -> Result<Self, String> {
        if size
            .iter()
            .any(|v| !v.is_finite() || *v <= 0.0 || *v > 10.0)
            || !(0.1..=0.4).contains(&h)
        {
            return Err("Invalid bounded grid".into());
        }
        let shape = [
            (size[0] / h).ceil() as usize,
            (size[1] / h).ceil() as usize,
            (size[2] / h).floor() as usize,
        ];
        let mut map = std::collections::BTreeMap::new();
        let mut points = Vec::new();
        for z in 0..shape[2] {
            for y in 0..shape[1] {
                for x in 0..shape[0] {
                    let p = [
                        (x as f64 + 0.5) * h,
                        (y as f64 + 0.5) * h,
                        (z as f64 + 0.5) * h,
                    ];
                    if p[0] < size[0] && p[1] < size[1] {
                        map.insert([x, y, z], points.len());
                        points.push(p);
                    }
                }
            }
        }
        if points.is_empty() || points.len() > 60_000 {
            return Err("Grid exceeds pilot cell budget".into());
        }
        let mut rows = vec![0];
        let mut cols = Vec::new();
        let mut values = Vec::new();
        // Same cell-centred rigid-boundary operator as Swift; Sonium owns time stepping.
        let mut coordinates = vec![[0; 3]; points.len()];
        for (p, &i) in &map {
            coordinates[i] = *p;
        }
        for (i, p) in coordinates.iter().enumerate() {
            let mut neighbours = Vec::new();
            for (axis, delta) in [(0, 1), (0, -1), (1, 1), (1, -1), (2, 1), (2, -1)] {
                let mut q = *p;
                let n = q[axis] as isize + delta;
                if n < 0 {
                    continue;
                }
                q[axis] = n as usize;
                if let Some(&j) = map.get(&q) {
                    neighbours.push(j);
                }
            }
            let diagonal = neighbours.len() as f64 / (h * h);
            let mut row: Vec<_> = neighbours
                .into_iter()
                .map(|j| (j, -1.0 / (h * h)))
                .collect();
            row.push((i, diagonal));
            row.sort_by_key(|v| v.0);
            for (j, v) in row {
                cols.push(j);
                values.push(v);
            }
            rows.push(cols.len());
        }
        let n = points.len();
        Ok(Self {
            points,
            stiffness: CsrMatrix::from_raw_parts(n, n, rows, cols, values),
            h,
        })
    }
    fn weights(&self, p: [f64; 3]) -> Result<Vec<(usize, f64)>, String> {
        if p.iter().any(|v| !v.is_finite()) {
            return Err("Nonfinite point".into());
        }
        let mut out: Vec<_> = self
            .points
            .iter()
            .enumerate()
            .filter_map(|(i, q)| {
                let w = (0..3)
                    .map(|a| (1.0 - (q[a] - p[a]).abs() / self.h).max(0.0))
                    .product::<f64>();
                (w > 0.0).then_some((i, w))
            })
            .collect();
        let total: f64 = out.iter().map(|v| v.1).sum();
        if total <= 0.0 {
            return Err("Point outside grid".into());
        }
        for v in &mut out {
            v.1 /= total;
        }
        Ok(out)
    }
}
fn relative_error(a: &[f64], b: &[f64]) -> f64 {
    assert_eq!(
        a.len(),
        b.len(),
        "Relative error requires matching sample counts"
    );
    let e = a.iter().zip(b).map(|(x, y)| (x - y).powi(2)).sum::<f64>();
    let n = b.iter().map(|v| v * v).sum::<f64>();
    (e / n.max(1e-300)).sqrt()
}
fn validate_reference(r: &Reference, model: &Value) -> Result<(), String> {
    if r.loss_per_second != 0.0
        || r.speed_of_sound_meters_per_second != 343.0
        || r.duration_seconds != 0.6
        || ![2, 4].contains(&r.resolutions.len())
    {
        return Err(
            "Pilot requires two or four lossless 0.6s Swift reference grids at 343m/s".into(),
        );
    }
    let g = &model["geometry"]["theatre_zone"];
    let dimensions = [
        g["width_m"].as_f64(),
        g["length_m"].as_f64(),
        g["height_m"].as_f64(),
    ];
    if dimensions != r.dimensions_meters.map(Some) {
        return Err("Swift reference room dimensions do not match saved model".into());
    }
    for (position, value) in [
        (r.source_meters, &model["speakers"]["FL"]["position_xyz_m"]),
        (
            r.listener_meters,
            &model["measurement_positions"]["MLP"]["position_xyz_m"],
        ),
    ] {
        let supplied: [f64; 3] =
            serde_json::from_value(value.clone()).map_err(|_| "Missing saved room position")?;
        if supplied != position
            || position
                .iter()
                .enumerate()
                .any(|(i, p)| !p.is_finite() || *p < 0.0 || *p >= r.dimensions_meters[i])
        {
            return Err(
                "Reference position differs from saved room or lies outside theatre".into(),
            );
        }
    }
    for (b, expected_h) in r.resolutions.iter().zip([0.2, 0.15, 0.12, 0.1]) {
        if b.grid_meters != expected_h
            || b.frequencies_hz != (20..=100).map(f64::from).collect::<Vec<_>>()
            || b.response_real.len() != 81
            || b.response_imaginary.len() != 81
        {
            return Err("Reference grids/spectra differ from bounded trial".into());
        }
    }
    Ok(())
}
fn run_wave(r: &Reference, b: &ReferenceWave) -> Result<WaveResult, String> {
    let grid = Grid::new(r.dimensions_meters, b.grid_meters)?;
    let dt = b.grid_meters / (2.0 * r.speed_of_sound_meters_per_second);
    let n = (r.duration_seconds / dt) as usize;
    if grid.points.len() != b.cell_count
        || n != b.pressure.len()
        || (dt - b.time_step_seconds).abs() > 1e-14
        || n * grid.points.len() > 250_000_000
    {
        return Err("Swift grid/time contract differs or exceeds budget".into());
    }
    let source = grid.weights(r.source_meters)?;
    let listener = grid.weights(r.listener_meters)?;
    let mass = vec![1.0 / r.speed_of_sound_meters_per_second.powi(2); grid.points.len()];
    let mut stepper = wave::LeapfrogStepper::new(&mass, dt);
    let mut pressure = vec![0.0; mass.len()];
    let mut force = pressure.clone();
    let mut observed = Vec::with_capacity(n);
    #[cfg(feature = "viewer")]
    let mut frames = Vec::new();
    #[cfg(feature = "viewer")]
    let display_stride = grid.points.len().div_ceil(5_000);
    for k in 0..n {
        force.fill(0.0);
        let tau = (k as f64 * dt - 0.03) * std::f64::consts::PI * 80.0;
        let f = (1.0 - 2.0 * tau * tau) * (-tau * tau).exp();
        for &(i, w) in &source {
            force[i] = w * f / (b.grid_meters.powi(3) * r.speed_of_sound_meters_per_second.powi(2));
        }
        stepper.step(&mut pressure, &force, &grid.stiffness);
        observed.push(listener.iter().map(|&(i, w)| pressure[i] * w).sum::<f64>());
        #[cfg(feature = "viewer")]
        if k % (n / 48).max(1) == 0 {
            frames.push((
                (k + 1) as f64 * dt,
                pressure.iter().step_by(display_stride).copied().collect(),
            ));
        }
    }
    if observed.iter().any(|v| !v.is_finite()) {
        return Err("Nonfinite wave output".into());
    }
    let mut real = Vec::new();
    let mut imag = Vec::new();
    for &f in &b.frequencies_hz {
        let mut re = 0.0;
        let mut im = 0.0;
        for (k, &p) in observed.iter().enumerate() {
            let phase = 2.0 * std::f64::consts::PI * f * (k + 1) as f64 * dt;
            re += p * phase.cos() * dt;
            im -= p * phase.sin() * dt;
        }
        real.push(re);
        imag.push(im);
    }
    if real.len() != b.response_real.len() || imag.len() != b.response_imaginary.len() {
        return Err("Reference spectrum lengths differ".into());
    }
    let actual: Vec<_> = real.iter().chain(&imag).copied().collect();
    let expected: Vec<_> = b
        .response_real
        .iter()
        .chain(&b.response_imaginary)
        .copied()
        .collect();
    Ok(WaveResult {
        grid_m: b.grid_meters,
        dt_s: dt,
        cells: grid.points.len(),
        samples: n,
        represented_dimensions_m: std::array::from_fn(|axis| {
            grid.points
                .iter()
                .map(|p| p[axis] + b.grid_meters / 2.)
                .fold(0., f64::max)
        }),
        max_absolute_pressure_error: observed
            .iter()
            .zip(&b.pressure)
            .map(|(x, y)| (x - y).abs())
            .fold(0.0, f64::max),
        relative_l2_pressure_error: relative_error(&observed, &b.pressure),
        relative_complex_spectrum_error: relative_error(&actual, &expected),
        frequencies_hz: b.frequencies_hz.clone(),
        response_real: real,
        response_imaginary: imag,
        pressure: observed,
        #[cfg(feature = "viewer")]
        points: grid
            .points
            .iter()
            .step_by(display_stride)
            .map(|p| p.map(|x| x as f32))
            .collect(),
        #[cfg(feature = "viewer")]
        frames,
    })
}
fn grid_comparison(coarse: &WaveResult, fine: &WaveResult) -> Value {
    let complex = |w: &WaveResult| {
        w.response_real
            .iter()
            .chain(&w.response_imaginary)
            .copied()
            .collect::<Vec<_>>()
    };
    let magnitude = |w: &WaveResult| {
        w.response_real
            .iter()
            .zip(&w.response_imaginary)
            .map(|(re, im)| re.hypot(*im))
            .collect::<Vec<_>>()
    };
    json!({"coarse_grid_m":coarse.grid_m,"fine_grid_m":fine.grid_m,
        "relative_complex_spectrum_difference":relative_error(&complex(coarse), &complex(fine)),
        "relative_magnitude_spectrum_difference":relative_error(&magnitude(coarse), &magnitude(fine))})
}
fn faces(size: [f64; 3], open_left: bool) -> Vec<rays::Surface> {
    let [w, l, h] = size;
    let mut out = Vec::new();
    let mut quad = |p: [[f64; 3]; 4]| {
        out.push(rays::Surface::from_vertices(p[0], p[1], p[2], 0));
        out.push(rays::Surface::from_vertices(p[0], p[2], p[3], 0));
    };
    // Normals face inward so the tracer's origin offset stays inside the enclosure.
    if !open_left {
        quad([[0., 0., 0.], [0., l, 0.], [0., l, h], [0., 0., h]]);
    }
    quad([[w, 0., 0.], [w, 0., h], [w, l, h], [w, l, 0.]]);
    quad([[0., 0., 0.], [0., 0., h], [w, 0., h], [w, 0., 0.]]);
    quad([[0., l, 0.], [w, l, 0.], [w, l, h], [0., l, h]]);
    quad([[0., 0., 0.], [w, 0., 0.], [w, l, 0.], [0., l, 0.]]);
    quad([[0., 0., h], [0., l, h], [w, l, h], [w, 0., h]]);
    out
}
fn norm(v: [f64; 3]) -> f64 {
    v.iter().map(|v| v * v).sum::<f64>().sqrt()
}
fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    std::array::from_fn(|i| a[i] - b[i])
}
fn run_rays(r: &Reference) -> Value {
    let surfaces = faces(r.dimensions_meters, true);
    let mut receivers = [rays::Receiver::new(r.listener_meters, 0.15, 0.12, 0.0001)];
    let material = rays::SurfaceMaterial::new("rigid validation scenario", [0.; 6], [0.; 6]);
    let cfg = rays::RayTracerConfig {
        num_rays: 20_000,
        max_order: 8,
        max_time: 0.12,
        energy_threshold: 1e-9,
        speed_of_sound: 343.0,
        air_absorption: [0.; 6],
        histogram_dt: 0.0001,
    };
    rays::trace_room(
        r.source_meters,
        &surfaces,
        &[material],
        &mut receivers,
        &cfg,
    );
    let mut paths = Vec::new();
    for (name, axis, value) in [
        ("right", 0, r.dimensions_meters[0]),
        ("front", 1, 0.),
        ("rear", 1, r.dimensions_meters[1]),
        ("floor", 2, 0.),
        ("ceiling", 2, r.dimensions_meters[2]),
    ] {
        let mut image = r.listener_meters;
        image[axis] = 2.0 * value - image[axis];
        let dir = sub(image, r.source_meters);
        let len = norm(dir);
        let unit = dir.map(|v| v / len);
        if let Some(hit) = rays::nearest_intersection(r.source_meters, unit, &surfaces) {
            let point = std::array::from_fn(|i| r.source_meters[i] + hit.distance * unit[i]);
            let reflected = rays::reflect(unit, surfaces[hit.surface_index].normal);
            let to_listener = sub(r.listener_meters, point);
            let remaining = norm(to_listener);
            let angle_error = norm(sub(reflected, to_listener.map(|v| v / remaining)));
            if (point[axis] - value).abs() < 1e-8 && angle_error < 1e-8 {
                paths.push(json!({"boundary":name,"hit_m":point,"length_m":hit.distance+remaining,"time_s":(hit.distance+remaining)/343.0,"reflection_direction_error":angle_error}));
            }
        }
    }
    let direct = norm(sub(r.listener_meters, r.source_meters)) / 343.0;
    let first = receivers[0].histograms[0]
        .iter()
        .position(|&e| e > 0.0)
        .map(|i| i as f64 * cfg.histogram_dt);
    json!({"scenario":"nominal theatre box; left face open; rigid surfaces; no scattering or air absorption",
        "receiver_radius_m":0.15,"rays":cfg.num_rays,"max_order":cfg.max_order,"histogram_dt_s":cfg.histogram_dt,
        "direct_time_s":direct,"first_receiver_bin_s":first,"first_order_paths":paths,"energy_histograms":receivers[0].histograms,
        "limitations":["Energy histograms are not coherent pressure impulse responses or calibrated SPL","Canonical Sonium tracer currently uses specular reflection only; scattering coefficients are unused","Escaped segments are not receiver-tested after nearest_intersection returns None","Receiver arrivals use origin-to-centre distance; finite-radius receiver timing is approximate"]})
}
fn compare_rays(actual: &Value, reference: &Value) -> Result<Value, String> {
    let paths = actual["first_order_paths"]
        .as_array()
        .ok_or("Missing Sonium paths")?;
    let expected = reference["paths"]
        .as_array()
        .ok_or("Missing analytical paths")?;
    if paths.len() != 5 || expected.len() != 5 {
        return Err("Expected exactly five finite reflecting boundaries; left stays open".into());
    }
    let mut max_hit = 0.0f64;
    let mut max_length = 0.0f64;
    let mut max_delay = 0.0f64;
    for p in paths {
        let name = p["boundary"].as_str().ok_or("Missing path identity")?;
        let key = if name == "right" {
            "balcony_boundary"
        } else {
            name
        };
        let e = expected
            .iter()
            .find(|v| v["boundaryName"] == key)
            .ok_or_else(|| format!("Missing independent reflection {key}"))?;
        let a: [f64; 3] = serde_json::from_value(p["hit_m"].clone()).map_err(|e| e.to_string())?;
        let b: [f64; 3] =
            serde_json::from_value(e["reflectionPoint"].clone()).map_err(|e| e.to_string())?;
        max_hit = max_hit.max(norm(sub(a, b)));
        max_length = max_length.max(
            (p["length_m"].as_f64().ok_or("Missing distance")?
                - e["totalDistanceMeters"]
                    .as_f64()
                    .ok_or("Missing reference distance")?)
            .abs(),
        );
        let delay = p["time_s"].as_f64().ok_or("Missing time")?
            - actual["direct_time_s"]
                .as_f64()
                .ok_or("Missing direct time")?;
        max_delay = max_delay.max(
            (delay
                - e["delayMilliseconds"]
                    .as_f64()
                    .ok_or("Missing reference delay")?
                    / 1000.)
                .abs(),
        );
    }
    if max_hit > 1e-8 || max_length > 1e-8 || max_delay > 1e-10 {
        return Err("Independent analytical reflection comparison failed".into());
    }
    Ok(
        json!({"reference":"independent image-source geometry, not executed Swift raycaster","paths":paths.len(),"max_hit_error_m":max_hit,"max_path_length_error_m":max_length,"max_excess_delay_error_s":max_delay}),
    )
}
fn write_new(path: &Path, value: &impl Serialize) -> Result<(), Box<dyn std::error::Error>> {
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    serde_json::to_writer(&mut f, value)?;
    f.flush()?;
    Ok(())
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 4 {
        return Err("usage: sonium-room-pilot SWIFT_REFERENCE_JSON ROOM_MODEL_JSON ANALYTICAL_RAYS_JSON NEW_OUTPUT_DIR".into());
    }
    let baseline = fs::read(&args[0])?;
    let r: Reference = serde_json::from_slice(&baseline)?;
    let room_bytes = fs::read(&args[1])?;
    let model: Value = serde_json::from_slice(&room_bytes)?;
    validate_reference(&r, &model)?;
    let analytic_bytes = fs::read(&args[2])?;
    let analytic: Value = serde_json::from_slice(&analytic_bytes)?;
    if analytic["sourceMeters"] != json!(r.source_meters)
        || analytic["listenerMeters"] != json!(r.listener_meters)
        || analytic["dimensionsMeters"] != json!(r.dimensions_meters)
    {
        return Err("Analytical ray reference differs from saved room".into());
    }
    let out = PathBuf::from(&args[3]);
    fs::create_dir(&out)?;
    let waves = r
        .resolutions
        .iter()
        .map(|b| run_wave(&r, b))
        .collect::<Result<Vec<_>, _>>()?;
    let rays = run_rays(&r);
    let ray_comparison = compare_rays(&rays, &analytic)?;
    let adjacent = waves
        .windows(2)
        .map(|pair| grid_comparison(&pair[0], &pair[1]))
        .collect::<Vec<_>>();
    let finest = waves.last().ok_or("Missing grids")?;
    let against_finest = waves[..waves.len() - 1]
        .iter()
        .map(|w| grid_comparison(w, finest))
        .collect::<Vec<_>>();
    let report = json!({"artifact_type":"experimental_sonium_swift_room_comparison","transfer_prohibited":true,
        "room_sha256":format!("{:x}",Sha256::digest(&room_bytes)),"swift_reference_sha256":format!("{:x}",Sha256::digest(&baseline)),
        "swift_source_sha256":format!("{:x}",Sha256::digest(include_bytes!("../../../../open-room-calibration/Sources/OpenRoomCalibrationDSP/SpatialWaveStudy.swift"))),
        "swift_harness_sha256":format!("{:x}",Sha256::digest(include_bytes!("../swift-reference/main.swift"))),
        "analytical_reference_sha256":format!("{:x}",Sha256::digest(&analytic_bytes)),"ray_comparison":ray_comparison,
        "grid_study":{"adjacent_grids":adjacent,"against_finest":against_finest,"finest_grid_is_reference_not_truth":true,"convergence_established":false},
        "source_sha256":{"sonium_rays":format!("{:x}",Sha256::digest(include_bytes!("../../../sonium/crates/sonium-fem/src/physics/acoustics/pressure/room/ray_tracing.rs"))),"sonium_wave":format!("{:x}",Sha256::digest(include_bytes!("../../../sonium/crates/sonium-fem/src/physics/acoustics/pressure/time_domain.rs")))},
        "scope":"Untouched Sonium source modules and real sparse matrix crate; finite-difference operator adapter, not full FEM assembly. Wave scenario sealed/rigid/lossless; ray scenario left-open. No calibration or hardware commands.",
        "mesh_solver_approved":model["evidence"]["mesh_solver_approved"],"waves":waves,"rays":rays});
    write_new(&out.join("comparison.json"), &report)?;
    if waves
        .iter()
        .any(|w| w.relative_l2_pressure_error > 1e-8 || w.relative_complex_spectrum_error > 1e-8)
    {
        return Err("Swift/Sonium matched-operator parity failed; report retained".into());
    }
    #[cfg(feature = "viewer")]
    render::show(&r, &waves, &rays, &out)?;
    #[cfg(not(feature = "viewer"))]
    println!("{}", serde_json::to_string(&report["ray_comparison"])?);
    Ok(())
}

#[cfg(test)]
mod pilot_tests {
    use super::*;
    #[test]
    fn finer_grid_budget_and_relative_error() {
        assert_eq!(Grid::new([1.; 3], 0.1).unwrap().points.len(), 1_000);
        assert!(Grid::new([1.; 3], 0.09).is_err());
        assert_eq!(relative_error(&[1., 2.], &[1., 2.]), 0.);
        assert_eq!(relative_error(&[2., 4.], &[1., 2.]), 1.);
    }
    #[test]
    fn grid_preserves_constant_field_and_sampling() {
        let g = Grid::new([1.; 3], 0.2).unwrap();
        assert_eq!(g.points.len(), 125);
        for i in 0..125 {
            assert!(
                g.stiffness.values[g.stiffness.row_ptrs[i]..g.stiffness.row_ptrs[i + 1]]
                    .iter()
                    .sum::<f64>()
                    .abs()
                    < 1e-10
            );
        }
        assert!(
            (g.weights([0.5; 3])
                .unwrap()
                .iter()
                .map(|v| v.1)
                .sum::<f64>()
                - 1.)
                .abs()
                < 1e-14
        );
        assert!(g.weights([4.; 3]).is_err());
    }
    #[test]
    fn open_face_is_not_a_reflector() {
        let s = faces([1.; 3], true);
        assert!(rays::nearest_intersection([0.5; 3], [-1., 0., 0.], &s).is_none());
        let h = rays::nearest_intersection([0.5; 3], [1., 0., 0.], &s).unwrap();
        assert!((h.distance - 0.5).abs() < 1e-12);
        assert_eq!(
            rays::reflect([1., 0., 0.], s[h.surface_index].normal),
            [-1., 0., 0.]
        );
    }
}
