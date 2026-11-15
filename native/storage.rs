use anyhow::{Context, Result, bail};
use rusqlite::{Connection, params};
use serde_yaml_ng::Value;
use std::{
    collections::{HashMap, HashSet},
    env, fs,
    io::Write,
    path::{Path, PathBuf},
    time::Duration,
};

use crate::catalog::Metadata;

pub struct Settings {
    path: PathBuf,
    values: Value,
}

impl Settings {
    pub fn load() -> Result<Self> {
        let path = data_root()?.join("config").join("config.yaml");
        let values = match fs::read_to_string(&path) {
            Ok(text) => serde_yaml_ng::from_str(&text)
                .with_context(|| format!("无法读取设置 {}", path.display()))?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Value::Null,
            Err(error) => return Err(error.into()),
        };
        if !values.is_mapping() && !values.is_null() {
            bail!("设置文件应包含键值映射：{}", path.display());
        }
        Ok(Self { path, values })
    }

    pub fn database(&self) -> PathBuf {
        self.path.with_file_name("data.db")
    }

    pub fn backend_logs(&self) -> PathBuf {
        self.path.with_file_name("backend-logs")
    }

    pub fn set_scoop_root(&mut self, root: &Path) -> Result<()> {
        self.set("scoop_root", root.to_string_lossy().as_ref())
    }

    pub fn workspace(&self) -> Option<PathBuf> {
        if let Some(path) = self.values["workspace_root"]
            .as_str()
            .filter(|value| !value.is_empty())
        {
            return Some(PathBuf::from(path));
        }
        let home = PathBuf::from(env::var_os("USERPROFILE")?);
        std::iter::once(home.join("Tools"))
            .chain(['C', 'D', 'E', 'F'].into_iter().flat_map(|drive| {
                [
                    PathBuf::from(format!("{drive}:\\Tools")),
                    PathBuf::from(format!("{drive}:\\Toolbox")),
                ]
            }))
            .chain([home.join("Desktop")])
            .find(|path| path.is_dir())
    }

    pub fn presets(&self) -> Vec<(String, PathBuf)> {
        self.values["workspace_presets"]
            .as_sequence()
            .into_iter()
            .flatten()
            .filter_map(|record| {
                let path = PathBuf::from(record["path"].as_str().filter(|path| !path.is_empty())?);
                let name = record["name"]
                    .as_str()
                    .filter(|name| !name.is_empty())
                    .map(str::to_owned)
                    .unwrap_or_else(|| crate::catalog::file_name(&path));
                Some((name, path))
            })
            .collect()
    }

    pub fn scan_depth(&self) -> usize {
        self.values["scan_depth"]
            .as_i64()
            .or_else(|| self.values["scan_depth"].as_str()?.parse().ok())
            .unwrap_or(2)
            .clamp(1, 16) as usize
    }

    pub fn theme(&self) -> &str {
        self.values["theme"].as_str().unwrap_or("system")
    }

    pub fn terminal(&self) -> (&str, &str) {
        (
            self.values["terminal"].as_str().unwrap_or_default(),
            self.values["terminal_args"].as_str().unwrap_or_default(),
        )
    }

    pub fn scoop_root(&self) -> Option<&str> {
        self.values["scoop_root"]
            .as_str()
            .filter(|value| !value.is_empty())
    }

    pub fn set_workspace(&mut self, root: &Path) -> Result<()> {
        self.set("workspace_root", root.to_string_lossy().as_ref())
    }

    pub fn add_workspace(&mut self, root: &Path) -> Result<()> {
        let key = path_key(&root.to_string_lossy());
        let mut presets = self.presets();
        if !presets
            .iter()
            .any(|(_, existing)| path_key(&existing.to_string_lossy()) == key)
        {
            presets.push((crate::catalog::file_name(root), root.into()));
        }
        let mut next = self.values.clone();
        next["workspace_root"] = root.to_string_lossy().as_ref().into();
        next["workspace_presets"] = preset_values(&presets);
        self.save(next)
    }

    pub fn named_only(&self) -> bool {
        self.values["named_directories_only"]
            .as_bool()
            .unwrap_or_default()
    }

