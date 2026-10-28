import sys

if __name__ == '__main__':
    if '--self-test' in sys.argv:
        import json
        import traceback
        from pathlib import Path
        report = Path(sys.argv[sys.argv.index('--self-test') + 1])
        try:
            from terrain_export.selftest import run
            result = run()
            from PySide6.QtWidgets import QApplication
            from terrain_export.gui import Window
            app = QApplication([])
            window = Window()
            window.silent = True
            window.show()
            app.processEvents()
            import tempfile
            import time
            from PySide6.QtCore import QThread
            with tempfile.TemporaryDirectory() as folder:
                window.load_sample()
                window.output.setText(str(Path(folder) / 'gui-output.shp'))
                window.start()
                deadline = time.monotonic() + 45
                while not window.run_button.isEnabled() and time.monotonic() < deadline:
                    app.processEvents()
                    QThread.msleep(10)
                if not window.run_button.isEnabled():
                    window.cancel()
                    window.worker.wait()
                    raise RuntimeError('GUI worker timed out')
                if not window.open_output.isEnabled() or not window.result_path.exists():
                    raise RuntimeError(window.log.toPlainText())
                window.grab().save(str(report.with_suffix('.png')))
            window.close()
            result["gui"] = "preview, threaded extraction and output verified"
            report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        except Exception:
            report.write_text(json.dumps({'status': 'failed', 'error': traceback.format_exc()}, indent=2), encoding='utf-8')
            sys.exit(1)
    elif '--smoke-test' in sys.argv:
        from PySide6.QtWidgets import QApplication
        from terrain_export.gui import Window
        app = QApplication([])
        window = Window()
        window.show()
        app.processEvents()
        window.close()
    else:
        from terrain_export.gui import main
        main()
