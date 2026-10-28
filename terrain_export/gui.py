import sys
import os
import logging
import traceback
import tempfile
from pathlib import Path
from threading import Event
from logging.handlers import RotatingFileHandler
import numpy as np
import fiona
import rasterio
from rasterio.enums import Resampling
from PySide6.QtCore import QThread, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QImage, QPixmap, QPainter, QPen, QColor, QPainterPath
from PySide6.QtWidgets import (QApplication, QWidget, QFormLayout, QHBoxLayout, QVBoxLayout,
    QLineEdit, QPushButton, QFileDialog, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QTextEdit, QLabel, QMessageBox, QFrame, QScrollArea, QProgressBar, QSplitter, QSizePolicy)
from terrain_export.engine import Options, extract, CancelledError
from terrain_export.surface import load_surface, project
from shapely.geometry import shape

STYLE = '''
QWidget {font-family: 'Segoe UI', Tahoma; font-size: 13px; color: #18364a;}
QWidget#shell {background: #eef3f6;}
QFrame#card {background: white; border: 1px solid #dce6ec; border-radius: 14px;}
QFrame#header {background: #102c40; border-radius: 14px;}
QLabel#brand {color: #f5fbff; font-size: 26px; font-weight: 700;}
QLabel#subtitle {color: #a5c9d8; font-size: 13px;}
QLabel#section {font-size: 16px; font-weight: 700; color: #123b4b;}
QLabel#muted {color: #587486; font-size: 12px;}
QLabel#status {font-weight: 600; color: #087e78; padding: 8px;}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {background: #f7fafc; border: 1px solid #cbdbe5; border-radius: 7px; padding: 7px; min-height: 21px;}
QLineEdit:focus, QDoubleSpinBox:focus {border: 1px solid #079c92;}
QPushButton {background: #e8f0f4; border: none; border-radius: 7px; padding: 10px 14px; font-weight: 600;}
QPushButton:hover {background: #d5e6ed;}
QPushButton#primary {background: #087f79; color: white; min-height: 28px; font-size: 15px;}
QPushButton#primary:hover {background: #076962;}
QPushButton:disabled {background: #e2e8ec; color: #91a3ac;}
QTextEdit {background: #f6f9fb; border: 1px solid #dce6ec; border-radius: 8px; padding: 8px; font-family: Consolas, Tahoma; font-size: 12px;}
QProgressBar {border: none; background: #e0eded; border-radius: 4px; height: 7px;}
QProgressBar::chunk {background: #09a795; border-radius: 4px;}
QScrollArea {border: none; background: transparent;}
QToolTip {background: #102c40; color: white; padding: 6px;}
'''


