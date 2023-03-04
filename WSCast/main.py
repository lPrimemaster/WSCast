
from __future__ import annotations
import sys, socket
from PyQt5.QtCore import QTimer
from PyQt5 import QtGui, QtCore
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QStyle, QStyleOption
from PyQt5 import uic, QtWidgets
import ui.resources
import qdarktheme

import win32gui
from ctypes import windll

import numpy as np
import cv2 as cv
from PIL import ImageGrab

import astral
import astral.sun
import datetime
import tzlocal
import pytz

# from selenium import webdriver
# from selenium.webdriver.common.by import By
# import logging
# logger = logging.getLogger('selenium.webdriver.remote.remote_connection')
# logger.setLevel(logging.CRITICAL)

class CustomMenuBar(QWidget):
    def __init__(self, parent):
        super(CustomMenuBar, self).__init__(parent)
        self.pressing = False
        self.start = self.mapToGlobal(self.pos())
        self.end = self.start

    def setWindow(self, window):
        self.mwindow = window
    
    def paintEvent(self, evt: QtGui.QPaintEvent) -> None:
        o = QStyleOption()
        o.initFrom(self)
        p = QtGui.QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, o, p, self)


    def mousePressEvent(self, event):
        self.start = self.mwindow.mapToGlobal(event.pos())
        self.pressing = True

    def mouseMoveEvent(self, event):
        if self.pressing:
            self.end = self.mwindow.mapToGlobal(event.pos())
            self.movement = self.end-self.start
            self.mwindow.setGeometry(self.mwindow.mapToGlobal(self.movement).x(),
                                self.mwindow.mapToGlobal(self.movement).y(),
                                self.mwindow.width(),
                                self.mwindow.height())
            self.start = self.end

    def mouseReleaseEvent(self, QMouseEvent):
        self.pressing = False

