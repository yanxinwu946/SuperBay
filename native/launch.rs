use anyhow::{Context, Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::Value;
use std::{
    env,
    ffi::{OsStr, OsString},
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    os::windows::{ffi::OsStrExt, process::CommandExt},
    path::{Path, PathBuf},
    process::Command,
};
use windows::{
    Win32::UI::{Shell::ShellExecuteW, WindowsAndMessaging::SW_SHOWNORMAL},
    core::PCWSTR,
};

use crate::catalog::Entry;

fn wide(value: impl AsRef<OsStr>) -> Result<Vec<u16>> {
    let mut value: Vec<u16> = value.as_ref().encode_wide().collect();
    if value.contains(&0) {
        bail!("启动参数包含无效字符");
    }
    value.push(0);
    Ok(value)
}

fn shell_execute(target: &OsStr, args: &str, cwd: &Path, admin: bool) -> Result<()> {
    let target = wide(target)?;
    let args = wide(args)?;
    let cwd = wide(cwd)?;
    let verb = wide(if admin { "runas" } else { "open" })?;
    // ShellExecuteW borrows these NUL-terminated buffers for this call only.
    let code = unsafe {
        ShellExecuteW(
            None,
            PCWSTR(verb.as_ptr()),
            PCWSTR(target.as_ptr()),
            PCWSTR(args.as_ptr()),
            PCWSTR(cwd.as_ptr()),
            SW_SHOWNORMAL,
        )
    }
    .0 as isize;
    if code <= 32 {
        bail!("Windows 无法启动这个入口（错误码 {code}）");
    }
    Ok(())
}

fn extension(path: &Path) -> String {
    path.extension()
        .unwrap_or_default()
        .to_string_lossy()
        .to_ascii_lowercase()
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Subsystem {
    Gui,
    Console,
}

fn subsystem(path: &Path) -> Option<Subsystem> {
    let mut file = File::open(path).ok()?;
    let mut dos = [0u8; 64];
    file.read_exact(&mut dos).ok()?;
    if &dos[..2] != b"MZ" {
        return None;
    }
    let offset = u32::from_le_bytes(dos[60..64].try_into().ok()?) as u64;
    if offset < 64 {
        return None;
    }
    file.seek(SeekFrom::Start(offset)).ok()?;
    let mut pe = [0u8; 94];
    file.read_exact(&mut pe).ok()?;
    if &pe[..4] != b"PE\0\0"
        || u16::from_le_bytes([pe[20], pe[21]]) < 70
        || !matches!(u16::from_le_bytes([pe[24], pe[25]]), 0x10b | 0x20b)
    {
        return None;
    }
    match u16::from_le_bytes([pe[92], pe[93]]) {
        2 => Some(Subsystem::Gui),
        3 => Some(Subsystem::Console),
        _ => None,
    }
}

fn resolve_program(name: &OsStr, cwd: &Path, search: &OsStr) -> Option<PathBuf> {
    let mut path = PathBuf::from(name);
    if path.extension().is_none() {
        path.set_extension("exe");
    }
    if path.is_absolute() {
        return path.is_file().then_some(path);
    }
    std::iter::once(cwd.join(&path))
        .chain(env::split_paths(search).map(|folder| folder.join(&path)))
        .chain(
            env::var_os("SystemRoot").map(|root| PathBuf::from(root).join("System32").join(&path)),
        )
        .find(|path| path.is_file())
}

fn direct_command<'a>(command: &'a str, shell: &str) -> Option<(&'a str, &'a str)> {
    let mut text = command.trim();
    if shell == "powershell"
        && let Some(rest) = text.strip_prefix("& ")
    {
        text = rest.trim_start();
    }
    let mut quoted = None;
    for ch in text.chars() {
        match ch {
            '\r' | '\n' | '$' | '%' | '\x60' => return None,
            '"' | '\'' if quoted == Some(ch) => quoted = None,
            '"' | '\'' if quoted.is_none() => quoted = Some(ch),
            '&' | '|' | '<' | '>' | ';' | '^' if quoted.is_none() => return None,
            _ => {}
        }
    }
    if quoted.is_some() || text.is_empty() {
        return None;
    }
    if let Some(rest) = text.strip_prefix('"').or_else(|| {
        (shell == "powershell")
            .then(|| text.strip_prefix('\''))
            .flatten()
    }) {
        let quote = text.chars().next()?;
        let (name, tail) = rest.split_once(quote)?;
        if !tail.is_empty() && !tail.starts_with(char::is_whitespace) {
            return None;
        }
        return Some((name, tail.trim_start()));
    }
    let end = text.find(char::is_whitespace).unwrap_or(text.len());
    Some((&text[..end], text[end..].trim_start()))
}

pub struct Backend {
    pub child: std::process::Child,
    pub log: PathBuf,
}

pub fn start_backend(entry: &Entry, args: &str, logs: &Path) -> Result<Backend> {
    let cwd = if entry.directory {
        entry.path.as_path()
    } else {
        entry.path.parent().context("无法定位工作目录")?
    };
    let runtime = Runtime::read(&entry.metadata.runtime)?;
    let search = if let Some(runtime) = &runtime {
        runtime.search_path()?
    } else {
        env::var_os("PATH").unwrap_or_default()
    };
    let (interpreter, parameters) = if let Some(command) = entry
        .metadata
        .command
        .as_deref()
        .filter(|value| !value.trim().is_empty())
    {
        let (program, parameters) = direct_command(command, &entry.metadata.shell)
            .context("后台运行需要直接调用 Python，例如 python -m uvicorn app:app")?;
        let name = Path::new(program)
            .file_stem()
            .and_then(OsStr::to_str)
            .unwrap_or_default()
            .to_ascii_lowercase();
        if !matches!(name.as_str(), "python" | "python3" | "pythonw" | "py") {
            bail!("后台运行需要 Python 解释器");
        }
        (
            resolve_program(OsStr::new(program), cwd, &search)
                .context("找不到配置的 Python 解释器")?,
            append_args(parameters, args),
        )
    } else {
        let candidate = if entry.directory {
            let scripts: Vec<_> = candidates(&entry.path)
                .into_iter()
                .filter(|path| extension(path) == "py")
                .collect();
            preferred_python(&scripts)
                .context("请配置 Python 启动命令，或在目录中保留一个明确的 Python 入口")?
        } else {
            entry.path.clone()
        };
        if extension(&candidate) != "py" || !candidate.is_file() {
            bail!("请选择 Python 脚本");
        }
        let local = cwd.join(".venv/Scripts/python.exe");
        let python = if runtime.is_none() && local.is_file() {
            local
        } else {
            resolve_program(OsStr::new("python.exe"), cwd, &search)
                .context("找不到 Python 解释器")?
        };
        (python, append_args(&quote(&candidate), args))
    };
    fs::create_dir_all(logs)?;
    let temporary = tempfile::Builder::new()
        .prefix("python-")
        .suffix(".log")
        .tempfile_in(logs)?;
    let stdout = temporary.reopen()?;
    let stderr = stdout.try_clone()?;
    let mut command = Command::new(interpreter);
    command
        .raw_arg(parameters)
        .current_dir(cwd)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .creation_flags(0x08000000)
        .stdin(std::process::Stdio::null())
        .stdout(stdout)
        .stderr(stderr);
    if let Some(runtime) = runtime {
        runtime.configure(&mut command)?;
    }
    let (file, log) = temporary.keep()?;
    drop(file);
    match command.spawn() {
        Ok(child) => Ok(Backend { child, log }),
        Err(error) => {
            let _ = fs::remove_file(&log);
            Err(error).context("无法启动 Python 后端")
        }
    }
}

fn preferred_python(paths: &[PathBuf]) -> Option<PathBuf> {
    if paths.len() == 1 {
        return paths.first().cloned();
    }
    for name in ["main.py", "app.py", "server.py", "cli.py"] {
        if let Some(path) = paths
            .iter()
            .find(|path| crate::catalog::file_name(path).eq_ignore_ascii_case(name))
        {
            return Some(path.clone());
        }
    }
    None
}

pub fn open_path(path: &Path) -> Result<()> {
    let cwd = path.parent().context("无法定位工作目录")?;
    shell_execute(path.as_os_str(), "", cwd, false)
}

pub fn candidates(path: &Path) -> Vec<PathBuf> {
    let mut paths: Vec<_> = fs::read_dir(path)
        .into_iter()
        .flatten()
        .flatten()
        .map(|item| item.path())
        .filter(|item| {
            let name = crate::catalog::file_name(item).to_lowercase();
            matches!(
                extension(item).as_str(),
                "exe" | "lnk" | "cmd" | "bat" | "ps1" | "py" | "pyw" | "jar" | "js" | "cjs" | "mjs"
            ) && !["uninstall", "unins", "卸载", "setup", "update"]
                .iter()
                .any(|part| name.contains(part))
                && item.is_file()
        })
        .collect();
    paths.sort_unstable();
    paths
}

fn preferred(path: &Path, candidates: &[PathBuf]) -> Option<PathBuf> {
    if candidates.len() == 1 {
        return candidates.first().cloned();
    }
    let name = crate::catalog::file_name(path);
    let application = candidates
        .iter()
        .find(|item| {
            matches!(extension(item).as_str(), "exe" | "lnk")
                && item
                    .file_stem()
                    .is_some_and(|stem| stem.to_string_lossy().eq_ignore_ascii_case(&name))
        })
        .cloned();
    if application.is_some() {
        return application;
    }
    if candidates.iter().all(|path| extension(path) == "py") {
        return preferred_python(candidates);
    }
    None
}

pub struct EntryPreview {
    pub choices: Vec<PathBuf>,
    pub automatic: Option<PathBuf>,
}

pub fn inspect_entry(path: &Path, directory: bool) -> EntryPreview {
    if !directory {
        return EntryPreview {
            choices: Vec::new(),
            automatic: Some(path.into()),
        };
    }
    let choices = candidates(path);
    let automatic = preferred(path, &choices);
    EntryPreview { choices, automatic }
}

pub fn entry_mode(path: &Path) -> &'static str {
    match extension(path).as_str() {
        "exe" => match subsystem(path) {
            Some(Subsystem::Gui) => "GUI 程序",
            Some(Subsystem::Console) => "CLI 程序",
            None => "Windows 程序",
        },
        "lnk" | "url" => "快捷方式",
        "py" | "pyw" => "Python",
        "jar" => "Java",
        "js" | "mjs" | "cjs" => "Node.js",
        "bat" | "cmd" => "命令提示符",
        "ps1" => "PowerShell",
        _ => "系统默认方式",
    }
}