    pub fn set_named_only(&mut self, enabled: bool) -> Result<()> {
        let mut next = self.values.clone();
        next["named_directories_only"] = Value::Bool(enabled);
        self.save(next)
    }

    pub fn hide_empty_categories(&self) -> bool {
        self.values["hide_empty_categories"]
            .as_bool()
            .unwrap_or(false)
    }

    pub fn set_hide_empty_categories(&mut self, enabled: bool) -> Result<()> {
        let mut next = self.values.clone();
        next["hide_empty_categories"] = Value::Bool(enabled);
        self.save(next)
    }

    pub fn close_action(&self) -> &str {
        self.values["close_action"].as_str().unwrap_or("ask")
    }

    pub fn set_close_action(&mut self, action: &str) -> Result<()> {
        self.set("close_action", action)
    }

    pub fn start_minimized(&self) -> bool {
        self.values["start_minimized"].as_bool().unwrap_or(false)
    }

    pub fn set_start_minimized(&mut self, enabled: bool) -> Result<()> {
        self.set_flag("start_minimized", enabled)
    }

    pub fn hotkey(&self) -> &str {
        self.values["tray_hotkey"]
            .as_str()
            .unwrap_or(crate::hotkey::DEFAULT)
    }

    pub fn set_hotkey(&mut self, hotkey: &str) -> Result<()> {
        self.set("tray_hotkey", hotkey)
    }

    pub fn tray_hint_shown(&self) -> bool {
        self.values["tray_hint_shown"].as_bool().unwrap_or(false)
    }

    pub fn set_tray_hint_shown(&mut self, shown: bool) -> Result<()> {
        self.set_flag("tray_hint_shown", shown)
    }

    pub fn guide_seen(&self) -> bool {
        self.values["guide_seen"].as_bool().unwrap_or(false)
    }

    pub fn set_guide_seen(&mut self, seen: bool) -> Result<()> {
        self.set_flag("guide_seen", seen)
    }

    pub fn set_theme(&mut self, theme: &str) -> Result<()> {
        self.set("theme", theme)
    }

    pub fn result_sort(&self) -> &str {
        self.values["result_sort"].as_str().unwrap_or_default()
    }

    pub fn dense_results(&self) -> bool {
        self.values["result_density"].as_str() == Some("compact")
    }

    pub fn grid_results(&self) -> bool {
        self.values["view"].as_str() == Some("grid")
    }

    pub fn set_grid_results(&mut self, grid: bool) -> Result<()> {
        self.set("view", if grid { "grid" } else { "list" })
    }

    pub fn set_result_layout(&mut self, sort: &str, compact: bool) -> Result<()> {
        self.update(&[
            ("result_sort", sort),
            (
                "result_density",
                if compact { "compact" } else { "comfortable" },
            ),
        ])
    }

    pub fn set_terminal(&mut self, terminal: &str, arguments: &str) -> Result<()> {
        self.update(&[("terminal", terminal), ("terminal_args", arguments)])
    }

    pub fn set_presets(&mut self, presets: &[(String, PathBuf)]) -> Result<()> {
        let mut next = self.values.clone();
        next["workspace_presets"] = preset_values(presets);
        self.save(next)
    }

    fn set(&mut self, key: &str, value: &str) -> Result<()> {
        self.update(&[(key, value)])
    }

    fn set_flag(&mut self, key: &str, value: bool) -> Result<()> {
        let mut next = self.values.clone();
        next[key] = Value::Bool(value);
        self.save(next)
    }

    fn update(&mut self, pairs: &[(&str, &str)]) -> Result<()> {
        let mut next = self.values.clone();
        for &(key, value) in pairs {
            next[key] = Value::String(value.to_owned());
        }
        self.save(next)
    }

    fn save(&mut self, next: Value) -> Result<()> {
        atomic_write(&self.path, serde_yaml_ng::to_string(&next)?.as_bytes())?;
        self.values = next;
        Ok(())
    }
}

fn preset_values(presets: &[(String, PathBuf)]) -> Value {
    Value::Sequence(
        presets
            .iter()
            .map(|(name, path)| {
                let mut record = serde_yaml_ng::Mapping::new();
                record.insert("name".into(), name.as_str().into());
                record.insert("path".into(), path.to_string_lossy().as_ref().into());
                Value::Mapping(record)
            })
            .collect(),
    )
}

