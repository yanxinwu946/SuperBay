#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod autostart;
mod catalog;
mod hotkey;
mod launch;
mod resources;
mod scoop;
mod single_instance;
mod storage;
mod tray;
mod view;

use gpui::{
    App, AppContext, Application, Bounds, Image, ImageFormat, WindowBounds, WindowOptions, px, size,
};
use gpui_component::{Root, WindowExt};
use std::sync::{Arc, LazyLock};

pub(crate) static LOGO: LazyLock<Arc<Image>> = LazyLock::new(|| {
    Arc::new(Image::from_bytes(
        ImageFormat::Png,
        include_bytes!("../assets/superbay-app.png").to_vec(),
    ))
});

fn main() {
    if !single_instance::acquire() {
        return;
    }
    Application::new()
        .with_assets(gpui_component_assets::Assets)
        .run(|cx: &mut App| {
            gpui_component::init(cx);
            view::bind_keys(cx);
            cx.on_window_closed(|cx| {
                if cx.windows().is_empty() {
                    cx.quit();
                }
            })
            .detach();
            let preferences = storage::Settings::load().ok();
            let start_minimized = std::env::args_os().any(|argument| argument == "--minimized")
                || preferences
                    .as_ref()
                    .is_some_and(storage::Settings::start_minimized);
            let hotkey_setting = preferences
                .as_ref()
                .map(|settings| settings.hotkey().to_owned())
                .unwrap_or_else(|| hotkey::DEFAULT.to_owned());
            let (hotkey, hotkey_setting_error) = match hotkey::from_setting(&hotkey_setting) {
                Ok(hotkey) => (hotkey, None),
                Err(error) => (
                    hotkey::from_setting(hotkey::DEFAULT).unwrap_or(None),
                    Some(error),
                ),
            };
            let bounds = Bounds::centered(None, size(px(1180.), px(760.)), cx);
            let result = cx.open_window(
                WindowOptions {
                    window_bounds: Some(WindowBounds::Windowed(bounds)),
                    window_min_size: Some(size(px(820.), px(530.))),
                    focus: !start_minimized,
                    titlebar: Some(gpui::TitlebarOptions {
                        title: Some("SuperBay".into()),
                        ..Default::default()
                    }),
                    ..Default::default()
                },
                |window, cx| {
                    if start_minimized {
                        tray::hide(window);
                    }
                    let view = cx.new(|cx| view::Workspace::new(window, cx));
                    let workspace = view.downgrade();
                    let root = cx.new(|cx| Root::new(view, window, cx));
                    let (send, receive) = async_channel::unbounded();
                    let tray = tray::install(
                        window,
                        send,
                        &tray::Preferences {
                            hotkey: hotkey.clone(),
                        },
                    );
                    let tray_ready = match tray {
                        Ok((handle, hotkey_error)) => {
                            if let Some(error) = hotkey_error {
                                window.push_notification(format!("{error:#}"), cx);
                            }
                            let _ =
                                workspace.update(cx, |workspace, _| workspace.attach_tray(handle));
                            true
                        }
                        Err(error) => {
                            window.push_notification(format!("托盘初始化失败 · {error:#}"), cx);
                            false
                        }
                    };
                    if let Some(error) = hotkey_setting_error {
                        window.push_notification(
                            format!("{error:#}，已使用 {}", hotkey::DEFAULT_LABEL),
                            cx,
                        );
                    }
                    if start_minimized && !tray_ready {
                        // Do not strand the user without a window when the tray failed.
                        tray::reveal(window);
                    }
                    let tray_workspace = workspace.clone();
                    window
                        .spawn(cx, async move |cx| {
                            while let Ok(event) = receive.recv().await {
                                let result = cx.update(|window, cx| match event {
                                    tray::Event::Show => {
                                        let _ = tray_workspace.update(cx, |workspace, cx| {
                                            workspace.summon(window, cx)
                                        });
                                    }
                                    tray::Event::Settings => {
                                        let _ = tray_workspace.update(cx, |workspace, cx| {
                                            workspace.summon(window, cx);
                                            workspace.open_tray_settings(window, cx);
                                        });
                                    }
                                    tray::Event::Quit => {
                                        if tray_workspace
                                            .update(cx, |workspace, cx| {
                                                workspace.flush_notes(window, cx)
                                            })
                                            .unwrap_or(true)
                                        {
                                            cx.quit();
                                        } else {
                                            tray::reveal(window);
                                        }
                                    }
                                });
                                if result.is_err() {
                                    break;
                                }
                            }
                        })
                        .detach();
                    window.on_window_should_close(cx, move |window, cx| {
                        let saved = workspace
                            .update(cx, |workspace, cx| workspace.flush_notes(window, cx))
                            .unwrap_or(true);
                        if !saved {
                            return false;
                        }
                        if !tray_ready {
                            let _ = workspace.update(cx, |workspace, cx| workspace.quit_app(cx));
                            return true;
                        }
                        match workspace
                            .update(cx, |workspace, _| workspace.close_action())
                            .unwrap_or(view::CloseAction::Ask)
                        {
                            view::CloseAction::Ask => {
                                if !window.has_active_dialog(cx) {
                                    let _ = workspace.update(cx, |workspace, cx| {
                                        workspace.confirm_close(window, cx)
                                    });
                                }
                                false
                            }
                            view::CloseAction::Tray => {
                                let _ = workspace
                                    .update(cx, |workspace, cx| workspace.hide_to_tray(window, cx));
                                false
                            }
                            view::CloseAction::Quit => {
                                let _ =
                                    workspace.update(cx, |workspace, cx| workspace.quit_app(cx));
                                true
                            }
                        }
                    });
                    root
                },
            );
            if let Err(error) = result {
                view::report_startup_error(&error.to_string());
                cx.quit();
            }
        });
}