fn quote(path: &Path) -> String {
    // Windows paths cannot contain quotes. File arguments do not end in a backslash.
    format!(r#""{}""#, path.display())
}

fn console_command(target: &OsStr, args: &str) -> String {
    let target = Path::new(target);
    if target.is_absolute() {
        format!("{} {args}", quote(target))
    } else {
        format!("{} {args}", target.to_string_lossy())
    }
}

fn keep_open_arguments(target: &OsStr, args: &str) -> String {
    format!("/d /s /k \"{}\"", console_command(target, args))
}

fn append_args(command: &str, args: &str) -> String {
    if args.trim().is_empty() {
        command.to_owned()
    } else {
        format!("{command} {}", args.trim())
    }
}

pub struct Request<'a> {
    pub args: &'a str,
    pub elevate: bool,
    pub chosen: Option<&'a Path>,
    pub plan: Option<(&'a str, &'a str, bool)>,
}

pub fn run(entry: &Entry, request: Request<'_>, terminal: (&str, &str)) -> Result<()> {
    if !entry.path.exists() {
        bail!("路径不存在：{}", entry.path.display());
    }
    let cwd = if entry.directory {
        &entry.path
    } else {
        entry.path.parent().context("无法定位工作目录")?
    };
    let (command, shell, admin) = request.plan.map_or(
        (
            entry.metadata.command.as_deref(),
            entry.metadata.shell.as_str(),
            entry.metadata.admin,
        ),
        |(command, shell, admin)| (Some(command), shell, admin),
    );
    let admin = request.elevate || admin;
    let args = request.args;
    let runtime = Runtime::read(&entry.metadata.runtime)?;
    let invoke = |target: &OsStr, args: &str, console: bool, keep_open: bool| -> Result<()> {
        if console && !terminal.0.trim().is_empty() && !admin {
            let command = match &runtime {
                Some(runtime) => format!("powershell.exe {}", runtime.encoded(target, args, cwd)?),
                None if Path::new(target).is_absolute() => {
                    format!("{} {args}", quote(Path::new(target)))
                }
                None => format!("{} {args}", target.to_string_lossy()),
            };
            let command = if keep_open {
                format!("cmd.exe /d /s /k \"{command}\"")
            } else {
                command
            };
            return open_terminal(terminal, cwd, &command);
        }
        if let Some(runtime) = &runtime {
            return runtime.launch(target, args, cwd, admin, keep_open);
        }
        if console && keep_open {
            return shell_execute(
                OsStr::new("cmd.exe"),
                &keep_open_arguments(target, args),
                cwd,
                admin,
            );
        }
        shell_execute(target, args, cwd, admin)
    };
    if request.chosen.is_none()
        && let Some(command) = command.filter(|command| !command.trim().is_empty())
    {
        let shortcut = Path::new(command.trim().trim_matches('"'));
        let shortcut = cwd.join(shortcut);
        if extension(&shortcut) == "lnk" && shortcut.is_file() {
            return shell_execute(shortcut.as_os_str(), args, cwd, admin);
        }
        let command = append_args(command, args);
        if let Some((program, parameters)) = direct_command(&command, shell) {
            let search = if let Some(runtime) = &runtime {
                runtime.search_path()?
            } else {
                env::var_os("PATH").unwrap_or_default()
            };
            if let Some(program) = resolve_program(OsStr::new(program), cwd, &search) {
                match subsystem(&program) {
                    Some(Subsystem::Gui) => {
                        return invoke(program.as_os_str(), parameters, false, false);
                    }
                    Some(Subsystem::Console) if !terminal.0.trim().is_empty() && !admin => {
                        return invoke(program.as_os_str(), parameters, true, true);
                    }
                    _ => {}
                }
            }
        }
        let (shell, params) = if shell == "powershell" {
            (
                "powershell.exe",
                format!("-NoLogo -NoProfile -NoExit -Command {command}"),
            )
        } else {
            ("cmd.exe", format!("/d /s /k \"{command}\""))
        };
        return invoke(OsStr::new(shell), &params, true, false);
    }
    let target = request.chosen.map(Path::to_owned).or_else(|| {
        if entry.directory {
            preferred(&entry.path, &candidates(&entry.path))
        } else {
            Some(entry.path.clone())
        }
    });
    let Some(target) = target else {
        return shell_execute(entry.path.as_os_str(), "", cwd, false);
    };
    let ext = extension(&target);
    let quoted = quote(&target);
    let local_python = cwd.join(".venv").join("Scripts").join("python.exe");
    match ext.as_str() {
        "py" | "pyw" => invoke(
            if ext == "pyw" && runtime.is_none() {
                OsStr::new("pythonw.exe")
            } else if runtime.is_none() && local_python.is_file() {
                local_python.as_os_str()
            } else {
                OsStr::new("python.exe")
            },
            &append_args(&format!("-u {quoted}"), args),
            ext == "py",
            ext == "py",
        ),
        "jar" => invoke(
            OsStr::new("java.exe"),
            &append_args(&format!("-jar {quoted}"), args),
            true,
            true,
        ),
        "js" | "cjs" | "mjs" => invoke(
            OsStr::new("node.exe"),
            &append_args(&quoted, args),
            true,
            true,
        ),
        "ps1" => invoke(
            OsStr::new("powershell.exe"),
            &append_args(&format!("-NoLogo -NoProfile -NoExit -File {quoted}"), args),
            true,
            false,
        ),
        "bat" | "cmd" => invoke(
            OsStr::new("cmd.exe"),
            &format!("/d /s /k \"{}\"", append_args(&quoted, args)),
            true,
            false,
        ),
        "exe" => {
            let console = subsystem(&target) == Some(Subsystem::Console);
            invoke(target.as_os_str(), args, console, console)
        }
        _ => shell_execute(target.as_os_str(), args, cwd, admin),
    }
}

