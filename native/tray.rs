use std::{
    cell::Cell,
    mem::size_of,
    sync::{Arc, Mutex},
};

use anyhow::{Result, anyhow, bail};
use gpui::{Keystroke, Window};
use gpui_component::kbd::Kbd;
use raw_window_handle::{HasWindowHandle, RawWindowHandle};
use windows::{
    Win32::{
        Foundation::{COLORREF, HINSTANCE, HWND, LPARAM, LRESULT, POINT, WPARAM},
        System::LibraryLoader::GetModuleHandleW,
        UI::{
            Input::KeyboardAndMouse::{RegisterHotKey, UnregisterHotKey},
            Shell::{
                DefSubclassProc, NIF_ICON, NIF_INFO, NIF_MESSAGE, NIF_SHOWTIP, NIF_TIP, NIIF_INFO,
                NIM_ADD, NIM_DELETE, NIM_MODIFY, NIM_SETFOCUS, NIM_SETVERSION, NIN_SELECT,
                NINF_KEY, NOTIFYICON_VERSION_4, NOTIFYICONDATAW, NOTIFYICONDATAW_0,
                NOTIFYICONIDENTIFIER, RemoveWindowSubclass, SetWindowSubclass,
                Shell_NotifyIconGetRect, Shell_NotifyIconW,
            },
            WindowsAndMessaging::{
                AppendMenuW, CreatePopupMenu, CreateWindowExW, DestroyMenu, DestroyWindow,
                GetCursorPos, GetSystemMetrics, IsIconic, IsWindowVisible, LWA_ALPHA, LoadIconW,
                MF_SEPARATOR, MF_STRING, PostMessageW, RegisterWindowMessageW,
                SM_MENUDROPALIGNMENT, SW_HIDE, SW_RESTORE, SW_SHOW, SW_SHOWNOACTIVATE,
                SetForegroundWindow, SetLayeredWindowAttributes, SetMenuDefaultItem, ShowWindow,
                TPM_LEFTALIGN, TPM_NONOTIFY, TPM_RETURNCMD, TPM_RIGHTALIGN, TPM_RIGHTBUTTON,
                TPM_VERTICAL, TPMPARAMS, TrackPopupMenuEx, WM_APP, WM_CONTEXTMENU, WM_HOTKEY,
                WM_LBUTTONUP, WM_NCDESTROY, WM_NULL, WM_RBUTTONUP, WS_EX_LAYERED, WS_EX_TOOLWINDOW,
                WS_EX_TOPMOST, WS_POPUP,
            },
        },
    },
    core::{HSTRING, PCWSTR, w},
};

const TRAY_MESSAGE: u32 = WM_APP + 42;
const HOTKEY_ID: i32 = 1;

#[derive(Clone, Copy)]
pub enum Event {
    Show,
    Settings,
    Quit,
}

#[derive(Clone)]
pub struct Preferences {
    pub hotkey: Option<Keystroke>,
}

#[derive(Clone)]
pub struct Handle {
    hwnd: HWND,
    state: Arc<State>,
}

struct State {
    hotkey: Mutex<Option<Keystroke>>,
}

impl State {
    fn hotkey(&self) -> Option<Keystroke> {
        self.hotkey.lock().ok().and_then(|hotkey| hotkey.clone())
    }
}

struct Tray {
    icon: NOTIFYICONDATAW,
    menu_owner: MenuOwner,
    version_4: Cell<bool>,
    taskbar_created: u32,
    activate: u32,
    state: Arc<State>,
    sender: async_channel::Sender<Event>,
}

