"""GUI integration regressions with isolated state and a fully mocked cache core.

Run after installing requirements.txt with
QT_QPA_PLATFORM=offscreen.  These tests never bind ports, edit hosts, read the
user's cache configuration, or start the real cache service.
"""

import copy
import io
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QProgressBar
    import dance_cache as core
    from dance_ui.app import MainWindow
    from dance_ui.controller import CoreController
    from dance_ui.theme import ThemeStore
    QT_READY = True
except ImportError:
    QT_READY = False


@unittest.skipUnless(QT_READY, "Install the Qt UI dependencies to run GUI integration tests.")
class UIIntegrationTests(unittest.TestCase):
    # A closed QMainWindow normally lives until QApplication exits.  Retaining
    # these here also allows its already scheduled informational notices to
    # finish harmlessly while later test cases run their event loops.
    _retired = []

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")
        fonts = Path(__file__).resolve().parents[2] / "verification-fonts"
        if fonts.is_dir():
            for font in fonts.glob("*.ttf"):
                QFontDatabase.addApplicationFont(str(font))
        if "Noto Sans SC" in QFontDatabase.families():
            cls.app.setFont(QFont("Noto Sans SC", 10))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pypydance-ui-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.windows = []
        self.release_events = []
        self.background_threads = []
        self.config = copy.deepcopy(core.DEFAULT_CONFIG)
        self.config.update(
            cache_dir=str(self.root / "cache"), library_dir=str(self.root / "library"),
            cooldown_file=str(self.root / "cooldown.mp4"), cdn_permanent=True,
        )
        self.core_mocks = {}
        guarded = (
            "load_config", "load_mapping", "add_listener", "remove_listener",
            "start_services", "stop_services", "clean_stale_hosts", "hijack_hosts",
            "restore_hosts", "ensure_admin", "get_config", "get_runtime_state",
            "get_queue_snapshot", "get_downloads_snapshot", "get_cooldown_state",
            "emit_stats", "update_config", "clear_cache", "rebuild_mapping",
            "verify_library", "cancel_library_download", "speed_test_ips", "full_scan_ips",
            "cooldown_arm", "cooldown_disarm", "is_running",
        )
        for name in guarded:
            self.core_mocks[name] = self.patches.enter_context(patch.object(
                core, name, side_effect=AssertionError("Unexpected real-core operation: " + name),
            ))

    def wait_until(self, condition, message="Qt event did not arrive", timeout=1500):
        if condition():
            return
        loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
        errors = []

        def check():
            try:
                if condition():
                    loop.quit()
            except BaseException as error:
                errors.append(error)
                loop.quit()

        poll.timeout.connect(check)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start(10)
        deadline.start(timeout)
        loop.exec()
        poll.stop()
        deadline.stop()
        if errors:
            raise errors[0]
        self.assertTrue(condition(), message)

    def allow_core(self, name, function=None, *, result=None):
        mocked = self.core_mocks[name]
        mocked.side_effect = function
        mocked.return_value = result
        return mocked

    def make_window(self, *, demo=True, publish=True, before_controller=None):
        if not demo:
            for name in ("load_config", "load_mapping", "add_listener", "remove_listener", "emit_stats"):
                self.allow_core(name)
            self.allow_core("get_config", lambda: copy.deepcopy(self.config))
            self.allow_core("get_runtime_state", result={"running": False, "phase": "stopped", "warnings": [], "error": None})
            self.allow_core("get_queue_snapshot", result=[])
            self.allow_core("get_downloads_snapshot", result=[])
            self.allow_core("get_cooldown_state", result={"phase": "idle", "available": False})
        if before_controller is not None:
            before_controller()
        store = ThemeStore(self.root / ("appearance-" + str(len(self.windows))))
        controller = CoreController(demo=demo)
        window = MainWindow(controller, store)
        self.windows.append((window, controller))
        window.show()
        QTest.qWait(1)
        if publish:
            controller.publish_initial()
        return window, controller, store

    def tearDown(self):
        for release in self.release_events:
            release.set()
        for thread in self.background_threads:
            thread.join(1)
            self.assertFalse(thread.is_alive(), "A test lock holder did not stop")
        for window, controller in self.windows:
            self.wait_until(lambda: not controller._workers, "A mocked worker did not finish")
            window.appearance_page._finish_edit()
            window._appearance_timer.stop()
            window._download_timer.stop()
            controller._demo_maintenance_timer.stop()
            window._can_close = True
            window.close()
            controller.dispose()
            self._retired.append((window, controller))
        QApplication.processEvents()

    @staticmethod
    def cell_text(widget):
        return " ".join(label.text() for label in widget.findChildren(QLabel))

    def download_row(self, window, filename):
        for row in range(window.download_table.rowCount()):
            item = window.download_table.item(row, 0)
            if item and item.toolTip() == filename:
                return row
        self.fail("Download was not displayed: " + filename)

    def test_all_four_navigation_buttons_open_their_page(self):
        window, controller, _ = self.make_window()
        self.assertEqual([button.text() for button in window.nav_buttons], ["运行", "存储", "设置", "外观"])
        self.assertEqual(window.stack.count(), 4)
        for index, button in enumerate(window.nav_buttons):
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            self.assertEqual(window.stack.currentIndex(), index)
            self.assertEqual(window.nav_group.checkedId(), index)
            self.assertTrue(window.stack.currentWidget().isVisible())
        for mocked in self.core_mocks.values():
            mocked.assert_not_called()

    def test_applying_storage_keeps_the_settings_draft(self):
        window, controller, _ = self.make_window(demo=False)
        original_settings = copy.deepcopy(self.config)
        window.preload_count.setValue(7)
        window.preferred_ip.setText("104.16.0.2")
        window.cooldown_path.setText(str(self.root / "unsaved-draft.mp4"))
        window.cache_limit.setValue(4096)
        received = []

        def update(values):
            received.append(dict(values))
            self.config.update(values)
            return {"changed": list(values), "restart_required": False}

        self.allow_core("update_config", update)
        QTest.mouseClick(window.nav_buttons[1], Qt.MouseButton.LeftButton)
        QTest.mouseClick(window.apply_buttons["storage"], Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not controller.busy("apply_storage") and not window._dirty["storage"])
        self.assertEqual(self.config["max_cache_mb"], 4096)
        self.assertNotIn("preload_count", received[0])
        self.assertEqual(self.config["preload_count"], original_settings["preload_count"])
        self.assertEqual(window.preload_count.value(), 7)
        self.assertEqual(window.preferred_ip.text(), "104.16.0.2")
        self.assertEqual(window.cooldown_path.text(), str(self.root / "unsaved-draft.mp4"))
        self.assertTrue(window._dirty["settings"])
        self.assertTrue(window.apply_buttons["settings"].isEnabled())

    def test_async_config_failure_preserves_draft_and_allows_retry(self):
        window, controller, _ = self.make_window(demo=False)
        window.preferred_ip.setText("an-invalid-draft")
        before = copy.deepcopy(self.config)
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)

        def reject(values):
            entered.set()
            release.wait(1)
            raise ValueError("IP 地址格式无效")

        self.allow_core("update_config", reject)
        QTest.mouseClick(window.nav_buttons[2], Qt.MouseButton.LeftButton)
        QTest.mouseClick(window.apply_buttons["settings"], Qt.MouseButton.LeftButton)
        self.wait_until(entered.is_set)
        self.assertFalse(window.stack.widget(2).isEnabled())
        self.assertIn("保存", window.apply_hints["settings"].text())
        release.set()
        self.wait_until(lambda: not controller.busy("apply_settings"))
        self.assertEqual(window.preferred_ip.text(), "an-invalid-draft")
        self.assertEqual(self.config, before)
        self.assertTrue(window._dirty["settings"])
        self.assertTrue(window.stack.widget(2).isEnabled())
        self.assertTrue(window.apply_buttons["settings"].isEnabled())
        self.assertNotIn("正在", window.apply_hints["settings"].text())
        self.assertIn("失败", window.apply_hints["settings"].text())
        self.assertIn("IP 地址格式无效", window.banner.text())

    def test_saved_cooldown_file_refreshes_the_run_action_and_arms_off_the_gui_thread(self):
        window, controller, _ = self.make_window(demo=False)
        selected = str(self.root / "new-cooldown.mp4")

        def update(values):
            self.config.update(values)
            return {"changed": list(values), "restart_required": False}

        self.allow_core("update_config", update)
        self.allow_core("get_cooldown_state", lambda: {
            "phase": "idle", "available": self.config["cooldown_file"] == selected,
        })
        controller.event.emit("state", {"running": True, "phase": "running", "warnings": []})
        window.navigate(2)
        window.cooldown_path.setText(selected)
        QTest.mouseClick(window.apply_buttons["settings"], Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not controller.busy("apply_settings") and not window._dirty["settings"])
        self.assertEqual(controller.config()["cooldown_file"], selected)
        window.navigate(0)
        self.assertEqual(window.cooldown_button.text(), "启用 CoolDown")
        self.assertTrue(window.cooldown_button.isEnabled())
        self.assertEqual(window.cooldown_file_label.text(), selected)
        threads = []
        self.allow_core("is_running", result=True)
        self.allow_core("cooldown_arm", lambda: threads.append(threading.get_ident()) or True)
        QTest.mouseClick(window.cooldown_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: bool(threads) and not controller.busy("cooldown"))
        self.assertNotEqual(threads[0], threading.get_ident())

    def test_core_listener_delivers_background_event_on_qt_thread(self):
        window, controller, _ = self.make_window(publish=False)
        gui_thread = threading.get_ident()
        observed = []
        original = window.append_log

        def record(message):
            observed.append(threading.get_ident())
            original(message)

        window.append_log = record
        sender = threading.Thread(target=lambda: controller._listener("log", {"msg": "worker-thread-event"}))
        sender.start()
        sender.join(1)
        self.assertFalse(sender.is_alive())
        self.wait_until(lambda: "worker-thread-event" in window.logs.toPlainText())
        self.assertEqual(observed, [gui_thread])

    def test_close_waits_for_a_late_start_and_restores_its_service_before_exiting(self):
        window, controller, _ = self.make_window(demo=False)
        entered, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        runtime = {"running": False, "phase": "stopped", "warnings": [], "error": None}
        stops = []

        def late_start():
            entered.set()
            release.wait(1)
            runtime.update(running=True, phase="running")

        def stop():
            stops.append(runtime["running"])
            runtime.update(running=False, phase="stopped")

        self.allow_core("get_runtime_state", lambda: dict(runtime))
        self.allow_core("stop_services", stop)
        controller._run("start", late_start)
        self.wait_until(entered.is_set)
        window.close()
        self.wait_until(lambda: bool(stops))
        self.assertTrue(window.isVisible())
        self.assertFalse(window._can_close)
        release.set()
        self.wait_until(lambda: not window.isVisible())
        self.assertEqual(stops, [False, True])
        self.assertFalse(runtime["running"])

    def test_initial_load_events_are_replayed_once_after_window_connects(self):
        listeners = []
        warning = "配置读取失败，已使用默认值"
        mapping_log = "索引记录已恢复"

        def prepare():
            self.allow_core("add_listener", listeners.append)

            def load_config():
                payload = {"message": warning, "details": {"recovered": True}}
                listeners[0]("warning", payload)
                # Initialization events must be snapshots, not references to a
                # mutable payload that the core may reuse before UI creation.
                payload["message"] = "changed after notification"
                payload["details"]["recovered"] = False

            self.allow_core("load_config", load_config)
            self.allow_core("load_mapping", lambda: listeners[0]("log", {"msg": mapping_log}))

        window, controller, _ = self.make_window(demo=False, publish=False, before_controller=prepare)
        self.assertEqual(window.logs.toPlainText(), "")
        events = []
        controller.event.connect(lambda kind, data: events.append((kind, data)))
        controller.publish_initial()
        self.wait_until(lambda: mapping_log in window.logs.toPlainText())
        self.assertEqual([kind for kind, _ in events[:2]], ["warning", "log"])
        self.assertEqual(events[0][1]["message"], warning)
        self.assertTrue(events[0][1]["details"]["recovered"])
        self.assertIn(warning, window.banner.text())
        controller.publish_initial()
        self.assertEqual(window.logs.toPlainText().count(warning), 1)
        self.assertEqual(window.logs.toPlainText().count(mapping_log), 1)
        self.core_mocks["load_config"].assert_called_once_with()
        self.core_mocks["load_mapping"].assert_called_once_with()

    def test_unknown_download_size_failure_and_cancellation_are_distinct(self):
        window, controller, _ = self.make_window(publish=False)
        for task in ("active", "failed", "cancelled"):
            controller.event.emit("download_start", {"task_id": task, "filename": task + ".mp4"})
            controller.event.emit("progress", {"task_id": task, "filename": task + ".mp4", "done": 1024 * 1024, "total": 0})
        self.wait_until(lambda: window.download_table.rowCount() == 3)
        row = self.download_row(window, "active.mp4")
        progress_cell = window.download_table.cellWidget(row, 1)
        progress = progress_cell.findChild(QProgressBar)
        self.assertEqual((progress.minimum(), progress.maximum()), (0, 0))
        self.assertIn("大小未知", self.cell_text(progress_cell))
        self.assertNotIn("%", self.cell_text(progress_cell))
        controller.event.emit("download_done", {"task_id": "failed", "filename": "failed.mp4", "ok": False, "error": "远端连接中断"})
        controller.event.emit("download_done", {"task_id": "cancelled", "filename": "cancelled.mp4", "ok": False, "cancelled": True})
        self.wait_until(lambda: "失败" in self.cell_text(window.download_table.cellWidget(self.download_row(window, "failed.mp4"), 3)))
        failed = window.download_table.cellWidget(self.download_row(window, "failed.mp4"), 3)
        cancelled = window.download_table.cellWidget(self.download_row(window, "cancelled.mp4"), 3)
        self.assertEqual(self.cell_text(cancelled), "已取消")
        self.assertEqual(failed.toolTip(), "远端连接中断")
        self.assertIn("[下载失败] failed.mp4", window.logs.toPlainText())
        self.assertNotIn("[下载失败] cancelled.mp4", window.logs.toPlainText())
        self.assertEqual(window.download_count.text(), "1 项进行中")
        QTest.mouseClick(window.clear_history_button, Qt.MouseButton.LeftButton)
        self.assertEqual(window.download_table.rowCount(), 1)
        self.assertEqual(window.download_table.item(0, 0).toolTip(), "active.mp4")

    def test_maintenance_cancel_waits_for_the_worker_completion_event(self):
        window, controller, _ = self.make_window(demo=False)
        self.allow_core("verify_library", result=True)
        cancelled = self.allow_core("cancel_library_download")
        QTest.mouseClick(window.nav_buttons[1], Qt.MouseButton.LeftButton)
        QTest.mouseClick(window.verify_button, Qt.MouseButton.LeftButton)
        self.assertEqual(window._maintenance, "verify")
        self.wait_until(lambda: not controller.busy("verify"))
        controller.event.emit("verify_progress", {"checked": 4, "total": 23, "current": "1234.mp4"})
        QTest.mouseClick(window.cancel_maintenance_button, Qt.MouseButton.LeftButton)
        cancelled.assert_called_once_with()
        self.assertEqual(window._maintenance, "verify")
        self.assertFalse(window.verify_button.isEnabled())
        self.assertFalse(window.cancel_maintenance_button.isEnabled())
        self.assertIn("正在取消", window.maintenance_status.text())
        controller.event.emit("verify_done", {"checked": 4, "total": 23, "cancelled": True, "bad": 0, "failed": 0})
        self.assertIsNone(window._maintenance)
        self.assertTrue(window.verify_button.isEnabled())
        self.assertTrue(window.rebuild_button.isEnabled())
        self.assertIn("已取消", window.maintenance_status.text())
        self.assertEqual(window.maintenance_progress.value(), 4)

    def test_maintenance_waiting_for_lifecycle_lock_keeps_ui_responsive_and_honors_cancel(self):
        window, controller, _ = self.make_window(demo=False)
        lock = self.patches.enter_context(patch.object(core, "_lifecycle_lock", threading.RLock()))
        lock_held, release, entered, started, cancelled_after_start = (threading.Event() for _ in range(5))
        self.release_events.append(release)
        begin_threads, cancel_states, heartbeat = [], [], []

        def hold_lock():
            with lock:
                lock_held.set()
                release.wait(1.5)

        holder = threading.Thread(target=hold_lock)
        self.background_threads.append(holder)
        holder.start()
        self.assertTrue(lock_held.wait(0.5))

        def begin_verify():
            begin_threads.append(threading.get_ident())
            entered.set()
            with core._lifecycle_lock:
                started.set()
            return True

        def cancel():
            cancel_states.append(started.is_set())
            if started.is_set():
                cancelled_after_start.set()

        self.allow_core("verify_library", begin_verify)
        self.allow_core("cancel_library_download", cancel)
        QTest.mouseClick(window.nav_buttons[1], Qt.MouseButton.LeftButton)
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        QTest.mouseClick(window.verify_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: entered.is_set() and bool(heartbeat))
        self.assertFalse(started.is_set(), "Maintenance startup blocked the UI until the lifecycle lock released")
        self.assertNotEqual(begin_threads[0], threading.get_ident())
        QTest.mouseClick(window.cancel_maintenance_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(window.nav_buttons[0], Qt.MouseButton.LeftButton)
        self.assertEqual(window.stack.currentIndex(), 0)
        self.assertEqual(cancel_states, [False])
        self.assertEqual(window._maintenance, "verify")
        release.set()
        self.wait_until(lambda: cancelled_after_start.is_set() and not controller.busy("verify"))
        self.assertEqual(cancel_states, [False, True])
        controller.event.emit("verify_done", {"checked": 0, "total": 0, "cancelled": True})
        self.assertIn("已取消", window.maintenance_status.text())
        self.assertIsNone(window._maintenance)

    def test_demo_maintenance_cancel_cannot_later_report_completed(self):
        window, controller, _ = self.make_window()
        QTest.mouseClick(window.nav_buttons[1], Qt.MouseButton.LeftButton)
        QTest.mouseClick(window.rebuild_button, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(window._maintenance)
        QTest.mouseClick(window.cancel_maintenance_button, Qt.MouseButton.LeftButton)
        self.assertIn("已取消", window.maintenance_status.text())
        QTest.qWait(550)
        self.assertIn("已取消", window.maintenance_status.text())
        self.assertNotIn("维护完成", window.maintenance_status.text())
        self.assertIsNone(window._maintenance)

    def test_demo_from_construction_to_close_never_calls_core_services_or_files(self):
        window, controller, _ = self.make_window()
        QTest.mouseClick(window.service_button, Qt.MouseButton.LeftButton)
        self.assertFalse(window._runtime["running"])
        QTest.mouseClick(window.service_button, Qt.MouseButton.LeftButton)
        self.assertTrue(window._runtime["running"])
        controller.scan()
        controller.clear_cache()
        controller.cooldown()
        controller.cooldown()
        window.close()
        self.wait_until(lambda: not window.isVisible(), "Demo window did not finish its deferred close")
        self.assertTrue(window._can_close)
        for name, mocked in self.core_mocks.items():
            with self.subTest(core_operation=name):
                mocked.assert_not_called()

    def test_demo_smoke_report_checks_four_pages_svg_and_rendered_shipped_background(self):
        from dance_ui.smoke import inspect_demo

        window, _, _ = self.make_window()
        report = inspect_demo(window)
        self.assertEqual(report["status"], "passed", report.get("error"))
        self.assertFalse(report["frozen"])
        self.assertFalse(report["real_service_tested"])
        self.assertFalse(report["vrchat_tested"])
        self.assertEqual(report["theme_name"], "小余12主题")
        self.assertEqual(report["checks"], [
            "demo_mode", "shipped_theme_and_pillow_background",
            "embedded_svg_icons", "four_page_navigation_and_rendering",
        ])
        self.assertEqual(window.stack.currentIndex(), 3)
        self.assertIsNotNone(window.backdrop.image)
        self.assertIsNotNone(window.backdrop._render)
        self.assertFalse(window.backdrop._render.isNull())
        for name, mocked in self.core_mocks.items():
            with self.subTest(core_operation=name):
                mocked.assert_not_called()

    def test_demo_smoke_report_fails_when_shipped_background_cannot_be_decoded(self):
        from dance_ui.smoke import inspect_demo

        window, _, _ = self.make_window()
        window.backdrop.image = None
        report = inspect_demo(window)
        self.assertEqual(report["status"], "failed")
        self.assertIn("Pillow could not decode the shipped background", report["error"])
        self.assertNotIn("shipped_theme_and_pillow_background", report["checks"])
        for name, mocked in self.core_mocks.items():
            with self.subTest(core_operation=name):
                mocked.assert_not_called()

    def test_smoke_report_argument_requires_demo_before_initializing_the_core(self):
        from dance_ui.app import main

        report_path = self.root / "should-not-exist.json"
        error_output = io.StringIO()
        with redirect_stderr(error_output), self.assertRaises(SystemExit) as raised:
            main(["--smoke-test-report", str(report_path)])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--smoke-test-report 必须与 --demo 一起使用", error_output.getvalue())
        self.assertFalse(report_path.exists())
        self.assertEqual(self.windows, [])
        for name, mocked in self.core_mocks.items():
            with self.subTest(core_operation=name):
                mocked.assert_not_called()

    def test_failed_shutdown_keeps_window_open_and_can_be_retried(self):
        window, controller, _ = self.make_window(demo=False)
        stopped = self.allow_core("stop_services", result="hosts 恢复失败")
        window.close()
        self.wait_until(lambda: not controller.busy("shutdown") and not window._closing)
        self.assertTrue(window.isVisible())
        self.assertTrue(window.isEnabled())
        self.assertFalse(window._can_close)
        self.assertIn("hosts 恢复失败", window.banner.text())
        stopped.return_value = None
        window.close()
        self.wait_until(lambda: not window.isVisible())
        self.assertTrue(window._can_close)
        self.assertEqual(stopped.call_count, 2)
        self.core_mocks["start_services"].assert_not_called()

    def test_theme_scaling_and_log_preferences_reach_the_whole_window(self):
        window, controller, store = self.make_window()
        QTest.mouseClick(window.logs_toggle, Qt.MouseButton.LeftButton)
        self.assertTrue(store.preferences["logs_expanded"])
        QTest.mouseClick(window.nav_buttons[3], Qt.MouseButton.LeftButton)
        editor = window.appearance_page
        logs = editor._preference_fields["logs_expanded"]
        self.assertTrue(logs.isChecked())
        old_row_height = window.queue_table.verticalHeader().defaultSectionSize()
        original_field = editor._fields["accent"]
        editor.theme_combo.setCurrentIndex(editor.theme_combo.findData("dark"))
        self.wait_until(lambda: window.backdrop.theme.get("id") == "dark")
        self.assertTrue(store.preferences["logs_expanded"])
        scale = editor._preference_fields["scale"]
        scale.setCurrentIndex(scale.findData(150))
        self.wait_until(lambda: window.queue_table.verticalHeader().defaultSectionSize() > old_row_height)
        self.assertEqual(store.preferences["scale"], 150)
        self.assertIs(editor._fields["accent"], original_field)
        self.assertEqual(editor.scroll.horizontalScrollBar().maximum(), 0)
        logs.setChecked(False)
        self.wait_until(lambda: not window.logs_panel.isVisible())
        self.assertFalse(store.preferences["logs_expanded"])
        QTest.mouseClick(window.nav_buttons[0], Qt.MouseButton.LeftButton)
        self.assertEqual(window.logs_toggle.text(), "查看日志")


if __name__ == "__main__":
    unittest.main()
