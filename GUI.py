#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NekoSDKTools GUI - a Tkinter front-end for pak.py, scr.py and bmp_alp.py.

Place this file in the same directory as pak.py, scr.py and bmp_alp.py.
Requires Python 3.10+. Pillow is required only for BMP/ALP operations.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

APP_NAME = "NekoSDKTools 圖形介面"
TOOL_FILES = {
    "pak": "pak.py",
    "scr": "scr.py",
    "bmp": "bmp_alp.py",
}


class NekoSDKToolsGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x780")
        self.root.minsize(860, 650)

        self.tool_dir = Path(__file__).resolve().parent
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None
        self.running = False
        self.cancel_requested = False
        self._run_log_lines: list[str] = []
        self.start_buttons: list[ttk.Button] = []
        self.cancel_buttons: list[ttk.Button] = []
        self.progressbars: list[ttk.Progressbar] = []
        self.row_widgets: dict[tuple[ttk.Frame, int], list[tk.Widget]] = {}

        self._configure_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_queue)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft JhengHei UI", 15, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Run.TButton", padding=(14, 7))
        style.configure("TLabelframe", padding=8)
        style.configure("TLabelframe.Label", font=("Microsoft JhengHei UI", 10, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(0, 8))
        ttk.Label(heading, text="NekoSDKTools", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            heading,
            text="封包解包／重包、腳本 JSON 匯出／匯入、BMP／ALP 圖像轉換",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)

        self.pak_tab = ttk.Frame(self.notebook, padding=12)
        self.scr_tab = ttk.Frame(self.notebook, padding=12)
        self.bmp_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.pak_tab, text="PAK 封包")
        self.notebook.add(self.scr_tab, text="腳本／JSON")
        self.notebook.add(self.bmp_tab, text="BMP／ALP 圖像")

        self._build_pak_tab()
        self._build_scr_tab()
        self._build_bmp_tab()

        log_frame = ttk.LabelFrame(outer, text="執行記錄")
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill="x", pady=(0, 5))
        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(log_toolbar, textvariable=self.status_var).pack(side="left")
        ttk.Button(log_toolbar, text="清除記錄", command=self.clear_log).pack(side="right")
        self.log_text = tk.Text(
            log_frame,
            height=10,
            wrap="none",
            font=("Consolas", 9),
            state="disabled",
            undo=False,
        )
        yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        xscroll = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        self.global_progress = ttk.Progressbar(footer, mode="indeterminate")
        self.global_progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.global_cancel = ttk.Button(footer, text="停止工作", command=self.stop_process, state="disabled")
        self.global_cancel.pack(side="right")

    # ---------- Shared UI helpers ----------
    @staticmethod
    def _path_row(parent: ttk.Frame, row: int, label: str, variable: tk.StringVar,
                  browse_file: Callable[[], None], browse_folder: Callable[[], None],
                  *, file_text: str = "選擇檔案", folder_text: str = "選擇資料夾") -> ttk.Label:
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        file_button = ttk.Button(parent, text=file_text, command=browse_file)
        file_button.grid(row=row, column=2, padx=(7, 3), pady=5)
        folder_button = ttk.Button(parent, text=folder_text, command=browse_folder)
        folder_button.grid(row=row, column=3, padx=(3, 0), pady=5)
        parent.columnconfigure(1, weight=1)
        # Keep explicit references so optional form rows can be hidden/restored reliably.
        # The returned label remains convenient for changing row captions.
        if not hasattr(parent, "_neko_row_widgets"):
            parent._neko_row_widgets = {}  # type: ignore[attr-defined]
        parent._neko_row_widgets[row] = [label_widget, entry, file_button, folder_button]  # type: ignore[attr-defined]
        return label_widget

    @staticmethod
    def _set_optional_row(label_widget: ttk.Label, *, row: int, visible: bool) -> None:
        parent = label_widget.master
        widgets = getattr(parent, "_neko_row_widgets", {}).get(row, [])
        for widget in widgets:
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    @staticmethod
    def _set_row_button_states(label_widget: ttk.Label, *, row: int, file_enabled: bool, folder_enabled: bool) -> None:
        widgets = getattr(label_widget.master, "_neko_row_widgets", {}).get(row, [])
        if len(widgets) >= 4:
            widgets[2].configure(state="normal" if file_enabled else "disabled")
            widgets[3].configure(state="normal" if folder_enabled else "disabled")

    def _register_run_controls(self, button: ttk.Button) -> None:
        self.start_buttons.append(button)

    def _register_progress(self, bar: ttk.Progressbar) -> None:
        self.progressbars.append(bar)

    @staticmethod
    def _resolved(path_text: str) -> Path:
        return Path(path_text.strip().strip('"')).expanduser().resolve(strict=False)

    @staticmethod
    def _is_inside(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    def _validate_tool(self, key: str) -> Path:
        path = self.tool_dir / TOOL_FILES[key]
        if not path.is_file():
            raise ValueError(f"找不到工具檔案：\n{path}\n\n請將 GUI.py 放在三支工具程式的同一個資料夾。")
        return path

    def _require_file(self, value: str, label: str) -> Path:
        if not value.strip():
            raise ValueError(f"請指定{label}。")
        path = self._resolved(value)
        if not path.is_file():
            raise ValueError(f"{label}不存在或不是檔案：\n{path}")
        return path

    def _require_dir(self, value: str, label: str) -> Path:
        if not value.strip():
            raise ValueError(f"請指定{label}。")
        path = self._resolved(value)
        if not path.is_dir():
            raise ValueError(f"{label}不存在或不是資料夾：\n{path}")
        return path

    def _confirm_output(self, path: Path, label: str) -> bool:
        if not path.exists():
            return True
        if path.is_dir():
            try:
                nonempty = next(path.iterdir(), None) is not None
            except OSError:
                nonempty = True
            if not nonempty:
                return True
            return messagebox.askyesno(
                "確認覆寫",
                f"{label}已存在且不是空資料夾：\n{path}\n\n執行時可能覆蓋其中同名檔案，是否繼續？",
                parent=self.root,
            )
        return messagebox.askyesno(
            "確認覆寫", f"{label}已存在：\n{path}\n\n是否覆蓋？", parent=self.root
        )

    def _set_busy(self, busy: bool) -> None:
        self.running = busy
        for button in self.start_buttons:
            button.configure(state="disabled" if busy else "normal")
        self.global_cancel.configure(state="normal" if busy else "disabled")
        if busy:
            self.global_progress.start(12)
        else:
            self.global_progress.stop()

    def _start(self, script_key: str, args: list[str], *, env_values: dict[str, str] | None = None,
               output_path: Path | None = None) -> None:
        if self.running:
            return
        tool = self._validate_tool(script_key)
        command = [sys.executable, "-u", str(tool), *args]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        if env_values:
            env.update(env_values)

        self.cancel_requested = False
        self._run_log_lines = []
        self._set_busy(True)
        self.status_var.set("執行中…")
        self._append_log("\n" + "=" * 72 + "\n")
        self._append_log("執行指令：\n" + subprocess.list2cmdline(command) + "\n")
        if env_values:
            self._append_log("環境設定：" + ", ".join(f"{k}={v}" for k, v in env_values.items()) + "\n")

        def run() -> None:
            return_code = -1
            try:
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                process = subprocess.Popen(
                    command,
                    cwd=str(self.tool_dir),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
                self.process = process
                assert process.stdout is not None
                for line in process.stdout:
                    self.event_queue.put(("log", line))
                return_code = process.wait()
            except Exception as exc:  # report worker errors in the Tk thread
                self.event_queue.put(("log", f"執行失敗：{type(exc).__name__}: {exc}\n"))
            finally:
                self.event_queue.put(("finished", (return_code, output_path)))

        self.worker = threading.Thread(target=run, name="NekoSDKToolsWorker", daemon=True)
        self.worker.start()

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "log":
                    line = str(payload)
                    self._run_log_lines.append(line)
                    if len(self._run_log_lines) > 200:
                        del self._run_log_lines[:-200]
                    self._append_log(line)
                elif kind == "finished":
                    code, output_path = payload  # type: ignore[misc]
                    self.process = None
                    self._set_busy(False)
                    if self.cancel_requested:
                        self.status_var.set("已停止")
                        self._append_log("\n工作已停止。\n")
                    elif code == 0:
                        self.status_var.set("完成")
                        self._append_log("\n作業完成。\n")
                        if output_path is not None:
                            self._append_log(f"輸出位置：{output_path}\n")
                    else:
                        short_reason, explanation = self._exit_code_explanation(code)
                        detail = self._last_run_error_detail()
                        self.status_var.set(f"失敗（代碼 {code}：{short_reason}）")
                        self._append_log(
                            f"\n作業失敗，結束代碼：{code}\n"
                            f"錯誤說明：{explanation}\n"
                        )
                        if detail:
                            self._append_log(f"詳細錯誤：{detail}\n")
                        dialog = f"結束代碼：{code}\n原因：{explanation}"
                        if detail:
                            dialog += f"\n\n詳細錯誤：\n{detail}"
                        messagebox.showerror("作業失敗", dialog, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    @staticmethod
    def _exit_code_explanation(code: int) -> tuple[str, str]:
        """Return a user-facing summary for common Python/tool exit codes."""
        if code == 0:
            return ("成功", "工具已正常完成。")
        if code == 1:
            return (
                "工具處理失敗",
                "工具執行時遇到資料或檔案錯誤，常見原因包括格式不符、字串編碼不符、JSON 內容錯誤，或檔案無法讀寫。請參考下方詳細錯誤。",
            )
        if code == 2:
            return (
                "命令列參數錯誤",
                "傳入的操作模式、參數數量或路徑格式不正確。請確認來源、輸出及必要參數。",
            )
        if code == -1:
            return (
                "工具啟動失敗",
                "無法啟動 Python 子程序。請確認 Python 執行環境、工具檔案及執行權限。",
            )
        if code == 130:
            return ("程序中斷", "工具收到中斷訊號而結束。")
        return (
            "非預期的結束代碼",
            f"工具以未列入常見錯誤對照的代碼 {code} 結束；請查看執行記錄末段。",
        )

    def _last_run_error_detail(self) -> str:
        """Extract the most useful error line from the child process output."""
        for line in reversed(self._run_log_lines):
            value = line.strip()
            if value.lower().startswith("error:"):
                return value[6:].strip()
            if value.startswith("執行失敗："):
                return value
            if value.lower().startswith("usage:"):
                return value
        return ""

    def stop_process(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            if messagebox.askyesno("停止工作", "確定要停止目前的工作？", parent=self.root):
                try:
                    self.cancel_requested = True
                    process.terminate()
                    self.status_var.set("正在停止…")
                except OSError as exc:
                    messagebox.showerror("停止失敗", str(exc), parent=self.root)

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno("結束程式", "目前仍有工作執行中。確定停止並關閉？", parent=self.root):
                return
            if self.process is not None and self.process.poll() is None:
                self.cancel_requested = True
                self.process.terminate()
        self.root.destroy()

    # ---------- PAK tab ----------
    def _build_pak_tab(self) -> None:
        frame = self.pak_tab
        self.pak_mode = tk.StringVar(value="unpack")
        self.pak_input = tk.StringVar()
        self.pak_output = tk.StringVar()
        self.pak_level = tk.StringVar(value="9")
        self.pak_workers = tk.StringVar(value="1")

        mode_box = ttk.LabelFrame(frame, text="操作")
        mode_box.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(mode_box, text="解包：PAK → 資料夾", variable=self.pak_mode,
                        value="unpack", command=self._pak_mode_changed).pack(side="left", padx=8, pady=4)
        ttk.Radiobutton(mode_box, text="重包：資料夾 → PAK", variable=self.pak_mode,
                        value="pack", command=self._pak_mode_changed).pack(side="left", padx=8, pady=4)

        paths = ttk.LabelFrame(frame, text="來源與輸出")
        paths.pack(fill="x", pady=(0, 10))
        self.pak_input_label = self._path_row(
            paths, 0, "PAK 檔案：", self.pak_input,
            self._pak_pick_input_file, self._pak_pick_input_folder,
            file_text="選擇 PAK", folder_text="選擇資料夾",
        )
        self.pak_output_label = self._path_row(
            paths, 1, "解包資料夾：", self.pak_output,
            self._pak_pick_output_file, self._pak_pick_output_folder,
            file_text="選擇檔案", folder_text="選擇位置",
        )

        options = ttk.LabelFrame(frame, text="重包設定")
        options.pack(fill="x", pady=(0, 10))
        ttk.Label(options, text="Zlib 壓縮等級：").pack(side="left", padx=(4, 5), pady=5)
        self.level_spin = ttk.Spinbox(options, from_=0, to=9, width=5, textvariable=self.pak_level)
        self.level_spin.pack(side="left", padx=(0, 18), pady=5)
        ttk.Label(options, text="工作執行緒：").pack(side="left", padx=(0, 5), pady=5)
        self.worker_spin = ttk.Spinbox(options, from_=1, to=16, width=5, textvariable=self.pak_workers)
        self.worker_spin.pack(side="left", pady=5)
        ttk.Label(options, text="舊電腦建議維持 1；壓縮等級 9 較省檔案空間。", style="Hint.TLabel").pack(side="left", padx=12)

        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        run_button = ttk.Button(actions, text="開始解包", style="Run.TButton", command=self.run_pak)
        run_button.pack(side="left")
        self.pak_run_button = run_button
        self._register_run_controls(run_button)
        ttk.Label(frame, text="支援 NEKOPACK4A；解包及重包不會自動修改檔案內容。", style="Hint.TLabel").pack(anchor="w", pady=(10, 0))
        self._pak_mode_changed()

    def _pak_mode_changed(self) -> None:
        unpack = self.pak_mode.get() == "unpack"
        self.pak_input_label.configure(text="PAK 檔案：" if unpack else "來源資料夾：")
        self.pak_output_label.configure(text="解包資料夾：" if unpack else "輸出 PAK：")
        self.level_spin.configure(state="disabled" if unpack else "normal")
        self.worker_spin.configure(state="disabled" if unpack else "normal")
        self._set_row_button_states(self.pak_input_label, row=0, file_enabled=unpack, folder_enabled=not unpack)
        self._set_row_button_states(self.pak_output_label, row=1, file_enabled=not unpack, folder_enabled=unpack)
        for button in self.start_buttons:
            if str(button) == str(self.pak_run_button):
                button.configure(text="開始解包" if unpack else "開始重包")

    def _pak_pick_input_file(self) -> None:
        value = filedialog.askopenfilename(title="選擇 PAK 封包", filetypes=[("NekoSDK PAK", "*.pak"), ("所有檔案", "*.*")])
        if value:
            self.pak_input.set(value)
            if self.pak_mode.get() == "unpack":
                p = Path(value)
                self.pak_output.set(str(p.with_name(p.stem + "_unpacked")))

    def _pak_pick_input_folder(self) -> None:
        value = filedialog.askdirectory(title="選擇重包來源資料夾")
        if value:
            self.pak_input.set(value)
            if self.pak_mode.get() == "pack":
                p = Path(value)
                self.pak_output.set(str(p.with_name(p.name + "_new.pak")))

    def _pak_pick_output_file(self) -> None:
        value = filedialog.asksaveasfilename(title="設定輸出 PAK", defaultextension=".pak", filetypes=[("NekoSDK PAK", "*.pak"), ("所有檔案", "*.*")])
        if value:
            self.pak_output.set(value)

    def _pak_pick_output_folder(self) -> None:
        value = filedialog.askdirectory(title="選擇解包輸出位置")
        if value:
            self.pak_output.set(value)

    def run_pak(self) -> None:
        try:
            tool = self._validate_tool("pak")
            del tool
            if self.pak_mode.get() == "unpack":
                source = self._require_file(self.pak_input.get(), "PAK 檔案")
                output = self._resolved(self.pak_output.get()) if self.pak_output.get().strip() else None
                if output is None:
                    raise ValueError("請指定解包輸出資料夾。")
                if output == source or output.is_file():
                    raise ValueError("解包輸出必須是資料夾，且不能與來源檔案相同。")
                if not self._confirm_output(output, "解包輸出資料夾"):
                    return
                self._start("pak", ["-u", str(source), str(output)], output_path=output)
            else:
                source = self._require_dir(self.pak_input.get(), "來源資料夾")
                if not self.pak_output.get().strip():
                    raise ValueError("請指定輸出 PAK 檔案。")
                output = self._resolved(self.pak_output.get())
                if output.suffix.lower() != ".pak":
                    raise ValueError("輸出封包副檔名請使用 .pak。")
                if self._is_inside(output, source):
                    raise ValueError("輸出 PAK 不能放在來源資料夾內。")
                level = int(self.pak_level.get())
                workers = int(self.pak_workers.get())
                if not 0 <= level <= 9 or not 1 <= workers <= 16:
                    raise ValueError("壓縮等級需為 0–9，工作執行緒需為 1–16。")
                if not self._confirm_output(output, "輸出 PAK"):
                    return
                env = {"NEKO_PAK_LEVEL": str(level), "NEKO_PAK_WORKERS": str(workers)}
                self._start("pak", ["-p", str(source), str(output)], env_values=env, output_path=output)
        except (ValueError, OSError) as exc:
            messagebox.showerror("無法開始", str(exc), parent=self.root)

    # ---------- Script tab ----------
    def _build_scr_tab(self) -> None:
        frame = self.scr_tab
        self.scr_mode = tk.StringVar(value="export")
        self.scr_source = tk.StringVar()
        self.scr_json = tk.StringVar()
        self.scr_output = tk.StringVar()
        self.scr_encoding = tk.StringVar(value="cp932")
        self.scr_dialogue_encoding = tk.StringVar(value="cp932")
        self.scr_output_auto = True
        self.scr_source.trace_add("write", lambda *_: self._scr_source_changed())

        mode_box = ttk.LabelFrame(frame, text="操作")
        mode_box.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(mode_box, text="匯出：腳本 → JSON", variable=self.scr_mode,
                        value="export", command=self._scr_mode_changed).pack(side="left", padx=8, pady=4)
        ttk.Radiobutton(mode_box, text="匯入：JSON → 腳本", variable=self.scr_mode,
                        value="import", command=self._scr_mode_changed).pack(side="left", padx=8, pady=4)

        paths = ttk.LabelFrame(frame, text="來源與輸出")
        paths.pack(fill="x", pady=(0, 10))
        self.scr_source_label = self._path_row(
            paths, 0, "原始腳本／資料夾：", self.scr_source,
            self._scr_pick_source_file, self._scr_pick_source_folder,
        )
        self.scr_json_label = self._path_row(
            paths, 1, "翻譯 JSON／資料夾：", self.scr_json,
            self._scr_pick_json_file, self._scr_pick_json_folder,
        )
        self.scr_output_label = self._path_row(
            paths, 2, "輸出 JSON／資料夾：", self.scr_output,
            self._scr_pick_output_file, self._scr_pick_output_folder,
        )

        encoding = ttk.LabelFrame(frame, text="腳本字串編碼")
        encoding.pack(fill="x", pady=(0, 10))
        encoding_values = ("cp932", "shift_jis", "utf-8", "utf-8-sig", "gbk", "big5")
        ttk.Label(encoding, text="指令／場景：").grid(row=0, column=0, sticky="w", padx=(4, 5), pady=4)
        self.scr_encoding_box = ttk.Combobox(
            encoding, textvariable=self.scr_encoding, width=14, values=encoding_values
        )
        self.scr_encoding_box.grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(encoding, text="對話／說話者：").grid(row=0, column=2, sticky="w", padx=(18, 5), pady=4)
        self.scr_dialogue_encoding_box = ttk.Combobox(
            encoding, textvariable=self.scr_dialogue_encoding, width=14, values=encoding_values
        )
        self.scr_dialogue_encoding_box.grid(row=0, column=3, sticky="w", pady=4)
        ttk.Label(
            encoding,
            text="兩者預設 CP932。若日文指令搭配 GBK 中文對話，請設為：指令／場景 CP932、對話／說話者 GBK。JSON 固定使用 UTF-8。",
            style="Hint.TLabel",
            wraplength=840,
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=4, pady=(2, 4))

        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        run_button = ttk.Button(actions, text="開始匯出 JSON", style="Run.TButton", command=self.run_scr)
        run_button.pack(side="left")
        self.scr_run_button = run_button
        self._register_run_controls(run_button)
        ttk.Label(
            frame,
            text="資料夾模式會保留子資料夾結構；匯入時，非 ADVSCRIPT2 檔案會原樣複製到輸出資料夾。",
            style="Hint.TLabel", wraplength=850,
        ).pack(anchor="w", pady=(10, 0))
        self._scr_mode_changed()

    def _scr_mode_changed(self) -> None:
        exporting = self.scr_mode.get() == "export"
        self._set_optional_row(self.scr_json_label, row=1, visible=not exporting)
        self.scr_source_label.configure(text="原始腳本／資料夾：")
        self.scr_json_label.configure(text="翻譯 JSON／資料夾：")
        self.scr_output_label.configure(text="輸出 JSON／資料夾：" if exporting else "輸出腳本／資料夾：")
        self.scr_run_button.configure(text="開始匯出 JSON" if exporting else "開始匯入腳本")
        self._scr_apply_default_output()

    def _scr_source_changed(self) -> None:
        # A changed source selects a fresh automatic destination unless the user
        # subsequently chooses or edits a custom output path.
        self.scr_output_auto = True
        self._scr_apply_default_output()

    def _scr_default_output_path(self, source: Path) -> Path:
        exporting = self.scr_mode.get() == "export"
        if source.is_dir():
            suffix = "_json" if exporting else "_imported"
            return source.with_name(source.name + suffix)
        if exporting:
            return source.with_suffix(".json")
        return source.with_name(source.stem + "_imported" + source.suffix)

    def _scr_apply_default_output(self) -> None:
        source_text = self.scr_source.get().strip()
        if not source_text or not self.scr_output_auto:
            return
        source = self._resolved(source_text)
        if source.exists():
            self.scr_output.set(str(self._scr_default_output_path(source)))

    def _scr_pick_source_file(self) -> None:
        value = filedialog.askopenfilename(
            title="選擇 ADVSCRIPT2 腳本／TXT",
            filetypes=[("腳本或 TXT", "*.txt *.bin"), ("所有檔案", "*.*")],
        )
        if value:
            self.scr_source.set(value)
            self.scr_output_auto = True
            self.scr_output.set(str(self._scr_default_output_path(Path(value))))

    def _scr_pick_source_folder(self) -> None:
        value = filedialog.askdirectory(title="選擇腳本解包資料夾")
        if value:
            self.scr_source.set(value)
            self.scr_output_auto = True
            self.scr_output.set(str(self._scr_default_output_path(Path(value))))

    def _scr_pick_json_file(self) -> None:
        value = filedialog.askopenfilename(title="選擇翻譯 JSON", filetypes=[("JSON", "*.json"), ("所有檔案", "*.*")])
        if value:
            self.scr_json.set(value)

    def _scr_pick_json_folder(self) -> None:
        value = filedialog.askdirectory(title="選擇 JSON 翻譯資料夾")
        if value:
            self.scr_json.set(value)

    def _scr_pick_output_file(self) -> None:
        exporting = self.scr_mode.get() == "export"
        current = self.scr_output.get().strip()
        current_path = self._resolved(current) if current else None
        initial_dir = str(current_path.parent) if current_path is not None else None
        initial_file = current_path.name if current_path is not None else None
        value = filedialog.asksaveasfilename(
            title="設定輸出檔案",
            initialdir=initial_dir,
            initialfile=initial_file,
            defaultextension=".json" if exporting else ".txt",
            filetypes=[("JSON", "*.json"), ("所有檔案", "*.*")] if exporting else [("腳本／TXT", "*.txt"), ("所有檔案", "*.*")],
        )
        if value:
            self.scr_output.set(value)
            self.scr_output_auto = False

    def _scr_pick_output_folder(self) -> None:
        value = filedialog.askdirectory(title="選擇輸出資料夾")
        if value:
            self.scr_output.set(value)
            self.scr_output_auto = False

    def run_scr(self) -> None:
        try:
            self._validate_tool("scr")
            command_encoding = self.scr_encoding.get().strip()
            dialogue_encoding = self.scr_dialogue_encoding.get().strip()
            if not command_encoding:
                raise ValueError("請指定指令／場景編碼。")
            if not dialogue_encoding:
                raise ValueError("請指定對話／說話者編碼。")

            source_value = self.scr_source.get()
            if not source_value.strip():
                raise ValueError("請指定原始腳本或資料夾。")
            source = self._resolved(source_value)
            if not source.exists():
                raise ValueError(f"來源路徑不存在：\n{source}")
            if source.is_dir():
                mode = "folder"
            elif source.is_file():
                mode = "file"
            else:
                raise ValueError("來源必須是檔案或資料夾。")

            output_value = self.scr_output.get().strip()
            default_output = self._scr_default_output_path(source)
            if not output_value:
                output = default_output
                self.scr_output.set(str(output))
                self.scr_output_auto = True
            elif self.scr_output_auto:
                current_output = self._resolved(output_value)
                generated_output = self._resolved(str(default_output))
                if current_output == generated_output:
                    output = default_output
                else:
                    # The user manually edited the destination field.
                    output = current_output
                    self.scr_output_auto = False
            else:
                output = self._resolved(output_value)

            # If a folder was picked in the output row during single-file mode,
            # treat it as the destination folder and append the generated filename.
            if mode == "file" and output.exists() and output.is_dir():
                output = output / self._scr_default_output_path(source).name
                self.scr_output.set(str(output))

            expected_output_is_dir = mode == "folder"
            if expected_output_is_dir and output.exists() and not output.is_dir():
                raise ValueError(f"資料夾模式的輸出路徑已被同名檔案佔用：\n{output}")
            if not expected_output_is_dir and output.exists() and output.is_dir():
                raise ValueError(f"單檔模式的輸出路徑必須是檔案：\n{output}")
            if output == source:
                raise ValueError("輸出路徑不能與原始來源相同。")
            if mode == "file":
                output.parent.mkdir(parents=True, exist_ok=True)
            if mode == "folder" and self._is_inside(output, source):
                raise ValueError("輸出資料夾不能放在原始來源資料夾內。")

            if self.scr_mode.get() == "export":
                args = [
                    "--export", str(source), str(output),
                    "--encoding", command_encoding,
                    "--dialogue-encoding", dialogue_encoding,
                ]
            else:
                json_value = self.scr_json.get()
                if not json_value.strip():
                    raise ValueError("匯入時請指定 JSON 檔案或資料夾。")
                json_path = self._resolved(json_value)
                if mode == "folder" and not json_path.is_dir():
                    raise ValueError("來源是資料夾時，翻譯 JSON 也必須指定資料夾。")
                if mode == "file" and not json_path.is_file():
                    raise ValueError("來源是單一檔案時，翻譯 JSON 也必須指定單一檔案。")
                if mode == "folder" and (json_path == source or self._is_inside(json_path, source)):
                    raise ValueError("JSON 資料夾不能放在原始腳本資料夾內。")
                args = [
                    "--import-json", str(source), str(json_path), str(output),
                    "--encoding", command_encoding,
                    "--dialogue-encoding", dialogue_encoding,
                ]

            if not self._confirm_output(output, "腳本工具輸出"):
                return
            self._start("scr", args, output_path=output)
        except (ValueError, OSError) as exc:
            messagebox.showerror("無法開始", str(exc), parent=self.root)

    # ---------- BMP/ALP tab ----------
    def _build_bmp_tab(self) -> None:
        frame = self.bmp_tab
        self.bmp_mode = tk.StringVar(value="批次合併 BMP+ALP")
        self.bmp_source = tk.StringVar()
        self.bmp_alp = tk.StringVar()
        self.bmp_output = tk.StringVar()

        mode_box = ttk.LabelFrame(frame, text="操作模式")
        mode_box.pack(fill="x", pady=(0, 10))
        self.bmp_mode_box = ttk.Combobox(
            mode_box,
            textvariable=self.bmp_mode,
            state="readonly",
            values=(
                "批次合併 BMP+ALP", "單組合併 BMP+ALP", "批次拆分圖片", "單張拆分圖片"
            ),
            width=22,
        )
        self.bmp_mode_box.pack(side="left", padx=6, pady=6)
        self.bmp_mode_box.bind("<<ComboboxSelected>>", self._bmp_mode_changed)
        self.bmp_mode_desc = ttk.Label(mode_box, text="批次合併 BMP + ALP → PNG")
        self.bmp_mode_desc.pack(side="left", padx=10)

        paths = ttk.LabelFrame(frame, text="來源與輸出")
        paths.pack(fill="x", pady=(0, 10))
        self.bmp_source_label = self._path_row(
            paths, 0, "BMP+ALP 資料夾：", self.bmp_source,
            self._bmp_pick_source_file, self._bmp_pick_source_folder,
        )
        self.bmp_alp_label = self._path_row(
            paths, 1, "ALP 檔案：", self.bmp_alp,
            self._bmp_pick_alp_file, self._bmp_pick_alp_folder,
        )
        self.bmp_output_label = self._path_row(
            paths, 2, "PNG 輸出資料夾：", self.bmp_output,
            self._bmp_pick_output_file, self._bmp_pick_output_folder,
        )
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        run_button = ttk.Button(actions, text="開始轉換", style="Run.TButton", command=self.run_bmp)
        run_button.pack(side="left")
        self._register_run_controls(run_button)
        ttk.Label(
            frame,
            text="批次合併會略過缺少對應 ALP 的 BMP；批次拆分支援 PNG、TGA、BMP、JPEG、WebP、TIFF。",
            style="Hint.TLabel", wraplength=850,
        ).pack(anchor="w", pady=(10, 0))
        self._bmp_mode_changed()

    def _bmp_mode_changed(self, _event: object = None) -> None:
        mode_map = {
            "批次合併 BMP+ALP": "extract_folder",
            "單組合併 BMP+ALP": "extract_single",
            "批次拆分圖片": "create_folder",
            "單張拆分圖片": "create_single",
        }
        mode = mode_map.get(self.bmp_mode.get(), "extract_folder")
        descriptions = {
            "extract_folder": ("批次合併 BMP + ALP → PNG", "BMP+ALP 資料夾：", "ALP：不需要", "PNG 輸出資料夾："),
            "extract_single": ("單組合併 BMP + ALP → 圖片", "BMP 檔案：", "ALP 檔案：", "輸出圖片："),
            "create_folder": ("批次拆分圖片 → BMP + ALP", "圖片資料夾：", "ALP：不需要", "BMP+ALP 輸出資料夾："),
            "create_single": ("單張圖片拆分 → BMP + ALP", "來源圖片：", "ALP：自動產生", "輸出 BMP 檔案："),
        }
        desc, source_label, alp_label, output_label = descriptions[mode]
        self.bmp_mode_desc.configure(text=desc)
        self.bmp_source_label.configure(text=source_label)
        self.bmp_alp_label.configure(text=alp_label)
        self.bmp_output_label.configure(text=output_label)
        self._set_optional_row(self.bmp_alp_label, row=1, visible=(mode == "extract_single"))
        single_mode = mode in ("extract_single", "create_single")
        self._set_row_button_states(self.bmp_source_label, row=0, file_enabled=single_mode, folder_enabled=not single_mode)
        self._set_row_button_states(self.bmp_alp_label, row=1, file_enabled=(mode == "extract_single"), folder_enabled=False)
        self._set_row_button_states(self.bmp_output_label, row=2, file_enabled=single_mode, folder_enabled=not single_mode)

    def _bmp_mode_key(self) -> str:
        return {
            "批次合併 BMP+ALP": "extract_folder",
            "單組合併 BMP+ALP": "extract_single",
            "批次拆分圖片": "create_folder",
            "單張拆分圖片": "create_single",
        }.get(self.bmp_mode.get(), "extract_folder")

    def _bmp_pick_source_file(self) -> None:
        mode = self._bmp_mode_key()
        if mode == "extract_single":
            value = filedialog.askopenfilename(title="選擇 BMP 檔案", filetypes=[("BMP", "*.bmp"), ("所有檔案", "*.*")])
            if value:
                self.bmp_source.set(value)
                p = Path(value)
                self.bmp_alp.set(str(p.with_suffix(".alp")))
                self.bmp_output.set(str(p.with_suffix(".png")))
        elif mode == "create_single":
            value = filedialog.askopenfilename(title="選擇來源圖片", filetypes=[("支援的圖片", "*.png *.tga *.bmp *.jpg *.jpeg *.webp *.tif *.tiff"), ("所有檔案", "*.*")])
            if value:
                self.bmp_source.set(value)
                self.bmp_output.set(str(Path(value).with_suffix(".bmp")))

    def _bmp_pick_source_folder(self) -> None:
        value = filedialog.askdirectory(title="選擇來源資料夾")
        if value:
            self.bmp_source.set(value)
            p = Path(value)
            suffix = "_png" if self._bmp_mode_key() == "extract_folder" else "_bmp_alp"
            self.bmp_output.set(str(p.with_name(p.name + suffix)))

    def _bmp_pick_alp_file(self) -> None:
        value = filedialog.askopenfilename(title="選擇 ALP 檔案", filetypes=[("ALP", "*.alp"), ("所有檔案", "*.*")])
        if value:
            self.bmp_alp.set(value)

    def _bmp_pick_alp_folder(self) -> None:
        # ALP is only a single-file field in the current GUI.
        return

    def _bmp_pick_output_file(self) -> None:
        mode = self._bmp_mode_key()
        if mode == "extract_single":
            types = [("PNG", "*.png"), ("TGA", "*.tga"), ("BMP", "*.bmp"), ("所有檔案", "*.*")]
            default = ".png"
        elif mode == "create_single":
            types = [("BMP", "*.bmp"), ("所有檔案", "*.*")]
            default = ".bmp"
        else:
            types = [("所有檔案", "*.*")]
            default = ""
        value = filedialog.asksaveasfilename(title="設定輸出檔案", defaultextension=default, filetypes=types)
        if value:
            self.bmp_output.set(value)

    def _bmp_pick_output_folder(self) -> None:
        value = filedialog.askdirectory(title="選擇輸出資料夾")
        if value:
            self.bmp_output.set(value)

    def run_bmp(self) -> None:
        try:
            self._validate_tool("bmp")
            mode = self._bmp_mode_key()
            if mode == "extract_folder":
                source = self._require_dir(self.bmp_source.get(), "BMP／ALP 來源資料夾")
                if not self.bmp_output.get().strip():
                    raise ValueError("請指定 PNG 輸出資料夾。")
                output = self._resolved(self.bmp_output.get())
                if self._is_inside(output, source):
                    raise ValueError("輸出資料夾不能放在來源資料夾內。")
                args = ["-e", str(source), str(output)]
            elif mode == "extract_single":
                bmp = self._require_file(self.bmp_source.get(), "BMP 檔案")
                alp = self._require_file(self.bmp_alp.get(), "ALP 檔案")
                if not self.bmp_output.get().strip():
                    raise ValueError("請指定輸出圖片路徑。")
                output = self._resolved(self.bmp_output.get())
                if output.suffix.lower() not in {".png", ".tga", ".bmp", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
                    raise ValueError("輸出圖片副檔名不受支援。")
                if output in (bmp, alp):
                    raise ValueError("輸出圖片不能覆蓋來源 BMP 或 ALP。")
                args = ["-e", str(bmp), str(alp), str(output)]
            elif mode == "create_folder":
                source = self._require_dir(self.bmp_source.get(), "圖片來源資料夾")
                if not self.bmp_output.get().strip():
                    raise ValueError("請指定 BMP／ALP 輸出資料夾。")
                output = self._resolved(self.bmp_output.get())
                if self._is_inside(output, source):
                    raise ValueError("輸出資料夾不能放在來源資料夾內。")
                args = ["-c", str(source), str(output)]
            else:  # create_single
                source = self._require_file(self.bmp_source.get(), "來源圖片")
                if not self.bmp_output.get().strip():
                    raise ValueError("請指定 BMP 輸出檔案。")
                bmp_output = self._resolved(self.bmp_output.get())
                if bmp_output.suffix.lower() != ".bmp":
                    raise ValueError("單張拆分的輸出檔案請使用 .bmp 副檔名。")
                alp_output = bmp_output.with_suffix(".alp")
                if source in (bmp_output, alp_output):
                    raise ValueError("輸出檔案不能覆蓋來源圖片。")
                output = bmp_output
                args = ["-c", str(source), str(bmp_output), str(alp_output)]

            outputs_to_confirm = [output]
            if mode == "create_single":
                outputs_to_confirm.append(output.with_suffix(".alp"))
            for item in outputs_to_confirm:
                if not self._confirm_output(item, "圖像工具輸出"):
                    return
            self._start("bmp", args, output_path=output)
        except (ValueError, OSError) as exc:
            messagebox.showerror("無法開始", str(exc), parent=self.root)


def main() -> int:
    root = tk.Tk()
    NekoSDKToolsGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
