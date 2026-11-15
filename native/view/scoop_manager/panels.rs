use gpui::{
    App, AppContext, Context, Entity, IntoElement, Styled, Window, div, prelude::*, px,
    uniform_list,
};
use gpui_component::{
    ActiveTheme, Disableable, Sizable, StyledExt, WindowExt,
    button::ButtonVariants,
    checkbox::Checkbox,
    h_flex,
    input::{Input, InputState},
    v_flex,
};
use serde_json::Value;

use super::{Manager, Operation, ScoopView, input_root, pick_path, scoop};
use crate::view::{
    Workspace, button,
    dialogs::{ConfirmDialog, dialog_frame, field},
};

#[derive(Default)]
struct ConfigPanel {
    key: Option<&'static str>,
    values: Value,
}

impl ScoopView {
    pub(super) fn select_all_results(&self, cx: &mut Context<Self>) {
        self.state.update(cx, |state, cx| {
            let Some((operation, rows)) = &state.results else {
                return;
            };
            let eligible = rows
                .iter()
                .enumerate()
                .filter_map(|(index, row)| result_eligible(*operation, row).then_some(index));
            let all_checked = eligible
                .clone()
                .all(|index| state.result_checked.contains(&index));
            state.result_checked.clear();
            if !all_checked {
                state.result_checked.extend(eligible);
            }
            cx.notify();
        });
    }

    fn run_result_selection(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let state = self.state.read(cx);
        if state.result_root.as_ref() != Some(&input_root(&self.root, cx)) {
            return;
        }
        let Some((operation, rows)) = &state.results else {
            return;
        };
        let mut values: Vec<_> = rows
            .iter()
            .enumerate()
            .filter(|(index, _)| state.result_checked.contains(index))
            .filter_map(|(_, row)| result_target(*operation, row))
            .collect();
        values.sort_unstable();
        values.dedup();
        let action = if *operation == Operation::Search {
            Operation::Install
        } else {
            Operation::Update
        };
        self.invoke_many(action, values, window, cx);
    }

