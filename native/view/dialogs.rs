use std::{path::Path, rc::Rc};

use gpui::{prelude::*, *};
use gpui_component::{
    ActiveTheme as _, WindowExt,
    button::{Button, ButtonVariants},
    checkbox::Checkbox,
    dialog::Dialog,
    input::Input,
    v_flex,
};

use super::*;

pub(super) fn field(
    label: impl Into<SharedString>,
    state: &Entity<InputState>,
) -> impl IntoElement {
    v_flex()
        .gap_1()
        .child(div().text_sm().child(label.into()))
        .child(Input::new(state))
}

pub(super) trait ConfirmDialog {
    fn confirm_with(
        self,
        label: &'static str,
        confirm: impl Fn(&ClickEvent, &mut Window, &mut App) -> bool + 'static,
    ) -> Self;
}

impl ConfirmDialog for Dialog {
    fn confirm_with(
        self,
        label: &'static str,
        confirm: impl Fn(&ClickEvent, &mut Window, &mut App) -> bool + 'static,
    ) -> Self {
        let confirm = Rc::new(confirm);
        let keyboard_confirm = confirm.clone();
        self.on_ok(move |event, window, cx| keyboard_confirm(event, window, cx))
            .footer(move |_, _, _, _| {
                let confirm = confirm.clone();
                vec![
                    footer_button("dialog-cancel", "取消")
                        .outline()
                        .on_click(|_, window, cx| window.close_dialog(cx)),
                    footer_button("dialog-confirm", label).primary().on_click(
                        move |event, window, cx| {
                            if confirm(event, window, cx) {
                                window.close_dialog(cx);
                            }
                        },
                    ),
                ]
            })
    }
}

pub(super) fn footer_button(id: &'static str, label: &'static str) -> Button {
    button(id).w(px(96.)).label(label)
}

pub(super) fn dialog_frame(dialog: Dialog, window: &Window) -> Dialog {
    let height = window.viewport_size().height;
    dialog
        .margin_top(px(24.))
        .max_h((height - px(128.)).max(px(168.)))
}

