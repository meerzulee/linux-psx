// SPDX-License-Identifier: MIT
//! Hands the finished report to a phone or computer on the same network. It
//! starts only when asked, and serves one file at one unguessable address.
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::Duration;

const FIRST_PORT: u16 = 8080;
const LAST_PORT: u16 = 8089;
const ALPHABET: &[u8] = b"abcdefghjkmnpqrstuvwxyz23456789";

pub struct Server {
    pub link: String,
    pub fetched: Arc<AtomicBool>,
}

fn token() -> io::Result<String> {
    let mut bytes = [0_u8; 10];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|byte| ALPHABET[*byte as usize % ALPHABET.len()] as char).collect())
}

fn respond(stream: &mut TcpStream, path: &str, file: &Path, fetched: &AtomicBool) -> io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(60)))?;
    let mut request = Vec::new();
    let mut part = [0_u8; 512];
    while !request.windows(4).any(|window| window == b"\r\n\r\n") && request.len() < 8192 {
        let count = stream.read(&mut part)?;
        if count == 0 {
            break;
        }
        request.extend_from_slice(&part[..count]);
    }
    let text = String::from_utf8_lossy(&request);
    let wanted = text.lines().next().unwrap_or_default().split(' ').nth(1).unwrap_or_default();
    let data = if wanted == path { fs::read(file).ok() } else { None };
    let Some(data) = data else {
        return stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    };
    let header = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\nContent-Disposition: attachment; filename=\"diagnostix-report.zip\"\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        data.len()
    );
    stream.write_all(header.as_bytes())?;
    stream.write_all(&data)?;
    fetched.store(true, Ordering::Relaxed);
    Ok(())
}

pub fn start(address: &str, file: PathBuf) -> io::Result<Server> {
    let mut bound = None;
    for port in FIRST_PORT..=LAST_PORT {
        if let Ok(listener) = TcpListener::bind((address, port)) {
            bound = Some((listener, port));
            break;
        }
    }
    let Some((listener, port)) = bound else {
        return Err(io::Error::new(io::ErrorKind::AddrInUse, "no free port"));
    };
    let token = token()?;
    let link = format!("http://{address}:{port}/{token}");
    let fetched = Arc::new(AtomicBool::new(false));
    let (path, seen) = (format!("/{token}"), fetched.clone());
    thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let mut stream = stream;
            let _ = respond(&mut stream, &path, &file, &seen);
        }
    });
    Ok(Server { link, fetched })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn get(address: &str, path: &str) -> String {
        let mut stream = TcpStream::connect(address).unwrap();
        write!(stream, "GET {path} HTTP/1.1\r\nHost: x\r\n\r\n").unwrap();
        let mut reply = String::new();
        stream.read_to_string(&mut reply).unwrap();
        reply
    }

    #[test]
    fn serves_only_the_report_at_its_own_address() {
        let file = std::env::temp_dir().join(format!("dx-serve-{}.zip", std::process::id()));
        fs::write(&file, "PKreport").unwrap();
        let server = start("127.0.0.1", file.clone()).unwrap();
        let address = server.link.trim_start_matches("http://").split('/').next().unwrap().to_owned();
        let path = format!("/{}", server.link.rsplit('/').next().unwrap());

        let reply = get(&address, "/");
        assert!(reply.starts_with("HTTP/1.1 404"), "{reply}");
        let reply = get(&address, "/favicon.ico");
        assert!(reply.starts_with("HTTP/1.1 404"), "{reply}");
        assert!(!server.fetched.load(Ordering::Relaxed));

        let reply = get(&address, &path);
        assert!(reply.starts_with("HTTP/1.1 200"), "{reply}");
        assert!(reply.ends_with("PKreport"));
        assert!(reply.contains("attachment"));
        assert!(server.fetched.load(Ordering::Relaxed));
        let _ = fs::remove_file(file);
    }

    #[test]
    fn address_is_long_enough_not_to_be_guessed() {
        assert_eq!(token().unwrap().len(), 10);
    }
}
