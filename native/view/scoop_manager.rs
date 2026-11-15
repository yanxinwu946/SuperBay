mod panels;

use std::{
    path::{Path, PathBuf},
    rc::Rc,
};

use anyhow::{Context as _, Result};
use gpui::{Bounds, TitlebarOptions, WindowBounds, WindowOptions, div, prelude::*, px, size};
use gpui_component::{
    ActiveTheme, Disableable, Root, Sizable, StyledExt, button::ButtonVariants, checkbox::Checkbox,
    h_flex, input::Input, scroll::Scrollbar, v_flex,
};

use super::{
    dialogs::{ConfirmDialog, dialog_frame},
    launch_settings::pick_path,
    *,
};
use crate::{
    scoop::{self, Operation, Version},
    storage::LaunchField,
};

#[derive(Default)]
struct Manager {
    inventory_root: Option<PathBuf>,
    versions: Vec<Version>,
    selected: Option<(String, String)>,
    multiple: HashSet<(String, String)>,
    anchor: Option<(String, String)>,
    results: Option<(Operation, Rc<[scoop::ObjectRow]>)>,
    result_root: Option<PathBuf>,
    result_checked: HashSet<usize>,
    busy: bool,
    output: String,
}

gpui::actions!(
    scoop_window,
    [
        MoveUp,
        MoveDown,
        CloseManager,
        FindPackage,
        RefreshPackages,
        SelectPackages
    ]
);

struct ScoopView {
    workspace: gpui::WeakEntity<Workspace>,
    entry: Option<PathBuf>,
    root: Entity<InputState>,
    query: Entity<InputState>,
    state: Entity<Manager>,
    scroll: UniformListScrollHandle,
    show_output: bool,
    all_versions: bool,
    runtime: Option<&'static str>,
    focus: FocusHandle,
}

