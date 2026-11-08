"""Static regression tests for the single-window GUI integration."""
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_PATH = ROOT / "GUI.py"


class MainGUIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = GUI_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(GUI_PATH))
        cls.gui_class = next(
            node for node in cls.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NekoSDKToolsGUI"
        )
        cls.methods = {
            node.name: node for node in cls.gui_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_uif_app_is_imported_into_original_gui(self):
        imported = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "uif_char_helper"
            and any(alias.name == "UIFCharHelperApp" for alias in node.names)
            for node in self.tree.body
        )
        self.assertTrue(imported)

    def test_notebook_tabs_are_in_expected_order(self):
        build_ui = self.methods["_build_ui"]
        tab_names = []
        for node in ast.walk(build_ui):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "add"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "notebook"
            ):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name) and first.value.id == "self":
                text_kw = next((kw.value.value for kw in node.keywords if kw.arg == "text" and isinstance(kw.value, ast.Constant)), None)
                if text_kw:
                    tab_names.append((first.attr, text_kw))

        self.assertEqual(
            tab_names,
            [
                ("pak_tab", "PAK 封包"),
                ("scr_tab", "腳本／JSON"),
                ("bmp_tab", "BMP／ALP 圖像"),
                ("char_tab", "字元管理"),
                ("uif_tab", "UIF 輔助工具"),
            ],
        )

    def test_uif_helper_is_embedded_in_scrollable_tab(self):
        build_ui = self.methods["_build_ui"]
        calls = [
            node.func.attr for node in ast.walk(build_ui)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertIn("_build_uif_tab", calls)

        build_uif = self.methods["_build_uif_tab"]
        source = ast.unparse(build_uif)
        self.assertIn("UIFCharHelperApp", source)
        self.assertIn("self.uif_content", source)
        self.assertIn("self.uif_canvas", source)
        self.assertIn("self.uif_scrollbar", source)
        self.assertIn("_install_uif_mousewheel", source)

    def test_mousewheel_uses_private_bindtag_not_global_bind_all(self):
        source = ast.unparse(self.methods["_install_uif_mousewheel"])
        source += ast.unparse(self.methods["_add_uif_mousewheel_tag"])
        self.assertIn("bind_class", source)
        self.assertNotIn("bind_all", self.source)
        self.assertNotIn("unbind_all", self.source)

    def test_mousewheel_preserves_text_and_treeview_scrolling(self):
        source = ast.unparse(self.methods["_uif_widget_uses_own_mousewheel"])
        self.assertIn("Text", source)
        self.assertIn("Treeview", source)
        self.assertIn("Listbox", source)
        handler = ast.unparse(self.methods["_on_uif_mousewheel"])
        self.assertIn("_uif_widget_uses_own_mousewheel", handler)
        self.assertIn("return None", handler)

    def test_char_manager_is_embedded_in_scrollable_tab(self):
        build_ui = self.methods["_build_ui"]
        calls = [
            node.func.attr for node in ast.walk(build_ui)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertIn("_build_char_tab", calls)

        build_char = self.methods["_build_char_tab"]
        source = ast.unparse(build_char)
        self.assertIn("CharacterManagerFrame", source)
        self.assertIn("self.char_content", source)
        self.assertIn("self.char_canvas", source)
        self.assertIn("self.char_scrollbar", source)
        self.assertIn("_install_char_mousewheel", source)

    def test_char_mousewheel_preserves_internal_widget_scrolling(self):
        source = ast.unparse(self.methods["_install_char_mousewheel"])
        source += ast.unparse(self.methods["_add_char_mousewheel_tag"])
        self.assertIn("bind_class", source)
        self.assertNotIn("bind_all", self.source)

        widget_source = ast.unparse(
            self.methods["_char_widget_uses_own_mousewheel"]
        )
        self.assertIn("Text", widget_source)
        self.assertIn("Treeview", widget_source)
        self.assertIn("Listbox", widget_source)

        handler = ast.unparse(self.methods["_on_char_mousewheel"])
        self.assertIn("_char_widget_uses_own_mousewheel", handler)
        self.assertIn("return None", handler)

    def test_old_second_launcher_is_not_used_by_main_gui(self):
        self.assertNotIn("GUI_with_uif", self.source)
        main_function = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        main_source = ast.unparse(main_function)
        self.assertIn("NekoSDKToolsGUI(root)", main_source)
        self.assertNotIn("NekoSDKToolsGUIWithUIF", main_source)


if __name__ == "__main__":
    unittest.main()