pub fn install(
    window: &Window,
    sender: async_channel::Sender<Event>,
    preferences: &Preferences,
) -> Result<(Handle, Option<anyhow::Error>)> {
    let handle =
        HasWindowHandle::window_handle(window).map_err(|_| anyhow!("无法读取主窗口句柄"))?;
    let RawWindowHandle::Win32(handle) = handle.as_raw() else {
        bail!("需要 Win32 窗口");
    };
    let hwnd = HWND(handle.hwnd.get() as *mut _);
    unsafe {
        let module = GetModuleHandleW(PCWSTR::null())?;
        let instance = HINSTANCE(module.0);
        let icon = LoadIconW(Some(instance), PCWSTR(1 as *const u16))?;
        let menu_owner = MenuOwner::new(instance)?;
        let state = Arc::new(State {
            hotkey: Mutex::new(None),
        });
        let hotkey_error = match &preferences.hotkey {
            Some(hotkey) => match crate::hotkey::binding(hotkey) {
                Ok(binding) => {
                    if RegisterHotKey(
                        Some(hwnd),
                        HOTKEY_ID,
                        binding.modifiers,
                        binding.virtual_key,
                    )
                    .is_ok()
                    {
                        if let Ok(mut current) = state.hotkey.lock() {
                            *current = Some(hotkey.clone());
                        }
                        None
                    } else {
                        Some(anyhow!(
                            "无法注册 {}，可能已被其他程序占用",
                            Kbd::format(hotkey)
                        ))
                    }
                }
                Err(error) => Some(error),
            },
            None => None,
        };
        let mut data = NOTIFYICONDATAW {
            cbSize: size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: hwnd,
            uID: 1,
            uFlags: NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP,
            uCallbackMessage: TRAY_MESSAGE,
            hIcon: icon,
            ..Default::default()
        };
        apply_tip(&mut data, &state);
        let tray = Box::new(Tray {
            icon: data,
            menu_owner,
            version_4: Cell::new(false),
            taskbar_created: RegisterWindowMessageW(w!("TaskbarCreated")),
            activate: crate::single_instance::activate_message(),
            state: state.clone(),
            sender,
        });
        if !add_icon(&tray) {
            if state.hotkey().is_some() {
                let _ = UnregisterHotKey(Some(hwnd), HOTKEY_ID);
            }
            bail!("Windows 未能添加 SuperBay 托盘图标");
        }
        let raw = Box::into_raw(tray);
        if !SetWindowSubclass(hwnd, Some(tray_proc), 1, raw as usize).as_bool() {
            let tray = Box::from_raw(raw);
            cleanup(&tray);
            bail!("无法连接 Windows 托盘事件");
        }
        Ok((Handle { hwnd, state }, hotkey_error))
    }
}

struct MenuOwner(HWND);

impl MenuOwner {
    fn new(instance: HINSTANCE) -> Result<Self> {
        // A separate top-level window lets Windows activate the menu without raising SuperBay.
        let owner = Self(unsafe {
            CreateWindowExW(
                WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED,
                w!("STATIC"),
                PCWSTR::null(),
                WS_POPUP,
                0,
                0,
                1,
                1,
                None,
                None,
                Some(instance),
                None,
            )?
        });
        unsafe {
            SetLayeredWindowAttributes(owner.0, COLORREF(0), 0, LWA_ALPHA)?;
        }
        Ok(owner)
    }
}

impl Drop for MenuOwner {
    fn drop(&mut self) {
        unsafe {
            let _ = DestroyWindow(self.0);
        }
    }
}

impl Handle {
    pub fn hotkey(&self) -> Option<Keystroke> {
        self.state.hotkey()
    }

    pub fn hotkey_registered(&self) -> bool {
        self.state.hotkey().is_some()
    }

    pub fn set_hotkey(&self, hotkey: Option<Keystroke>) -> Result<()> {
        let binding = match &hotkey {
            Some(hotkey) => Some(crate::hotkey::binding(hotkey)?),
            None => None,
        };
        let mut current = self
            .state
            .hotkey
            .lock()
            .map_err(|_| anyhow!("快捷键状态不可用"))?;
        if *current == hotkey {
            return Ok(());
        }
        let previous = current.clone();
        unsafe {
            let _ = UnregisterHotKey(Some(self.hwnd), HOTKEY_ID);
        }
        if let Some(binding) = binding {
            if unsafe {
                RegisterHotKey(
                    Some(self.hwnd),
                    HOTKEY_ID,
                    binding.modifiers,
                    binding.virtual_key,
                )
            }
            .is_err()
            {
                if let Some(previous_hotkey) = previous {
                    if let Ok(previous_binding) = crate::hotkey::binding(&previous_hotkey) {
                        unsafe {
                            let _ = RegisterHotKey(
                                Some(self.hwnd),
                                HOTKEY_ID,
                                previous_binding.modifiers,
                                previous_binding.virtual_key,
                            );
                        }
                    }
                    *current = Some(previous_hotkey);
                }
                drop(current);
                self.update_tip();
                return Err(anyhow!(
                    "无法注册 {}，可能已被其他程序占用",
                    hotkey.as_ref().map(Kbd::format).unwrap_or_default()
                ));
            }
            *current = hotkey;
        } else {
            *current = None;
        }
        drop(current);
        self.update_tip();
        Ok(())
    }

