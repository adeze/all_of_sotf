//! Native Rerun views of one measured STFT grid; display resampling never changes its reference.
use crate::rir_waterfall::{SharedDisplayControls, WaterfallGrid};

const MAX_TIMES: usize = 256;
const MAX_FREQS: usize = 512;

fn color(db: f32, controls: &SharedDisplayControls) -> [u8; 3] {
    // Monotonic blue/cyan/yellow luminance; shared across the image and mesh.
    const STOPS: [[f32; 3]; 4] = [
        [15.0, 23.0, 55.0],
        [42.0, 95.0, 140.0],
        [50.0, 170.0, 170.0],
        [245.0, 232.0, 110.0],
    ];
    let u = ((f64::from(db) - controls.floor_db) / (controls.ceiling_db - controls.floor_db))
        .clamp(0.0, 1.0) as f32
        * 3.0;
    let i = (u as usize).min(2);
    std::array::from_fn(|c| {
        (STOPS[i][c] + (STOPS[i + 1][c] - STOPS[i][c]) * (u - i as f32)).round() as u8
    })
}

fn indices(count: usize, cap: usize) -> Vec<usize> {
    (0..count.min(cap))
        .map(|i| i * (count - 1) / (count.min(cap) - 1).max(1))
        .collect()
}

fn nearest_frequency(freqs: &[f64], hz: f64) -> usize {
    let upper = freqs.partition_point(|&f| f < hz).min(freqs.len() - 1);
    if upper > 0 && hz - freqs[upper - 1] < freqs[upper] - hz {
        upper - 1
    } else {
        upper
    }
}

fn position(hz: f64, time_ms: f64, db: f32, controls: &SharedDisplayControls) -> [f32; 3] {
    [
        (30.0 * hz.log10()) as f32,
        f64::from(db).clamp(controls.floor_db, controls.ceiling_db) as f32,
        (0.1 * time_ms) as f32,
    ]
}

fn tensor_f64(values: &[f64]) -> rerun::Tensor {
    rerun::Tensor::new(rerun::encodings::TensorData::new(
        vec![values.len() as u64],
        rerun::encodings::TensorBuffer::F64(values.to_vec().into()),
    ))
}

fn frequency_label(hz: f64) -> String {
    if hz >= 1000.0 {
        format!("{:.2}k", hz / 1000.0)
    } else {
        format!("{hz:.0}")
    }
}

fn spectrogram_labels(
    min_hz: f64,
    max_hz: f64,
    start_ms: f64,
    end_ms: f64,
    controls: &SharedDisplayControls,
) -> (Vec<[f32; 2]>, Vec<String>) {
    let mut positions = Vec::new();
    let mut labels = Vec::new();
    for i in 0..=4 {
        let fraction = i as f64 / 4.0;
        let pixel = (MAX_FREQS as f64 * fraction) as f32;
        positions.extend([[pixel, 540.0], [-30.0, pixel], [605.0, pixel]]);
        labels.extend([
            frequency_label((min_hz.ln() + fraction * (max_hz / min_hz).ln()).exp()),
            format!("{:.0}", start_ms + fraction * (end_ms - start_ms)),
            format!(
                "{:.0}",
                controls.ceiling_db - fraction * (controls.ceiling_db - controls.floor_db)
            ),
        ]);
    }
    positions.extend([[256.0, 580.0], [-30.0, -28.0], [580.0, -28.0]]);
    labels.extend([
        "Frequency (Hz)".into(),
        "Time (ms)".into(),
        "Relative dB".into(),
    ]);
    (positions, labels)
}

