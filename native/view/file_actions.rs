use std::{fs, path::Path};

use anyhow::Result;
use gpui::{ClipboardItem, Modifiers, StyledImage, div, prelude::*, px};
use gpui_component::{ActiveTheme, Icon, IconName, Sizable, h_flex, v_flex};

use super::*;
use crate::resources;

impl Workspace {
    pub(super) fn select_row(
        &mut self,
        id: usize,
        modifiers: Modifiers,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let previous = self.selected;
        if modifiers.shift {
            let anchor = self.selection_anchor.or(previous).unwrap_or(id);
            let start = self.results.iter().position(|&item| item == anchor);
            let end = self.results.iter().position(|&item| item == id);
            if let (Some(start), Some(end)) = (start, end) {
                if !modifiers.control {
                    self.group_selection.clear();
                }
                self.group_selection.extend(
                    self.results[start.min(end)..=start.max(end)]
                        .iter()
                        .copied(),
                );
            }
        } else if modifiers.control {
            if self.group_selection.is_empty() {
                self.group_selection.extend(previous);
            }
            if !self.group_selection.remove(&id) {
                self.group_selection.insert(id);
            }
            self.selection_anchor = Some(id);
        } else {
            self.group_selection.clear();
            self.selection_anchor = Some(id);
        }
        self.selected = if self.group_selection.len() == 1 {
            self.group_selection.drain().next()
        } else if modifiers.control && !self.group_selection.contains(&id) {
            self.results
                .iter()
                .copied()
                .find(|item| self.group_selection.contains(item))
        } else {
            Some(id)
        };
        self.list_focus.focus(window);
        cx.notify();
    }

    pub(super) fn select_all(&mut self, window: &Window, cx: &mut Context<Self>) {
        if self.list_focus.is_focused(window) && self.results.len() > 1 {
            self.group_selection = self.results.iter().copied().collect();
            self.selected = self.results.first().copied();
            self.selection_anchor = self.selected;
            cx.notify();
        }
    }

    pub(super) fn selected_ids(&self) -> Vec<usize> {
        if self.group_selection.is_empty() {
            return self.selected.into_iter().collect();
        }
        self.results
            .iter()
            .copied()
            .filter(|id| self.group_selection.contains(id))
            .collect()
    }

    pub(super) fn copy_paths(&self, window: &mut Window, cx: &mut Context<Self>) {
        let ids = self.selected_ids();
        if ids.is_empty() {
            return;
        }
        let paths: Vec<_> = ids
            .iter()
            .map(|&id| self.catalog.entries[id].path.to_string_lossy())
            .collect();
        cx.write_to_clipboard(ClipboardItem::new_string(paths.join("\r\n")));
        window.push_notification(format!("已复制 {} 项路径", ids.len()), cx);
    }

    pub(super) fn pin_selection(
        &mut self,
        pinned: Option<bool>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let ids = self.selected_ids();
        let Some(settings) = &self.settings else {
            return;
        };
        if ids.is_empty() {
            return;
        }
        let pinned = pinned.unwrap_or_else(|| {
            ids.iter()
                .any(|&id| !self.catalog.entries[id].metadata.pinned)
        });
        let paths: Vec<_> = ids
            .iter()
            .map(|&id| self.catalog.entries[id].data_path().to_path_buf())
            .collect();
        match Store::open(&settings.database()).and_then(|mut store| store.pin_many(&paths, pinned))
        {
            Ok(()) => {
                for id in &ids {
                    self.catalog.entries[*id].metadata.pinned = pinned;
                }
                window.push_notification(
                    format!(
                        "{} {} 项",
                        if pinned {
                            "已固定"
                        } else {
                            "已取消固定"
                        },
                        ids.len()
                    ),
                    cx,
                );
                self.rebuild(false, cx);
            }
            Err(error) => Self::notify_error(error, window, cx),
        }
    }

    pub(super) fn result_layout(
        &mut self,
        sort: ResultSort,
        dense: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(settings) = &mut self.settings else {
            return;
        };
        if let Err(error) = settings.set_result_layout(sort.key(), dense) {
            Self::notify_error(error, window, cx);
        } else {
            self.sort = sort;
            self.dense = dense;
            self.rebuild(false, cx);
        }
    }

    pub(super) fn terminal_here(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(settings) = &self.settings else {
            return;
        };
        let root = self
            .selected_entry()
            .map(|entry| {
                if entry.directory {
                    entry.path.as_path()
                } else {
                    entry.path.parent().unwrap_or(&entry.path)
                }
            })
            .or(self.root.as_deref());
        if let Some(root) = root
            && let Err(error) = launch::terminal_here(root, settings.terminal())
        {
            Self::notify_error(error, window, cx);
        }
    }

