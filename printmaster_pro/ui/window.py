from dataclasses import replace, asdict
from pathlib import Path
import shutil
import tempfile
import pymupdf as fitz
import keyring
from PySide6.QtCore import Qt, QThreadPool, QTimer, QPropertyAnimation, QSize
from PySide6.QtGui import QAction, QImage, QPixmap, QIcon
from PySide6.QtPrintSupport import QPrinterInfo
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFormLayout, QStackedWidget, QListWidget, QListWidgetItem, QFileDialog, QMessageBox, QSplitter, QScrollArea,
    QSpinBox, QComboBox, QLineEdit, QCheckBox, QTabWidget, QProgressBar, QInputDialog, QTextEdit, QGroupBox,
    QGraphicsOpacityEffect, QAbstractItemView, QDoubleSpinBox, QMenu)
from core.documents import SUPPORTED, to_pdf, save_new, libreoffice, extract_text, images_pdf
from core.layout import Layout, PAPER, GRID, compose
from core import pdf
from services.state import State
from services.worker import Job
from services.printing import PrintSettings, send, printer_status
from services.images import generate, SERVICE

ICONS = Path(__file__).resolve().parents[1]/'assets'


def spin(minimum, maximum, value):
    box = QSpinBox(); box.setRange(minimum,maximum); box.setValue(value); return box


def check(checked=False):
    box = QCheckBox(); box.setChecked(checked); return box


class Preview(QWidget):
    def __init__(self, window, path: Path, data: bytes):
        super().__init__()
        self.window, self.path, self.data = window, path, data
        self.output = None
        self.revision = 0
        self.render_job = None
        self.hits = ''
        layout = QVBoxLayout(self)
        row = QHBoxLayout(); layout.addLayout(row)
        self.page = spin(1,1,1); self.zoom = spin(20,300,85); self.zoom.setSuffix('%')
        self.count = QLabel(); self.search = QLineEdit(); self.search.setPlaceholderText(window.t('جستجو در صفحه','Find on page'))
        for widget in [window.button('‹',lambda: self.page.stepBy(-1)),self.page,self.count,window.button('›',lambda: self.page.stepBy(1)),self.zoom,self.search]:
            row.addWidget(widget)
        self.image = QLabel(); self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea(); scroll.setObjectName('canvas'); scroll.setWidgetResizable(True); scroll.setWidget(self.image); layout.addWidget(scroll,1)
        self.page.valueChanged.connect(self.render); self.zoom.valueChanged.connect(self.render)
        self.search.returnPressed.connect(self.render)

    def render(self):
        if self.output is None:
            return
        if self.render_job:
            self.render_job.cancelled.set()
        data, n, zoom, text = self.output, self.page.value()-1, self.zoom.value()/100, self.search.text().strip()
        self.revision += 1; revision = self.revision
        def task(progress):
            with fitz.open(stream=data,filetype='pdf') as doc:
                page = doc[n]
                if text:
                    for rect in page.search_for(text):
                        page.draw_rect(rect, color=None, fill=(1,.72,0), fill_opacity=.35, overlay=True)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom,zoom),alpha=False)
                return QImage(pix.samples,pix.width,pix.height,pix.stride,QImage.Format.Format_RGB888).copy()
        def display(image):
            if revision == self.revision:
                self.image.setPixmap(QPixmap.fromImage(image))
        job = Job(task); job.events.done.connect(display); self.render_job = job
        job.events.failed.connect(self.window.error); self.window.document_pool.start(job)

    def apply(self, data: bytes, total: int):
        self.output = data; self.page.blockSignals(True); self.page.setMaximum(total); self.page.blockSignals(False)
        self.count.setText(f'/ {total}'); self.render()


