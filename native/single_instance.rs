use windows::{
    Win32::{
        Foundation::{
            ERROR_ALREADY_EXISTS, GetLastError, LPARAM, SetLastError, WIN32_ERROR, WPARAM,
        },
        System::Threading::CreateMutexW,
        UI::WindowsAndMessaging::{HWND_BROADCAST, PostMessageW, RegisterWindowMessageW},
    },
    core::{PCWSTR, w},
};

const MUTEX_NAME: PCWSTR = w!("Local\\SuperBay.SingleInstance");
const ACTIVATE_MESSAGE: PCWSTR = w!("SuperBay.Activate");

/// Returns false when another SuperBay instance is already running. In that case
/// the existing instance is asked to show its window.
pub fn acquire() -> bool {
    unsafe {
        SetLastError(WIN32_ERROR(0));
        let Ok(_handle) = CreateMutexW(None, false, MUTEX_NAME) else {
            // A mutex failure should not prevent SuperBay from starting.
            return true;
        };
        if GetLastError() == ERROR_ALREADY_EXISTS {
            request_show();
            false
        } else {
            true
        }
    }
}

pub fn activate_message() -> u32 {
    unsafe { RegisterWindowMessageW(ACTIVATE_MESSAGE) }
}

fn request_show() {
    let message = activate_message();
    if message != 0 {
        unsafe {
            let _ = PostMessageW(Some(HWND_BROADCAST), message, WPARAM(0), LPARAM(0));
        }
    }
}