fn data_root() -> Result<PathBuf> {
    if let Some(path) = env::var_os("SUPERBAY_DATA_DIR") {
        return Ok(std::path::absolute(path)?);
    }
    let base = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .context("无法定位 Windows 本地应用数据目录")?;
    let current = base.join("SuperBay");
    let legacy = base.join("SuperGUI");
    if !current.exists() && legacy.is_dir() {
        let stage = tempfile::tempdir_in(&base)?;
        let staged = stage.path().join("data");
        copy_directory(&legacy, &staged)?;
        if let Err(error) = fs::rename(&staged, &current)
            && !current.is_dir()
        {
            return Err(error).context("无法迁移旧版 SuperGUI 数据");
        }
    }
    Ok(current)
}

fn copy_directory(source: &Path, destination: &Path) -> Result<()> {
    fs::create_dir(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        if kind.is_dir() {
            copy_directory(&entry.path(), &destination.join(entry.file_name()))?;
        } else if kind.is_file() {
            fs::copy(entry.path(), destination.join(entry.file_name()))?;
        }
    }
    Ok(())
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().context("文件缺少父目录")?;
    fs::create_dir_all(parent)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    temporary.write_all(bytes)?;
    temporary.as_file().sync_all()?;
    temporary.persist(path)?;
    Ok(())
}

pub struct Store(Connection);

impl Store {
    pub fn open(path: &Path) -> Result<Self> {
        fs::create_dir_all(path.parent().context("数据库缺少父目录")?)?;
        let mut connection = Connection::open(path)?;
        connection.busy_timeout(Duration::from_secs(3))?;
        let version: usize =
            connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
        if version < MIGRATIONS.len() {
            let transaction =
                connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let version: usize =
                transaction.pragma_query_value(None, "user_version", |row| row.get(0))?;
            for (version, sql) in MIGRATIONS.iter().enumerate().skip(version) {
                transaction.execute_batch(sql)?;
                transaction.pragma_update(None, "user_version", version + 1)?;
            }
            transaction.commit()?;
        }
        Ok(Self(connection))
    }

