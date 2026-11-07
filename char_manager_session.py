#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NekoSDKTools 字元管理工作流程擴充。

提供：
- 掃描時工作區初始空白，不自動載入 uif_config.json。
- mapping_draft.json 自動保存與續作。
- 手動載入既有 UIF 映射，並標示既有／本次新增。
- 匯出只有兩行的 source_characters / target_characters TXT。
- 大型字形預覽；統計欄位維持一般字級。

GUI.py 由本模組提供 CharacterManagerFrame。
底層掃描、報表、CP932 安全檢查仍沿用 char_manager.py。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any

from char_manager import (
    CharacterManagerFrame as BaseCharacterManagerFrame,
    CharacterManagerError,
    can_encode,
    is_safe_source_character,
    load_uif_mapping,
    write_uif_mapping,
)


class CharacterManagerFrame(BaseCharacterManagerFrame):
    """可保存續作並輸出兩行替換字串的字元管理頁面。"""

    def __init__(self, master: tk.Misc, app_dir: Path | None = None, **kwargs: Any) -> None:
        self._pending_scan = False
        self._scan_previous_result: Any = None
        self._resume_draft: dict[str, Any] | None = None
        self._draft_path: Path | None = None
        self._loaded_uif_sources: set[str] = set()
        self._removed_uif_sources: set[str] = set()
        self._uif_was_loaded = False
        super().__init__(master, app_dir=app_dir, **kwargs)
        self._install_workflow_ui()
        self._refresh_resume_button()

    def _install_workflow_ui(self) -> None:
        """保留原本的字表與操作，只增加預覽及工作管理列。"""
        row_widgets: dict[int, tk.Widget] = {}
        for child in self.winfo_children():
            try:
                info = child.grid_info()
                if info:
                    row_widgets[int(info["row"])] = child
            except (tk.TclError, KeyError, ValueError):
                continue

        panes = row_widgets.get(2)
        actions = row_widgets.get(3)
        mapping_box = row_widgets.get(4)
        status_label = row_widgets.get(5)
        if not all((panes, actions, mapping_box, status_label)):
            raise CharacterManagerError("無法辨識原字元管理介面配置，停止載入工作流程擴充。")

        panes.grid_configure(row=3)
        actions.grid_configure(row=4)
        mapping_box.grid_configure(row=6)
        status_label.grid_configure(row=7)
        self.rowconfigure(2, weight=0)
        self.rowconfigure(3, weight=1)
        self.rowconfigure(6, weight=0)

        preview = ttk.Frame(self)
        preview.grid(row=2, column=0, sticky="ew", pady=(0, 7))
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(1, weight=1)

        target_box = ttk.LabelFrame(preview, text="目標中文字形預覽")
        target_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        source_box = ttk.LabelFrame(preview, text="CP932 來源字形預覽")
        source_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        preview_font = ("Microsoft JhengHei UI", 34)
        self.target_preview_var = tk.StringVar(value="尚未選取")
        self.source_preview_var = tk.StringVar(value="尚未選取")
        ttk.Label(
            target_box, textvariable=self.target_preview_var,
            font=preview_font, anchor="center",
        ).pack(fill="both", expand=True, padx=8, pady=(2, 5))
        ttk.Label(
            source_box, textvariable=self.source_preview_var,
            font=preview_font, anchor="center",
        ).pack(fill="both", expand=True, padx=8, pady=(2, 5))

        self.target_tree.bind("<<TreeviewSelect>>", self._update_previews, add="+")
        self.candidate_tree.bind("<<TreeviewSelect>>", self._update_previews, add="+")
        self.mapping_tree.bind("<<TreeviewSelect>>", self._update_previews, add="+")

        # 統計數字、檔案數及 Unicode 欄位不放大；字形由上方預覽區單獨放大。
        ttk.Style(self).configure(
            "Treeview", font=("Microsoft JhengHei UI", 10), rowheight=25
        )
        ttk.Style(self).configure(
            "Treeview.Heading", font=("Microsoft JhengHei UI", 10, "bold")
        )

        workflow = ttk.Frame(self)
        workflow.grid(row=5, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(
            workflow, text="讀取 UIF 映射…", command=self._load_uif_mapping_dialog
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            workflow, text="匯出兩行 TXT…", command=self._export_two_line_mapping
        ).pack(side="left", padx=5)
        ttk.Button(
            workflow, text="保存工作草稿", command=self._save_mapping_draft
        ).pack(side="left", padx=5)
        self.resume_button = ttk.Button(
            workflow, text="繼續上次工作…", command=self._resume_last_work,
            state="disabled",
        )
        self.resume_button.pack(side="right", padx=4)

        self.mapping_tree.configure(
            columns=("target", "source_unicode", "target_unicode", "state")
        )
        self.mapping_tree.heading("target", text="目標字元")
        self.mapping_tree.heading("source_unicode", text="來源 U碼")
        self.mapping_tree.heading("target_unicode", text="目標 U碼")
        self.mapping_tree.heading("state", text="映射狀態")
        self.mapping_tree.column("target", width=95, anchor="center")
        self.mapping_tree.column("source_unicode", width=95, anchor="center")
        self.mapping_tree.column("target_unicode", width=95, anchor="center")
        self.mapping_tree.column("state", width=105, anchor="center")
        self._refresh_mapping_tree()
        self._update_previews()

    def _update_previews(self, _event: Any = None) -> None:
        target = "尚未選取"
        source = "尚未選取"
        try:
            selected = self.target_tree.selection()
            if selected:
                target = chr(int(selected[0][1:]))
        except (ValueError, IndexError):
            pass
        try:
            selected = self.candidate_tree.selection()
            if selected:
                source = chr(int(selected[0][1:]))
        except (ValueError, IndexError):
            pass
        try:
            selected = self.mapping_tree.selection()
            if selected:
                item = self.mapping_tree.item(selected[0])
                source = str(item.get("text", source))
                values = item.get("values", ())
                if values:
                    target = str(values[0])
        except (tk.TclError, IndexError):
            pass
        self.target_preview_var.set(target)
        self.source_preview_var.set(source)

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def _draft_payload(self) -> dict[str, Any]:
        if self.result is None:
            raise CharacterManagerError("尚未掃描，沒有可保存的工作資料。")
        return {
            "version": 1,
            "translation_root": str(self.result.translation_root),
            "source_root": str(self.result.source_root or ""),
            "report_root": str(self.result.report_root),
            "translation_text_encoding": self.encoding_var.get().strip() or "utf-8-sig",
            "command_encoding": self.command_encoding_var.get().strip() or "cp932",
            "dialogue_encoding": self.dialogue_encoding_var.get().strip() or "auto",
            "config_path": str(self.config_path),
            "mapping": [
                {"source": source, "target": target}
                for source, target in self.mapping.items()
            ],
            "loaded_uif_sources": sorted(self._loaded_uif_sources, key=ord),
            "removed_uif_sources": sorted(self._removed_uif_sources, key=ord),
            "uif_was_loaded": self._uif_was_loaded,
        }

    def _save_mapping_draft(self, show_message: bool = True) -> None:
        if self.result is None:
            if show_message:
                messagebox.showwarning(
                    "尚未掃描", "請先讀取翻譯文檔及原始遊戲腳本並產生字表。", parent=self
                )
            return
        path = self.result.report_root / "mapping_draft.json"
        pointer = self.app_dir / "tool" / "char_manager_last_work.json"
        try:
            self._atomic_write_json(path, self._draft_payload())
            self._atomic_write_json(pointer, {"version": 1, "draft_path": str(path)})
            self._draft_path = path
            self._refresh_resume_button()
            self.status_var.set(f"工作草稿已保存：{path}")
            if show_message:
                messagebox.showinfo("保存完成", f"工作草稿已保存。\n\n{path}", parent=self)
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("保存失敗", str(exc), parent=self)

    def _read_last_draft(self) -> tuple[Path, dict[str, Any]]:
        pointer = self.app_dir / "tool" / "char_manager_last_work.json"
        if not pointer.is_file():
            raise CharacterManagerError("找不到上次工作的索引檔。")
        try:
            value = json.loads(pointer.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError("上次工作索引檔版本或格式錯誤。")
            draft_path = Path(value["draft_path"]).expanduser()
            draft = json.loads(draft_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CharacterManagerError(f"無法讀取上次工作草稿：{exc}") from exc
        if not isinstance(draft, dict) or draft.get("version") != 1:
            raise CharacterManagerError("工作草稿版本不支援或格式錯誤。")
        for key in ("translation_root", "source_root", "mapping"):
            if key not in draft:
                raise CharacterManagerError(f"工作草稿缺少欄位：{key}")
        if not isinstance(draft["mapping"], list):
            raise CharacterManagerError("工作草稿的 mapping 必須是陣列。")
        return draft_path, draft

    def _refresh_resume_button(self) -> None:
        if not hasattr(self, "resume_button"):
            return
        pointer = self.app_dir / "tool" / "char_manager_last_work.json"
        available = False
        if pointer.is_file():
            try:
                value = json.loads(pointer.read_text(encoding="utf-8-sig"))
                draft = Path(value.get("draft_path", ""))
                available = draft.is_file()
            except (OSError, ValueError, TypeError, AttributeError):
                available = False
        self.resume_button.configure(state="normal" if available else "disabled")

    def _resume_last_work(self) -> None:
        try:
            draft_path, draft = self._read_last_draft()
            translation = Path(str(draft["translation_root"])).expanduser()
            source = Path(str(draft["source_root"])).expanduser()
            if not translation.is_dir() or not source.is_dir():
                raise CharacterManagerError(
                    "上次工作使用的翻譯文檔或原始腳本資料夾已不存在，無法續作。"
                )
            self.translation_var.set(str(translation))
            self.source_var.set(str(source))
            saved_config = Path(
                str(draft.get("config_path", self.config_path))
            ).expanduser()
            if not saved_config.is_file():
                raise CharacterManagerError(
                    f"上次工作使用的 UIF 設定檔不存在：\n{saved_config}"
                )
            self.config_path = saved_config.resolve()
            self.encoding_var.set(str(draft.get("translation_text_encoding", "utf-8-sig")))
            self.command_encoding_var.set(str(draft.get("command_encoding", "cp932")))
            self.dialogue_encoding_var.set(str(draft.get("dialogue_encoding", "auto")))
            self._draft_path = draft_path
            self._resume_draft = draft
            self._start_scan()
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("無法繼續工作", str(exc), parent=self)

    def _validate_restored_mapping(
        self, draft: dict[str, Any]
    ) -> tuple[dict[str, str], set[str]]:
        if self.result is None:
            raise CharacterManagerError("掃描結果不存在，無法恢復映射。")
        restored: dict[str, str] = {}
        pairs = draft.get("mapping")
        if not isinstance(pairs, list):
            raise CharacterManagerError("工作草稿的映射清單格式錯誤。")
        loaded = draft.get("loaded_uif_sources", [])
        removed = draft.get("removed_uif_sources", [])
        if not isinstance(loaded, list) or any(
            not isinstance(ch, str) or len(ch) != 1 for ch in loaded
        ):
            raise CharacterManagerError("工作草稿的 UIF 已載入清單格式錯誤。")
        if not isinstance(removed, list) or any(
            not isinstance(ch, str) or len(ch) != 1 for ch in removed
        ):
            raise CharacterManagerError("工作草稿的 UIF 移除清單格式錯誤。")
        loaded_sources = set(loaded)
        removed_sources = set(removed)
        if not removed_sources.issubset(loaded_sources):
            raise CharacterManagerError("工作草稿移除的 UIF 映射不在已載入清單內。")

        for index, pair in enumerate(pairs, start=1):
            if not isinstance(pair, dict):
                raise CharacterManagerError(f"工作草稿第 {index} 組映射格式錯誤。")
            source, target = pair.get("source"), pair.get("target")
            if not isinstance(source, str) or len(source) != 1:
                raise CharacterManagerError(f"工作草稿第 {index} 組來源必須是一個字元。")
            if not isinstance(target, str) or len(target) != 1:
                raise CharacterManagerError(f"工作草稿第 {index} 組目標必須是一個字元。")
            if source in restored:
                raise CharacterManagerError(f"工作草稿來源字元重複：{source}")
            if target in restored.values():
                raise CharacterManagerError(f"工作草稿目標字元重複：{target}")
            if not can_encode(source, "cp932") or not is_safe_source_character(source):
                raise CharacterManagerError(f"工作草稿來源不是安全的 CP932 候選：{source}")
            # UIF 既有映射是使用者明確載入的舊資料；續作時保留並顯示，
            # 本次新建映射則必須再次通過最新原文／翻譯占用檢查。
            if source not in loaded_sources:
                if source in self.result.original_characters:
                    raise CharacterManagerError(
                        f"來源字元 {source!r} 已出現在最新原始腳本，不能恢復此新映射。"
                    )
                if source in self.result.stats:
                    raise CharacterManagerError(
                        f"來源字元 {source!r} 已出現在最新翻譯文檔，不能恢復此新映射。"
                    )
            restored[source] = target

        if set(restored) & set(restored.values()):
            raise CharacterManagerError("工作草稿存在來源／目標交叉映射，可能形成替換鏈。")
        self._removed_uif_sources = removed_sources
        self._uif_was_loaded = bool(draft.get("uif_was_loaded", bool(loaded_sources)))
        return restored, loaded_sources

    def _start_scan(self) -> None:
        """掃描時使用不存在的設定路徑，避免自動讀入舊 UIF 映射。"""
        old_worker = self.worker
        old_config = self.config_path

        # 相同資料夾重新掃描時，先把目前映射轉成續作草稿，掃描完成後恢復，
        # 避免使用者手動重掃就失去尚未完成的字形配對。
        if self._resume_draft is None and self.result is not None and self.mapping:
            translation_text = self.translation_var.get().strip().strip('"')
            source_text = self.source_var.get().strip().strip('"')
            try:
                same_translation = Path(translation_text).expanduser().resolve() == self.result.translation_root.resolve()
                same_source = (
                    self.result.source_root is not None
                    and Path(source_text).expanduser().resolve() == self.result.source_root.resolve()
                )
            except (OSError, ValueError):
                same_translation = same_source = False
            if same_translation and same_source:
                self._resume_draft = self._draft_payload()
            else:
                self._save_mapping_draft(show_message=False)

        empty_config = self.app_dir / "tool" / ".neko_char_manager_empty_scan_config.json"
        try:
            self.config_path = empty_config
            super()._start_scan()
        finally:
            self.config_path = old_config
        if self.worker is not None and self.worker is not old_worker and self.worker.is_alive():
            self._pending_scan = True
            self._scan_previous_result = self.result

    def _poll_events(self) -> None:
        # 父類負責接收掃描執行緒事件、更新結果及排定下一次輪詢。
        super()._poll_events()
        if not self._pending_scan:
            return
        try:
            running = str(self.scan_button.cget("state")) == "disabled"
        except tk.TclError:
            running = False
        if running:
            return

        self._pending_scan = False
        if self.result is None or self.result is self._scan_previous_result:
            # 掃描失敗，舊草稿留在磁碟，不以空資料覆蓋。
            self._resume_draft = None
            return

        if self._resume_draft is not None:
            draft = self._resume_draft
            try:
                expected_translation = Path(str(draft["translation_root"])).resolve()
                expected_source = Path(str(draft["source_root"])).resolve()
                if self.result.translation_root.resolve() != expected_translation:
                    raise CharacterManagerError("翻譯文檔路徑與上次工作不一致，拒絕恢復。")
                if self.result.source_root is None or self.result.source_root.resolve() != expected_source:
                    raise CharacterManagerError("原始腳本路徑與上次工作不一致，拒絕恢復。")
                self.mapping, self._loaded_uif_sources = self._validate_restored_mapping(draft)
                self._refresh_all()
                self.status_var.set(
                    f"已恢復上次工作：載入 {len(self.mapping)} 組映射，"
                    f"仍有 {len(self._current_missing_targets())} 個待替換字元。"
                )
            except (OSError, CharacterManagerError) as exc:
                self.mapping = {}
                self._loaded_uif_sources.clear()
                self._removed_uif_sources.clear()
                self._uif_was_loaded = False
                self._refresh_all()
                messagebox.showwarning(
                    "部分工作無法恢復",
                    f"{exc}\n\n已保留最新掃描結果，請檢查後重新建立映射。",
                    parent=self,
                )
            finally:
                self._resume_draft = None
        else:
            self.mapping = {}
            self._loaded_uif_sources.clear()
            self._removed_uif_sources.clear()
            self._uif_was_loaded = False
            self._refresh_all()

        self._save_mapping_draft(show_message=False)

    def _add_pair(self) -> None:
        before = dict(self.mapping)
        super()._add_pair()
        if self.mapping != before:
            for source in set(self.mapping) - set(before):
                self._removed_uif_sources.discard(source)
            self._save_mapping_draft(show_message=False)

    def _remove_pair(self) -> None:
        before = dict(self.mapping)
        super()._remove_pair()
        if self.mapping != before:
            selected_source_removed = set(before) - set(self.mapping)
            self._removed_uif_sources.update(
                source for source in selected_source_removed
                if source in self._loaded_uif_sources
            )
            self._refresh_mapping_tree()
            self._save_mapping_draft(show_message=False)

    def _refresh_mapping_tree(self) -> None:
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        for source, target in self.mapping.items():
            state = "UIF 既有" if source in self._loaded_uif_sources else "本次新增"
            self.mapping_tree.insert(
                "", "end", iid=f"m{ord(source)}", text=source,
                values=(target, f"U+{ord(source):04X}", f"U+{ord(target):04X}", state),
            )

    @staticmethod
    def _merge_mapping(base: dict[str, str], incoming: dict[str, str], label: str) -> dict[str, str]:
        merged = dict(base)
        target_owner = {target: source for source, target in merged.items()}
        for source, target in incoming.items():
            previous = merged.get(source)
            if previous is not None and previous != target:
                raise CharacterManagerError(
                    f"{label}衝突：來源 {source!r} 已對應 {previous!r}，不能再對應 {target!r}。"
                )
            owner = target_owner.get(target)
            if owner is not None and owner != source:
                raise CharacterManagerError(
                    f"{label}衝突：目標 {target!r} 已由來源 {owner!r} 使用。"
                )
            if target in merged and target != source:
                raise CharacterManagerError(
                    f"{label}衝突：目標 {target!r} 同時是另一組來源，會形成替換鏈。"
                )
            merged[source] = target
            target_owner[target] = source
        if set(merged) & set(merged.values()):
            raise CharacterManagerError(f"{label}會形成來源／目標交叉映射，已取消合併。")
        if len(set(merged.values())) != len(merged):
            raise CharacterManagerError(f"{label}存在重複目標字元，已取消合併。")
        return merged

    def _load_uif_mapping_dialog(self) -> None:
        value = filedialog.askopenfilename(
            title="選擇 UIF uif_config.json",
            initialdir=str(self.config_path.parent),
            initialfile=self.config_path.name,
            filetypes=[("UIF 設定檔", "uif_config.json"), ("JSON", "*.json"), ("所有檔案", "*.*")],
            parent=self,
        )
        if not value:
            return
        try:
            if self.result is None:
                raise CharacterManagerError("請先完成翻譯及原始腳本掃描，再載入既有映射。")
            incoming = load_uif_mapping(Path(value))
            if not incoming:
                messagebox.showinfo("沒有映射", "選取的設定檔沒有既有字元映射。", parent=self)
                return
            for source in incoming:
                if not can_encode(source, "cp932") or not is_safe_source_character(source):
                    raise CharacterManagerError(f"UIF 來源不是安全的 CP932 漢字／特殊符號：{source!r}")
            merged = self._merge_mapping(self.mapping, incoming, "UIF 映射")
            self.config_path = Path(value).expanduser().resolve()
            self.mapping = merged
            self._loaded_uif_sources.update(incoming)
            self._removed_uif_sources.difference_update(incoming)
            self._uif_was_loaded = True
            self._refresh_all()
            self._save_mapping_draft(show_message=False)
            self.status_var.set(
                f"已載入並合併 UIF 映射 {len(incoming)} 組；目前工作區共 {len(self.mapping)} 組。"
            )
            messagebox.showinfo(
                "載入完成", f"已載入 {len(incoming)} 組既有映射。\n工作區目前共 {len(self.mapping)} 組。",
                parent=self,
            )
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("載入 UIF 映射失敗", str(exc), parent=self)

    def _export_two_line_mapping(self) -> None:
        if not self.mapping:
            messagebox.showwarning("沒有映射", "目前沒有可匯出的替換映射。", parent=self)
            return
        value = filedialog.asksaveasfilename(
            title="匯出兩行替換字串",
            initialdir=str(self.result.report_root if self.result else self.app_dir),
            initialfile="replacement_mapping.txt",
            defaultextension=".txt",
            filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")],
            parent=self,
        )
        if not value:
            return
        sources = "".join(self.mapping.keys())
        targets = "".join(self.mapping.values())
        if len(sources) != len(targets):
            messagebox.showerror("映射錯誤", "來源及目標字元數量不一致，取消匯出。", parent=self)
            return
        try:
            path = Path(value)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(sources + "\n" + targets + "\n")
            self.status_var.set(f"兩行替換字串已匯出：{path}")
            messagebox.showinfo(
                "匯出完成",
                "已輸出兩行字串：\n第一行 source_characters\n第二行 target_characters",
                parent=self,
            )
        except OSError as exc:
            messagebox.showerror("匯出失敗", str(exc), parent=self)

    def _apply_mapping(self) -> None:
        if self.result is None:
            messagebox.showwarning("尚未掃描", "請先掃描翻譯及原始遊戲腳本。", parent=self)
            return
        if not self.result.source_scan_complete or not self.result.translation_scan_complete:
            messagebox.showerror("掃描不完整", "原始腳本或翻譯文檔掃描不完整，拒絕套用替換映射。", parent=self)
            return
        if not self.mapping:
            messagebox.showwarning("沒有映射", "目前沒有可套用的替換映射。", parent=self)
            return

        try:
            # 即使使用者沒有手動載入 UIF，也在「套用」當下讀取現有資料，
            # 避免把尚未載入的既有映射整批覆蓋掉。
            existing = load_uif_mapping(self.config_path)
            if self._uif_was_loaded:
                existing = {
                    source: target for source, target in existing.items()
                    if source not in self._removed_uif_sources
                }
            merged = self._merge_mapping(existing, self.mapping, "目前工作與 UIF 設定")

            new_sources = set(self.mapping) - set(existing)
            for source in new_sources:
                if source in self.result.original_characters:
                    raise CharacterManagerError(f"新來源字元 {source!r} 已出現在原始遊戲文字。")
                if source in self.result.stats:
                    raise CharacterManagerError(f"新來源字元 {source!r} 已出現在翻譯文檔。")
                if source in self.excluded_sources:
                    raise CharacterManagerError(f"新來源字元 {source!r} 已列入人工排除清單。")
                if not can_encode(source, "cp932") or not is_safe_source_character(source):
                    raise CharacterManagerError(f"新來源字元 {source!r} 不是安全的 CP932 候選。")

            if not messagebox.askyesno(
                "確認更新 UIF 設定",
                f"即將合併 {len(new_sources)} 組新映射，\n"
                f"保留既有 {len(existing)} 組映射。\n\n"
                f"設定檔：\n{self.config_path}\n\n"
                "原設定會先備份成 .bak。是否繼續？",
                parent=self,
            ):
                return

            write_uif_mapping(self.config_path, merged)
            self.mapping = merged
            self._loaded_uif_sources = set(merged)
            self._removed_uif_sources.clear()
            self._uif_was_loaded = True
            settings = self._load_settings_for_save()
            settings["excluded_source_characters"] = sorted(self.excluded_sources, key=ord)
            settings["text_encoding"] = self.encoding_var.get().strip() or "utf-8-sig"
            self._save_settings(settings)
            self._refresh_all()
            self._save_mapping_draft(show_message=False)
            self.status_var.set(f"已更新 UIF 映射，共 {len(merged)} 組。")
            messagebox.showinfo(
                "更新完成", "uif_config.json 已更新並建立備份。\n請重新啟動遊戲，讓 UIF 重新載入設定。",
                parent=self,
            )
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("更新失敗", str(exc), parent=self)

    def _load_settings_for_save(self) -> dict[str, Any]:
        from char_manager import load_manager_settings
        return load_manager_settings(self.settings_path)

    def _save_settings(self, settings: dict[str, Any]) -> None:
        from char_manager import save_manager_settings
        save_manager_settings(self.settings_path, settings)
