use super::*;
use rerun::{
    RecordingStream, RecordingStreamBuilder,
    blueprint::{Blueprint, Spatial3DView, Tabs, TextDocumentView, TimeSeriesView},
};
use std::{net::TcpListener, time::Duration};

pub(super) fn show(
    r: &Reference,
    waves: &[WaveResult],
    rays: &Value,
    out: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let blueprint = || {
        Blueprint::new(Tabs::new([
            TimeSeriesView::new("Grid comparison · frequency_hz · relative dB")
                .with_contents(["/spectra/**"])
                .into(),
            Spatial3DView::new("Pressure · 0.20 m grid")
                .with_origin("room")
                .with_contents([
                    "/room/boundaries",
                    "/room/source_listener",
                    "/room/grid_0/**",
                ])
                .into(),
            Spatial3DView::new(format!(
                "Pressure · finest {} m grid",
                waves.last().unwrap().grid_m
            ))
            .with_origin("room")
            .with_contents([
                "/room/boundaries",
                "/room/source_listener",
                &format!("/room/grid_{}/**", waves.len() - 1),
            ])
            .into(),
            Spatial3DView::new("First reflections · left-open approximation")
                .with_origin("rays")
                .with_contents(["/rays/**"])
                .into(),
            TimeSeriesView::new("Swift / Sonium · arbitrary pressure")
                .with_contents(["/comparison/**"])
                .into(),
            TimeSeriesView::new("Ray arrivals · energy, not coherent IR")
                .with_contents(["/energy/**"])
                .into(),
            TextDocumentView::new("Trial scope")
                .with_contents(["/scope"])
                .into(),
        ]))
        .with_auto_layout(false)
        .with_auto_views(false)
    };
    let store = rerun::StoreId::random(rerun::StoreKind::Recording, "sonium_swift_room_trial");
    let make = || RecordingStreamBuilder::from_store_id(&store).with_default_blueprint(blueprint());
    let file = make().save(out.join("sonium-swift-room.rrd"))?;
    let reservation = TcpListener::bind(("127.0.0.1", 0))?;
    let port = reservation.local_addr()?.port();
    drop(reservation);
    let live = make().serve_grpc_opts(
        "127.0.0.1",
        port,
        rerun::ServerOptions {
            memory_limit: rerun::MemoryLimit::from_bytes(64 * 1024 * 1024),
            ..Default::default()
        },
    )?;
    for stream in [&file, &live] {
        log(stream, r, waves, rays)?;
    }
    file.flush_with_timeout(Duration::from_secs(5))?;
    live.flush_with_timeout(Duration::from_secs(5))?;
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
    write_new(
        &out.join("viewer.json"),
        &json!({"viewer_url":url,"lifetime_seconds":1800,"transfer_prohibited":true}),
    )?;
    println!(
        "{}",
        json!({"viewer_url":url,"output":out,"wave_relative_errors":waves.iter().map(|w|w.relative_l2_pressure_error).collect::<Vec<_>>()})
    );
    std::thread::sleep(Duration::from_secs(1800));
    Ok(())
}
fn log(
    s: &RecordingStream,
    r: &Reference,
    waves: &[WaveResult],
    rays: &Value,
) -> Result<(), Box<dyn std::error::Error>> {
    s.set_log_time_enabled(false);
    let comparisons = waves
        .windows(2)
        .map(|p| grid_comparison(&p[0], &p[1]))
        .collect::<Vec<_>>();
    s.log_static("scope",&rerun::TextDocument::new(format!("Experimental numeric comparison only. Saved nominal VisionPlan theatre approximation; registered mesh remains solver-unapproved. Wave: sealed rigid lossless 3D FD operator driven by unchanged Sonium LeapfrogStepper, compared with unchanged Swift SpatialWaveStudy. Rays: left boundary open, rigid surfaces, unmeasured materials deliberately not fitted. Geometric reference is analytical, not executed Swift raycaster. No calibrated SPL, full FEM assembly, convergence claim, filters or receiver writes. Pressure glyphs are spatially subsampled for display only; red positive, blue negative; fixed scale per grid. For spectral comparison select frequency_hz timeline; all curves share one amplitude reference. For room animation select simulation_time.\n\nAdjacent-grid relative spectral differences:\n{}",serde_json::to_string_pretty(&comparisons)?)))?;
    for root in ["room", "rays"] {
        s.log_static(root, &rerun::ViewCoordinates::RIGHT_HAND_Z_UP())?;
        let lines = super::faces(r.dimensions_meters, root == "rays")
            .into_iter()
            .map(|f| {
                let mut p: Vec<_> = f.vertices.iter().map(|v| v.map(|x| x as f32)).collect();
                p.push(p[0]);
                p
            })
            .collect::<Vec<_>>();
        s.log_static(
            format!("{root}/boundaries"),
            &rerun::LineStrips3D::new(lines).with_colors([0x6B778888u32]),
        )?;
        s.log_static(
            format!("{root}/source_listener"),
            &rerun::Points3D::new([
                r.source_meters.map(|v| v as f32),
                r.listener_meters.map(|v| v as f32),
            ])
            .with_colors([0xDA913CFFu32, 0x27895AFF])
            .with_radii([0.07])
            .with_labels(["FL", "MLP"]),
        )?;
    }
    for p in rays["first_order_paths"]
        .as_array()
        .ok_or("Missing ray paths")?
    {
        let hit: [f64; 3] = serde_json::from_value(p["hit_m"].clone())?;
        s.log_static(
            format!(
                "rays/paths/{}",
                p["boundary"].as_str().ok_or("Missing boundary")?
            ),
            &rerun::LineStrips3D::new([vec![
                r.source_meters.map(|v| v as f32),
                hit.map(|v| v as f32),
                r.listener_meters.map(|v| v as f32),
            ]])
            .with_colors([0x287ACBFFu32]),
        )?;
    }
    for (i, w) in waves.iter().enumerate() {
        let max = w
            .frames
            .iter()
            .flat_map(|(_, p)| p)
            .map(|v| v.abs())
            .fold(0.0, f64::max)
            .max(1e-30);
        for (time, field) in &w.frames {
            s.set_time("simulation_time", std::time::Duration::from_secs_f64(*time));
            let colors = field
                .iter()
                .map(|&p| {
                    let a = (p.abs() / max).sqrt().clamp(0., 1.);
                    if p >= 0. {
                        [216, 67, 57, (a * 220.) as u8]
                    } else {
                        [51, 112, 211, (a * 220.) as u8]
                    }
                })
                .collect::<Vec<_>>();
            s.log(
                format!("room/grid_{i}/pressure"),
                &rerun::Points3D::new(w.points.clone())
                    .with_colors(colors)
                    .with_radii([(w.grid_m * 0.16) as f32]),
            )?;
        }
        s.log_static(
            format!("comparison/grid_{i}/sonium"),
            &rerun::SeriesLines::new().with_names([format!("Sonium h={}m", w.grid_m)]),
        )?;
        s.log_static(
            format!("comparison/grid_{i}/swift"),
            &rerun::SeriesLines::new().with_names([format!("Swift h={}m", w.grid_m)]),
        )?;
        for (k, (&actual, &expected)) in w
            .pressure
            .iter()
            .zip(&r.resolutions[i].pressure)
            .enumerate()
        {
            s.set_time(
                "simulation_time",
                Duration::from_secs_f64((k + 1) as f64 * w.dt_s),
            );
            s.log(
                format!("comparison/grid_{i}/sonium"),
                &rerun::Scalars::new([actual]),
            )?;
            s.log(
                format!("comparison/grid_{i}/swift"),
                &rerun::Scalars::new([expected]),
            )?;
        }
    }
    for (band, hist) in rays["energy_histograms"]
        .as_array()
        .ok_or("Missing energy histograms")?
        .iter()
        .enumerate()
    {
        for (k, e) in hist
            .as_array()
            .ok_or("Malformed histogram")?
            .iter()
            .enumerate()
        {
            s.set_time(
                "simulation_time",
                Duration::from_secs_f64(k as f64 * 0.0001),
            );
            s.log(
                format!("energy/{}Hz", super::rays::OCTAVE_BANDS[band]),
                &rerun::Scalars::new([e.as_f64().ok_or("Malformed energy")?]),
            )?;
        }
    }
    s.disable_timeline("simulation_time");
    let reference = waves
        .iter()
        .flat_map(|w| {
            w.response_real
                .iter()
                .zip(&w.response_imaginary)
                .map(|(re, im)| re.hypot(*im))
        })
        .fold(0., f64::max)
        .max(1e-30);
    for (i, w) in waves.iter().enumerate() {
        s.log_static(
            format!("spectra/grid_{i}"),
            &rerun::SeriesLines::new().with_names([format!("h={} m", w.grid_m)]),
        )?;
        for ((f, re), im) in w
            .frequencies_hz
            .iter()
            .zip(&w.response_real)
            .zip(&w.response_imaginary)
        {
            s.set_time_sequence("frequency_hz", *f as i64);
            s.log(
                format!("spectra/grid_{i}"),
                &rerun::Scalars::new([20. * (re.hypot(*im) / reference).max(1e-15).log10()]),
            )?;
        }
    }
    s.disable_timeline("frequency_hz");
    Ok(())
}
