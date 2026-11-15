use anyhow::{Context, Result, bail};
use serde_json::Value;
use std::{
    env, fs,
    io::Read,
    os::windows::process::CommandExt,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

pub fn script(root: &Path) -> PathBuf {
    root.join("apps/scoop/current/bin/scoop.ps1")
}

pub fn detect(configured: Option<&str>) -> Option<PathBuf> {
    configured
        .map(PathBuf::from)
        .into_iter()
        .chain(env::var_os("SCOOP").map(PathBuf::from))
        .chain(
            env::var_os("PATH")
                .into_iter()
                .flat_map(|paths| env::split_paths(&paths).collect::<Vec<_>>())
                .filter(|path| {
                    path.file_name()
                        .is_some_and(|name| name.eq_ignore_ascii_case("shims"))
                })
                .filter_map(|path| path.parent().map(Path::to_owned)),
        )
        .chain(env::var_os("USERPROFILE").map(|home| PathBuf::from(home).join("scoop")))
        .find(|root| script(root).is_file())
}

#[derive(Clone)]
pub struct Version {
    pub package: String,
    pub version: String,
    pub current: bool,
    pub held: bool,
    pub bucket: String,
    pub families: Vec<&'static str>,
}

pub fn inventory(root: &Path) -> Result<Vec<Version>> {
    if !script(root).is_file() {
        bail!("找不到 Scoop 安装：{}", root.display());
    }
    let mut apps = Vec::new();
    for package in fs::read_dir(root.join("apps"))? {
        let package = package?;
        let name = package.file_name().to_string_lossy().into_owned();
        if !valid_segment(&name)
            || name.eq_ignore_ascii_case("scoop")
            || !package.file_type()?.is_dir()
        {
            continue;
        }
        let current = package.path().join("current").canonicalize().ok();
        let Ok(versions) = fs::read_dir(package.path()) else {
            continue;
        };
        for version in versions.flatten() {
            let value = version.file_name().to_string_lossy().into_owned();
            if value.eq_ignore_ascii_case("current")
                || !valid_segment(&value)
                || !version.file_type().is_ok_and(|kind| kind.is_dir())
            {
                continue;
            }
            let folder = version.path();
            if !["manifest.json", "scoop-manifest.json"]
                .iter()
                .any(|name| folder.join(name).is_file())
            {
                continue;
            }
            let descriptor_path = ["scoop-install.json", "install.json"]
                .into_iter()
                .map(|name| folder.join(name))
                .find(|path| path.is_file());
            let installed_at = descriptor_path
                .as_ref()
                .and_then(|path| path.metadata().ok()?.modified().ok());
            let descriptor: Value = descriptor_path
                .and_then(|path| fs::File::open(path).ok())
                .and_then(|file| serde_json::from_reader(file.take(64 * 1024)).ok())
                .unwrap_or_default();
            let families = [
                ("Python", "python.exe"),
                ("Java", "bin/java.exe"),
                ("Node", "node.exe"),
            ]
            .into_iter()
            .filter_map(|(family, file)| folder.join(file).is_file().then_some(family))
            .collect();
            apps.push((
                Version {
                    package: name.clone(),
                    version: value,
                    current: current.is_some() && folder.canonicalize().ok() == current,
                    held: descriptor["hold"].as_bool().unwrap_or_default(),
                    bucket: descriptor["bucket"].as_str().unwrap_or_default().into(),
                    families,
                },
                installed_at,
            ));
        }
    }
    apps.sort_unstable_by(|(a, a_time), (b, b_time)| {
        a.package
            .cmp(&b.package)
            .then_with(|| b.current.cmp(&a.current))
            .then_with(|| b_time.cmp(a_time))
            .then_with(|| b.version.cmp(&a.version))
    });
    // Scoop uses the newest install record as its fallback when current has no junction.
    let mut start = 0;
    while start < apps.len() {
        apps[start].0.current = true;
        start += apps[start..].partition_point(|(item, _)| item.package == apps[start].0.package);
    }
    Ok(apps.into_iter().map(|(version, _)| version).collect())
}

fn valid_segment(value: &str) -> bool {
    !matches!(value, "" | "." | "..")
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
}

pub fn is_bucket_name(name: &str) -> bool {
    valid_segment(name)
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub enum Operation {
    Status,
    Search,
    Sync,
    Install,
    Update,
    Reset,
    Cleanup,
    Uninstall,
    Hold,
    Unhold,
}

impl Operation {
    pub const fn label(self) -> &'static str {
        match self {
            Self::Status => "检查更新",
            Self::Search => "搜索",
            Self::Sync => "同步软件源",
            Self::Install => "安装",
            Self::Update => "更新",
            Self::Reset => "切换当前版本",
            Self::Cleanup => "清理历史版本",
            Self::Uninstall => "卸载",
            Self::Hold => "冻结更新",
            Self::Unhold => "解除冻结",
        }
    }

    fn arguments(self, value: &str) -> Result<Vec<&str>> {
        let name = match self {
            Self::Status => return Ok(vec!["status", "--local"]),
            Self::Sync => return Ok(vec!["update"]),
            Self::Search => {
                if value.trim().is_empty() || value.len() > 120 || value.starts_with('-') {
                    bail!("请输入有效的搜索关键词");
                }
                return Ok(vec!["search", value]);
            }
            Self::Install => "install",
            Self::Update => "update",
            Self::Reset => "reset",
            Self::Cleanup => "cleanup",
            Self::Uninstall => "uninstall",
            Self::Hold => "hold",
            Self::Unhold => "unhold",
        };
        let valid = match self {
            Self::Install => {
                let (package, version) = value
                    .split_once('@')
                    .map_or((value, None), |(before, after)| (before, Some(after)));
                let valid_name = package.split_once('/').map_or_else(
                    || valid_segment(package),
                    |(bucket, app)| valid_segment(bucket) && valid_segment(app),
                );
                valid_name && version.is_none_or(valid_segment)
            }
            Self::Reset => value
                .split_once('@')
                .is_some_and(|(package, version)| valid_segment(package) && valid_segment(version)),
            _ => valid_segment(value),
        };
        if !valid {
            bail!("请输入有效的 Scoop 包名或版本");
        }
        Ok(vec![name, value])
    }
}

pub struct Output {
    pub success: bool,
    pub text: String,
}

#[derive(Clone, Debug)]
pub struct ObjectRow {
    pub name: String,
    pub current: String,
    pub latest: String,
    pub source: String,
    pub note: String,
}

pub fn objects(root: &Path, operation: Operation, query: &str) -> Result<(Vec<ObjectRow>, String)> {
    let args = operation.arguments(query)?;
    let (value, details) = invoke_json(root, &args, false)?;
    let rows = value
        .as_array()
        .context("Scoop 返回了无法识别的对象格式")?
        .iter()
        .filter_map(|row| {
            let field = |key| row[key].as_str().unwrap_or_default();
            let name = field("Name");
            if name.is_empty() {
                return None;
            }
            Some(ObjectRow {
                name: name.to_owned(),
                current: field("Installed Version").to_owned(),
                latest: if operation == Operation::Status {
                    field("Latest Version")
                } else {
                    field("Version")
                }
                .to_owned(),
                source: field("Source").to_owned(),
                note: [
                    field("Info"),
                    field("Missing Dependencies"),
                    field("Binaries"),
                ]
                .into_iter()
                .filter(|item| !item.is_empty())
                .collect::<Vec<_>>()
                .join(" · "),
            })
        })
        .collect();
    Ok((rows, details))
}

pub fn execute_many(
    root: &Path,
    operation: Operation,
    values: &[String],
) -> Result<(Vec<ObjectRow>, String)> {
    let mut commands = Vec::new();
    let mut rows = Vec::with_capacity(values.len());
    for (index, value) in values.iter().enumerate() {
        let name = if value.is_empty() || operation == Operation::Sync {
            operation.label()
        } else {
            value
        };
        let mut row = ObjectRow {
            name: name.into(),
            current: String::new(),
            latest: String::new(),
            source: String::new(),
            note: String::new(),
        };
        match operation.arguments(value) {
            Ok(args) => {
                row.note = "未收到执行结果".into();
                commands.push((index, args));
            }
            Err(error) => row.note = error.to_string(),
        }
        rows.push(row);
    }
    if commands.is_empty() {
        return Ok((rows, String::new()));
    }
    let batch = serde_json::to_string(&commands)?;
    let (results, details) = invoke_json(root, &["", &batch], false)?;
    for result in results.as_array().context("Scoop 没有返回软件包操作结果")? {
        let Some(index) = result["Name"]
            .as_str()
            .and_then(|value| value.parse::<usize>().ok())
        else {
            continue;
        };
        if let Some(row) = rows.get_mut(index) {
            row.note = if result["Info"] == "0" {
                "已完成"
            } else {
                "失败，查看命令输出"
            }
            .into();
        }
    }
    Ok((rows, details))
}

pub fn configured_values(root: &Path) -> Result<Value> {
    invoke_json(root, &["config"], true).map(|(value, _)| value)
}

pub fn buckets(root: &Path) -> Result<Vec<ObjectRow>> {
    let (value, _) = invoke_json(root, &["bucket", "list"], false)?;
    Ok(value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|row| {
            Some(ObjectRow {
                name: row["Name"].as_str()?.to_owned(),
                source: row["Source"].as_str().unwrap_or_default().into(),
                current: String::new(),
                latest: String::new(),
                note: [
                    row["Manifests"]
                        .as_str()
                        .map(|value| format!("{value} 个软件")),
                    row["Updated"].as_str().map(str::to_owned),
                ]
                .into_iter()
                .flatten()
                .collect::<Vec<_>>()
                .join(" · "),
            })
        })
        .collect())
}

