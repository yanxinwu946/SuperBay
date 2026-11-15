use anyhow::{Context as _, Result, bail};
use gpui::{div, prelude::*, px};
use gpui_component::{
    ActiveTheme, Disableable, Icon, IconName, Sizable, StyledExt, button::ButtonVariants, h_flex,
    kbd::Kbd, switch::Switch, v_flex,
};

use super::{dialogs::field, launch_settings::pick_path, *};

#[derive(Clone, Copy, Eq, PartialEq)]
enum Tab {
    Appearance,
    Tray,
    Terminal,
    Workspaces,
    Scoop,
    About,
}

impl Tab {
    const ALL: [(Self, &str, IconName); 6] = [
        (Self::Appearance, "外观", IconName::Palette),
        (Self::Tray, "托盘", IconName::Minimize),
        (Self::Terminal, "外部终端", IconName::SquareTerminal),
        (Self::Workspaces, "工作区", IconName::FolderOpen),
        (Self::Scoop, "Scoop", IconName::Frame),
        (Self::About, "关于", IconName::Info),
    ];
}

pub(super) struct SettingsPage {
    tab: Tab,
    pub(super) focus: FocusHandle,
    terminal: Entity<InputState>,
    arguments: Entity<InputState>,
    scoop: Entity<InputState>,
    hotkey_recording: Option<gpui::Subscription>,
    pub(super) hotkey_error: Option<gpui::SharedString>,
}

impl SettingsPage {
    pub(super) fn stop_hotkey_recording(&mut self) {
        self.hotkey_recording = None;
    }
}

