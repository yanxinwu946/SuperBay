use anyhow::{Result, bail};
use gpui::Keystroke;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    HOT_KEY_MODIFIERS, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, VK_BACK, VK_DELETE,
    VK_DOWN, VK_END, VK_ESCAPE, VK_F1, VK_HOME, VK_INSERT, VK_LEFT, VK_NEXT, VK_PRIOR, VK_RETURN,
    VK_RIGHT, VK_SPACE, VK_TAB, VK_UP,
};

pub const DEFAULT: &str = "alt-s";
pub const DEFAULT_LABEL: &str = "Alt+S";

#[derive(Clone, Copy)]
pub struct Binding {
    pub modifiers: HOT_KEY_MODIFIERS,
    pub virtual_key: u32,
}

/// Convert a GPUI keystroke into the arguments used by `RegisterHotKey`.
pub fn binding(keystroke: &Keystroke) -> Result<Binding> {
    if !(keystroke.modifiers.control || keystroke.modifiers.alt || keystroke.modifiers.platform) {
        bail!("快捷键需要包含 Ctrl、Alt 或 Win");
    }
    if keystroke.modifiers.function {
        bail!("当前系统不支持 Fn 组合键");
    }
    let mut modifiers = MOD_NOREPEAT;
    if keystroke.modifiers.control {
        modifiers |= MOD_CONTROL;
    }
    if keystroke.modifiers.alt {
        modifiers |= MOD_ALT;
    }
    if keystroke.modifiers.shift {
        modifiers |= MOD_SHIFT;
    }
    if keystroke.modifiers.platform {
        modifiers |= MOD_WIN;
    }
    Ok(Binding {
        modifiers,
        virtual_key: virtual_key(&keystroke.key)?,
    })
}

/// Read a hotkey from config. An empty value means the shortcut is disabled.
pub fn from_setting(value: &str) -> Result<Option<Keystroke>> {
    let value = value.trim();
    if value.is_empty() {
        return Ok(None);
    }
    Keystroke::parse(value)
        .map(Some)
        .map_err(|_| anyhow::anyhow!("唤起快捷键格式无效：{value}"))
}

pub fn is_modifier(key: &str) -> bool {
    matches!(
        key,
        "alt"
            | "control"
            | "ctrl"
            | "shift"
            | "platform"
            | "win"
            | "super"
            | "cmd"
            | "function"
            | "fn"
    )
}

fn virtual_key(key: &str) -> Result<u32> {
    let key = key.to_ascii_lowercase();
    if key.len() == 1 {
        let byte = key.as_bytes()[0];
        if byte.is_ascii_alphanumeric() {
            return Ok(u32::from(byte.to_ascii_uppercase()));
        }
    }
    if let Some(number) = key.strip_prefix('f')
        && let Ok(number) = number.parse::<u32>()
        && (1..=24).contains(&number)
    {
        return Ok(u32::from(VK_F1.0) + number - 1);
    }
    let named = match key.as_str() {
        "space" => VK_SPACE,
        "tab" => VK_TAB,
        "enter" => VK_RETURN,
        "escape" => VK_ESCAPE,
        "backspace" => VK_BACK,
        "delete" => VK_DELETE,
        "insert" => VK_INSERT,
        "home" => VK_HOME,
        "end" => VK_END,
        "pageup" => VK_PRIOR,
        "pagedown" => VK_NEXT,
        "left" => VK_LEFT,
        "up" => VK_UP,
        "right" => VK_RIGHT,
        "down" => VK_DOWN,
        _ => bail!("暂不支持这个按键，请使用字母、数字、F1–F24 或方向键"),
    };
    Ok(u32::from(named.0))
}

#[cfg(test)]
mod tests {
    use super::{Binding, binding, from_setting};
    use gpui::Keystroke;
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT,
    };

    fn parse(source: &str) -> Binding {
        binding(&Keystroke::parse(source).unwrap()).unwrap()
    }

    #[test]
    fn default_hotkey_uses_alt_and_s() {
        let binding = parse("alt-s");
        assert_eq!(binding.modifiers, MOD_ALT | MOD_NOREPEAT);
        assert_eq!(binding.virtual_key, u32::from(b'S'));
    }

    #[test]
    fn modifiers_and_named_keys_map_to_win32() {
        let binding = parse("ctrl-alt-shift-pageup");
        assert_eq!(
            binding.modifiers,
            MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT
        );
        assert_eq!(binding.virtual_key, 33);
        assert_eq!(parse("win-f12").virtual_key, 123);
    }

    #[test]
    fn shortcut_requires_a_regular_modifier() {
        assert!(binding(&Keystroke::parse("s").unwrap()).is_err());
        assert!(binding(&Keystroke::parse("shift-s").unwrap()).is_err());
        assert!(binding(&Keystroke::parse("ctrl-s").unwrap()).is_ok());
    }

    #[test]
    fn unsupported_keys_are_rejected() {
        assert!(binding(&Keystroke::parse("alt-f25").unwrap()).is_err());
        assert!(binding(&Keystroke::parse("alt-unknown").unwrap()).is_err());
    }

    #[test]
    fn config_values_can_disable_the_hotkey() {
        assert!(from_setting("").unwrap().is_none());
        assert_eq!(
            from_setting("ctrl-alt-k").unwrap().unwrap().key,
            "k".to_owned()
        );
        assert!(from_setting("not-a-key").is_err());
    }
}
