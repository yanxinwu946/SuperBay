use gpui::{
    App, Context, Corner, Div, ElementId, Entity, Hsla, Stateful, Window, div, linear_color_stop,
    linear_gradient, prelude::*, px, rgb, uniform_list,
};
use gpui_component::{
    ActiveTheme, Icon, IconName, Root, Sizable, StyledExt, Theme,
    button::ButtonVariants,
    h_flex,
    input::Input,
    menu::{ContextMenuExt, PopupMenuItem},
    popover::Popover,
    scroll::Scrollbar,
    tooltip::Tooltip,
    v_flex,
};

use super::*;

#[derive(Clone, Copy)]
pub(super) struct Palette {
    base: Hsla,
    sidebar: Hsla,
    surface: Hsla,
    inset: Hsla,
    line: Hsla,
    ink: Hsla,
    muted: Hsla,
    accent: Hsla,
    selection: Hsla,
    primary: Hsla,
    on_primary: Hsla,
}

impl Palette {
    fn current(cx: &App) -> Self {
        let colors = if cx.theme().is_dark() {
            [
                0x121218, 0x191920, 0x202028, 0x282830, 0x31313c, 0xf4f2f8, 0xa6a4b6, 0xc2b5ff,
                0x302947, 0xb5a6ff, 0x21173d,
            ]
        } else {
            [
                0xf8f7fc, 0xefedf6, 0xffffff, 0xf2f1f7, 0xe1deeb, 0x241e35, 0x6f6a80, 0x6242c4,
                0xede7ff, 0x6040c7, 0xffffff,
            ]
        };
        let [
            base,
            sidebar,
            surface,
            inset,
            line,
            ink,
            muted,
            accent,
            selection,
            primary,
            on_primary,
        ] = colors.map(|color| rgb(color).into());
        Self {
            base,
            sidebar,
            surface,
            inset,
            line,
            ink,
            muted,
            accent,
            selection,
            primary,
            on_primary,
        }
    }

    pub(super) fn apply(cx: &mut App) {
        let p = Self::current(cx);
        let theme = Theme::global_mut(cx);
        theme.radius = px(8.);
        theme.radius_lg = px(16.);
        theme.colors.background = p.surface;
        theme.colors.foreground = p.ink;
        theme.colors.muted_foreground = p.muted;
        theme.colors.input = p.line;
        theme.colors.border = p.line;
        theme.colors.ring = p.accent;
        theme.colors.primary = p.primary;
        theme.colors.primary_hover = p.primary.opacity(0.88);
        theme.colors.primary_active = p.primary.opacity(0.8);
        theme.colors.primary_foreground = p.on_primary;
        theme.colors.popover = p.surface;
        theme.colors.popover_foreground = p.ink;
        theme.colors.accent = p.selection;
        theme.colors.accent_foreground = p.accent;
    }
}

