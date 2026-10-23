//! Independent offline draft, using the existing Designer contract and validator.
use roomeq_model::DesignerLimits;
use serde_json::{Value, json};
use sotf_dev_api::{
    Capabilities, HttpResponse, Method, RunId, canonical_json_hash,
    server::{ServerConfig, ServerHandle, start_server},
    workbench::WorkbenchRequest,
};
use sotf_rerun_viewer::ViewerSession;
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

struct Draft {
    model: DesignerLimits,
    sample_rate: f64,
    path: PathBuf,
    session: Option<Arc<ViewerSession>>,
    study: crate::study::Study,
}

impl Draft {
    fn snapshot(&self) -> Result<Value, String> {
        let config = &self.model;
        let designer = json!({"min_freq":config.min_freq,"max_freq":config.max_freq,
            "max_boost_db":config.max_db,"max_cut_db":-config.min_db});
        let revision = canonical_json_hash(&designer).map_err(|e| e.to_string())?;
        Ok(json!({"revision":revision,"designer":designer,
            "project":{"state_owner":"standalone_rerun_workbench","source":"independent_offline_draft"},
            "capabilities":{"commands":["inspect","update_designer","run_study","cancel_study"]},
            "study":self.study.snapshot(&revision)?,"chat_enabled":false,"offline_only":true}))
    }

    fn command(&mut self, request: WorkbenchRequest) -> Result<Value, String> {
        if let WorkbenchRequest::RunStudy { expected_revision }
        | WorkbenchRequest::CancelStudy { expected_revision } = &request
        {
            if self.snapshot()?["revision"].as_str() != Some(expected_revision.as_str()) {
                return Err("Stale workbench revision; inspect before retrying".into());
            }
            if matches!(request, WorkbenchRequest::RunStudy { .. }) {
                self.study
                    .start(self.model, expected_revision.clone(), self.session.clone())?;
            } else {
                self.study.cancel()?;
            }
            return self.snapshot();
        }
        let (expected_revision, patch) = match request {
            WorkbenchRequest::Inspect {} => return self.snapshot(),
            WorkbenchRequest::UpdateDesigner { expected_revision, patch } => (expected_revision, patch),
            _ => return Err("Unsupported command: this standalone pilot supports inspect and update_designer only".into()),
        };
        if self.snapshot()?["revision"].as_str() != Some(expected_revision.as_str()) {
            return Err("Stale workbench revision; inspect before retrying".into());
        }
        if self.study.running()? {
            return Err("Designer edits are locked while a study is running".into());
        }
        if patch.max_boost_db.is_some_and(|v| v < 0.0) || patch.max_cut_db.is_some_and(|v| v < 0.0)
        {
            return Err("Boost and cut limits must be nonnegative magnitudes".into());
        }
        let next = self.model.updated(
            patch.min_freq,
            patch.max_freq,
            patch.max_cut_db.map(|v| -v),
            patch.max_boost_db,
            self.sample_rate,
        )?;
        let config =
            serde_json::to_vec_pretty(&json!({"limits":next,"sample_rate":self.sample_rate}))
                .map_err(|e| e.to_string())?;
        let staging = self.path.with_extension("next.json");
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staging)
            .map_err(|e| e.to_string())?;
        let persisted = file
            .write_all(&config)
            .and_then(|_| file.sync_all())
            .and_then(|_| fs::rename(&staging, &self.path));
        if let Err(error) = persisted {
            let _ = fs::remove_file(&staging);
            return Err(format!(
                "Draft persistence failed; state unchanged: {error}"
            ));
        }
        self.model = next;
        let mut value = self.snapshot()?;
        // Accepted draft remains authoritative if the optional viewer fails to publish it.
        if let Some(session) = &self.session {
            if let Err(error) = session.log_project_entity("project/designer", &value) {
                value["viewer_publication_error"] = Value::String(error);
            }
        }
        Ok(value)
    }
}

