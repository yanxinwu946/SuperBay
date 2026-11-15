mod dialogs;
mod entry_preview;
mod file_actions;
mod guide;
mod launch_settings;
mod layout;
mod notes;
mod processes;
mod scoop_manager;
mod settings_page;

use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    time::Duration,
};

use gpui::{
    App, AppContext, Context, Entity, FocusHandle, KeyBinding, Keystroke, PathPromptOptions,
    ScrollStrategy, Task, UniformListScrollHandle, Window, actions,
};
use gpui_component::{
    Theme, ThemeMode, WindowExt,
    input::{InputEvent, InputState},
    notification::Notification,
};
use notify::{Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};

use crate::{
    catalog::{self, Catalog, Kind},
    launch,
    storage::{Settings, Store},
};

const BUTTON_HEIGHT: f32 = 32.;

fn button(id: impl Into<gpui::ElementId>) -> gpui_component::button::Button {
    use gpui::Styled as _;
    gpui_component::button::Button::new(id).h(gpui::px(BUTTON_HEIGHT))
}

actions!(
    superbay,
    [
        Find,
        Refresh,
        Previous,
        Next,
        Left,
        Right,
        Run,
        RunElevated,
        RunWithArgs,
        Pin,
        SelectAll,
        CopyPaths,
        PreviewSelection,
        EscapeWorkspace,
        EditNote,
        SaveNote,
        ToggleDetail,
        ClearFilters,
        OpenSettings
    ]
);

pub fn bind_keys(cx: &mut App) {
    cx.bind_keys([
        KeyBinding::new("ctrl-,", OpenSettings, Some("SuperBay")),
        KeyBinding::new("ctrl-,", OpenSettings, Some("Settings")),
        KeyBinding::new("escape", EscapeWorkspace, Some("Settings")),
        KeyBinding::new("ctrl-k", Find, Some("SuperBay")),
        KeyBinding::new("f5", Refresh, Some("SuperBay")),
        KeyBinding::new("ctrl-b", ToggleDetail, Some("SuperBay")),
        KeyBinding::new("ctrl-shift-f", ClearFilters, Some("SuperBay")),
        KeyBinding::new("up", Previous, Some("SuperBay && !Input")),
        KeyBinding::new("down", Next, Some("SuperBay && !Input")),
        KeyBinding::new("left", Left, Some("SuperBay && !Input")),
        KeyBinding::new("right", Right, Some("SuperBay && !Input")),
        KeyBinding::new("enter", Run, Some("SuperBay && !Input")),
        KeyBinding::new("ctrl-enter", RunElevated, Some("SuperBay")),
        KeyBinding::new("shift-enter", RunWithArgs, Some("SuperBay")),
        KeyBinding::new("ctrl-d", Pin, Some("SuperBay && !Input")),
        KeyBinding::new("ctrl-a", SelectAll, Some("SuperBay && !Input")),
        KeyBinding::new("ctrl-c", CopyPaths, Some("SuperBay && !Input")),
        KeyBinding::new("space", PreviewSelection, Some("SuperBay && !Input")),
        KeyBinding::new("escape", EscapeWorkspace, Some("SuperBay")),
        KeyBinding::new("f2", EditNote, Some("SuperBay && !Input")),
        KeyBinding::new("ctrl-s", SaveNote, Some("SuperBay")),
    ]);
}