pub const CONFIG_FIELDS: &[&str] = &[
    "proxy",
    "cache_path",
    "default_architecture",
    "aria2-enabled",
    "aria2-fallback-enabled",
    "aria2-warning-enabled",
    "aria2-split",
    "aria2-max-connection-per-server",
    "aria2-min-split-size",
    "aria2-retry-wait",
    "aria2-options",
    "use_sqlite_cache",
    "gh_proxy",
    "url_replace",
    "desktop_shortcut",
    "uninstall_shortcut",
    "scoop_repo",
    "scoop_branch",
    "use_git_history",
    "no_junction",
    "use_external_7zip",
    "use_lessmsi",
    "show_update_log",
    "show_manifest",
    "force_update",
    "autostash_on_conflict",
    "update_nightly",
    "ignore_running_processes",
    "use_isolated_path",
    "shim",
    "shim_auto_refresh",
    "debug",
    "hold_update_until",
];

pub fn set_config(root: &Path, key: &str, value: Option<&str>) -> Result<()> {
    if !CONFIG_FIELDS.contains(&key) {
        bail!("不支持的 Scoop 设置");
    }
    if key == "aria2-options"
        && let Some(value) = value
    {
        serde_json::from_str::<Vec<String>>(value).context("ARIA2 附加选项需要 JSON 字符串数组")?;
    }
    let args = if let Some(value) = value {
        vec!["config", key, value]
    } else {
        vec!["config", "rm", key]
    };
    invoke_json(root, &args, false).map(|_| ())
}

