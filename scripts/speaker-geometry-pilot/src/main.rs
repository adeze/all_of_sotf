//! Offline geometry qualification; never participates in calibration generation.
use manifold_rust::{
    linalg::Vec3,
    manifold::Manifold,
    types::{Error, MeshGL64},
};
use rerun::blueprint::{Blueprint, Horizontal, Spatial3DView};
use serde::{Deserialize, Serialize};
use solarxy_kernel::{KernelMesh, primitives::generate_box};
use std::{collections::BTreeMap, error::Error as StdError, path::Path};

type Result<T> = std::result::Result<T, Box<dyn StdError>>;
const SOLARXY_REV: &str = "9aa3b49191f0904478fd3a19dc7a4c1fbbfb6e36";

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Parameters {
    width_m: f64,
    height_m: f64,
    depth_m: f64,
    wall_m: f64,
    port_radius_m: f64,
    port_length_m: f64,
    port_wall_m: f64,
}
impl Default for Parameters {
    fn default() -> Self {
        Self {
            width_m: 0.260,
            height_m: 0.450,
            depth_m: 0.350,
            wall_m: 0.018,
            port_radius_m: 0.025,
            port_length_m: 0.160,
            port_wall_m: 0.004,
        }
    }
}
impl Parameters {
    fn validate(self) -> Result<Self> {
        let values = [
            self.width_m,
            self.height_m,
            self.depth_m,
            self.wall_m,
            self.port_radius_m,
            self.port_length_m,
            self.port_wall_m,
        ];
        if values.iter().any(|v| !v.is_finite() || *v <= 0.) {
            return Err("Geometry parameters must be finite, positive metres".into());
        }
        // shortcut: fixed driver/port centres, widen this envelope only with layout constraints.
        if !(0.240..=0.400).contains(&self.width_m)
            || !(0.430..=0.650).contains(&self.height_m)
            || !(0.300..=0.500).contains(&self.depth_m)
            || !(0.010..=0.030).contains(&self.wall_m)
            || !(0.015..=0.030).contains(&self.port_radius_m)
            || !(0.002..=0.008).contains(&self.port_wall_m)
            || self.port_length_m <= self.wall_m + 0.020
            || self.port_length_m >= self.depth_m - 2. * self.wall_m - 0.020
        {
            return Err("Parameters exceed the qualified pilot layout envelope".into());
        }
        Ok(self)
    }
}

