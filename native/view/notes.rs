use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};

use gpui::{Focusable, Rems, SharedString, div, prelude::*};
use gpui_component::{ActiveTheme, StyledExt, input::Input, v_flex};

use super::*;

// Two 1.25 rem lines with a little space below the last line.
const EDITOR_HEIGHT: Rems = Rems(2.875);

pub(super) struct Draft {
    path: PathBuf,
    input: Entity<InputState>,
    original: Option<SharedString>,
    message: Option<String>,
    pending: Option<Task<()>>,
}

impl Workspace {
    pub(super) fn note_editor_pending(&self) -> bool {
        self.settings_page.is_none()
            && self
                .selected_entry()
                .is_some_and(|entry| entry.directory && !self.notes.contains_key(&entry.path))
    }

    pub(super) fn prepare_selected_note(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if !self.details || !self.group_selection.is_empty() || !self.note_editor_pending() {
            return;
        }
        let Some(entry) = self.selected_entry() else {
            return;
        };
        let tool = entry.path.clone();
        let path = entry.note.clone().unwrap_or_else(|| tool.join("NOTE.md"));
        let content = read_note(&path);
        let original = content.as_ref().ok().cloned();
        let message = content.err().map(|error| format!("无法读取笔记 · {error}"));
        let input = cx.new(|cx| {
            InputState::new(window, cx)
                .multi_line(true)
                .rows(2)
                .soft_wrap(true)
                .placeholder("输入笔记")
                .default_value(original.clone().unwrap_or_default())
        });
        let note = tool.clone();
        cx.subscribe_in(
            &input,
            window,
            move |this, _, event, window, cx| match event {
                InputEvent::Change => this.queue_note_save(&note, window, cx),
                InputEvent::Blur => this.save_note_for(&note, window, cx),
                _ => {}
            },
        )
        .detach();
        self.notes.insert(
            tool,
            Draft {
                path,
                input,
                original,
                message,
                pending: None,
            },
        );
        cx.notify();
    }

    fn queue_note_save(&mut self, tool: &Path, window: &mut Window, cx: &mut Context<Self>) {
        let Some(draft) = self.notes.get_mut(tool) else {
            return;
        };
        let tool = tool.to_owned();
        let timer = cx.background_executor().timer(Duration::from_millis(600));
        draft.pending = Some(cx.spawn_in(window, async move |view, cx| {
            timer.await;
            let _ = cx.update(|window, cx| {
                view.update(cx, |this, cx| this.save_note_for(&tool, window, cx))
            });
        }));
    }

    pub(super) fn edit_note(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if window.has_active_dialog(cx) || !self.group_selection.is_empty() {
            return;
        }
        self.details = true;
        self.prepare_selected_note(window, cx);
        if let Some(draft) = self
            .selected_entry()
            .and_then(|entry| self.notes.get(&entry.path))
            && draft.original.is_some()
        {
            draft.input.update(cx, |input, cx| input.focus(window, cx));
        }
        cx.notify();
    }

    pub(super) fn save_note(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(tool) = self.selected_entry().map(|entry| entry.path.clone()) {
            self.save_note_for(&tool, window, cx);
        }
    }

    pub(crate) fn flush_notes(&mut self, window: &mut Window, cx: &mut Context<Self>) -> bool {
        let dirty: Vec<_> = self
            .notes
            .iter()
            .filter_map(|(tool, draft)| {
                draft
                    .original
                    .as_ref()
                    .filter(|&original| draft.input.read(cx).value() != *original)
                    .map(|_| tool.clone())
            })
            .collect();
        for tool in dirty {
            self.save_note_for(&tool, window, cx);
        }
        !self.notes.values().any(|draft| {
            draft
                .original
                .as_ref()
                .is_some_and(|original| draft.input.read(cx).value() != *original)
        })
    }

    fn save_note_for(&mut self, tool: &Path, window: &mut Window, cx: &mut Context<Self>) {
        let Some(draft) = self.notes.get_mut(tool) else {
            return;
        };
        draft.pending = None;
        let Some(original) = &draft.original else {
            return;
        };
        let value = draft.input.read(cx).value();
        if value == *original {
            if draft.message.take().is_some() {
                cx.notify();
            }
            return;
        }
        let result = read_note(&draft.path).and_then(|current| {
            anyhow::ensure!(
                current == *original || current == value,
                "磁盘上的笔记已变更，当前编辑内容已保留"
            );
            if current != value {
                crate::storage::atomic_write(&draft.path, value.as_bytes())?;
            }
            Ok(())
        });
        match result {
            Ok(()) => {
                draft.original = Some(value);
                draft.message = None;
                self.refresh(window, cx);
            }
            Err(error) => {
                let message = format!("无法保存笔记 · {error}");
                if draft.message.as_ref() != Some(&message) {
                    draft.message = Some(message.clone());
                    window.push_notification(Notification::error(message), cx);
                }
            }
        }
        cx.notify();
    }

    pub(super) fn escape_workspace(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let note_focused = self
            .selected_entry()
            .and_then(|entry| self.notes.get(&entry.path))
            .is_some_and(|draft| draft.input.read(cx).focus_handle(cx).is_focused(window));
        if window.has_active_dialog(cx) {
            window.close_dialog(cx);
        } else if self.settings_page.is_some() {
            self.toggle_settings(window, cx);
        } else if self.guide {
            self.dismiss_guide(window, cx);
        } else if note_focused {
            self.save_note(window, cx);
            self.list_focus.focus(window);
        } else if !self.group_selection.is_empty() {
            self.group_selection.clear();
        } else if !self.query.is_empty() {
            self.search
                .update(cx, |input, cx| input.set_value("", window, cx));
            self.query.clear();
            self.rebuild(true, cx);
        } else if self.details {
            self.details = false;
            self.list_focus.focus(window);
        }
        cx.notify();
    }

    pub(super) fn note_panel(&self, entry: &catalog::Entry, cx: &App) -> gpui::Div {
        let theme = cx.theme();
        v_flex()
            .px_4()
            .py_3()
            .gap_2()
            .flex_shrink_0()
            .border_t_1()
            .border_color(theme.border)
            .child(div().text_sm().font_semibold().child("笔记"))
            .child(
                div()
                    .h(EDITOR_HEIGHT)
                    .children(self.notes.get(&entry.path).map(|draft| {
                        Input::new(&draft.input)
                            .appearance(false)
                            .h_full()
                            .pl_0()
                            .pr_3()
                            .py_0()
                            .disabled(draft.original.is_none())
                    })),
            )
            .when_some(
                self.notes
                    .get(&entry.path)
                    .and_then(|draft| draft.message.as_ref()),
                |panel, message| {
                    panel.child(
                        div()
                            .text_xs()
                            .text_color(theme.muted_foreground)
                            .child(message.clone()),
                    )
                },
            )
    }
}

fn read_note(path: &Path) -> anyhow::Result<SharedString> {
    match fs::read_to_string(path) {
        Ok(text) => Ok(text.into()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(SharedString::default()),
        Err(error) => Err(error.into()),
    }
}