pub const FAMILIES: [&str; 3] = ["Python", "Java", "Node"];

struct Runtime {
    prefixes: Vec<PathBuf>,
    java_home: Option<PathBuf>,
    reset_python: bool,
}

pub fn validate_runtime(value: &Value) -> Result<()> {
    Runtime::read(value).map(|_| ())
}

impl Runtime {
    fn read(value: &Value) -> Result<Option<Self>> {
        if value.is_null() || value.as_object().is_some_and(|value| value.is_empty()) {
            return Ok(None);
        }
        let versions = value["versions"]
            .as_object()
            .filter(|versions| !versions.is_empty())
            .context("运行环境缺少版本配置")?;
        let root = PathBuf::from(value["root"].as_str().context("运行环境缺少 Scoop 目录")?);
        if !root.join("apps/scoop/current/bin/scoop.ps1").is_file() {
            bail!("Scoop 目录不可用：{}", root.display());
        }
        let mut runtime = Self {
            prefixes: Vec::new(),
            java_home: None,
            reset_python: false,
        };
        for (family, reference) in versions {
            let package = runtime_segment(&reference["package"])?;
            let version = runtime_segment(&reference["version"])?;
            let directory = root.join("apps").join(package).join(version);
            let relative = match family.as_str() {
                "Java" => "bin/java.exe",
                "Python" => "python.exe",
                "Node" => "node.exe",
                _ => bail!("无法识别运行环境 {family}"),
            };
            let binary = directory.join(relative);
            if !binary.is_file()
                || !["manifest.json", "scoop-manifest.json"]
                    .iter()
                    .any(|name| directory.join(name).is_file())
            {
                bail!("{family} 的绑定版本不可用：{package}/{version}");
            }
            runtime.prefixes.push(
                binary
                    .parent()
                    .expect("runtime has a parent directory")
                    .into(),
            );
            if family == "Java" {
                runtime.java_home = Some(directory);
            } else if family == "Python" {
                runtime.reset_python = true;
                runtime.prefixes.push(directory.join("Scripts"));
            }
        }
        Ok(Some(runtime))
    }