    pub(super) fn result_table(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let state = self.state.read(cx);
        let (operation, rows) = state.results.as_ref().expect("results are visible");
        let operation = *operation;
        let rows = rows.clone();
        let count = rows.len();
        let busy = state.busy;
        let selected = state.result_checked.len();
        let eligible = rows
            .iter()
            .filter(|row| result_eligible(operation, row))
            .count();
        let all_checked = eligible > 0 && selected == eligible;
        let root = input_root(&self.root, cx);
        let disabled =
            busy || state.result_root.as_ref() != Some(&root) || !scoop::script(&root).is_file();
        let entity = self.state.clone();
        let focus = self.focus.clone();
        let view = cx.weak_entity();
        let line = cx.theme().border;
        let muted = cx.theme().muted_foreground;
        let package_table = matches!(operation, Operation::Search | Operation::Status);
        v_flex()
            .flex_1()
            .min_h_0()
            .px_5()
            .gap_2()
            .child(
                h_flex()
                    .h(px(35.))
                    .flex_shrink_0()
                    .gap_2()
                    .child(div().font_semibold().child(operation.label()))
                    .child(
                        div()
                            .flex_1()
                            .text_sm()
                            .text_color(muted)
                            .child(format!("{count} 项")),
                    ),
            )
            .when(package_table && count > 0, |page| {
                page.child(
                    h_flex()
                        .gap_2()
                        .child(
                            div()
                                .flex_1()
                                .text_sm()
                                .text_color(muted)
                                .child(format!("已选 {selected} 项")),
                        )
                        .child(
                            button("clear-result-selection")
                                .ghost()
                                .small()
                                .label("清空选择")
                                .disabled(disabled || selected == 0)
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.state.update(cx, |state, cx| {
                                        state.result_checked.clear();
                                        cx.notify();
                                    })
                                })),
                        )
                        .child(
                            button("run-result-selection")
                                .primary()
                                .small()
                                .label(if operation == Operation::Search {
                                    "安装所选"
                                } else {
                                    "更新所选"
                                })
                                .disabled(disabled || selected == 0)
                                .on_click(cx.listener(|this, _, window, cx| {
                                    this.run_result_selection(window, cx)
                                })),
                        ),
                )
            })
            .when(count == 0, |body| {
                body.child(div().p_4().text_sm().text_color(muted).child(
                    if operation == Operation::Status {
                        "本地软件源中没有可更新或需要处理的软件"
                    } else {
                        "没有找到匹配的软件"
                    },
                ))
            })
            .when(count > 0, |body| {
                body.child(
                    v_flex()
                        .flex_1()
                        .min_h_0()
                        .overflow_hidden()
                        .rounded_lg()
                        .border_1()
                        .border_color(line)
                        .child(
                            result_row()
                                .border_b_1()
                                .border_color(line)
                                .text_color(muted)
                                .when(package_table, |row| {
                                    row.child(result_checkbox(
                                        Checkbox::new("result-check-all")
                                            .checked(all_checked)
                                            .disabled(disabled || eligible == 0)
                                            .on_click(cx.listener(|this, _, _, cx| {
                                                this.select_all_results(cx)
                                            })),
                                    ))
                                })
                                .child(result_name("软件包"))
                                .when(operation == Operation::Status, |row| {
                                    row.child(super::cell("已安装", 112.))
                                })
                                .when(package_table, |row| {
                                    row.child(super::cell("可用版本", 112.))
                                })
                                .child(div().flex_1().child(if operation == Operation::Search {
                                    "软件源 / 匹配项"
                                } else {
                                    "状态"
                                }))
                                .when(package_table, |row| row.child(super::cell("操作", 65.))),
                        )
                        .child(
                            uniform_list("scoop-object-results", count, move |range, _, cx| {
                                let state = entity.read(cx);
                                range
                                    .filter_map(|index| {
                                        let result = rows.get(index)?;
                                        let note = [&result.source, &result.note]
                                            .into_iter()
                                            .filter(|value| !value.is_empty())
                                            .map(String::as_str)
                                            .collect::<Vec<_>>()
                                            .join(" · ");
                                        let action = if operation == Operation::Search {
                                            Operation::Install
                                        } else {
                                            Operation::Update
                                        };
                                        let package = result_target(operation, result);
                                        let enabled = !disabled && package.is_some();
                                        let remote_source = (operation == Operation::Search
                                            && package.is_none()
                                            && result.source.contains("://"))
                                        .then(|| result.source.clone());
                                        let view = view.clone();
                                        let checkbox_state = entity.clone();
                                        let checkbox_focus = focus.clone();
                                        let checked = state.result_checked.contains(&index);
                                        Some(
                                            result_row()
                                                .border_b_1()
                                                .border_color(line)
                                                .when(package_table, |row| {
                                                    row.child(result_checkbox(
                                                        Checkbox::new(("result-check", index))
                                                            .checked(checked)
                                                            .disabled(!enabled)
                                                            .on_click(
                                                                move |checked, window, cx| {
                                                                    checkbox_focus.focus(window);
                                                                    checkbox_state.update(
                                                                        cx,
                                                                        |state, cx| {
                                                                            if *checked {
                                                                                state
                                                                                    .result_checked
                                                                                    .insert(index);
                                                                            } else {
                                                                                state
                                                                                    .result_checked
                                                                                    .remove(&index);
                                                                            }
                                                                            cx.notify();
                                                                        },
                                                                    );
                                                                },
                                                            ),
                                                    ))
                                                })
                                                .child(
                                                    result_name(result.name.clone())
                                                        .font_semibold(),
                                                )
                                                .when(operation == Operation::Status, |row| {
                                                    row.child(super::cell(
                                                        result.current.clone(),
                                                        112.,
                                                    ))
                                                })
                                                .when(package_table, |row| {
                                                    row.child(super::cell(
                                                        result.latest.clone(),
                                                        112.,
                                                    ))
                                                })
                                                .child(
                                                    div()
                                                        .flex_1()
                                                        .min_w_0()
                                                        .truncate()
                                                        .text_color(muted)
                                                        .child(note),
                                                )
                                                .when(package_table, |row| {
                                                    let action_button = if let Some(source) =
                                                        remote_source
                                                    {
                                                        button(("result-action", index))
                                                            .outline()
                                                            .small()
                                                            .w(px(65.))
                                                            .label("添加源")
                                                            .disabled(disabled)
                                                            .tooltip(
                                                                "先添加这个远程软件源，再搜索安装",
                                                            )
                                                            .on_click(move |_, window, cx| {
                                                                let _ =
                                                                    view.update(cx, |this, cx| {
                                                                        this.buckets_panel_with(
                                                                            Some(source.clone()),
                                                                            window,
                                                                            cx,
                                                                        )
                                                                    });
                                                            })
                                                    } else {
                                                        button(("result-action", index))
                                                            .outline()
                                                            .small()
                                                            .w(px(65.))
                                                            .label(action.label())
                                                            .disabled(!enabled)
                                                            .on_click(move |_, window, cx| {
                                                                if let Some(package) = &package {
                                                                    let _ = view.update(
                                                                        cx,
                                                                        |this, cx| {
                                                                            this.invoke(
                                                                                action,
                                                                                package.clone(),
                                                                                window,
                                                                                cx,
                                                                            )
                                                                        },
                                                                    );
                                                                }
                                                            })
                                                    };
                                                    row.child(action_button)
                                                }),
                                        )
                                    })
                                    .collect()
                            })
                            .flex_1()
                            .min_h_0(),
                        ),
                )
            })
    }

    pub(super) fn settings_panel(&self, window: &mut Window, cx: &mut Context<Self>) {
        let root = input_root(&self.root, cx);
        let manager = self.state.clone();
        let panel = cx.new(|_| ConfigPanel::default());
        let value = cx.new(|cx| InputState::new(window, cx));
        let load = panel.clone();
        let read_root = root.clone();
        job(
            &manager,
            window,
            cx,
            move || scoop::configured_values(&read_root),
            move |values, _, cx| {
                load.update(cx, |panel, _| panel.values = values);
            },
        );
        window.open_dialog(cx, move |dialog, window, cx| {
            let model = panel.read(cx);
            let busy = manager.read(cx).busy;
            let key = model.key;
            let line = cx.theme().border;
            let muted = cx.theme().muted_foreground;
            let body = h_flex()
                .items_start()
                .gap_4()
                .child(
                    v_flex()
                        .id("scoop-setting-keys")
                        .w(px(232.))
                        .h(px(300.))
                        .flex_shrink_0()
                        .overflow_y_scroll()
                        .pr_2()
                        .border_r_1()
                        .border_color(line)
                        .gap_1()
                        .children(scoop::CONFIG_FIELDS.iter().copied().map(|entry| {
                            let label = config_label(entry);
                            let model = panel.clone();
                            let input = value.clone();
                            let button = button(entry).small().justify_start().label(label);
                            (if key == Some(entry) {
                                button.primary()
                            } else {
                                button.ghost()
                            })
                            .disabled(busy)
                            .on_click(move |_, window, cx| {
                                let text = config_text(&model.read(cx).values[entry]);
                                model.update(cx, |model, _| model.key = Some(entry));
                                input.update(cx, |input, cx| input.set_value(text, window, cx));
                                window.refresh();
                            })
                        })),
                )
                .child(
                    v_flex()
                        .flex_1()
                        .min_w_0()
                        .gap_3()
                        .when(key.is_none(), |body| {
                            body.child(div().text_sm().text_color(muted).child(if busy {
                                "正在读取 Scoop 设置…"
                            } else {
                                "从左侧选择一项设置"
                            }))
                        })
                        .when_some(key, |body, key| {
                            let persist = {
                                let (panel, manager, root) =
                                    (panel.clone(), manager.clone(), root.clone());
                                move |next: Option<String>, window: &mut Window, cx: &mut App| {
                                    let (panel, root) = (panel.clone(), root.clone());
                                    job(
                                        &manager,
                                        window,
                                        cx,
                                        move || {
                                            scoop::set_config(&root, key, next.as_deref())?;
                                            scoop::configured_values(&root)
                                        },
                                        move |values, window, cx| {
                                            panel.update(cx, |panel, _| panel.values = values);
                                            window.push_notification("Scoop 设置已更新", cx);
                                        },
                                    );
                                }
                            };
                            let reset = persist.clone();
                            let is_bool = bool_setting(key);
                            let current = &model.values[key];
                            let current_display = if current.is_null() {
                                "默认".to_owned()
                            } else {
                                config_text(current)
                            };
                            body.child(div().font_semibold().child(config_label(key)))
                                .child(div().text_xs().text_color(muted).child(key))
                                .when(is_bool, |body| {
                                    body.child(
                                        div()
                                            .text_sm()
                                            .child(format!("当前值 · {current_display}")),
                                    )
                                    .child(
                                        h_flex().gap_2().children(
                                            [(true, "启用"), (false, "关闭")].map(
                                                |(flag, label)| {
                                                    let persist = persist.clone();
                                                    let button = button(label).small().label(label);
                                                    (if current.as_bool() == Some(flag) {
                                                        button.primary()
                                                    } else {
                                                        button.outline()
                                                    })
                                                    .disabled(busy)
                                                    .on_click(move |_, window, cx| {
                                                        persist(Some(flag.to_string()), window, cx)
                                                    })
                                                },
                                            ),
                                        ),
                                    )
                                })
                                .when(!is_bool, |body| {
                                    let input = value.clone();
                                    let browse = input.clone();
                                    body.child(
                                        h_flex()
                                            .gap_2()
                                            .child(
                                                div().flex_1().min_w_0().child(Input::new(&input)),
                                            )
                                            .when(key == "cache_path", |row| {
                                                row.child(
                                                    button("browse-scoop-cache")
                                                        .outline()
                                                        .label("浏览")
                                                        .disabled(busy)
                                                        .on_click(move |_, window, cx| {
                                                            pick_path(&browse, true, window, cx)
                                                        }),
                                                )
                                            }),
                                    )
                                    .child(
                                        button("save-scoop-option")
                                            .primary()
                                            .label("保存")
                                            .disabled(busy)
                                            .on_click(move |_, window, cx| {
                                                persist(
                                                    Some(input.read(cx).value().trim().to_owned()),
                                                    window,
                                                    cx,
                                                );
                                            }),
                                    )
                                })
                                .child(
                                    button("reset-scoop-option")
                                        .ghost()
                                        .label("恢复默认")
                                        .disabled(busy || current.is_null())
                                        .on_click(move |_, window, cx| reset(None, window, cx)),
                                )
                        }),
                );
            dialog_frame(dialog, window)
                .title("Scoop 设置")
                .w(px(680.))
                .child(body)
        });
    }

    pub(super) fn buckets_panel(&self, window: &mut Window, cx: &mut Context<Self>) {
        self.buckets_panel_with(None, window, cx);
    }

    pub(super) fn buckets_panel_with(
        &self,
        prefill: Option<String>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let root = input_root(&self.root, cx);
        let manager = self.state.clone();
        let rows = cx.new(|_| Vec::<scoop::ObjectRow>::new());
        let (name_value, source_value) = prefill.map_or((String::new(), String::new()), |source| {
            (suggested_bucket_name(&source), source)
        });
        let name = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(name_value)
                .placeholder("例如 extras")
        });
        let source = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(source_value)
                .placeholder("HTTPS 仓库地址，内置软件源可留空")
        });
        let read_root = root.clone();
        let list = rows.clone();
        job(
            &manager,
            window,
            cx,
            move || scoop::buckets(&read_root),
            move |values, _, cx| list.update(cx, |rows, _| *rows = values),
        );
        window.open_dialog(cx, move |dialog, window, cx| {
            let busy = manager.read(cx).busy;
            let update = {
                let (rows, root, manager) = (rows.clone(), root.clone(), manager.clone());
                move |name: String, source: Option<String>, window: &mut Window, cx: &mut App| {
                    let (rows, root) = (rows.clone(), root.clone());
                    job(
                        &manager,
                        window,
                        cx,
                        move || {
                            scoop::change_bucket(&root, &name, source.as_deref())?;
                            scoop::buckets(&root)
                        },
                        move |values, _, cx| rows.update(cx, |rows, _| *rows = values),
                    );
                }
            };
            let add = update.clone();
            let (name_input, source_input) = (name.clone(), source.clone());
            let line = cx.theme().border;
            let muted = cx.theme().muted_foreground;
            dialog_frame(dialog, window)
                .title("Scoop 软件源")
                .w(px(720.))
                .child(
                    v_flex()
                        .gap_3()
                        .child(
                            h_flex()
                                .gap_2()
                                .items_end()
                                .child(div().w(px(126.)).child(field("名称", &name)))
                                .child(div().flex_1().min_w_0().child(field("仓库地址", &source)))
                                .child(
                                    button("add-scoop-bucket")
                                        .primary()
                                        .label("添加")
                                        .disabled(busy)
                                        .on_click(move |_, window, cx| {
                                            let name =
                                                name_input.read(cx).value().trim().to_owned();
                                            let source =
                                                source_input.read(cx).value().trim().to_owned();
                                            add(name, Some(source), window, cx);
                                        }),
                                ),
                        )
                        .child(
                            v_flex().gap_1().children(
                                rows.read(cx)
                                    .iter()
                                    .cloned()
                                    .enumerate()
                                    .map(|(index, row)| {
                                        let update = update.clone();
                                        let name = row.name.clone();
                                        h_flex()
                                            .min_w_0()
                                            .gap_2()
                                            .border_t_1()
                                            .border_color(line)
                                            .py_2()
                                            .child(
                                                div()
                                                    .w(px(100.))
                                                    .truncate()
                                                    .font_semibold()
                                                    .child(row.name),
                                            )
                                            .child(
                                                div()
                                                    .flex_1()
                                                    .min_w_0()
                                                    .truncate()
                                                    .text_sm()
                                                    .text_color(muted)
                                                    .child(row.source),
                                            )
                                            .child(
                                                div().text_xs().text_color(muted).child(row.note),
                                            )
                                            .child(
                                                button(("remove-bucket", index))
                                                    .ghost()
                                                    .small()
                                                    .label("移除")
                                                    .disabled(busy)
                                                    .on_click(move |_, window, cx| {
                                                        let (update, name) =
                                                            (update.clone(), name.clone());
                                                        window.open_dialog(
                                                            cx,
                                                            move |dialog, window, _| {
                                                                let (update, name) =
                                                                    (update.clone(), name.clone());
                                                                dialog_frame(dialog, window)
                                                                    .title(format!(
                                                                        "移除软件源 {name}？"
                                                                    ))
                                                                    .confirm_with(
                                                                        "移除",
                                                                        move |_, window, cx| {
                                                                            update(
                                                                                name.clone(),
                                                                                None,
                                                                                window,
                                                                                cx,
                                                                            );
                                                                            true
                                                                        },
                                                                    )
                                                            },
                                                        );
                                                    }),
                                            )
                                    }),
                            ),
                        ),
                )
        });
    }

    pub(super) fn install_panel(&self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(root) = scoop::detect(None) {
            self.root.update(cx, |input, cx| {
                input.set_value(root.to_string_lossy().into_owned(), window, cx)
            });
            self.connect(window, cx);
            return;
        }
        let requested = input_root(&self.root, cx);
        let default = if requested.is_absolute() {
            requested
        } else {
            std::env::var_os("USERPROFILE")
                .map(std::path::PathBuf::from)
                .unwrap_or_default()
                .join("scoop")
        };
        let root = cx.new(|cx| {
            InputState::new(window, cx).default_value(default.to_string_lossy().into_owned())
        });
        let global = cx.new(|cx| InputState::new(window, cx).placeholder("使用默认目录"));
        let cache = cx.new(|cx| InputState::new(window, cx).placeholder("使用默认目录"));
        let proxy = cx.new(|cx| InputState::new(window, cx).placeholder("使用安装器默认值"));
        let packages = cx.new(|cx| InputState::new(window, cx).placeholder("用空格分隔软件包"));
        let slim = cx.new(|_| false);
        let view = cx.weak_entity();
        let manager = self.state.clone();
        window.open_dialog(cx, move |dialog, window, cx| {
            let (root, global, cache, proxy, packages, slim) = (
                root.clone(),
                global.clone(),
                cache.clone(),
                proxy.clone(),
                packages.clone(),
                slim.clone(),
            );
            let (view, manager) = (view.clone(), manager.clone());
            let check = slim.clone();
            dialog_frame(dialog, window)
                .title("安装 xrgzs/scoop")
                .w(px(580.))
                .child(
                    v_flex()
                        .gap_3()
                        .child(path_field("scoop-install-root", "Scoop 安装目录", &root))
                        .child(path_field("scoop-install-global", "全局软件目录", &global))
                        .child(path_field("scoop-install-cache", "下载缓存目录", &cache))
                        .child(field("GitHub 加速地址", &proxy))
                        .child(field("附加软件包", &packages))
                        .child(
                            Checkbox::new("scoop-slim")
                                .label("精简安装")
                                .checked(*slim.read(cx))
                                .on_click(move |checked, window, cx| {
                                    check.update(cx, |value, _| *value = *checked);
                                    window.refresh();
                                }),
                        ),
                )
                .confirm_with("开始安装", move |_, window, cx| {
                    let options = scoop::InstallOptions {
                        path: input_root(&root, cx),
                        global_path: global.read(cx).value().trim().into(),
                        cache_path: cache.read(cx).value().trim().into(),
                        github_proxy: proxy.read(cx).value().trim().into(),
                        packages: packages
                            .read(cx)
                            .value()
                            .split_whitespace()
                            .map(str::to_owned)
                            .collect(),
                        slim: *slim.read(cx),
                    };
                    if let Err(error) = scoop::validate_install(&options) {
                        Workspace::notify_error(error, window, cx);
                        return false;
                    }
                    let (manager_done, view) = (manager.clone(), view.clone());
                    job(
                        &manager,
                        window,
                        cx,
                        move || scoop::install(&options).map(|result| (options.path, result)),
                        move |(path, output), window, cx| {
                            if output.success {
                                let _ = view.update(cx, |this, cx| {
                                    this.root.update(cx, |input, cx| {
                                        input.set_value(
                                            path.to_string_lossy().into_owned(),
                                            window,
                                            cx,
                                        )
                                    });
                                    this.connect(window, cx);
                                });
                                window.push_notification("xrgzs/scoop 安装完成", cx);
                            } else {
                                manager_done.update(cx, |manager, _| manager.output = output.text);
                                window.push_notification("Scoop 安装失败，详情见命令输出", cx);
                                let _ = view.update(cx, |this, cx| {
                                    this.show_output = true;
                                    cx.notify();
                                });
                            }
                        },
                    );
                    true
                })
        });
    }
}