pub fn start(
    port: u16,
    config_path: &Path,
    output: &Path,
    session: Arc<ViewerSession>,
) -> Result<ServerHandle, String> {
    let run_id = RunId::parse(
        std::env::var("SOTF_WORKBENCH_RUN_ID").map_err(|_| "SOTF_WORKBENCH_RUN_ID is required")?,
    )
    .map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    fs::File::open(config_path)
        .map_err(|e| e.to_string())?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 1024 * 1024 {
        return Err("Designer source config exceeds 1 MiB".into());
    }
    let source: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    let optimizer = source
        .get("optimizer")
        .ok_or("Source config has no optimizer")?;
    let number = |key: &str| -> Result<f64, String> {
        optimizer
            .get(key)
            .and_then(Value::as_f64)
            .ok_or_else(|| format!("Source optimizer requires numeric {key}"))
    };
    // Standalone draft operates at the Room EQ offline study's explicit 48 kHz rate.
    let sample_rate = 48_000.0;
    let model = DesignerLimits {
        min_freq: number("min_freq")?,
        max_freq: number("max_freq")?,
        min_db: number("min_db")?,
        max_db: number("max_db")?,
    };
    // Validate source values without triggering the shared no-change rejection.
    DesignerLimits {
        min_freq: 0.0,
        ..model
    }
    .updated(Some(model.min_freq), None, None, None, sample_rate)?;
    let draft = Draft {
        model,
        sample_rate,
        path: output.join("designer-draft.json"),
        session: Some(session),
        study: crate::study::Study::new(
            source,
            config_path.parent().unwrap_or(Path::new(".")).to_path_buf(),
            output.to_path_buf(),
        ),
    };
    let bytes =
        serde_json::to_vec_pretty(&json!({"limits":draft.model,"sample_rate":draft.sample_rate}))
            .map_err(|e| e.to_string())?;
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&draft.path)
        .and_then(|mut f| {
            f.write_all(&bytes)?;
            f.sync_all()
        })
        .map_err(|e| e.to_string())?;
    let state = Mutex::new(draft);
    let mut config =
        ServerConfig::loopback(run_id, Capabilities::new("workbench", "rerun-workbench"));
    config.bind.set_port(port);
    config.capabilities.limits.command_body_bytes = 8192;
    start_server(
        config,
        move |request: sotf_dev_api::HttpRequest, _context| {
            if request.method != Method::Post || request.path != "/workbench" {
                return HttpResponse::json(
                    404,
                    json!({"ok":false,"error":"Unknown workbench operation"}).to_string(),
                );
            }
            let result = serde_json::from_slice::<WorkbenchRequest>(&request.body)
                .map_err(|e| e.to_string())
                .and_then(|request| {
                    state
                        .lock()
                        .map_err(|_| "Designer state unavailable".to_string())?
                        .command(request)
                });
            match result {
                Ok(value) => HttpResponse::json(200, json!({"ok":true,"value":value}).to_string()),
                Err(error) => {
                    HttpResponse::json(400, json!({"ok":false,"error":error}).to_string())
                }
            }
        },
    )
    .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use sotf_dev_api::workbench::DesignerPatch;
    #[test]
    fn invalid_and_stale_edits_preserve_state() {
        let mut draft = Draft {
            model: DesignerLimits {
                min_freq: 20.0,
                max_freq: 20000.0,
                min_db: -12.0,
                max_db: 0.0,
            },
            sample_rate: 48000.0,
            path: PathBuf::from("unused"),
            session: None,
            study: crate::study::Study::new(json!({}), PathBuf::new(), PathBuf::from("unused")),
        };
        let before = draft.snapshot().unwrap();
        for (revision, patch) in [
            (
                "stale".to_string(),
                DesignerPatch {
                    max_cut_db: Some(5.0),
                    ..Default::default()
                },
            ),
            (
                before["revision"].as_str().unwrap().to_string(),
                DesignerPatch {
                    min_freq: Some(20000.0),
                    ..Default::default()
                },
            ),
            (
                before["revision"].as_str().unwrap().to_string(),
                DesignerPatch {
                    max_boost_db: Some(-1.0),
                    ..Default::default()
                },
            ),
        ] {
            assert!(
                draft
                    .command(WorkbenchRequest::UpdateDesigner {
                        expected_revision: revision,
                        patch
                    })
                    .is_err()
            );
            assert_eq!(draft.snapshot().unwrap(), before);
        }
        assert!(
            draft
                .command(WorkbenchRequest::RunStudy {
                    expected_revision: "stale".into()
                })
                .is_err()
        );
        draft.study.state.lock().unwrap()["status"] = json!("running");
        assert!(
            draft
                .command(WorkbenchRequest::UpdateDesigner {
                    expected_revision: before["revision"].as_str().unwrap().into(),
                    patch: DesignerPatch {
                        max_cut_db: Some(5.0),
                        ..Default::default()
                    }
                })
                .unwrap_err()
                .contains("locked")
        );
        assert!(
            draft
                .command(WorkbenchRequest::CancelStudy {
                    expected_revision: "stale".into()
                })
                .is_err()
        );
        assert!(
            draft
                .command(WorkbenchRequest::CancelStudy {
                    expected_revision: before["revision"].as_str().unwrap().into()
                })
                .is_ok()
        );
    }
    #[test]
    fn persisted_edit_and_storage_failure_are_atomic() {
        let dir = std::env::temp_dir().join(format!("rerun-designer-test-{}", std::process::id()));
        fs::create_dir(&dir).unwrap();
        let mut draft = Draft {
            model: DesignerLimits {
                min_freq: 20.0,
                max_freq: 20000.0,
                min_db: -12.0,
                max_db: 0.0,
            },
            sample_rate: 48000.0,
            path: dir.join("designer.json"),
            session: None,
            study: crate::study::Study::new(json!({}), dir.clone(), dir.clone()),
        };
        let before = draft.snapshot().unwrap();
        let patch = DesignerPatch {
            max_cut_db: Some(7.25),
            ..Default::default()
        };
        let value = draft
            .command(WorkbenchRequest::UpdateDesigner {
                expected_revision: before["revision"].as_str().unwrap().into(),
                patch,
            })
            .unwrap();
        assert_eq!(value["designer"]["max_cut_db"], 7.25);
        let saved: Value = serde_json::from_slice(&fs::read(&draft.path).unwrap()).unwrap();
        assert_eq!(saved["limits"]["min_db"], -7.25);
        draft.path = dir.join("missing").join("draft.json");
        assert!(
            draft
                .command(WorkbenchRequest::UpdateDesigner {
                    expected_revision: value["revision"].as_str().unwrap().into(),
                    patch: DesignerPatch {
                        max_cut_db: Some(9.0),
                        ..Default::default()
                    }
                })
                .is_err()
        );
        assert_eq!(draft.snapshot().unwrap(), value);
        fs::remove_dir_all(dir).unwrap();
    }
}