    pub fn hint(&self) {
        let (title, message) = match self.state.hotkey() {
            Some(hotkey) => (
                "SuperBay 仍在运行",
                format!(
                    "按 {} 或点击托盘图标可随时召回，右键托盘图标可退出。",
                    Kbd::format(&hotkey)
                ),
            ),
            None => (
                "SuperBay 仍在运行",
                "点击托盘图标可随时召回，右键托盘图标可退出。".to_owned(),
            ),
        };
        let mut icon = NOTIFYICONDATAW {
            cbSize: size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: self.hwnd,
            uID: 1,
            uFlags: NIF_INFO,
            dwInfoFlags: NIIF_INFO,
            Anonymous: NOTIFYICONDATAW_0 { uTimeout: 8000 },
            ..Default::default()
        };
        set_text(&mut icon.szInfoTitle, title);
        set_text(&mut icon.szInfo, &message);
        unsafe {
            let _ = Shell_NotifyIconW(NIM_MODIFY, &icon);
        }
    }

    fn update_tip(&self) {
        let mut icon = NOTIFYICONDATAW {
            cbSize: size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: self.hwnd,
            uID: 1,
            uFlags: NIF_TIP | NIF_SHOWTIP,
            ..Default::default()
        };
        apply_tip(&mut icon, &self.state);
        unsafe {
            let _ = Shell_NotifyIconW(NIM_MODIFY, &icon);
        }
    }
}

pub fn hide(window: &Window) {
    if let Ok(handle) = HasWindowHandle::window_handle(window)
        && let RawWindowHandle::Win32(handle) = handle.as_raw()
    {
        unsafe {
            let _ = ShowWindow(HWND(handle.hwnd.get() as *mut _), SW_HIDE);
        }
    }
}

pub fn reveal(window: &Window) {
    if let Ok(handle) = HasWindowHandle::window_handle(window)
        && let RawWindowHandle::Win32(handle) = handle.as_raw()
    {
        show_window(HWND(handle.hwnd.get() as *mut _));
    }
}

fn show_window(hwnd: HWND) {
    unsafe {
        let _ = ShowWindow(
            hwnd,
            if IsIconic(hwnd).as_bool() {
                SW_RESTORE
            } else {
                SW_SHOW
            },
        );
        let _ = SetForegroundWindow(hwnd);
    }
}

fn apply_tip(icon: &mut NOTIFYICONDATAW, state: &State) {
    let title = match state.hotkey() {
        Some(hotkey) => format!("SuperBay · {}", Kbd::format(&hotkey)),
        None => "SuperBay".to_owned(),
    };
    set_text(&mut icon.szTip, &title);
}

fn set_text(buffer: &mut [u16], text: &str) {
    buffer.fill(0);
    let limit = buffer.len().saturating_sub(1);
    for (slot, code) in buffer.iter_mut().zip(text.encode_utf16()).take(limit) {
        *slot = code;
    }
}

unsafe fn add_icon(tray: &Tray) -> bool {
    unsafe {
        let mut icon = tray.icon;
        apply_tip(&mut icon, &tray.state);
        if !Shell_NotifyIconW(NIM_ADD, &icon).as_bool() {
            return false;
        }
        let mut version = icon;
        version.Anonymous.uVersion = NOTIFYICON_VERSION_4;
        tray.version_4
            .set(Shell_NotifyIconW(NIM_SETVERSION, &version).as_bool());
    }
    true
}

unsafe fn cleanup(tray: &Tray) {
    unsafe {
        let _ = Shell_NotifyIconW(NIM_DELETE, &tray.icon);
        if tray.state.hotkey().is_some() {
            let _ = UnregisterHotKey(Some(tray.icon.hWnd), HOTKEY_ID);
        }
    }
}

unsafe extern "system" fn tray_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    id: usize,
    data: usize,
) -> LRESULT {
    unsafe {
        if message == WM_NCDESTROY {
            let _ = RemoveWindowSubclass(hwnd, Some(tray_proc), id);
            cleanup(&Box::from_raw(data as *mut Tray));
            return DefSubclassProc(hwnd, message, wparam, lparam);
        }
        let tray = &*(data as *const Tray);
        match message {
            WM_HOTKEY if wparam.0 == HOTKEY_ID as usize => show(tray),
            TRAY_MESSAGE => match (lparam.0 as u32) & 0xffff {
                event
                    if event
                        == if tray.version_4.get() {
                            WM_CONTEXTMENU
                        } else {
                            WM_RBUTTONUP
                        } =>
                {
                    let position = tray.version_4.get().then(|| notification_point(wparam));
                    menu(tray, position);
                }
                WM_LBUTTONUP | NIN_SELECT => show(tray),
                event if event == NIN_SELECT | NINF_KEY => show(tray),
                _ => {}
            },
            message if message != 0 && message == tray.activate => show(tray),
            message if message != 0 && message == tray.taskbar_created => {
                add_icon(tray);
            }
            _ => return DefSubclassProc(hwnd, message, wparam, lparam),
        }
        LRESULT(0)
    }
}