fn result_eligible(operation: Operation, row: &scoop::ObjectRow) -> bool {
    match operation {
        Operation::Status => {
            !row.latest.is_empty()
                && !row.note.contains("Held package")
                && !row.note.contains("Pinned")
        }
        Operation::Search => scoop::is_bucket_name(&row.source),
        _ => false,
    }
}

fn result_target(operation: Operation, row: &scoop::ObjectRow) -> Option<String> {
    result_eligible(operation, row).then(|| {
        if operation == Operation::Search {
            format!("{}/{}", row.source, row.name)
        } else {
            row.name.clone()
        }
    })
}

#[cfg(test)]
mod tests {
    use super::suggested_bucket_name;

    #[test]
    fn remote_bucket_names_are_derived_from_urls() {
        assert_eq!(
            suggested_bucket_name("https://github.com/uJZk/Scoop-Koolar"),
            "Scoop-Koolar"
        );
        assert_eq!(
            suggested_bucket_name("https://github.com/LISSConsulting/scoop-bucket.git"),
            "scoop-bucket"
        );
        assert_eq!(
            suggested_bucket_name("https://example.com/not a bucket"),
            ""
        );
    }
}

fn suggested_bucket_name(source: &str) -> String {
    let name = source
        .trim()
        .trim_end_matches('/')
        .trim_end_matches(".git")
        .rsplit(|ch| ch == '/' || ch == '\\')
        .next()
        .unwrap_or_default();
    if scoop::is_bucket_name(name) {
        name.to_owned()
    } else {
        String::new()
    }
}

