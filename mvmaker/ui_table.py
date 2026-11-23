"""Excel 式表格：平常只顯示文字，正在輸入的那一格才出現下拉或輸入框。

規則（整個 App 都一樣）：
- 點一下或 Tab 開始輸入；Enter 確定並跳下一格；Esc 取消
- 下拉選單要再點一下或按 ↓ 才打開；↑↓ 不會直接換值
- 滑鼠滾輪絕對不會改到欄位的值
- 輸入錯誤：格子標紅，不存
- 數字靠右
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, Signal, QTimer
from PySide6.QtGui import QColor, QBrush, QKeyEvent
from PySide6.QtWidgets import (QTableWidget, QTableWidgetItem, QStyledItemDelegate, QLineEdit,
                               QComboBox, QAbstractItemView, QHeaderView, QAbstractItemDelegate)

ERR_BG = QColor(230, 70, 70, 110)
ERR_FG = QColor(200, 30, 30)
ACTIVE_BG = QColor(80, 150, 230, 60)


class NoWheelCombo(QComboBox):
    """滾輪不改值；↑ 不動；↓ 打開清單。"""

    def wheelEvent(self, e):
        e.ignore()

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key_Down and not e.modifiers():
            self.showPopup()
            return
        if e.key() in (Qt.Key_Up, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Escape):
            e.ignore()
            return
        super().keyPressEvent(e)


class NoWheelLine(QLineEdit):
    def wheelEvent(self, e):
        e.ignore()


class Col:
    """欄位定義。kind: text / time / seconds / combo / check / bool / star / thumb / label"""

    def __init__(self, key, title, kind="text", width=120, options=None, stretch=False, right=False):
        self.key, self.title, self.kind, self.width = key, title, kind, width
        self.options = options or []
        self.stretch = stretch
        self.right = right          # 數字下拉也靠右

    @property
    def numeric(self):
        return self.kind in ("time", "seconds", "int") or self.right


class ExcelDelegate(QStyledItemDelegate):
    def __init__(self, table: "ExcelTable"):
        super().__init__(table)
        self.t = table

    def createEditor(self, parent, option, index):
        col = self.t.cols[index.column()]
        if col.kind == "combo":
            ed = NoWheelCombo(parent)
            opts = self.t.options_for(index.row(), col)
            ed.addItems(opts)
            ed.setFocusPolicy(Qt.StrongFocus)
            ed.activated.connect(lambda _i, e=ed: self._combo_picked(e))
        else:
            ed = NoWheelLine(parent)
            if col.numeric:
                ed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ed.installEventFilter(self)
        return ed

    def _combo_picked(self, ed):
        self.commitData.emit(ed)

    def setEditorData(self, ed, index):
        txt = index.data(Qt.EditRole) or ""
        if isinstance(ed, QComboBox):
            i = ed.findText(txt)
            ed.setCurrentIndex(max(0, i))
        else:
            ed.setText(txt)
            ed.selectAll()

    def setModelData(self, ed, model, index):
        text = ed.currentText() if isinstance(ed, QComboBox) else ed.text()
        self.t.commit_text(index.row(), index.column(), text)

    def updateEditorGeometry(self, ed, option, index):
        ed.setGeometry(option.rect)

    def eventFilter(self, ed, ev):
        if ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Return, Qt.Key_Enter) and not (ev.modifiers() & Qt.ShiftModifier):
                if isinstance(ed, QComboBox) and ed.view().isVisible():
                    return False
                self.commitData.emit(ed)
                self.closeEditor.emit(ed, QAbstractItemDelegate.NoHint)
                QTimer.singleShot(0, lambda: self.t.move_next(1))
                return True
            if k in (Qt.Key_Tab, Qt.Key_Backtab):
                back = k == Qt.Key_Backtab or bool(ev.modifiers() & Qt.ShiftModifier)
                self.commitData.emit(ed)
                self.closeEditor.emit(ed, QAbstractItemDelegate.NoHint)
                QTimer.singleShot(0, lambda: self.t.move_next(-1 if back else 1))
                return True
            if k == Qt.Key_Escape:
                self.closeEditor.emit(ed, QAbstractItemDelegate.RevertModelCache)
                return True
        if ev.type() == QEvent.Wheel:
            ev.ignore()
            return True
        return super().eventFilter(ed, ev)


class ExcelTable(QTableWidget):
    """子類別要實作：row_count、cell_text、cell_check、set_value。"""

    changed = Signal()
    checked_changed = Signal()

    def __init__(self, cols: list[Col], parent=None, row_height=44, blank_row=False):
        super().__init__(parent)
        self.cols = cols
        self.blank_row = blank_row
        self.errors: dict[tuple[int, int], str] = {}
        self.active_row = -1
        self._loading = False
        self.setColumnCount(len(cols))
        self.setHorizontalHeaderLabels([c.title for c in cols])
        self.setItemDelegate(ExcelDelegate(self))
        self.setEditTriggers(QAbstractItemView.CurrentChanged | QAbstractItemView.SelectedClicked |
                             QAbstractItemView.DoubleClicked | QAbstractItemView.AnyKeyPressed |
                             QAbstractItemView.EditKeyPressed)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setTabKeyNavigation(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideMiddle)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setMinimumSectionSize(40)
        for i, c in enumerate(cols):
            if c.stretch:
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                hh.setSectionResizeMode(i, QHeaderView.Fixed)
                self.setColumnWidth(i, c.width)
            item = self.horizontalHeaderItem(i)
            if c.numeric:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            elif c.kind in ("check", "bool", "star"):
                item.setTextAlignment(Qt.AlignCenter)
            else:
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.itemChanged.connect(self._item_changed)

    # ---- 子類別實作
    def row_count(self) -> int:
        return 0

    def cell_text(self, r: int, key: str) -> str:
        return ""

    def cell_check(self, r: int, key: str) -> bool | None:
        """check / bool 欄：回傳 True/False；None = 這列沒有這個勾選框"""
        return None

    def set_check(self, r: int, key: str, v: bool):
        pass

    def set_value(self, r: int, key: str, text: str) -> bool:
        """回傳 False 表示輸入錯誤（不存、標紅）。r == row_count() 代表空白輸入列。"""
        return True

    def editable(self, r: int, col: Col) -> bool:
        return col.kind in ("text", "time", "seconds", "combo", "int")

    def options_for(self, r: int, col: Col) -> list[str]:
        return col.options

    def decorate(self, r: int, items: dict):
        """子類別可改字色、圖示。"""

    # ---- 共用
    def refresh(self):
        self._loading = True
        n = self.row_count()
        total = n + (1 if self.blank_row else 0)
        self.setRowCount(total)
        for r in range(total):
            items = {}
            for ci, c in enumerate(self.cols):
                it = self.item(r, ci)
                if it is None:
                    it = QTableWidgetItem()
                    self.setItem(r, ci, it)
                blank = r >= n
                flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                it.setData(Qt.CheckStateRole, None)
                if c.kind in ("check", "bool"):
                    v = None if blank else self.cell_check(r, c.key)
                    if v is not None:
                        flags |= Qt.ItemIsUserCheckable
                        it.setCheckState(Qt.Checked if v else Qt.Unchecked)
                    it.setText("")
                else:
                    err = self.errors.get((r, ci))
                    txt = err if err is not None else ("" if blank and c.kind not in ("text", "time", "seconds") else
                                                       self.cell_text(r, c.key) if not blank else "")
                    it.setText(txt)
                    if self.editable(r, c) and not (blank and c.kind not in ("text", "time")):
                        flags |= Qt.ItemIsEditable
                    it.setBackground(QBrush(ERR_BG) if err is not None else
                                     QBrush(ACTIVE_BG) if r == self.active_row else QBrush())
                    it.setForeground(QBrush(ERR_FG) if err is not None else QBrush())
                if c.numeric:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif c.kind in ("check", "bool", "star"):
                    it.setTextAlignment(Qt.AlignCenter)
                else:
                    it.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                it.setFlags(flags)
                items[c.key] = it
            if r < n:
                self.decorate(r, items)
        self._loading = False

    def set_active_row(self, r: int):
        if r == self.active_row:
            return
        old, self.active_row = self.active_row, r
        for rr in (old, r):
            if 0 <= rr < self.rowCount():
                for ci, c in enumerate(self.cols):
                    it = self.item(rr, ci)
                    if it and c.kind not in ("check", "bool") and (rr, ci) not in self.errors:
                        self._loading = True
                        it.setBackground(QBrush(ACTIVE_BG) if rr == r else QBrush())
                        self._loading = False
        if r >= 0 and self.state() != QAbstractItemView.EditingState:
            self.scrollToItem(self.item(r, 0), QAbstractItemView.EnsureVisible)

    def commit_text(self, r: int, ci: int, text: str):
        c = self.cols[ci]
        ok = self.set_value(r, c.key, text.strip() if c.kind != "text" else text)
        if ok:
            self.errors.pop((r, ci), None)
        else:
            self.errors[(r, ci)] = text
        QTimer.singleShot(0, self._after_commit)

    def _after_commit(self):
        self.refresh()
        self.changed.emit()

    def _item_changed(self, it: QTableWidgetItem):
        if self._loading:
            return
        c = self.cols[it.column()]
        if c.kind in ("check", "bool") and it.flags() & Qt.ItemIsUserCheckable:
            r = it.row()
            if r < self.row_count():
                self.set_check(r, c.key, it.checkState() == Qt.Checked)
                if c.kind == "check":
                    self.checked_changed.emit()
                else:
                    self.changed.emit()

    def move_next(self, step: int):
        """跳到下一個可以輸入的格子（同一列往右，到底換下一列）。"""
        r, ci = self.currentRow(), self.currentColumn()
        total = self.rowCount()
        for _ in range(total * len(self.cols) + 1):
            ci += step
            if ci >= len(self.cols):
                ci, r = 0, r + 1
            elif ci < 0:
                ci, r = len(self.cols) - 1, r - 1
            if r < 0 or r >= total:
                return
            it = self.item(r, ci)
            if it and it.flags() & Qt.ItemIsEditable:
                self.setCurrentCell(r, ci)
                if self.state() != QAbstractItemView.EditingState:
                    self.edit(self.currentIndex())
                return

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Return, Qt.Key_Enter) and self.state() != QAbstractItemView.EditingState:
            self.move_next(1)
            return
        if k in (Qt.Key_Tab, Qt.Key_Backtab):
            self.move_next(-1 if k == Qt.Key_Backtab else 1)
            return
        if k == Qt.Key_Space:
            it = self.currentItem()
            if it and it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
                return
        if k == Qt.Key_Delete and self.state() != QAbstractItemView.EditingState:
            it = self.currentItem()
            c = self.cols[self.currentColumn()] if self.currentColumn() >= 0 else None
            if it and c and it.flags() & Qt.ItemIsEditable and c.kind != "combo" and self.currentRow() < self.row_count():
                self.commit_text(self.currentRow(), self.currentColumn(), "")
                return
        super().keyPressEvent(e)

    def checked_rows(self) -> list[int]:
        out = []
        for ci, c in enumerate(self.cols):
            if c.kind == "check":
                for r in range(self.row_count()):
                    if self.cell_check(r, c.key):
                        out.append(r)
        return out