    pub(super) fn preview_selection(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if !self.group_selection.is_empty() || window.has_active_dialog(cx) {
            return;
        }
        let Some(entry) = self.selected_entry() else {
            return;
        };
        let path = entry.path.clone();
        let name = entry.name.clone();
        let generation = self.generation;
        let source = path.clone();
        let task = cx
            .background_executor()
            .spawn(async move { read_preview(&path) });
        cx.spawn_in(window, async move |view, cx| {
            let result = task.await;
            let _ = cx.update(|window, cx| {
                if !view
                    .upgrade()
                    .is_some_and(|view| view.read(cx).generation == generation)
                    || window.has_active_dialog(cx)
                {
                    return;
                }
                match result {
                    Ok(preview) => window.open_dialog(cx, move |dialog, window, cx| {
                        let source = source.clone();
                        let copy = match &preview {
                            Preview::Text(text, false, _) => Some(text.clone()),
                            _ => None,
                        };
                        let dialog = super::dialogs::dialog_frame(dialog, window)
                            .title(name.clone())
                            .w(px(650.))
                            .child(preview.element(window, cx));
                        if !matches!(preview, Preview::Text(..)) {
                            return dialog;
                        }
                        dialog.footer(move |_, _, _, _| {
                            let (copy, source) = (copy.clone(), source.clone());
                            vec![
                                super::dialogs::footer_button("copy-full-preview", "复制全文")
                                    .outline()
                                    .on_click(move |_, window, cx| {
                                        if let Some(text) = &copy {
                                            copy_preview(text.clone(), window, cx);
                                            return;
                                        }
                                        let source = source.clone();
                                        let task = cx.background_executor().spawn(async move {
                                            resources::read_full_text(&source)
                                        });
                                        window
                                            .spawn(cx, async move |cx| {
                                                let result = task.await;
                                                let _ = cx.update(|window, cx| match result {
                                                    Ok(Some(text)) => {
                                                        copy_preview(text, window, cx)
                                                    }
                                                    Ok(None) => window.push_notification(
                                                        "文件已变更，无法复制全文",
                                                        cx,
                                                    ),
                                                    Err(error) => Workspace::notify_error(
                                                        error.into(),
                                                        window,
                                                        cx,
                                                    ),
                                                });
                                            })
                                            .detach();
                                    }),
                            ]
                        })
                    }),
                    Err(error) => Self::notify_error(error, window, cx),
                }
            });
        })
        .detach();
    }
}

fn copy_preview(text: String, window: &mut Window, cx: &mut App) {
    cx.write_to_clipboard(ClipboardItem::new_string(text));
    window.push_notification("全文已复制", cx);
}

enum Preview {
    Text(String, bool, bool),
    Image(PathBuf),
    Folder(Vec<String>, bool),
    File(u64),
}

impl Preview {
    fn element(&self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let muted = cx.theme().muted_foreground;
        let mono = cx.theme().mono_font_family.clone();
        let body = v_flex().gap_1();
        match self {
            Self::Text(content, truncated, markdown) => body
                .child(if *markdown {
                    gpui_component::text::TextView::markdown(
                        "resource-markdown",
                        content.clone(),
                        window,
                        cx,
                    )
                    .selectable(true)
                    .into_any_element()
                } else {
                    div()
                        .font_family(mono)
                        .text_sm()
                        .child(content.clone())
                        .into_any_element()
                })
                .when(*truncated, |body| {
                    body.child(
                        div()
                            .text_xs()
                            .text_color(muted)
                            .child("预览前 48 KiB，可复制完整文件"),
                    )
                }),
            Self::Image(path) => body.child(
                gpui::img(path.clone())
                    .w_full()
                    .h(px(370.))
                    .object_fit(gpui::ObjectFit::Contain),
            ),
            Self::Folder(names, truncated) => body
                .children(names.iter().map(|name| {
                    h_flex()
                        .gap_2()
                        .child(Icon::new(IconName::File).small().text_color(muted))
                        .child(div().truncate().child(name.clone()))
                }))
                .when(names.is_empty(), |body| body.child("空目录"))
                .when(*truncated, |body| {
                    body.child(div().text_xs().text_color(muted).child("仅显示前 100 项"))
                }),
            Self::File(size) => body.child(
                div()
                    .text_sm()
                    .text_color(muted)
                    .child(format!("文件大小 · {}", resources::size_label(*size))),
            ),
        }
    }
}

fn read_preview(path: &Path) -> Result<Preview> {
    const LIMIT: usize = 48 * 1024;
    let metadata = path.metadata()?;
    if metadata.is_dir() {
        let mut names: Vec<_> = fs::read_dir(path)?
            .take(101)
            .map(|entry| entry.map(|entry| entry.file_name().to_string_lossy().into_owned()))
            .collect::<std::io::Result<_>>()?;
        let truncated = names.len() > 100;
        names.truncate(100);
        names.sort_unstable_by_key(|name| name.to_lowercase());
        return Ok(Preview::Folder(names, truncated));
    }
    if !metadata.is_file() {
        return Ok(Preview::File(metadata.len()));
    }
    let extension = resources::extension(path);
    if metadata.len() <= 24 * 1024 * 1024
        && matches!(
            extension.as_str(),
            "png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp"
        )
    {
        return Ok(Preview::Image(path.to_owned()));
    }
    if let Some((content, truncated)) = resources::read_text(path, LIMIT)? {
        return Ok(Preview::Text(
            content,
            truncated,
            matches!(extension.as_str(), "md" | "markdown"),
        ));
    }
    Ok(Preview::File(metadata.len()))
}