fn notification_point(wparam: WPARAM) -> POINT {
    POINT {
        x: i32::from(wparam.0 as i16),
        y: i32::from((wparam.0 >> 16) as i16),
    }
}

unsafe fn show(tray: &Tray) {
    let (hwnd, sender) = (tray.icon.hWnd, tray.sender.clone());
    show_window(hwnd);
    let _ = sender.try_send(Event::Show);
}

unsafe fn menu(tray: &Tray, position: Option<POINT>) {
    const SHOW: i32 = 1;
    const SETTINGS: i32 = 2;
    const QUIT: i32 = 3;
    unsafe {
        let owner = tray.menu_owner.0;
        let icon = &tray.icon;
        if IsWindowVisible(owner).as_bool() {
            return;
        }
        let Ok(menu) = CreatePopupMenu() else {
            return;
        };
        let show_label = match tray.state.hotkey() {
            Some(hotkey) => format!("显示 SuperBay\t{}", Kbd::format(&hotkey)),
            None => "显示 SuperBay".to_owned(),
        };
        let show_label = HSTRING::from(show_label);
        let _ = AppendMenuW(menu, MF_STRING, SHOW as usize, &show_label);
        let _ = AppendMenuW(menu, MF_STRING, SETTINGS as usize, w!("设置…"));
        let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
        let _ = AppendMenuW(menu, MF_STRING, QUIT as usize, w!("退出"));
        let _ = SetMenuDefaultItem(menu, SHOW as u32, 0);
        let bounds = Shell_NotifyIconGetRect(&NOTIFYICONIDENTIFIER {
            cbSize: size_of::<NOTIFYICONIDENTIFIER>() as u32,
            hWnd: icon.hWnd,
            uID: icon.uID,
            ..Default::default()
        })
        .ok();
        let point = position
            .filter(|point| (point.x, point.y) != (-1, -1))
            .or_else(|| {
                bounds.map(|rect| POINT {
                    x: rect.left,
                    y: rect.top,
                })
            })
            .unwrap_or_else(|| {
                let mut point = POINT::default();
                let _ = GetCursorPos(&mut point);
                point
            });
        let exclude = bounds.map(|rc_exclude| TPMPARAMS {
            cbSize: size_of::<TPMPARAMS>() as u32,
            rcExclude: rc_exclude,
        });
        let align = if GetSystemMetrics(SM_MENUDROPALIGNMENT) == 0 {
            TPM_LEFTALIGN
        } else {
            TPM_RIGHTALIGN
        };
        let _ = ShowWindow(owner, SW_SHOWNOACTIVATE);
        let _ = SetForegroundWindow(owner);
        let choice = TrackPopupMenuEx(
            menu,
            (TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON | TPM_VERTICAL | align).0,
            point.x,
            point.y,
            owner,
            exclude.as_ref().map(std::ptr::from_ref),
        )
        .0;
        let _ = DestroyMenu(menu);
        let _ = PostMessageW(Some(owner), WM_NULL, WPARAM(0), LPARAM(0));
        let _ = ShowWindow(owner, SW_HIDE);
        match choice {
            SHOW => {
                show_window(icon.hWnd);
                let _ = tray.sender.try_send(Event::Show);
            }
            SETTINGS => {
                show_window(icon.hWnd);
                let _ = tray.sender.try_send(Event::Settings);
            }
            QUIT => {
                let _ = tray.sender.try_send(Event::Quit);
            }
            _ => {
                let _ = Shell_NotifyIconW(NIM_SETFOCUS, icon);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{WPARAM, notification_point, set_text};

    #[test]
    fn tray_anchor_preserves_negative_coordinates() {
        let packed = u32::from((-1100i16) as u16) | (u32::from((-24i16) as u16) << 16);
        let point = notification_point(WPARAM(packed as usize));
        assert_eq!((point.x, point.y), (-1100, -24));
    }

    #[test]
    fn tray_text_is_null_terminated() {
        let mut buffer = [1u16; 6];
        set_text(&mut buffer, "SuperBay");
        assert_eq!(
            buffer,
            [
                b'S' as u16,
                b'u' as u16,
                b'p' as u16,
                b'e' as u16,
                b'r' as u16,
                0
            ]
        );
    }
}