/// Solarxy deliberately duplicates face vertices for normals/UVs. Weld exact
/// coincident positions for solid topology; do not round or repair arbitrary meshes.
fn solarxy_solid(mesh: &KernelMesh) -> Result<Manifold> {
    let mut indices = BTreeMap::new();
    let mut solid = MeshGL64 {
        num_prop: 3,
        ..Default::default()
    };
    let mut remap = Vec::new();
    for point in mesh.positions.iter() {
        if point.iter().any(|v| !v.is_finite()) {
            return Err("Non-finite Solarxy vertex".into());
        }
        let key = point.map(|v| if v == 0. { 0 } else { v.to_bits() });
        let next = (solid.vert_properties.len() / 3) as u64;
        let id = *indices.entry(key).or_insert_with(|| {
            solid
                .vert_properties
                .extend(point.iter().map(|&v| f64::from(v)));
            next
        });
        remap.push(id);
    }
    if mesh.indices.len() % 3 != 0 {
        return Err("Expected triangle mesh".into());
    }
    for &i in mesh.indices.iter() {
        solid
            .tri_verts
            .push(*remap.get(i as usize).ok_or("Invalid Solarxy index")?);
    }
    checked(Manifold::from_mesh_gl64(&solid))
}
fn checked(solid: Manifold) -> Result<Manifold> {
    if solid.status() != Error::NoError
        || solid.is_empty()
        || !solid.volume().is_finite()
        || solid.volume() <= 0.
        || solid.has_self_intersections()
    {
        return Err(format!("Invalid solid: {:?}", solid.status()).into());
    }
    Ok(solid)
}
fn box_solid(w: f64, h: f64, d: f64) -> Result<Manifold> {
    solarxy_solid(&generate_box(w as f32, h as f32, d as f32, 1, 1, 1))
}
fn tube(length: f64, low: f64, high: f64, y: f64, z: f64) -> Manifold {
    Manifold::cylinder(length, low, high, 48).translate(Vec3::new(0., y, z))
}
struct Model {
    cabinet: Manifold,
    cutaway: Manifold,
    air: Manifold,
    duct: Manifold,
}
fn build(p: Parameters) -> Result<Model> {
    let p = p.validate()?;
    let outer = box_solid(p.width_m, p.height_m, p.depth_m)?;
    let chamber = box_solid(
        p.width_m - 2. * p.wall_m,
        p.height_m - 2. * p.wall_m,
        p.depth_m - 2. * p.wall_m,
    )?;
    let front = p.depth_m / 2.;
    let start = front - p.port_length_m;
    let flare = 0.020;
    let duct_outer = checked(
        tube(
            p.port_length_m - flare,
            p.port_radius_m + p.port_wall_m,
            p.port_radius_m + p.port_wall_m,
            -0.145,
            start,
        )
        .union(&tube(
            flare,
            p.port_radius_m + p.port_wall_m,
            p.port_radius_m + p.port_wall_m + 0.008,
            -0.145,
            front - flare,
        )),
    )?;
    let bore = checked(
        tube(
            p.port_length_m - flare + 0.001,
            p.port_radius_m,
            p.port_radius_m,
            -0.145,
            start - 0.001,
        )
        .union(&tube(
            flare + 0.001,
            p.port_radius_m,
            p.port_radius_m + 0.008,
            -0.145,
            front - flare,
        )),
    )?;
    let duct = checked(duct_outer.difference(&bore))?;
    let shell = checked(outer.difference(&chamber))?;
    let woofer = tube(
        p.wall_m + 0.002,
        0.080,
        0.080,
        -0.010,
        front - p.wall_m - 0.001,
    );
    let tweeter = tube(
        p.wall_m + 0.002,
        0.026,
        0.050,
        0.145,
        front - p.wall_m - 0.001,
    );
    let cabinet = checked(
        shell
            .union(&duct_outer)
            .difference(&bore)
            .difference(&woofer)
            .difference(&tweeter),
    )?;
    let air = checked(chamber.difference(&duct_outer))?;
    let cutter = Manifold::cube(Vec3::new(p.width_m, p.height_m * 2., p.depth_m * 2.), true)
        .translate(Vec3::new(p.width_m / 2., 0., 0.));
    let cutaway = checked(cabinet.difference(&cutter))?;
    Ok(Model {
        cabinet,
        cutaway,
        air,
        duct,
    })
}
fn log_mesh(
    rec: &rerun::RecordingStream,
    path: &str,
    solid: &Manifold,
    color: [u8; 3],
) -> Result<()> {
    let mesh = solid.calculate_normals(0, 30.).get_mesh_gl64(0);
    let vertices: Vec<[f32; 3]> = mesh
        .vert_properties
        .chunks_exact(mesh.num_prop as usize)
        .map(|v| [v[0] as f32, v[1] as f32, v[2] as f32])
        .collect();
    let normals: Vec<[f32; 3]> = mesh
        .vert_properties
        .chunks_exact(mesh.num_prop as usize)
        .map(|v| [v[3] as f32, v[4] as f32, v[5] as f32])
        .collect();
    let faces: Vec<[u32; 3]> = mesh
        .tri_verts
        .chunks_exact(3)
        .map(|t| {
            Ok([
                u32::try_from(t[0])?,
                u32::try_from(t[1])?,
                u32::try_from(t[2])?,
            ])
        })
        .collect::<Result<_>>()?;
    rec.log_static(
        path,
        &rerun::Mesh3D::new(vertices)
            .with_vertex_normals(normals)
            .with_triangle_indices(faces)
            .with_albedo_factor(*rerun::Color::from_rgb(color[0], color[1], color[2])),
    )?;
    Ok(())
}
fn log_model(rec: &rerun::RecordingStream, model: &Model) -> Result<()> {
    rec.log_static("/", &rerun::ViewCoordinates::RIGHT_HAND_Y_UP())?;
    log_mesh(rec, "full/cabinet", &model.cabinet, [184, 192, 207])?;
    log_mesh(rec, "cutaway/cabinet", &model.cutaway, [184, 192, 207])?;
    log_mesh(rec, "cutaway/port", &model.duct, [215, 140, 70])?;
    log_mesh(rec, "air/chamber", &model.air, [80, 155, 220])?;
    Ok(())
}
fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let output = args
        .next()
        .ok_or("Usage: speaker-geometry-pilot NEW_OUTPUT [parameters.json] [--serve]")?;
    let rest: Vec<_> = args.collect();
    let params = rest
        .iter()
        .find(|s| s.as_str() != "--serve")
        .map(|s| -> Result<Parameters> { Ok(serde_json::from_slice(&std::fs::read(s)?)?) })
        .transpose()?
        .unwrap_or_default()
        .validate()?;
    let model = build(params)?;
    std::fs::create_dir(&output)?; // Refuse overwrite of an earlier trial.
    let output = Path::new(&output);
    let blueprint = || {
        Blueprint::new(Horizontal::new([
            Spatial3DView::new("Cabinet · metres")
                .with_origin("full")
                .with_contents(["/full/**"])
                .into(),
            Spatial3DView::new("Cutaway · removed right half")
                .with_origin("cutaway")
                .with_contents(["/cutaway/**"])
                .into(),
            Spatial3DView::new("Chamber air · excludes port envelope")
                .with_origin("air")
                .with_contents(["/air/**"])
                .into(),
        ]))
        .with_auto_layout(false)
        .with_auto_views(false)
    };
    let rec = rerun::RecordingStreamBuilder::new("adeze_speaker_geometry_pilot")
        .with_default_blueprint(blueprint())
        .save(output.join("speaker.rrd"))?;
    log_model(&rec, &model)?;
    for (name, solid) in [
        ("cabinet", &model.cabinet),
        ("cutaway", &model.cutaway),
        ("chamber-air", &model.air),
        ("port", &model.duct),
    ] {
        std::fs::write(output.join(format!("{name}.obj")), solid.write_obj())?;
    }
    let receipt = serde_json::json!({"parameters": params, "solarxy_revision": SOLARXY_REV,
        "manifold_version":"0.16.0", "coordinate_system":"right-handed Y-up, +Z front, metres",
        "cabinet_material_litres": model.cabinet.volume()*1000.,
        "chamber_air_excluding_port_envelope_litres":model.air.volume()*1000.,
        "port_material_litres":model.duct.volume()*1000.,
        "all_solids_valid":true, "acoustically_validated":false,
        "limitations":["Illustrative driver apertures, not manufacturer geometry",
            "No driver, brace or absorber displacement", "No port tuning or waveguide acoustic qualification",
            "Solarxy f32 source positions promoted to f64 for Manifold; no recovered precision"]});
    std::fs::write(
        output.join("receipt.json"),
        serde_json::to_vec_pretty(&receipt)?,
    )?;
    println!("{receipt}");
    rec.flush_blocking()?;
    if rest.iter().any(|s| s == "--serve") {
        let live = rerun::RecordingStreamBuilder::new("adeze_speaker_geometry_pilot")
            .with_default_blueprint(blueprint())
            .serve_grpc_opts(
                "127.0.0.1",
                9878,
                rerun::ServerOptions {
                    memory_limit: rerun::MemoryLimit::from_bytes(32 * 1024 * 1024),
                    ..Default::default()
                },
            )?;
        log_model(&live, &model)?;
        live.flush_blocking()?;
        let _web = rerun::web_viewer::WebViewerConfig {
            bind_ip: "127.0.0.1".into(),
            open_browser: false,
            ..Default::default()
        }
        .host_web_viewer()?;
        println!(
            "VIEWER_URL={}?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9878%2Fproxy&renderer=webgl",
            _web.bound_url()
        );
        loop {
            std::thread::park();
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn solarxy_conversion_preserves_volume_and_rejects_open_mesh() -> Result<()> {
        let mut mesh = generate_box(0.26, 0.45, 0.35, 1, 1, 1);
        assert!((solarxy_solid(&mesh)?.volume() - 0.26 * 0.45 * 0.35).abs() < 1e-8);
        std::sync::Arc::make_mut(&mut mesh.indices).truncate(3);
        assert!(solarxy_solid(&mesh).is_err());
        Ok(())
    }
    #[test]
    fn cabinet_boolean_volumes_and_parameter_changes() -> Result<()> {
        let p = Parameters::default();
        let a = build(p)?;
        assert!(a.air.volume() > 0.025 && a.air.volume() < 0.030);
        assert!(a.cutaway.volume() < a.cabinet.volume());
        assert!((a.cutaway.volume() * 2. - a.cabinet.volume()).abs() < 1e-8);
        let b = build(Parameters { wall_m: 0.022, ..p })?;
        assert!(b.air.volume() < a.air.volume());
        assert!(b.cabinet.volume() > a.cabinet.volume());
        Ok(())
    }
    #[test]
    fn invalid_parameters_fail_closed() {
        for p in [
            Parameters {
                wall_m: f64::NAN,
                ..Default::default()
            },
            Parameters {
                port_length_m: 1.,
                ..Default::default()
            },
            Parameters {
                width_m: 0.,
                ..Default::default()
            },
        ] {
            assert!(build(p).is_err());
        }
    }
}
