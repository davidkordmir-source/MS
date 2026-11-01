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
            from PySide6.QtGui import QFont
            from terrain_export.gui import Window
            app = QApplication([])
            app.setStyle('Fusion'); app.setFont(QFont('Segoe UI', 10))
            window = Window()
            window.silent = True
            window.show()
            app.processEvents()
            import tempfile
            import time
            from PySide6.QtCore import QThread, QPointF, Qt
            from PySide6.QtTest import QTest
            with tempfile.TemporaryDirectory() as folder:
                window.load_sample()
                window.output.setText(str(Path(folder) / 'gui-output.shp'))
                window.vector.clear()
                window.aoi_mode.setCurrentIndex(0)
                window.canvas.fit_map()
                window.canvas.begin_area()
                rect = window.canvas.sceneRect()
                for x, y in [(.03, .03), (.97, .03), (.97, .97), (.03, .97)]:
                    point = QPointF(rect.left()+rect.width()*x, rect.top()+rect.height()*y)
                    QTest.mouseClick(window.canvas.viewport(), Qt.LeftButton, pos=window.canvas.mapFromScene(point))
                if not window.canvas.finish_area():
                    raise RuntimeError('Drawing a valid AOI failed')
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
                while window.progress.value() != 100 and time.monotonic() < deadline:
                    app.processEvents(); QThread.msleep(10)
                while not window.canvas.rendering_ready and time.monotonic() < deadline:
                    app.processEvents(); QThread.msleep(10)
                if not window.canvas.rendering_ready:
                    raise RuntimeError('Map renderer did not become ready')
                window.grab().save(str(report.with_suffix('.png')))
                window.map_tabs.setCurrentIndex(1)
                app.processEvents()
                window.render_preview()
                window.grab().save(str(report.with_name(report.stem+'-preview.png')))
            window.close()
            result["gui"] = "single DSM; mouse-drawn AOI; zoomable preview; threaded extraction; one output dataset verified"
            result['renderer'] = window.detail_status.toolTip()
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
        from terrain_export.launcher import main
        report = sys.argv[sys.argv.index('--startup-test')+1] if '--startup-test' in sys.argv else None
        sys.exit(main(report))