    pub fn metadata(&self) -> Result<HashMap<String, Metadata>> {
        let mut statement = self.0.prepare(
            "WITH paths AS (SELECT path FROM meta UNION SELECT path FROM stats)
             SELECT paths.path, COALESCE(pinned,0), COALESCE(tags,''), aliases, command,
                    COALESCE(shell_type,'cmd'), COALESCE(last_args,''), COALESCE(admin,0),
                    COALESCE(runs,0), COALESCE(last_run,''), COALESCE(recent_hidden,0), runtime_profile,
                    launch_plans, chosen_entry
             FROM paths LEFT JOIN meta USING(path) LEFT JOIN stats USING(path)",
        )?;
        let rows = statement.query_map([], |row| {
            let path: String = row.get(0)?;
            let aliases: Option<String> = row.get(3)?;
            let runtime: Option<String> = row.get(11)?;
            let plans: Option<String> = row.get(12)?;
            let chosen: Option<String> = row.get(13)?;
            Ok((
                path_key(&path),
                Metadata {
                    stored_path: Some(path.into()),
                    pinned: row.get(1)?,
                    tags: row.get(2)?,
                    aliases: aliases
                        .and_then(|value| serde_json::from_str(&value).ok())
                        .unwrap_or_default(),
                    command: row.get(4)?,
                    shell: row.get(5)?,
                    last_args: row.get(6)?,
                    admin: row.get(7)?,
                    runs: row.get(8)?,
                    last_run: row.get(9)?,
                    recent_hidden: row.get(10)?,
                    runtime: runtime
                        .and_then(|value| serde_json::from_str(&value).ok())
                        .unwrap_or_default(),
                    plans: plans
                        .and_then(|value| serde_json::from_str(&value).ok())
                        .unwrap_or_default(),
                    chosen_entry: chosen.filter(|value| !value.is_empty()).map(PathBuf::from),
                },
            ))
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    pub fn pin(&self, path: &Path, pinned: bool) -> Result<()> {
        self.0.execute(
            "INSERT INTO meta(path,pinned) VALUES(?1,?2)
            ON CONFLICT(path) DO UPDATE SET pinned=excluded.pinned",
            params![path.to_string_lossy(), pinned],
        )?;
        Ok(())
    }

    pub fn pin_many(&mut self, paths: &[PathBuf], pinned: bool) -> Result<()> {
        let transaction = self.0.transaction()?;
        {
            let mut statement = transaction.prepare_cached(
                "INSERT INTO meta(path,pinned) VALUES(?1,?2)
                ON CONFLICT(path) DO UPDATE SET pinned=excluded.pinned",
            )?;
            for path in paths {
                statement.execute(params![path.to_string_lossy(), pinned])?;
            }
        }
        transaction.commit()?;
        Ok(())
    }

    pub fn edit(
        &self,
        path: &Path,
        tags: &str,
        aliases: &[String],
        command: &str,
        shell: &str,
        admin: bool,
    ) -> Result<()> {
        self.0.execute(
            "INSERT INTO meta(path,tags,aliases,command,shell_type,admin) VALUES(?1,?2,?3,NULLIF(?4,''),?5,?6)
             ON CONFLICT(path) DO UPDATE SET tags=excluded.tags, aliases=excluded.aliases,
             command=excluded.command, shell_type=excluded.shell_type, admin=excluded.admin",
            params![path.to_string_lossy(), tags, serde_json::to_string(aliases)?, command, shell, admin],
        )?;
        Ok(())
    }

    pub fn record_run(&mut self, path: &Path, args: Option<&str>) -> Result<String> {
        let transaction = self.0.transaction()?;
        let time = transaction.query_row(
            "INSERT INTO stats(path,runs,last_run) VALUES(?1,1,datetime('now','localtime'))
             ON CONFLICT(path) DO UPDATE SET runs=runs+1,last_run=excluded.last_run,recent_hidden=0
             RETURNING last_run",
            [path.to_string_lossy()],
            |row| row.get(0),
        )?;
        if let Some(args) = args {
            transaction.execute(
                "INSERT INTO meta(path,last_args) VALUES(?1,?2)
                ON CONFLICT(path) DO UPDATE SET last_args=excluded.last_args",
                params![path.to_string_lossy(), args],
            )?;
        }
        transaction.commit()?;
        Ok(time)
    }

    pub fn hide_recent(&mut self, paths: &[PathBuf]) -> Result<usize> {
        if paths.is_empty() {
            return Ok(0);
        }
        let transaction = self.0.transaction()?;
        let mut hidden = 0;
        {
            let mut statement = transaction.prepare_cached(
                "UPDATE stats SET recent_hidden=1
                 WHERE path=?1 AND recent_hidden=0 AND last_run IS NOT NULL AND last_run <> ''",
            )?;
            for path in paths {
                hidden += statement.execute([path.to_string_lossy()])?;
            }
        }
        transaction.commit()?;
        Ok(hidden)
    }

    pub fn clear_recent(&self) -> Result<usize> {
        Ok(self.0.execute(
            "UPDATE stats SET recent_hidden=1
             WHERE recent_hidden=0 AND last_run IS NOT NULL AND last_run <> ''",
            [],
        )?)
    }

    pub fn orphan_candidates(&self, roots: &[PathBuf]) -> Result<Vec<PathBuf>> {
        let mut paths = HashSet::new();
        {
            let mut statement = self
                .0
                .prepare("SELECT path FROM meta UNION SELECT path FROM stats")?;
            let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
            for path in rows {
                paths.insert(PathBuf::from(path?));
            }
        }
        let roots: Vec<(String, bool)> = roots
            .iter()
            .map(|root| {
                let key = path_key(&root.to_string_lossy());
                (key.trim_end_matches('\\').to_owned(), root.exists())
            })
            .collect();
        Ok(paths
            .into_iter()
            .filter(|path| {
                if path.exists() {
                    return false;
                }
                let key = path_key(&path.to_string_lossy());
                roots.iter().any(|(root, exists)| {
                    *exists
                        && (key == *root
                            || key
                                .strip_prefix(root.as_str())
                                .is_some_and(|rest| rest.starts_with('\\')))
                })
            })
            .collect())
    }

    pub fn prune_orphans(&mut self, roots: &[PathBuf]) -> Result<usize> {
        let paths = self.orphan_candidates(roots)?;
        if paths.is_empty() {
            return Ok(0);
        }
        let transaction = self.0.transaction()?;
        {
            let mut meta = transaction.prepare_cached("DELETE FROM meta WHERE path=?1")?;
            let mut stats = transaction.prepare_cached("DELETE FROM stats WHERE path=?1")?;
            for path in &paths {
                meta.execute([path.to_string_lossy()])?;
                stats.execute([path.to_string_lossy()])?;
            }
        }
        transaction.commit()?;
        Ok(paths.len())
    }

    pub fn set_chosen_entry(&self, path: &Path, chosen: Option<&Path>) -> Result<()> {
        self.0.execute(
            "INSERT INTO meta(path,chosen_entry) VALUES(?1,?2)
             ON CONFLICT(path) DO UPDATE SET chosen_entry=excluded.chosen_entry",
            params![
                path.to_string_lossy(),
                chosen.map(|chosen| chosen.to_string_lossy().into_owned())
            ],
        )?;
        Ok(())
    }

    pub fn save_launch_field(&self, path: &Path, key: LaunchField, json: String) -> Result<()> {
        let sql = match key {
            LaunchField::Plans => {
                "INSERT INTO meta(path,launch_plans) VALUES(?1,?2)
                ON CONFLICT(path) DO UPDATE SET launch_plans=excluded.launch_plans"
            }
            LaunchField::Runtime => {
                "INSERT INTO meta(path,runtime_profile) VALUES(?1,?2)
                ON CONFLICT(path) DO UPDATE SET runtime_profile=excluded.runtime_profile"
            }
        };
        self.0.execute(sql, params![path.to_string_lossy(), json])?;
        Ok(())
    }
}

#[derive(Clone, Copy)]
pub enum LaunchField {
    Plans,
    Runtime,
}

pub fn path_key(path: &str) -> String {
    path.replace('/', "\\").to_lowercase()
}

const MIGRATIONS: &[&str] = &[
    "CREATE TABLE IF NOT EXISTS meta(path TEXT PRIMARY KEY,pinned INTEGER DEFAULT 0,
        tags TEXT DEFAULT '',hidden INTEGER DEFAULT 0,command TEXT,shell_type TEXT);
     CREATE TABLE IF NOT EXISTS stats(path TEXT PRIMARY KEY,runs INTEGER DEFAULT 0,last_run TEXT);",
    "ALTER TABLE meta DROP COLUMN hidden;",
    "ALTER TABLE meta ADD COLUMN last_args TEXT; ALTER TABLE meta ADD COLUMN admin INTEGER DEFAULT 0;",
    "ALTER TABLE meta ADD COLUMN runtime_profile TEXT DEFAULT '{}';",
    "ALTER TABLE meta ADD COLUMN aliases TEXT DEFAULT '[]';
     ALTER TABLE meta ADD COLUMN launch_plans TEXT DEFAULT '[]';",
    "ALTER TABLE stats ADD COLUMN recent_hidden INTEGER DEFAULT 0;",
    "ALTER TABLE meta ADD COLUMN chosen_entry TEXT;",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adding_workspaces_persists_and_deduplicates_paths() {
        let temp = tempfile::tempdir().unwrap();
        let config = temp.path().join("config.yaml");
        let mut settings = Settings {
            path: config.clone(),
            values: Value::Null,
        };
        let first = temp.path().join("Tools");
        let second = temp.path().join("Other");
        settings.add_workspace(&first).unwrap();
        settings.add_workspace(&second).unwrap();
        settings.add_workspace(&temp.path().join("tools")).unwrap();
        settings.set_named_only(true).unwrap();
        let restored = Settings {
            path: config.clone(),
            values: serde_yaml_ng::from_str(&fs::read_to_string(config).unwrap()).unwrap(),
        };
        assert_eq!(
            restored.presets(),
            [("Tools".into(), first), ("Other".into(), second)]
        );
        assert_eq!(
            restored.workspace().as_deref(),
            Some(temp.path().join("tools").as_path())
        );
        assert!(restored.named_only());
    }

    #[test]
    fn recent_entries_can_be_hidden_and_return_after_running() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(&temp.path().join("data.db")).unwrap();
        let tool = Path::new(r"D:\Tools\fscan");
        store.record_run(tool, None).unwrap();
        assert_eq!(store.hide_recent(&[tool.to_path_buf()]).unwrap(), 1);
        let key = path_key(&tool.to_string_lossy());
        assert!(
            store.metadata().unwrap()[&key].recent_hidden,
            "hidden entry should be marked in the database"
        );
        store.record_run(tool, None).unwrap();
        assert!(!store.metadata().unwrap()[&key].recent_hidden);
        store.record_run(tool, None).unwrap();
        assert_eq!(store.clear_recent().unwrap(), 1);
        assert!(store.metadata().unwrap()[&key].recent_hidden);
    }

    #[test]
    fn orphan_cleanup_only_removes_missing_paths_under_existing_roots() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("workspace");
        let existing = root.join("tool");
        fs::create_dir_all(&existing).unwrap();
        let missing = root.join("gone");
        let absent_root = temp.path().join("offline");
        let offline_tool = absent_root.join("tool");
        let mut store = Store::open(&temp.path().join("data.db")).unwrap();
        for path in [&existing, &missing, &offline_tool] {
            store.pin(path, true).unwrap();
            store.record_run(path, None).unwrap();
        }
        let candidates = store.orphan_candidates(&[root.clone()]).unwrap();
        assert_eq!(candidates, [missing.clone()]);
        assert_eq!(
            store
                .orphan_candidates(&[root.clone(), absent_root.clone()])
                .unwrap(),
            [missing.clone()]
        );
        assert_eq!(store.prune_orphans(&[root]).unwrap(), 1);
        let metadata = store.metadata().unwrap();
        assert!(metadata.contains_key(&path_key(&existing.to_string_lossy())));
        assert!(!metadata.contains_key(&path_key(&missing.to_string_lossy())));
        assert!(metadata.contains_key(&path_key(&offline_tool.to_string_lossy())));
    }

