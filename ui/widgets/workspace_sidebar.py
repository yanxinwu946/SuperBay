"""Workspace navigation defaults and width adaptation for resizable panels."""
from ui.widgets.sidebar import SideBar


class WorkspaceSideBar(SideBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(200)
        self.setMaximumWidth(420)
        self._known_categories = set()
        self._category_scope = None

    def rebuild(self, categories, total, star_count, recent_count, tags):
        scope = tuple(sorted({str(cat.path if cat.cat_id == "__root__" else cat.path.parent)
                              for cat in categories if not cat.parent_id}))
        if scope != self._category_scope:
            self._category_scope = scope
            self._known_categories.clear()
            self._collapsed.clear()
        keys = {cat.cat_id for cat in categories}
        self._collapsed.intersection_update(keys)
        self._collapsed.update(keys-self._known_categories)
        self._known_categories = keys
        super().rebuild(categories, total, star_count, recent_count, tags)
        self._fit_category_widths()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_category_widths()

    def _fit_category_widths(self):
        if not hasattr(self, "_cat_scroll"):
            return
        available = self.width()-20-self._cat_scroll.verticalScrollBar().sizeHint().width()
        for key, (_, category, _, _) in getattr(self, "_category_rows", {}).items():
            self._nav_buttons[key].setMaximumWidth(max(96, available-18-min(category.depth, 3)*12))
