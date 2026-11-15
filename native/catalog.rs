use anyhow::{Context, Result};
use pinyin::ToPinyin;
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
};
use unicode_normalization::UnicodeNormalization;

use crate::storage::Store;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Kind {
    Python,
    Go,
    Node,
    Java,
    Rust,
    DotNet,
    Php,
    Ruby,
    Exe,
    Script,
    Shortcut,
    Other,
    Data,
}

impl Kind {
    pub fn label(self) -> &'static str {
        match self {
            Self::Python => "Python",
            Self::Go => "Go",
            Self::Node => "Node",
            Self::Java => "Java",
            Self::Rust => "Rust",
            Self::DotNet => ".NET",
            Self::Php => "PHP",
            Self::Ruby => "Ruby",
            Self::Exe => "EXE",
            Self::Script => "脚本",
            Self::Shortcut => "快捷方式",
            Self::Other => "其他",
            Self::Data => "资料",
        }
    }

    fn detect(name: &str) -> Option<Self> {
        match name {
            "pyproject.toml" | "requirements.txt" | "setup.cfg" | "pipfile" | "poetry.lock"
            | "uv.lock" => return Some(Self::Python),
            "go.mod" | "go.sum" | "go.work" => return Some(Self::Go),
            "package.json" | "package-lock.json" | "pnpm-lock.yaml" | "yarn.lock"
            | "tsconfig.json" => return Some(Self::Node),
            "pom.xml"
            | "build.gradle"
            | "build.gradle.kts"
            | "settings.gradle"
            | "settings.gradle.kts" => return Some(Self::Java),
            "cargo.toml" | "cargo.lock" => return Some(Self::Rust),
            "composer.json" => return Some(Self::Php),
            "gemfile" => return Some(Self::Ruby),
            _ => {}
        }
        use Kind::*;
        Some(match name.rsplit_once('.')?.1 {
            "py" | "pyw" | "pyz" => Python,
            "go" => Go,
            "js" | "mjs" | "cjs" | "ts" | "tsx" | "jsx" => Node,
            "jar" | "war" => Java,
            "rs" => Rust,
            "cs" | "vb" => DotNet,
            "php" => Php,
            "rb" => Ruby,
            "exe" | "msi" | "com" => Exe,
            "bat" | "cmd" | "ps1" | "psm1" | "sh" | "bash" | "vbs" => Script,
            "lnk" | "url" => Shortcut,
            _ => return None,
        })
    }
}

#[derive(Default)]
pub struct Metadata {
    pub stored_path: Option<PathBuf>,
    pub pinned: bool,
    pub tags: String,
    pub aliases: Vec<String>,
    pub command: Option<String>,
    pub shell: String,
    pub last_args: String,
    pub admin: bool,
    pub runs: u32,
    pub last_run: String,
    pub recent_hidden: bool,
    pub runtime: serde_json::Value,
    pub plans: Vec<serde_json::Value>,
    pub chosen_entry: Option<PathBuf>,
}

pub struct Entry {
    pub path: PathBuf,
    pub name: String,
    pub category: usize,
    pub workspace: String,
    pub directory: bool,
    pub resource: Option<crate::resources::Format>,
    pub file_size: Option<u64>,
    pub modified: Option<std::time::SystemTime>,
    pub kinds: Vec<Kind>,
    pub note: Option<PathBuf>,
    pub excerpt: String,
    pub metadata: Metadata,
    entry_type: &'static str,
    note_head: String,
    index: SearchRecord,
}

impl Entry {
    pub fn is_launchable(&self) -> bool {
        self.kinds.iter().any(|kind| *kind != Kind::Data)
            || self
                .metadata
                .command
                .as_deref()
                .is_some_and(|command| !command.trim().is_empty())
            || !self.metadata.plans.is_empty()
    }
    pub fn data_path(&self) -> &Path {
        self.metadata.stored_path.as_deref().unwrap_or(&self.path)
    }

    pub fn reindex(&mut self, category: &str) {
        self.index = SearchRecord::new(self, category);
    }

    pub fn score(&self, tokens: &[&str]) -> Option<u32> {
        self.index.score(tokens)
    }
}

pub struct Category {
    pub path: PathBuf,
    pub name: String,
    pub depth: usize,
    pub has_launchable_entries: bool,
    pub workspace_root: bool,
}

#[derive(Default)]
pub struct Catalog {
    pub categories: Vec<Category>,
    pub entries: Vec<Entry>,
    pub kinds: Vec<Kind>,
}