    fn search_path(&self) -> Result<OsString> {
        let inherited = env::var_os("PATH").unwrap_or_default();
        Ok(env::join_paths(
            self.prefixes
                .iter()
                .cloned()
                .chain(env::split_paths(&inherited)),
        )?)
    }

    fn configure(&self, command: &mut Command) -> Result<()> {
        command.env("PATH", self.search_path()?);
        if let Some(java) = &self.java_home {
            command.env("JAVA_HOME", java);
        }
        if self.reset_python {
            command.env_remove("PYTHONHOME").env_remove("PYTHONPATH");
        }
        Ok(())
    }

    fn resolve(&self, target: &OsStr) -> Result<PathBuf> {
        let path = Path::new(target);
        if path.is_absolute() {
            return Ok(path.to_owned());
        }
        env::split_paths(&self.search_path()?)
            .map(|folder| folder.join(path))
            .chain(
                env::var_os("SystemRoot")
                    .map(|root| PathBuf::from(root).join("System32").join(path)),
            )
            .find(|path| path.is_file())
            .with_context(|| format!("无法找到 {}", target.to_string_lossy()))
    }

    fn launch(
        &self,
        target: &OsStr,
        args: &str,
        cwd: &Path,
        admin: bool,
        keep_open: bool,
    ) -> Result<()> {
        let console = subsystem(Path::new(target)) == Some(Subsystem::Console);
        if admin {
            let encoded = self.encoded(target, args, cwd)?;
            let encoded = if console && keep_open {
                format!("-NoExit {encoded}")
            } else {
                encoded
            };
            return shell_execute(OsStr::new("powershell.exe"), &encoded, cwd, true);
        }
        const CREATE_NEW_CONSOLE: u32 = 0x10;
        let resolved = self.resolve(target)?;
        let (program, arguments) = if console && keep_open {
            (
                OsString::from("cmd.exe"),
                keep_open_arguments(resolved.as_os_str(), args),
            )
        } else {
            (resolved.into_os_string(), args.to_owned())
        };
        let mut command = Command::new(&program);
        command
            .raw_arg(&arguments)
            .current_dir(cwd)
            .creation_flags(if console { CREATE_NEW_CONSOLE } else { 0 });
        self.configure(&mut command)?;
        command.spawn().context("无法在绑定的环境中启动")?;
        Ok(())
    }