class Window(QMainWindow):
    def t(self, fa, en):
        return fa if self.fa else en

    def __init__(self):
        super().__init__()
        self.state = State(); self.fa = self.state.settings.get('language','fa') == 'fa'
        self.document_pool = QThreadPool(self); self.document_pool.setMaxThreadCount(1)
        self.jobs = set(); self.queue = []; self.queue_active = False; self.generated = []; self.preview_job = None
        self.setWindowTitle('PrintMaster Pro'); self.resize(1280,820); self.setMinimumSize(950,630); self.setAcceptDrops(True)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft if self.fa else Qt.LayoutDirection.LeftToRight)
        self.make_ui(); self.apply_theme()
        if not self.state.settings.get('initialized'):
            self.nav.setCurrentRow(7); self.statusBar().showMessage(self.t('برای شروع زبان، تم و پرینتر را انتخاب و تنظیمات را ذخیره کنید.','Choose language, theme and printer, then save your settings.'))
        else:
            self.nav.setCurrentRow(0)

    def button(self, text, callback, primary=False):
        btn = QPushButton(text); btn.clicked.connect(callback)
        if primary:
            btn.setProperty('primary', True)
        return btn

    def combo(self, values):
        box = QComboBox()
        for value in values:
            if isinstance(value,tuple):
                box.addItem(value[1],value[0])
            else:
                box.addItem(str(value),value)
        return box

    def make_ui(self):
        center = QWidget(); self.setCentralWidget(center); host = QHBoxLayout(center); host.setContentsMargins(0,0,0,0); host.setSpacing(0)
        sidebar = QWidget(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(209); host.addWidget(sidebar)
        rail = QVBoxLayout(sidebar); rail.setContentsMargins(13,20,13,15)
        brand = QLabel('PRINTMASTER\nPRO'); brand.setObjectName('brand'); rail.addWidget(brand); rail.addSpacing(16)
        self.nav = QListWidget(); self.nav.setObjectName('nav'); self.nav.setIconSize(QSize(19,19)); rail.addWidget(self.nav)
        foot = QLabel(self.t('●  محیط کاری آفلاین','●  OFFLINE WORKSPACE')); foot.setObjectName('muted'); rail.addWidget(foot)
        main = QWidget(); region = QVBoxLayout(main); host.addWidget(main,1); region.setContentsMargins(22,16,22,13)
        top = QHBoxLayout(); region.addLayout(top); self.heading = QLabel(); self.heading.setObjectName('heading'); top.addWidget(self.heading); top.addStretch()
        top.addWidget(self.button(self.t('باز کردن فایل','Open files'),self.browse,True))
        self.stack = QStackedWidget(); region.addWidget(self.stack,1)
        titles = [('خانه','Workspace','home'),('پیش‌نمایش و چاپ','Preview & print','printer'),('ابزارهای PDF','PDF toolkit','file'),('تبدیل و ویرایش','Convert & edit','convert'),('استودیوی تصویر','Image studio','image'),('صف چاپ','Print queue','queue'),('تاریخچه','History','history'),('تنظیمات','Settings','settings')]
        for (fa,en,icon), factory in zip(titles,[self.home,self.preview,self.tools,self.convert,self.ai,self.batch,self.history,self.settings]):
            QListWidgetItem(QIcon(str(ICONS/(icon+'.svg'))),self.t(fa,en),self.nav)
            widget = QWidget(); self.stack.addWidget(widget); factory(QVBoxLayout(widget))
        self.nav.currentRowChanged.connect(self.navigate)
        self.progress = QProgressBar(); self.progress.setRange(0,100); self.progress.setFixedWidth(145); self.progress.hide()
        self.cancel = self.button(self.t('لغو','Cancel'),self.cancel_jobs); self.cancel.hide()
        self.statusBar().addPermanentWidget(self.progress); self.statusBar().addPermanentWidget(self.cancel)
        menu = self.menuBar().addMenu(self.t('فایل','File'))
        for label,key,callback in [(self.t('باز کردن','Open'),'Ctrl+O',self.browse),(self.t('بستن تب','Close tab'),'Ctrl+W',lambda: self.close_tab(self.tabs.currentIndex())),(self.t('چاپ','Print'),'Ctrl+P',self.print_current),(self.t('خروج','Quit'),'Ctrl+Q',self.close)]:
            action = QAction(label,self); action.setShortcut(key); action.triggered.connect(callback); menu.addAction(action)

    def navigate(self, index):
        if index < 0:
            return
        self.heading.setText(self.nav.item(index).text()); self.stack.setCurrentIndex(index)
        effect = QGraphicsOpacityEffect(self.stack.currentWidget()); self.stack.currentWidget().setGraphicsEffect(effect)
        self.anim = QPropertyAnimation(effect,b'opacity',self); self.anim.setDuration(155); self.anim.setStartValue(.76); self.anim.setEndValue(1.0); self.anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        if index in (0,6):
            self.refresh_history()

    def apply_theme(self):
        mode = self.state.settings.get('theme','auto')
        dark = mode == 'dark' or (mode == 'auto' and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        bg,panel,text,muted,line,canvas = ('#101827','#192436','#edf4ff','#a4b5ce','#30415b','#101624') if dark else ('#edf2f8','#ffffff','#142641','#536781','#d5dfec','#dce5ef')
        self.setStyleSheet(f'''
        QMainWindow,QWidget {{background:{bg};color:{text};}} #sidebar {{background:#111d31;}} #sidebar QLabel {{background:transparent;color:#a9c0dd;}}
        #brand {{font-size:25px;font-weight:800;color:white;padding:12px;}} #heading {{font-size:27px;font-weight:800;}} #muted {{color:{muted};padding:8px;}}
        #hero {{background:#182f53;color:white;border-radius:16px;padding:27px;font-size:24px;font-weight:800;}}
        #nav {{background:#111d31;color:#c4d7ed;border:0;outline:0;}} #nav::item {{padding:12px 8px;border-radius:8px;margin:3px 0;}} #nav::item:selected {{background:#294b77;color:white;}} #nav::item:hover:!selected {{background:#203653;}}
        QPushButton {{background:{panel};color:{text};border:1px solid {line};padding:7px 12px;border-radius:8px;}} QPushButton:hover {{border-color:#6099ff;}} QPushButton[primary=true] {{background:#225bd7;color:white;border-color:#225bd7;font-weight:700;}}
        QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QTextEdit,QListWidget {{background:{panel};border:1px solid {line};border-radius:7px;padding:5px;selection-background-color:#225bd7;selection-color:white;}}
        QListWidget::item {{padding:7px;}} QComboBox QAbstractItemView {{background:{panel};color:{text};selection-background-color:#225bd7;}}
        QGroupBox {{border:1px solid {line};border-radius:9px;margin-top:13px;padding:10px;}} QGroupBox::title {{subcontrol-origin:margin;padding:0 6px;}} QTabWidget::pane {{border:1px solid {line};}} QTabBar::tab {{background:{panel};padding:8px;}} QTabBar::tab:selected {{border-bottom:3px solid #437cf0;}} #canvas, #canvas QWidget {{background:{canvas};}}
        ''')

    def home(self, layout):
        hero = QLabel(self.t('چاپ دقیق، از یک فضای کاری\nPrintMaster Pro','A dedicated workspace for better printing\nPrintMaster Pro')); hero.setObjectName('hero'); layout.addWidget(hero)
        layout.addWidget(QLabel(self.t('فایل‌ها را اینجا رها کنید یا از ابزارهای زیر شروع کنید.','Drop documents here or choose a tool to get started.')))
        row = QHBoxLayout(); layout.addLayout(row)
        for label, callback in [(self.t('باز کردن سند','Open document'),self.browse),(self.t('مدیریت PDF','PDF tools'),lambda: self.nav.setCurrentRow(2)),(self.t('تبدیل فایل','Convert files'),lambda: self.nav.setCurrentRow(3))]:
            button = self.button(label,callback); button.setMinimumHeight(62); row.addWidget(button)
        layout.addWidget(QLabel(self.t('فایل‌های اخیر','Recent files'))); self.recent = QListWidget(); self.recent.itemDoubleClicked.connect(self.open_item); layout.addWidget(self.recent,1)

    def preview(self, layout):
        split = QSplitter(); layout.addWidget(split)
        self.tabs = QTabWidget(); self.tabs.setTabsClosable(True); self.tabs.setMovable(True); self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tabs.customContextMenuRequested.connect(self.preview_menu)
        self.tabs.currentChanged.connect(lambda _:self.schedule()); split.addWidget(self.tabs)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumWidth(273); panel = QWidget(); controls = QVBoxLayout(panel); scroll.setWidget(panel); split.addWidget(scroll); split.setStretchFactor(0,1)
        box = QGroupBox(self.t('چیدمان برگه','Sheet layout')); form = QFormLayout(box); controls.addWidget(box)
        fields = [
            ('selection','محدوده صفحات','Page range',QLineEdit()),('parity','فیلتر صفحات','Page filter',self.combo([('all',self.t('همه','All')),('odd',self.t('فرد','Odd')),('even',self.t('زوج','Even'))])),
            ('paper','کاغذ','Paper',self.combo(PAPER)),('landscape','افقی','Landscape',check()),('nup','صفحه در هر رو','Pages per side',self.combo(GRID)),
            ('mode','حالت','Mode',self.combo([('normal',self.t('عادی','Normal')),('booklet',self.t('کتابچه','Booklet')),('poster',self.t('پوستر (با درصد)','Poster (uses % scale)'))])),
            ('width_mm','عرض سفارشی (mm)','Custom width (mm)',spin(50,1000,210)),('height_mm','ارتفاع سفارشی (mm)','Custom height (mm)',spin(50,1000,297)),('overlap','همپوشانی پوستر (pt)','Poster overlap (pt)',spin(0,120,18)),
            ('margin','حاشیه (pt)','Margin (pt)',spin(0,250,18)),('gutter','صحافی (pt)','Gutter (pt)',spin(0,250,0)),
            ('scale','مقیاس','Scale',self.combo([('fit',self.t('جای‌گیری','Fit')),('shrink',self.t('فقط کوچک‌کردن','Shrink only')),('actual',self.t('اندازه واقعی','Actual size')),('percent',self.t('درصد','Custom %'))])),
            ('percent','درصد','Percent',spin(1,400,100)),('rtl','ترتیب راست‌به‌چپ','Right-to-left order',check()),('grayscale','خاکستری','Grayscale',check()),
            ('numbers','شماره برگه','Sheet numbers',check()),('watermark','واترمارک متنی','Text watermark',QLineEdit())]
        self.fields = {}
        for name,fa,en,widget in fields:
            form.addRow(self.t(fa,en),widget); self.fields[name] = widget
            signal = widget.textChanged if isinstance(widget,QLineEdit) else widget.toggled if isinstance(widget,QCheckBox) else widget.currentIndexChanged if isinstance(widget,QComboBox) else widget.valueChanged
            signal.connect(self.schedule)
        self.fields['selection'].setPlaceholderText('1-3,5,8-10')
        box = QGroupBox(self.t('چاپگر و نسخه‌ها','Printer & copies')); form = QFormLayout(box); controls.addWidget(box)
        self.printer = self.combo(QPrinterInfo.availablePrinterNames()); default = self.state.settings.get('printer') or QPrinterInfo.defaultPrinterName(); self.printer.setCurrentText(default)
        self.copies = spin(1,99,1); self.collate = check(True); self.dpi = self.combo([150,300,600]); self.dpi.setCurrentIndex(1)
        self.duplex = self.combo([('none',self.t('یک‌رو','Simplex')),('long',self.t('دورو: لبه بلند','Duplex: long edge')),('short',self.t('دورو: لبه کوتاه','Duplex: short edge')),('manual',self.t('دوروی دستی','Manual duplex'))])
        for fa,en,w in [('پرینتر','Printer',self.printer),('تعداد','Copies',self.copies),('ترتیب نسخه‌ها','Collate',self.collate),('کیفیت DPI','Quality DPI',self.dpi),('دورو','Duplex',self.duplex)]:
            form.addRow(self.t(fa,en),w)
        self.copies.valueChanged.connect(self.estimate); self.duplex.currentIndexChanged.connect(self.estimate)
        self.estimate_label = QLabel(); controls.addWidget(self.estimate_label)
        for label,fn,primary in [(self.t('چاپ','Print'),self.print_current,True),(self.t('ذخیره برگه‌ها به PDF','Save composed PDF'),self.export_preview,False),(self.t('افزودن به صف','Add to queue'),self.enqueue,False),(self.t('وضعیت پرینتر','Printer status'),self.status_printer,False),(self.t('ذخیره پیش‌تنظیم','Save preset'),self.save_preset,False),(self.t('بارگذاری پیش‌تنظیم','Load preset'),self.load_preset,False)]:
            controls.addWidget(self.button(label,fn,primary))
        controls.addStretch()
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(350); self.timer.timeout.connect(self.recompose)

    def preview_menu(self, point):
        if not self.current(): return
        menu = QMenu(self)
        for label,fn in [(self.t('چاپ','Print'),self.print_current),(self.t('ذخیره PDF','Save PDF'),self.export_preview),(self.t('بستن تب','Close tab'),lambda:self.close_tab(self.tabs.currentIndex()))]:
            menu.addAction(label,fn)
        menu.exec(self.tabs.mapToGlobal(point))

    def current(self):
        return self.tabs.currentWidget()

    def options(self):
        return Layout(**{k:(w.text() if isinstance(w,QLineEdit) else w.currentData() if isinstance(w,QComboBox) else w.isChecked() if isinstance(w,QCheckBox) else w.value()) for k,w in self.fields.items()})

    def schedule(self, *_):
        if hasattr(self,'timer'):
            if self.preview_job and self.preview_job in self.jobs:
                self.preview_job.cancelled.set()
            if tab := self.current():
                tab.output = None; tab.image.setText(self.t('در حال به‌روزرسانی…','Updating preview…'))
            self.timer.start()

    def recompose(self):
        tab = self.current()
        if tab is None:
            return
        options = self.options()
        def task(progress):
            output = compose(tab.data,options,progress)
            with fitz.open(stream=output,filetype='pdf') as doc:
                return output,len(doc)
        def ready(result):
            if self.current() is tab and options == self.options():
                tab.apply(*result); self.estimate()
        self.preview_job = self.run(task,ready)

    def estimate(self, *_):
        tab = self.current()
        if not tab or tab.output is None:
            self.estimate_label.clear(); return
        sides = tab.page.maximum(); sheets = (sides if self.duplex.currentData() == 'none' else (sides+1)//2)*self.copies.value()
        self.estimate_label.setText(self.t(f'{sheets} برگ • هزینه {sheets*self.cost.value():,.2f}',f'{sheets} sheets • cost {sheets*self.cost.value():,.2f}'))

    def save_preset(self):
        name, ok = QInputDialog.getText(self,self.t('پیش‌تنظیم','Preset'),self.t('نام','Name'))
        if ok and name.strip():
            self.state.settings.setdefault('presets',{})[name.strip()] = asdict(self.options()); self.state.save()

    def load_preset(self):
        presets = self.state.settings.get('presets',{})
        if not presets:
            return self.error(self.t('پیش‌تنظیمی ذخیره نشده است.','No presets saved.'))
        name,ok = QInputDialog.getItem(self,self.t('پیش‌تنظیم','Preset'),self.t('نام','Name'),list(presets),editable=False)
        if ok:
            for key,value in presets[name].items():
                w = self.fields[key]
                if isinstance(w,QLineEdit): w.setText(value)
                elif isinstance(w,QCheckBox): w.setChecked(value)
                elif isinstance(w,QComboBox): w.setCurrentIndex(w.findData(value))
                else: w.setValue(value)

    def run(self, action, ready, finished=None, background_io=False):
        job = Job(action); self.jobs.add(job); self.progress.show(); self.cancel.show()
        job.events.progress.connect(self.progress.setValue); job.events.done.connect(ready); job.events.failed.connect(self.error)
        def cleanup():
            self.jobs.discard(job)
            if not self.jobs:
                self.cancel.hide(); self.progress.hide()
            if finished:
                finished()
        job.events.finished.connect(cleanup); (QThreadPool.globalInstance() if background_io else self.document_pool).start(job); return job

    def cancel_jobs(self):
        for job in self.jobs:
            job.cancelled.set()

    def error(self, text):
        QMessageBox.warning(self,'PrintMaster Pro',text)

    def browse(self):
        pattern = ' '.join('*'+s for s in sorted(SUPPORTED)); paths,_ = QFileDialog.getOpenFileNames(self,self.t('انتخاب فایل','Open files'),filter=f'Documents ({pattern});;All files (*)')
        self.open_paths([Path(p) for p in paths])

    def open_paths(self, paths):
        for path in paths:
            if path.suffix.lower() not in SUPPORTED:
                continue
            password = ''
            if path.suffix.lower() == '.pdf':
                try:
                    with fitz.open(path) as doc: encrypted = doc.needs_pass
                except Exception as exc:
                    self.error(str(exc)); continue
                if encrypted:
                    password,ok = QInputDialog.getText(self,path.name,self.t('رمز PDF','PDF password'),QLineEdit.EchoMode.Password)
                    if not ok: continue
            self.run(lambda p, path=path, password=password: to_pdf(path,password),lambda data,path=path:self.add_document(path,data))

    def add_document(self, path, data):
        tab = Preview(self,path,data); index = self.tabs.addTab(tab,path.name); self.tabs.setCurrentIndex(index)
        self.nav.setCurrentRow(1); self.state.record('open',path); self.schedule()

    def close_tab(self,index):
        if index >= 0:
            widget = self.tabs.widget(index); self.tabs.removeTab(index)
            if widget.render_job: widget.render_job.cancelled.set()
            widget.deleteLater()

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in SUPPORTED for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self,event):
        self.open_paths([Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]); event.acceptProposedAction()

    def active_data(self, composed=False):
        tab = self.current()
        if tab is None or (composed and tab.output is None):
            self.error(self.t('ابتدا فایل و پیش‌نمایش آماده را انتخاب کنید.','Open a file and wait for its preview.')); return None
        return tab

    def save_result(self,path,data,extension='.pdf'):
        suggested = Path(path).with_name(Path(path).stem+'_edited'+extension)
        target,_ = QFileDialog.getSaveFileName(self,self.t('ذخیره خروجی','Save output'),str(suggested),f'*{extension}')
        if target:
            self.run(lambda p: save_new(Path(target),data),lambda _: self.statusBar().showMessage(self.t('خروجی ذخیره شد','Output saved'),5000))

    def export_preview(self):
        if tab := self.active_data(True): self.save_result(tab.path,tab.output)

    def printer_settings(self):
        mode = self.duplex.currentData()
        return PrintSettings(self.printer.currentText(),self.copies.value(),self.collate.isChecked(),self.dpi.currentData(),'none' if mode == 'manual' else mode)

    def status_printer(self):
        name = self.printer.currentText()
        self.run(lambda p: printer_status(name),lambda text: QMessageBox.information(self,name or 'Printer',text or self.t('وضعیت در دسترس نیست.','Status unavailable.')))

    def print_current(self):
        if tab := self.active_data(True):
            path, data, settings = tab.path, tab.output, self.printer_settings()
            if self.duplex.currentData() == 'manual':
                if settings.copies != 1:
                    return self.error(self.t('برای دوروی دستی هر بار یک نسخه چاپ کنید.','Manual duplex requires one copy at a time.'))
                if tab.page.maximum() % 2:
                    return self.error(self.t('برای دوروی دستی تعداد روها باید زوج باشد.','Manual duplex requires an even number of sides.'))
                def back(_):
                    if QMessageBox.question(self,'PrintMaster Pro',self.t('برگه‌ها را طبق مسیر تغذیه پرینتر برگردانید. ابتدا با دو برگه آزمایش کنید. چاپ پشت به ترتیب معکوس انجام شود؟','Reinsert the stack following your printer’s feed direction. Test with two sheets first. Print backs in reverse order?')) == QMessageBox.StandardButton.Yes:
                        self.print_job(path,data,replace(settings,pass_number=2))
                self.run(lambda p: send(data,replace(settings,pass_number=1),path.name,p),back)
            else:
                self.print_job(path,data,settings)

    def print_job(self,path,data,settings):
        self.run(lambda p: send(data,settings,path.name,p),lambda _: self.printed(path))

    def printed(self,path):
        self.state.record('print',path); self.statusBar().showMessage(self.t('ارسال به پرینتر انجام شد','Sent to printer'),5000)

    def tools(self, layout):
        row = QHBoxLayout(); layout.addLayout(row)
        row.addWidget(self.button(self.t('ادغام PDFها','Merge PDFs'),self.merge_dialog,True))
        row.addWidget(self.button(self.t('تقسیم هر N صفحه','Split every N pages'),self.split_dialog))
        row.addWidget(self.button(self.t('مقایسه دو PDF','Compare two PDFs'),self.compare_dialog))
        box = QGroupBox(self.t('عملیات روی تب فعال','Operations on active document')); layout.addWidget(box); form = QFormLayout(box)
        self.pdf_op = self.combo([(a,self.t(b,c)) for a,b,c in [('extract','استخراج / تغییر ترتیب','Extract / reorder'),('delete','حذف صفحات','Delete pages'),('rotate','چرخش (زاویه)','Rotate (degrees)'),('crop','برش (چپ، بالا، راست، پایین)','Crop (left,top,right,bottom pt)'),('blank','افزودن صفحه خالی','Append blank page'),('highlight','هایلایت عبارت','Highlight text'),('redact','حذف دائمی عبارت','Redact text matches'),('flatten','تخت‌سازی فرم و حاشیه‌نویسی','Flatten forms & annotations'),('metadata','عنوان سند','Set document title'),('scrub','حذف metadata','Strip metadata'),('lock','رمزگذاری AES-256','Encrypt AES-256'),('unlock','ذخیره بدون رمز','Save without encryption'),('compress','بهینه‌سازی (۱ تا ۳)','Optimize (1–3)'),('linear','نمایش سریع وب','Linearize'),('ocr','OCR (eng+fas+ara)','OCR (eng+fas+ara)')]])
        self.pdf_range = QLineEdit(); self.pdf_range.setPlaceholderText('1-3,5'); self.pdf_value = QLineEdit()
        self.pdf_op.currentIndexChanged.connect(lambda: (self.pdf_value.clear(),self.pdf_value.setEchoMode(QLineEdit.EchoMode.Password if self.pdf_op.currentData() == 'lock' else QLineEdit.EchoMode.Normal)))
        form.addRow(self.t('عملیات','Operation'),self.pdf_op); form.addRow(self.t('صفحات (خالی = همه)','Pages (blank = all)'),self.pdf_range); form.addRow(self.t('مقدار / متن / زبان','Value / text / languages'),self.pdf_value)
        form.addRow(self.button(self.t('اجرا و ذخیره نسخه جدید','Run and save a new copy'),self.run_pdf,True))
        note = QLabel(self.t('OCR به نصب Tesseract و بسته‌های fas، eng و ara نیاز دارد. حذف دائمی فقط موارد پیدا شده در لایه متن را پوشش می‌دهد؛ خروجی را بررسی کنید.','OCR needs Tesseract and the fas, eng and ara language packs. Redaction only covers matches in the text layer; inspect the result.')); note.setWordWrap(True); layout.addWidget(note); layout.addStretch()

    def compare_dialog(self):
        if not (tab := self.active_data()): return
        path,_ = QFileDialog.getOpenFileName(self,self.t('PDF دوم','Second PDF'),filter='PDF (*.pdf)')
        if path:
            data = tab.data
            self.run(lambda p:pdf.compare(data,to_pdf(Path(path)),p),lambda text: QMessageBox.information(self,'PDF',text))

    def merge_dialog(self):
        names,_ = QFileDialog.getOpenFileNames(self,self.t('انتخاب PDFها','Choose PDFs'),filter='PDF (*.pdf)')
        if not names: return
        from PySide6.QtWidgets import QDialog
        dlg = QDialog(self); dlg.setWindowTitle(self.t('ترتیب ادغام','Merge order')); dlg.resize(620,340)
        layout = QVBoxLayout(dlg); listing = QListWidget(); listing.addItems(names); listing.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove); layout.addWidget(listing)
        layout.addWidget(self.button(self.t('ادغام به ترتیب فهرست','Merge in list order'),dlg.accept,True))
        if dlg.exec():
            paths = [Path(listing.item(i).text()) for i in range(listing.count())]; self.run(lambda p: pdf.merge(paths),lambda data:self.save_result(paths[0],data))

    def split_dialog(self):
        if not (tab := self.active_data()): return
        size,ok = QInputDialog.getInt(self,'PDF',self.t('تعداد صفحه در هر بخش','Pages per part'),1,1,10000)
        if not ok: return
        folder = QFileDialog.getExistingDirectory(self,self.t('پوشه خروجی','Output folder'))
        if not folder: return
        data,path = tab.data,tab.path
        def task(progress):
            parts = pdf.split(data,size)
            for i,part in enumerate(parts):
                progress(i*100//len(parts)); save_new(Path(folder)/f'{path.stem}_edited_{i+1:03}.pdf',part)
            return len(parts)
        self.run(task,lambda count:self.statusBar().showMessage(f'{count} PDF',5000))

    def run_pdf(self):
        if not (tab := self.active_data()): return
        op,spec,value,data,path = self.pdf_op.currentData(),self.pdf_range.text(),self.pdf_value.text(),tab.data,tab.path
        if op == 'lock': self.pdf_value.clear()
        def task(progress):
            if op in ('extract','delete'): return pdf.select(data,spec,op=='delete')
            if op == 'ocr': return pdf.ocr(data,value.strip() or 'fas+eng+ara',progress)
            if op == 'unlock': return data
            if op == 'lock' and not value.strip(): raise ValueError('Enter a non-empty password.')
            if op in ('linear','compress','lock','scrub'):
                level = int(value or '2') if op == 'compress' else 2
                if level not in (1,2,3): raise ValueError('Enter an optimization level from 1 to 3.')
                return pdf.optimize(data,level=level,linear=op=='linear',password=value if op=='lock' else '',strip=op=='scrub')
            return pdf.modify(data,op,spec,value)
        def done(result):
            self.statusBar().showMessage(f'{len(data):,} → {len(result):,} bytes',8000); self.save_result(path,result)
        self.run(task,done)

    def convert(self,layout):
        row = QHBoxLayout(); layout.addLayout(row)
        row.addWidget(self.button(self.t('استخراج متن از فایل','Import file text'),self.import_text))
        row.addWidget(self.button(self.t('تبدیل دسته‌ای به PDF','Batch convert to PDF'),self.batch_convert))
        row.addWidget(self.button(self.t('PDF به تصویر','PDF to images'),self.export_images))
        self.editor = QTextEdit(); self.editor.setPlaceholderText(self.t('متن خود را بنویسید یا از یک سند استخراج کنید…','Write text or import it from a document…'))
        tools = QHBoxLayout(); layout.addLayout(tools)
        for label,fn in [('B',lambda:self.editor.setFontWeight(700 if self.editor.fontWeight()<700 else 400)),('I',lambda:self.editor.setFontItalic(not self.editor.fontItalic())),('U',lambda:self.editor.setFontUnderline(not self.editor.fontUnderline())),('↶',self.editor.undo),('↷',self.editor.redo),('RTL',lambda:self.editor_direction(True)),('LTR',lambda:self.editor_direction(False))]:
            tools.addWidget(self.button(label,fn))
        from PySide6.QtWidgets import QFontComboBox
        font = QFontComboBox(); font.setMaximumWidth(160); font.currentFontChanged.connect(self.editor.setCurrentFont); tools.addWidget(font)
        for label,fn in [('≡',lambda:self.editor.setAlignment(Qt.AlignmentFlag.AlignHCenter)),('•',self.editor_list),('▦',self.editor_table),(self.t('جستجو','Find'),self.editor_find)]:
            tools.addWidget(self.button(label,fn))
        size = spin(7,96,12); size.valueChanged.connect(self.editor.setFontPointSize); tools.addWidget(size); tools.addStretch()
        layout.addWidget(self.editor,1)
        row = QHBoxLayout(); layout.addLayout(row); self.words = QLabel(); row.addWidget(self.words); row.addStretch()
        self.editor.textChanged.connect(lambda:self.words.setText(self.t('واژه‌ها: ','Words: ')+str(len(self.editor.toPlainText().split()))))
        for ext in ['TXT','DOCX','PDF']:
            row.addWidget(self.button(ext,lambda _,ext=ext:self.save_editor(ext)))
        row.addWidget(self.button(self.t('پیش‌نمایش چاپ','Print preview'),lambda:self.save_editor('PDF',True),True))
        self.autosave = QTimer(self); self.autosave.setInterval(5000); self.autosave.timeout.connect(self.save_draft); self.autosave.start()
        self.draft = self.state.root/'draft.html'
        if self.draft.exists():
            try: self.editor.setHtml(self.draft.read_text(encoding='utf-8'))
            except OSError: pass
        self.editor.document().setModified(False)

    def editor_list(self):
        from PySide6.QtGui import QTextListFormat
        self.editor.textCursor().createList(QTextListFormat.Style.ListDisc)

    def editor_table(self):
        rows,ok = QInputDialog.getInt(self,'Table',self.t('تعداد ردیف','Rows'),2,1,30)
        if ok:
            cols,ok = QInputDialog.getInt(self,'Table',self.t('تعداد ستون','Columns'),2,1,15)
            if ok: self.editor.textCursor().insertTable(rows,cols)

    def editor_find(self):
        needle,ok = QInputDialog.getText(self,self.t('جستجو','Find'),self.t('متن','Text'))
        if ok and needle:
            from PySide6.QtGui import QTextCursor
            if not self.editor.find(needle):
                self.editor.moveCursor(QTextCursor.MoveOperation.Start)
                if not self.editor.find(needle): return self.statusBar().showMessage(self.t('یافت نشد','Not found'),4000)
            replacement,ok = QInputDialog.getText(self,self.t('جایگزینی اختیاری','Optional replacement'),self.t('متن جایگزین (لغو برای حفظ انتخاب)','Replacement (Cancel to keep selection)'))
            if ok: self.editor.textCursor().insertText(replacement)

    def save_draft(self):
        if not self.editor.document().isModified(): return
        try:
            temp = self.draft.with_suffix('.tmp'); temp.write_text(self.editor.toHtml(),encoding='utf-8'); temp.replace(self.draft)
            self.editor.document().setModified(False)
        except OSError as exc:
            self.statusBar().showMessage(str(exc),5000)

    def editor_direction(self, rtl):
        cursor = self.editor.textCursor(); fmt = cursor.blockFormat(); fmt.setLayoutDirection(Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight); cursor.mergeBlockFormat(fmt)
        self.editor.setAlignment(Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft)

    def import_text(self):
        path,_ = QFileDialog.getOpenFileName(self,self.t('استخراج متن','Import text'),filter='Documents (*.pdf *.docx *.txt *.md)')
        if path: self.run(lambda p:extract_text(Path(path)),self.editor.setPlainText)

    def save_editor(self,ext,preview=False):
        text,html,rtl = self.editor.toPlainText(),self.editor.toHtml(),self.fa
        if ext == 'DOCX' and QMessageBox.question(self,'DOCX',self.t('خروجی DOCX فقط متن را حفظ می‌کند. ادامه؟','DOCX export preserves text only. Continue?')) != QMessageBox.StandardButton.Yes: return
        def task(progress):
            if ext == 'TXT': return text.encode('utf-8')
            if ext == 'DOCX':
                from docx import Document
                from io import BytesIO
                doc = Document()
                for p in text.split('\n'):
                    par = doc.add_paragraph(p)
                    if rtl:
                        from docx.oxml import OxmlElement
                        par._p.get_or_add_pPr().append(OxmlElement('w:bidi'))
                buf = BytesIO(); doc.save(buf); return buf.getvalue()
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp)/'document.html'; path.write_text(html,encoding='utf-8'); return to_pdf(path)
        self.run(task,lambda data:self.add_document(Path('Document.pdf'),data) if preview else self.save_result(Path('Document'),data,'.'+ext.lower()))

    def batch_convert(self):
        paths,_ = QFileDialog.getOpenFileNames(self,'PDF',filter='Files ('+' '.join('*'+s for s in sorted(SUPPORTED-{'.pdf'}))+')')
        if not paths: return
        folder = QFileDialog.getExistingDirectory(self,self.t('پوشه خروجی','Output folder'))
        if not folder: return
        def task(progress):
            errors = []
            for i,p in enumerate(paths):
                progress(i*100//len(paths)); path = Path(p)
                try: save_new(Path(folder)/f'{path.stem}_edited.pdf',to_pdf(path))
                except Exception as exc: errors.append(f'{path.name}: {exc}')
            return errors
        self.run(task,lambda errors:self.error('\n'.join(errors)) if errors else self.statusBar().showMessage('PDF ✓',5000))

    def export_images(self):
        if not (tab := self.active_data()): return
        dpi,ok = QInputDialog.getInt(self,'PNG','DPI',150,72,600)
        if not ok: return
        folder = QFileDialog.getExistingDirectory(self,self.t('پوشه خروجی','Output folder'))
        if not folder: return
        data, name = tab.data,tab.path.stem
        def task(progress):
            with fitz.open(stream=data,filetype='pdf') as doc:
                for i,page in enumerate(doc):
                    progress(i*100//len(doc)); save_new(Path(folder)/f'{name}_edited_{i+1:03}.png',page.get_pixmap(dpi=dpi).tobytes('png'))
        self.run(task,lambda _:self.statusBar().showMessage('PNG ✓',5000))

    def ai(self,layout):
        label = QLabel(self.t('فقط این بخش به اینترنت نیاز دارد. پرامپت به ارائه‌دهنده تنظیم‌شده ارسال می‌شود.','Only this workspace uses the internet. Your prompt is sent to the configured provider.')); label.setWordWrap(True); layout.addWidget(label)
        self.prompt = QTextEdit(); self.prompt.setMaximumHeight(112); self.prompt.setPlaceholderText(self.t('توضیح تصویر…','Describe an image…')); layout.addWidget(self.prompt)
        row = QHBoxLayout(); layout.addLayout(row); self.ai_size = self.combo(['1024x1024','1024x1536','1536x1024']); self.ai_count = spin(1,10,1); self.style_hint = QLineEdit(); self.style_hint.setPlaceholderText(self.t('سبک اختیاری','Optional style'))
        for w in [self.ai_size,self.ai_count,self.style_hint,self.button(self.t('تولید / تلاش دوباره','Generate / retry'),self.generate_images,True)]: row.addWidget(w)
        self.gallery = QListWidget(); self.gallery.setViewMode(QListWidget.ViewMode.IconMode); self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust); self.gallery.setIconSize(QSize(120,100)); self.gallery.itemDoubleClicked.connect(self.show_image); layout.addWidget(self.gallery,1)
        row = QHBoxLayout(); layout.addLayout(row); row.addWidget(self.button(self.t('ذخیره تصویر انتخابی','Save selected image'),self.save_image)); row.addWidget(self.button(self.t('ساخت PDF و پیش‌نمایش','Create PDF & preview'),self.gallery_pdf,True))

    def generate_images(self):
        settings = self.state.settings; endpoint,model = settings.get('api_url',''),settings.get('api_model',''); prompt = self.prompt.toPlainText().strip()
        if self.style_hint.text().strip(): prompt += '\nStyle: '+self.style_hint.text().strip()
        size,count = self.ai_size.currentData(),self.ai_count.value()
        def ready(result):
            self.generated = result; self.gallery.clear()
            for index,raw in enumerate(result):
                pix = QPixmap(); pix.loadFromData(raw); QListWidgetItem(QIcon(pix),str(index+1),self.gallery)
            self.gallery.setCurrentRow(0)
        self.run(lambda p:generate(endpoint,model,prompt,size,count,p),ready,background_io=True)

    def show_image(self,item):
        from PySide6.QtWidgets import QDialog
        raw = self.generated[self.gallery.row(item)]; pix = QPixmap(); pix.loadFromData(raw)
        dlg = QDialog(self); layout = QVBoxLayout(dlg); label = QLabel(); label.setPixmap(pix.scaled(720,620,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)); layout.addWidget(label); dlg.exec()

    def save_image(self):
        index = self.gallery.currentRow()
        if index >= 0: self.save_result(Path(f'Image_{index+1}'),self.generated[index],'.png')

    def gallery_pdf(self):
        images = self.generated[:]
        if not images: return
        def task(progress):
            with tempfile.TemporaryDirectory() as temp:
                paths = []
                for i,data in enumerate(images):
                    progress(i*100//len(images)); path = Path(temp)/f'{i}.png'; path.write_bytes(data); paths.append(path)
                return images_pdf(paths)
        self.run(task,lambda data:self.add_document(Path('Images.pdf'),data))

    def batch(self,layout):
        self.queue_list = QListWidget(); layout.addWidget(self.queue_list,1)
        row = QHBoxLayout(); layout.addLayout(row)
        for label,fn in [(self.t('بالا بردن','Move up'),self.queue_up),(self.t('حذف','Remove'),self.queue_remove),(self.t('توقف بعد از کار جاری','Pause after current'),self.queue_pause),(self.t('شروع / ادامه','Start / resume'),self.queue_start)]: row.addWidget(self.button(label,fn))

    def enqueue(self):
        if self.duplex.currentData() == 'manual': return self.error(self.t('دوروی دستی فقط در چاپ مستقیم.','Manual duplex is only available for direct printing.'))
        if tab := self.active_data(True):
            settings=self.printer_settings(); self.queue.append((tab.path,tab.output,settings)); self.refresh_queue(); self.nav.setCurrentRow(5)

    def refresh_queue(self):
        self.queue_list.clear(); self.queue_list.addItems([f'{path.name}  •  {settings.name or "—"}  ×{settings.copies}' for path,_,settings in self.queue])

    def queue_up(self):
        i = self.queue_list.currentRow()
        if i > 0: self.queue[i-1],self.queue[i] = self.queue[i],self.queue[i-1]; self.refresh_queue(); self.queue_list.setCurrentRow(i-1)

    def queue_remove(self):
        i = self.queue_list.currentRow()
        if i >= 0: self.queue.pop(i); self.refresh_queue()

    def queue_pause(self): self.queue_active = False

    def queue_start(self):
        self.queue_active = True
        self.queue_next()

    def queue_next(self):
        if not self.queue_active or not self.queue or getattr(self,'queue_busy',False): return
        path,data,settings = self.queue.pop(0); self.refresh_queue(); self.queue_busy = True
        completed = False
        def done(_):
            nonlocal completed
            completed = True; self.printed(path)
        def finish():
            self.queue_busy = False
            if not completed:
                self.queue_active = False; self.queue.insert(0,(path,data,settings)); self.refresh_queue()
            else: self.queue_next()
        self.run(lambda p:send(data,settings,path.name,p),done,finish)

    def history(self,layout):
        self.log_list = QListWidget(); self.log_list.itemDoubleClicked.connect(self.open_item); layout.addWidget(self.log_list,1)
        layout.addWidget(QLabel(self.t('برای باز کردن و چاپ دوباره، روی فایل دوبار کلیک کنید.','Double-click a file to reopen it and print again.')))

    def refresh_history(self):
        for widget,kind in [(self.recent,'open'),(self.log_list,None)]:
            widget.clear(); seen = set()
            for stamp,event,path in self.state.history(kind):
                if kind and path in seen: continue
                seen.add(path); item = QListWidgetItem(f'{Path(path).name}  •  {event}  •  {stamp}'); item.setData(Qt.ItemDataRole.UserRole,path); item.setToolTip(path); widget.addItem(item)

    def open_item(self,item):
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if path.is_file(): self.open_paths([path])
        else: self.error(self.t('فایل اصلی در دسترس نیست.','Original file is unavailable.'))

    def settings(self,layout):
        form = QFormLayout(); layout.addLayout(form); settings = self.state.settings
        self.language = self.combo([('fa','فارسی'),('en','English')]); self.language.setCurrentIndex(0 if self.fa else 1)
        self.theme = self.combo([('auto',self.t('خودکار','Automatic')),('light',self.t('روشن','Light')),('dark',self.t('تیره','Dark'))]); self.theme.setCurrentIndex(max(0,self.theme.findData(settings.get('theme','auto'))))
        self.default_printer = self.combo(['',*QPrinterInfo.availablePrinterNames()]); self.default_printer.setCurrentText(settings.get('printer',QPrinterInfo.defaultPrinterName()))
        self.cost = QDoubleSpinBox(); self.cost.setRange(0,1_000_000); self.cost.setValue(settings.get('cost',0))
        self.api_url = QLineEdit(settings.get('api_url','')); self.api_url.setPlaceholderText('https://provider.example/v1'); self.api_model = QLineEdit(settings.get('api_model',''))
        self.api_key = QLineEdit(); self.api_key.setEchoMode(QLineEdit.EchoMode.Password); self.api_key.setPlaceholderText(self.t('خالی = کلید موجود حفظ می‌شود','Blank keeps existing key'))
        for fa,en,w in [('زبان','Language',self.language),('تم','Theme',self.theme),('پرینتر پیش‌فرض','Default printer',self.default_printer),('قیمت هر برگ','Cost per sheet',self.cost),('نشانی API تصویر','Image API base URL',self.api_url),('مدل','Image model',self.api_model),('کلید','Image API key',self.api_key)]: form.addRow(self.t(fa,en),w)
        self.dependencies = QLabel(self.t('وابستگی‌ها را بررسی کنید.','Check optional dependencies.')); self.dependencies.setWordWrap(True); layout.addWidget(self.dependencies)
        layout.addWidget(self.button(self.t('بررسی Tesseract و LibreOffice','Check Tesseract & LibreOffice'),self.check_dependencies))
        layout.addWidget(self.button(self.t('ذخیره تنظیمات','Save settings'),self.save_settings,True)); layout.addStretch()

    def check_dependencies(self):
        def task(progress):
            import pytesseract
            try: tess = ', '.join(pytesseract.get_languages()) or 'no language data'
            except (OSError,RuntimeError): tess = 'not available on PATH'
            return f'Tesseract: {tess}\nLibreOffice: {libreoffice() or "not found"}\nWindows: winget install UB-Mannheim.TesseractOCR; winget install TheDocumentFoundation.LibreOffice'
        self.run(task,self.dependencies.setText)

    def save_settings(self):
        endpoint = self.api_url.text().strip().rstrip('/'); token = self.api_key.text().strip()
        if token and not endpoint.startswith('https://'): return self.error(self.t('کلید را فقط برای نشانی HTTPS ذخیره کنید.','Only save keys for HTTPS endpoints.'))
        language = self.language.currentData(); settings = self.state.settings
        values = dict(initialized=True,language=language,theme=self.theme.currentData(),printer=self.default_printer.currentText(),cost=self.cost.value(),api_url=endpoint,api_model=self.api_model.text().strip())
        def task(progress):
            if token: keyring.set_password(SERVICE,endpoint,token)
        def done(_):
            settings.update(values); self.state.save(); self.api_key.clear(); self.apply_theme(); self.estimate(); self.printer.setCurrentText(values['printer'] or QPrinterInfo.defaultPrinterName())
            if (language=='fa') != self.fa:
                QMessageBox.information(self,'PrintMaster Pro',self.t('برای تغییر زبان برنامه را دوباره اجرا کنید.','Restart the application to apply the language change.'))
            else: self.statusBar().showMessage(self.t('تنظیمات ذخیره شد','Settings saved'),5000)
        self.run(task,done)

    def closeEvent(self,event):
        if self.jobs:
            self.cancel_jobs(); self.statusBar().showMessage(self.t('پس از پایان عملیات دوباره ببندید.','Close again after pending jobs finish.')); event.ignore(); return
        self.save_draft(); super().closeEvent(event)