impl Catalog {
    pub fn index_launchable_categories(&mut self) {
        let paths: HashMap<_, _> = self
            .categories
            .iter()
            .enumerate()
            .map(|(id, category)| (category.path.as_path(), id))
            .collect();
        let mut populated = vec![false; self.categories.len()];
        for entry in self.entries.iter().filter(|entry| entry.is_launchable()) {
            for path in self.categories[entry.category].path.ancestors() {
                if let Some(&id) = paths.get(path) {
                    if std::mem::replace(&mut populated[id], true) {
                        break;
                    }
                }
            }
        }
        for (category, populated) in self.categories.iter_mut().zip(populated) {
            category.has_launchable_entries = populated;
        }
    }
}

struct Item {
    path: PathBuf,
    name: String,
    directory: bool,
}

const MARKERS: &[&str] = &[
    "pyproject.toml",
    "package.json",
    "cargo.toml",
    "go.mod",
    "requirements.txt",
];
const SUPPORT: &[&str] = &[
    "bin",
    "build",
    "dist",
    "doc",
    "docs",
    "example",
    "examples",
    "include",
    "lib",
    "libs",
    "out",
    "resource",
    "resources",
    "runtime",
    "src",
    "target",
    "test",
    "tests",
    "jre",
    "jdk",
    "scripts",
    "script",
    "tools",
    "tool",
    "plugins",
    "plugin",
    "conf",
    "config",
    "data",
    "assets",
    "static",
    "public",
    "migrations",
    "api",
    "routes",
    "models",
    "views",
    "controllers",
    "services",
    "utils",
    "common",
    "core",
    "internal",
    "pkg",
    "cmd",
];