impl Render for Workspace {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let p = Palette::current(cx);
        let compact = window.viewport_size().width < px(1050.);
        if !self.grid {
            self.update_grid_columns(1);
        }
        if self.details && self.group_selection.is_empty() {
            let selected = self
                .selected_entry()
                .filter(|entry| entry.resource.is_none())
                .map(|entry| entry.path.clone());
            if self.launch_preview.needs_refresh(selected.as_deref()) {
                cx.on_next_frame(window, |this, window, cx| {
                    this.update_launch_preview(window, cx)
                });
            }
            if self.note_editor_pending() {
                cx.on_next_frame(window, |this, window, cx| {
                    this.prepare_selected_note(window, cx)
                });
            }
        }
        let dialogs = Root::render_dialog_layer(window, cx);
        let notifications = Root::render_notification_layer(window, cx);
        if self.guide {
            return div()
                .id("guide-page")
                .relative()
                .size_full()
                .key_context(if self.settings_page.is_some() {
                    "SuperBayOverlay"
                } else {
                    "SuperBay"
                })
                .track_focus(&self.list_focus)
                .on_action(cx.listener(|this, _: &OpenSettings, window, cx| {
                    this.toggle_settings(window, cx)
                }))
                .on_action(cx.listener(|this, _: &EscapeWorkspace, window, cx| {
                    this.escape_workspace(window, cx)
                }))
                .child(self.guide_content(cx))
                .when_some(settings_overlay(self, window, cx), |root, overlay| {
                    root.child(overlay)
                })
                .children(dialogs)
                .children(notifications);
        }
        div()
            .id("superbay")
            .relative()
            .size_full()
            .flex()
            .overflow_hidden()
            .key_context(if self.settings_page.is_some() {
                "SuperBayOverlay"
            } else {
                "SuperBay"
            })
            .font_family(Theme::global(cx).font_family.clone())
            .text_color(p.ink)
            .bg(p.base)
            .on_action(cx.listener(|this, _: &OpenSettings, window, cx| {
                if !window.has_active_dialog(cx) {
                    this.toggle_settings(window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &Find, window, cx| {
                if !window.has_active_dialog(cx) {
                    this.focus_search(window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &Refresh, window, cx| {
                if this.watcher.is_none() {
                    this.watch(window, cx);
                }
                this.refresh(window, cx);
            }))
            .on_action(cx.listener(|this, _: &ToggleDetail, _, cx| {
                this.details = !this.details;
                cx.notify();
            }))
            .on_action(cx.listener(|this, _: &ClearFilters, window, cx| this.clear(window, cx)))
            .on_action(cx.listener(|this, _: &Previous, window, cx| {
                this.navigate(false, this.columns, window, cx)
            }))
            .on_action(cx.listener(|this, _: &Next, window, cx| {
                if !window.has_active_dialog(cx) {
                    this.navigate(true, this.columns, window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &Left, window, cx| {
                if this.grid && this.list_focus.is_focused(window) && !window.has_active_dialog(cx)
                {
                    this.navigate(false, 1, window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &Right, window, cx| {
                if this.grid && this.list_focus.is_focused(window) && !window.has_active_dialog(cx)
                {
                    this.navigate(true, 1, window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &Run, window, cx| {
                if !window.has_active_dialog(cx) {
                    this.run_selected(window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &RunElevated, window, cx| {
                if !window.has_active_dialog(cx)
                    && this.group_selection.is_empty()
                    && let Some(id) = this.selected
                {
                    this.run_entry(id, None, true, None, window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &RunWithArgs, window, cx| {
                if !window.has_active_dialog(cx) {
                    this.args_dialog(window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &Pin, window, cx| this.toggle_pin(window, cx)))
            .on_action(cx.listener(|this, _: &SelectAll, window, cx| {
                if !window.has_active_dialog(cx) {
                    this.select_all(window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &CopyPaths, window, cx| {
                if !window.has_active_dialog(cx) && this.list_focus.is_focused(window) {
                    this.copy_paths(window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &PreviewSelection, window, cx| {
                if this.list_focus.is_focused(window) {
                    this.preview_selection(window, cx);
                }
            }))
            .on_action(cx.listener(|this, _: &EscapeWorkspace, window, cx| {
                this.escape_workspace(window, cx)
            }))
            .on_action(cx.listener(|this, _: &EditNote, window, cx| this.edit_note(window, cx)))
            .on_action(cx.listener(|this, _: &SaveNote, window, cx| {
                if !window.has_active_dialog(cx) {
                    this.save_note(window, cx);
                }
            }))
            .child(self.sidebar(p, cx))
            .child(
                v_flex()
                    .flex_1()
                    .min_w_0()
                    .h_full()
                    .child(self.toolbar(p, cx))
                    .child(
                        div()
                            .flex()
                            .relative()
                            .flex_1()
                            .min_h_0()
                            .w_full()
                            .child(self.center(p, cx))
                            .when(
                                self.details
                                    && self.group_selection.is_empty()
                                    && (!compact || self.selected.is_some()),
                                |main| main.child(self.detail(p, compact, cx)),
                            ),
                    ),
            )
            .when_some(settings_overlay(self, window, cx), |root, overlay| {
                root.child(overlay)
            })
            .children(dialogs)
            .children(notifications)
    }
}

fn settings_overlay(
    workspace: &Workspace,
    window: &mut Window,
    cx: &mut Context<Workspace>,
) -> Option<gpui::AnyElement> {
    let focus = workspace.settings_page.as_ref()?.focus.clone();
    let content = workspace.settings_content(cx)?;
    let viewport = window.viewport_size();
    let width = px(1040.).min(viewport.width - px(48.));
    let height = px(760.).min(viewport.height - px(48.));
    let theme = cx.theme();
    Some(
        div()
            .id("settings-overlay")
            .key_context("Settings")
            .track_focus(&focus)
            .on_action(cx.listener(|this, _: &EscapeWorkspace, window, cx| {
                this.escape_workspace(window, cx)
            }))
            .on_action(
                cx.listener(|this, _: &OpenSettings, window, cx| this.toggle_settings(window, cx)),
            )
            .absolute()
            .top_0()
            .left_0()
            .size_full()
            .occlude()
            .flex()
            .items_center()
            .justify_center()
            .bg(theme.overlay)
            .child(
                div()
                    .w(width)
                    .h(height)
                    .overflow_hidden()
                    .rounded_lg()
                    .border_1()
                    .border_color(theme.border)
                    .shadow_xl()
                    .child(content),
            )
            .into_any_element(),
    )
}

const GRID_GAP: f32 = 12.;
const GRID_MIN_CARD: f32 = 208.;
const GRID_ROW_HEIGHT: f32 = 108.;
const RESULTS_SCROLLBAR_GUTTER: f32 = 20.;

fn grid_columns(width: f32) -> usize {
    ((width + GRID_GAP) / (GRID_MIN_CARD + GRID_GAP))
        .floor()
        .max(1.) as usize
}

#[cfg(test)]
#[test]
fn grid_columns_fit_the_measured_card_area() {
    assert_eq!(grid_columns(160.), 1);
    assert_eq!(grid_columns(427.), 1);
    assert_eq!(grid_columns(428.), 2);
    assert_eq!(grid_columns(868.), 4);
}

fn glyph(icon: IconName, edge: f32, background: Hsla, foreground: Hsla) -> Div {
    div()
        .flex()
        .items_center()
        .justify_center()
        .size(px(edge))
        .flex_shrink_0()
        .rounded(px(edge * 0.27))
        .bg(background)
        .text_color(foreground)
        .child(Icon::new(icon))
}

fn keycap(key: &'static str, p: Palette) -> Div {
    div()
        .px_1p5()
        .py_0p5()
        .rounded_md()
        .bg(p.inset)
        .text_xs()
        .text_color(p.muted)
        .child(key)
}

fn kind_icon(kind: Kind) -> IconName {
    match kind {
        Kind::Data => IconName::Folder,
        Kind::Shortcut => IconName::ExternalLink,
        Kind::Exe => IconName::Frame,
        Kind::Other => IconName::LayoutDashboard,
        _ => IconName::SquareTerminal,
    }
}

fn entry_menu(
    menu_view: gpui::WeakEntity<Workspace>,
    path: std::path::PathBuf,
    has_python: bool,
    has_recent: bool,
) -> impl Fn(
    gpui_component::menu::PopupMenu,
    &mut Window,
    &mut Context<gpui_component::menu::PopupMenu>,
) -> gpui_component::menu::PopupMenu {
    move |menu, _, _| {
        let item = |label,
                    icon,
                    action: fn(&mut Workspace, &mut Window, &mut Context<Workspace>),
                    preserve_group: bool| {
            let view = menu_view.clone();
            let path = path.clone();
            PopupMenuItem::new(label)
                .icon(icon)
                .on_click(move |_, window, cx| {
                    let _ = view.update(cx, |this, cx| {
                        if let Some(id) = this
                            .catalog
                            .entries
                            .iter()
                            .position(|entry| entry.path == path)
                        {
                            if !preserve_group || !this.group_selection.contains(&id) {
                                this.select_row(id, gpui::Modifiers::default(), window, cx);
                            }
                            action(this, window, cx);
                        }
                    });
                })
        };
        menu.item(item(
            "打开",
            IconName::ExternalLink,
            Workspace::run_selected,
            false,
        ))
        .item(item(
            "预览",
            IconName::Eye,
            Workspace::preview_selection,
            false,
        ))
        .item(item(
            "在此打开终端",
            IconName::SquareTerminal,
            Workspace::terminal_here,
            false,
        ))
        .item(
            item(
                "Python 后端",
                IconName::SquareTerminal,
                Workspace::backend_dialog,
                false,
            )
            .disabled(!has_python),
        )
        .item(item(
            "在资源管理器中定位",
            IconName::FolderOpen,
            |this, window, cx| this.location(window, cx),
            false,
        ))
        .separator()
        .item(item(
            "固定或取消固定",
            IconName::Star,
            Workspace::toggle_pin,
            true,
        ))
        .item(item(
            "复制路径",
            IconName::Copy,
            |this, window, cx| this.copy_paths(window, cx),
            true,
        ))
        .when(has_recent, |menu| {
            menu.separator().item(item(
                "从最近中移除",
                IconName::Delete,
                Workspace::remove_from_recent,
                true,
            ))
        })
    }
}

impl Workspace {
    pub(super) fn set_grid(&mut self, grid: bool, window: &mut Window, cx: &mut Context<Self>) {
        if self.grid == grid {
            return;
        }
        let Some(settings) = self.settings.as_mut() else {
            return;
        };
        if let Err(error) = settings.set_grid_results(grid) {
            Self::notify_error(error, window, cx);
            return;
        }
        self.grid = grid;
        self.columns = if grid {
            self.results_width
                .map_or(1, |width| grid_columns(f32::from(width)))
        } else {
            1
        };
        self.list.scroll_to_item_strict(0, ScrollStrategy::Top);
        cx.notify();
    }

    fn update_grid_columns(&mut self, columns: usize) {
        if self.columns == columns {
            return;
        }
        let offset = -f32::from(self.list.0.borrow().base_handle.offset().y);
        let row = (offset / GRID_ROW_HEIGHT).max(0.) as usize;
        let first = (row * self.columns).min(self.results.len().saturating_sub(1));
        self.columns = columns;
        self.list
            .scroll_to_item_strict(first / columns, ScrollStrategy::Top);
    }

    fn grid_row(&self, row: usize, p: Palette, entity: &Entity<Workspace>) -> gpui::AnyElement {
        let start = row * self.columns;
        let end = (start + self.columns).min(self.results.len());
        let cards = self
            .results
            .get(start..end)
            .unwrap_or_default()
            .iter()
            .map(|&index| {
                let item = &self.catalog.entries[index];
                let category = &self.catalog.categories[item.category];
                let active = self.selected == Some(index) || self.group_selection.contains(&index);
                let subtitle = if self.all_workspaces {
                    format!("{} · {}", item.workspace, category.name)
                } else {
                    category.name.clone()
                };
                let kind = item
                    .resource
                    .map(crate::resources::Format::label)
                    .unwrap_or(item.kinds[0].label());
                let tip = format!("{}\n{subtitle} · {kind}", item.name);
                let path = item.path.clone();
                let view = entity.downgrade();
                let has_recent = !item.metadata.last_run.is_empty() && !item.metadata.recent_hidden;
                let menu = entry_menu(
                    entity.downgrade(),
                    path,
                    item.kinds.contains(&Kind::Python) || item.metadata.command.is_some(),
                    has_recent,
                );
                v_flex()
                    .id(("grid-card", index))
                    .relative()
                    .top(px(0.))
                    .flex_1()
                    .min_w_0()
                    .h_full()
                    .justify_between()
                    .gap_2()
                    .p_3()
                    .rounded_lg()
                    .border_1()
                    .border_color(if active { p.accent } else { p.line })
                    .bg(if active { p.selection } else { p.surface })
                    .shadow_sm()
                    .hover(|card| card.bg(p.inset).top(px(-3.)).shadow_md())
                    .active(|card| card.top(px(1.)))
                    .cursor_pointer()
                    .child(
                        h_flex()
                            .w_full()
                            .gap_2()
                            .min_w_0()
                            .child(glyph(kind_icon(item.kinds[0]), 32., p.inset, p.accent))
                            .child(
                                v_flex().flex_1().min_w_0().child(
                                    div()
                                        .w_full()
                                        .min_w_0()
                                        .text_size(px(15.))
                                        .font_semibold()
                                        .line_clamp(2)
                                        .child(item.name.clone()),
                                ),
                            )
                            .when(item.metadata.pinned, |line| {
                                line.child(Icon::new(IconName::Star).xsmall().text_color(p.accent))
                            }),
                    )
                    .child(
                        h_flex()
                            .w_full()
                            .min_w_0()
                            .gap_2()
                            .text_xs()
                            .text_color(p.muted)
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .child(div().w_full().truncate().child(subtitle)),
                            )
                            .child(
                                div()
                                    .flex_shrink_0()
                                    .rounded_md()
                                    .bg(p.inset)
                                    .px_2()
                                    .py_0p5()
                                    .child(kind),
                            ),
                    )
                    .tooltip(move |window, cx| Tooltip::new(tip.clone()).build(window, cx))
                    .on_click(move |event, window, cx| {
                        let _ = view.update(cx, |this, cx| {
                            let modifiers = event.modifiers();
                            this.select_row(index, modifiers, window, cx);
                            if event.click_count() == 2 && !modifiers.control && !modifiers.shift {
                                this.run_selected(window, cx);
                            }
                        });
                    })
                    .context_menu(menu)
            });
        h_flex()
            .id(("grid-row", row))
            .w_full()
            .h(px(GRID_ROW_HEIGHT))
            .gap(px(GRID_GAP))
            .py_1p5()
            .children(cards)
            .into_any_element()
    }

    fn toolbar(&self, p: Palette, cx: &mut Context<Self>) -> impl IntoElement {
        h_flex()
            .h(px(66.))
            .w_full()
            .flex_shrink_0()
            .px_5()
            .gap_3()
            .border_b_1()
            .border_color(p.line)
            .child(
                div().flex_1().max_w(px(660.)).min_w_0().child(
                    Input::new(&self.search)
                        .large()
                        .cleanable(true)
                        .prefix(Icon::new(IconName::Search).small().text_color(p.muted))
                        .suffix(keycap("Ctrl K", p)),
                ),
            )
            .child(div().flex_1().min_w_0())
            .child(
                h_flex()
                    .gap_1()
                    .rounded_lg()
                    .bg(p.inset)
                    .p_1()
                    .child(
                        button("refresh")
                            .ghost()
                            .small()
                            .icon(IconName::Redo)
                            .tooltip("刷新 · F5")
                            .loading(self.scanning)
                            .on_click(cx.listener(|this, _, window, cx| this.refresh(window, cx))),
                    )
                    .child(
                        button("scoop")
                            .ghost()
                            .small()
                            .icon(IconName::Frame)
                            .tooltip("Scoop 管理")
                            .on_click(
                                cx.listener(|this, _, window, cx| this.scoop_manager(window, cx)),
                            ),
                    )
                    .child(
                        button("open-settings")
                            .ghost()
                            .small()
                            .icon(IconName::Settings)
                            .tooltip("设置 · Ctrl+, ")
                            .on_click(
                                cx.listener(|this, _, window, cx| this.toggle_settings(window, cx)),
                            ),
                    )
                    .child(
                        button("detail")
                            .ghost()
                            .small()
                            .icon(if self.details {
                                IconName::PanelRightClose
                            } else {
                                IconName::PanelRightOpen
                            })
                            .tooltip("切换详情 · Ctrl+B")
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.details = !this.details;
                                cx.notify();
                            })),
                    ),
            )
    }

    fn sidebar(&self, p: Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let name = self
            .root
            .as_deref()
            .map(catalog::file_name)
            .unwrap_or_else(|| "选择目录".into());
        let presets = self
            .settings
            .as_ref()
            .map(Settings::presets)
            .unwrap_or_default();
        let current = self
            .root
            .as_deref()
            .map(|path| crate::storage::path_key(&path.to_string_lossy()));
        let is_saved = current.as_ref().is_some_and(|current| {
            presets
                .iter()
                .any(|(_, path)| crate::storage::path_key(&path.to_string_lossy()) == *current)
        });
        let named_only = self.settings.as_ref().is_some_and(Settings::named_only);
        let hide_empty = self
            .settings
            .as_ref()
            .is_some_and(Settings::hide_empty_categories);
        let view = cx.weak_entity();
        let switcher = Popover::new("workspace-switcher")
            .trigger(
                button("workspace")
                    .outline()
                    .w_full()
                    .justify_start()
                    .child(
                        h_flex()
                            .w_full()
                            .gap_2()
                            .child(Icon::new(IconName::FolderOpen).small().text_color(p.accent))
                            .child(div().flex_1().min_w_0().truncate().child(name))
                            .child(
                                Icon::new(IconName::ChevronsUpDown)
                                    .xsmall()
                                    .text_color(p.muted),
                            ),
                    ),
            )
            .content(move |_, _, cx| {
                let popover = cx.entity().downgrade();
                let view_for_picker = view.clone();
                let picker_popover = popover.clone();
                let preset_view = view.clone();
                let preset_popover = popover.clone();
                let naming_view = view.clone();
                let naming_popover = popover.clone();
                v_flex()
                    .id("workspace-options")
                    .w(px(214.))
                    .max_h(px(350.))
                    .overflow_y_scroll()
                    .gap_1()
                    .children(presets.iter().enumerate().map(|(index, (name, root))| {
                        let view = view.clone();
                        let root = root.clone();
                        let popover = popover.clone();
                        button(("workspace-preset", index))
                            .ghost()
                            .small()
                            .justify_start()
                            .icon(IconName::Folder)
                            .label(name.clone())
                            .on_click(move |_, window, cx| {
                                let _ =
                                    popover.update(cx, |popover, cx| popover.dismiss(window, cx));
                                let _ = view.update(cx, |this, cx| {
                                    this.switch_workspace(root.clone(), window, cx)
                                });
                            })
                    }))
                    .child(
                        button("workspace-browse")
                            .ghost()
                            .small()
                            .justify_start()
                            .icon(IconName::Plus)
                            .label("添加工作区…")
                            .on_click(move |_, window, cx| {
                                let _ = picker_popover
                                    .update(cx, |popover, cx| popover.dismiss(window, cx));
                                let _ = view_for_picker
                                    .update(cx, |this, cx| this.pick_workspace(window, cx));
                            }),
                    )
                    .when(current.is_some(), |list| {
                        list.child(
                            button("workspace-save")
                                .ghost()
                                .small()
                                .justify_start()
                                .icon(IconName::Star)
                                .label(if is_saved {
                                    "移除当前快捷入口"
                                } else {
                                    "保存当前工作区"
                                })
                                .on_click(move |_, window, cx| {
                                    let _ = preset_popover
                                        .update(cx, |popover, cx| popover.dismiss(window, cx));
                                    let _ = preset_view.update(cx, |this, cx| {
                                        this.toggle_workspace_preset(window, cx)
                                    });
                                }),
                        )
                    })
                    .child(
                        button("workspace-naming")
                            .ghost()
                            .small()
                            .justify_start()
                            .icon(if named_only {
                                IconName::Check
                            } else {
                                IconName::Folder
                            })
                            .label("仅扫描规范目录")
                            .tooltip("工作区第一层按 A-01 或 A-01-名称 匹配")
                            .on_click(move |_, window, cx| {
                                let _ = naming_popover
                                    .update(cx, |popover, cx| popover.dismiss(window, cx));
                                let _ = naming_view
                                    .update(cx, |this, cx| this.toggle_named_only(window, cx));
                            }),
                    )
            });
        v_flex()
            .w(px(200.))
            .min_h_0()
            .flex_shrink_0()
            .gap_4()
            .px_3()
            .pb_3()
            .bg(p.sidebar)
            .border_r_1()
            .border_color(p.line)
            .child(
                h_flex()
                    .h(px(66.))
                    .px_2()
                    .gap_2p5()
                    .flex_shrink_0()
                    .child(gpui::img(crate::LOGO.clone()).size(px(38.)).flex_shrink_0())
                    .child(div().text_size(px(19.)).font_semibold().child("SuperBay")),
            )
            .child(switcher)
            .child(
                v_flex()
                    .gap_0p5()
                    .child(
                        self.nav_row(
                            "all-workspaces",
                            "全部工作区",
                            Icon::new(IconName::Globe).small(),
                            self.all_workspaces && self.scope == Scope::All,
                            p,
                        )
                        .on_click(
                            cx.listener(|this, _, window, cx| {
                                this.search_all_workspaces(window, cx)
                            }),
                        ),
                    )
                    .child(
                        self.nav_row(
                            "all",
                            "当前工作区",
                            Icon::new(IconName::LayoutDashboard).small(),
                            !self.all_workspaces && self.scope == Scope::All,
                            p,
                        )
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.show_current_workspace(window, cx)
                        })),
                    )
                    .child(self.nav("pinned", "已固定", IconName::Star, Scope::Pinned, p, cx))
                    .child(self.nav(
                        "recent",
                        "最近使用",
                        IconName::Calendar,
                        Scope::Recent,
                        p,
                        cx,
                    )),
            )
            .when(
                !self.all_workspaces
                    && self.catalog.categories.iter().any(|category| {
                        !category.workspace_root && (!hide_empty || category.has_launchable_entries)
                    }),
                |rail| {
                    rail.child(
                        v_flex()
                            .flex_1()
                            .min_h_0()
                            .gap_2()
                            .child(
                                div()
                                    .px_2()
                                    .text_xs()
                                    .font_semibold()
                                    .text_color(p.muted)
                                    .child("目录"),
                            )
                            .child(
                                v_flex()
                                    .id("categories")
                                    .overflow_y_scroll()
                                    .min_h_0()
                                    .flex_1()
                                    .gap_0p5()
                                    .children(
                                        self.catalog
                                            .categories
                                            .iter()
                                            .enumerate()
                                            .filter(|(_, category)| {
                                                !category.workspace_root
                                                    && (!hide_empty
                                                        || category.has_launchable_entries)
                                                    && self.category_visible(category)
                                            })
                                            .map(|(id, category)| {
                                                self.category_row(id, category, p, cx)
                                            }),
                                    ),
                            ),
                    )
                },
            )
    }

    fn nav(
        &self,
        id: impl Into<ElementId>,
        label: &str,
        icon: IconName,
        scope: Scope,
        p: Palette,
        cx: &Context<Self>,
    ) -> Stateful<Div> {
        self.nav_row(id, label, Icon::new(icon).small(), self.scope == scope, p)
            .on_click(cx.listener(move |this, _, _, cx| {
                this.scope = scope;
                this.rebuild(true, cx);
            }))
    }

    fn nav_row(
        &self,
        id: impl Into<ElementId>,
        label: &str,
        icon: impl IntoElement,
        selected: bool,
        p: Palette,
    ) -> Stateful<Div> {
        h_flex()
            .id(id)
            .h(px(35.))
            .px(px(9.))
            .gap_2()
            .rounded_lg()
            .cursor_pointer()
            .text_sm()
            .hover(|row| row.bg(p.inset))
            .when(selected, |row| {
                row.bg(p.selection).text_color(p.accent).font_semibold()
            })
            .child(div().w(px(16.)).h(px(16.)).flex_shrink_0().child(icon))
            .child(div().flex_1().min_w_0().truncate().child(label.to_owned()))
    }

    fn category_visible(&self, category: &catalog::Category) -> bool {
        if category.depth == 0 {
            return true;
        }
        category
            .path
            .ancestors()
            .skip(1)
            .take(category.depth)
            .all(|parent| self.expanded_categories.contains(parent))
    }

    fn category_row(
        &self,
        id: usize,
        category: &catalog::Category,
        p: Palette,
        cx: &Context<Self>,
    ) -> Stateful<Div> {
        let path = category.path.clone();
        let hide_empty = self
            .settings
            .as_ref()
            .is_some_and(Settings::hide_empty_categories);
        let has_children = self.catalog.categories[id + 1..]
            .iter()
            .take_while(|next| next.depth > category.depth)
            .any(|next| !hide_empty || next.has_launchable_entries);
        let expanded = self.expanded_categories.contains(&path);
        let icon = match path
            .file_name()
            .and_then(|name| name.to_str())
            .and_then(catalog::directory_letter)
        {
            Some(letter) => div()
                .flex()
                .size_full()
                .justify_center()
                .items_center()
                .rounded_sm()
                .bg(p.selection)
                .text_color(p.accent)
                .text_xs()
                .font_semibold()
                .child(letter.to_string())
                .into_any_element(),
            None => Icon::new(IconName::Folder).small().into_any_element(),
        };
        self.nav_row(
            ("category", id),
            &category.name,
            icon,
            self.scope == Scope::Category(id),
            p,
        )
        .on_click(cx.listener(move |this, _, _, cx| {
            this.scope = Scope::Category(id);
            this.rebuild(true, cx);
        }))
        .pl(px(9. + category.depth.min(5) as f32 * 12.))
        .when(has_children, |row| {
            row.child(
                button(("expand-category", id))
                    .ghost()
                    .xsmall()
                    .icon(if expanded {
                        IconName::ChevronDown
                    } else {
                        IconName::ChevronRight
                    })
                    .tooltip(if expanded {
                        "收起子目录"
                    } else {
                        "展开子目录"
                    })
                    .on_click(cx.listener(move |this, _, _, cx| {
                        cx.stop_propagation();
                        if !this.expanded_categories.remove(&path) {
                            this.expanded_categories.insert(path.clone());
                        }
                        cx.notify();
                    })),
            )
        })
    }

    fn center(&self, p: Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let width = self.results_width;
        let measured_view = cx.weak_entity();
        let title = match self.scope {
            Scope::All if self.all_workspaces => "全部工作区",
            Scope::All => "所有工具",
            Scope::Pinned => "已固定",
            Scope::Recent => "最近使用",
            Scope::Category(index) => &self.catalog.categories[index].name,
        };
        let entity = cx.entity();
        let view = entity.downgrade();
        let kinds = self.catalog.kinds.clone();
        let active = self.kind;
        let sorting_view = entity.downgrade();
        let sort = self.sort;
        let dense = self.dense;
        let layout = Popover::new("result-layout")
            .anchor(Corner::TopRight)
            .trigger(
                button("result-layout-button")
                    .outline()
                    .small()
                    .icon(IconName::Settings2)
                    .tooltip("排序和密度"),
            )
            .content(move |_, _, cx| {
                let popover = cx.entity().downgrade();
                let density_view = sorting_view.clone();
                let density_popover = popover.clone();
                v_flex()
                    .w(px(152.))
                    .gap_1()
                    .children(ResultSort::OPTIONS.into_iter().map(|option| {
                        let view = sorting_view.clone();
                        let popover = popover.clone();
                        button(option.key())
                            .ghost()
                            .small()
                            .justify_start()
                            .label(option.label())
                            .when(option == sort, |button| button.icon(IconName::Check))
                            .on_click(move |_, window, cx| {
                                let _ = popover.update(cx, |this, cx| this.dismiss(window, cx));
                                let _ = view.update(cx, |this, cx| {
                                    this.result_layout(option, dense, window, cx)
                                });
                            })
                    }))
                    .child(
                        button("density")
                            .ghost()
                            .small()
                            .justify_start()
                            .label(if dense {
                                "切换为舒展列表"
                            } else {
                                "切换为紧凑列表"
                            })
                            .on_click(move |_, window, cx| {
                                let _ =
                                    density_popover.update(cx, |this, cx| this.dismiss(window, cx));
                                let _ = density_view.update(cx, |this, cx| {
                                    this.result_layout(sort, !dense, window, cx)
                                });
                            }),
                    )
            });
        let filter = Popover::new("type-filter")
            .anchor(Corner::TopRight)
            .trigger(
                button("filter")
                    .outline()
                    .small()
                    .icon(IconName::SortDescending)
                    .label(active.map(Kind::label).unwrap_or("全部类型")),
            )
            .content(move |_, _, cx| {
                let popover = cx.entity().downgrade();
                v_flex()
                    .id("type-options")
                    .w(px(160.))
                    .max_h(px(345.))
                    .overflow_y_scroll()
                    .gap_0p5()
                    .children(
                        std::iter::once(None)
                            .chain(kinds.iter().copied().map(Some))
                            .enumerate()
                            .map(|(index, kind)| {
                                let view = view.clone();
                                let popover = popover.clone();
                                button(("type-option", index))
                                    .ghost()
                                    .small()
                                    .justify_start()
                                    .label(kind.map(Kind::label).unwrap_or("全部类型"))
                                    .when(kind == active, |button| button.icon(IconName::Check))
                                    .on_click(move |_, window, cx| {
                                        let _ = popover
                                            .update(cx, |popover, cx| popover.dismiss(window, cx));
                                        let _ = view.update(cx, |this, cx| {
                                            this.kind = kind;
                                            this.rebuild(true, cx);
                                        });
                                    })
                            }),
                    )
            });
        v_flex()
            .flex_1()
            .min_w_0()
            .min_h_0()
            .px_5()
            .pt_5()
            .gap_3()
            .child(
                h_flex()
                    .min_w_0()
                    .gap_3()
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .text_size(px(27.))
                            .font_semibold()
                            .truncate()
                            .child(title.to_owned()),
                    )
                    .when(self.scope == Scope::Recent, |header| {
                        header.child(
                            button("clear-recent")
                                .outline()
                                .small()
                                .icon(IconName::Delete)
                                .label("清空最近")
                                .on_click(
                                    cx.listener(|this, _, window, cx| {
                                        this.clear_recent(window, cx)
                                    }),
                                ),
                        )
                    })
                    .child(filter)
                    .child(
                        button("layout-mode")
                            .outline()
                            .small()
                            .icon(if self.grid {
                                IconName::Menu
                            } else {
                                IconName::LayoutDashboard
                            })
                            .tooltip(if self.grid {
                                "切换到列表"
                            } else {
                                "切换到网格"
                            })
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.set_grid(!this.grid, window, cx);
                            })),
                    )
                    .child(layout),
            )
            .when_some(self.error.as_ref(), |column, error| {
                column.child(
                    h_flex()
                        .gap_2()
                        .p_3()
                        .rounded_lg()
                        .bg(p.selection)
                        .text_color(p.accent)
                        .text_sm()
                        .child(Icon::new(IconName::TriangleAlert).flex_shrink_0())
                        .child(error.clone()),
                )
            })
            .child(
                v_flex()
                    .id("results-panel")
                    .track_focus(&self.list_focus)
                    .relative()
                    .flex_1()
                    .min_h_0()
                    .overflow_hidden()
                    .rounded_xl()
                    .when(!self.results.is_empty(), |panel| {
                        panel.pr(px(RESULTS_SCROLLBAR_GUTTER))
                    })
                    .when(!self.grid, |panel| {
                        panel.bg(p.surface).border_1().border_color(p.line)
                    })
                    .when(self.grid, |panel| {
                        panel.child(
                            gpui::canvas(
                                move |bounds, window, _| {
                                    if width != Some(bounds.size.width) {
                                        window.on_next_frame(move |_, cx| {
                                            let _ = measured_view.update(cx, |this, cx| {
                                                if this.grid {
                                                    this.results_width = Some(bounds.size.width);
                                                    this.update_grid_columns(grid_columns(
                                                        f32::from(bounds.size.width),
                                                    ));
                                                    cx.notify();
                                                }
                                            });
                                        });
                                    }
                                },
                                |_, _, _, _| {},
                            )
                            .absolute()
                            .top_0()
                            .left_0()
                            .right(px(if self.results.is_empty() {
                                0.
                            } else {
                                RESULTS_SCROLLBAR_GUTTER
                            }))
                            .h(px(0.)),
                        )
                    })
                    .when(self.results.is_empty(), |panel| {
                        panel.child(self.empty(p, cx))
                    })
                    .when(!self.results.is_empty(), |panel| {
                        panel
                            .child(
                                uniform_list(
                                    if self.grid {
                                        "grid-results"
                                    } else {
                                        "list-results"
                                    },
                                    self.results.len().div_ceil(self.columns),
                                    move |range, _, cx| {
                                        let this = entity.read(cx);
                                        if this.grid {
                                            return range
                                                .map(|row| this.grid_row(row, p, &entity))
                                                .collect();
                                        }
                                        range
                                            .filter_map(|row| {
                                                this.results.get(row).map(|&index| {
                                                    this.row(index, p, &entity).into_any_element()
                                                })
                                            })
                                            .collect()
                                    },
                                )
                                .track_scroll(self.list.clone())
                                .flex_1()
                                .min_h_0()
                                .w_full(),
                            )
                            .child(
                                div()
                                    .absolute()
                                    .top_0()
                                    .bottom_0()
                                    .right_0()
                                    .w(px(RESULTS_SCROLLBAR_GUTTER))
                                    .child(Scrollbar::vertical(&self.list)),
                            )
                    }),
            )
            .child(
                h_flex()
                    .h(px(BUTTON_HEIGHT))
                    .mb_2()
                    .gap_2()
                    .text_xs()
                    .text_color(p.muted)
                    .child(div().flex_1().child(if self.scanning {
                        "正在扫描…".into()
                    } else {
                        format!("{} 项", self.results.len())
                    }))
                    .when(!self.group_selection.is_empty(), |footer| {
                        footer
                            .child(format!("已选 {} 项", self.group_selection.len()))
                            .child(button("batch-pin").ghost().small().label("固定").on_click(
                                cx.listener(|this, _, window, cx| {
                                    this.pin_selection(Some(true), window, cx)
                                }),
                            ))
                            .child(
                                button("batch-unpin")
                                    .ghost()
                                    .small()
                                    .label("取消固定")
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.pin_selection(Some(false), window, cx)
                                    })),
                            )
                            .child(
                                button("batch-paths")
                                    .ghost()
                                    .small()
                                    .label("复制路径")
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.copy_paths(window, cx)
                                    })),
                            )
                    })
                    .when(
                        !self.results.is_empty() && self.group_selection.is_empty(),
                        |footer| {
                            footer
                                .child(keycap("Space", p))
                                .child("预览")
                                .child(keycap("Enter", p))
                                .child("打开")
                        },
                    ),
            )
    }

    fn row(&self, index: usize, p: Palette, entity: &Entity<Workspace>) -> Stateful<Div> {
        let entry = &self.catalog.entries[index];
        let category = &self.catalog.categories[entry.category];
        let selected = self.selected == Some(index) || self.group_selection.contains(&index);
        let mut context = if entry.excerpt.is_empty() {
            category.name.clone()
        } else {
            entry.excerpt.clone()
        };
        if self.all_workspaces {
            context = format!("{} · {context}", entry.workspace);
        }
        let menu_view = entity.downgrade();
        let path = entry.path.clone();
        let has_python = entry.kinds.contains(&Kind::Python) || entry.metadata.command.is_some();
        let has_recent = !entry.metadata.last_run.is_empty() && !entry.metadata.recent_hidden;
        let label = entry
            .resource
            .map(crate::resources::Format::label)
            .unwrap_or(entry.kinds[0].label());
        let entity = entity.downgrade();
        div()
            .id(("entry-container", index))
            .h(px(self.list_row_height()))
            .px_1p5()
            .py_1()
            .child(
                h_flex()
                    .id(("entry", index))
                    .relative()
                    .size_full()
                    .gap_3()
                    .px_3()
                    .rounded_lg()
                    .cursor_pointer()
                    .hover(|row| row.bg(p.inset))
                    .when(selected, |row| {
                        row.bg(p.selection).child(
                            div()
                                .absolute()
                                .left_0()
                                .top(px(if self.dense { 9. } else { 16. }))
                                .h(px(22.))
                                .w(px(3.))
                                .rounded_full()
                                .bg(p.accent),
                        )
                    })
                    .child(glyph(
                        kind_icon(entry.kinds[0]),
                        if self.dense { 28. } else { 35. },
                        if selected { p.surface } else { p.inset },
                        p.accent,
                    ))
                    .child(
                        v_flex()
                            .flex_1()
                            .min_w_0()
                            .gap_0p5()
                            .child(
                                h_flex()
                                    .gap_1p5()
                                    .min_w_0()
                                    .child(
                                        div()
                                            .min_w_0()
                                            .truncate()
                                            .text_sm()
                                            .font_semibold()
                                            .child(entry.name.clone()),
                                    )
                                    .when(entry.metadata.pinned, |line| {
                                        line.child(
                                            Icon::new(IconName::Star).xsmall().text_color(p.accent),
                                        )
                                    }),
                            )
                            .child(
                                div()
                                    .truncate()
                                    .text_xs()
                                    .text_color(p.muted)
                                    .child(context),
                            ),
                    )
                    .child(
                        div()
                            .text_xs()
                            .text_color(p.muted)
                            .flex_shrink_0()
                            .child(label),
                    )
                    .child(
                        Icon::new(IconName::ChevronRight)
                            .xsmall()
                            .text_color(p.muted),
                    )
                    .on_click(move |event, window, cx| {
                        let _ = entity.update(cx, |this, cx| {
                            let modifiers = event.modifiers();
                            this.select_row(index, modifiers, window, cx);
                            if event.click_count() == 2 && !modifiers.control && !modifiers.shift {
                                this.run_selected(window, cx);
                            }
                            cx.notify();
                        });
                    })
                    .context_menu(entry_menu(menu_view, path, has_python, has_recent)),
            )
    }

    fn list_row_height(&self) -> f32 {
        if self.dense { 46. } else { 62. }
    }

    fn empty(&self, p: Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let (icon, title) = if self.scanning {
            (IconName::LoaderCircle, "正在扫描工作区")
        } else if self.error.is_some() {
            (IconName::TriangleAlert, "工作区不可用")
        } else if self.root.is_none() {
            (IconName::FolderOpen, "打开一个工作区")
        } else if !self.query.is_empty() || self.kind.is_some() {
            (IconName::Search, "没有匹配的条目")
        } else {
            match self.scope {
                Scope::Pinned => (IconName::Star, "还没有固定的工具"),
                Scope::Recent => (IconName::Calendar, "还没有最近使用的工具"),
                _ => (IconName::Inbox, "这个目录还没有条目"),
            }
        };
        v_flex()
            .size_full()
            .items_center()
            .justify_center()
            .gap_3()
            .p_4()
            .child(glyph(icon, 52., p.selection, p.accent))
            .child(div().text_base().font_semibold().child(title))
            .when(self.root.is_none() && self.settings.is_some(), |empty| {
                empty.child(
                    h_flex()
                        .gap_2()
                        .child(button("empty-pick").primary().label("选择目录").on_click(
                            cx.listener(|this, _, window, cx| this.pick_workspace(window, cx)),
                        ))
                        .child(button("empty-guide").ghost().label("目录规范").on_click(
                            cx.listener(|this, _, window, cx| this.open_guide(window, cx)),
                        )),
                )
            })
            .when(!self.query.is_empty() || self.kind.is_some(), |empty| {
                empty.child(
                    button("empty-clear")
                        .ghost()
                        .label("清除筛选")
                        .on_click(cx.listener(|this, _, window, cx| this.clear(window, cx))),
                )
            })
    }

    fn detail(&self, p: Palette, compact: bool, cx: &mut Context<Self>) -> impl IntoElement {
        let panel = v_flex()
            .w(px(if compact { 265. } else { 310. }))
            .h_full()
            .min_h_0()
            .flex_shrink_0()
            .bg(p.surface)
            .border_l_1()
            .border_color(p.line);
        let Some(entry) = self.selected_entry() else {
            return panel.child(
                div()
                    .p_5()
                    .text_sm()
                    .text_color(p.muted)
                    .child("选择一个条目"),
            );
        };
        let kind = entry.kinds[0];
        panel
            .child(
                v_flex()
                    .id("detail-content")
                    .flex_1()
                    .min_h_0()
                    .overflow_y_scroll()
                    .p_4()
                    .gap_4()
                    .child(
                        v_flex()
                            .gap_2()
                            .p_4()
                            .rounded_xl()
                            .border_1()
                            .border_color(p.line)
                            .bg(linear_gradient(
                                140.,
                                linear_color_stop(p.selection, 0.),
                                linear_color_stop(p.surface, 1.),
                            ))
                            .child(
                                h_flex()
                                    .justify_between()
                                    .gap_2()
                                    .child(glyph(kind_icon(kind), 44., p.primary, p.on_primary))
                                    .child(
                                        h_flex()
                                            .gap_1()
                                            .child(
                                                button("pin")
                                                    .ghost()
                                                    .small()
                                                    .icon(if entry.metadata.pinned {
                                                        IconName::StarOff
                                                    } else {
                                                        IconName::Star
                                                    })
                                                    .tooltip(if entry.metadata.pinned {
                                                        "取消固定"
                                                    } else {
                                                        "固定"
                                                    })
                                                    .on_click(cx.listener(
                                                        |this, _, window, cx| {
                                                            this.toggle_pin(window, cx)
                                                        },
                                                    )),
                                            )
                                            .when(compact, |actions| {
                                                actions.child(
                                                    button("close-details")
                                                        .ghost()
                                                        .small()
                                                        .icon(IconName::Close)
                                                        .tooltip("关闭详情")
                                                        .on_click(cx.listener(|this, _, _, cx| {
                                                            this.details = false;
                                                            cx.notify();
                                                        })),
                                                )
                                            }),
                                    ),
                            )
                            .child(
                                h_flex().w_full().min_w_0().child(
                                    div()
                                        .flex_1()
                                        .text_size(px(22.))
                                        .font_semibold()
                                        .min_w_0()
                                        .truncate()
                                        .child(entry.name.clone()),
                                ),
                            )
                            .child(
                                h_flex()
                                    .gap_1p5()
                                    .flex_wrap()
                                    .children(entry.kinds.iter().map(|kind| {
                                        div()
                                            .rounded_full()
                                            .bg(p.surface)
                                            .px_2()
                                            .py_0p5()
                                            .text_xs()
                                            .text_color(p.accent)
                                            .child(kind.label())
                                    })),
                            )
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(p.muted)
                                    .font_family(cx.theme().mono_font_family.clone())
                                    .child(super::entry_preview::shorten(
                                        &entry.path.to_string_lossy(),
                                        32,
                                    )),
                            ),
                    )
                    .child(
                        h_flex()
                            .w_full()
                            .gap_2()
                            .child(
                                button("run")
                                    .primary()
                                    .flex_1()
                                    .icon(IconName::ExternalLink)
                                    .label("打开")
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.run_selected(window, cx)
                                    })),
                            )
                            .child(
                                button("location")
                                    .outline()
                                    .w(px(BUTTON_HEIGHT))
                                    .icon(IconName::FolderOpen)
                                    .tooltip("在资源管理器中定位")
                                    .on_click(
                                        cx.listener(|this, _, window, cx| {
                                            this.location(window, cx)
                                        }),
                                    ),
                            )
                            .child(
                                button("copy-detail-path")
                                    .outline()
                                    .w(px(BUTTON_HEIGHT))
                                    .icon(IconName::Copy)
                                    .tooltip(format!("复制路径\n{}", entry.path.display()))
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.copy_paths(window, cx)
                                    })),
                            ),
                    )
                    .child(
                        v_flex()
                            .gap_1()
                            .child(
                                button("launch-plans")
                                    .ghost()
                                    .small()
                                    .justify_start()
                                    .icon(IconName::LayoutDashboard)
                                    .label("启动方案")
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.plans_dialog(window, cx)
                                    })),
                            )
                            .child(
                                button("args")
                                    .ghost()
                                    .small()
                                    .justify_start()
                                    .icon(IconName::SquareTerminal)
                                    .label("带参数启动")
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.args_dialog(window, cx)
                                    })),
                            )
                            .when(
                                entry.kinds.contains(&Kind::Python)
                                    || entry.metadata.command.is_some(),
                                |section| {
                                    section
                                        .child(
                                            button("backend")
                                                .ghost()
                                                .small()
                                                .justify_start()
                                                .icon(IconName::SquareTerminal)
                                                .label("Python 后端")
                                                .on_click(cx.listener(|this, _, window, cx| {
                                                    this.backend_dialog(window, cx)
                                                })),
                                        )
                                        .when(
                                            !entry.directory
                                                && entry.resource.is_none()
                                                && crate::resources::plain_text(&entry.path),
                                            |section| {
                                                section.child(
                                                    button("preview-source-text")
                                                        .ghost()
                                                        .small()
                                                        .justify_start()
                                                        .icon(IconName::Eye)
                                                        .label("文本预览")
                                                        .on_click(cx.listener(
                                                            |this, _, window, cx| {
                                                                this.preview_selection(window, cx)
                                                            },
                                                        )),
                                                )
                                            },
                                        )
                                },
                            )
                            .child(
                                button("edit-entry")
                                    .ghost()
                                    .small()
                                    .justify_start()
                                    .icon(IconName::Settings2)
                                    .label("启动命令与标签")
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.metadata_dialog(window, cx)
                                    })),
                            )
                            .child(
                                button("runtime")
                                    .ghost()
                                    .small()
                                    .justify_start()
                                    .icon(IconName::Frame)
                                    .label("运行环境")
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.runtime_dialog(window, cx)
                                    })),
                            ),
                    )
                    .when(entry.resource.is_none(), |detail| {
                        detail.child(self.launch_preview_panel(entry, cx))
                    })
                    .when(entry.resource.is_some(), |detail| {
                        detail.child(
                            v_flex()
                                .gap_2()
                                .p_3()
                                .rounded_lg()
                                .bg(p.inset)
                                .child(
                                    h_flex()
                                        .justify_between()
                                        .child(div().text_sm().font_semibold().child("文件信息"))
                                        .child(
                                            button("resource-preview")
                                                .ghost()
                                                .small()
                                                .icon(IconName::Eye)
                                                .label("预览")
                                                .on_click(cx.listener(|this, _, window, cx| {
                                                    this.preview_selection(window, cx)
                                                })),
                                        ),
                                )
                                .when_some(entry.file_size, |panel, size| {
                                    panel.child(div().text_sm().text_color(p.muted).child(format!(
                                        "大小 · {}",
                                        crate::resources::size_label(size)
                                    )))
                                })
                                .when_some(entry.modified, |panel, modified| {
                                    let time: chrono::DateTime<chrono::Local> = modified.into();
                                    panel.child(
                                        div().text_sm().text_color(p.muted).child(format!(
                                            "修改 · {}",
                                            time.format("%Y-%m-%d %H:%M")
                                        )),
                                    )
                                }),
                        )
                    })
                    .when(!entry.metadata.tags.is_empty(), |detail| {
                        detail.child(
                            div()
                                .text_sm()
                                .text_color(p.muted)
                                .child(entry.metadata.tags.replace(',', " · ")),
                        )
                    })
                    .when(entry.metadata.runs > 0, |detail| {
                        detail.child(div().text_xs().text_color(p.muted).child(format!(
                            "打开 {} 次 · {}",
                            entry.metadata.runs, entry.metadata.last_run
                        )))
                    }),
            )
            .when(entry.directory, |panel| {
                panel.child(self.note_panel(entry, cx))
            })
    }
}