pub fn change_bucket(root: &Path, name: &str, source: Option<&str>) -> Result<()> {
    if !valid_segment(name) {
        bail!("请输入有效的 bucket 名称");
    }
    let args = match source {
        None => vec!["bucket", "rm", name],
        Some("") => vec!["bucket", "add", name],
        Some(url) if url.starts_with("https://") || url.starts_with("http://") => {
            vec!["bucket", "add", name, url]
        }
        _ => bail!("请输入 HTTP 或 HTTPS 仓库地址"),
    };
    invoke_json(root, &args, false)?;
    Ok(())
}

fn invoke_json(root: &Path, args: &[&str], config: bool) -> Result<(Value, String)> {
    let script_path = script(root);
    if !script_path.is_file() {
        bail!("Scoop 安装不可用");
    }
    let temp = tempfile::tempdir()?;
    let result_path = temp.path().join("objects.json");
    let arguments_path = temp.path().join("arguments.json");
    fs::write(&arguments_path, serde_json::to_vec(args)?)?;
    let script = r#"
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$exe = $env:SUPERBAY_SCOOP_SCRIPT
[object[]]$argv = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($env:SUPERBAY_SCOOP_ARGUMENTS))
$global:LASTEXITCODE = 0
if ($argv[0] -eq '') {
    $items = @(foreach ($item in (ConvertFrom-Json -InputObject $argv[1])) {
        [object[]]$command = $item[1]
        [Console]::WriteLine(($command -join ' '))
        $global:LASTEXITCODE = 0
        try {
            & $exe @command | Out-Host
            $resultCode = $LASTEXITCODE
        } catch {
            [Console]::WriteLine($_.Exception.Message)
            $resultCode = 1
        }
        [pscustomobject]@{ Name = $item[0]; Info = $resultCode }
    })
    $global:LASTEXITCODE = 0
} elseif ($argv.Count -eq 3 -and $argv[0] -eq 'config' -and $argv[1] -eq 'aria2-options') {
    . (Join-Path (Split-Path $exe) '../lib/core.ps1')
    [object[]]$options = ConvertFrom-Json -InputObject $argv[2]
    $items = @(set_config $argv[1] $options)
} else {
    $items = @(& $exe @argv)
}
$code = $LASTEXITCODE
if ($env:SUPERBAY_SCOOP_CONFIG -eq '1') {
    $out = @{}
    foreach ($item in $items) {
        foreach ($key in (ConvertFrom-Json $env:SUPERBAY_SCOOP_FIELDS)) {
            if ($null -ne $item.$key) { $out[$key] = $item.$key }
        }
    }
} else {
    $out = @(foreach ($item in $items) {
        if ($null -eq $item -or $null -eq $item.PSObject.Properties['Name']) {
            if ($item -is [string]) { [Console]::WriteLine($item) }
            continue
        }
        $row = @{}
        foreach ($key in @('Name', 'Installed Version', 'Latest Version', 'Version', 'Source', 'Info', 'Missing Dependencies', 'Binaries', 'Manifests', 'Updated')) {
            $value = $item.$key
            if ($null -ne $value) {
                if ($value -is [array]) { $row[$key] = $value -join ' · ' }
                elseif ($value -is [datetime]) { $row[$key] = $value.ToString('yyyy-MM-dd HH:mm') }
                else { $row[$key] = [string]$value }
            }
        }
        $row
    })
}
$json = ConvertTo-Json -InputObject $out -Depth 5 -Compress
[IO.File]::WriteAllText($env:SUPERBAY_SCOOP_RESULT, $json, [Text.UTF8Encoding]::new($false))
exit $code
"#;
    let details = tempfile::tempfile()?;
    let status = powershell(script)
        .env("SCOOP", root)
        .env("SUPERBAY_SCOOP_SCRIPT", script_path)
        .env("SUPERBAY_SCOOP_ARGUMENTS", arguments_path)
        .env("SUPERBAY_SCOOP_CONFIG", if config { "1" } else { "0" })
        .env(
            "SUPERBAY_SCOOP_FIELDS",
            serde_json::to_string(CONFIG_FIELDS)?,
        )
        .env("SUPERBAY_SCOOP_RESULT", &result_path)
        .stdin(Stdio::null())
        .stdout(details.try_clone()?)
        .stderr(details.try_clone()?)
        .creation_flags(0x08000000)
        .status()
        .context("无法读取 Scoop PowerShell 对象")?;
    let details = tail(details)?;
    let allow_empty = matches!(args, ["bucket", "list"]) && status.code() == Some(2)
        || matches!(args.first(), Some(&"search"))
            && status.code() == Some(1)
            && details.contains("No matches found.");
    if !status.success() && !allow_empty {
        bail!(
            "Scoop 命令失败（{}）{}",
            status.code().unwrap_or_default(),
            if config || args.first() == Some(&"config") {
                String::new()
            } else {
                format!("\n{details}")
            }
        );
    }
    let result = fs::read_to_string(&result_path).context("Scoop 未返回结构化结果")?;
    Ok((
        serde_json::from_str(&result).context("无法解析 Scoop 对象")?,
        details,
    ))
}