    fn encoded(&self, target: &OsStr, args: &str, cwd: &Path) -> Result<String> {
        let json = serde_json::json!({
            "file": self.resolve(target)?,
            "args": args,
            "cwd": cwd,
            "prefix": env::join_paths(&self.prefixes)?.to_string_lossy(),
            "java": self.java_home,
            "python": self.reset_python,
        });
        let payload = STANDARD.encode(serde_json::to_vec(&json)?);
        let script = format!(
            r#"
$ErrorActionPreference = 'Stop'
$d = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json
$p = New-Object Diagnostics.ProcessStartInfo
$p.FileName = $d.file
$p.Arguments = $d.args
$p.WorkingDirectory = $d.cwd
$p.UseShellExecute = $false
$p.EnvironmentVariables['PATH'] = "$($d.prefix);$env:PATH"
if ($d.java) {{ $p.EnvironmentVariables['JAVA_HOME'] = $d.java }}
if ($d.python) {{
    $p.EnvironmentVariables.Remove('PYTHONHOME')
    $p.EnvironmentVariables.Remove('PYTHONPATH')
}}
$child = [Diagnostics.Process]::Start($p)
$child.WaitForExit()
exit $child.ExitCode
"#
        );
        let bytes: Vec<_> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
        let result = format!(
            "-NoLogo -NoProfile -EncodedCommand {}",
            STANDARD.encode(bytes)
        );
        if result.len() > 30_000 {
            bail!("命令太长，无法交给 Windows 终端");
        }
        Ok(result)
    }
}

