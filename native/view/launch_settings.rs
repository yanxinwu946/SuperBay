use std::{
    collections::{HashMap, HashSet},
    ops::Range,
    path::{Path, PathBuf},
    sync::LazyLock,
};

use anyhow::{Context as _, Result, bail};
use gpui::{PathPromptOptions, div, prelude::*};
use gpui_component::{Sizable, button::ButtonVariants, checkbox::Checkbox, h_flex, v_flex};
use serde_json::{Value, json};

use super::{
    dialogs::{ConfirmDialog, dialog_frame, field},
    *,
};
use crate::storage::LaunchField;

impl Workspace {
    pub(super) fn runtime_dialog(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(entry) = self.selected_entry() else {
            return;
        };
        let path = entry.path.clone();
        let runtime = &entry.metadata.runtime;
        let configured_root = self.settings.as_ref().and_then(Settings::scoop_root);
        let root = runtime["root"]
            .as_str()
            .or(configured_root)
            .map(str::to_owned)
            .or_else(|| std::env::var("SCOOP").ok())
            .unwrap_or_default();
        let root = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(root)
                .placeholder(r#"D:\Scoop"#)
        });
        let fields: Vec<_> = launch::FAMILIES
            .into_iter()
            .map(|family| {
                let current = &runtime["versions"][family];
                let value = match (current["package"].as_str(), current["version"].as_str()) {
                    (Some(package), Some(version)) => format!("{package}/{version}"),
                    _ => String::new(),
                };
                (
                    family,
                    cx.new(|cx| {
                        InputState::new(window, cx)
                            .default_value(value)
                            .placeholder("包名/版本，留空使用系统环境")
                    }),
                )
            })
            .collect();
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, _| {
            let (root, fields, path, view) =
                (root.clone(), fields.clone(), path.clone(), view.clone());
            dialog_frame(dialog, window)
                .title("绑定 Scoop 运行环境")
                .child(
                    v_flex()
                        .gap_3()
                        .child(field("Scoop 根目录", &root))
                        .children(fields.iter().map(|(family, input)| field(*family, input))),
                )
                .confirm_with("保存", move |_, window, cx| {
                    let result = (|| {
                        let mut versions = serde_json::Map::new();
                        for (family, field) in &fields {
                            let value = field.read(cx).value();
                            if value.trim().is_empty() {
                                continue;
                            }
                            let (package, version) = value
                                .trim()
                                .split_once('/')
                                .with_context(|| format!("{family} 使用 包名/版本 格式"))?;
                            versions.insert(
                                (*family).into(),
                                json!({"package": package.trim(), "version": version.trim()}),
                            );
                        }
                        let profile = if versions.is_empty() {
                            json!({})
                        } else {
                            json!({"root": root.read(cx).value().trim(), "versions": versions})
                        };
                        launch::validate_runtime(&profile)?;
                        view.update(cx, |this, cx| {
                            this.save_launch_field(&path, LaunchField::Runtime, profile, cx)
                        })?
                    })();
                    dialog_result(result, window, cx)
                })
        });
    }

    fn save_launch_field(
        &mut self,
        path: &Path,
        field: LaunchField,
        value: Value,
        cx: &mut Context<Self>,
    ) -> Result<()> {
        let index = self
            .index_for_path(path)
            .context("当前工作区已没有这个条目")?;
        let settings = self.settings.as_ref().context("无法读取应用设置")?;
        let entry = &mut self.catalog.entries[index];
        Store::open(&settings.database())?.save_launch_field(
            entry.data_path(),
            field,
            serde_json::to_string(&value)?,
        )?;
        match field {
            LaunchField::Plans => entry.metadata.plans = serde_json::from_value(value)?,
            LaunchField::Runtime => entry.metadata.runtime = value,
        }
        entry
            .metadata
            .stored_path
            .get_or_insert_with(|| entry.path.clone());
        if matches!(field, LaunchField::Plans) {
            self.catalog.index_launchable_categories();
            self.rebuild(false, cx);
        }
        cx.notify();
        Ok(())
    }

    pub(super) fn plans_dialog(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(entry) = self.selected_entry() else {
            return;
        };
        let path = entry.path.clone();
        let plans = entry.metadata.plans.clone();
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, _| {
            let (add_view, add_path) = (view.clone(), path.clone());
            dialog_frame(dialog, window).title("启动方案").child(
                v_flex()
                    .gap_2()
                    .when(plans.is_empty(), |list| {
                        list.child(div().text_sm().child("保存常用命令和参数。"))
                    })
                    .children(plans.iter().enumerate().map(|(index, plan)| {
                        let (run_view, run_path) = (view.clone(), path.clone());
                        let (edit_view, edit_path) = (view.clone(), path.clone());
                        h_flex()
                            .gap_2()
                            .child(
                                button(("execute-plan", index))
                                    .outline()
                                    .small()
                                    .justify_start()
                                    .flex_1()
                                    .label(plan["name"].as_str().unwrap_or("未命名").to_owned())
                                    .on_click(move |_, window, cx| {
                                        window.close_dialog(cx);
                                        let _ = run_view.update(cx, |this, cx| {
                                            this.run_plan(run_path.clone(), index, window, cx)
                                        });
                                    }),
                            )
                            .child(
                                button(("edit-plan", index))
                                    .ghost()
                                    .small()
                                    .label("编辑")
                                    .on_click(move |_, window, cx| {
                                        window.close_dialog(cx);
                                        let _ = edit_view.update(cx, |this, cx| {
                                            this.edit_plan(
                                                edit_path.clone(),
                                                Some(index),
                                                window,
                                                cx,
                                            )
                                        });
                                    }),
                            )
                    }))
                    .child(
                        button("add-plan")
                            .primary()
                            .small()
                            .label("新建方案")
                            .on_click(move |_, window, cx| {
                                window.close_dialog(cx);
                                let _ = add_view.update(cx, |this, cx| {
                                    this.edit_plan(add_path.clone(), None, window, cx)
                                });
                            }),
                    ),
            )
        });
    }

    fn edit_plan(
        &mut self,
        path: PathBuf,
        index: Option<usize>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(id) = self.index_for_path(&path) else {
            return;
        };
        let entry = &self.catalog.entries[id];
        let plan = index
            .and_then(|id| entry.metadata.plans.get(id))
            .cloned()
            .unwrap_or_default();
        let name = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(plan["name"].as_str().unwrap_or_default().to_owned())
        });
        let command = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(
                    plan["command"]
                        .as_str()
                        .or(entry.metadata.command.as_deref())
                        .unwrap_or_default()
                        .to_owned(),
                )
                .placeholder("可写入 {参数}、{文件:file} 或 {目录:dir}")
        });
        let flags = cx.new(|_| {
            (
                plan["shell_type"].as_str().unwrap_or(&entry.metadata.shell) == "powershell",
                plan["admin"].as_bool().unwrap_or(entry.metadata.admin),
            )
        });
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, cx| {
            let (power, admin) = *flags.read(cx);
            let (power_flags, admin_flags, flags) = (flags.clone(), flags.clone(), flags.clone());
            let (view, path, name, command, plan) = (
                view.clone(),
                path.clone(),
                name.clone(),
                command.clone(),
                plan.clone(),
            );
            dialog_frame(dialog, window)
                .title(if index.is_some() {
                    "编辑启动方案"
                } else {
                    "新建启动方案"
                })
                .child(
                    v_flex()
                        .gap_3()
                        .child(field("名称", &name))
                        .child(field("命令", &command))
                        .child(
                            Checkbox::new("plan-powershell")
                                .label("使用 PowerShell")
                                .checked(power)
                                .on_click(move |checked, window, cx| {
                                    power_flags.update(cx, |flags, _| flags.0 = *checked);
                                    window.refresh();
                                }),
                        )
                        .child(
                            Checkbox::new("plan-admin")
                                .label("以管理员身份启动")
                                .checked(admin)
                                .on_click(move |checked, window, cx| {
                                    admin_flags.update(cx, |flags, _| flags.1 = *checked);
                                    window.refresh();
                                }),
                        ),
                )
                .confirm_with("保存", move |_, window, cx| {
                    let name = name.read(cx).value().trim().to_owned();
                    let command = command.read(cx).value().trim().to_owned();
                    let (power, admin) = *flags.read(cx);
                    let result = view
                        .update(cx, |this, cx| {
                            if name.is_empty() || command.is_empty() {
                                bail!("请填写方案名称和命令");
                            }
                            let id = this.index_for_path(&path).context("条目已离开工作区")?;
                            let mut plans = this.catalog.entries[id].metadata.plans.clone();
                            if plans.iter().enumerate().any(|(i, plan)| {
                                Some(i) != index && plan["name"].as_str() == Some(&name)
                            }) {
                                bail!("已有同名启动方案");
                            }
                            let mut next = plan.as_object().cloned().unwrap_or_default();
                            next.insert("name".into(), json!(name));
                            next.insert("command".into(), json!(command));
                            next.insert("admin".into(), json!(admin));
                            next.insert(
                                "shell_type".into(),
                                json!(if power { "powershell" } else { "cmd" }),
                            );
                            let next = Value::Object(next);
                            if let Some(index) = index {
                                let current = plans
                                    .get_mut(index)
                                    .filter(|current| **current == plan)
                                    .context("方案已变更，请重新打开")?;
                                *current = next;
                            } else {
                                plans.push(next);
                            }
                            this.save_launch_field(&path, LaunchField::Plans, json!(plans), cx)
                        })
                        .and_then(|result| result);
                    dialog_result(result, window, cx)
                })
        });
    }

    fn run_plan(
        &mut self,
        path: PathBuf,
        index: usize,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(entry) = self.index_for_path(&path) else {
            return;
        };
        let Some(plan) = self.catalog.entries[entry]
            .metadata
            .plans
            .get(index)
            .cloned()
        else {
            return;
        };
        let Some(command) = plan["command"].as_str() else {
            return;
        };
        let mut names = HashSet::new();
        let inputs: Vec<_> = template_fields(command)
            .filter(|(name, _, _)| names.insert(*name))
            .map(|(name, kind, _)| PlanField {
                name: name.to_owned(),
                kind,
                input: cx.new(|cx| {
                    InputState::new(window, cx)
                        .default_value(plan["values"][name].as_str().unwrap_or_default().to_owned())
                }),
            })
            .collect();
        if inputs.is_empty() {
            self.dispatch(entry, plan_request(command, &plan), None, window, cx);
            return;
        }
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, _| {
            let (view, path, plan, inputs) =
                (view.clone(), path.clone(), plan.clone(), inputs.clone());
            dialog_frame(dialog, window)
                .title(plan["name"].as_str().unwrap_or("启动参数").to_owned())
                .child(
                    v_flex().gap_3().children(
                        inputs
                            .iter()
                            .enumerate()
                            .map(|(index, input)| input.render(index)),
                    ),
                )
                .confirm_with("启动", move |_, window, cx| {
                    let result = (|| {
                        let values = inputs
                            .iter()
                            .map(|input| input.value(cx))
                            .collect::<Result<HashMap<_, _>>>()?;
                        let template = plan["command"].as_str().unwrap_or_default();
                        let command = render_template(template, &values);
                        view.update(cx, |this, cx| {
                            let id = this.index_for_path(&path).context("条目已离开工作区")?;
                            let mut plans = this.catalog.entries[id].metadata.plans.clone();
                            let stored = plans
                                .get_mut(index)
                                .filter(|stored| **stored == plan)
                                .context("方案已变更，请重新打开")?;
                            stored["values"] = serde_json::to_value(values)?;
                            this.save_launch_field(&path, LaunchField::Plans, json!(plans), cx)?;
                            this.dispatch(id, plan_request(&command, &plan), None, window, cx);
                            Ok(())
                        })?
                    })();
                    dialog_result(result, window, cx)
                })
        });
    }
}