def logger():
    log = logging.getLogger('terrain')
    if not log.handlers:
        try:
            folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'TerrainPolygon' / 'logs'
            folder.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(folder / 'application.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
        except OSError:
            handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    return log


def friendly_error(exc):
    text = str(exc)
    known = {
        'CRS override conflicts': 'CRS فایل از قبل تعریف شده؛ جایگزین‌کردن آن مختصات را جابه‌جا می‌کند. داده را با CRS صحیح بازپروژکت کنید.',
        'Vertical units': 'واحد عمودی داده مشخص نیست؛ متر یا فوت بودن داده خود را انتخاب کنید.',
        'DSM file': 'در این حالت فایل DSM هم باید انتخاب شود.',
        'No valid raster cells': 'در این محدوده هیچ پیکسل معتبری مطابق شرط ارتفاع پیدا نشد. بازه ارتفاع و محدوده را بررسی کنید.',
        'do not overlap': 'محدوده ورودی و رستر هم‌پوشانی ندارند. سیستم مختصات و محل داده‌ها را بررسی کنید.',
        'defined CRS': 'سیستم مختصات شیپ فایل مشخص نیست. کد EPSG صحیح آن را در بخش ورودی وارد کنید؛ حدس نزنید.',
        'Raster CRS': 'رستر سیستم مختصات ندارد. ابتدا CRS صحیح را در نرم‌افزار GIS به فایل معرفی کنید.',
        'Minimum elevation': 'حد پایین ارتفاع باید کوچک‌تر یا مساوی حد بالا باشد.',
        'memory limit': 'محدوده بیش از ظرفیت پردازش این نسخه است (۴۰ میلیون سلول). محدوده را کوچک‌تر یا رستر را کم‌حجم کنید.',
        'existing': 'نام خروجی قبلاً استفاده شده است. یک نام جدید انتخاب کنید.',
        'unavailable': 'باند انتخابی در رستر وجود ندارد.',
        'invalid geometry': 'لایه خالی است یا هندسه قابل پردازش ندارد.',
        'mixed/line': 'لایه باید فقط نقطه یا فقط پلیگون باشد؛ لایه خطی پشتیبانی نمی‌شود.',
        'does not exist': 'فایل ورودی پیدا نشد؛ مسیر و فایل‌های همراه را بررسی کنید.',
    }
    if isinstance(exc, PermissionError):
        return 'اجازه نوشتن وجود ندارد یا فایل در برنامه دیگری باز است. پوشه خروجی را تغییر دهید و فایل را ببندید.'
    for key, message in known.items():
        if key in text:
            return message
    return 'پردازش انجام نشد. جزئیات فنی در گزارش پایین و فایل گزارش ثبت شد.\n' + text


class FileEdit(QLineEdit):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setLayoutDirection(Qt.LeftToRight)
        self.setPlaceholderText('انتخاب فایل یا کشیدن و رها کردن آن…')
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.setText(urls[0].toLocalFile())
            self.editingFinished.emit()
            event.acceptProposedAction()


class Worker(QThread):
    message = Signal(str)
    result = Signal(bool, str)
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options
        self.cancelled = Event()
    def run(self):
        try:
            count = extract(self.options, self.message.emit, self.cancelled.is_set)
            self.result.emit(True, f'{count:,} پلیگون با آمار ارتفاع ذخیره شد.')
        except CancelledError:
            self.result.emit(False, 'پردازش لغو شد؛ خروجی ذخیره نشد.')
        except Exception as exc:
            logger().error('Processing failed: %s\n%s', exc, traceback.format_exc())
            self.message.emit(traceback.format_exc())
            self.result.emit(False, friendly_error(exc))


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName('shell')
        self.setStyleSheet(STYLE)
        self.setWindowTitle('Terrain Studio 0.3.0 | استخراج پلیگون ارتفاعی')
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1220, 850)
        self.setMinimumSize(980, 700)
        self.worker = None
        self.silent = False
        self.sample = None
        self.result_path = None
        self.preview_geometry = None
        self.preview_affine = None
        self.preview_crs = None
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        header = QFrame(); header.setObjectName('header')
        hl = QVBoxLayout(header); hl.setContentsMargins(22, 16, 22, 16)
        hl.addWidget(self.label('Terrain Studio  /  استودیو ارتفاع', 'brand'))
        hl.addWidget(self.label('۰.۳.۰    •    پردازش محلی و آفلاین    •    خروجی پلیگون با آمار ارتفاع', 'subtitle'))
        root.addWidget(header)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumWidth(440)
        self.controls = QWidget(); formroot = QVBoxLayout(self.controls); formroot.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.controls); splitter.addWidget(scroll)
        c, l = self.card('۱  /  محدوده و داده ارتفاعی'); formroot.addWidget(c)
        form = QFormLayout(); form.setSpacing(10); l.addLayout(form)
        demo = QPushButton('آزمایش سریع با داده نمونه'); demo.clicked.connect(self.load_sample); l.addWidget(demo)
        self.vector = self.file_row(form, 'محدوده', 'Shapefile (*.shp)')
        self.vector_info = self.label('نقطه یا پلیگون انتخاب کنید؛ فایل‌های همراه را کنار SHP نگه دارید.', 'muted'); l.addWidget(self.vector_info)
        self.radius = self.number(100, .01, 1_000_000); form.addRow('بافر نقاط (متر)', self.radius)
        self.crs_override = QLineEdit(); self.crs_override.setPlaceholderText('فقط برای لایه بدون CRS؛ برای جابه‌جایی مختصات استفاده نکنید')
        self.crs_override.setLayoutDirection(Qt.LeftToRight); form.addRow('CRS جایگزین', self.crs_override)
        self.raster = self.file_row(form, 'فایل DEM', 'Raster (*.tif *.tiff *.img *.asc);;All files (*)')
        self.band = QSpinBox(); self.band.setRange(1, 999); form.addRow('باند DEM', self.band)
        self.dsm = self.file_row(form, 'فایل DSM', 'Raster (*.tif *.tiff *.img *.asc);;All files (*)')
        self.dsm_band = QSpinBox(); self.dsm_band.setRange(1, 999); form.addRow('باند DSM', self.dsm_band)
        self.raster_info = self.label('اندازه، مختصات و نمونه ارتفاع پس از انتخاب فایل نمایش داده می‌شود.', 'muted'); l.addWidget(self.raster_info)
        c, l = self.card('۲  /  شرط ارتفاع و خروجی'); formroot.addWidget(c)
        form = QFormLayout(); form.setSpacing(10); l.addLayout(form)
        self.height_kind = QComboBox(); self.height_kind.addItems(['مبنای ارتفاع را انتخاب کنید…', 'مقدار ارتفاع DEM', 'مقدار ارتفاع DSM', 'ارتفاع از زمین: DSM − DEM'])
        form.addRow('منبع شرط ارتفاع', self.height_kind)
        self.unit = QComboBox(); self.unit.addItems(['واحد داده فاقد متادیتا را انتخاب کنید…', 'متر', 'فوت'])
        form.addRow('واحد ارتفاع نامشخص', self.unit)
        self.refresh = QPushButton('بررسی محدوده و پیش‌نمایش واقعی'); self.refresh.clicked.connect(self.inspect_raster); l.addWidget(self.refresh)
        self.mode = QComboBox(); self.mode.addItems(['بین دو ارتفاع • شامل مرزها', 'بیشتر از حد پایین', 'کمتر از حد بالا'])
        form.addRow('نوع شرط', self.mode)
        self.minimum = self.number(30); self.maximum = self.number(100)
        form.addRow('حد پایین', self.minimum); form.addRow('حد بالا', self.maximum)
        self.output = self.file_row(form, 'فایل خروجی', 'Shapefile (*.shp)', True)
        l.addWidget(self.label('آستانه‌ها به متر هستند. در حالت اختلاف، DEM و DSM باید مرجع عمودی یکسان داشته باشند. مختصات خروجی عین CRS پلیگون ورودی است.', 'muted'))
        c, l = self.card('۳  /  تنظیمات پردازش'); formroot.addWidget(c)
        form = QFormLayout(); l.addLayout(form)
        self.workers = QSpinBox(); self.workers.setRange(1, min(256, os.cpu_count() or 1)); self.workers.setValue(min(32, os.cpu_count() or 1))
        form.addRow('رشته‌های CPU', self.workers)
        self.gpu = QCheckBox('CUDA اختیاری — در این نصاب موجود نیست'); self.gpu.setEnabled(False); form.addRow(self.gpu)
        formroot.addStretch()
        right = QWidget(); rightlayout = QVBoxLayout(right); rightlayout.setContentsMargins(10, 0, 0, 0)
        splitter.addWidget(right); splitter.setSizes([520, 650])
        c, l = self.card('پیش‌نمایش ارتفاع'); rightlayout.addWidget(c, 3)
        self.preview = QLabel('DEM/DSM را انتخاب کنید\n\nپیش‌نمایش نمونه‌برداری‌شده از رستر اینجا نمایش داده می‌شود.')
        self.preview.setAlignment(Qt.AlignCenter); self.preview.setMinimumSize(300, 150)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.preview.setStyleSheet('background: #102c40; color: #95bbca; border-radius: 10px;')
        l.addWidget(self.preview, 1)
        self.preview_note = self.label('مرز زرد: پلیگون شما | فیروزه‌ای: ارتفاع‌های منطبق\nپیش‌نمایش از محدوده خودتان است؛ مرز خروجی دقیقاً به پلیگون برش می‌خورد.', 'muted'); self.preview_note.setMinimumHeight(48); l.addWidget(self.preview_note)
        c, l = self.card('وضعیت و گزارش'); rightlayout.addWidget(c, 2)
        self.status = self.label('آماده • فایل‌ها را انتخاب کنید', 'status'); l.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0); self.progress.setTextVisible(False); l.addWidget(self.progress)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setLayoutDirection(Qt.LeftToRight); l.addWidget(self.log, 1)
        actions = QHBoxLayout(); l.addLayout(actions)
        self.open_output = QPushButton('پوشه خروجی'); self.open_output.setEnabled(False); self.open_output.clicked.connect(self.open_folder); actions.addWidget(self.open_output)
        log_button = QPushButton('ذخیره گزارش'); log_button.clicked.connect(self.save_log); actions.addWidget(log_button)
        bottom = QHBoxLayout(); root.addLayout(bottom)
        self.run_button = QPushButton('شروع استخراج پلیگون  ←'); self.run_button.setObjectName('primary'); self.run_button.clicked.connect(self.start); bottom.addWidget(self.run_button, 1)
        self.cancel_button = QPushButton('لغو پردازش'); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel); bottom.addWidget(self.cancel_button)
        self.vector.editingFinished.connect(self.inspect_vector)
        self.raster.editingFinished.connect(self.inspect_raster)
        self.band.valueChanged.connect(self.inspect_raster)
        self.dsm.editingFinished.connect(self.inspect_raster)
        self.dsm_band.valueChanged.connect(self.inspect_raster)
        self.radius.valueChanged.connect(self.inspect_raster)
        self.height_kind.currentIndexChanged.connect(self.inspect_raster)
        self.unit.currentIndexChanged.connect(self.inspect_raster)
        self.crs_override.editingFinished.connect(self.inspect_raster)
        self.mode.currentIndexChanged.connect(self.update_limits)
        self.minimum.valueChanged.connect(self.render_preview); self.maximum.valueChanged.connect(self.render_preview)
    def label(self, text, name):
        label = QLabel(text); label.setObjectName(name); label.setWordWrap(True); return label
    def card(self, title):
        frame = QFrame(); frame.setObjectName('card'); layout = QVBoxLayout(frame); layout.setContentsMargins(18, 16, 18, 16); layout.setSpacing(12)
        layout.addWidget(self.label(title, 'section')); return frame, layout
    def number(self, value, low=-1_000_000, high=1_000_000):
        box = QDoubleSpinBox(); box.setRange(low, high); box.setDecimals(2); box.setValue(value); box.setGroupSeparatorShown(True); return box
    def file_row(self, form, title, pattern, save=False):
        row = QHBoxLayout(); edit = FileEdit(); button = QPushButton('انتخاب')
        def choose():
            dialog = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
            name, _ = dialog(self, title, edit.text(), pattern)
            if name:
                edit.setText(name if not save or name.lower().endswith('.shp') else name + '.shp')
                edit.editingFinished.emit()
        button.clicked.connect(choose); row.addWidget(edit, 1); row.addWidget(button); form.addRow(title, row); return edit
    def inspect_vector(self):
        try:
            with fiona.open(self.vector.text().strip()) as src:
                kind = src.schema['geometry']; crs = src.crs.to_string() if src.crs else 'تعریف نشده'
                self.vector_info.setText(f'{kind}  •  {len(src):,} عارضه  •  {crs}')
                self.radius.setEnabled('Point' in kind)
                self.crs_override.setEnabled(not bool(src.crs))
                if src.crs: self.crs_override.clear()
            if not self.output.text():
                self.output.setText(str(Path(self.vector.text()).with_name(Path(self.vector.text()).stem + '_polygons.shp')))
            if hasattr(self, 'height_kind'): self.inspect_raster()
        except Exception as exc:
            self.vector_info.setText('خواندن لایه ممکن نشد؛ مسیر و فایل‌های جانبی را بررسی کنید.'); self.log.append(str(exc))
    def options(self):
        return Options(vector=self.vector.text().strip(), raster=self.raster.text().strip(), output=self.output.text().strip(),
            mode=['between', 'above', 'below'][self.mode.currentIndex()], minimum=self.minimum.value(), maximum=self.maximum.value(),
            radius=self.radius.value(), workers=self.workers.value(), band=self.band.value(), vector_crs=self.crs_override.text().strip(),
            dsm=self.dsm.text().strip(), dsm_band=self.dsm_band.value(),
            height_kind=['', 'dem', 'dsm', 'relative'][self.height_kind.currentIndex()],
            unit_scale=[None, 1., .3048][self.unit.currentIndex()])
    def inspect_raster(self):
        if not hasattr(self, 'height_kind'): return
        if not self.vector.text() or self.height_kind.currentIndex() == 0:
            self.preview.clear(); self.preview.setText('پلیگون و مبنای ارتفاع را انتخاب کنید.'); return
        try:
            o = self.options()
            self.sample, self.preview_affine, self.preview_crs, original, crs, self.preview_geometry, metadata = load_surface(o, max_side=600)
            from pyproj import CRS
            center = project(original, crs, CRS.from_epsg(4326)).representative_point()
            self.raster_info.setText(f"منبع: {metadata['reference']}  •  واحد خروجی: {metadata['z_unit']}\nCRS خروجی: {metadata['output_crs']}\nمرکز محدوده: طول {center.x:.6f} / عرض {center.y:.6f}")
            self.render_preview()
        except Exception as exc:
            self.sample = None; self.preview.clear(); self.preview.setText(friendly_error(exc)); self.raster_info.setText(str(exc))
    def load_sample(self):
        folder = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'examples'
        self.vector.setText(str(folder / 'demo_area.shp')); self.inspect_vector()
        self.raster.setText(str(folder / 'demo_dem.tif'))
        self.dsm.setText(str(folder / 'demo_dsm.tif'))
        self.unit.setCurrentIndex(1); self.height_kind.setCurrentIndex(3); self.inspect_raster()
        self.output.setText(str(Path(tempfile.gettempdir()) / 'TerrainStudio-demo' / 'result.shp'))
        self.mode.setCurrentIndex(1); self.minimum.setValue(30); self.maximum.setValue(100)
        self.status.setText('نمونه مصنوعی: DEM + DSM • اختلاف بالای ۳۰ متر • شروع استخراج را بزنید.')
    def update_limits(self):
        self.minimum.setEnabled(self.mode.currentIndex() != 2); self.maximum.setEnabled(self.mode.currentIndex() != 1); self.render_preview()
    def render_preview(self):
        if self.sample is None: return
        z = self.sample; valid = np.isfinite(z)
        if not valid.any(): return
        low, high = np.percentile(z[valid], [2, 98]); norm = np.clip((np.nan_to_num(z, nan=low) - low) / max(high-low, .001), 0, 1)
        rgb = np.stack([35+norm*173, 61+norm*162, 73+norm*130], axis=-1).astype('uint8')
        selected = valid.copy(); mode = self.mode.currentIndex()
        if mode in (0, 1): selected &= z >= self.minimum.value() if mode == 0 else z > self.minimum.value()
        if mode in (0, 2): selected &= z <= self.maximum.value() if mode == 0 else z < self.maximum.value()
        rgb[selected] = (rgb[selected] * .35 + np.array([8, 205, 177]) * .65).astype('uint8'); rgb[~valid] = [16, 44, 64]
        rgb = np.ascontiguousarray(rgb); h, w = rgb.shape[:2]
        pixmap = QPixmap.fromImage(QImage(rgb.data, w, h, w*3, QImage.Format_RGB888).copy())
        if self.preview_geometry is not None:
            painter = QPainter(pixmap); painter.setPen(QPen(QColor('#ffe59b'), 2))
            inverse = ~self.preview_affine
            polygons = [self.preview_geometry] if self.preview_geometry.geom_type == 'Polygon' else list(self.preview_geometry.geoms)
            for polygon in polygons:
                if polygon.geom_type != 'Polygon': continue
                for ring in [polygon.exterior, *polygon.interiors]:
                    path = QPainterPath()
                    for i, (x, y) in enumerate(ring.coords):
                        col, row = inverse * (x, y)
                        if i == 0: path.moveTo(col, row)
                        else: path.lineTo(col, row)
                    path.closeSubpath(); painter.drawPath(path)
            painter.end()
        self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def start(self):
        o = self.options()
        if not o.height_kind:
            QMessageBox.warning(self, 'مبنای ارتفاع', 'انتخاب کنید شرط روی DEM، DSM یا اختلاف DSM−DEM اعمال شود.'); return
        if not all((o.vector, o.raster, o.output)):
            QMessageBox.warning(self, 'ورودی ناقص', 'محدوده، رستر و مسیر خروجی را انتخاب کنید.'); return
        if o.mode == 'between' and o.minimum > o.maximum:
            QMessageBox.warning(self, 'بازه نامعتبر', 'حد پایین نباید از حد بالا بیشتر باشد.'); return
        if not o.output.lower().endswith('.shp'): o.output += '.shp'
        original = Path(o.output); index = 1
        while any(Path(o.output).with_suffix(s).exists() for s in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.json')):
            o.output = str(original.with_name(f'{original.stem}_{index}.shp')); index += 1
        self.output.setText(o.output); self.result_path = Path(o.output)
        self.controls.setEnabled(False); self.run_button.setEnabled(False); self.cancel_button.setEnabled(True); self.open_output.setEnabled(False)
        self.progress.setRange(0, 0); self.status.setText('در حال پردازش…'); self.log.clear(); self.log.append('Output: ' + o.output)
        self.worker = Worker(o, self); self.worker.message.connect(self.show_progress); self.worker.result.connect(self.done); self.worker.finished.connect(self.finished); self.worker.start()
    def show_progress(self, message):
        self.log.append(message)
        if message.startswith('Writing polygons:'): self.status.setText('در حال ذخیره نواحی • ' + message.split(':', 1)[1])
    def done(self, success, message):
        self.progress.setRange(0, 100); self.progress.setValue(100 if success else 0); self.status.setText(message); self.log.append(message); self.open_output.setEnabled(success)
        if not success and 'لغو' not in message and not self.silent:
            dialog = QMessageBox(QMessageBox.Warning, 'پردازش انجام نشد', message, parent=self)
            dialog.setDetailedText(self.log.toPlainText()); dialog.exec()
    def finished(self):
        worker = self.worker
        self.worker = None
        if worker: worker.deleteLater()
        self.controls.setEnabled(True); self.update_limits(); self.run_button.setEnabled(True); self.cancel_button.setEnabled(False)
    def cancel(self):
        if self.worker: self.worker.cancelled.set(); self.cancel_button.setEnabled(False); self.status.setText('درخواست لغو ثبت شد؛ منتظر پایان مرحله جاری…')
    def open_folder(self):
        if self.result_path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_path.parent)))
    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, 'ذخیره گزارش', 'terrain-report.txt', 'Text (*.txt)')
        if path:
            try: Path(path).write_text(self.log.toPlainText(), encoding='utf-8')
            except OSError as exc: QMessageBox.warning(self, 'خطا', str(exc))
    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.cancel(); QMessageBox.information(self, 'پردازش فعال', 'لغو درخواست شد. پس از توقف پردازش پنجره را ببندید.'); event.ignore()
        else: event.accept()


def main():
    app = QApplication(sys.argv); app.setStyle('Fusion'); app.setFont(QFont('Segoe UI', 10))
    def handle_error(kind, value, tb):
        logger().error('Unhandled exception', exc_info=(kind, value, tb))
        QMessageBox.critical(None, 'خطای برنامه', friendly_error(value))
    sys.excepthook = handle_error
    window = Window(); window.showMaximized(); sys.exit(app.exec())


if __name__ == '__main__': main()
