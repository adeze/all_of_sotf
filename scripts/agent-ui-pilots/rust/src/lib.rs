//! Offline AG-UI transport qualification. No model, calibration or receiver execution.
use ag_ui_client::{
    Agent, HttpAgent,
    agent::{AgentError, AgentStateMutation, RunAgentParams},
    subscriber::{AgentSubscriber, AgentSubscriberParams},
};
use ag_ui_core::event::Event;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sotf_dev_api::workbench::WorkbenchRequest;
use std::{
    error::Error,
    io::{Read, Write},
    net::{IpAddr, TcpListener},
    sync::{Arc, Mutex},
    time::Duration,
};

pub type PilotError = Box<dyn Error + Send + Sync>;
const MAX_EVENT_BYTES: usize = 64 * 1024;
const PANEL_EVENT: &str = "roomcal.designer-panel";

pub fn fixture() -> Result<Value, PilotError> {
    Ok(serde_json::from_str(include_str!(
        "../../fixtures/workbench.json"
    ))?)
}

/// Read-only projection; canonical configuration and numerical validation stay in the host.
#[derive(Debug, Deserialize, Serialize)]
pub struct Presentation {
    pub revision: String,
    pub designer: DesignerLimits,
    pub chat_enabled: bool,
    pub offline_only: bool,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct DesignerLimits {
    pub min_freq: f64,
    pub max_freq: f64,
    pub max_cut_db: f64,
    pub max_boost_db: f64,
}

fn presentation(value: &Value) -> Result<Presentation, PilotError> {
    if serde_json::to_vec(value)?.len() > MAX_EVENT_BYTES {
        return Err("presentation exceeds offline pilot limit".into());
    }
    let view: Presentation = serde_json::from_value(value.clone())?;
    if view.chat_enabled
        || !view.offline_only
        || view.revision.is_empty()
        || view.revision.len() > 256
    {
        return Err("expected a revision-bound offline presentation with chat disabled".into());
    }
    Ok(view)
}

/// Decode only existing commands; returning a proposal never executes it.
pub fn command_proposal(snapshot: &Value, proposal: Value) -> Result<WorkbenchRequest, PilotError> {
    let view = presentation(snapshot)?;
    if serde_json::to_vec(&proposal)?.len() > 4096 {
        return Err("proposal exceeds offline pilot limit".into());
    }
    let command: WorkbenchRequest = serde_json::from_value(proposal)?;
    let revision = match &command {
        WorkbenchRequest::Inspect {} => return Ok(command),
        WorkbenchRequest::UpdateDesigner {
            expected_revision, ..
        }
        | WorkbenchRequest::RunStudy { expected_revision }
        | WorkbenchRequest::CancelStudy { expected_revision } => expected_revision,
    };
    if revision != &view.revision {
        return Err("stale workbench proposal; inspect the current host state".into());
    }
    Ok(command)
}

#[derive(Default, Serialize)]
struct EventsSeen {
    count: usize,
    started: bool,
    finished: bool,
    panel: Option<Value>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PanelEnvelope {
    kind: String,
    schema_version: u32,
    read_only: bool,
    snapshot: Value,
}

#[derive(Clone)]
struct Observer(Arc<Mutex<EventsSeen>>);

fn rejected(message: impl ToString) -> AgentError {
    AgentError::ExecutionError {
        message: message.to_string(),
    }
}

#[async_trait::async_trait]
impl AgentSubscriber for Observer {
    async fn on_event(
        &self,
        event: &Event<Value>,
        params: AgentSubscriberParams<'async_trait, Value, Value>,
    ) -> Result<AgentStateMutation<Value>, AgentError> {
        if serde_json::to_vec(event)?.len() > MAX_EVENT_BYTES {
            return Err(rejected("event exceeds offline pilot limit"));
        }
        let mut seen = self.0.lock().map_err(rejected)?;
        seen.count += 1;
        if seen.count > 32 || seen.finished {
            return Err(rejected(
                "unexpected event after completion or event limit exceeded",
            ));
        }
        match event {
            Event::RunStarted(_) if !seen.started => seen.started = true,
            _ if !seen.started => return Err(rejected("missing RUN_STARTED")),
            Event::StateSnapshot(event) => {
                let snapshot = presentation(&event.snapshot).map_err(rejected)?;
                if Some(snapshot.revision.as_str()) != params.input.state["revision"].as_str() {
                    return Err(rejected("snapshot revision changed during offline run"));
                }
            }
            Event::StateDelta(event) => {
                let delta = serde_json::to_value(&event.delta)?;
                let operations = delta
                    .as_array()
                    .ok_or_else(|| rejected("invalid state delta"))?;
                if operations.len() != 1
                    || operations.iter().any(|op| {
                        op["op"] != "replace"
                            || op["path"] != "/study/message"
                            || op["value"].as_str().is_none_or(|text| text.len() > 4096)
                    })
                {
                    return Err(rejected(
                        "only read-only study presentation deltas are accepted",
                    ));
                }
            }
            Event::Custom(event) if event.name == PANEL_EVENT => {
                let panel: PanelEnvelope = serde_json::from_value(event.value.clone())?;
                if panel.kind != "designer_panel" || panel.schema_version != 1 || !panel.read_only {
                    return Err(rejected("unsupported offline Designer panel"));
                }
                let panel_state = presentation(&panel.snapshot).map_err(rejected)?;
                if Some(panel_state.revision.as_str()) != params.input.state["revision"].as_str() {
                    return Err(rejected("panel revision differs from the inspected state"));
                }
                seen.panel = Some(event.value.clone());
            }
            Event::RunFinished(_) => seen.finished = true,
            Event::RunError(event) => return Err(rejected(&event.message)),
            _ => return Err(rejected("unsupported event in offline workbench pilot")),
        }
        Ok(AgentStateMutation::default())
    }
}

#[derive(Serialize)]
pub struct PilotReport {
    pub sdk: &'static str,
    pub chat_enabled: bool,
    pub presentation: Presentation,
    pub state: Value,
    events: EventsSeen,
}

/// The URL must name a literal loopback IP. This pilot never connects to a model provider.
pub async fn inspect_stream(endpoint: &str) -> Result<PilotReport, PilotError> {
    let url = reqwest::Url::parse(endpoint)?;
    let host = url.host_str().unwrap_or_default().trim_matches(['[', ']']);
    if url.scheme() != "http"
        || !host.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback())
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || url.query().is_some()
        || url.path() != "/__roomcal/agent-ui/offline"
    {
        return Err("offline AG-UI endpoint must use HTTP and a literal loopback IP".into());
    }
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(5))
        .build()?;
    let agent = HttpAgent::builder()
        .with_url(url)
        .with_http_client(client)
        .build()?;
    let params: RunAgentParams<Value, Value> = RunAgentParams {
        state: fixture()?,
        // Published 0.1.0 unwraps this optional field instead of supplying a default.
        forwarded_props: Some(json!({})),
        ..Default::default()
    };
    let observer = Observer(Arc::new(Mutex::new(EventsSeen::default())));
    let result = tokio::time::timeout(
        Duration::from_secs(6),
        agent.run_agent(&params, (observer.clone(),)),
    )
    .await??;
    let events = Arc::try_unwrap(observer.0)
        .map_err(|_| "SDK retained event observer after completion")?
        .into_inner()
        .map_err(|_| "event observer was poisoned")?;
    if !events.started
        || !events.finished
        || events.panel.is_none()
        || !result.new_messages.is_empty()
    {
        return Err("offline run did not finish with a panel and no chat messages".into());
    }
    Ok(PilotReport {
        sdk: "ag-ui-client=0.1.0",
        chat_enabled: false,
        presentation: presentation(&result.new_state)?,
        state: result.new_state,
        events,
    })
}