impl ScoopView {
    fn new(
        workspace: gpui::WeakEntity<Workspace>,
        entry: Option<PathBuf>,
        root: Option<PathBuf>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        cx.bind_keys([
            KeyBinding::new("up", MoveUp, Some("Scoop && !Input")),
            KeyBinding::new("down", MoveDown, Some("Scoop")),
            KeyBinding::new("escape", CloseManager, Some("Scoop")),
            KeyBinding::new("ctrl-k", FindPackage, Some("Scoop")),
            KeyBinding::new("f5", RefreshPackages, Some("Scoop")),
            KeyBinding::new("ctrl-a", SelectPackages, Some("Scoop && !Input")),
        ]);
        let root_input = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(
                    root.as_ref()
                        .map(|path| path.to_string_lossy().into_owned())
                        .unwrap_or_default(),
                )
                .placeholder("Scoop 安装目录")
        });
        let query = cx.new(|cx| InputState::new(window, cx).placeholder("筛选名称、版本或来源"));
        let state = cx.new(|_| Manager::default());
        let focus = cx.focus_handle();
        cx.observe(&state, |_, _, cx| cx.notify()).detach();
        cx.subscribe_in(&root_input, window, |_, _, event, _, cx| {
            if matches!(event, InputEvent::Change) {
                cx.notify();
            }
        })
        .detach();
        cx.subscribe_in(&query, window, |this, _, event, _, cx| {
            if matches!(event, InputEvent::Change) {
                this.scroll.scroll_to_item_strict(0, ScrollStrategy::Top);
                this.state.update(cx, |manager, _| manager.anchor = None);
                cx.notify();
            }
        })
        .detach();
        cx.on_next_frame(window, move |this, window, cx| {
            this.query.update(cx, |input, cx| input.focus(window, cx));
            if let Some(root) = root {
                perform(this.state.clone(), root, None, window, cx);
            }
        });
        Self {
            workspace,
            entry,
            root: root_input,
            query,
            state,
            scroll: UniformListScrollHandle::new(),
            show_output: false,
            all_versions: false,
            runtime: None,
            focus,
        }
    }

    fn visible(&self, cx: &App) -> Vec<usize> {
        let query = self.query.read(cx).value().to_ascii_lowercase();
        let tokens: Vec<_> = query.split_whitespace().collect();
        self.state
            .read(cx)
            .versions
            .iter()
            .enumerate()
            .filter_map(|(id, item)| {
                if (!self.all_versions && !item.current)
                    || self
                        .runtime
                        .is_some_and(|runtime| !item.families.contains(&runtime))
                {
                    return None;
                }
                if tokens.is_empty() {
                    return Some(id);
                }
                let fields = [&item.package, &item.version, &item.bucket]
                    .map(|field| field.to_ascii_lowercase());
                tokens
                    .iter()
                    .all(|token| {
                        fields.iter().any(|field| field.contains(token))
                            || item
                                .families
                                .iter()
                                .any(|family| family.to_ascii_lowercase().contains(token))
                    })
                    .then_some(id)
            })
            .collect()
    }

    fn navigate(&self, step: isize, cx: &mut Context<Self>) {
        let visible = self.visible(cx);
        let manager = self.state.read(cx);
        let position = visible
            .iter()
            .position(|&id| {
                let item = &manager.versions[id];
                manager.selected.as_ref().is_some_and(|(package, version)| {
                    *package == item.package && *version == item.version
                })
            })
            .map_or(0, |at| {
                at.saturating_add_signed(step)
                    .min(visible.len().saturating_sub(1))
            });
        if let Some(&id) = visible.get(position) {
            let item = &manager.versions[id];
            let key = (item.package.clone(), item.version.clone());
            self.state.update(cx, |manager, cx| {
                manager.anchor = Some(key.clone());
                manager.selected = Some(key);
                cx.notify();
            });
            self.scroll.scroll_to_item(
                position,
                if step < 0 {
                    ScrollStrategy::Top
                } else {
                    ScrollStrategy::Bottom
                },
            );
        }
    }

    fn select_packages(&self, cx: &mut Context<Self>) {
        let visible = self.visible(cx);
        self.state.update(cx, |manager, cx| {
            let keys: Vec<_> = visible
                .into_iter()
                .map(|id| version_key(&manager.versions[id]))
                .collect();
            if !keys.is_empty() && keys.iter().all(|key| manager.multiple.contains(key)) {
                for key in keys {
                    manager.multiple.remove(&key);
                }
            } else {
                manager.multiple.extend(keys);
            }
            cx.notify();
        });
    }

    fn change_version_filter(&mut self, all_versions: bool, cx: &mut Context<Self>) {
        if self.all_versions == all_versions && self.state.read(cx).results.is_none() {
            return;
        }
        self.all_versions = all_versions;
        self.refresh_filter(cx);
    }

    fn refresh_filter(&mut self, cx: &mut Context<Self>) {
        let first = self
            .visible(cx)
            .first()
            .map(|&id| version_key(&self.state.read(cx).versions[id]));
        self.state.update(cx, |manager, cx| {
            manager.results = None;
            manager.multiple.clear();
            manager.anchor = first.clone();
            manager.selected = first;
            cx.notify();
        });
        self.scroll.scroll_to_item_strict(0, ScrollStrategy::Top);
        cx.notify();
    }

    fn invoke_selected(
        &mut self,
        operation: Operation,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let manager = self.state.read(cx);
        let root = input_root(&self.root, cx);
        if manager.inventory_root.as_ref() != Some(&root) {
            return;
        }
        let values = batch_targets(manager, operation);
        self.invoke_many(operation, values, window, cx);
    }

    fn connect(&self, window: &mut Window, cx: &mut Context<Self>) {
        let root = input_root(&self.root, cx);
        if !scoop::script(&root).is_file() {
            Workspace::notify_error(anyhow::anyhow!("此目录没有可用的 Scoop 安装"), window, cx);
            return;
        }
        if let Err(error) = self
            .workspace
            .update(cx, |this, _| {
                this.settings
                    .as_mut()
                    .context("设置不可用")?
                    .set_scoop_root(&root)
            })
            .and_then(|result| result)
        {
            Workspace::notify_error(error, window, cx);
            return;
        }
        perform(self.state.clone(), root, None, window, cx);
    }

    fn invoke(
        &mut self,
        operation: Operation,
        value: String,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.invoke_many(operation, vec![value], window, cx);
    }

    fn invoke_many(
        &mut self,
        operation: Operation,
        values: Vec<String>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if values.is_empty() || self.state.read(cx).busy {
            return;
        }
        let root = input_root(&self.root, cx);
        self.show_output = false;
        cx.notify();
        if matches!(operation, Operation::Uninstall | Operation::Cleanup) {
            let state = self.state.clone();
            window.open_dialog(cx, move |dialog, window, _| {
                let (state, root, values) = (state.clone(), root.clone(), values.clone());
                let description = if operation == Operation::Uninstall {
                    "将卸载所选软件并保留持久化数据。"
                } else {
                    "将删除所选软件的历史版本并保留当前版本。"
                };
                dialog_frame(dialog, window)
                    .title(format!("{} · {} 个软件", operation.label(), values.len()))
                    .child(
                        v_flex().gap_2().child(description).children(
                            values
                                .iter()
                                .map(|name| div().text_sm().child(name.clone())),
                        ),
                    )
                    .confirm_with(operation.label(), move |_, window, cx| {
                        perform(
                            state.clone(),
                            root.clone(),
                            Some((operation, values.clone())),
                            window,
                            cx,
                        );
                        true
                    })
            });
        } else {
            perform(
                self.state.clone(),
                root,
                Some((operation, values)),
                window,
                cx,
            );
        }
    }
}