pub const INSTALLER_URL: &str = "https://raw.githubusercontent.com/xrgzs/scoop/9dd148b0f55f419ddecc7b79107fa558dd385604/bin/install.ps1";

pub struct InstallOptions {
    pub path: PathBuf,
    pub global_path: String,
    pub cache_path: String,
    pub github_proxy: String,
    pub packages: Vec<String>,
    pub slim: bool,
}

pub fn validate_install(options: &InstallOptions) -> Result<()> {
    if !options.path.is_absolute()
        || [&options.global_path, &options.cache_path]
            .iter()
            .any(|path| !path.is_empty() && !Path::new(path).is_absolute())
    {
        bail!("请使用绝对安装路径");
    }
    if detect(None).is_some() || script(&options.path).is_file() {
        bail!("检测到已安装的 Scoop，请先连接现有安装");
    }
    if !options.github_proxy.is_empty() && !options.github_proxy.starts_with("https://") {
        bail!("GitHub 代理需要使用 HTTPS 地址");
    }
    if options.slim && !options.packages.is_empty() {
        bail!("精简安装时请留空附加软件包");
    }
    for package in &options.packages {
        Operation::Install.arguments(package)?;
    }
    Ok(())
}

pub fn install(options: &InstallOptions) -> Result<Output> {
    validate_install(options)?;
    let temp = tempfile::tempdir()?;
    let installer = temp.path().join("install.ps1");
    let bootstrap = r#"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$ProgressPreference = 'SilentlyContinue'
if ([Net.ServicePointManager]::SecurityProtocol -ne [Net.SecurityProtocolType]::SystemDefault) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
}
Invoke-WebRequest -UseBasicParsing -Uri $env:SUPERBAY_SCOOP_URL -OutFile $env:SUPERBAY_SCOOP_INSTALLER -ErrorAction Stop
$opts = @{ ScoopDir = $env:SUPERBAY_SCOOP_DIR; Slim = ($env:SUPERBAY_SCOOP_SLIM -eq '1') }
if ($env:SUPERBAY_SCOOP_GLOBAL) { $opts.ScoopGlobalDir = $env:SUPERBAY_SCOOP_GLOBAL }
if ($env:SUPERBAY_SCOOP_CACHE) { $opts.ScoopCacheDir = $env:SUPERBAY_SCOOP_CACHE }
if ($env:SUPERBAY_SCOOP_PROXY) { $opts.GitHubProxy = $env:SUPERBAY_SCOOP_PROXY }
$installer = [scriptblock]::Create([IO.File]::ReadAllText($env:SUPERBAY_SCOOP_INSTALLER))
& $installer @opts
if ($env:SUPERBAY_SCOOP_PACKAGES -and (Test-Path -LiteralPath $env:SUPERBAY_SCOOP_SCRIPT)) {
    $failed = $false
    foreach ($package in ($env:SUPERBAY_SCOOP_PACKAGES -split ' ')) {
        $global:LASTEXITCODE = 0
        & $env:SUPERBAY_SCOOP_SCRIPT install $package
        if ($LASTEXITCODE -ne 0) { $failed = $true }
    }
    if ($failed) { exit 1 }
}
"#;
    let log = tempfile::tempfile()?;
    let status = powershell(bootstrap)
        .env("SUPERBAY_SCOOP_URL", INSTALLER_URL)
        .env("SUPERBAY_SCOOP_INSTALLER", installer)
        .env("SUPERBAY_SCOOP_SCRIPT", script(&options.path))
        .env("SUPERBAY_SCOOP_DIR", &options.path)
        .env("SUPERBAY_SCOOP_GLOBAL", &options.global_path)
        .env("SUPERBAY_SCOOP_CACHE", &options.cache_path)
        .env("SUPERBAY_SCOOP_PROXY", &options.github_proxy)
        .env("SUPERBAY_SCOOP_PACKAGES", options.packages.join(" "))
        .env("SUPERBAY_SCOOP_SLIM", if options.slim { "1" } else { "0" })
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log.try_clone()?)
        .creation_flags(0x08000000)
        .status()
        .context("无法运行 xrgzs/scoop 安装脚本")?;
    Ok(Output {
        success: status.success() && script(&options.path).is_file(),
        text: tail(log)?,
    })
}