fn result_checkbox(check: Checkbox) -> gpui::Div {
    div().w(px(22.)).flex_shrink_0().child(check)
}

fn job<T: Send + 'static>(
    manager: &Entity<Manager>,
    window: &mut Window,
    cx: &mut App,
    run: impl FnOnce() -> anyhow::Result<T> + Send + 'static,
    done: impl FnOnce(T, &mut Window, &mut App) + 'static,
) {
    if manager.read(cx).busy {
        return;
    }
    let manager = manager.clone();
    manager.update(cx, |manager, cx| {
        manager.busy = true;
        cx.notify();
    });
    window.refresh();
    let task = cx.background_executor().spawn(async move { run() });
    window
        .spawn(cx, async move |cx| {
            let result = task.await;
            let _ = cx.update(|window, cx| {
                manager.update(cx, |manager, cx| {
                    manager.busy = false;
                    cx.notify();
                });
                match result {
                    Ok(value) => done(value, window, cx),
                    Err(error) => Workspace::notify_error(error, window, cx),
                }
                window.refresh();
            });
        })
        .detach();
}

fn path_field(
    id: &'static str,
    label: &'static str,
    input: &Entity<InputState>,
) -> impl IntoElement {
    let browse = input.clone();
    h_flex()
        .items_end()
        .gap_2()
        .child(div().flex_1().child(field(label, input)))
        .child(
            button(id)
                .outline()
                .label("浏览")
                .on_click(move |_, window, cx| pick_path(&browse, true, window, cx)),
        )
}

