//! Read-only overview of this pilot's actual data flow, not an execution engine.
use crate::display_smoothing::Mode;
use rerun::RecordingStream;

struct Node {
    id: String,
    label: String,
    details: String,
    position: [f32; 2],
}

fn valid_channel(channel: &str) -> bool {
    !channel.is_empty()
        && channel.len() <= 32
        && channel
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

fn graph(
    channels: &[String],
    modes: &[Mode],
    target: f64,
) -> Result<(Vec<Node>, Vec<(String, String)>), String> {
    if !target.is_finite()
        || channels.is_empty()
        || channels.len() > 8
        || channels.iter().any(|c| !valid_channel(c))
        || channels
            .iter()
            .collect::<std::collections::BTreeSet<_>>()
            .len()
            != channels.len()
        || modes.is_empty()
        || modes.len() > Mode::ALL.len()
        || modes
            .iter()
            .map(|m| m.as_str())
            .collect::<std::collections::BTreeSet<_>>()
            .len()
            != modes.len()
    {
        return Err(
            "Workflow requires finite target, 1..8 unique speaker names and unique supported modes"
                .into(),
        );
    }
    let modes = modes
        .iter()
        .map(|m| m.as_str())
        .collect::<Vec<_>>()
        .join(", ");
    let mut nodes = vec![
        Node { id: "target".into(), position: [-220.,-150.], label: format!("Demo target · {target:.2} dB"), details: format!("Input: first selected speaker's saved dB samples within 20..300 Hz. Output: common flat target {target:.6} dB on source frequency grid. Parameter role: derived, read-only; not an acoustically selected target or absolute SPL claim. Rule is fixed_trial, not a user target control.") },
        Node { id: "constraints".into(), position:[220.,-150.], label: "Fixed trial constraints".into(), details: "Current trial graph only, not the full Designer algorithm catalog or execution engine. Two PK filters per speaker. Parameter roles: solver_free: frequency 20..300 Hz (encoded log10 Hz), Q 0.5..3 (dimensionless), gain -12..0 dB per filter. fixed_trial: filter count 2, cut-only bounds, sample rate 48000 Hz, seed 91827, population 24, maximum generations 48, minimum convergence generation 48, no polish. Lock semantics: fixed_trial values cannot change during optimization; no interactive locks are implemented here. CLI configurable: speaker subset. Editing source is required to change trial constants.".into() },
    ];
    let mut edges = vec![(format!("{}/input", channels[0]), "target".into())];
    for (channel_index, channel) in channels.iter().enumerate() {
        let stages = [
            ("input", "Saved measurement", "Input: source-bound CSV frequency (Hz), magnitude (dB). Output: baseline curve on validated positive increasing frequency grid. Role: readonly source data; channel selection is CLI configurable. No implicit resampling or averaging.".to_owned()),
            ("solver", "Canonical DE + FlatStrategy", "Inputs: baseline, common target, fixed PK bounds. Objective: existing FlatStrategy over 20..300 Hz, using target-minus-baseline deviation. Parameter role: six solver_free variables, two sets of log10 frequency Hz, dimensionless Q, gain dB, bounded by constraints node. Output role: derived actual generation-barrier population, loss and best parameter vector. Each speaker optimized independently; no coupled speaker optimization.".to_owned()),
            ("population", "Population checkpoint", "Input: actual canonical DE checkpoint. Output: every finite-fitness candidate's two PK filters and best candidate. Finite fitness is not acoustic qualification. No polling or sampled generations.".to_owned()),
            ("response", "Canonical PEQ response", "Inputs: source baseline dB and each candidate's log10(Hz), Q, gain dB. Output: predicted corrected magnitude dB from existing ObjectiveContext.peq_spl plus baseline; not a post-calibration measurement.".to_owned()),
            ("smoothing", "Display smoothing", format!("Inputs: each candidate response, baseline, target and best response. Modes: {modes}. Output: same-grid display dB arrays. Parameter role: display-only selection between precomputed views; frozen solver state remains identical for every view. Does not change objective or filters. Each candidate is smoothed before envelope statistics.")),
            ("envelope", "Min/max + p10/p90", "Input: equally weighted, smoothed finite-fitness candidate responses. Outputs: pointwise minimum, maximum, interpolated 10th and 90th percentiles in dB. Population spread, not confidence intervals, acoustic uncertainty or FIR tap bounds.".to_owned()),
            ("comparison", "Speaker comparison", "Inputs: per-speaker best, baseline, common target and response bands. Output: Rerun comparison, common axes and actual generation timeline. Toggle speaker entities or use CLI subset. Read-only view; no controls alter calibration or run an executable graph.".to_owned()),
        ];
        for (stage_index, (stage, label, details)) in stages.into_iter().enumerate() {
            nodes.push(Node {
                id: format!("{channel}/{stage}"),
                label: format!("{channel} · {label}"),
                details,
                position: [
                    (channel_index as f32 - (channels.len() - 1) as f32 / 2.) * 600.,
                    if stage_index == 0 {
                        -350.
                    } else {
                        (stage_index - 1) as f32 * 150.
                    },
                ],
            });
        }
        for (from, to) in [
            ("input", "solver"),
            ("solver", "population"),
            ("population", "response"),
            ("response", "smoothing"),
            ("smoothing", "envelope"),
            ("envelope", "comparison"),
            ("input", "comparison"),
        ] {
            edges.push((format!("{channel}/{from}"), format!("{channel}/{to}")));
        }
        edges.push(("target".into(), format!("{channel}/solver")));
        edges.push(("constraints".into(), format!("{channel}/solver")));
        edges.push(("target".into(), format!("{channel}/comparison")));
    }
    Ok((nodes, edges))
}

pub fn log_static(
    rec: &RecordingStream,
    channels: &[String],
    modes: &[Mode],
    common_target: f64,
) -> Result<(), String> {
    let (nodes, edges) = graph(channels, modes, common_target)?;
    rec.log_static(
        "workflow/graph",
        &rerun::GraphNodes::new(nodes.iter().map(|n| n.id.as_str()))
            .with_positions(nodes.iter().map(|n| n.position))
            .with_labels(nodes.iter().map(|n| n.label.as_str()))
            .with_show_labels(true),
    )
    .map_err(|e| e.to_string())?;
    rec.log_static(
        "workflow/graph",
        &rerun::GraphEdges::new(edges.iter().map(|(a, b)| (a.as_str(), b.as_str())))
            .with_directed_edges(),
    )
    .map_err(|e| e.to_string())?;
    for node in nodes {
        rec.log_static(
            format!("workflow/stages/{}", node.id),
            &rerun::TextDocument::from_markdown(format!(
                "# {}\n\n{}",
                node.label,
                node.details
                    .replace(". Output:", ".\n\n**Output:**")
                    .replace(". Parameter role:", ".\n\n**Parameter role:**")
                    .replace(". Lock semantics:", ".\n\n**Lock semantics:**")
            )),
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn panel_specs(
    channels: &[String],
    modes: &[Mode],
    target: f64,
) -> Result<Vec<(String, String)>, String> {
    Ok(graph(channels, modes, target)?
        .0
        .into_iter()
        .map(|n| (n.id, n.label))
        .collect())
}

pub fn parameter_tree(
    specs: &[(String, String)],
    channels: &[String],
) -> rerun::blueprint::ContainerLike {
    use rerun::blueprint::{ContainerLike, Tabs, TextDocumentView};
    let view = |(id, label): &(String, String)| -> ContainerLike {
        TextDocumentView::new(label)
            .with_contents([format!("/workflow/stages/{id}")])
            .into()
    };
    let mut groups = vec![
        Tabs::new(specs.iter().filter(|(id, _)| !id.contains('/')).map(view))
            .with_name("Target and constraints")
            .into(),
    ];
    for channel in channels {
        let mut panels = specs
            .iter()
            .filter(|(id, _)| id.starts_with(&format!("{channel}/")))
            .map(view)
            .collect::<Vec<_>>();
        panels.push(
            TextDocumentView::new(format!("{channel} · Live solver parameters"))
                .with_contents([format!("/workflow/stages/{channel}/live")])
                .into(),
        );
        groups.push(
            Tabs::new(panels)
                .with_name(format!("Speaker {channel}"))
                .into(),
        );
    }
    Tabs::new(groups).with_name("Algorithm stages").into()
}

pub fn log_callback(
    rec: &RecordingStream,
    channel: &str,
    generation: usize,
    params: &[f64],
    loss: f64,
) -> Result<(), String> {
    if !valid_channel(channel)
        || params.len() != 6
        || params.iter().any(|v| !v.is_finite())
        || !loss.is_finite()
    {
        return Err(
            "Workflow callback requires speaker, six finite PK parameters and finite loss".into(),
        );
    }
    let filters = params
        .chunks_exact(3)
        .map(|p| (10f64.powf(p[0]), p[1], p[2]))
        .collect::<Vec<_>>();
    if filters
        .iter()
        .any(|(hz, _, _)| !hz.is_finite() || *hz <= 0.)
    {
        return Err("Workflow callback frequency exceeds finite range".into());
    }
    let mut text = format!(
        "{channel} · actual solver checkpoint\nGeneration: {generation}\nBest objective: {loss:.8} (FlatStrategy loss units, not dB)\nRead-only observed state; no parameter edits or calibration writes.\n"
    );
    for (index, (hz, q, db)) in filters.iter().enumerate() {
        text.push_str(&format!(
            "PK {}: frequency {hz:.5} Hz; Q {q:.6}; gain {db:.6} dB\n",
            index + 1
        ));
    }
    rec.set_time_sequence("generation", generation as i64);
    rec.log(
        format!("workflow/stages/{channel}/live"),
        &rerun::TextDocument::new(text),
    )
    .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn graph_branches_match_declared_inputs_and_edges() {
        let (nodes, edges) = graph(&["L".into(), "R".into()], &Mode::ALL, 70.).unwrap();
        let ids = nodes
            .iter()
            .map(|n| n.id.as_str())
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(nodes.len(), 16);
        assert_eq!(ids.len(), nodes.len());
        for (from, to) in &edges {
            let a = nodes.iter().find(|n| &n.id == from).unwrap();
            let b = nodes.iter().find(|n| &n.id == to).unwrap();
            assert!(
                a.position[1] < b.position[1],
                "Every dependency must flow top to bottom"
            );
        }
        assert!(
            edges
                .iter()
                .all(|(a, b)| ids.contains(a.as_str()) && ids.contains(b.as_str()))
        );
        assert!(edges.contains(&("constraints".into(), "R/solver".into())));
        assert!(
            nodes
                .iter()
                .any(|n| n.id == "L/smoothing" && n.details.contains("psychoacoustic"))
        );
        assert!(graph(&["L".into(), "L".into()], &Mode::ALL, 70.).is_err());
        assert!(graph(&["../L".into()], &Mode::ALL, 70.).is_err());
        assert!(graph(&["L".into()], &Mode::ALL, f64::NAN).is_err());
    }
}
