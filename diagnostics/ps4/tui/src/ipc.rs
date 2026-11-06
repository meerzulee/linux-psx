// SPDX-License-Identifier: MIT
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::Duration;

pub const LIMIT: usize = 1024 * 1024;

// Deliberately no Debug implementation: connection parameters may contain a password.
pub struct Request {
    pub id: u64,
    pub action: String,
    pub params: Value,
}

pub struct Reply {
    pub action: String,
    pub result: Result<Value, String>,
}

pub fn decode(bytes: &[u8]) -> Result<Value, String> {
    if bytes.len() > LIMIT {
        return Err("Controller response exceeds 1 MiB".into());
    }
    let mut response: Value =
        serde_json::from_slice(bytes).map_err(|_| "Controller returned invalid JSON".to_owned())?;
    match response.get("ok").and_then(Value::as_bool) {
        Some(true) => Ok(response
            .get_mut("data")
            .map(Value::take)
            .unwrap_or(Value::Null)),
        Some(false) => Err(response
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("Controller rejected this action")
            .chars()
            .take(600)
            .collect()),
        None => Err("Controller response has no result status".into()),
    }
}

fn exchange(socket: &Path, request: &Request) -> Result<Value, String> {
    let mut stream = UnixStream::connect(socket)
        .map_err(|_| "Diagnostic controller is not available yet".to_owned())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(
            if request.action == "ssh.github.preview" {
                20
            } else {
                8
            },
        )))
        .map_err(|_| "Cannot set IPC timeout")?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| "Cannot set IPC timeout")?;
    let mut line = serde_json::to_vec(
        &json!({"id": request.id, "action": request.action, "params": request.params}),
    )
    .map_err(|_| "Cannot encode controller request")?;
    if line.len() > LIMIT {
        return Err("Request exceeds 1 MiB".into());
    }
    line.push(b'\n');
    stream
        .write_all(&line)
        .map_err(|_| "Could not contact the diagnostic controller")?;
    // Take bounds allocation even if a peer never sends a newline.
    let mut reader = BufReader::new(stream);
    let mut response = Vec::new();
    let mut part = [0_u8; 8192];
    loop {
        let available = reader
            .fill_buf()
            .map_err(|_| "Controller response timed out")?;
        if available.is_empty() {
            return Err("Controller closed the connection before replying".into());
        }
        let end = available
            .iter()
            .position(|c| *c == b'\n')
            .map(|i| i + 1)
            .unwrap_or(available.len());
        let count = end.min(part.len());
        part[..count].copy_from_slice(&available[..count]);
        let complete = part[..count].contains(&b'\n');
        reader.consume(count);
        if response.len() + count > LIMIT {
            return Err("Controller response exceeds 1 MiB".into());
        }
        response.extend_from_slice(&part[..count]);
        if complete {
            break;
        }
    }
    let result = decode(&response);
    if request.action == "ssh.password.enable" {
        response.fill(0);
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    }
    result
}