fn config_text(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

fn bool_setting(key: &str) -> bool {
    matches!(
        key,
        "aria2-enabled"
            | "aria2-fallback-enabled"
            | "aria2-warning-enabled"
            | "use_sqlite_cache"
            | "url_replace"
            | "desktop_shortcut"
            | "uninstall_shortcut"
            | "use_git_history"
            | "no_junction"
            | "use_external_7zip"
            | "use_lessmsi"
            | "show_update_log"
            | "show_manifest"
            | "force_update"
            | "autostash_on_conflict"
            | "update_nightly"
            | "ignore_running_processes"
            | "shim_auto_refresh"
            | "debug"
    )
}

fn config_label(key: &'static str) -> &'static str {
    match key {
        "proxy" => "下载代理",
        "cache_path" => "下载缓存目录",
        "default_architecture" => "默认架构",
        "aria2-enabled" => "使用 Aria2 下载",
        "aria2-fallback-enabled" => "Aria2 失败时自动回退",
        "aria2-warning-enabled" => "显示 Aria2 提示",
        "aria2-split" => "Aria2 分段数",
        "aria2-max-connection-per-server" => "Aria2 单服务器连接数",
        "aria2-min-split-size" => "Aria2 最小分段大小",
        "aria2-retry-wait" => "Aria2 重试间隔",
        "aria2-options" => "Aria2 附加选项（JSON 数组）",
        "gh_proxy" => "GitHub 加速镜像",
        "url_replace" => "自动替换下载地址",
        "desktop_shortcut" => "创建桌面快捷方式",
        "uninstall_shortcut" => "注册控制面板卸载入口",
        "scoop_repo" => "Scoop 更新仓库",
        "scoop_branch" => "Scoop 更新分支",
        "use_sqlite_cache" => "使用 SQLite 搜索缓存",
        "hold_update_until" => "冻结 Scoop 自更新至指定日期",
        _ => key,
    }
}

fn result_row() -> gpui::Div {
    h_flex().px_3().gap_3().h(px(38.)).w_full().text_sm()
}
fn result_name(name: impl Into<gpui::SharedString>) -> gpui::Div {
    super::cell(name, 156.)
}