const BATCH_ACTIONS: [Operation; 6] = [
    Operation::Update,
    Operation::Hold,
    Operation::Unhold,
    Operation::Reset,
    Operation::Cleanup,
    Operation::Uninstall,
];

fn batch_targets(manager: &Manager, operation: Operation) -> Vec<String> {
    if operation == Operation::Reset {
        let mut choices = std::collections::BTreeMap::new();
        for item in &manager.versions {
            if manager.multiple.contains(&version_key(item)) {
                let selection = choices.entry(&item.package).or_insert((0, item));
                selection.0 += 1;
            }
        }
        return choices
            .into_values()
            .filter_map(|(count, item)| {
                (count == 1 && !item.current).then(|| format!("{}@{}", item.package, item.version))
            })
            .collect();
    }
    let picked: HashSet<_> = manager
        .multiple
        .iter()
        .map(|(package, _)| package.as_str())
        .collect();
    let history: HashSet<_> = if operation == Operation::Cleanup {
        manager
            .versions
            .iter()
            .filter(|item| !item.current)
            .map(|item| item.package.as_str())
            .collect()
    } else {
        HashSet::new()
    };
    manager
        .versions
        .iter()
        .filter(|item| item.current && picked.contains(item.package.as_str()))
        .filter(|item| match operation {
            Operation::Update | Operation::Hold => !item.held,
            Operation::Unhold => item.held,
            Operation::Cleanup => history.contains(item.package.as_str()),
            _ => true,
        })
        .map(|item| item.package.clone())
        .collect()
}

fn version_key(item: &Version) -> (String, String) {
    (item.package.clone(), item.version.clone())
}

fn select_version(
    manager: &mut Manager,
    visible: &[usize],
    row: usize,
    modifiers: gpui::Modifiers,
) {
    let Some(&id) = visible.get(row) else {
        return;
    };
    let key = version_key(&manager.versions[id]);
    if modifiers.shift {
        let anchor = manager.anchor.as_ref().or(manager.selected.as_ref());
        let start = anchor
            .and_then(|anchor| {
                visible.iter().position(|&id| {
                    let item = &manager.versions[id];
                    anchor.0 == item.package && anchor.1 == item.version
                })
            })
            .unwrap_or(row);
        if !modifiers.control {
            manager.multiple.clear();
        }
        manager.multiple.extend(
            visible[start.min(row)..=start.max(row)]
                .iter()
                .map(|&id| version_key(&manager.versions[id])),
        );
    } else if modifiers.control {
        if !manager.multiple.remove(&key) {
            manager.multiple.insert(key.clone());
        }
        manager.anchor = Some(key.clone());
    } else {
        manager.anchor = Some(key.clone());
    }
    manager.selected = Some(key);
}

fn columns() -> gpui::Div {
    h_flex().w_full().gap_3().pl_4().pr_6()
}

fn cell(content: impl Into<gpui::SharedString>, width: f32) -> gpui::Div {
    div()
        .w(px(width))
        .flex_shrink_0()
        .truncate()
        .child(content.into())
}