fn children(path: &Path) -> Result<Vec<Item>> {
    let mut items = Vec::new();
    for entry in fs::read_dir(path).with_context(|| format!("无法读取 {}", path.display()))? {
        let Ok(entry) = entry else { continue };
        let name = entry.file_name().to_string_lossy().to_lowercase();
        if name.starts_with('.')
            || matches!(
                name.as_str(),
                "supergui" | "superbay" | "node_modules" | "__pycache__" | "venv" | "vendor"
            )
        {
            continue;
        }
        // Match Python's treatment of links, while the traversal checks their resolved targets.
        let directory = entry
            .file_type()
            .is_ok_and(|kind| kind.is_dir() || (kind.is_symlink() && entry.path().is_dir()));
        items.push(Item {
            path: entry.path(),
            name,
            directory,
        });
    }
    items.sort_unstable_by(|a, b| {
        b.directory
            .cmp(&a.directory)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(items)
}

fn ignored_file(item: &Item) -> bool {
    matches!(
        item.name.as_str(),
        "00.txt"
            | "note.md"
            | "note.txt"
            | "readme.md"
            | "readme.txt"
            | "readme.zh-cn.md"
            | "license"
            | "license.md"
            | "license.txt"
            | "contributing.md"
            | "changelog.md"
            | "desktop.ini"
            | "thumbs.db"
    )
}

fn launch_file(item: &Item) -> bool {
    !item.directory
        && (MARKERS.contains(&item.name.as_str())
            || item.name.rsplit_once('.').is_some_and(|(_, ext)| {
                matches!(
                    ext,
                    "exe" | "bat" | "cmd" | "lnk" | "ps1" | "py" | "jar" | "js" | "mjs" | "cjs"
                )
            }))
}

fn tool(path: &Path, items: &[Item]) -> bool {
    items.iter().any(launch_file) || path.join(".git").exists()
}

fn collection(path: &Path, items: &[Item]) -> bool {
    if path.join(".git").exists()
        || items
            .iter()
            .any(|item| MARKERS.contains(&item.name.as_str()))
    {
        return false;
    }
    let needed = items.iter().filter(|item| launch_file(item)).count().max(2);
    let folders: Vec<_> = items
        .iter()
        .filter(|item| item.directory && !SUPPORT.contains(&item.name.as_str()))
        .collect();
    folders.len() >= needed
        && folders
            .into_iter()
            .filter(|item| tool(&item.path, &children(&item.path).unwrap_or_default()))
            .take(needed)
            .count()
            == needed
}

pub fn file_name(path: &Path) -> String {
    path.file_name()
        .unwrap_or(path.as_os_str())
        .to_string_lossy()
        .into_owned()
}

fn category_name(path: &Path) -> String {
    let name = file_name(path);
    let bytes = name.as_bytes();
    if bytes.len() > 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b'-' {
        let short = &name[2..];
        if let Some((number, title)) = short.split_once('-')
            && !number.is_empty()
            && !title.is_empty()
            && number.bytes().all(|b| b.is_ascii_digit())
        {
            return title.to_owned();
        }
        return short.to_owned();
    }
    name
}

fn note_head(path: &Path) -> String {
    let mut bytes = Vec::new();
    if let Ok(file) = File::open(path) {
        let _ = file.take(300).read_to_end(&mut bytes);
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

struct Scanner {
    catalog: Catalog,
    root: PathBuf,
    workspace: String,
    visited: HashSet<PathBuf>,
    max_depth: usize,
}

impl Scanner {
    fn category(&mut self, path: &Path, depth: usize) -> usize {
        let id = self.catalog.categories.len();
        self.catalog.categories.push(Category {
            path: path.into(),
            name: category_name(path),
            depth,
            has_launchable_entries: false,
            workspace_root: false,
        });
        id
    }

    fn visit_once(&mut self, path: &Path) -> bool {
        path.canonicalize()
            .is_ok_and(|target| target.starts_with(&self.root) && self.visited.insert(target))
    }

    fn add(&mut self, item: Item, category: usize, items: &[Item], is_tool: bool) {
        let name = file_name(&item.path);
        let mut kinds = if item.directory {
            items
                .iter()
                .filter_map(|child| Kind::detect(&child.name))
                .collect::<Vec<_>>()
        } else {
            Kind::detect(&item.name).into_iter().collect()
        };
        kinds.sort_unstable();
        kinds.dedup();
        if kinds.is_empty() {
            kinds.push(if is_tool { Kind::Other } else { Kind::Data });
        }
        let note = item
            .directory
            .then(|| {
                ["NOTE.md", "00.txt"]
                    .into_iter()
                    .map(|name| item.path.join(name))
                    .find(|path| path.is_file())
            })
            .flatten();
        let note_head = note.as_deref().map(note_head).unwrap_or_default();
        let excerpt = note_head
            .lines()
            .map(|line| line.trim().trim_start_matches('#').trim())
            .find(|line| !line.is_empty() && !line.starts_with("---"))
            .map(|line| line.chars().take(80).collect())
            .unwrap_or_default();
        let entry_type = if is_tool {
            "tool"
        } else if item.directory {
            "dir"
        } else {
            "file"
        };
        let metadata = item.path.metadata().ok();
        self.catalog.entries.push(Entry {
            resource: (!is_tool && kinds == [Kind::Data])
                .then(|| crate::resources::Format::from_path(&item.path, item.directory)),
            file_size: metadata
                .as_ref()
                .filter(|_| !item.directory)
                .map(|meta| meta.len()),
            modified: metadata.and_then(|meta| meta.modified().ok()),
            name,
            path: item.path,
            category,
            workspace: self.workspace.clone(),
            directory: item.directory,
            kinds,
            note,
            excerpt,
            note_head,
            entry_type,
            metadata: Metadata::default(),
            index: SearchRecord::default(),
        });
    }

    fn visit(&mut self, path: &Path, items: Vec<Item>, depth: usize) {
        let category = self.category(path, depth);
        for item in items {
            if !item.directory {
                if !ignored_file(&item) {
                    self.add(item, category, &[], false);
                }
                continue;
            }
            if !self.visit_once(&item.path) {
                continue;
            }
            let nested = children(&item.path).unwrap_or_default();
            let is_tool = tool(&item.path, &nested);
            if depth + 1 < self.max_depth
                && nested
                    .iter()
                    .any(|child| child.directory || !ignored_file(child))
                && (!is_tool || collection(&item.path, &nested))
            {
                self.visit(&item.path, nested, depth + 1);
            } else {
                self.add(item, category, &nested, is_tool);
            }
        }
    }
}

pub fn directory_letter(name: &str) -> Option<char> {
    let mut parts = name.splitn(3, '-');
    let (Some(letter), Some(number)) = (parts.next(), parts.next()) else {
        return None;
    };
    (letter.len() == 1
        && letter.as_bytes()[0].is_ascii_alphabetic()
        && !number.is_empty()
        && number.bytes().all(|byte| byte.is_ascii_digit())
        && parts.next().is_none_or(|name| !name.trim().is_empty()))
    .then(|| letter.as_bytes()[0].to_ascii_uppercase() as char)
}

#[cfg(test)]
pub fn scan_root(root: &Path, workspace: String, max_depth: usize) -> Result<Catalog> {
    scan_workspace(root, workspace, max_depth, false)
}

pub fn scan_workspace(
    root: &Path,
    workspace: String,
    max_depth: usize,
    named_only: bool,
) -> Result<Catalog> {
    let canonical = root
        .canonicalize()
        .with_context(|| format!("无法打开工作区 {}", root.display()))?;
    let items = children(root)?;
    let mut scanner = Scanner {
        catalog: Catalog::default(),
        root: canonical.clone(),
        workspace,
        visited: HashSet::from([canonical]),
        max_depth: max_depth.clamp(1, 16),
    };
    let root_id = scanner.category(root, 0);
    scanner.catalog.categories[root_id].workspace_root = true;
    for item in items {
        if named_only && (!item.directory || directory_letter(&item.name).is_none()) {
            continue;
        }
        if item.directory {
            if !scanner.visit_once(&item.path) {
                continue;
            }
            let nested = children(&item.path).unwrap_or_default();
            if tool(&item.path, &nested) && !collection(&item.path, &nested) {
                scanner.add(item, root_id, &nested, true);
            } else {
                scanner.visit(&item.path, nested, 0);
            }
        } else if !ignored_file(&item) {
            scanner.add(item, root_id, &[], false);
        }
    }
    Ok(scanner.catalog)
}

pub fn merge(catalogs: Vec<Catalog>, db: &Path) -> Result<Catalog> {
    let mut catalog = Catalog::default();
    let mut seen = HashSet::new();
    let mut paths = HashSet::new();
    let deduplicate = catalogs.len() > 1;
    for mut part in catalogs {
        let offset = catalog.categories.len();
        part.entries.retain_mut(|entry| {
            if deduplicate {
                let path = crate::storage::path_key(&entry.path.to_string_lossy());
                let target = entry.path.canonicalize().ok();
                if !paths.insert(path) || target.is_some_and(|target| !seen.insert(target)) {
                    return false;
                }
            }
            entry.category += offset;
            true
        });
        catalog.categories.append(&mut part.categories);
        catalog.entries.append(&mut part.entries);
    }
    let mut metadata = Store::open(db)?.metadata()?;
    for entry in &mut catalog.entries {
        entry.metadata = metadata
            .remove(&crate::storage::path_key(&entry.path.to_string_lossy()))
            .unwrap_or_default();
        entry.reindex(&catalog.categories[entry.category].name);
    }
    catalog.kinds = catalog
        .entries
        .iter()
        .filter(|entry| entry.is_launchable())
        .flat_map(|entry| entry.kinds.iter().copied())
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    catalog.index_launchable_categories();
    catalog.entries.sort_unstable_by(|a, b| {
        a.index
            .name
            .cmp(&b.index.name)
            .then_with(|| a.path.cmp(&b.path))
    });
    Ok(catalog)
}

pub fn normalize(value: &str) -> String {
    value.nfkc().flat_map(char::to_lowercase).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn named_only_filters_top_level_categories_and_keeps_tools_inside() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path();
        for name in ["A-01-工具", "z-7", "notes", "alpha-2", "C-xx"] {
            let folder = root.join(name);
            fs::create_dir(&folder).unwrap();
            fs::write(folder.join("manual.txt"), name).unwrap();
        }
        fs::write(root.join("loose.txt"), "").unwrap();
        let catalog = scan_workspace(root, "Test".into(), 2, true).unwrap();
        let mut children: Vec<_> = catalog
            .entries
            .iter()
            .map(|entry| file_name(entry.path.parent().unwrap()))
            .collect();
        children.sort_unstable();
        assert_eq!(children, ["A-01-工具", "z-7"]);
    }

    #[test]
    fn categories_only_count_detected_launchers_and_their_ancestors() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path();
        for folder in ["A-01/leaf", "B-01", "C-01"] {
            fs::create_dir_all(root.join(folder)).unwrap();
        }
        fs::write(root.join("A-01/leaf/start.py"), "").unwrap();
        fs::write(root.join("B-01/manual.txt"), "").unwrap();
        let catalog = merge(
            vec![scan_root(root, "Test".into(), 4).unwrap()],
            &root.join("data.db"),
        )
        .unwrap();
        let populated = |name| {
            catalog
                .categories
                .iter()
                .find(|category| category.path.ends_with(name))
                .unwrap()
                .has_launchable_entries
        };
        assert!(populated("A-01"));
        assert!(!populated("B-01"));
        assert!(!populated("C-01"));
        assert_eq!(directory_letter("b-007-工具"), Some('B'));
        assert_eq!(directory_letter("B-07-"), None);
    }

    #[test]
    fn overlapping_roots_deduplicate_and_index_workspace_labels() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("parent");
        let nested = root.join("nested");
        fs::create_dir_all(&nested).unwrap();
        fs::write(nested.join("shared.txt"), "").unwrap();
        let other = temp.path().join("other");
        fs::create_dir(&other).unwrap();
        fs::write(other.join("extra.py"), "").unwrap();
        let catalog = merge(
            vec![
                scan_root(&nested, "Primary".into(), 2).unwrap(),
                scan_root(&root, "Overlap".into(), 2).unwrap(),
                scan_root(&other, "Secondary".into(), 2).unwrap(),
            ],
            &temp.path().join("data.db"),
        )
        .unwrap();
        let shared: Vec<_> = catalog
            .entries
            .iter()
            .filter(|entry| entry.name == "shared.txt")
            .collect();
        assert_eq!(shared.len(), 1);
        assert_eq!(shared[0].workspace, "Primary");
        assert!(shared[0].score(&["primary", "shared"]).is_some());
        assert!(
            catalog
                .entries
                .iter()
                .any(|entry| entry.score(&["secondary", "extra"]).is_some())
        );
    }
}

#[derive(Default)]
struct SearchRecord {
    name: String,
    aliases_and_tags: String,
    tags: String,
    context: String,
    note: String,
    phonetic: String,
    initials: String,
    languages: String,
    entry_type: &'static str,
}

impl SearchRecord {
    fn new(entry: &Entry, category: &str) -> Self {
        let name = normalize(&entry.name);
        let aliases = entry.metadata.aliases.join(" ");
        let phonetic_source = format!("{} {aliases}", entry.name);
        let mut phonetic = String::new();
        if !phonetic_source.is_ascii() {
            let mut initials = String::new();
            for (ch, value) in phonetic_source
                .chars()
                .zip(phonetic_source.as_str().to_pinyin())
            {
                if let Some(value) = value {
                    phonetic.push_str(value.plain());
                    initials.push_str(value.first_letter());
                } else {
                    phonetic.push(ch);
                    initials.push(ch);
                }
            }
            phonetic.push(' ');
            phonetic.push_str(&initials);
        }
        Self {
            initials: name
                .split(|ch: char| !ch.is_ascii_alphanumeric())
                .filter_map(|word| word.chars().next())
                .collect(),
            name,
            aliases_and_tags: normalize(&format!(
                "{} {aliases}",
                entry.metadata.tags.replace(',', " ")
            )),
            tags: normalize(&entry.metadata.tags),
            context: normalize(&format!(
                "{} {category} {}",
                entry.workspace,
                entry.path.display()
            )),
            note: normalize(&entry.note_head),
            phonetic: normalize(&phonetic),
            languages: normalize(
                &entry
                    .kinds
                    .iter()
                    .map(|kind| kind.label())
                    .collect::<Vec<_>>()
                    .join(" "),
            ),
            entry_type: entry.entry_type,
        }
    }

    fn score(&self, tokens: &[&str]) -> Option<u32> {
        let mut total = 0;
        for &token in tokens {
            if let Some(value) = token
                .strip_prefix("lang:")
                .or_else(|| token.strip_prefix("语言:"))
            {
                if value.is_empty() || !self.languages.contains(value) {
                    return None;
                }
                total += 60;
            } else if let Some(value) = token
                .strip_prefix("tag:")
                .or_else(|| token.strip_prefix("标签:"))
            {
                if value.is_empty() || !self.tags.contains(value) {
                    return None;
                }
                total += 65;
            } else if let Some(value) = token
                .strip_prefix("type:")
                .or_else(|| token.strip_prefix("类型:"))
            {
                let value = match value {
                    "工具" => "tool",
                    "文件" => "file",
                    "目录" => "dir",
                    value => value,
                };
                if value != self.entry_type {
                    return None;
                }
            } else {
                total += if token == self.name {
                    120
                } else if self.name.starts_with(token) {
                    100
                } else if self.name.contains(token) {
                    80
                } else if token == self.initials {
                    70
                } else if self.aliases_and_tags.contains(token) {
                    65
                } else if self.languages.contains(token) {
                    55
                } else if self.context.contains(token) {
                    40
                } else if self.phonetic.contains(token) {
                    30
                } else if (2..=12).contains(&token.len())
                    && token.bytes().all(|byte| byte.is_ascii_alphanumeric())
                    && {
                        let mut name = self.name.chars();
                        token
                            .chars()
                            .all(|ch| name.any(|candidate| candidate == ch))
                    }
                {
                    25
                } else if self.note.contains(token) {
                    15
                } else {
                    return None;
                };
            }
        }
        Some(total)
    }
}