fn dialog_result(result: Result<()>, window: &mut Window, cx: &mut App) -> bool {
    if let Err(error) = result {
        Workspace::notify_error(error, window, cx);
        return false;
    }
    true
}

fn plan_request<'a>(command: &'a str, plan: &'a Value) -> launch::Request<'a> {
    launch::Request {
        args: "",
        chosen: None,
        elevate: false,
        plan: Some((
            command,
            plan["shell_type"].as_str().unwrap_or("cmd"),
            plan["admin"].as_bool().unwrap_or_default(),
        )),
    }
}

#[derive(Clone)]
struct PlanField {
    name: String,
    kind: &'static str,
    input: Entity<InputState>,
}

impl PlanField {
    fn render(&self, index: usize) -> impl IntoElement {
        let input = self.input.clone();
        let directories = self.kind == "dir";
        h_flex()
            .gap_2()
            .items_end()
            .child(
                v_flex()
                    .flex_1()
                    .child(field(self.name.clone(), &self.input)),
            )
            .when(self.kind != "text", |row| {
                row.child(
                    button(("plan-file-picker", index))
                        .outline()
                        .label("浏览")
                        .on_click(move |_, window, cx| pick_path(&input, directories, window, cx)),
                )
            })
    }

    fn value(&self, cx: &App) -> Result<(String, String)> {
        let value = self.input.read(cx).value().trim().to_owned();
        if value.is_empty() {
            bail!("请填写 {}", self.name);
        }
        let path = Path::new(value.trim_matches('"'));
        if self.kind == "dir" && !path.is_dir() {
            bail!("{} 不是可用目录", self.name);
        }
        if self.kind == "file" && !path.is_file() {
            bail!("{} 不是可用文件", self.name);
        }
        Ok((self.name.clone(), value))
    }
}