impl Render for ScoopView {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let theme = cx.theme();
        let (bg, line, muted, inset, selected_bg, accent) = (
            theme.background,
            theme.border,
            theme.muted_foreground,
            theme.muted,
            theme.accent,
            theme.accent_foreground,
        );
        let manager = self.state.read(cx);
        let busy = manager.busy;
        let output = manager.output.clone();
        let visible = self.visible(cx);
        let count = visible.len();
        let total = manager
            .versions
            .iter()
            .filter(|item| self.all_versions || item.current)
            .count();
        let batch_count = manager.multiple.len();
        let batch_packages = manager
            .multiple
            .iter()
            .map(|(package, _)| package)
            .collect::<HashSet<_>>()
            .len();
        let has_results = manager.results.is_some();
        let result_summary = manager
            .results
            .as_ref()
            .map(|(operation, rows)| (operation.label(), rows.len()));
        let root = input_root(&self.root, cx);
        let connected = scoop::script(&root).is_file();
        let inventory_ready = connected && manager.inventory_root.as_ref() == Some(&root);
        let batch_availability = BATCH_ACTIONS
            .map(|operation| batch_count > 0 && !batch_targets(manager, operation).is_empty());
        let all_checked = count > 0
            && visible.iter().all(|&id| {
                manager
                    .multiple
                    .contains(&version_key(&manager.versions[id]))
            });
        let visible = Rc::new(visible);
        let selected = manager.selected.as_ref().and_then(|(package, version)| {
            visible
                .iter()
                .map(|&index| &manager.versions[index])
                .find(|item| item.package == *package && item.version == *version)
                .cloned()
        });
        let state = self.state.clone();
        let list_focus = self.focus.clone();
        let picker = self.root.clone();
        let dialogs = Root::render_dialog_layer(window, cx);
        let notifications = Root::render_notification_layer(window, cx);
        v_flex()
            .id("scoop-window")
            .key_context("Scoop")
            .track_focus(&self.focus)
            .relative()
            .size_full()
            .overflow_hidden()
            .bg(bg)
            .on_action(cx.listener(|this, _: &MoveUp, _, cx| this.navigate(-1, cx)))
            .on_action(cx.listener(|this, _: &SelectPackages, window, cx| {
                if !window.has_active_dialog(cx) && !this.state.read(cx).busy {
                    if this.state.read(cx).results.is_some() { this.select_all_results(cx); }
                    else { this.select_packages(cx); }
                }
            }))
            .on_action(cx.listener(|this, _: &MoveDown, window, cx| {
                if !window.has_active_dialog(cx) && this.state.read(cx).results.is_none() {
                    this.navigate(1, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &CloseManager, window, cx| {
                if window.has_active_dialog(cx) {
                    window.close_dialog(cx);
                } else if this.show_output {
                    this.show_output = false;
                    cx.notify();
                } else if this.state.read(cx).results.is_some() {
                    this.state.update(cx, |manager, cx| { manager.results = None; cx.notify(); });
                } else if !this.state.read(cx).multiple.is_empty() {
                    this.state.update(cx, |manager, cx| { manager.multiple.clear(); cx.notify(); });
                } else if !this.query.read(cx).value().is_empty() {
                    this.query
                        .update(cx, |input, cx| input.set_value("", window, cx));
                } else if !this.state.read(cx).busy {
                    window.remove_window();
                }
            }))
            .on_action(cx.listener(|this, _: &FindPackage, window, cx| {
                this.query.update(cx, |input, cx| input.focus(window, cx));
            }))
            .on_action(
                cx.listener(|this, _: &RefreshPackages, window, cx| this.connect(window, cx)),
            )
            .child(
                h_flex()
                    .px_5()
                    .py_3()
                    .gap_2()
                    .border_b_1()
                    .border_color(line)
                    .child(div().text_sm().font_semibold().child("Scoop 安装位置"))
                    .child(div().flex_1().min_w_0().child(Input::new(&self.root).disabled(busy)))
                    .child(
                        button("scoop-browse")
                            .outline()
                            .label("浏览")
                            .disabled(busy)
                            .on_click(move |_, window, cx| pick_path(&picker, true, window, cx)),
                    )
                    .child(
                        button("scoop-connect")
                            .primary()
                            .label("连接")
                            .disabled(busy)
                            .on_click(cx.listener(|this, _, window, cx| this.connect(window, cx))),
                    )
                    .child(button("scoop-settings").ghost().label("设置").disabled(busy || !connected)
                        .on_click(cx.listener(|this, _, window, cx| this.settings_panel(window, cx))))
                    .child(button("scoop-buckets").ghost().label("软件源").disabled(busy || !connected)
                        .on_click(cx.listener(|this, _, window, cx| this.buckets_panel(window, cx))))
                    .when(!connected, |bar| bar.child(button("scoop-setup").outline().label("安装 Scoop").disabled(busy)
                        .on_click(cx.listener(|this, _, window, cx| this.install_panel(window, cx))))),
            )
            .child(
                h_flex()
                    .px_5()
                    .py_3()
                    .gap_2()
                    .border_b_1()
                    .border_color(line)
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .child(Input::new(&self.query).cleanable(true)),
                    )
                    .children(
                        [Operation::Search, Operation::Install, Operation::Status, Operation::Sync].map(
                            |operation| {
                                button(operation.label())
                                    .outline()
                                    .label(operation.label())
                                    .disabled(busy || !connected)
                                    .on_click(cx.listener(move |this, _, window, cx| {
                                        let value = this.query.read(cx).value().trim().to_owned();
                                        this.invoke(operation, value, window, cx);
                                    }))
                            },
                        ),
                    ),
            )
            .child(h_flex().px_5().py_2().gap_2().border_b_1().border_color(line)
                .children([(false, "当前版本"), (true, "所有版本")].map(|(all, label)| {
                    let button = button(label).small().label(label);
                    (if all == self.all_versions && !has_results { button.primary() } else { button.ghost() })
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.change_version_filter(all, cx);
                        }))
                }))
                .when(!has_results, |bar| bar
                    .child(div().w(px(1.)).h(px(20.)).bg(line).mx_1())
                    .children([(None, "全部软件"), (Some("Python"), "Python"), (Some("Node"), "Node.js"), (Some("Java"), "Java")].map(|(runtime, label)| {
                        let toggle = button(label).small().label(label);
                        (if self.runtime == runtime { toggle.primary() } else { toggle.ghost() })
                            .on_click(cx.listener(move |this, _, _, cx| {
                                if this.runtime != runtime {
                                    this.runtime = runtime;
                                    this.refresh_filter(cx);
                                }
                            }))
                    })))
                .when(has_results, |bar| bar.child(div().text_sm().text_color(accent).child("命令结果")))
            )
            .when(!has_results, |page| page.child(h_flex().px_5().py_2().gap_2().flex_wrap().border_b_1().border_color(line)
                .child(div().text_sm().font_semibold().child("批量管理"))
                .child(div().text_xs().text_color(muted).child(format!("已选 {batch_packages} 个软件")))
                .child(button("clear-batch").ghost().small().label("清空").disabled(busy || batch_count == 0)
                    .on_click(cx.listener(|this, _, _, cx| this.state.update(cx, |manager, cx| { manager.multiple.clear(); manager.anchor = None; cx.notify(); }))))
                .children(BATCH_ACTIONS.into_iter().zip(batch_availability).map(|(operation, available)| {
                    button(operation.label()).outline().small().label(operation.label())
                        .disabled(busy || !inventory_ready || !available)
                        .when(operation == Operation::Reset, |button| button.tooltip("在所有版本中，每个软件勾选一个历史版本"))
                        .on_click(cx.listener(move |this, _, window, cx| this.invoke_selected(operation, window, cx)))
                }))))
            .when(has_results, |page| page.child(self.result_table(cx)))
            .when(!has_results, |page| page.child(
                div()
                    .flex()
                    .flex_1()
                    .min_h_0()
                    .w_full()
                    .child(
                        v_flex()
                            .min_w_0()
                            .min_h_0()
                            .flex_1()
                            .relative()
                            .child(
                                columns()
                                    .h(px(36.))
                                    .flex_shrink_0()
                                    .bg(inset)
                                    .text_xs()
                                    .text_color(muted)
                                    .child(div().w(px(22.)).flex_shrink_0().child(Checkbox::new("scoop-check-all").checked(all_checked).disabled(busy || count == 0 || !inventory_ready)
                                        .on_click(cx.listener(|this, _, window, cx| { this.focus.focus(window); this.select_packages(cx); }))))
                                    .child(div().flex_1().min_w_0().child("软件"))
                                    .child(cell("版本", 120.))
                                    .child(cell("来源", 82.))
                                    .child(cell("状态", 72.)),
                            )
                            .when(count == 0, |table| {
                                table.child(div().p_5().text_sm().text_color(muted).child(
                                    if busy {
                                        "正在读取已安装软件…"
                                    } else if total > 0 {
                                        "没有匹配的软件"
                                    } else {
                                        "没有可显示的软件，请确认 Scoop 安装位置。"
                                    },
                                ))
                            })
                            .when(count > 0, |table| {
                                table
                                    .child(
                                        gpui::uniform_list(
                                            "scoop-inventory",
                                            count,
                                            move |range, _, cx| {
                                                let manager = state.read(cx);
                                                let focus = &list_focus;
                                                range
                                                    .filter_map(|row| {
                                                        let item = manager
                                                            .versions
                                                            .get(*visible.get(row)?)?;
                                                        let (package, version) = (
                                                            item.package.clone(),
                                                            item.version.clone(),
                                                        );
                                                        let key = version_key(item);
                                                        let checked = manager.multiple.contains(&key);
                                                        let active = checked || manager.selected.as_ref() == Some(&key);
                                                        let key_for_check = key.clone();
                                                        let checkbox_state = state.clone();
                                                        let checkbox_focus = focus.clone();
                                                        let row_focus = focus.clone();
                                                        let state = state.clone();
                                                        let visible = visible.clone();
                                                        Some(
                                                            columns()
                                                                .id(("scoop-row", row))
                                                                .h(px(40.))
                                                                .border_b_1()
                                                                .border_color(line)
                                                                .cursor_pointer()
                                                                .hover(|row| row.bg(inset))
                                                                .when(active, |row| {
                                                                    row.bg(selected_bg)
                                                                })
                                                                .child(div().w(px(22.)).flex_shrink_0().child(
                                                                    Checkbox::new(("scoop-check", row)).checked(checked).disabled(busy || !inventory_ready)
                                                                        .on_click(move |checked, window, cx| {
                                                                            cx.stop_propagation();
                                                                            checkbox_focus.focus(window);
                                                                            checkbox_state.update(cx, |manager, cx| {
                                                                                if *checked { manager.multiple.insert(key_for_check.clone()); }
                                                                                else { manager.multiple.remove(&key_for_check); }
                                                                                manager.selected = Some(key_for_check.clone());
                                                                                manager.anchor = Some(key_for_check.clone());
                                                                                cx.notify();
                                                                            });
                                                                        })
                                                                ))
                                                                .child(
                                                                    div()
                                                                        .flex_1()
                                                                        .min_w_0()
                                                                        .truncate()
                                                                        .text_sm()
                                                                        .font_semibold()
                                                                        .child(package.clone()),
                                                                )
                                                                .child(
                                                                    cell(version.clone(), 120.)
                                                                        .text_sm(),
                                                                )
                                                                .child(
                                                                    cell(
                                                                        if item.bucket.is_empty() {
                                                                            "—".into()
                                                                        } else {
                                                                            item.bucket.clone()
                                                                        },
                                                                        82.,
                                                                    )
                                                                    .text_sm()
                                                                    .text_color(muted),
                                                                )
                                                                .child(
                                                                    cell(
                                                                        if item.held { "已冻结" } else if item.current {
                                                                            "当前"
                                                                        } else {
                                                                            "已安装"
                                                                        },
                                                                        72.,
                                                                    )
                                                                    .text_xs()
                                                                    .text_color(if item.current {
                                                                        accent
                                                                    } else {
                                                                        muted
                                                                    }),
                                                                )
                                                                .on_click(move |event, window, cx| {
                                                                    if busy { return; }
                                                                    row_focus.focus(window);
                                                                    state.update(
                                                                        cx,
                                                                        |manager, cx| {
                                                                            select_version(manager, &visible, row, event.modifiers());
                                                                            cx.notify();
                                                                        },
                                                                    )
                                                                }),
                                                        )
                                                    })
                                                    .collect()
                                            },
                                        )
                                        .track_scroll(self.scroll.clone())
                                        .flex_1()
                                        .min_h_0()
                                        .w_full(),
                                    )
                                    .child(Scrollbar::vertical(&self.scroll))
                            }),
                    )
                    .child(
                        v_flex()
                            .id("scoop-inspector")
                            .w(px(228.))
                            .min_h_0()
                            .flex_shrink_0()
                            .overflow_y_scroll()
                            .p_4()
                            .gap_2()
                            .border_l_1()
                            .border_color(line)
                            .map(|panel| {
                                let Some(version) = selected else {
                                    return panel
                                        .text_sm()
                                        .text_color(muted)
                                        .child("选择软件查看操作");
                                };
                                let package_held = self.state.read(cx).versions.iter().any(|item| item.package == version.package && item.current && item.held);
                                let has_history = self.state.read(cx).versions.iter().any(|item| item.package == version.package && !item.current);
                                let mut panel = panel
                                    .child(
                                        div()
                                            .text_lg()
                                            .font_semibold()
                                            .truncate()
                                            .child(version.package.clone()),
                                    )
                                    .child(div().text_sm().text_color(muted).child(format!(
                                        "{} · {}",
                                        version.version,
                                        if version.current {
                                            "当前版本"
                                        } else {
                                            "已安装版本"
                                        }
                                    )));
                                for operation in [
                                    Operation::Update,
                                    Operation::Reset,
                                    Operation::Hold,
                                    Operation::Unhold,
                                    Operation::Cleanup,
                                    Operation::Uninstall,
                                ] {
                                    let value = if matches!(operation, Operation::Reset) {
                                        format!("{}@{}", version.package, version.version)
                                    } else {
                                        version.package.clone()
                                    };
                                    panel = panel.child(
                                        button(operation.label())
                                            .outline()
                                            .small()
                                            .justify_start()
                                            .label(operation.label())
                                            .disabled(
                                                busy || !inventory_ready || match operation {
                                                    Operation::Reset => version.current,
                                                    Operation::Update | Operation::Hold => package_held,
                                                    Operation::Unhold => !package_held,
                                                    Operation::Cleanup => !has_history,
                                                    _ => false,
                                                },
                                            )
                                            .on_click(cx.listener(move |this, _, window, cx| {
                                                this.invoke(operation, value.clone(), window, cx)
                                            })),
                                    );
                                }
                                if let Some(path) = &self.entry {
                                    for &family in &version.families {
                                        let path = path.clone();
                                        let version = version.clone();
                                        panel = panel.child(
                                            button(family)
                                                .ghost()
                                                .small()
                                                .justify_start()
                                                .label(format!("绑定 {family} 到条目"))
                                            .disabled(busy || !inventory_ready)
                                                .on_click(cx.listener(
                                                    move |this, _, window, cx| {
                                                        let root = input_root(&this.root, cx);
                                                        match this
                                                            .workspace
                                                            .update(cx, |workspace, cx| {
                                                                workspace.bind_scoop(
                                                                    &path, &root, family, &version,
                                                                    cx,
                                                                )
                                                            })
                                                            .and_then(|result| result)
                                                        {
                                                            Ok(()) => window.push_notification(
                                                                format!(
                                                                    "已绑定 {family} {}",
                                                                    version.version
                                                                ),
                                                                cx,
                                                            ),
                                                            Err(error) => Workspace::notify_error(
                                                                error, window, cx,
                                                            ),
                                                        }
                                                    },
                                                )),
                                        );
                                    }
                                }
                                panel
                            }),
                    ),
            ))
            .when(self.show_output && !output.is_empty(), |page| {
                page.child(
                    div()
                        .id("scoop-output")
                        .max_h(px(145.))
                        .overflow_y_scroll()
                        .p_3()
                        .text_sm()
                        .text_color(muted)
                        .bg(inset)
                        .border_t_1()
                        .border_color(line)
                        .child(output.clone()),
                )
            })
            .child(
                h_flex()
                    .h(px(40.))
                    .px_4()
                    .gap_2()
                    .border_t_1()
                    .border_color(line)
                    .text_xs()
                    .text_color(muted)
                    .child(div().flex_1().child(if busy {
                        "Scoop 正在执行…".into()
                    } else if let Some((label, count)) = result_summary {
                        format!("{label} · {count} 项")
                    } else {
                        format!("{count} / {total} 个{}", if self.all_versions { "已安装版本" } else { "当前版本" })
                    }))
                    .when(!output.is_empty(), |bar| {
                        bar.child(
                            button("scoop-log")
                                .ghost()
                                .small()
                                .label(if self.show_output {
                                    "收起输出"
                                } else {
                                    "查看输出"
                                })
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.show_output = !this.show_output;
                                    cx.notify();
                                })),
                        )
                    }),
            )
            .children(dialogs)
            .children(notifications)
    }
}