fn runtime_segment(value: &Value) -> Result<&str> {
    value
        .as_str()
        .filter(|value| {
            !value.is_empty()
                && !matches!(*value, "." | ".." | "current")
                && value
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._+-".contains(&b))
        })
        .context("Scoop 包名与版本必须是有效的单一级目录名")
}

pub fn terminal_here(cwd: &Path, terminal: (&str, &str)) -> Result<()> {
    if !cwd.is_dir() {
        bail!("工作目录不可用：{}", cwd.display());
    }
    if terminal.0.trim().is_empty() {
        return shell_execute(OsStr::new("cmd.exe"), "/d /k", cwd, false);
    }
    open_terminal(terminal, cwd, "cmd.exe /d /k")
}

pub fn terminal_path(terminal: &str) -> Result<PathBuf> {
    let mut target = PathBuf::from(terminal.trim().trim_matches('"'));
    if target.components().count() == 1 {
        if target.extension().is_none() {
            target.set_extension("exe");
        }
        let paths = env::var_os("PATH").unwrap_or_default();
        if let Some(resolved) = env::split_paths(&paths)
            .map(|dir| dir.join(&target))
            .find(|path| path.is_file())
        {
            target = resolved;
        }
    }
    if let Ok(shim) = fs::read_to_string(target.with_extension("shim")) {
        if let Some(path) = shim
            .lines()
            .filter_map(|line| line.split_once('='))
            .find_map(|(key, value)| {
                key.trim()
                    .eq_ignore_ascii_case("path")
                    .then(|| PathBuf::from(value.trim().trim_matches('"')))
            })
            .filter(|path| path.is_file())
        {
            target = path;
        }
    }
    let name = crate::catalog::file_name(&target).to_ascii_lowercase();
    if name == "wezterm.exe" && target.with_file_name("wezterm-gui.exe").is_file() {
        target.set_file_name("wezterm-gui.exe");
    }
    if !target.is_file() || extension(&target) != "exe" {
        bail!("找不到终端程序：{}", target.display());
    }
    Ok(target)
}

