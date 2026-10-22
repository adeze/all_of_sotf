use roomcal_agui_pilot::{PilotError, inspect_stream, mock_endpoint};

#[tokio::main]
async fn main() -> Result<(), PilotError> {
    let endpoint = std::env::args().nth(1);
    let (endpoint, server) = match endpoint {
        Some(endpoint) => (endpoint, None),
        None => {
            let (endpoint, server) = mock_endpoint()?;
            (endpoint, Some(server))
        }
    };
    let report = inspect_stream(&endpoint).await;
    if let Some(server) = server {
        server
            .join()
            .map_err(|_| "offline fixture server panicked")??;
    }
    println!("{}", serde_json::to_string_pretty(&report?)?);
    Ok(())
}
