use gpui::{div, prelude::*, px};
use gpui_component::{Sizable, button::ButtonVariants, h_flex, v_flex};

use super::{dialogs::ConfirmDialog, *};

impl Workspace {
    pub(super) fn backend_dialog(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let (Some(entry), Some(settings)) = (self.selected_entry(), &self.settings) else {
            return;
        };
        let path = entry.path.clone();
        let logs = settings.backend_logs();
        let name = entry.name.clone();
        let input = cx
            .new(|cx| InputState::new(window, cx).default_value(entry.metadata.last_args.clone()));
        let status =
            self.backends
                .get_mut(&path)
                .and_then(|backend| match backend.child.try_wait() {
                    Ok(None) => Some(("正在运行".to_owned(), true, backend.log.clone())),
                    Ok(Some(status)) => Some((
                        format!("已退出 · {}", status.code().unwrap_or_default()),
                        false,
                        backend.log.clone(),
                    )),
                    Err(error) => Some((error.to_string(), false, backend.log.clone())),
                });
        let running = status.as_ref().is_some_and(|(_, running, _)| *running);
        let view = cx.weak_entity();
        window.open_dialog(cx, move |dialog, window, _| {
            let mut content = v_flex().gap_3();
            if let Some((message, running, log)) = &status {
                let log = log.clone();
                let stop_view = view.clone();
                let stop_path = path.clone();
                content = content.child(div().text_sm().child(message.clone())).child(
                    h_flex()
                        .gap_2()
                        .child(
                            button("backend-open-log")
                                .outline()
                                .small()
                                .label("打开日志")
                                .on_click(move |_, window, cx| {
                                    if let Err(error) = launch::open_path(&log) {
                                        Self::notify_error(error, window, cx);
                                    }
                                }),
                        )
                        .when(*running, |row| {
                            row.child(
                                button("backend-stop")
                                    .danger()
                                    .small()
                                    .label("停止")
                                    .on_click(move |_, window, cx| {
                                        let result = stop_view
                                            .update(cx, |this, _| {
                                                if let Some(backend) =
                                                    this.backends.get_mut(&stop_path)
                                                    && backend.child.try_wait()?.is_none()
                                                {
                                                    backend.child.kill()?;
                                                }
                                                Ok::<_, anyhow::Error>(())
                                            })
                                            .and_then(|result| result);
                                        if let Err(error) = result {
                                            Self::notify_error(error, window, cx);
                                        } else {
                                            window.close_dialog(cx);
                                        }
                                    }),
                            )
                        }),
                );
            }
            let (path, input, logs, view) =
                (path.clone(), input.clone(), logs.clone(), view.clone());
            let dialog = super::dialogs::dialog_frame(dialog, window)
                .title(format!("Python 后端 · {name}"))
                .w(px(490.))
                .child(content);
            if running {
                return dialog;
            }
            dialog
                .child(super::dialogs::field("附加参数", &input))
                .confirm_with("后台启动", move |_, window, cx| {
                    let args = input.read(cx).value();
                    let result = view
                        .update(cx, |this, _| {
                            if let Some(backend) = this.backends.get_mut(&path)
                                && backend.child.try_wait()?.is_none()
                            {
                                anyhow::bail!("此后端已经在运行");
                            }
                            let entry = this
                                .catalog
                                .entries
                                .iter()
                                .find(|entry| entry.path == path)
                                .ok_or_else(|| anyhow::anyhow!("条目已离开工作区"))?;
                            let backend = launch::start_backend(entry, args.as_ref(), &logs)?;
                            this.backends.insert(path.clone(), backend);
                            Ok::<_, anyhow::Error>(())
                        })
                        .and_then(|result| result);
                    match result {
                        Ok(()) => {
                            window.push_notification("Python 后端已启动", cx);
                            true
                        }
                        Err(error) => {
                            Self::notify_error(error, window, cx);
                            false
                        }
                    }
                })
        });
    }
}