fn open_terminal((terminal, template): (&str, &str), cwd: &Path, command: &str) -> Result<()> {
    let target = terminal_path(terminal)?;
    let name = crate::catalog::file_name(&target).to_ascii_lowercase();
    let arguments = terminal_arguments(&name, template, cwd, command)?;
    shell_execute(target.as_os_str(), &arguments, cwd, false)
}

fn terminal_arguments(name: &str, template: &str, cwd: &Path, command: &str) -> Result<String> {
    Ok(match template.trim() {
        "" if name.starts_with("wezterm") => format!("start --cwd {} -- {command}", quote(cwd)),
        "" if name.starts_with("alacritty") => {
            format!("--working-directory {} -e {command}", quote(cwd))
        }
        "" if matches!(name, "wt.exe" | "wtd.exe") => {
            format!("new-tab --startingDirectory {} {command}", quote(cwd))
        }
        "" => format!("-e {command}"),
        text if text.contains("{command}") => {
            let mut result = String::new();
            for (index, section) in text.split("{command}").enumerate() {
                if index > 0 {
                    result.push_str(command);
                }
                result.push_str(&section.replace("{cwd}", &cwd.to_string_lossy()));
            }
            result
        }
        _ => bail!("终端参数必须包含 {{command}}"),
    })
}