pub(super) fn pick_path(
    input: &Entity<InputState>,
    directories: bool,
    window: &mut Window,
    cx: &mut App,
) {
    let paths = cx.prompt_for_paths(PathPromptOptions {
        directories,
        files: !directories,
        multiple: false,
        prompt: None,
    });
    let input = input.clone();
    window
        .spawn(cx, async move |cx| {
            if let Ok(Ok(Some(paths))) = paths.await
                && let Some(path) = paths.into_iter().next()
            {
                let _ = cx.update(|window, cx| {
                    input.update(cx, |input, cx| {
                        input.set_value(path.to_string_lossy().into_owned(), window, cx);
                    })
                });
            }
        })
        .detach();
}

fn template_fields(command: &str) -> impl Iterator<Item = (&str, &'static str, Range<usize>)> {
    static PATTERN: LazyLock<regex::Regex> = LazyLock::new(|| {
        regex::Regex::new(r"\{([A-Za-z_\u{3400}-\u{9fff}][\w-]*)(?::(file|dir|text))?\}").unwrap()
    });
    PATTERN.captures_iter(command).filter_map(|capture| {
        let whole = capture.get(0)?;
        if command[..whole.start()].ends_with('{') || command[whole.end()..].starts_with('}') {
            return None;
        }
        let kind = match capture.get(2).map(|part| part.as_str()) {
            Some("file") => "file",
            Some("dir") => "dir",
            _ => "text",
        };
        Some((capture.get(1)?.as_str(), kind, whole.range()))
    })
}

fn render_template(command: &str, values: &HashMap<String, String>) -> String {
    let mut end = 0;
    let mut result = String::new();
    for (name, _, range) in template_fields(command) {
        result.push_str(&command[end..range.start]);
        result.push_str(&values[name]);
        end = range.end;
    }
    result.push_str(&command[end..]);
    result
}
