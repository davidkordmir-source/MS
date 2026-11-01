STYLE = '''
QWidget {font-family: 'Segoe UI', Tahoma; font-size: 13px; color: #17354b;}
QWidget#shell {background: #edf3f7;}
QFrame#card {background: #ffffff; border: 1px solid #dbe5ed; border-radius: 17px;}
QFrame#header {background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #11273f, stop:1 #0d4753); border-radius: 18px;}
QLabel#brand {color: #f3fbff; font-size: 25px; font-weight: 700;}
QLabel#subtitle {color: #a6cbd3; font-size: 12px;}
QLabel#stage {color: #b5f4df; background: #174e5a; border: 1px solid #296675; border-radius: 12px; padding: 8px 14px; font-weight: 600;}
QLabel#section {font-size: 15px; font-weight: 700; color: #173d51;}
QLabel#muted {color: #5b7789; font-size: 12px;}
QLabel#status {font-weight: 600; color: #0b857d; padding: 8px;}
QLabel#condition {background: #edf9f5; border: 1px solid #c9e9df; border-radius: 9px; padding: 10px; color: #14685f; font-weight: 600;}
QLabel#zoom {background: #edf4f8; color: #335368; border-radius: 7px; padding: 5px 9px; font-weight: 600;}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {background: #f8fbfd; border: 1px solid #ccdce7; border-radius: 8px; padding: 7px; min-height: 21px; selection-background-color: #138f88;}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {background: white; border: 1px solid #13a49a;}
QComboBox::drop-down {border: none; width: 24px;}
QComboBox::down-arrow {image: url("ASSET_ROOT/chevron-down.png"); width: 14px; height: 14px;}
QComboBox QAbstractItemView {background: white; border: 1px solid #cbdce6; selection-background-color: #e0f3ee; selection-color: #174c46; padding: 5px;}
QPushButton {background: #e9f1f6; color: #23485d; border: 1px solid transparent; border-radius: 9px; padding: 9px 13px; font-weight: 600;}
QPushButton:hover {background: #e1efef;}
QPushButton:pressed {background: #c8e4df;}
QPushButton#primary {background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #087e79, stop:1 #0b9e8b); color: white; min-height: 28px; font-size: 15px;}
QPushButton#primary:hover {background: #0a8d80;}
QPushButton:disabled {background: #e3ebf0; color: #94a7b4;}
QTextEdit {background: #f7fafc; border: 1px solid #dfE8ef; border-radius: 9px; padding: 8px; font-family: Consolas, Tahoma; font-size: 12px;}
QProgressBar {border: none; background: #e0eceb; border-radius: 4px; height: 8px;}
QProgressBar::chunk {background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b49c, stop:1 #38cdb6); border-radius: 4px;}
QTabWidget::pane {border: none; background: transparent;}
QTabBar::tab {background: #edf3f6; color: #617b8d; border: none; border-radius: 7px; padding: 8px 12px; margin: 2px;}
QTabBar::tab:selected {background: #dff3ee; color: #14695d; font-weight: 600;}
QScrollArea {border: none; background: transparent;}
QScrollBar:vertical {background: transparent; width: 9px; margin: 2px;}
QScrollBar::handle:vertical {background: #c2d3de; border-radius: 4px; min-height: 36px;}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {height: 0px;}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {background: none;}
QSplitter::handle {background: transparent; width: 10px;}
QToolTip {background: #143748; color: #effaff; border: none; padding: 7px;}
'''