pub fn open_location(entry: &Entry) -> Result<()> {
    if !entry.path.exists() {
        bail!("路径不存在：{}", entry.path.display());
    }
    if entry.directory {
        shell_execute(entry.path.as_os_str(), "", &entry.path, false)
    } else {
        shell_execute(
            OsStr::new("explorer.exe"),
            &format!("/select,{}", quote(&entry.path)),
            entry.path.parent().context("无法定位工作目录")?,
            false,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog;
    use std::time::{Duration, Instant};

    #[test]
    fn classifies_windows_gui_and_console_executables() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("fixture.exe");
        let mut bytes = vec![0; 64 + 94];
        bytes[..2].copy_from_slice(b"MZ");
        bytes[60..64].copy_from_slice(&64u32.to_le_bytes());
        bytes[64..68].copy_from_slice(b"PE\0\0");
        bytes[84..86].copy_from_slice(&240u16.to_le_bytes());
        bytes[88..90].copy_from_slice(&0x20bu16.to_le_bytes());
        for (value, expected) in [
            (2u16, Some(Subsystem::Gui)),
            (3, Some(Subsystem::Console)),
            (5, None),
        ] {
            bytes[156..158].copy_from_slice(&value.to_le_bytes());
            fs::write(&path, &bytes).unwrap();
            assert_eq!(subsystem(&path), expected);
        }
        fs::write(&path, &bytes[..68]).unwrap();
        assert_eq!(subsystem(&path), None);
    }

    #[test]
    fn detects_simple_launches_without_taking_over_shell_expressions() {
        assert_eq!(
            direct_command(r#""C:\Program Files\App.exe" --flag"#, "cmd"),
            Some((r"C:\Program Files\App.exe", "--flag"))
        );
        assert_eq!(
            direct_command("& 'tool.exe' --name test", "powershell"),
            Some(("tool.exe", "--name test"))
        );
        for expression in [
            "tool.exe && other.exe",
            "tool.exe | other.exe",
            "tool.exe %PATH%",
            "tool.exe > file.txt",
            "\"tool.exe\"extra",
        ] {
            assert_eq!(direct_command(expression, "cmd"), None);
        }
    }

    #[test]
    fn console_commands_quote_paths_and_keep_the_window_open() {
        let path = Path::new(r"C:\Tools\Demo Tool\tool.exe");
        assert_eq!(
            console_command(path.as_os_str(), "--flag"),
            r#""C:\Tools\Demo Tool\tool.exe" --flag"#
        );
        assert_eq!(
            keep_open_arguments(path.as_os_str(), "--flag"),
            r#"/d /s /k ""C:\Tools\Demo Tool\tool.exe" --flag""#
        );
        assert_eq!(
            keep_open_arguments(OsStr::new("python.exe"), "-u tool.py"),
            r#"/d /s /k "python.exe -u tool.py""#
        );
    }

    #[test]
    fn terminal_arguments_cover_presets_and_custom_templates() {
        let cwd = Path::new(r"C:\Work Space");
        let program = r#""C:\Work Space\tool.exe" --flag"#;
        assert_eq!(
            terminal_arguments("wezterm-gui.exe", "", cwd, program).unwrap(),
            r#"start --cwd "C:\Work Space" -- "C:\Work Space\tool.exe" --flag"#
        );
        assert_eq!(
            terminal_arguments("wt.exe", "", cwd, program).unwrap(),
            r#"new-tab --startingDirectory "C:\Work Space" "C:\Work Space\tool.exe" --flag"#
        );
        assert_eq!(
            terminal_arguments("alacritty.exe", "", cwd, "cmd.exe").unwrap(),
            r#"--working-directory "C:\Work Space" -e cmd.exe"#
        );
        assert_eq!(
            terminal_arguments(
                "custom.exe",
                r#"--cwd "{cwd}" -- {command}"#,
                cwd,
                "node.exe {cwd}"
            )
            .unwrap(),
            r#"--cwd "C:\Work Space" -- node.exe {cwd}"#
        );
        assert!(terminal_arguments("custom.exe", "{cwd}", cwd, program).is_err());
    }

    #[test]
    fn python_backend_runs_with_separate_args_working_dir_and_log() {
        let Ok(interpreter) = env::var("SUPERBAY_TEST_PYTHON") else {
            eprintln!("Set SUPERBAY_TEST_PYTHON to exercise the Python child process");
            return;
        };
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("workspace with spaces");
        fs::create_dir(&root).unwrap();
        let path = root.join("server.py");
        fs::write(&path, "import os,sys\nprint(os.getcwd())\nprint(sys.argv[1])\nprint('stderr-ok', file=sys.stderr)").unwrap();
        let mut catalog = catalog::merge(
            vec![catalog::scan_root(&root, "root".into(), 2).unwrap()],
            &temp.path().join("data.db"),
        )
        .unwrap();
        catalog.entries[0].metadata.command =
            Some(format!(r#""{interpreter}" "{}""#, path.display()));
        let mut backend = start_backend(
            &catalog.entries[0],
            r#""hello world""#,
            &temp.path().join("logs"),
        )
        .unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(status) = backend.child.try_wait().unwrap() {
                assert!(status.success());
                break;
            }
            if Instant::now() > deadline {
                let _ = backend.child.kill();
                panic!("Python demo did not terminate");
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let log = fs::read_to_string(backend.log).unwrap();
        assert!(log.contains("workspace with spaces"));
        assert!(log.contains("hello world"));
        assert!(log.contains("stderr-ok"));
    }
}
