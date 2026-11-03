"""Soft action feedback without changing button geometry."""
from shiboken6 import isValid
from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect


class ActionGlow(QObject):
    def __init__(self, button):
        super().__init__(button)
        self.button = button
        self.hovered = False
        self.pressed = False
        self.effect = QGraphicsDropShadowEffect(button)
        self.effect.setOffset(0, 0)
        button.setGraphicsEffect(self.effect)
        button.installEventFilter(self)
        button.toggled.connect(self.update)
        button.pressed.connect(lambda: self.set_pressed(True))
        button.released.connect(lambda: self.set_pressed(False))
        self.update()

    def set_pressed(self, value):
        self.pressed = value
        self.update()

    def update(self, *_):
        if not isValid(self.button) or not isValid(self.effect): return
        active = self.button.isEnabled() and (self.hovered or self.button.isChecked() or self.button.hasFocus())
        self.effect.setBlurRadius(8 if self.pressed else 16)
        self.effect.setColor(QColor(60, 181, 111, 75 if self.button.isChecked() else 48))
        self.effect.setEnabled(active)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter: self.hovered = True
        elif event.type() == QEvent.Type.Leave: self.hovered = False
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.FocusIn,
                            QEvent.Type.FocusOut, QEvent.Type.EnabledChange):
            self.update()
        return False