pub fn worker(socket: std::path::PathBuf) -> (Sender<Request>, Receiver<Reply>) {
    let (sender, requests) = mpsc::channel::<Request>();
    let (normal, normal_requests) = mpsc::channel::<Request>();
    let (power, power_requests) = mpsc::channel::<Request>();
    let (responses, receiver) = mpsc::channel();
    for queue in [normal_requests, power_requests] {
        let socket = socket.clone();
        let responses = responses.clone();
        thread::spawn(move || {
            for request in queue {
                let result = exchange(&socket, &request);
                if responses
                    .send(Reply {
                        action: request.action,
                        result,
                    })
                    .is_err()
                {
                    break;
                }
            }
        });
    }
    // Keep confirmed Power independent of a slow state/report/SSH exchange.
    thread::spawn(move || {
        for request in requests {
            let queue = if request.action == "power.execute" {
                &power
            } else {
                &normal
            };
            if queue.send(request).is_err() {
                break;
            }
        }
    });
    (sender, receiver)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn power_exchange_does_not_wait_for_a_blocked_state_exchange() {
        use std::os::unix::net::UnixListener;
        let path =
            std::env::temp_dir().join(format!("psx-power-priority-{}.sock", std::process::id()));
        let listener = UnixListener::bind(&path).unwrap();
        let (ready, state_started) = mpsc::channel();
        let (release, wait) = mpsc::channel();
        let server = thread::spawn(move || {
            let (mut state, _) = listener.accept().unwrap();
            let mut line = String::new();
            BufReader::new(state.try_clone().unwrap())
                .read_line(&mut line)
                .unwrap();
            assert_eq!(
                serde_json::from_str::<Value>(&line).unwrap()["action"],
                "state"
            );
            ready.send(()).unwrap();
            let blocked = thread::spawn(move || {
                wait.recv_timeout(Duration::from_secs(4)).unwrap();
                state.write_all(b"{\"ok\":true,\"data\":{}}\n").unwrap();
            });
            let (mut power, _) = listener.accept().unwrap();
            line.clear();
            BufReader::new(power.try_clone().unwrap())
                .read_line(&mut line)
                .unwrap();
            assert_eq!(
                serde_json::from_str::<Value>(&line).unwrap()["action"],
                "power.execute"
            );
            power
                .write_all(b"{\"ok\":true,\"data\":{\"id\":\"power\"}}\n")
                .unwrap();
            blocked.join().unwrap();
        });
        let (sender, responses) = worker(path.clone());
        sender
            .send(Request {
                id: 1,
                action: "state".into(),
                params: json!({}),
            })
            .unwrap();
        state_started.recv_timeout(Duration::from_secs(2)).unwrap();
        sender
            .send(Request {
                id: 2,
                action: "power.execute".into(),
                params: json!({"operation":"shutdown","confirm":true}),
            })
            .unwrap();
        let result = responses.recv_timeout(Duration::from_secs(2));
        release.send(()).unwrap();
        server.join().unwrap();
        std::fs::remove_file(path).unwrap();
        assert_eq!(result.unwrap().action, "power.execute");
    }

    #[test]
    fn errors_and_oversized_responses_never_become_success() {
        assert!(decode(br#"{"ok":false,"error":"not connected"}"#).is_err());
        assert!(decode(br#"{"data":{}}"#).is_err());
        assert!(decode(br#"not-json"#).is_err());
        assert!(decode(&vec![b' '; LIMIT + 1]).is_err());
        assert_eq!(
            decode(br#"{"ok":true,"data":{"enabled":false}}"#).unwrap()["enabled"],
            false
        );
    }
    #[test]
    fn worker_exchanges_one_bounded_newline_request_over_a_fixture_socket() {
        use std::os::unix::net::UnixListener;
        let suffix = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::path::PathBuf::from(format!(
            "/var/tmp/psx-tui-{}-{suffix}.sock",
            std::process::id()
        ));
        struct Cleanup(std::path::PathBuf);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = std::fs::remove_file(&self.0);
            }
        }
        let _cleanup = Cleanup(path.clone());
        let listener = UnixListener::bind(&path).unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut line = String::new();
            BufReader::new(stream.try_clone().unwrap())
                .read_line(&mut line)
                .unwrap();
            let request: Value = serde_json::from_str(&line).unwrap();
            assert_eq!(request["id"], 7);
            assert_eq!(request["action"], "state");
            stream
                .write_all(b"{\"ok\":true,\"data\":{\"hostname\":\"fixture\"}}\n")
                .unwrap();
        });
        let (sender, receiver) = worker(path);
        sender
            .send(Request {
                id: 7,
                action: "state".into(),
                params: json!({}),
            })
            .unwrap_or_else(|_| panic!("worker stopped"));
        let response = receiver.recv_timeout(Duration::from_secs(3)).unwrap();
        assert_eq!(response.action, "state");
        assert_eq!(response.result.unwrap()["hostname"], "fixture");
        drop(sender);
        server.join().unwrap();
    }
}