/// Log matching spectrogram and waterfall views with one crop and fixed level scale.
pub fn log_views(
    rec: &rerun::RecordingStream,
    grid: &WaterfallGrid,
    controls: &SharedDisplayControls,
    label: &str,
) -> Result<(), String> {
    if grid
        .times_ms
        .len()
        .checked_mul(grid.freqs_hz.len())
        .is_none_or(|n| n > 4_000_000)
    {
        return Err("Decay source exceeds four million cells".into());
    }
    let crop = controls.crop(grid).map_err(|e| e.to_string())?;
    let positive = crop.freqs_hz.partition_point(|&f| f <= 0.0);
    if crop.freqs_hz.len() - positive < 2 || crop.times_ms.len() < 2 {
        return Err("Decay views require two positive frequencies and two time frames".into());
    }
    if [
        controls.floor_db,
        controls.ceiling_db,
        0.1 * crop.times_ms[0],
        0.1 * crop.times_ms.last().unwrap(),
    ]
    .iter()
    .any(|v| !(*v as f32).is_finite())
    {
        return Err("Decay view coordinates exceed renderer precision range".into());
    }
    let frequencies = &crop.freqs_hz[positive..];
    let min_hz = frequencies[0];
    let max_hz = *frequencies.last().unwrap();
    let time_indices = indices(crop.times_ms.len(), MAX_TIMES);
    let width = MAX_FREQS;
    let raster_bins: Vec<usize> = (0..width)
        .map(|i| {
            let hz =
                (min_hz.ln() + (max_hz.ln() - min_hz.ln()) * i as f64 / (width - 1) as f64).exp();
            positive + nearest_frequency(frequencies, hz)
        })
        .collect();
    let mut mesh_bins = raster_bins.clone();
    mesh_bins.dedup();
    let height = time_indices.len();
    // A square display raster prevents short IRs becoming unreadable thin strips.
    let raster_height = MAX_FREQS;
    let mut rgb = Vec::with_capacity(width * raster_height * 3);
    for row in 0..raster_height {
        let time = row * (crop.times_ms.len() - 1) / (raster_height - 1);
        for &bin in &raster_bins {
            rgb.extend(color(crop.mags_db[time][bin], controls));
        }
    }
    rec.log_static(
        "decay/spectrogram",
        &rerun::Image::from_rgb24(rgb, [width as u32, raster_height as u32]),
    )
    .map_err(|e| e.to_string())?;
    let extent = MAX_FREQS as f32;
    let mut ticks = vec![vec![[0.0, 0.0], [0.0, extent], [extent, extent]]];
    for i in 0..=4 {
        let pixel = extent * i as f32 / 4.0;
        ticks.extend([
            vec![[pixel, extent], [pixel, extent + 8.0]],
            vec![[-8.0, pixel], [0.0, pixel]],
        ]);
    }
    rec.log_static(
        "decay/spectrogram/axes",
        &rerun::LineStrips2D::new(ticks).with_colors([0x8896A8FF_u32]),
    )
    .map_err(|e| e.to_string())?;
    let (label_positions, axis_labels) = spectrogram_labels(
        min_hz,
        max_hz,
        crop.times_ms[0],
        *crop.times_ms.last().unwrap(),
        controls,
    );
    rec.log_static(
        "decay/spectrogram/labels",
        &rerun::Points2D::new(label_positions)
            .with_labels(axis_labels)
            .with_show_labels(true)
            .with_radii([0.0])
            .with_colors([0xD5DCE5FF_u32]),
    )
    .map_err(|e| e.to_string())?;
    let legend: Vec<Vec<[f32; 2]>> = (0..128)
        .map(|i| {
            let y = extent * i as f32 / 127.0;
            vec![[552.0, y], [568.0, y]]
        })
        .collect();
    let legend_colors: Vec<[u8; 3]> = (0..128)
        .map(|i| {
            color(
                (controls.ceiling_db - i as f64 / 127.0 * (controls.ceiling_db - controls.floor_db))
                    as f32,
                controls,
            )
        })
        .collect();
    rec.log_static(
        "decay/spectrogram/level_legend",
        &rerun::LineStrips2D::new(legend)
            .with_colors(legend_colors)
            .with_radii([3.0]),
    )
    .map_err(|e| e.to_string())?;

    let mut positions = Vec::with_capacity(height * mesh_bins.len());
    let mut colors = Vec::with_capacity(positions.capacity());
    for &time in &time_indices {
        for &bin in &mesh_bins {
            positions.push(position(
                crop.freqs_hz[bin],
                crop.times_ms[time],
                crop.mags_db[time][bin],
                controls,
            ));
            colors.push(color(crop.mags_db[time][bin], controls));
        }
    }
    let columns = mesh_bins.len();
    let mut triangles = Vec::with_capacity((height - 1) * (columns - 1) * 2);
    for t in 0..height - 1 {
        for f in 0..columns - 1 {
            let a = (t * columns + f) as u32;
            let b = a + 1;
            let c = a + columns as u32;
            triangles.extend([[a, c, b], [b, c, c + 1]]);
        }
    }
    let mut normals = vec![[0.0_f32; 3]; positions.len()];
    for &[a, b, c] in &triangles {
        let p = positions[a as usize];
        let u: [f32; 3] = std::array::from_fn(|i| positions[b as usize][i] - p[i]);
        let v: [f32; 3] = std::array::from_fn(|i| positions[c as usize][i] - p[i]);
        let n = [
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        ];
        for vertex in [a, b, c] {
            for i in 0..3 {
                normals[vertex as usize][i] += n[i];
            }
        }
    }
    for n in &mut normals {
        let length = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
            .sqrt()
            .max(f32::MIN_POSITIVE);
        for component in n {
            *component /= length;
        }
    }
    rec.log_static(
        "decay/waterfall",
        &rerun::ViewCoordinates::RIGHT_HAND_Y_UP(),
    )
    .map_err(|e| e.to_string())?;
    rec.log_static(
        "decay/waterfall/surface",
        &rerun::Mesh3D::new(positions.clone())
            .with_triangle_indices(triangles)
            .with_vertex_normals(normals)
            .with_vertex_colors(colors),
    )
    .map_err(|e| e.to_string())?;
    let slices: Vec<Vec<[f32; 3]>> = indices(height, 48)
        .into_iter()
        .map(|t| positions[t * columns..(t + 1) * columns].to_vec())
        .collect();
    rec.log_static(
        "decay/waterfall/slices",
        &rerun::LineStrips3D::new(slices).with_colors([0x12223888_u32]),
    )
    .map_err(|e| e.to_string())?;
    let lo = position(min_hz, crop.times_ms[0], controls.floor_db as f32, controls);
    let x = position(max_hz, crop.times_ms[0], controls.floor_db as f32, controls);
    let y = position(
        min_hz,
        crop.times_ms[0],
        controls.ceiling_db as f32,
        controls,
    );
    let z = position(
        min_hz,
        *crop.times_ms.last().unwrap(),
        controls.floor_db as f32,
        controls,
    );
    rec.log_static(
        "decay/waterfall/axes",
        &rerun::LineStrips3D::new([vec![lo, x], vec![lo, y], vec![lo, z]])
            .with_colors([0x8896A8FF_u32]),
    )
    .map_err(|e| e.to_string())?;
    rec.log_static(
        "decay/waterfall/labels",
        &rerun::Points3D::new([lo, x, y, z])
            .with_labels([
                format!(
                    "{min_hz:.1} Hz / {:.1} ms / {:.1} dB",
                    crop.times_ms[0], controls.floor_db
                ),
                format!("{max_hz:.1} Hz"),
                format!("{:.1} dB relative", controls.ceiling_db),
                format!("{:.1} ms", crop.times_ms.last().unwrap()),
            ])
            .with_radii([0.1])
            .with_colors([0xD5DCE5FF_u32]),
    )
    .map_err(|e| e.to_string())?;
    rec.log_static("decay/data/frequency_hz", &tensor_f64(&grid.freqs_hz))
        .map_err(|e| e.to_string())?;
    rec.log_static("decay/data/time_ms", &tensor_f64(&grid.times_ms))
        .map_err(|e| e.to_string())?;
    rec.log_static(
        "decay/data/magnitude_db",
        &rerun::Tensor::new(rerun::encodings::TensorData::new(
            vec![grid.times_ms.len() as u64, grid.freqs_hz.len() as u64],
            rerun::encodings::TensorBuffer::F32(
                grid.mags_db
                    .iter()
                    .flatten()
                    .copied()
                    .collect::<Vec<_>>()
                    .into(),
            ),
        )),
    )
    .map_err(|e| e.to_string())?;
    let metadata = serde_json::json!({
        "measurement": label, "units": "dB relative to complete analysis peak, not SPL",
        "frequency_hz": [min_hz,max_hz], "time_ms": [crop.times_ms[0],crop.times_ms.last().unwrap()],
        "level_db": [controls.floor_db,controls.ceiling_db],
        "source_shape": [grid.times_ms.len(),grid.freqs_hz.len()],
        "spectrogram_shape": [raster_height,width], "waterfall_shape": [height,columns],
        "display_sampling": "Spectrogram is a square display raster: rows repeat nearest source times and columns select nearest actual FFT bin on a logarithmic scale; mesh uses source time indices and unique actual bin frequencies. No additional time/frequency resolution, no interpolation, no renormalization.",
        "spectrogram_axes": "left to right = log frequency Hz; top to bottom = increasing time ms; fixed shared color scale",
        "waterfall_coordinates": "x=30*log10(frequency_hz), y=relative_dB clipped to display limits, z=0.1*time_ms",
        "source_arrays": "Full analysis axes and unmodified f32 magnitudes retained under decay/data"
    });
    rec.log_static(
        "decay/metadata",
        &rerun::TextDocument::new(
            serde_json::to_string_pretty(&metadata).map_err(|e| e.to_string())?,
        ),
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn controls() -> SharedDisplayControls {
        SharedDisplayControls {
            min_hz: 20.0,
            max_hz: 20000.0,
            start_ms: 0.0,
            end_ms: 500.0,
            floor_db: -60.0,
            ceiling_db: 0.0,
        }
    }
    #[test]
    fn display_mapping_is_fixed_bounded_and_uses_actual_indices() {
        let c = controls();
        assert_eq!(color(-100.0, &c), color(-60.0, &c));
        assert_eq!(color(20.0, &c), color(0.0, &c));
        assert_ne!(color(-30.0, &c), color(-60.0, &c));
        assert_eq!(position(100.0, 100.0, -30.0, &c), [60.0, -30.0, 10.0]);
        assert_eq!(indices(1000, 256).len(), 256);
        assert_eq!(indices(1000, 256).last(), Some(&999));
        assert_eq!(nearest_frequency(&[10.0, 20.0, 30.0], 24.0), 1);
        assert_eq!(nearest_frequency(&[10.0, 20.0, 30.0], 28.0), 2);
        let (positions, labels) = spectrogram_labels(100.0, 10000.0, 25.0, 225.0, &c);
        assert_eq!(&labels[..3], ["100", "25", "0"]);
        assert_eq!(&labels[12..15], ["10.00k", "225", "-60"]);
        assert_eq!(positions[12], [512.0, 540.0]);
    }
    #[test]
    fn both_views_retain_the_shared_crop_magnitudes() {
        let grid = WaterfallGrid {
            times_ms: vec![0.0, 100.0, 200.0],
            freqs_hz: vec![0.0, 100.0, 1000.0],
            mags_db: vec![
                vec![0.0, -10.0, -20.0],
                vec![-20.0, -30.0, -40.0],
                vec![-40.0, -50.0, -60.0],
            ],
        };
        let c = SharedDisplayControls {
            min_hz: 100.0,
            max_hz: 1000.0,
            start_ms: 100.0,
            end_ms: 200.0,
            ..controls()
        };
        let crop = c.crop(&grid).unwrap();
        assert_eq!(crop.mags_db[0], [-30.0, -40.0]);
        for t in indices(crop.times_ms.len(), MAX_TIMES) {
            for f in indices(crop.freqs_hz.len(), MAX_FREQS) {
                assert_eq!(
                    position(crop.freqs_hz[f], crop.times_ms[t], crop.mags_db[t][f], &c)[1],
                    crop.mags_db[t][f]
                );
                assert_eq!(
                    color(crop.mags_db[t][f], &c),
                    color(grid.mags_db[t + 1][f + 1], &c)
                );
            }
        }
        let (rec, _storage) = rerun::RecordingStreamBuilder::new("decay_renderer_test")
            .memory()
            .unwrap();
        assert!(log_views(&rec, &grid, &c, "known grid").is_ok());
        let empty_selection = SharedDisplayControls {
            min_hz: 2000.0,
            max_hz: 3000.0,
            ..c
        };
        assert!(log_views(&rec, &grid, &empty_selection, "known grid").is_err());
    }
}
