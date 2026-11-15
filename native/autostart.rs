use std::{env, path::Path};

use anyhow::{Context, Result};

const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const VALUE: &str = "SuperBay";

pub fn enabled() -> bool {
    windows_registry::CURRENT_USER
        .open(RUN_KEY)
        .and_then(|key| key.get_string(VALUE))
        .is_ok_and(|command| !command.trim().is_empty())
}

pub fn apply(enabled: bool) -> Result<()> {
    let key = windows_registry::CURRENT_USER
        .create(RUN_KEY)
        .context("无法打开 Windows 启动项")?;
    if enabled {
        let executable = env::current_exe().context("无法定位 SuperBay 程序路径")?;
        key.set_string(VALUE, command(&executable))
            .context("无法写入开机启动项")?;
    } else if key.get_string(VALUE).is_ok() {
        key.remove_value(VALUE).context("无法移除开机启动项")?;
    }
    Ok(())
}

fn command(executable: &Path) -> String {
    format!("\"{}\" --minimized", executable.display())
}

#[cfg(test)]
mod tests {
    use super::command;
    use std::path::Path;

    #[test]
    fn startup_command_quotes_the_executable() {
        assert_eq!(
            command(Path::new(r"D:\Tools\SuperBay\superbay.exe")),
            r#""D:\Tools\SuperBay\superbay.exe" --minimized"#
        );
    }
}