impl Workspace {
    fn create_settings_page(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.settings_page.is_some() {
            return;
        }
        let Some(settings) = &self.settings else {
            return;
        };
        let (terminal, arguments) = settings.terminal();
        let scoop = settings.scoop_root().unwrap_or_default().to_owned();
        self.settings_page = Some(SettingsPage {
            tab: Tab::Appearance,
            focus: cx.focus_handle(),
            terminal: cx.new(|cx| {
                InputState::new(window, cx)
                    .default_value(terminal.to_owned())
                    .placeholder("使用 Windows 控制台")
            }),
            arguments: cx.new(|cx| {
                InputState::new(window, cx)
                    .default_value(arguments.to_owned())
                    .placeholder(r#"-d "{cwd}" {command}"#)
            }),
            scoop: cx.new(|cx| {
                InputState::new(window, cx)
                    .default_value(scoop)
                    .placeholder("自动检测")
            }),
            hotkey_recording: None,
            hotkey_error: None,
        });
    }

    pub(super) fn toggle_settings(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.settings_page.take().is_some() {
            self.focus_search(window, cx);
        } else {
            self.create_settings_page(window, cx);
            if let Some(page) = &self.settings_page {
                page.focus.focus(window);
            }
        }
        cx.notify();
    }

    pub(crate) fn open_tray_settings(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.settings_page.is_none() {
            self.create_settings_page(window, cx);
        }
        if let Some(page) = self.settings_page.as_mut() {
            page.stop_hotkey_recording();
            page.hotkey_error = None;
            page.tab = Tab::Tray;
            page.focus.focus(window);
        }
        cx.notify();
    }

    pub(super) fn cancel_hotkey_recording(&mut self, cx: &mut Context<Self>) {
        if let Some(page) = self.settings_page.as_mut()
            && page.hotkey_recording.is_some()
        {
            page.stop_hotkey_recording();
            page.hotkey_error = None;
            cx.notify();
        }
    }

    fn toggle_hotkey_recording(&mut self, cx: &mut Context<Self>) {
        let Some(page) = self.settings_page.as_mut() else {
            return;
        };
        if page.hotkey_recording.is_some() {
            page.stop_hotkey_recording();
            page.hotkey_error = None;
            cx.notify();
            return;
        }
        let view = cx.entity().downgrade();
        let subscription = cx.intercept_keystrokes(move |event, window, cx| {
            let _ = view.update(cx, |this, cx| {
                this.capture_hotkey(&event.keystroke, window, cx)
            });
            cx.stop_propagation();
            window.prevent_default();
        });
        if let Some(page) = self.settings_page.as_mut() {
            page.hotkey_recording = Some(subscription);
            page.hotkey_error = None;
        }
        cx.notify();
    }

    fn capture_hotkey(
        &mut self,
        keystroke: &Keystroke,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self
            .settings_page
            .as_ref()
            .is_none_or(|page| page.hotkey_recording.is_none())
        {
            return;
        }
        if keystroke.key == "escape" && !keystroke.modifiers.modified() {
            if let Some(page) = self.settings_page.as_mut() {
                page.stop_hotkey_recording();
                page.hotkey_error = None;
            }
            cx.notify();
            return;
        }
        if crate::hotkey::is_modifier(&keystroke.key) {
            return;
        }
        if let Some(page) = self.settings_page.as_mut() {
            page.hotkey_error = None;
        }
        if let Err(error) = self.apply_hotkey(Some(keystroke.clone()), window, cx) {
            if let Some(page) = self.settings_page.as_mut() {
                page.hotkey_error = Some(format!("{error:#}").into());
            }
            cx.notify();
        }
    }

    fn reset_hotkey(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let default = Keystroke::parse(crate::hotkey::DEFAULT).expect("默认唤起快捷键应当有效");
        if let Err(error) = self.apply_hotkey(Some(default), window, cx) {
            Self::notify_error(error, window, cx);
        }
    }

    fn disable_hotkey(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if let Err(error) = self.apply_hotkey(None, window, cx) {
            Self::notify_error(error, window, cx);
        }
    }

    pub(super) fn settings_content(&self, cx: &mut Context<Self>) -> Option<gpui::AnyElement> {
        let page = self.settings_page.as_ref()?;
        let settings = self.settings.as_ref()?;
        let theme = cx.theme();
        let (line, muted, background) = (theme.border, theme.muted_foreground, theme.background);
        let selected = page.tab;
        let mut content = v_flex().gap_4().w_full().max_w(px(680.));
        match selected {
            Tab::Appearance => {
                content = content
                    .child(section(
                        "主题",
                        line,
                        v_flex().gap_2().child(
                            h_flex().gap_2().children(
                                [
                                    ("system", "跟随系统", IconName::Frame),
                                    ("light", "浅色", IconName::Sun),
                                    ("dark", "深色", IconName::Moon),
                                ]
                                .map(|(key, title, icon)| {
                                    let button = button(key).label(title).icon(icon).flex_1();
                                    (if settings.theme() == key {
                                        button.primary()
                                    } else {
                                        button.outline()
                                    })
                                    .on_click(cx.listener(
                                        move |this, _, window, cx| this.set_theme(key, window, cx),
                                    ))
                                }),
                            ),
                        ),
                    ))
                    .child(section(
                        "工具视图",
                        line,
                        h_flex().gap_2().children(
                            [
                                (false, "列表", IconName::Menu),
                                (true, "网格", IconName::LayoutDashboard),
                            ]
                            .map(|(grid, label, icon)| {
                                let button = button(label).label(label).icon(icon).flex_1();
                                (if self.grid == grid {
                                    button.primary()
                                } else {
                                    button.outline()
                                })
                                .on_click(cx.listener(
                                    move |this, _, window, cx| this.set_grid(grid, window, cx),
                                ))
                            }),
                        ),
                    ));
            }
            Tab::Tray => {
                let available = self.tray.is_some();
                let stored_hotkey = settings.hotkey().trim();
                let configured = Keystroke::parse(stored_hotkey).ok();
                let active = self.tray.as_ref().and_then(crate::tray::Handle::hotkey);
                let registered = self
                    .tray
                    .as_ref()
                    .is_some_and(crate::tray::Handle::hotkey_registered);
                let invalid = !stored_hotkey.is_empty() && configured.is_none();
                let failed = available
                    && configured.is_some()
                    && (!registered || active.as_ref() != configured.as_ref());
                let recording = page.hotkey_recording.is_some();
                let close_action = self.close_action();
                let close_hint = match close_action {
                    CloseAction::Ask => "关闭主窗口时，询问是收起到托盘还是退出。",
                    CloseAction::Tray => "关闭主窗口后继续在托盘运行。",
                    CloseAction::Quit => "关闭主窗口时直接退出 SuperBay。",
                };
                let hotkey_label = match &configured {
                    Some(hotkey) => Kbd::format(hotkey),
                    None => "未设置".to_owned(),
                };
                content = content
                    .child(section(
                        "关闭与退出",
                        line,
                        v_flex()
                            .gap_3()
                            .child(
                                h_flex().gap_2().children(
                                    [CloseAction::Ask, CloseAction::Tray, CloseAction::Quit]
                                        .into_iter()
                                        .enumerate()
                                        .map(|(index, action)| {
                                            let button = button(("close-action", index))
                                                .label(action.label())
                                                .flex_1();
                                            (if close_action == action {
                                                button.primary()
                                            } else {
                                                button.outline()
                                            })
                                            .disabled(!available)
                                            .on_click(
                                                cx.listener(move |this, _, window, cx| {
                                                    this.set_close_action(action, window, cx)
                                                }),
                                            )
                                        }),
                                ),
                            )
                            .child(div().text_xs().text_color(muted).child(close_hint)),
                    ))
                    .child(section(
                        "唤起快捷键",
                        line,
                        v_flex()
                            .gap_3()
                            .child(
                                v_flex().gap_1().child("全局唤起快捷键").child(
                                    div()
                                        .text_xs()
                                        .text_color(muted)
                                        .child("在任何窗口中按下这个组合键，召回 SuperBay。"),
                                ),
                            )
                            .child(
                                h_flex()
                                    .gap_2()
                                    .items_center()
                                    .child(
                                        button("settings-hotkey-record")
                                            .outline()
                                            .disabled(!available)
                                            .label(if recording {
                                                "请按下新的快捷键…".to_owned()
                                            } else {
                                                hotkey_label.clone()
                                            })
                                            .on_click(cx.listener(|this, _, _, cx| {
                                                this.toggle_hotkey_recording(cx)
                                            })),
                                    )
                                    .child(
                                        button("settings-hotkey-default")
                                            .ghost()
                                            .disabled(!available)
                                            .label("恢复默认")
                                            .on_click(cx.listener(|this, _, window, cx| {
                                                this.reset_hotkey(window, cx)
                                            })),
                                    )
                                    .when(configured.is_some(), |this| {
                                        this.child(
                                            button("settings-hotkey-disable")
                                                .ghost()
                                                .disabled(!available)
                                                .label("停用")
                                                .on_click(cx.listener(|this, _, window, cx| {
                                                    this.disable_hotkey(window, cx)
                                                })),
                                        )
                                    }),
                            )
                            .when(recording, |this| {
                                this.child(
                                    div()
                                        .text_xs()
                                        .text_color(muted)
                                        .child("按下包含 Ctrl、Alt 或 Win 的组合键，按 Esc 取消。"),
                                )
                            })
                            .when_some(page.hotkey_error.clone(), |this, error| {
                                this.child(div().text_xs().text_color(theme.danger).child(error))
                            })
                            .when(invalid, |this| {
                                this.child(
                                    div()
                                        .text_xs()
                                        .text_color(theme.danger)
                                        .child("当前快捷键配置无法识别，请重新设置。"),
                                )
                            })
                            .when(failed, |this| {
                                this.child(
                                    div()
                                        .text_xs()
                                        .text_color(theme.danger)
                                        .child("当前快捷键未能生效，可能已被其他程序占用。"),
                                )
                            }),
                    ))
                    .child(section(
                        "启动",
                        line,
                        v_flex()
                            .gap_3()
                            .child(toggle_row(
                                "settings-start-minimized",
                                "启动时隐藏到托盘",
                                "启动后直接留在托盘，不占用桌面。",
                                settings.start_minimized(),
                                !available,
                                muted,
                                cx.listener(|this, enabled, window, cx| {
                                    this.set_start_minimized(*enabled, window, cx)
                                }),
                            ))
                            .child(toggle_row(
                                "settings-autostart",
                                "开机自动启动",
                                "登录 Windows 后自动在托盘中启动。",
                                self.autostart,
                                !available,
                                muted,
                                cx.listener(|this, enabled, window, cx| {
                                    this.set_autostart(*enabled, window, cx)
                                }),
                            )),
                    ));
                if !available {
                    content = content.child(
                        div()
                            .text_xs()
                            .text_color(muted)
                            .child("当前系统未能创建托盘图标，托盘相关选项暂不可用。"),
                    );
                }
            }
            Tab::Terminal => {
                let browse = page.terminal.clone();
                content = content.child(section("启动命令行工具", line, v_flex().gap_3()
                    .child(h_flex().items_end().gap_2().child(div().flex_1().min_w_0().child(field("终端程序", &page.terminal)))
                        .child(button("pick-terminal").outline().label("浏览").on_click(move |_, window, cx| pick_path(&browse, false, window, cx))))
                    .child(field("自定义参数", &page.arguments))
                    .child(div().text_xs().text_color(muted).child("留空可自动适配 WezTerm、Windows Terminal 和 Alacritty。自定义参数可用 {cwd} 和 {command}。"))
                    .child(h_flex().gap_2()
                        .child(button("test-terminal").outline().label("试开终端").on_click(cx.listener(|this, _, window, cx| this.apply_terminal(false, window, cx))))
                        .child(button("save-terminal").primary().label("保存").on_click(cx.listener(|this, _, window, cx| this.apply_terminal(true, window, cx)))))));
            }
            Tab::Workspaces => {
                content =
                    content
                        .child(section(
                            "目录筛选",
                            line,
                            v_flex()
                                .gap_3()
                                .child(
                                    h_flex()
                                        .gap_3()
                                        .child(
                                            v_flex()
                                                .flex_1()
                                                .gap_1()
                                                .child("仅扫描规范命名目录")
                                                .child(
                                                    div().text_xs().text_color(muted).child(
                                                        "第一层采用 A-01 或 A-01-名称 格式。",
                                                    ),
                                                ),
                                        )
                                        .child(
                                            Switch::new("settings-named-only")
                                                .checked(settings.named_only())
                                                .on_click(cx.listener(|this, _, window, cx| {
                                                    this.toggle_named_only(window, cx)
                                                })),
                                        ),
                                )
                                .child(
                                    h_flex()
                                        .gap_3()
                                        .child(
                                            v_flex()
                                                .flex_1()
                                                .gap_1()
                                                .child("隐藏无启动项的目录")
                                                .child(div().text_xs().text_color(muted).child(
                                                    "只隐藏扫描结果里没有可启动工具的目录。",
                                                )),
                                        )
                                        .child(
                                            Switch::new("settings-hide-empty-categories")
                                                .checked(settings.hide_empty_categories())
                                                .on_click(cx.listener(|this, _, window, cx| {
                                                    this.toggle_empty_categories(window, cx)
                                                })),
                                        ),
                                ),
                        ))
                        .child(section(
                            "已保存工作区",
                            line,
                            v_flex()
                                .gap_2()
                                .children(settings.presets().into_iter().enumerate().map(
                                    |(index, (name, path))| {
                                        let active = self.root.as_ref() == Some(&path);
                                        button(("settings-preset", index))
                                            .ghost()
                                            .justify_start()
                                            .label(name)
                                            .icon(if active {
                                                IconName::Check
                                            } else {
                                                IconName::Folder
                                            })
                                            .tooltip(path.display().to_string())
                                            .on_click(cx.listener(move |this, _, window, cx| {
                                                this.switch_workspace(path.clone(), window, cx)
                                            }))
                                    },
                                ))
                                .child(
                                    h_flex()
                                        .gap_2()
                                        .child(
                                            button("settings-add-workspace")
                                                .outline()
                                                .icon(IconName::Plus)
                                                .label("添加工作区")
                                                .on_click(cx.listener(|this, _, window, cx| {
                                                    this.pick_workspace(window, cx)
                                                })),
                                        )
                                        .child(
                                            button("settings-open-guide")
                                                .ghost()
                                                .icon(IconName::BookOpen)
                                                .label("目录规范")
                                                .on_click(cx.listener(|this, _, window, cx| {
                                                    this.open_guide(window, cx)
                                                })),
                                        ),
                                ),
                        ))
                        .child(section(
                            "维护",
                            line,
                            v_flex()
                                .gap_3()
                                .child(div().text_xs().text_color(muted).child(
                                    "清理目录改名或移动后留下的失效记录；不会删除工具文件和笔记。",
                                ))
                                .child(
                                    h_flex().child(
                                        button("settings-prune-orphans")
                                            .outline()
                                            .icon(IconName::Delete)
                                            .label("清理失效记录")
                                            .on_click(cx.listener(|this, _, window, cx| {
                                                this.prune_orphans_dialog(window, cx)
                                            })),
                                    ),
                                ),
                        ));
            }
            Tab::Scoop => {
                let browse = page.scoop.clone();
                content = content.child(section(
                    "安装与软件源",
                    line,
                    v_flex()
                        .gap_3()
                        .child(
                            div()
                                .text_sm()
                                .text_color(muted)
                                .child("进入 Scoop 管理可安装 Scoop、查看更新、管理软件源和设置。"),
                        )
                        .child(
                            h_flex()
                                .items_end()
                                .gap_2()
                                .child(
                                    div()
                                        .flex_1()
                                        .min_w_0()
                                        .child(field("安装目录", &page.scoop)),
                                )
                                .child(
                                    button("settings-scoop-browse")
                                        .outline()
                                        .label("浏览")
                                        .on_click(move |_, window, cx| {
                                            pick_path(&browse, true, window, cx)
                                        }),
                                ),
                        )
                        .child(
                            h_flex()
                                .gap_2()
                                .child(
                                    button("settings-scoop-save")
                                        .outline()
                                        .label("保存目录")
                                        .on_click(cx.listener(|this, _, window, cx| {
                                            this.save_scoop_location(window, cx)
                                        })),
                                )
                                .child(
                                    button("settings-scoop-open")
                                        .primary()
                                        .label("打开 Scoop 管理")
                                        .on_click(cx.listener(|this, _, window, cx| {
                                            this.scoop_manager(window, cx)
                                        })),
                                ),
                        ),
                ));
            }
            Tab::About => {
                content = content.child(
                    v_flex()
                        .gap_4()
                        .p_5()
                        .rounded_lg()
                        .border_1()
                        .border_color(line)
                        .child(
                            h_flex()
                                .gap_3()
                                .child(gpui::img(crate::LOGO.clone()).size(px(48.)).flex_shrink_0())
                                .child(
                                    v_flex()
                                        .gap_1()
                                        .child(
                                            div()
                                                .text_size(px(22.))
                                                .font_semibold()
                                                .child("SuperBay"),
                                        )
                                        .child(
                                            div()
                                                .text_sm()
                                                .text_color(muted)
                                                .child(concat!("版本 ", env!("CARGO_PKG_VERSION"))),
                                        ),
                                ),
                        )
                        .child(
                            h_flex()
                                .justify_between()
                                .pt_3()
                                .border_t_1()
                                .border_color(line)
                                .child(div().text_sm().text_color(muted).child("作者"))
                                .child(div().text_sm().font_semibold().child("Yanxinwu946")),
                        ),
                );
            }
        }
        let current_title = Tab::ALL
            .into_iter()
            .find(|(tab, _, _)| *tab == selected)
            .map_or("设置", |(_, title, _)| title);
        let header = h_flex()
            .h(px(66.))
            .flex_shrink_0()
            .px_5()
            .gap_3()
            .border_b_1()
            .border_color(line)
            .child(
                button("back-settings")
                    .ghost()
                    .small()
                    .icon(IconName::ArrowLeft)
                    .label("返回工作台")
                    .on_click(cx.listener(|this, _, window, cx| this.toggle_settings(window, cx))),
            )
            .child(div().text_size(px(19.)).font_semibold().child("设置"));
        let sidebar = v_flex()
            .w(px(168.))
            .h_full()
            .flex_shrink_0()
            .px_3()
            .py_5()
            .gap_1()
            .border_r_1()
            .border_color(line)
            // Keep tab IDs distinct from the content pane ID, which uses the
            // active tab title.
            .children(
                Tab::ALL
                    .into_iter()
                    .enumerate()
                    .map(|(index, (tab, title, icon))| {
                        let button = button(("settings-tab", index))
                            .justify_start()
                            .icon(icon)
                            .label(title);
                        (if tab == selected {
                            button.primary()
                        } else {
                            button.ghost()
                        })
                        .on_click(cx.listener(move |this, _, _, cx| {
                            if let Some(page) = this.settings_page.as_mut() {
                                page.stop_hotkey_recording();
                                page.hotkey_error = None;
                                page.tab = tab;
                                cx.notify();
                            }
                        }))
                    }),
            );
        let pane = v_flex()
            .id(current_title)
            .size_full()
            .flex_1()
            .min_w_0()
            .min_h_0()
            .overflow_y_scroll()
            .px_6()
            .py_5()
            .gap_4()
            .child(
                div()
                    .text_size(px(24.))
                    .font_semibold()
                    .child(current_title),
            )
            .child(content);
        let page = v_flex()
            .size_full()
            .min_h_0()
            .bg(background)
            .child(header)
            .child(
                h_flex()
                    .flex_1()
                    .min_h_0()
                    .items_start()
                    .child(sidebar)
                    .child(pane),
            );
        Some(page.into_any_element())
    }

    fn apply_terminal(&mut self, save: bool, window: &mut Window, cx: &mut Context<Self>) {
        let Some(page) = &self.settings_page else {
            return;
        };
        let path = page.terminal.read(cx).value();
        let arguments = page.arguments.read(cx).value();
        let result = (|| -> Result<()> {
            if !arguments.trim().is_empty() && !arguments.contains("{command}") {
                bail!("终端参数必须包含 {{command}}");
            }
            if !path.trim().is_empty() {
                launch::terminal_path(path.trim())?;
            }
            if save {
                self.settings
                    .as_mut()
                    .context("无法读取应用设置")?
                    .set_terminal(path.trim(), arguments.trim())?;
                window.push_notification("终端设置已保存", cx);
                return Ok(());
            }
            let folder = self.root.clone().map_or_else(std::env::current_dir, Ok)?;
            launch::terminal_here(&folder, (path.trim(), arguments.trim()))
        })();
        if let Err(error) = result {
            Self::notify_error(error, window, cx);
        }
    }

    fn save_scoop_location(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(page) = &self.settings_page else {
            return;
        };
        let value = page.scoop.read(cx).value();
        let path = std::path::Path::new(value.trim().trim_matches('"'));
        if !path.as_os_str().is_empty() && !crate::scoop::script(path).is_file() {
            Self::notify_error(anyhow::anyhow!("此目录没有可用的 Scoop 安装"), window, cx);
            return;
        }
        if let Some(settings) = self.settings.as_mut() {
            match settings.set_scoop_root(path) {
                Ok(()) => window.push_notification("Scoop 安装目录已保存", cx),
                Err(error) => Self::notify_error(error, window, cx),
            }
        }
    }
}

fn toggle_row(
    id: &'static str,
    title: &'static str,
    description: &'static str,
    checked: bool,
    disabled: bool,
    muted: gpui::Hsla,
    on_toggle: impl Fn(&bool, &mut gpui::Window, &mut gpui::App) + 'static,
) -> impl IntoElement {
    h_flex()
        .gap_3()
        .items_start()
        .child(
            v_flex()
                .flex_1()
                .gap_1()
                .child(title)
                .child(div().text_xs().text_color(muted).child(description)),
        )
        .child(
            Switch::new(id)
                .checked(checked)
                .disabled(disabled)
                .on_click(on_toggle),
        )
}

fn section(title: &'static str, border: gpui::Hsla, content: impl IntoElement) -> impl IntoElement {
    v_flex()
        .w_full()
        .gap_3()
        .p_4()
        .rounded_lg()
        .border_1()
        .border_color(border)
        .child(
            h_flex()
                .gap_2()
                .child(Icon::new(IconName::Settings2).small())
                .child(div().text_sm().font_semibold().child(title)),
        )
        .child(content)
}