impl Workspace {
    pub(super) fn scoop_manager(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(handle) = self.scoop_window
            && handle
                .update(cx, |_, window, _| window.activate_window())
                .is_ok()
        {
            return;
        }
        let Some(settings) = &self.settings else {
            return;
        };
        let root = scoop::detect(settings.scoop_root());
        let entry = self.selected_entry().map(|entry| entry.path.clone());
        let workspace = cx.weak_entity();
        let display = window.display(cx);
        let display_id = display.as_ref().map(|display| display.id());
        let area = display
            .as_ref()
            .map(|display| display.bounds().size)
            .unwrap_or(size(px(1160.), px(760.)));
        let preferred = size(
            px(1120.).min(area.width - px(48.)),
            px(740.).min(area.height - px(84.)),
        );
        let bounds = Bounds::centered(display_id, preferred, cx);
        match cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                titlebar: Some(TitlebarOptions {
                    title: Some("Scoop 管理 · SuperBay".into()),
                    ..Default::default()
                }),
                display_id,
                window_min_size: Some(size(
                    px(780.).min(preferred.width),
                    px(500.).min(preferred.height),
                )),
                is_resizable: true,
                ..Default::default()
            },
            move |window, cx| {
                let view = cx.new(|cx| ScoopView::new(workspace, entry, root, window, cx));
                let manager = view.read(cx).state.clone();
                window.on_window_should_close(cx, move |window, cx| {
                    if manager.read(cx).busy {
                        window.push_notification("Scoop 操作完成后即可关闭", cx);
                        false
                    } else {
                        true
                    }
                });
                cx.new(|cx| Root::new(view, window, cx))
            },
        ) {
            Ok(handle) => self.scoop_window = Some(handle),
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    fn bind_scoop(
        &mut self,
        path: &Path,
        root: &Path,
        family: &str,
        version: &Version,
        cx: &mut Context<Self>,
    ) -> Result<()> {
        let index = self.index_for_path(path).context("条目已离开工作区")?;
        let entry = &mut self.catalog.entries[index];
        let settings = self.settings.as_ref().context("设置不可用")?;
        let mut profile = if entry.metadata.runtime["root"]
            .as_str()
            .is_some_and(|existing| {
                crate::storage::path_key(existing)
                    == crate::storage::path_key(&root.to_string_lossy())
            }) {
            entry.metadata.runtime.clone()
        } else {
            serde_json::json!({"root": root, "versions": {}})
        };
        profile["versions"][family] =
            serde_json::json!({"package": version.package, "version": version.version});
        launch::validate_runtime(&profile)?;
        Store::open(&settings.database())?.save_launch_field(
            entry.data_path(),
            LaunchField::Runtime,
            serde_json::to_string(&profile)?,
        )?;
        entry.metadata.runtime = profile;
        cx.notify();
        Ok(())
    }
}