/// One-request fixture server for executable qualification, bound to an ephemeral loopback port.
pub fn mock_endpoint()
-> Result<(String, std::thread::JoinHandle<Result<(), PilotError>>), PilotError> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    listener.set_nonblocking(true)?;
    let endpoint = format!(
        "http://{}/__roomcal/agent-ui/offline",
        listener.local_addr()?
    );
    let task = std::thread::spawn(move || {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let (mut socket, _) = loop {
            match listener.accept() {
                Ok(accepted) => break accepted,
                Err(error)
                    if error.kind() == std::io::ErrorKind::WouldBlock
                        && std::time::Instant::now() < deadline =>
                {
                    std::thread::sleep(Duration::from_millis(5))
                }
                Err(error) => return Err(error.into()),
            }
        };
        socket.set_nonblocking(false)?;
        socket.set_read_timeout(Some(Duration::from_secs(5)))?;
        socket.set_write_timeout(Some(Duration::from_secs(5)))?;
        let mut bytes = Vec::new();
        let header_end = loop {
            let mut part = [0; 1024];
            let read = socket.read(&mut part)?;
            if read == 0 || bytes.len() + read > 16 * 1024 {
                return Err("missing or oversized fixture request".into());
            }
            bytes.extend_from_slice(&part[..read]);
            if let Some(index) = bytes.windows(4).position(|part| part == b"\r\n\r\n") {
                break index + 4;
            }
        };
        let header = std::str::from_utf8(&bytes[..header_end])?;
        if !header.starts_with("POST /__roomcal/agent-ui/offline HTTP/1.1\r\n") {
            return Err("unexpected offline fixture route".into());
        }
        let length: usize = header
            .lines()
            .find_map(|line| {
                let (key, value) = line.split_once(':')?;
                key.eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse().ok())
                    .flatten()
            })
            .ok_or("fixture request requires Content-Length")?;
        if length > 8192 {
            return Err("fixture request exceeds limit".into());
        }
        while bytes.len() < header_end + length {
            let mut part = [0; 1024];
            let read = socket.read(&mut part)?;
            if read == 0 {
                return Err("truncated fixture request".into());
            }
            bytes.extend_from_slice(&part[..read]);
        }
        let input: Value = serde_json::from_slice(&bytes[header_end..header_end + length])?;
        let events = [
            json!({"type":"RUN_STARTED", "threadId":input["threadId"], "runId":input["runId"]}),
            json!({"type":"STATE_SNAPSHOT", "snapshot":fixture()?}),
            json!({"type":"STATE_DELTA", "delta":[{"op":"replace","path":"/study/message","value":"Offline SDK stream complete; no study ran"}]}),
            json!({"type":"CUSTOM", "name":PANEL_EVENT, "value":{"kind":"designer_panel","schema_version":1,"read_only":true,"snapshot":fixture()?}}),
            json!({"type":"RUN_FINISHED", "threadId":input["threadId"], "runId":input["runId"], "result":{"offline_only":true}}),
        ];
        let body = events
            .iter()
            .map(|event| format!("data: {event}\n\n"))
            .collect::<String>();
        write!(
            socket,
            "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )?;
        Ok(())
    });
    Ok((endpoint, task))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn real_sdk_consumes_loopback_lifecycle_state_and_panel() {
        let (endpoint, server) = mock_endpoint().unwrap();
        let report = inspect_stream(&endpoint).await.unwrap();
        server.join().unwrap().unwrap();
        assert_eq!(report.events.count, 5);
        assert!(report.events.finished);
        assert_eq!(
            report.state["study"]["message"],
            "Offline SDK stream complete; no study ran"
        );
        assert!(!report.chat_enabled);
        assert!(inspect_stream("https://example.com/agent").await.is_err());
        assert!(inspect_stream("http://127.0.0.1:3000/model").await.is_err());
        let input = ag_ui_core::types::input::RunAgentInput::new(
            ag_ui_core::types::ids::ThreadId::random(),
            ag_ui_core::types::ids::RunId::random(),
            fixture().unwrap(),
            vec![],
            vec![],
            vec![],
            json!({}),
        );
        let observer = Observer(Arc::new(Mutex::new(EventsSeen {
            started: true,
            ..Default::default()
        })));
        let delta: Event<Value> = serde_json::from_value(json!({
            "type":"STATE_DELTA", "delta":[{"op":"replace","path":"/designer/max_boost_db","value":25}]
        })).unwrap();
        assert!(
            observer
                .on_event(
                    &delta,
                    AgentSubscriberParams {
                        messages: &[],
                        state: &input.state,
                        input: &input,
                    }
                )
                .await
                .is_err()
        );
    }

    #[test]
    fn proposals_use_existing_contract_and_reject_stale_or_unsupported_commands() {
        let snapshot = fixture().unwrap();
        let command = json!({"operation":"update_designer", "expected_revision":"offline-pilot-r1", "patch":{"max_cut_db":6.0}});
        assert!(matches!(
            command_proposal(&snapshot, command.clone()).unwrap(),
            WorkbenchRequest::UpdateDesigner { .. }
        ));
        for value in [
            json!({"operation":"transfer"}),
            json!({"operation":"update_designer", "expected_revision":"stale", "patch":{"max_cut_db":6.0}}),
            json!({"operation":"update_designer", "expected_revision":"offline-pilot-r1", "patch":{"arbitrary":2}}),
        ] {
            assert!(command_proposal(&snapshot, value).is_err());
        }
        let mut online = snapshot;
        online["chat_enabled"] = json!(true);
        assert!(command_proposal(&online, command).is_err());
    }
}