class PalleteDisplay(QWidget):
    def __init__(self, parent=None):
        super(PalleteDisplay, self).__init__(parent)
        self.image = None
    
    def update(self, Z: cv.Mat):
        self.image = QtGui.QImage(Z, Z.shape[1], Z.shape[0], QtGui.QImage.Format_BGR888)
        self.repaint()

    def paintEvent(self, evt: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter()
        painter.begin(self)
        if self.image:
            source = QtCore.QRectF(0, 0, self.image.size().width(), self.image.size().height())
            target = QtCore.QRectF(0, 0, 300, 20)
            painter.drawImage(target, self.image, source)
        painter.end()

class Window(QMainWindow):
    ICON_RED_LED = ":/icons/led_red"
    ICON_GREEN_LED = ":/icons/led_green"
    ICON_BLUE_LED = ":/icons/led_blue"

    def __init__(self):
        super(Window, self).__init__()
        self.ui = uic.loadUi('ui/window.ui', self)
        self.connected = False
        self.mode = 0
        self.detect_mode = 0
        self.night_mode = True
        self.activated = False
        self.socket = None
        self.timer_tx = QTimer()
        self.timer_tx.timeout.connect(lambda: self.set_tx_bitmap(False))

        self.windll = windll.user32
        self.windll.SetProcessDPIAware()

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.loop)
        self.update_timer.setInterval(200)
        self.ui.polling_rate.valueChanged.connect(lambda x: self.update_timer.setInterval(x))

        self.process_check_timer = QTimer()
        self.process_check_timer.timeout.connect(self.handleProcessCheck)
        self.process_check_timer.setInterval(5000)
        self.process_check_timer.start()

        self.setWindowIcon(QtGui.QIcon(':/icons/wicon'))
        # self.setFixedSize(363, 212)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool)
        self.ui.btn_enable.clicked.connect(lambda: self.enable_disable_connection(manual=True))
        self.ui.brightness.sliderReleased.connect(self.handleBrightness)
        self.ui.brightness.valueChanged.connect(self.handleBrightnessVC)
        self.ui.func_mode.currentIndexChanged.connect(self.handleMode)
        self.ui.detection_mode.currentIndexChanged.connect(self.handleDetectionMode)

        self.ui.custom_bar.setWindow(self)
        self.ui.btn_exit.clicked.connect(lambda: (self.close(), QtWidgets.qApp.quit()))

        self.ui.chk_night.stateChanged.connect(self.setNightModeState)

        # Get lat and long
        # options = webdriver.EdgeOptions()
        # options.add_argument('headless')
        # options.add_argument('log-level=3')
        # self.driver = webdriver.Edge(options=options)
        # self.driver.get("https://www.gps-coordinates.net/my-location")
        # print([e.text for e in self.driver.find_elements(By.ID, 'lat')])
        # with open('wp.html', 'w') as f: f.write(self.driver.page_source)

        # Setup night time only display
        self.ui.combo_timezones.addItems(pytz.common_timezones)
        self.tz = tzlocal.get_localzone_name()
        self.ui.combo_timezones.setCurrentIndex(pytz.common_timezones.index(self.tz))
        self.ui.combo_timezones.setEnabled(False)
        self.ui.combo_timezones.currentIndexChanged.connect(self.handleNewTimezone)
        self.ui.chk_timezone.stateChanged.connect(self.resetTimezone)
        self.cityinfo = astral.LocationInfo('name', 'region', self.tz, latitude=38.659686, longitude=-9.201254) # Set to Lisbon for now
        self.suninfo = astral.sun.sun(self.cityinfo.observer, date=datetime.date.today())

        self.night_mode_timer = QTimer()
        self.night_mode_timer.timeout.connect(self.checkNightTime)
        self.checkNightTime()
        self.night_mode_timer.setInterval(60_000)
        self.night_mode_timer.start()

        # Setup a daily sunrise / sunset update
        self.sun_update_timer = QTimer()
        self.sun_update_timer.timeout.connect(self.updateSunTimings)
        self.sun_update_timer.setSingleShot(True)
        self.updateSunTimings()

        self.show()

    def updateSunTimings(self):
        # Update
        self.suninfo = astral.sun.sun(self.cityinfo.observer, date=datetime.date.today())

        # Display
        sunset = self.suninfo['sunset'].strftime('%H:%M:%S')
        self.ui.sunset.setText(f'{sunset}')
        sunrise = self.suninfo['sunrise'].strftime('%H:%M:%S')
        self.ui.sunrise.setText(f'{sunrise}')
        
        # Re-schedule
        now = datetime.date.today()
        tomorrow = now + datetime.timedelta(days=1)
        tomorrow_midnight = datetime.datetime(year=tomorrow.year, month=tomorrow.month, day=tomorrow.day, hour=0, minute=0, second=0)
        delta = tomorrow_midnight - datetime.datetime.now()
        self.sun_update_timer.setInterval((delta.seconds + 10) * 1000) # +10 sec just to be sure with the datetime's stuff
        self.sun_update_timer.start()



    def resetTimezone(self, state):
        if state == 2:
            self.ui.combo_timezones.setCurrentIndex(pytz.common_timezones.index(tzlocal.get_localzone_name()))
        self.ui.combo_timezones.setEnabled(state != 2)

    def handleNewTimezone(self, idx):
        self.tz = self.ui.combo_timezones.itemText(idx)
        self.cityinfo = astral.LocationInfo('name', 'region', self.tz)
        print(self.cityinfo)
        self.suninfo = astral.sun.sun(self.cityinfo.observer, date=datetime.date.today())
        print(self.tz in astral.zoneinfo.available_timezones())
        sunset = self.suninfo['sunset'].strftime('%H:%M:%S')
        self.ui.sunset.setText(f'{sunset}')
        sunrise = self.suninfo['sunrise'].strftime('%H:%M:%S')
        self.ui.sunrise.setText(f'{sunrise}')

    def setNightModeState(self, state):
        self.night_mode = (state == 2)

        if self.night_mode:
            self.checkNightTime()
            self.night_mode_timer.start()

            # Enable ui
            if not self.ui.chk_timezone.isChecked():
                self.ui.combo_timezones.setEnabled(True)
            self.ui.chk_timezone.setEnabled(True)
            self.ui.sunrise.setEnabled(True)
            self.ui.sunset.setEnabled(True)
        else:
            self.night_mode_timer.stop()
            self.process_check_timer.start()

            # Disable ui
            self.ui.combo_timezones.setEnabled(False)
            self.ui.chk_timezone.setEnabled(False)
            self.ui.sunrise.setEnabled(False)
            self.ui.sunset.setEnabled(False)
    
    def isNight(self):
        return not self.suninfo['sunrise'] < datetime.datetime.now(tz=self.cityinfo.tzinfo) <= self.suninfo['sunset']
    
    def checkNightTime(self):
        self.activated = self.isNight()

        if self.activated:
            self.set_detection_bitmap(1)
            self.process_check_timer.start()
        else:
            self.set_detection_bitmap(0)
            self.process_check_timer.stop()

            if self.connected and self.detect_mode == 0:
                self.ui.attached_window_name.clear()
                self.enable_disable_connection()
            

    def closeEvent(self, event):
        self.process_check_timer.stop()
        if self.connected:
            self.connected = False
            self.socket.close()
            self.socket = None
            self.ui.btn_enable.setText('Enable')
            self.ipEnable(True)
            self.ui.statusbar.showMessage('Disconnected.')
            self.update_timer.stop()

    def checkFullscreenWindow(self):
        try:
            hWnd = self.windll.GetForegroundWindow()
            rect = win32gui.GetWindowRect(hWnd)
            title = win32gui.GetWindowText(hWnd)
            return (rect == (0, 0, self.windll.GetSystemMetrics(0), self.windll.GetSystemMetrics(1)), title)
        except:
            return (False, '')

    def handleDetectionMode(self, idx):
        self.set_detection_bitmap(2 if idx == 1 else 1 if self.activated else 0)
        if self.detect_mode == 0 and self.connected:
            self.enable_disable_connection()
        self.detect_mode = idx

    def handleProcessCheck(self):
        fullscreen_window, window_title = self.checkFullscreenWindow()

        # Manual active
        if self.detect_mode == 1:
            return

        # Already connected
        if self.connected:
            if not fullscreen_window:
                # print('Fullscreen app exited.')
                self.ui.attached_window_name.clear()
                self.enable_disable_connection()
            return

        # No fullscreen app
        if not fullscreen_window:
            return

        # For now work for every fullscreen app
        # print('Fullscreen app detected.')
        self.ui.attached_window_name.setText(window_title)
        self.enable_disable_connection()


    def handleMode(self, idx):
        self.mode = idx

    def handleBrightness(self):
        value = self.ui.brightness.value()
        if self.connected:
            self.set_brightness(int(value * 255 / 100))

    def handleBrightnessVC(self, value):
        self.ui.brightness_display.setValue(value)

    def avg_cols(self, Z, min, max):
        K = 1
        criteria = (cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, label, center = cv.kmeans(np.float32(Z[:, min:max].reshape((-1, 3))),K,None,criteria,2,cv.KMEANS_RANDOM_CENTERS)
        Z[:, min:max] = np.uint8(center)[label.flatten()].reshape(Z[:, min:max].shape)
        color = np.uint8(center)[0].tolist()
        if color[0] < 10 and color[1] < 10 and color[2] < 10:
            return [0, 0, 0]
        return np.uint8(center)[0].tolist()

    def saturate(self, Z, factor):
        hsv = cv.cvtColor(Z, cv.COLOR_BGR2HSV)
        hsv[..., 1] = np.clip(cv.multiply(hsv[..., 1], factor), 0, 255)
        return cv.cvtColor(hsv, cv.COLOR_HSV2BGR)

    def fix_value(self, Z, value):
        hsv = cv.cvtColor(Z, cv.COLOR_BGR2HSV)
        hsv[..., 2] = np.clip(value, 0, 255)
        return cv.cvtColor(hsv, cv.COLOR_HSV2BGR)

    def loop(self):
        screen = np.array(ImageGrab.grab())[:,:,::-1] # Convert to BGR
        screen = self.saturate(screen, factor=self.ui.sat_boost_factor.value()) # Saturate the colors
        screen = self.fix_value(screen, 128) # Fix the color's value
        ZR = cv.resize(screen, (640, 360)) # Resize for a lower res image (a bit like decimate)

        if self.mode == 0:
            Z = self.avg_cols(ZR, 0, 640)[::-1] # Convert back to RGB
            self.set_rgb_static(Z[0], Z[1], Z[2]) # Copy values to strip
        elif self.mode == 1:
            Z = np.array([self.avg_cols(ZR, i, i+40) for i in range(0, 640, 40)])[:, ::-1] # Convert back to RGB
            self.set_rgb_regions(Z.flatten().tolist()) # Copy values to strip
        
        self.ui.pallete_image.update(ZR) # Just show the average bands on the screen palette preview

    def ipEnable(self, status: bool):
        self.ip3.setEnabled(status)
        self.ip2.setEnabled(status)
        self.ip1.setEnabled(status)
        self.ip0.setEnabled(status)

    def enable_disable_connection(self, manual=False):
        if self.connected:
            self.connected = False
            self.socket.close()
            self.socket = None
            self.ui.btn_enable.setText('Enable')
            self.ipEnable(True)
            self.ui.statusbar.showMessage('Disconnected.')
            self.update_timer.stop()
            if manual:
                self.detect_mode = self.detect_mode_default
                self.ui.detection_mode.setCurrentIndex(self.detect_mode)
                self.ui.detection_mode.setEnabled(True)
                self.set_detection_bitmap(1 if self.activated else 0)
        else:
            if manual:
                self.detect_mode_default = self.ui.detection_mode.currentIndex()
                self.detect_mode = 1
                self.ui.detection_mode.setCurrentIndex(1)
                self.ui.detection_mode.setEnabled(False)
                self.set_detection_bitmap(2)
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Get ip
            ip = '.'.join([self.ip3.text(), self.ip2.text(), self.ip1.text(), self.ip0.text()])

            try:
                self.socket.connect((ip, 1338))
                self.connected = True
                self.ui.btn_enable.setText('Disable')
                self.ipEnable(False)
                self.ui.statusbar.showMessage('Connected.')
                self.update_timer.start()
                self.set_brightness(int(self.ui.brightness.value() * 255 / 100)) # 50%
            except socket.error as msg:
                self.statusbar.showMessage(f'{msg}')
                self.socket = None
                return

    def set_brightness(self, brightness: int):
        data = bytearray()
        data.append(0x05)
        data.append(brightness)
        self.socket.sendall(data)
        self.set_tx_status()

    def set_rgb_static(self, r: int, g: int, b: int):
        data = bytearray()
        data.append(0x02)
        data.append(r)
        data.append(g)
        data.append(b)
        self.socket.sendall(data)
        self.set_tx_status()

    def set_rgb_raw(self, values):
        data = bytearray()
        data.append(0x00)
        data.extend(values)
        self.socket.sendall(data)
        self.set_tx_status()

    def set_rgb_regions(self, regions):
        data = bytearray()
        data.append(0x06)
        data.append(len(regions) // 3)
        data.extend(regions)
        self.socket.sendall(data)
        self.set_tx_status()

    def set_detection_bitmap(self, status: int):
        if status == 0: 
            self.ui.detection_status.setPixmap(QtGui.QPixmap(Window.ICON_RED_LED))
            self.ui.detection_status.setToolTip('Detection disabled.')
        if status == 1: 
            self.ui.detection_status.setPixmap(QtGui.QPixmap(Window.ICON_GREEN_LED))
            self.ui.detection_status.setToolTip('Detection enabled.')
        if status == 2: 
            self.ui.detection_status.setPixmap(QtGui.QPixmap(Window.ICON_BLUE_LED))
            self.ui.detection_status.setToolTip('Manual mode engaged.')

    def set_tx_bitmap(self, status: bool):
        self.ui.tx_status.setPixmap(QtGui.QPixmap(Window.ICON_GREEN_LED if status else Window.ICON_RED_LED))

    def set_tx_status(self):
        self.set_tx_bitmap(True)
        self.timer_tx.start(25)
        

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme(corner_shape='sharp')
    window = Window()
    app.exec_()
