"""Theme loading and shared material tokens for the application."""
import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)


def apply_theme(app: QApplication, app_dir: Path, theme: str) -> None:
    """加载 ui/themes/<theme>.qss；文件没了只记日志，不影响启动。"""
    qss_path = app_dir / "ui" / "themes" / f"{theme}.qss"
    try:
        app.setProperty("superguiTheme", theme)
        app.setStyle("Fusion")
        controls = (app_dir / "ui/themes/controls.qss").read_text(encoding="utf-8")
        palette = ({"surface": "#2b2f36", "base": "#1e2126", "text": "#e6e9ee", "border": "#3a3f47", "muted": "#9aa3af", "selection": "#2f5d43"}
                   if theme == "dark" else
                   {"surface": "#ffffff", "base": "#f4f6f8", "text": "#2b323d", "border": "#e8ebee", "muted": "#6b7380", "selection": "#e9f7ef"})
        # Explicit fallback palette prevents unstyled viewports inheriting OS dark colors.
        from PySide6.QtGui import QPalette, QColor
        native_palette = app.style().standardPalette()
        for role, color in ((QPalette.ColorRole.Window, palette["base"]),
                            (QPalette.ColorRole.Base, palette["surface"]),
                            (QPalette.ColorRole.AlternateBase, palette["base"]),
                            (QPalette.ColorRole.Text, palette["text"]),
                            (QPalette.ColorRole.WindowText, palette["text"]),
                            (QPalette.ColorRole.Button, palette["surface"]),
                            (QPalette.ColorRole.ButtonText, palette["text"]),
                            (QPalette.ColorRole.Highlight, palette["selection"]),
                            (QPalette.ColorRole.HighlightedText, palette["text"])):
            native_palette.setColor(role, QColor(color))
        app.setPalette(native_palette)
        glass = (app_dir / "ui/themes/glass.qss").read_text(encoding="utf-8")
        palette.update(({
            "glassTop": "rgba(55,59,65,215)", "glassBottom": "rgba(38,41,47,230)",
            "glassEdge": "rgba(255,255,255,25)", "glassHighlight": "rgba(255,255,255,62)",
            "contentSurface": "rgba(30,33,38,248)", "primary": "#5fd08e", "primaryText": "#1e2126",
        } if theme == "dark" else {
            "glassTop": "rgba(255,255,255,215)", "glassBottom": "rgba(244,246,248,230)",
            "glassEdge": "rgba(43,50,61,20)", "glassHighlight": "rgba(255,255,255,245)",
            "contentSurface": "rgba(255,255,255,248)", "primary": "#287c4b", "primaryText": "#ffffff",
        }))
        palette.update(assets=(app_dir / "assets/controls").as_posix(), theme=theme)
        for key in sorted(palette, key=len, reverse=True):
            value = palette[key]
            controls = controls.replace("@" + key, value)
            glass = glass.replace("@" + key, value)
        app.setStyleSheet(qss_path.read_text(encoding="utf-8") + controls + glass)
    except OSError as e:
        log.error("加载主题失败 %s: %s", qss_path, e)