fn powershell(script: &str) -> Command {
    let mut command = Command::new("powershell.exe");
    // Windows PowerShell serializes redirected host streams when using -EncodedCommand.
    command.args([
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
    ]);
    command
}

fn tail(mut reader: fs::File) -> Result<String> {
    use std::io::{Seek, SeekFrom};
    let end = reader.seek(SeekFrom::End(0))?;
    let limit = 64 * 1024;
    reader.seek(SeekFrom::Start(end.saturating_sub(limit)))?;
    let mut bytes = Vec::new();
    reader.take(limit).read_to_end(&mut bytes)?;
    let output = String::from_utf8_lossy(&bytes);
    let text = if end > limit {
        format!("…\n{output}")
    } else {
        output.into_owned()
    };
    Ok(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn operations_use_explicit_package_arguments() {
        assert_eq!(
            Operation::Install.arguments("main/python@3.13.0").unwrap(),
            ["install", "main/python@3.13.0"]
        );
        assert_eq!(
            Operation::Reset.arguments("python@3.13.0").unwrap(),
            ["reset", "python@3.13.0"]
        );
        for bad in [
            "-g",
            "../python",
            "python;whoami",
            "python@-g",
            "a/b/c",
            "https://example.com",
        ] {
            assert!(Operation::Install.arguments(bad).is_err());
        }
        assert!(Operation::Uninstall.arguments("*").is_err());
    }

    #[test]
    fn windows_powershell_keeps_arguments_and_objects_separate() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path();
        let fake = script(root);
        fs::create_dir_all(fake.parent().unwrap()).unwrap();
        fs::write(fake, r#"
switch ($args[0]) {
    'status' {
        if ($args.Count -ne 2 -or $args[1] -ne '--local') { exit 7 }
        Write-Progress -Activity 'module preparation' -Status 'working'
        Write-Host 'progress message'
        Write-Output 'diagnostic message'
        [PSCustomObject]@{ Name='python'; 'Installed Version'='3.12'; 'Latest Version'='3.13'; 'Missing Dependencies'=@('a', 'b') }
    }
    'search' {
        if ($args.Count -ne 2 -or $args[1] -ne 'python 3') { exit 7 }
        Write-Warning 'No matches found.'
        exit 1
    }
    'config' { [PSCustomObject]@{ url_replace=$false; 'aria2-options'=@('--quiet=true') } }
    'bucket' { exit 2 }
    'update' { if ($args[1] -eq 'bad') { exit 7 }; Write-Output 'done' }
    default { exit 7 }
}
"#).unwrap();
        let (rows, log) = objects(root, Operation::Status, "").unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].name, "python");
        assert_eq!(rows[0].latest, "3.13");
        assert!(rows[0].note.contains("a") && rows[0].note.contains("b"));
        assert!(log.contains("diagnostic message"));
        assert!(!log.contains("CLIXML"));
        assert!(!log.contains("module preparation"));
        assert!(
            objects(root, Operation::Search, "python 3")
                .unwrap()
                .0
                .is_empty()
        );
        assert!(buckets(root).unwrap().is_empty());
        let config = configured_values(root).unwrap();
        assert_eq!(config["url_replace"], false);
        assert_eq!(config["aria2-options"][0], "--quiet=true");
        let (rows, log) =
            execute_many(root, Operation::Update, &["bad".into(), "good".into()]).unwrap();
        assert!(rows[0].note.contains("失败"));
        assert_eq!(rows[1].note, "已完成");
        assert!(log.contains("done"));
    }

    #[test]
    fn inventory_reads_manifests_and_runtime_families() {
        let root = tempfile::tempdir().unwrap();
        let root = root.path();
        let scoop = script(root);
        fs::create_dir_all(scoop.parent().unwrap()).unwrap();
        fs::write(scoop, "").unwrap();
        let python = root.join("apps/python/3.13.0");
        fs::create_dir_all(&python).unwrap();
        fs::write(python.join("manifest.json"), "{}").unwrap();
        fs::write(python.join("install.json"), r#"{"bucket":"main"}"#).unwrap();
        fs::write(python.join("python.exe"), "").unwrap();
        let versions = inventory(root).unwrap();
        assert_eq!(versions.len(), 1);
        assert_eq!(versions[0].families, ["Python"]);
        assert_eq!(versions[0].bucket, "main");
    }
}