impl Workspace {
    pub(crate) fn confirm_close(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let remember = cx.new(|_| false);
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, cx| {
            let remember = remember.clone();
            let toggle = remember.clone();
            let view_tray = view.clone();
            let view_quit = view.clone();
            dialog_frame(dialog, window)
                .title("关闭 SuperBay")
                .w(px(460.))
                .child(
                    v_flex()
                        .gap_3()
                        .child(
                            div()
                                .text_sm()
                                .child("要收起到托盘继续运行，还是退出 SuperBay？"),
                        )
                        .child(
                            Checkbox::new("close-remember")
                                .label("记住我的选择")
                                .checked(*remember.read(cx))
                                .on_click(move |checked, window, cx| {
                                    toggle.update(cx, |value, _| *value = *checked);
                                    window.refresh();
                                }),
                        ),
                )
                .footer(move |_, _, _window, _cx| {
                    let view_tray = view_tray.clone();
                    let view_quit = view_quit.clone();
                    let remember_tray = remember.clone();
                    let remember_quit = remember.clone();
                    vec![
                        footer_button("close-cancel", "取消")
                            .outline()
                            .on_click(|_, window, cx| window.close_dialog(cx)),
                        footer_button("close-quit", "退出").outline().on_click(
                            move |_, window, cx| {
                                let remember = *remember_quit.read(cx);
                                window.close_dialog(cx);
                                let _ = view_quit.update(cx, |workspace, cx| {
                                    if remember {
                                        let _ = workspace.save_close_action(CloseAction::Quit);
                                    }
                                    workspace.quit_app(cx);
                                });
                            },
                        ),
                        footer_button("close-tray", "收起到托盘")
                            .primary()
                            .on_click(move |_, window, cx| {
                                let remember = *remember_tray.read(cx);
                                window.close_dialog(cx);
                                let _ = view_tray.update(cx, |workspace, cx| {
                                    if remember {
                                        let _ = workspace.save_close_action(CloseAction::Tray);
                                    }
                                    workspace.hide_to_tray(window, cx);
                                });
                            }),
                    ]
                })
        });
    }

    pub(super) fn prune_orphans_dialog(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(settings) = &self.settings else {
            return;
        };
        let database = settings.database();
        let mut roots: Vec<_> = settings
            .presets()
            .into_iter()
            .map(|(_, path)| path)
            .collect();
        if let Some(root) = &self.root {
            roots.push(root.clone());
        }
        roots.sort();
        roots.dedup();
        let candidates = match crate::storage::Store::open(&database)
            .and_then(|store| store.orphan_candidates(&roots))
        {
            Ok(paths) => paths,
            Err(error) => {
                Self::notify_error(error, window, cx);
                return;
            }
        };
        if candidates.is_empty() {
            window.push_notification("没有需要清理的失效记录", cx);
            return;
        }
        let count = candidates.len();
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, cx| {
            let view = view.clone();
            let database = database.clone();
            let roots = roots.clone();
            dialog_frame(dialog, window)
                .title("清理失效记录")
                .w(px(520.))
                .child(
                    v_flex()
                        .gap_3()
                        .child(div().text_sm().child(format!(
                            "将删除 {count} 条不存在路径的固定、标签、命令和启动记录。"
                        )))
                        .child(
                            div()
                                .text_xs()
                                .text_color(cx.theme().muted_foreground)
                                .child(
                                    "只处理当前可用工作区；工具文件和笔记不会被删除。目录只是临时不可用时，请不要清理。",
                                ),
                        ),
                )
                .confirm_with("清理", move |_, window, cx| {
                    match crate::storage::Store::open(&database)
                        .and_then(|mut store| store.prune_orphans(&roots))
                    {
                        Ok(removed) => {
                            window.push_notification(format!("已清理 {removed} 条失效记录"), cx);
                            let _ = view.update(cx, |this, cx| {
                                this.refresh(window, cx);
                                cx.notify();
                            });
                        }
                        Err(error) => Workspace::notify_error(error, window, cx),
                    }
                    true
                })
        });
    }

    pub(super) fn index_for_path(&self, path: &Path) -> Option<usize> {
        self.catalog
            .entries
            .iter()
            .position(|entry| entry.path == path)
    }

    pub(super) fn args_dialog(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(entry) = self.selected_entry() else {
            return;
        };
        let name = entry.name.clone();
        let path = entry.path.clone();
        let input = cx
            .new(|cx| InputState::new(window, cx).default_value(entry.metadata.last_args.clone()));
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, _| {
            let input = input.clone();
            let view = view.clone();
            let path = path.clone();
            dialog_frame(dialog, window)
                .title(format!("启动 {}", name))
                .child(field("附加参数", &input))
                .confirm_with("启动", move |_, window, cx| {
                    let args = input.read(cx).value();
                    let _ = view.update(cx, |this, cx| {
                        if let Some(index) = this.index_for_path(&path) {
                            this.run_entry(index, Some(args.as_ref()), false, None, window, cx);
                        }
                    });
                    true
                })
        });
    }

    pub(super) fn metadata_dialog(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let (Some(entry), Some(settings)) = (self.selected_entry(), &self.settings) else {
            return;
        };
        let path = entry.data_path().to_path_buf();
        let database = settings.database();
        let metadata = &entry.metadata;
        let command = cx.new(|cx| {
            InputState::new(window, cx)
                .placeholder("留空时自动识别启动入口")
                .default_value(metadata.command.clone().unwrap_or_default())
        });
        let tags = cx.new(|cx| InputState::new(window, cx).default_value(metadata.tags.clone()));
        let aliases =
            cx.new(|cx| InputState::new(window, cx).default_value(metadata.aliases.join(", ")));
        let power_shell = cx.new(|_| metadata.shell == "powershell");
        let admin = cx.new(|_| metadata.admin);
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, cx| {
            let power_checked = *power_shell.read(cx);
            let admin_checked = *admin.read(cx);
            let command = command.clone();
            let tags = tags.clone();
            let aliases = aliases.clone();
            let power_shell = power_shell.clone();
            let power_switch = power_shell.clone();
            let admin = admin.clone();
            let admin_switch = admin.clone();
            let database = database.clone();
            let path = path.clone();
            let view = view.clone();
            dialog_frame(dialog, window)
                .title("编辑启动与搜索信息")
                .child(
                    v_flex()
                        .gap_3()
                        .child(field("自定义启动命令", &command))
                        .child(
                            Checkbox::new("powershell")
                                .checked(power_checked)
                                .label("使用 PowerShell")
                                .on_click(move |checked, window, cx| {
                                    power_switch.update(cx, |flag, _| *flag = *checked);
                                    window.refresh();
                                }),
                        )
                        .child(
                            Checkbox::new("admin")
                                .checked(admin_checked)
                                .label("总是以管理员身份启动")
                                .on_click(move |checked, window, cx| {
                                    admin_switch.update(cx, |flag, _| *flag = *checked);
                                    window.refresh();
                                }),
                        )
                        .child(field("标签（逗号分隔）", &tags))
                        .child(field("搜索别名（逗号分隔）", &aliases)),
                )
                .confirm_with("保存", move |_, window, cx| {
                    let command = command.read(cx).value().trim().to_owned();
                    let tags = split_labels(tags.read(cx).value().as_ref()).join(",");
                    let aliases = split_labels(aliases.read(cx).value().as_ref());
                    let shell = if *power_shell.read(cx) {
                        "powershell"
                    } else {
                        "cmd"
                    };
                    let admin = *admin.read(cx);
                    if let Err(error) = Store::open(&database)
                        .and_then(|db| db.edit(&path, &tags, &aliases, &command, shell, admin))
                    {
                        Self::notify_error(error.context("无法保存条目信息"), window, cx);
                        return false;
                    }
                    let _ = view.update(cx, |this, cx| this.refresh(window, cx));
                    true
                })
        });
    }
}

fn split_labels(text: &str) -> Vec<String> {
    text.split([',', '，'])
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect()
}