    #[test]
    fn chosen_entry_round_trips_through_the_database() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(&temp.path().join("data.db")).unwrap();
        let tool = Path::new(r"D:\Tools\fscan");
        let chosen = Path::new(r"D:\Tools\fscan\fscanPlus_amd64.exe");
        store.set_chosen_entry(tool, Some(chosen)).unwrap();
        let key = path_key(&tool.to_string_lossy());
        let metadata = store.metadata().unwrap();
        assert_eq!(
            metadata
                .get(&key)
                .and_then(|metadata| metadata.chosen_entry.as_deref()),
            Some(chosen)
        );
        store.set_chosen_entry(tool, None).unwrap();
        let metadata = store.metadata().unwrap();
        assert!(
            metadata
                .get(&key)
                .and_then(|metadata| metadata.chosen_entry.as_deref())
                .is_none()
        );
    }

    #[test]
    fn tray_preferences_round_trip_and_default_safely() {
        let defaults = Settings {
            path: PathBuf::new(),
            values: Value::Null,
        };
        assert_eq!(defaults.close_action(), "ask");
        assert!(!defaults.start_minimized());
        assert_eq!(defaults.hotkey(), crate::hotkey::DEFAULT);
        assert!(!defaults.tray_hint_shown());
        assert!(!defaults.guide_seen());

        let temp = tempfile::tempdir().unwrap();
        let config = temp.path().join("config.yaml");
        let mut settings = Settings {
            path: config.clone(),
            values: Value::Null,
        };
        settings.set_close_action("tray").unwrap();
        settings.set_start_minimized(true).unwrap();
        settings.set_hotkey("ctrl-alt-k").unwrap();
        settings.set_tray_hint_shown(true).unwrap();
        settings.set_guide_seen(true).unwrap();
        let restored = Settings {
            path: config,
            values: serde_yaml_ng::from_str(
                &fs::read_to_string(temp.path().join("config.yaml")).unwrap(),
            )
            .unwrap(),
        };
        assert_eq!(restored.close_action(), "tray");
        assert!(restored.start_minimized());
        assert_eq!(restored.hotkey(), "ctrl-alt-k");
        assert!(restored.tray_hint_shown());
        assert!(restored.guide_seen());
    }
}