fn input_root(input: &Entity<InputState>, cx: &App) -> PathBuf {
    PathBuf::from(input.read(cx).value().trim().trim_matches('"'))
}

fn perform(
    state: Entity<Manager>,
    root: PathBuf,
    operation: Option<(Operation, Vec<String>)>,
    window: &mut Window,
    cx: &mut App,
) {
    if state.read(cx).busy {
        return;
    }
    if !scoop::script(&root).is_file() {
        Workspace::notify_error(anyhow::anyhow!("请先选择 Scoop 安装"), window, cx);
        return;
    }
    state.update(cx, |state, cx| {
        state.busy = true;
        if state.inventory_root.as_ref() != Some(&root) {
            state.versions.clear();
            state.multiple.clear();
            state.selected = None;
            state.anchor = None;
        }
        cx.notify();
    });
    window.refresh();
    let task = cx.background_executor().spawn(async move {
        let read_only = operation.as_ref().is_some_and(|(operation, _)| {
            matches!(operation, Operation::Status | Operation::Search)
        });
        let output = operation.map(|(operation, values)| {
            if matches!(operation, Operation::Status | Operation::Search) {
                return scoop::objects(&root, operation, values.first().map_or("", String::as_str))
                    .map(|(rows, details)| (operation, rows, details));
            }
            scoop::execute_many(&root, operation, &values)
                .map(|(rows, details)| (operation, rows, details))
        });
        (output, (!read_only).then(|| scoop::inventory(&root)), root)
    });
    window
        .spawn(cx, async move |cx| {
            let (output, versions, root) = task.await;
            let _ = cx.update(|window, cx| {
                state.update(cx, |state, cx| {
                    state.busy = false;
                    state.results = None;
                    state.result_root = None;
                    state.result_checked.clear();
                    state.output = match output {
                        Some(Ok((operation, rows, details))) => {
                            state.results = Some((operation, rows.into()));
                            state.result_root = Some(root.clone());
                            details
                        }
                        Some(Err(error)) => {
                            let message = format!("{error:#}");
                            window.push_notification(message.clone(), cx);
                            message
                        }
                        None => String::new(),
                    };
                    match versions {
                        Some(Ok(versions)) => {
                            if !state.selected.as_ref().is_some_and(|(package, version)| {
                                versions.iter().any(|item| {
                                    *package == item.package && *version == item.version
                                })
                            }) {
                                state.selected = versions
                                    .iter()
                                    .find(|item| item.current)
                                    .or_else(|| versions.first())
                                    .map(|item| (item.package.clone(), item.version.clone()));
                            }
                            state.versions = versions;
                            state.inventory_root = Some(root);
                            state.multiple.clear();
                            state.anchor = state.selected.clone();
                        }
                        Some(Err(error)) => {
                            state.versions.clear();
                            state.inventory_root = None;
                            state.output.push_str(&format!("\n{error:#}"));
                        }
                        None => {}
                    }
                    cx.notify();
                });
                window.refresh();
            });
        })
        .detach();
}
