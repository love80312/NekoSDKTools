#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NekoSDKTools 整合版 GUI：在「字元管理」右側加入獨立的 UIF 輔助分頁。

本程式沿用 GUI.py 的既有主視窗、PAK／腳本／BMP+ALP 功能及字元管理介面，
僅在主 Notebook 最右側新增一個同層級的「UIF 輔助工具」分頁。

必要檔案：
    GUI.py
    GUI_with_uif.py
    char_manager.py
    uif_char_helper.py
    pak.py、scr.py、bmp_alp.py

Python 3.10+；UIF 輔助工具仍可另外以 `py -3 uif_char_helper.py` 獨立啟動。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from GUI import NekoSDKToolsGUI
from uif_char_helper import UIFCharHelperApp


class NekoSDKToolsGUIWithUIF(NekoSDKToolsGUI):
    """在原主 GUI 的「字元管理」右側加入 UIF 同層級分頁。"""

    def _build_ui(self) -> None:
        # 先完整建立原本的主視窗與既有分頁。
        super()._build_ui()

        # 原本 Notebook 順序為：PAK、腳本／JSON、BMP／ALP、字元管理。
        # 直接 append，確保 UIF 是「字元管理」右側的同層級新分頁。
        self.uif_tab = ttk.Frame(
            self.notebook,
            padding=6,
        )
        self.notebook.add(
            self.uif_tab,
            text="UIF 輔助工具",
        )

        # 主 GUI 下方還有執行記錄區；以可捲動畫布包住 UIF 頁面，
        # 避免視窗高度不足時，下方的替換資料夾與開始按鈕被擠出可視範圍。
        scroll_host = ttk.Frame(self.uif_tab)
        scroll_host.pack(
            fill="both",
            expand=True,
        )
        scroll_host.rowconfigure(0, weight=1)
        scroll_host.columnconfigure(0, weight=1)

        self.uif_canvas = tk.Canvas(
            scroll_host,
            highlightthickness=0,
            borderwidth=0,
        )
        self.uif_canvas.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        self.uif_scrollbar = ttk.Scrollbar(
            scroll_host,
            orient="vertical",
            command=self.uif_canvas.yview,
        )
        self.uif_scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )
        self.uif_canvas.configure(
            yscrollcommand=self.uif_scrollbar.set,
        )

        self.uif_content = ttk.Frame(self.uif_canvas)
        self._uif_canvas_window = self.uif_canvas.create_window(
            (0, 0),
            window=self.uif_content,
            anchor="nw",
        )

        self.uif_content.bind(
            "<Configure>",
            self._update_uif_scroll_region,
        )
        self.uif_canvas.bind(
            "<Configure>",
            self._resize_uif_content,
        )

        # UIFCharHelperApp 本身就是可嵌入的 ttk.Frame；不會另外建立 Tk 根視窗。
        self.uif_helper_frame = UIFCharHelperApp(
            self.uif_content,
        )
        self.uif_helper_frame.pack(
            fill="both",
            expand=True,
        )

    def _update_uif_scroll_region(self, _event: tk.Event | None = None) -> None:
        """依 UIF 內容實際尺寸更新垂直捲動範圍。"""
        bounds = self.uif_canvas.bbox("all")
        if bounds is not None:
            self.uif_canvas.configure(
                scrollregion=bounds,
            )

    def _resize_uif_content(self, event: tk.Event) -> None:
        """讓 UIF 內容寬度跟隨分頁，不產生不必要的水平捲動。"""
        self.uif_canvas.itemconfigure(
            self._uif_canvas_window,
            width=max(1, event.width),
        )


def main() -> int:
    root = tk.Tk()
    NekoSDKToolsGUIWithUIF(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
