use std::path::{Path, PathBuf};

use gpui::{ClipboardItem, SharedString, div, prelude::*, px};
use gpui_component::{
    ActiveTheme, IconName, Sizable, StyledExt, button::ButtonVariants, h_flex, popover::Popover,
    tooltip::Tooltip, v_flex,
};

use super::*;

#[derive(Default)]
pub(super) struct LaunchPreview {
    path: Option<PathBuf>,
    info: Option<launch::EntryPreview>,
    _task: Option<Task<()>>,
}

impl LaunchPreview {
    pub fn needs_refresh(&self, path: Option<&Path>) -> bool {
        path.is_some() && self.path.as_deref() != path
    }
}

impl Workspace {
    pub(super) fn update_launch_preview(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(entry) = self
            .selected_entry()
            .filter(|_| self.details && self.group_selection.is_empty())
        else {
            return;
        };
        if self.launch_preview.path.as_ref() == Some(&entry.path) {
            return;
        }
        let path = entry.path.clone();
        let directory = entry.directory;
        let target = path.clone();
        let read = cx
            .background_executor()
            .spawn(async move { launch::inspect_entry(&target, directory) });
        let check = path.clone();
        let worker = cx.spawn_in(window, async move |view, cx| {
            let result = read.await;
            let _ = cx.update(|_, cx| {
                view.update(cx, |this, cx| {
                    if this.launch_preview.path.as_ref() == Some(&check) {
                        this.launch_preview.info = Some(result);
                        cx.notify();
                    }
                })
            });
        });
        self.launch_preview = LaunchPreview {
            path: Some(path),
            _task: Some(worker),
            ..Default::default()
        };
    }

    pub(super) fn launch_preview_panel(
        &self,
        entry: &catalog::Entry,
        cx: &mut Context<Self>,
    ) -> impl IntoElement {
        let theme = cx.theme();
        let (line, muted) = (theme.border, theme.muted_foreground);
        let view = cx.weak_entity();
        let path = entry.path.clone();
        let base = if entry.directory {
            entry.path.as_path()
        } else {
            entry.path.parent().unwrap_or(&entry.path)
        };
        let choices_base = base.to_owned();
        let info = self
            .launch_preview
            .info
            .as_ref()
            .filter(|_| self.launch_preview.path.as_ref() == Some(&entry.path));
        let chosen = entry.metadata.chosen_entry.as_deref();
        let automatic = info.and_then(|info| info.automatic.as_deref());
        let command = entry
            .metadata
            .command
            .as_deref()
            .filter(|cmd| !cmd.trim().is_empty());
        let choices = info.map(|info| info.choices.clone()).unwrap_or_default();
        let panel = v_flex()
            .gap_2()
            .p_3()
            .rounded_lg()
            .border_1()
            .border_color(line)
            .child(
                h_flex()
                    .justify_between()
                    .gap_1()
                    .child(div().text_sm().font_semibold().child("启动入口"))
                    .when(!choices.is_empty(), |header| {
                        header.child(
                            Popover::new("detected-entries")
                                .trigger(
                                    button("entry-choice")
                                        .ghost()
                                        .small()
                                        .icon(IconName::ChevronDown)
                                        .label("切换"),
                                )
                                .content(move |_, _, cx| {
                                    let popover = cx.entity().downgrade();
                                    v_flex()
                                        .id("entry-choices")
                                        .w(px(270.))
                                        .max_h(px(300.))
                                        .overflow_y_scroll()
                                        .gap_1()
                                        .children(
                                            std::iter::once(None)
                                                .chain(choices.iter().cloned().map(Some))
                                                .enumerate()
                                                .map(|(index, target)| {
                                                    let label = target
                                                        .as_deref()
                                                        .map(|target| {
                                                            display_target(&choices_base, target)
                                                                .to_string_lossy()
                                                                .into_owned()
                                                        })
                                                        .unwrap_or_else(|| "使用默认入口".into());
                                                    let view = view.clone();
                                                    let source = path.clone();
                                                    let popover = popover.clone();
                                                    button(("candidate-preview", index))
                                                        .ghost()
                                                        .small()
                                                        .justify_start()
                                                        .label(shorten(&label, 28))
                                                        .tooltip(label)
                                                        .on_click(move |_, window, cx| {
                                                            let _ = popover.update(
                                                                cx,
                                                                |popover, cx| {
                                                                    popover.dismiss(window, cx)
                                                                },
                                                            );
                                                            let _ = view.update(cx, |this, cx| {
                                                                let Some(index) =
                                                                    this.index_for_path(&source)
                                                                else {
                                                                    return;
                                                                };
                                                                let entry = &mut this
                                                                    .catalog
                                                                    .entries[index];
                                                                entry.metadata.chosen_entry =
                                                                    target.clone();
                                                                let tool =
                                                                    entry.data_path().to_path_buf();
                                                                let target = target.clone();
                                                                if let Some(settings) =
                                                                    &this.settings
                                                                {
                                                                    let database =
                                                                        settings.database();
                                                                    if let Err(error) =
                                                                        crate::storage::Store::open(
                                                                            &database,
                                                                        )
                                                                        .and_then(|db| {
                                                                            db.set_chosen_entry(
                                                                                &tool,
                                                                                target.as_deref(),
                                                                            )
                                                                        })
                                                                    {
                                                                        Self::notify_error(
                                                                            error, window, cx,
                                                                        );
                                                                    }
                                                                }
                                                                cx.notify();
                                                            });
                                                        })
                                                }),
                                        )
                                }),
                        )
                    }),
            );
        if let Some(command) = command.filter(|_| chosen.is_none()) {
            let shell = if entry.metadata.shell == "powershell" {
                "PowerShell"
            } else {
                "命令提示符"
            };
            return panel
                .child(
                    div()
                        .text_xs()
                        .text_color(muted)
                        .child(format!("自定义命令 · {shell}")),
                )
                .child(copyable_text("preview-command", command, command, cx));
        }
        if let Some(target) = chosen.or(automatic) {
            return panel
                .child(
                    div()
                        .text_xs()
                        .text_color(muted)
                        .child(launch::entry_mode(target)),
                )
                .child(copyable_text(
                    "preview-target",
                    &display_target(base, target).to_string_lossy(),
                    &target.to_string_lossy(),
                    cx,
                ));
        }
        let message = match info {
            None => "正在识别…",
            Some(info) if !info.choices.is_empty() => "有多个候选入口，可先选择一个",
            Some(_) => "打开文件夹",
        };
        panel.child(div().text_xs().text_color(muted).child(message))
    }
}

