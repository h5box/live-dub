// The window is only a view: the Python backend (livedub.server on 127.0.0.1:8090) runs the services and the
// session. If it is not running when the window opens, start it detached so it (and a running session) keeps going
// after the window closes. The project directory is found from the exe's location (the exe built in
// app/src-tauri/target/...) or from LIVEDUB_HOME.
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;
use tauri::Manager;

fn backend_up() -> bool {
    TcpStream::connect_timeout(&"127.0.0.1:8090".parse().unwrap(), Duration::from_millis(300)).is_ok()
}

fn is_project(d: &Path) -> bool {
    d.join("livedub").join("server").is_dir()
}

fn project_root() -> Option<PathBuf> {
    if let Some(h) = std::env::var_os("LIVEDUB_HOME").map(PathBuf::from).filter(|d| is_project(d)) {
        return Some(h);
    }
    let exe = std::env::current_exe().ok()?;
    exe.ancestors().find(|d| is_project(d)).map(|d| d.to_path_buf())
}

/// How to run Python in the project: its own .venv (made by `uv sync`), else `uv run` (uv on PATH or where its
/// installer puts it).
fn python_command(root: &Path) -> Command {
    let venv = if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };
    if venv.exists() {
        return Command::new(venv);
    }
    let exe = if cfg!(windows) { "uv.exe" } else { "uv" };
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from);
    let uv = home
        .map(|h| h.join(".local").join("bin").join(exe))
        .filter(|p| p.exists())
        .unwrap_or_else(|| PathBuf::from(exe));
    let mut c = Command::new(uv);
    c.args(["run", "python"]);
    c
}

fn start_backend(resources: &Path) {
    if backend_up() {
        return;
    }
    let bundled = resources.join("backend").join("LiveDubBackend.exe");
    let (root, mut cmd) = if bundled.is_file() {
        let root = std::env::var_os("LIVEDUB_HOME").map(PathBuf::from).unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default()).join("LiveDub")
        });
        (root, Command::new(bundled))
    } else {
        let Some(root) = project_root() else { return };
        let cmd = python_command(&root);
        (root, cmd)
    };
    let logs = root.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    if !resources.join("backend").join("LiveDubBackend.exe").is_file() {
        cmd.arg("-m");
    }
    cmd.arg("livedub.server").current_dir(&root)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(std::process::Stdio::null());
    if let Ok(f) = std::fs::File::create(logs.join("server.log")) {
        if let Ok(f2) = f.try_clone() {
            cmd.stderr(f2);
        }
        cmd.stdout(f);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        cmd.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0); // not in the window's process group: closing the window does not stop it
    }
    let _ = cmd.spawn();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            start_backend(&app.path().resource_dir()?);
            Ok(())
        })
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