pub fn report_startup_error(error: &str) {
    use windows::{
        Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW},
        core::{HSTRING, w},
    };
    // The message and caption outlive the synchronous call.
    unsafe {
        MessageBoxW(
            None,
            &HSTRING::from(error),
            w!("SuperBay"),
            MB_OK | MB_ICONERROR,
        );
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum Scope {
    All,
    Pinned,
    Recent,
    Category(usize),
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) enum CloseAction {
    Ask,
    Tray,
    Quit,
}

impl CloseAction {
    pub(crate) fn key(self) -> &'static str {
        match self {
            Self::Ask => "ask",
            Self::Tray => "tray",
            Self::Quit => "quit",
        }
    }

    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::Ask => "每次询问",
            Self::Tray => "收起到托盘",
            Self::Quit => "退出程序",
        }
    }

    pub(crate) fn from_key(value: &str) -> Self {
        match value {
            "tray" => Self::Tray,
            "quit" => Self::Quit,
            _ => Self::Ask,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ResultSort {
    Relevance,
    Name,
    Type,
    LastUsed,
    Modified,
    Size,
}

impl ResultSort {
    const OPTIONS: [Self; 6] = [
        Self::Relevance,
        Self::Name,
        Self::Type,
        Self::LastUsed,
        Self::Modified,
        Self::Size,
    ];

    fn from_key(value: &str) -> Self {
        match value {
            "name" => Self::Name,
            "type" => Self::Type,
            "last_used" => Self::LastUsed,
            "modified" => Self::Modified,
            "size" => Self::Size,
            _ => Self::Relevance,
        }
    }

    fn key(self) -> &'static str {
        match self {
            Self::Relevance => "relevance",
            Self::Name => "name",
            Self::Type => "type",
            Self::LastUsed => "last_used",
            Self::Modified => "modified",
            Self::Size => "size",
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Relevance => "默认顺序",
            Self::Name => "名称",
            Self::Type => "类型",
            Self::LastUsed => "最近打开",
            Self::Modified => "最近修改",
            Self::Size => "文件大小",
        }
    }
}

pub struct Workspace {
    settings: Option<Settings>,
    root: Option<PathBuf>,
    catalog: Catalog,
    search: Entity<InputState>,
    query: String,
    selected: Option<usize>,
    group_selection: HashSet<usize>,
    selection_anchor: Option<usize>,
    scope: Scope,
    kind: Option<Kind>,
    sort: ResultSort,
    dense: bool,
    grid: bool,
    columns: usize,
    results_width: Option<gpui::Pixels>,
    expanded_categories: HashSet<PathBuf>,
    results: Vec<usize>,
    list: UniformListScrollHandle,
    list_focus: FocusHandle,
    details: bool,
    scanning: bool,
    all_workspaces: bool,
    pending_scan: bool,
    generation: u64,
    error: Option<String>,
    watcher: Option<(RecommendedWatcher, Task<()>)>,
    backends: HashMap<PathBuf, launch::Backend>,
    scoop_window: Option<gpui::WindowHandle<gpui_component::Root>>,
    notes: HashMap<PathBuf, notes::Draft>,
    launch_preview: entry_preview::LaunchPreview,
    settings_page: Option<settings_page::SettingsPage>,
    guide: bool,
    tray: Option<crate::tray::Handle>,
    autostart: bool,
}

impl Workspace {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let (settings, error) = match Settings::load() {
            Ok(settings) => (Some(settings), None),
            Err(error) => (None, Some(format!("{error:#}"))),
        };
        let root = settings.as_ref().and_then(Settings::workspace);
        let guide = settings
            .as_ref()
            .is_some_and(|settings| !settings.guide_seen());
        let sort = ResultSort::from_key(
            settings
                .as_ref()
                .map(Settings::result_sort)
                .unwrap_or_default(),
        );
        let dense = settings.as_ref().is_some_and(Settings::dense_results);
        let grid = settings.as_ref().is_some_and(Settings::grid_results);
        let search =
            cx.new(|cx| InputState::new(window, cx).placeholder("搜索名称、标签、笔记或拼音"));
        cx.subscribe_in(
            &search,
            window,
            |this, search, event, window, cx| match event {
                InputEvent::Change => {
                    this.query = catalog::normalize(search.read(cx).value().as_ref());
                    this.rebuild(true, cx);
                }
                InputEvent::PressEnter { secondary: false } if !window.has_active_dialog(cx) => {
                    this.run_selected(window, cx);
                }
                _ => {}
            },
        )
        .detach();
        cx.observe_window_appearance(window, |this, window, cx| this.apply_theme(window, cx))
            .detach();
        cx.observe_window_activation(window, |this, window, cx| {
            if !window.is_window_active() {
                this.cancel_hotkey_recording(cx);
            }
        })
        .detach();
        let view = Self {
            settings,
            root,
            search,
            query: String::new(),
            selected: None,
            group_selection: HashSet::new(),
            selection_anchor: None,
            scope: Scope::All,
            kind: None,
            sort,
            dense,
            grid,
            columns: 1,
            results_width: None,
            expanded_categories: HashSet::new(),
            catalog: Catalog::default(),
            results: Vec::new(),
            list: UniformListScrollHandle::new(),
            list_focus: cx.focus_handle(),
            details: window.viewport_size().width >= gpui::px(1050.),
            scanning: false,
            all_workspaces: false,
            pending_scan: false,
            generation: 0,
            error,
            watcher: None,
            backends: HashMap::new(),
            scoop_window: None,
            notes: HashMap::new(),
            launch_preview: entry_preview::LaunchPreview::default(),
            settings_page: None,
            guide,
            tray: None,
            autostart: crate::autostart::enabled(),
        };
        view.apply_theme(window, cx);
        cx.on_next_frame(window, |this, window, cx| {
            if this.guide {
                this.list_focus.focus(window);
            } else {
                this.focus_search(window, cx);
            }
            this.refresh(window, cx);
            this.watch(window, cx);
        });
        view
    }

    fn apply_theme(&self, window: &mut Window, cx: &mut Context<Self>) {
        match self.settings.as_ref().map(Settings::theme) {
            Some("light") => Theme::change(ThemeMode::Light, Some(window), cx),
            Some("dark") => Theme::change(ThemeMode::Dark, Some(window), cx),
            _ => Theme::sync_system_appearance(Some(window), cx),
        }
        layout::Palette::apply(cx);
        cx.notify();
    }

    fn set_theme(&mut self, next: &str, window: &mut Window, cx: &mut Context<Self>) {
        let Some(settings) = self.settings.as_mut() else {
            return;
        };
        let message = match next {
            "light" => "浅色主题",
            "dark" => "深色主题",
            _ => "跟随系统主题",
        };
        match settings.set_theme(next) {
            Ok(()) => {
                self.apply_theme(window, cx);
                window.push_notification(message, cx);
            }
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    fn notify_error(error: anyhow::Error, window: &mut Window, cx: &mut App) {
        window.push_notification(Notification::error(format!("{error:#}")), cx);
    }

    fn pick_workspace(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let selection = cx.prompt_for_paths(PathPromptOptions {
            directories: true,
            files: false,
            multiple: false,
            prompt: Some("选择工具目录".into()),
        });
        cx.spawn_in(window, async move |view, cx| match selection.await {
            Ok(Ok(Some(paths))) => {
                if let Some(root) = paths.into_iter().next() {
                    let _ = cx.update(|window, cx| {
                        view.update(cx, |this, cx| {
                            if let Some(settings) = this.settings.as_mut() {
                                match settings.add_workspace(&root) {
                                    Ok(()) => this.activate_workspace(root, window, cx),
                                    Err(error) => Self::notify_error(error, window, cx),
                                }
                            }
                        })
                    });
                }
            }
            Ok(Err(error)) => {
                let _ = cx.update(|window, cx| Self::notify_error(error, window, cx));
            }
            _ => {}
        })
        .detach();
    }

    fn switch_workspace(&mut self, root: PathBuf, window: &mut Window, cx: &mut Context<Self>) {
        let Some(settings) = self.settings.as_mut() else {
            return;
        };
        if let Err(error) = settings.set_workspace(&root) {
            Self::notify_error(error, window, cx);
            return;
        }
        self.activate_workspace(root, window, cx);
    }

    fn activate_workspace(&mut self, root: PathBuf, window: &mut Window, cx: &mut Context<Self>) {
        self.root = Some(root);
        self.all_workspaces = false;
        if self.guide {
            self.dismiss_guide(window, cx);
        }
        self.reset_catalog(window, cx);
    }

    fn toggle_named_only(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(settings) = self.settings.as_mut() else {
            return;
        };
        match settings.set_named_only(!settings.named_only()) {
            Ok(()) => self.reset_catalog(window, cx),
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    fn toggle_empty_categories(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(settings) = self.settings.as_mut() else {
            return;
        };
        let hide = !settings.hide_empty_categories();
        if let Err(error) = settings.set_hide_empty_categories(hide) {
            Self::notify_error(error, window, cx);
            return;
        }
        if hide
            && let Scope::Category(id) = self.scope
            && !self.catalog.categories[id].has_launchable_entries
        {
            self.scope = Scope::All;
            self.rebuild(true, cx);
        }
        cx.notify();
    }

    pub(crate) fn attach_tray(&mut self, handle: crate::tray::Handle) {
        self.tray = Some(handle);
    }

    pub(crate) fn close_action(&self) -> CloseAction {
        self.settings
            .as_ref()
            .map(|settings| CloseAction::from_key(settings.close_action()))
            .unwrap_or(CloseAction::Ask)
    }

    pub(crate) fn save_close_action(&mut self, action: CloseAction) -> anyhow::Result<()> {
        let Some(settings) = self.settings.as_mut() else {
            anyhow::bail!("无法读取应用设置");
        };
        settings.set_close_action(action.key())
    }

    fn set_close_action(
        &mut self,
        action: CloseAction,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        match self.save_close_action(action) {
            Ok(()) => cx.notify(),
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    pub(crate) fn take_tray_hint(&mut self) -> bool {
        let Some(settings) = self.settings.as_mut() else {
            return false;
        };
        if settings.tray_hint_shown() {
            return false;
        }
        settings.set_tray_hint_shown(true).is_ok()
    }

    pub(crate) fn hide_to_tray(&mut self, window: &mut Window, _cx: &mut Context<Self>) {
        self.settings_page = None;
        let show_hint = self.take_tray_hint();
        crate::tray::hide(window);
        if show_hint && let Some(tray) = &self.tray {
            tray.hint();
        }
    }

    pub(crate) fn quit_app(&mut self, cx: &mut Context<Self>) {
        self.settings_page = None;
        cx.quit();
    }

    fn apply_hotkey(
        &mut self,
        hotkey: Option<Keystroke>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> anyhow::Result<()> {
        let Some(tray) = self.tray.clone() else {
            anyhow::bail!("托盘不可用，暂时无法设置快捷键");
        };
        let previous = tray.hotkey();
        tray.set_hotkey(hotkey.clone())?;
        let stored = hotkey.as_ref().map(Keystroke::unparse).unwrap_or_default();
        let Some(settings) = self.settings.as_mut() else {
            let _ = tray.set_hotkey(previous);
            anyhow::bail!("无法读取应用设置");
        };
        if let Err(error) = settings.set_hotkey(&stored) {
            let _ = tray.set_hotkey(previous);
            return Err(error);
        }
        if let Some(page) = self.settings_page.as_mut() {
            page.stop_hotkey_recording();
            page.hotkey_error = None;
        }
        let message = match &hotkey {
            Some(hotkey) => format!(
                "唤起快捷键已更新为 {}",
                gpui_component::kbd::Kbd::format(hotkey)
            ),
            None => "已停用唤起快捷键".to_owned(),
        };
        window.push_notification(message, cx);
        cx.notify();
        Ok(())
    }

    fn set_start_minimized(&mut self, enabled: bool, window: &mut Window, cx: &mut Context<Self>) {
        if enabled && self.tray.is_none() {
            Self::notify_error(anyhow::anyhow!("托盘不可用，无法在启动时隐藏"), window, cx);
            return;
        }
        let Some(settings) = self.settings.as_mut() else {
            return;
        };
        match settings.set_start_minimized(enabled) {
            Ok(()) => cx.notify(),
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    fn set_autostart(&mut self, enabled: bool, window: &mut Window, cx: &mut Context<Self>) {
        match crate::autostart::apply(enabled) {
            Ok(()) => {
                self.autostart = enabled;
                window.push_notification(
                    if enabled {
                        "登录 Windows 后将在托盘中启动"
                    } else {
                        "已关闭开机自动启动"
                    },
                    cx,
                );
                cx.notify();
            }
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    fn search_all_workspaces(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if !self.all_workspaces {
            self.all_workspaces = true;
            self.reset_catalog(window, cx);
        } else {
            self.scope = Scope::All;
            self.rebuild(true, cx);
        }
        self.focus_search(window, cx);
    }

    fn show_current_workspace(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.all_workspaces {
            self.all_workspaces = false;
            self.reset_catalog(window, cx);
        } else {
            self.scope = Scope::All;
            self.rebuild(true, cx);
        }
    }

    fn reset_catalog(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.generation += 1;
        self.scanning = false;
        self.pending_scan = false;
        self.catalog = Catalog::default();
        self.expanded_categories.clear();
        self.scope = Scope::All;
        self.kind = None;
        self.selected = None;
        self.launch_preview = entry_preview::LaunchPreview::default();
        self.rebuild(true, cx);
        self.refresh(window, cx);
        self.watch(window, cx);
    }

    fn workspace_roots(&self) -> Vec<(String, PathBuf)> {
        let mut roots = self
            .settings
            .as_ref()
            .filter(|_| self.all_workspaces)
            .map(Settings::presets)
            .unwrap_or_default();
        if let Some(root) = &self.root {
            let key = crate::storage::path_key(&root.to_string_lossy());
            let name = roots
                .iter()
                .find(|(_, path)| crate::storage::path_key(&path.to_string_lossy()) == key)
                .map(|(name, _)| name.clone())
                .unwrap_or_else(|| catalog::file_name(root));
            roots.insert(0, (name, root.clone()));
        }
        let mut seen = HashSet::new();
        roots.retain(|(_, path)| seen.insert(crate::storage::path_key(&path.to_string_lossy())));
        roots
    }

    fn toggle_workspace_preset(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let (Some(root), Some(settings)) = (&self.root, self.settings.as_mut()) else {
            return;
        };
        let mut presets = settings.presets();
        let key = crate::storage::path_key(&root.to_string_lossy());
        let message = if let Some(index) = presets
            .iter()
            .position(|(_, path)| crate::storage::path_key(&path.to_string_lossy()) == key)
        {
            presets.remove(index);
            "已移除工作区快捷入口"
        } else {
            presets.push((catalog::file_name(root), root.clone()));
            "已保存工作区快捷入口"
        };
        match settings.set_presets(&presets) {
            Ok(()) => {
                window.push_notification(message, cx);
                if self.all_workspaces {
                    self.reset_catalog(window, cx);
                }
                cx.notify();
            }
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    fn watch(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.watcher = None;
        let roots = self.workspace_roots();
        if roots.is_empty() {
            return;
        }
        let (sender, receiver) = async_channel::bounded(1);
        let watcher = notify::recommended_watcher(move |event: notify::Result<Event>| {
            if event.is_ok_and(|event| {
                matches!(
                    event.kind,
                    EventKind::Create(_)
                        | EventKind::Remove(_)
                        | EventKind::Modify(_)
                        | EventKind::Any
                )
            }) {
                let _ = sender.try_send(());
            }
        })
        .and_then(|mut watcher| {
            let mut watched = HashSet::new();
            let mut first_error = None;
            for (_, root) in &roots {
                if let Ok(path) = root.canonicalize()
                    && watched.iter().all(|parent| !path.starts_with(parent))
                {
                    match watcher.watch(root, RecursiveMode::Recursive) {
                        Ok(()) => {
                            watched.insert(path);
                        }
                        Err(error) => {
                            first_error.get_or_insert(error);
                        }
                    }
                }
            }
            if watched.is_empty()
                && let Some(error) = first_error
            {
                return Err(error);
            }
            Ok(watcher)
        });
        match watcher {
            Ok(watcher) => {
                let timer = cx.background_executor().clone();
                let task = cx.spawn_in(window, async move |view, cx| {
                    while receiver.recv().await.is_ok() {
                        timer.timer(Duration::from_millis(350)).await;
                        let _ = receiver.try_recv();
                        if cx
                            .update(|window, cx| {
                                view.update(cx, |this, cx| this.refresh(window, cx))
                            })
                            .is_err()
                        {
                            break;
                        }
                    }
                });
                self.watcher = Some((watcher, task));
            }
            Err(error) => {
                Self::notify_error(anyhow::anyhow!("无法监听工作区：{error}"), window, cx)
            }
        }
    }

    fn refresh(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(settings) = &self.settings else {
            return;
        };
        let roots = self.workspace_roots();
        if roots.is_empty() {
            return;
        }
        if self.scanning {
            self.pending_scan = true;
            return;
        }
        self.scanning = true;
        self.error = None;
        let generation = self.generation;
        let database = settings.database();
        let depth = settings.scan_depth();
        let named_only = settings.named_only();
        let executor = cx.background_executor().clone();
        let tasks: Vec<_> = roots
            .into_iter()
            .map(|(name, root)| {
                executor.spawn(async move {
                    let result = catalog::scan_workspace(&root, name.clone(), depth, named_only);
                    (name, result)
                })
            })
            .collect();
        cx.notify();
        cx.spawn_in(window, async move |view, cx| {
            let mut catalogs = Vec::new();
            let mut unavailable = Vec::new();
            for task in tasks {
                match task.await {
                    (_, Ok(catalog)) => catalogs.push(catalog),
                    (name, Err(error)) => unavailable.push((name, error)),
                }
            }
            let warnings = match unavailable.as_slice() {
                [] => None,
                [(_, error)] => Some(format!("{error:#}")),
                _ => Some(format!(
                    "部分工作区不可用 · {}",
                    unavailable
                        .iter()
                        .map(|(name, _)| name.as_str())
                        .collect::<Vec<_>>()
                        .join("、")
                )),
            };
            let result = executor
                .spawn(async move { catalog::merge(catalogs, &database) })
                .await;
            let _ = cx.update(|window, cx| {
                view.update(cx, |this, cx| {
                    if generation != this.generation {
                        return;
                    }
                    this.scanning = false;
                    match result {
                        Ok(catalog) => {
                            this.error = warnings;
                            let category_path = match this.scope {
                                Scope::Category(index) => {
                                    Some(&this.catalog.categories[index].path)
                                }
                                _ => None,
                            };
                            if let Some(path) = category_path {
                                this.scope = catalog
                                    .categories
                                    .iter()
                                    .position(|category| &category.path == path)
                                    .map(Scope::Category)
                                    .unwrap_or(Scope::All);
                            }
                            this.selected = this.selected.and_then(|index| {
                                let path = &this.catalog.entries[index].path;
                                catalog.entries.iter().position(|entry| &entry.path == path)
                            });
                            if !this.group_selection.is_empty() {
                                let paths: HashSet<_> = this
                                    .group_selection
                                    .iter()
                                    .map(|&id| &this.catalog.entries[id].path)
                                    .collect();
                                this.group_selection = catalog
                                    .entries
                                    .iter()
                                    .enumerate()
                                    .filter_map(|(id, entry)| {
                                        paths.contains(&entry.path).then_some(id)
                                    })
                                    .collect();
                            }
                            this.selection_anchor = this.selected;
                            this.catalog = catalog;
                            this.rebuild(false, cx);
                        }
                        Err(error) => this.error = Some(format!("{error:#}")),
                    }
                    if std::mem::take(&mut this.pending_scan) {
                        this.refresh(window, cx);
                    }
                    cx.notify();
                })
            });
        })
        .detach();
    }

    fn rebuild(&mut self, reset: bool, cx: &mut Context<Self>) {
        let tokens: Vec<_> = self.query.split_whitespace().collect();
        let mut ranked: Vec<_> = self
            .catalog
            .entries
            .iter()
            .enumerate()
            .filter_map(|(index, entry)| {
                let matches_scope = match self.scope {
                    Scope::All => true,
                    Scope::Pinned => entry.metadata.pinned,
                    Scope::Recent => {
                        !entry.metadata.last_run.is_empty() && !entry.metadata.recent_hidden
                    }
                    Scope::Category(id) => {
                        entry.category == id
                            || ((self.all_workspaces || id > 0)
                                && self.catalog.categories[entry.category]
                                    .path
                                    .starts_with(&self.catalog.categories[id].path))
                    }
                };
                if !matches_scope
                    || !entry.is_launchable()
                    || self.kind.is_some_and(|kind| !entry.kinds.contains(&kind))
                {
                    return None;
                }
                Some((index, entry.score(&tokens)?))
            })
            .collect();
        if !tokens.is_empty()
            || self.scope == Scope::Recent
            || matches!(
                self.sort,
                ResultSort::Type | ResultSort::LastUsed | ResultSort::Modified | ResultSort::Size
            )
        {
            ranked.sort_unstable_by(|&(a, sa), &(b, sb)| {
                let first = &self.catalog.entries[a];
                let second = &self.catalog.entries[b];
                let by_recency = || second.metadata.last_run.cmp(&first.metadata.last_run);
                match self.sort {
                    ResultSort::Relevance => sb.cmp(&sa).then_with(|| {
                        if self.scope == Scope::Recent {
                            by_recency()
                        } else {
                            std::cmp::Ordering::Equal
                        }
                    }),
                    ResultSort::Name => a.cmp(&b),
                    ResultSort::Type => first.kinds[0].cmp(&second.kinds[0]),
                    ResultSort::LastUsed => by_recency(),
                    ResultSort::Modified => second.modified.cmp(&first.modified),
                    ResultSort::Size => second.file_size.cmp(&first.file_size),
                }
                .then_with(|| a.cmp(&b))
            });
        }
        self.results = ranked.into_iter().map(|(index, _)| index).collect();
        if reset {
            self.group_selection.clear();
        } else if !self.group_selection.is_empty() {
            let visible: HashSet<_> = self.results.iter().copied().collect();
            self.group_selection.retain(|id| visible.contains(id));
            if self.group_selection.len() == 1 {
                self.selected = self.group_selection.drain().next();
            }
        }
        if reset
            || self
                .selected
                .is_none_or(|index| !self.results.contains(&index))
        {
            self.selected = self.results.first().copied();
        }
        if reset {
            self.selection_anchor = self.selected;
        }
        if reset && !self.results.is_empty() {
            self.list.scroll_to_item_strict(0, ScrollStrategy::Top);
        }
        cx.notify();
    }

    fn focus_search(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.search.update(cx, |input, cx| input.focus(window, cx));
    }

    pub(crate) fn summon(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if !window.has_active_dialog(cx) && self.settings_page.is_none() && !self.guide {
            self.focus_search(window, cx);
        }
    }

    fn clear(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.scope = Scope::All;
        self.kind = None;
        self.search
            .update(cx, |input, cx| input.set_value("", window, cx));
        self.query.clear();
        self.rebuild(true, cx);
    }

    fn navigate(
        &mut self,
        forward: bool,
        step: usize,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.results.is_empty() {
            return;
        }
        let position = self
            .selected
            .and_then(|id| self.results.iter().position(|value| *value == id));
        let position = if !self.list_focus.is_focused(window) {
            position.unwrap_or(0)
        } else if forward {
            position
                .map(|ix| (ix + step).min(self.results.len() - 1))
                .unwrap_or(0)
        } else {
            position.unwrap_or(0).saturating_sub(step)
        };
        self.list_focus.focus(window);
        self.selected = Some(self.results[position]);
        self.selection_anchor = self.selected;
        self.group_selection.clear();
        self.list.scroll_to_item(
            position / self.columns,
            if forward {
                ScrollStrategy::Bottom
            } else {
                ScrollStrategy::Top
            },
        );
        cx.notify();
    }

    fn selected_entry(&self) -> Option<&catalog::Entry> {
        self.selected.and_then(|id| self.catalog.entries.get(id))
    }

    fn toggle_pin(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if !self.group_selection.is_empty() {
            self.pin_selection(None, window, cx);
            return;
        }
        let (Some(id), Some(settings)) = (self.selected, &self.settings) else {
            return;
        };
        let entry = &mut self.catalog.entries[id];
        let next = !entry.metadata.pinned;
        match Store::open(&settings.database()).and_then(|db| db.pin(entry.data_path(), next)) {
            Ok(()) => {
                entry
                    .metadata
                    .stored_path
                    .get_or_insert_with(|| entry.path.clone());
                entry.metadata.pinned = next;
                self.rebuild(false, cx);
            }
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    pub(super) fn remove_from_recent(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let ids = self.selected_ids();
        let Some(database) = self.settings.as_ref().map(Settings::database) else {
            return;
        };
        let paths: Vec<_> = ids
            .iter()
            .filter_map(|&id| {
                let entry = &self.catalog.entries[id];
                (!entry.metadata.last_run.is_empty() && !entry.metadata.recent_hidden)
                    .then(|| entry.data_path().to_path_buf())
            })
            .collect();
        if paths.is_empty() {
            return;
        }
        match Store::open(&database).and_then(|mut store| store.hide_recent(&paths)) {
            Ok(_) => {
                for &id in &ids {
                    let entry = &mut self.catalog.entries[id];
                    if !entry.metadata.last_run.is_empty() {
                        entry.metadata.recent_hidden = true;
                    }
                }
                if self.scope == Scope::Recent {
                    self.rebuild(false, cx);
                } else {
                    cx.notify();
                }
                window.push_notification(format!("已从最近中移除 {} 项", paths.len()), cx);
            }
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    pub(super) fn clear_recent(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(database) = self.settings.as_ref().map(Settings::database) else {
            return;
        };
        match Store::open(&database).and_then(|store| store.clear_recent()) {
            Ok(count) => {
                for entry in &mut self.catalog.entries {
                    if !entry.metadata.last_run.is_empty() {
                        entry.metadata.recent_hidden = true;
                    }
                }
                if self.scope == Scope::Recent {
                    self.rebuild(true, cx);
                } else {
                    cx.notify();
                }
                window.push_notification(
                    if count == 0 {
                        "最近使用已经是空的".to_owned()
                    } else {
                        format!("已清空最近使用 · {count} 项")
                    },
                    cx,
                );
            }
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    fn run_selected(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.group_selection.is_empty()
            && let Some(id) = self.selected
        {
            self.run_entry(id, None, false, None, window, cx);
        }
    }

    fn run_entry(
        &mut self,
        id: usize,
        args: Option<&str>,
        admin: bool,
        chosen: Option<&std::path::Path>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let choice = self.catalog.entries[id].metadata.chosen_entry.clone();
        let request = launch::Request {
            args: args.unwrap_or_default(),
            elevate: admin,
            chosen: chosen.or(choice.as_deref()),
            plan: None,
        };
        self.dispatch(id, request, args, window, cx);
    }

    fn dispatch(
        &mut self,
        id: usize,
        request: launch::Request<'_>,
        args: Option<&str>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(settings) = &self.settings else {
            return;
        };
        let entry = &mut self.catalog.entries[id];
        if let Err(error) = launch::run(entry, request, settings.terminal()) {
            Self::notify_error(error, window, cx);
            return;
        }
        match Store::open(&settings.database())
            .and_then(|mut db| db.record_run(entry.data_path(), args))
        {
            Ok(time) => {
                entry
                    .metadata
                    .stored_path
                    .get_or_insert_with(|| entry.path.clone());
                entry.metadata.runs = entry.metadata.runs.saturating_add(1);
                entry.metadata.last_run = time;
                entry.metadata.recent_hidden = false;
                if let Some(args) = args {
                    entry.metadata.last_args = args.to_owned();
                }
                window.push_notification(format!("已打开 {}", entry.name), cx);
                self.rebuild(false, cx);
            }
            Err(error) => {
                Self::notify_error(error.context("入口已打开，保存运行记录失败"), window, cx)
            }
        }
    }

    fn location(&self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(entry) = self.selected_entry()
            && let Err(error) = launch::open_location(entry)
        {
            Self::notify_error(error, window, cx);
        }
    }
}