fn display_target<'a>(base: &Path, target: &'a Path) -> &'a Path {
    target
        .strip_prefix(base)
        .ok()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or(target)
}

fn copyable_text(id: &'static str, label: &str, full: &str, cx: &App) -> impl IntoElement {
    let short = shorten(label, 29);
    let text = SharedString::from(full.to_owned());
    let theme = cx.theme();
    h_flex()
        .w_full()
        .min_w_0()
        .gap_2()
        .child(
            div()
                .id(id)
                .flex_1()
                .min_w_0()
                .cursor_pointer()
                .child(
                    div()
                        .w_full()
                        .truncate()
                        .text_sm()
                        .font_family(theme.mono_font_family.clone())
                        .child(short),
                )
                .tooltip({
                    let text = text.clone();
                    move |window, cx| Tooltip::new(text.clone()).build(window, cx)
                })
                .on_click({
                    let text = text.clone();
                    move |_, window, cx| {
                        cx.write_to_clipboard(ClipboardItem::new_string(text.to_string()));
                        window.push_notification("已复制", cx);
                    }
                }),
        )
        .child(
            button(gpui::SharedString::from(format!("{id}-copy")))
                .ghost()
                .w(px(BUTTON_HEIGHT))
                .flex_shrink_0()
                .icon(IconName::Copy)
                .tooltip("复制完整内容")
                .on_click(move |_, window, cx| {
                    cx.write_to_clipboard(ClipboardItem::new_string(text.to_string()));
                    window.push_notification("已复制", cx);
                }),
        )
}

pub(super) fn shorten(text: &str, budget: usize) -> String {
    let chars: Vec<_> = text.chars().collect();
    if chars.len() <= budget {
        return text.to_owned();
    }
    let leading = (if text.starts_with(r"\\") { 8 } else { 5 }).min(budget.saturating_sub(1));
    if budget == 0 {
        return String::new();
    }
    chars[..leading]
        .iter()
        .chain(std::iter::once(&'…'))
        .chain(&chars[chars.len() - (budget - leading - 1)..])
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{Path, display_target, shorten};

    #[test]
    fn entry_paths_are_relative_only_inside_the_launch_directory() {
        let base = Path::new(r"D:\Tools\ARTEX");
        assert_eq!(
            display_target(base, Path::new(r"D:\Tools\ARTEX\bin\start.bat")),
            Path::new(r"bin\start.bat")
        );
        let external = Path::new(r"D:\Tools\ARTEX-other\start.bat");
        assert_eq!(display_target(base, external), external);
    }

    #[test]
    fn shortened_paths_keep_the_drive_and_filename() {
        let path = r"D:\工作\很长的目录名称\更深层的目录\launch.py";
        let short = shorten(path, 19);
        assert!(short.starts_with(r"D:\"));
        assert!(short.ends_with("launch.py"));
        assert_eq!(short.chars().count(), 19);
        assert_eq!(shorten("short", 19), "short");
    }
}
