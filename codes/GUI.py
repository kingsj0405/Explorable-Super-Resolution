from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *

from MainWindow import Ui_MainWindow

import os
import random
import types

# EDitable SR imports:
from models import create_model
import options.options as option
import utils.util as util
from utils.logger import Logger
import data.util as data_util
import numpy as np
import torch
import qimage2ndarray
import cv2
import imageio
import matplotlib
import copy

BRUSH_MULT = 3
SPRAY_PAINT_MULT = 5
SPRAY_PAINT_N = 100
USE_SVD = True
VERBOSITY = False
MAX_SVD_LAMBDA = 1.
Z_OPTIMIZER_INITIAL_LR = 1e1
DISPLAY_GT_HR = True
DISPLAY_ESRGAN_RESULTS = True
DISPLAY_INDUCED_LR = False

COLORS = [
    '#000000', '#82817f', '#820300', '#868417', '#007e03', '#037e7b', '#040079',
    '#81067a', '#7f7e45', '#05403c', '#0a7cf6', '#093c7e', '#7e07f9', '#7c4002',

    '#ffffff', '#c1c1c1', '#f70406', '#fffd00', '#08fb01', '#0bf8ee', '#0000fa',
    '#b92fc2', '#fffc91', '#00fd83', '#87f9f9', '#8481c4', '#dc137d', '#fb803c',
]

FONT_SIZES = [7, 8, 9, 10, 11, 12, 13, 14, 18, 24, 36, 48, 64, 72, 96, 144, 288]

MODES = [
    'selectpoly', 'selectrect',
    #'eraser', 'fill',
    #'dropper', 'stamp',
    'pen',
    #'spray', 'text',
    #'line', #'rand_Z',#'polyline',
    #'rect',
    #'polygon',
    #'ellipse', 'roundrect'
]

CANVAS_DIMENSIONS = 600, 400

# STAMP_DIR = './stamps'
# STAMPS = [os.path.join(STAMP_DIR, f) for f in os.listdir(STAMP_DIR)]

SELECTION_PEN = QPen(QColor(0xff, 0xff, 0xff), 1, Qt.DashLine)
PREVIEW_PEN = QPen(QColor(0xff, 0xff, 0xff), 1, Qt.SolidLine)


def build_font(config):
    """
    Construct a complete font from the configuration options
    :param self:
    :param config:
    :return: QFont
    """
    font = config['font']
    font.setPointSize(config['fontsize'])
    font.setBold(config['bold'])
    font.setItalic(config['italic'])
    font.setUnderline(config['underline'])
    return font


class Canvas(QLabel):

    mode = 'rectangle'

    primary_color = QColor(Qt.black)
    secondary_color = None

    primary_color_updated = pyqtSignal(str)
    secondary_color_updated = pyqtSignal(str)

    # Store configuration settings, including pen width, fonts etc.
    config = {
        # Drawing options.
        'size': 1,
        'fill': True,
        # Font options.
        'font': QFont('Times'),
        'fontsize': 12,
        'bold': False,
        'italic': False,
        'underline': False,
    }

    active_color = None
    preview_pen = None

    timer_event = None

    current_stamp = None

    def initialize(self):
        self.background_color = QColor(self.secondary_color) if self.secondary_color else QColor(Qt.white)
        self.eraser_color = QColor(self.secondary_color) if self.secondary_color else QColor(Qt.white)
        self.eraser_color.setAlpha(100)
        self.reset()

    def reset(self,canvas_dimensions=CANVAS_DIMENSIONS):
        # Create the pixmap for display.
        self.setPixmap(QPixmap(*canvas_dimensions))

        # Clear the canvas.
        self.pixmap().fill(self.background_color)

    def set_primary_color(self, hex):
        self.primary_color = QColor(hex)

    def set_secondary_color(self, hex):
        self.secondary_color = QColor(hex)

    def set_config(self, key, value):
        self.config[key] = value

    def set_mode(self, mode):
        # Clean up active timer animations.
        self.timer_cleanup()
        # Reset mode-specific vars (all)
        self.active_shape_fn = None
        self.active_shape_args = ()

        self.origin_pos = None

        self.current_pos = None
        self.last_pos = None

        self.history_pos = None
        self.last_history = []

        self.current_text = ""
        self.last_text = ""

        self.last_config = {}

        self.dash_offset = 0
        self.locked = False
        # Apply the mode
        self.mode = mode

    def reset_mode(self):
        self.set_mode(self.mode)

    def on_timer(self):
        if self.timer_event:
            self.timer_event()

    def timer_cleanup(self):
        if self.timer_event:
            # Stop the timer, then trigger cleanup.
            timer_event = self.timer_event
            self.timer_event = None
            timer_event(final=True)

    # Mouse events.

    def mousePressEvent(self, e):
        fn = getattr(self, "%s_mousePressEvent" % self.mode, None)
        if fn:
            return fn(e)

    def mouseMoveEvent(self, e):
        fn = getattr(self, "%s_mouseMoveEvent" % self.mode, None)
        if fn:
            return fn(e)

    def mouseReleaseEvent(self, e):
        fn = getattr(self, "%s_mouseReleaseEvent" % self.mode, None)
        if fn:
            return fn(e)

    def mouseDoubleClickEvent(self, e):
        fn = getattr(self, "%s_mouseDoubleClickEvent" % self.mode, None)
        if fn:
            return fn(e)

    # Generic events (shared by brush-like tools)

    def generic_mousePressEvent(self, e):
        self.last_pos = e.pos()

        if e.button() == Qt.LeftButton:
            self.active_color = self.primary_color
        else:
            self.active_color = self.secondary_color

    def generic_mouseReleaseEvent(self, e):
        self.last_pos = None

    # Mode-specific events.

    # Select polygon events

    def selectpoly_mousePressEvent(self, e):
        if not self.locked or e.button == Qt.RightButton:
            self.active_shape_fn = 'drawPolygon'
            self.preview_pen = SELECTION_PEN
            self.generic_poly_mousePressEvent(e)

    def selectpoly_timerEvent(self, final=False):
        self.generic_poly_timerEvent(final)

    def selectpoly_mouseMoveEvent(self, e):
        if not self.locked:
            self.generic_poly_mouseMoveEvent(e)

    def selectpoly_mouseDoubleClickEvent(self, e):
        self.current_pos = e.pos()
        self.locked = True
        self.HR_selected_mask = np.zeros(self.HR_size)
        # self.LR_mask_vertices = [(p.x(),p.y()) for p in (self.history_pos + [self.current_pos])]
        self.LR_mask_vertices = [(int(np.round(p.x()/self.DTE_opt['scale'])),int(np.round(p.y()/self.DTE_opt['scale']))) for p in (self.history_pos + [self.current_pos])]
        HR_mask_vertices = [(coord[0]*self.DTE_opt['scale'],coord[1]*self.DTE_opt['scale']) for coord in self.LR_mask_vertices]
        self.HR_selected_mask = cv2.fillPoly(self.HR_selected_mask,[np.array(HR_mask_vertices)],(1,1,1))
        self.Z_mask = np.zeros(self.Z_size)
        # self.LR_mask_vertices = [(int(np.round(p[0]/self.DTE_opt['scale'])),int(np.round(p[1]/self.DTE_opt['scale']))) for p in self.LR_mask_vertices]
        if self.HR_Z:
            self.Z_mask = cv2.fillPoly(self.Z_mask, [np.array(HR_mask_vertices)], (1, 1, 1))
        else:
            self.Z_mask = cv2.fillPoly(self.Z_mask,[np.array(self.LR_mask_vertices)],(1,1,1))
        # self.Z_mask = cv2.fillPoly(self.Z_mask,[np.array([(int(p.x()/self.DTE_opt['scale']),int(p.y()/self.DTE_opt['scale'])) for p in (self.history_pos + [self.current_pos])])],(1,1,1))
        self.Update_Z_Sliders()
        self.Z_optimizer_Reset()
        # self.selectpoly_copy()#I add this to remove the dashed selection lines from the image, after I didn't find any better way. This removes it if done immediatly after selection, for some yet to be known reason

    def selectpoly_copy(self):
        """
        Copy a polygon region from the current image, returning it.

        Create a mask for the selected area, and use it to blank
        out non-selected regions. Then get the bounding rect of the
        selection and crop to produce the smallest possible image.

        :return: QPixmap of the copied region.
        """
        self.timer_cleanup()

        pixmap = self.pixmap().copy()
        bitmap = QBitmap(*CANVAS_DIMENSIONS)
        bitmap.clear()  # Starts with random data visible.

        p = QPainter(bitmap)
        # Construct a mask where the user selected area will be kept, the rest removed from the image is transparent.
        userpoly = QPolygon(self.history_pos + [self.current_pos])
        p.setPen(QPen(Qt.color1))
        p.setBrush(QBrush(Qt.color1))  # Solid color, Qt.color1 == bit on.
        p.drawPolygon(userpoly)
        p.end()

        # Set our created mask on the image.
        pixmap.setMask(bitmap)

        # Calculate the bounding rect and return a copy of that region.
        return pixmap.copy(userpoly.boundingRect())

    # Select rectangle events

    def selectrect_mousePressEvent(self, e):
        self.active_shape_fn = 'drawRect'
        self.preview_pen = SELECTION_PEN
        self.generic_shape_mousePressEvent(e)

    def selectrect_timerEvent(self, final=False):
        self.generic_shape_timerEvent(final)

    def selectrect_mouseMoveEvent(self, e):
        if not self.locked:
            self.current_pos = e.pos()

    def selectrect_mouseReleaseEvent(self, e):
        self.current_pos = e.pos()
        self.locked = True
        self.HR_selected_mask = np.zeros(self.HR_size)
        # self.LR_mask_vertices = [(p.x(),p.y()) for p in [self.origin_pos, self.current_pos]]
        self.LR_mask_vertices = [(int(np.round(p.x()/self.DTE_opt['scale'])),int(np.round(p.y()/self.DTE_opt['scale']))) for p in [self.origin_pos, self.current_pos]]
        HR_mask_vertices = [(coord[0]*self.DTE_opt['scale'],coord[1]*self.DTE_opt['scale']) for coord in self.LR_mask_vertices]
        self.HR_selected_mask = cv2.rectangle(self.HR_selected_mask,HR_mask_vertices[0],HR_mask_vertices[1],(1,1,1),cv2.FILLED)
        self.Z_mask = np.zeros(self.Z_size)
        # self.LR_mask_vertices = [(int(np.round(p[0]/self.DTE_opt['scale'])),int(np.round(p[1]/self.DTE_opt['scale']))) for p in self.LR_mask_vertices]
        if self.HR_Z:
            self.Z_mask = cv2.rectangle(self.Z_mask, HR_mask_vertices[0], HR_mask_vertices[1], (1, 1, 1),cv2.FILLED)
        else:
            self.Z_mask = cv2.rectangle(self.Z_mask,self.LR_mask_vertices[0],self.LR_mask_vertices[1],(1,1,1),cv2.FILLED)
        self.Update_Z_Sliders()
        self.Z_optimizer_Reset()
        # self.selectrect_copy()  # I add this to remove the dashed selection lines from the image, after I didn't find any better way. This removes it if done immediatly after selection, for some yet to be known reason

    def Z_optimizer_Reset(self):
        self.Z_optimizer_initial_LR = Z_OPTIMIZER_INITIAL_LR
        self.Z_optimizer = None
        self.Z_optimizer_logger = None

    def Update_Z_Sliders(self):
        self.sliderZ0.setSliderPosition(100*np.sum(self.lambda0.data.cpu().numpy()*self.Z_mask)/np.sum(self.Z_mask))
        self.sliderZ1.setSliderPosition(100*np.sum(self.lambda1.data.cpu().numpy()*self.Z_mask)/np.sum(self.Z_mask))
        self.third_latent_channel.setSliderPosition(100*np.sum(self.theta.data.cpu().numpy()*self.Z_mask)/np.sum(self.Z_mask))

    def selectrect_copy(self):
        """
        Copy a rectangle region of the current image, returning it.

        :return: QPixmap of the copied region.
        """
        self.timer_cleanup()
        return self.pixmap().copy(QRect(self.origin_pos, self.current_pos))

    # Eraser events

    def eraser_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)

    def eraser_mouseMoveEvent(self, e):
        if self.last_pos:
            p = QPainter(self.pixmap())
            p.setPen(QPen(self.eraser_color, 30, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(self.last_pos, e.pos())

            self.last_pos = e.pos()
            self.update()

    def eraser_mouseReleaseEvent(self, e):
        self.generic_mouseReleaseEvent(e)

    # Stamp (pie) events

    def stamp_mousePressEvent(self, e):
        p = QPainter(self.pixmap())
        stamp = self.current_stamp
        p.drawPixmap(e.x() - stamp.width() // 2, e.y() - stamp.height() // 2, stamp)
        self.update()

    # Pen events

    def pen_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)

    def pen_mouseMoveEvent(self, e):
        if self.last_pos:
            p = QPainter(self.pixmap())
            p.setPen(QPen(self.active_color, self.config['size'], Qt.SolidLine, Qt.SquareCap, Qt.RoundJoin))
            p.drawLine(self.last_pos, e.pos())

            self.last_pos = e.pos()
            self.update()

    def pen_mouseReleaseEvent(self, e):
        self.generic_mouseReleaseEvent(e)

    # Brush events

    def brush_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)

    def brush_mouseMoveEvent(self, e):
        if self.last_pos:
            p = QPainter(self.pixmap())
            p.setPen(QPen(self.active_color, self.config['size'] * BRUSH_MULT, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(self.last_pos, e.pos())

            self.last_pos = e.pos()
            self.update()

    def brush_mouseReleaseEvent(self, e):
        self.generic_mouseReleaseEvent(e)

    # Spray events

    def spray_mousePressEvent(self, e):
        self.generic_mousePressEvent(e)

    def spray_mouseMoveEvent(self, e):
        if self.last_pos:
            p = QPainter(self.pixmap())
            p.setPen(QPen(self.active_color, 1))

            for n in range(self.config['size'] * SPRAY_PAINT_N):
                xo = random.gauss(0, self.config['size'] * SPRAY_PAINT_MULT)
                yo = random.gauss(0, self.config['size'] * SPRAY_PAINT_MULT)
                p.drawPoint(e.x() + xo, e.y() + yo)

        self.update()

    def spray_mouseReleaseEvent(self, e):
        self.generic_mouseReleaseEvent(e)

    # Text events

    def keyPressEvent(self, e):
        if self.mode == 'text':
            if e.key() == Qt.Key_Backspace:
                self.current_text = self.current_text[:-1]
            else:
                self.current_text = self.current_text + e.text()

    def text_mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.current_pos is None:
            self.current_pos = e.pos()
            self.current_text = ""
            self.timer_event = self.text_timerEvent

        elif e.button() == Qt.LeftButton:

            self.timer_cleanup()
            # Draw the text to the image
            p = QPainter(self.pixmap())
            p.setRenderHints(QPainter.Antialiasing)
            font = build_font(self.config)
            p.setFont(font)
            pen = QPen(self.primary_color, 1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            p.drawText(self.current_pos, self.current_text)
            self.update()

            self.reset_mode()

        elif e.button() == Qt.RightButton and self.current_pos:
            self.reset_mode()

    def text_timerEvent(self, final=False):
        p = QPainter(self.pixmap())
        p.setCompositionMode(QPainter.RasterOp_SourceXorDestination)
        pen = PREVIEW_PEN
        p.setPen(pen)
        if self.last_text:
            font = build_font(self.last_config)
            p.setFont(font)
            p.drawText(self.current_pos, self.last_text)

        if not final:
            font = build_font(self.config)
            p.setFont(font)
            p.drawText(self.current_pos, self.current_text)

        self.last_text = self.current_text
        self.last_config = self.config.copy()
        self.update()

    # Fill events

    def fill_mousePressEvent(self, e):

        if e.button() == Qt.LeftButton:
            self.active_color = self.primary_color
        else:
            self.active_color = self.secondary_color

        image = self.pixmap().toImage()
        w, h = image.width(), image.height()
        x, y = e.x(), e.y()

        # Get our target color from origin.
        target_color = image.pixel(x,y)

        have_seen = set()
        queue = [(x, y)]

        def get_cardinal_points(have_seen, center_pos):
            points = []
            cx, cy = center_pos
            for x, y in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
                xx, yy = cx + x, cy + y
                if (xx >= 0 and xx < w and
                    yy >= 0 and yy < h and
                    (xx, yy) not in have_seen):

                    points.append((xx, yy))
                    have_seen.add((xx, yy))

            return points

        # Now perform the search and fill.
        p = QPainter(self.pixmap())
        p.setPen(QPen(self.active_color))

        while queue:
            x, y = queue.pop()
            if image.pixel(x, y) == target_color:
                p.drawPoint(QPoint(x, y))
                queue.extend(get_cardinal_points(have_seen, (x, y)))

        self.update()

    # Dropper events

    def dropper_mousePressEvent(self, e):
        c = self.pixmap().toImage().pixel(e.pos())
        hex = QColor(c).name()

        if e.button() == Qt.LeftButton:
            self.set_primary_color(hex)
            self.primary_color_updated.emit(hex)  # Update UI.

        elif e.button() == Qt.RightButton:
            self.set_secondary_color(hex)
            self.secondary_color_updated.emit(hex)  # Update UI.

    # Generic shape events: Rectangle, Ellipse, Rounded-rect

    def generic_shape_mousePressEvent(self, e):
        self.origin_pos = e.pos()
        self.current_pos = e.pos()
        self.timer_event = self.generic_shape_timerEvent

    def generic_shape_timerEvent(self, final=False):
        p = QPainter(self.pixmap())
        p.setCompositionMode(QPainter.RasterOp_SourceXorDestination)
        pen = self.preview_pen
        pen.setDashOffset(self.dash_offset)
        p.setPen(pen)
        if self.last_pos:
            getattr(p, self.active_shape_fn)(QRect(self.origin_pos, self.last_pos), *self.active_shape_args)

        if not final:
            self.dash_offset -= 1
            pen.setDashOffset(self.dash_offset)
            p.setPen(pen)
            getattr(p, self.active_shape_fn)(QRect(self.origin_pos, self.current_pos), *self.active_shape_args)
        # else:
        #     print('Now its final')
            # print(self.current_pos)
        # self.dash_offset = 0

        self.update()
        self.last_pos = self.current_pos

    def generic_shape_mouseMoveEvent(self, e):
        self.current_pos = e.pos()

    def generic_shape_mouseReleaseEvent(self, e):
        if self.last_pos:
            # Clear up indicator.
            self.timer_cleanup()

            p = QPainter(self.pixmap())
            p.setPen(QPen(self.primary_color, self.config['size'], Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))

            if self.config['fill']:
                p.setBrush(QBrush(self.secondary_color))
            getattr(p, self.active_shape_fn)(QRect(self.origin_pos, e.pos()), *self.active_shape_args)
            self.update()

        self.reset_mode()

    # Line events

    def line_mousePressEvent(self, e):
        self.origin_pos = e.pos()
        self.current_pos = e.pos()
        self.preview_pen = PREVIEW_PEN
        self.timer_event = self.line_timerEvent

    def line_timerEvent(self, final=False):
        p = QPainter(self.pixmap())
        p.setCompositionMode(QPainter.RasterOp_SourceXorDestination)
        pen = self.preview_pen
        p.setPen(pen)
        if self.last_pos:
            p.drawLine(self.origin_pos, self.last_pos)

        if not final:
            p.drawLine(self.origin_pos, self.current_pos)

        self.update()
        self.last_pos = self.current_pos

    def line_mouseMoveEvent(self, e):
        self.current_pos = e.pos()

    def line_mouseReleaseEvent(self, e):
        if self.last_pos:
            # Clear up indicator.
            self.timer_cleanup()

            p = QPainter(self.pixmap())
            p.setPen(QPen(self.primary_color, self.config['size'], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

            p.drawLine(self.origin_pos, e.pos())
            self.update()

        self.reset_mode()

    # Generic poly events
    def generic_poly_mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.history_pos:
                self.history_pos.append(e.pos())
            else:
                self.history_pos = [e.pos()]
                self.current_pos = e.pos()
                self.timer_event = self.generic_poly_timerEvent

        elif e.button() == Qt.RightButton and self.history_pos:
            # Clean up, we're not drawing
            self.timer_cleanup()
            self.reset_mode()

    def generic_poly_timerEvent(self, final=False):
        p = QPainter(self.pixmap())
        p.setCompositionMode(QPainter.RasterOp_SourceXorDestination)
        pen = self.preview_pen
        pen.setDashOffset(self.dash_offset)
        p.setPen(pen)
        if self.last_history:
            getattr(p, self.active_shape_fn)(*self.last_history)

        if not final:
            self.dash_offset -= 1
            pen.setDashOffset(self.dash_offset)
            p.setPen(pen)
            getattr(p, self.active_shape_fn)(*self.history_pos + [self.current_pos])

        self.update()
        self.last_pos = self.current_pos
        self.last_history = self.history_pos + [self.current_pos]

    def generic_poly_mouseMoveEvent(self, e):
        self.current_pos = e.pos()

    def generic_poly_mouseDoubleClickEvent(self, e):
        self.timer_cleanup()
        p = QPainter(self.pixmap())
        p.setPen(QPen(self.primary_color, self.config['size'], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

        # Note the brush is ignored for polylines.
        if self.secondary_color:
            p.setBrush(QBrush(self.secondary_color))

        getattr(p, self.active_shape_fn)(*self.history_pos + [e.pos()])
        self.update()
        self.reset_mode()

    # Polyline events

    def polyline_mousePressEvent(self, e):
        self.active_shape_fn = 'drawPolyline'
        self.preview_pen = PREVIEW_PEN
        self.generic_poly_mousePressEvent(e)

    def polyline_timerEvent(self, final=False):
        self.generic_poly_timerEvent(final)

    def polyline_mouseMoveEvent(self, e):
        self.generic_poly_mouseMoveEvent(e)

    def polyline_mouseDoubleClickEvent(self, e):
        self.generic_poly_mouseDoubleClickEvent(e)

    # Rectangle events

    def rect_mousePressEvent(self, e):
        self.active_shape_fn = 'drawRect'
        self.active_shape_args = ()
        self.preview_pen = PREVIEW_PEN
        self.generic_shape_mousePressEvent(e)

    def rect_timerEvent(self, final=False):
        self.generic_shape_timerEvent(final)

    def rect_mouseMoveEvent(self, e):
        self.generic_shape_mouseMoveEvent(e)

    def rect_mouseReleaseEvent(self, e):
        self.generic_shape_mouseReleaseEvent(e)

    # Polygon events

    def polygon_mousePressEvent(self, e):
        self.active_shape_fn = 'drawPolygon'
        self.preview_pen = PREVIEW_PEN
        self.generic_poly_mousePressEvent(e)

    def polygon_timerEvent(self, final=False):
        self.generic_poly_timerEvent(final)

    def polygon_mouseMoveEvent(self, e):
        self.generic_poly_mouseMoveEvent(e)

    def polygon_mouseDoubleClickEvent(self, e):
        self.generic_poly_mouseDoubleClickEvent(e)

    # Ellipse events

    def ellipse_mousePressEvent(self, e):
        self.active_shape_fn = 'drawEllipse'
        self.active_shape_args = ()
        self.preview_pen = PREVIEW_PEN
        self.generic_shape_mousePressEvent(e)

    def ellipse_timerEvent(self, final=False):
        self.generic_shape_timerEvent(final)

    def ellipse_mouseMoveEvent(self, e):
        self.generic_shape_mouseMoveEvent(e)

    def ellipse_mouseReleaseEvent(self, e):
        self.generic_shape_mouseReleaseEvent(e)

    # Roundedrect events

    def roundrect_mousePressEvent(self, e):
        self.active_shape_fn = 'drawRoundedRect'
        self.active_shape_args = (25, 25)
        self.preview_pen = PREVIEW_PEN
        self.generic_shape_mousePressEvent(e)

    def roundrect_timerEvent(self, final=False):
        self.generic_shape_timerEvent(final)

    def roundrect_mouseMoveEvent(self, e):
        self.generic_shape_mouseMoveEvent(e)

    def roundrect_mouseReleaseEvent(self, e):
        self.generic_shape_mouseReleaseEvent(e)


class MainWindow(QMainWindow, Ui_MainWindow):

    def __init__(self, *args, **kwargs):
        super(MainWindow, self).__init__(*args, **kwargs)
        available_GPUs = util.Assign_GPU()
        self.setupUi(self)

        # Editable SR:
        opt = option.parse('./options/test/GUI_esrgan.json', is_train=False)
        opt = option.dict_to_nonedict(opt)
        self.SR_model = create_model(opt,init_Dnet=True)
        matplotlib.use('Qt5Agg')
        matplotlib.interactive(True)

        self.saved_outputs_counter = 0
        self.desired_hist_image = None
        self.auto_set_hist_temperature = False

        # Replace canvas placeholder from QtDesigner.
        self.horizontalLayout.removeWidget(self.canvas)
        self.canvas = Canvas()
        self.canvas.Z_optimizer_Reset()
        self.canvas.DTE_opt = opt
        self.canvas.initialize()
        self.canvas.HR_Z = 'HR' in self.canvas.DTE_opt['network_G']['latent_input_domain']

        # We need to enable mouse tracking to follow the mouse without the button pressed.
        self.canvas.setMouseTracking(True)
        # Enable focus to capture key inputs.
        self.canvas.setFocusPolicy(Qt.StrongFocus)
        self.horizontalLayout.addWidget(self.canvas)

        if DISPLAY_GT_HR:
            #Add a 2nd canvas:
            self.GT_canvas = Canvas()
            self.GT_canvas.initialize()
            self.horizontalLayout.addWidget(self.GT_canvas)
        if DISPLAY_ESRGAN_RESULTS:
            self.ESRGAN_canvas = Canvas()
            self.ESRGAN_canvas.initialize()
            self.horizontalLayout.addWidget(self.ESRGAN_canvas)

        if DISPLAY_INDUCED_LR:
            #Add a 3rd canvas:
            self.LR_canvas = Canvas()
            self.LR_canvas.initialize()
            self.horizontalLayout.addWidget(self.LR_canvas)

        # Setup the mode buttons
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)

        for mode in MODES:
            btn = getattr(self, '%sButton' % mode)
            btn.pressed.connect(lambda mode=mode: self.canvas.set_mode(mode))
            mode_group.addButton(btn)

        # Setup the color selection buttons.
        # self.primaryButton.pressed.connect(lambda: self.choose_color(self.set_primary_color))
        # self.secondaryButton.pressed.connect(lambda: self.choose_color(self.set_secondary_color))

        # Initialize button colours.
        # for n, hex in enumerate(COLORS, 1):
        #     btn = getattr(self, 'colorButton_%d' % n)
        #     btn.setStyleSheet('QPushButton { background-color: %s; }' % hex)
        #     btn.hex = hex  # For use in the event below
        #
        #     def patch_mousePressEvent(self_, e):
        #         if e.button() == Qt.LeftButton:
        #             self.set_primary_color(self_.hex)
        #
        #         elif e.button() == Qt.RightButton:
        #             self.set_secondary_color(self_.hex)
        #
        #     btn.mousePressEvent = types.MethodType(patch_mousePressEvent, btn)

        # Setup up action signals
        self.actionCopy.triggered.connect(self.copy_to_clipboard)

        # Initialize animation timer.
        self.timer = QTimer()
        self.timer.timeout.connect(self.canvas.on_timer)
        self.timer.setInterval(100)
        self.timer.start()

        # Setup to agree with Canvas.
        # self.set_primary_color('#000000')
        # self.set_secondary_color('#ffffff')

        # # Signals for canvas-initiated color changes (dropper).
        # self.canvas.primary_color_updated.connect(self.set_primary_color)
        # self.canvas.secondary_color_updated.connect(self.set_secondary_color)

        # Setup the stamp state.
        self.current_stamp_n = -1
        # self.next_stamp()
        # self.stampnextButton.pressed.connect(self.next_stamp)

        # Menu options
        self.actionNewImage.triggered.connect(self.canvas.initialize)
        self.actionOpenImage.triggered.connect(self.open_file)

        self.actionProcessRandZ.triggered.connect(self.Process_Random_Z)
        self.actionIncreaseSTD.triggered.connect(lambda x:self.Optimize_Z('max_STD'))
        self.actionIDecreaseSTD.triggered.connect(lambda x:self.Optimize_Z('min_STD'))
        self.actionImitateHist.triggered.connect(lambda x:self.Optimize_Z('hist'))
        self.actionImitatePatchHist.triggered.connect(lambda x:self.Optimize_Z('patchhist'))
        self.actionFoolAdversary.triggered.connect(lambda x:self.Optimize_Z('Adversarial'))
        self.actionMatchSliders.triggered.connect(lambda x:self.Optimize_Z('desired_SVD'))

        self.UnselectButton.clicked.connect(self.Clear_Z_Mask)
        self.invertSelectionButton.clicked.connect(self.Invert_Z_Mask)
        self.desiredHistModeButton.clicked.connect(lambda checked: self.DesiredHistMode(checked,another_image=False))
        self.desiredImageHistModeButton.clicked.connect(lambda checked: self.DesiredHistMode(checked,another_image=True))
        self.auto_hist_temperature_mode_button.clicked.connect(lambda checked:self.AutoHistTemperatureMode(checked))

        # self.actionReProcess.triggered.connect(self.ReProcess)

        self.actionSaveImage.triggered.connect(self.save_file)
        self.actionAutoSaveImage.triggered.connect(self.save_file_and_Z_map)
        self.actionClearImage.triggered.connect(self.canvas.reset)
        self.actionInvertColors.triggered.connect(self.invert)
        self.actionFlipHorizontal.triggered.connect(self.flip_horizontal)
        self.actionFlipVertical.triggered.connect(self.flip_vertical)

        # Setup the drawing toolbar.
        # self.fontselect = QFontComboBox()
        # self.fontToolbar.addWidget(self.fontselect)
        # self.fontselect.currentFontChanged.connect(lambda f: self.canvas.set_config('font', f))
        # self.fontselect.setCurrentFont(QFont('Times'))

        # self.fontsize = QComboBox()
        # self.fontsize.addItems([str(s) for s in FONT_SIZES])
        # self.fontsize.currentTextChanged.connect(lambda f: self.canvas.set_config('fontsize', int(f)))

        # Connect to the signal producing the text of the current selection. Convert the string to float
        # and set as the pointsize. We could also use the index + retrieve from FONT_SIZES.
        # self.fontToolbar.addWidget(self.fontsize)
        #
        # self.fontToolbar.addAction(self.actionBold)
        # self.actionBold.triggered.connect(lambda s: self.canvas.set_config('bold', s))
        # self.fontToolbar.addAction(self.actionItalic)
        # self.actionItalic.triggered.connect(lambda s: self.canvas.set_config('italic', s))
        # self.fontToolbar.addAction(self.actionUnderline)
        # self.actionUnderline.triggered.connect(lambda s: self.canvas.set_config('underline', s))

        sizeicon = QLabel()
        sizeicon.setPixmap(QPixmap(os.path.join('images', 'border-weight.png')))
        # self.drawingToolbar.addWidget(sizeicon)
        self.sizeselect = QSlider()
        self.sizeselect.setRange(1,20)
        self.sizeselect.setOrientation(Qt.Horizontal)
        self.sizeselect.valueChanged.connect(lambda s: self.canvas.set_config('size', s))
        # self.drawingToolbar.addWidget(self.sizeselect)
        # if USE_SVD:
        #     # self.canvas.lambda0 = torch.tensor(0.5)#*np.ones(self.canvas.LR_size)
        #     # self.canvas.lambda1 = torch.tensor(0.5)#*np.ones(self.canvas.LR_size)
        #     # self.canvas.theta = torch.tensor(0)#*np.ones(self.canvas.LR_size)
        #     self.SetZ(0.5,0)#*np.ones(self.canvas.LR_size)
        #     self.SetZ(0.5,1)#*np.ones(self.canvas.LR_size)
        #     self.SetZ(0,2)#*np.ones(self.canvas.LR_size)
        #     if VERBOSITY:
        #         self.latent_mins = 100*torch.ones([1,3,1,1])
        #         self.latent_maxs = -100*torch.ones([1,3,1,1])
        self.sliderZ0 = QSlider()
        self.sliderZ0.setObjectName('sliderZ0')
        if USE_SVD:
            self.sliderZ0.setRange(0, 100*MAX_SVD_LAMBDA)
            self.sliderZ0.setSliderPosition(100*MAX_SVD_LAMBDA/2)
        else:
            self.sliderZ0.setRange(-100,100)
        self.sliderZ0.setSingleStep(1)
        self.sliderZ0.setOrientation(Qt.Vertical)
        self.sliderZ0.sliderMoved.connect(lambda s: self.SetZ_And_Display(value=s / 100, index=0))
        self.sliderZ0.sliderReleased.connect(self.Remember_Zmap)
        self.ZToolbar.addWidget(self.sliderZ0)
        self.sliderZ1 = QSlider()
        self.sliderZ1.setObjectName('sliderZ1')
        if USE_SVD:
            self.sliderZ1.setRange(0, 100*MAX_SVD_LAMBDA)
            self.sliderZ1.setSliderPosition(100*MAX_SVD_LAMBDA/2)
        else:
            self.sliderZ1.setRange(-100,100)
        self.sliderZ1.setSingleStep(1)
        self.sliderZ1.setOrientation(Qt.Vertical)
        self.sliderZ1.sliderMoved.connect(lambda s: self.SetZ_And_Display(value=s / 100, index=1))
        self.sliderZ1.sliderReleased.connect(self.Remember_Zmap)
        self.ZToolbar.addWidget(self.sliderZ1)
        if USE_SVD:
            self.third_latent_channel = QDial()
            self.third_latent_channel.setWrapping(True)
            self.third_latent_channel.setNotchesVisible(True)
        else:
            self.third_latent_channel = QSlider()
        self.third_latent_channel.setObjectName('third_latent_channel')
        if USE_SVD:
            self.third_latent_channel.setRange(-100*np.pi, 100*np.pi)
        else:
            self.third_latent_channel.setRange(-100,100)
        self.third_latent_channel.setSingleStep(1)
        self.third_latent_channel.setOrientation(Qt.Vertical)
        self.third_latent_channel.sliderMoved.connect(lambda s: self.SetZ_And_Display(value=s / 100, index=2))
        # self.third_latent_channel.sliderReleased.connect(lambda s=self.third_latent_channel.sliderPosition():self.SetZ(value=self.third_latent_channel.sliderPosition()/100,index=2))
        self.third_latent_channel.sliderReleased.connect(self.Remember_Zmap)
        self.ZToolbar.addWidget(self.third_latent_channel)
        self.ZToolbar.addAction(self.actionProcessRandZ)
        self.ZToolbar.insertSeparator(self.actionProcessRandZ)
        self.ZToolbar.addAction(self.actionIncreaseSTD)
        self.ZToolbar.addAction(self.actionIDecreaseSTD)
        self.ZToolbar.addAction(self.actionImitateHist)
        self.ZToolbar.addAction(self.actionImitatePatchHist)
        self.ZToolbar.addAction(self.actionFoolAdversary)
        self.ZToolbar.addAction(self.actionMatchSliders)

        self.canvas.sliderZ0 = self.sliderZ0
        self.canvas.sliderZ1 = self.sliderZ1
        self.canvas.third_latent_channel = self.third_latent_channel

        self.actionFillShapes.triggered.connect(lambda s: self.canvas.set_config('fill', s))
        # self.drawingToolbar.addAction(self.actionFillShapes)
        self.actionFillShapes.setChecked(True)
        self.open_file()
        self.show()

    # def choose_color(self, callback):
    #     dlg = QColorDialog()
    #     if dlg.exec():
    #         callback( dlg.selectedColor().name() )
    #
    # def set_primary_color(self, hex):
    #     self.canvas.set_primary_color(hex)
    #     self.primaryButton.setStyleSheet('QPushButton { background-color: %s; }' % hex)
    #
    # def set_secondary_color(self, hex):
    #     self.canvas.set_secondary_color(hex)
    #     self.secondaryButton.setStyleSheet('QPushButton { background-color: %s; }' % hex)

    # def next_stamp(self):
    #     self.current_stamp_n += 1
    #     if self.current_stamp_n >= len(STAMPS):
    #         self.current_stamp_n = 0
    #
    #     pixmap = QPixmap(STAMPS[self.current_stamp_n])
    #     self.stampnextButton.setIcon(QIcon(pixmap))
    #
    #     self.canvas.current_stamp = pixmap
    def Remember_Zmap(self):
        pass#I can use this to add current Z-map to some deque to enable undo and redo
    def AutoHistTemperatureMode(self,checked):
        if checked:
            self.auto_set_hist_temperature = True
            self.canvas.Z_optimizer_Reset()
        else:
            self.auto_set_hist_temperature = False

    def DesiredHistMode(self,checked,another_image):
        if checked:
            self.MasksStorage(True)
            self.canvas.HR_selected_mask = np.ones(self.canvas.HR_size)
            if another_image:
                path, _ = QFileDialog.getOpenFileName(self, "Desired image for histogram imitation", "",
                                                      "PNG image files (*.png); JPEG image files (*jpg); All files (*.*)")
                if path:
                    self.desired_hist_image = data_util.read_img(None, path)
                    if self.desired_hist_image.shape[2] == 3:
                        self.desired_hist_image = self.desired_hist_image[:, :, [2, 1, 0]]
                    pixmap = QPixmap()
                    pixmap.convertFromImage(qimage2ndarray.array2qimage(255*self.desired_hist_image))
                    self.canvas.setPixmap(pixmap)
                    self.canvas.setGeometry(QRect(0,0,self.desired_hist_image.shape[0],self.desired_hist_image.shape[1]))
            else:
                self.desired_hist_image = self.SR_model.fake_H[0].data.cpu().numpy().transpose(1,2,0)

        else:
            self.desired_hist_image_HR_mask = 1*self.canvas.HR_selected_mask
            self.MasksStorage(False)
            self.Update_Image_Display()

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()

        if self.canvas.mode == 'selectrect' and self.canvas.locked:
            clipboard.setPixmap(self.canvas.selectrect_copy())

        elif self.canvas.mode == 'selectpoly' and self.canvas.locked:
            clipboard.setPixmap(self.canvas.selectpoly_copy())

        else:
            clipboard.setPixmap(self.canvas.pixmap())
    def Compute_SR_Image(self):
        if self.cur_Z.size(2)==1:
            self.SR_model.cur_Z = ((self.cur_Z * torch.ones([1, 1] + self.canvas.Z_size) - 0.5) * 2).type(self.var_L.type())
        else:
            self.SR_model.cur_Z = self.cur_Z.type(self.var_L.type())
        self.SR_model.Assing_LR_and_Latent(LR_image=self.var_L,latent_input=self.SR_model.cur_Z)
        # self.SR_model.var_L = torch.cat([self.SR_model.cur_Z, self.var_L], dim=1)
        self.SR_model.netG.eval()
        with torch.no_grad():
            self.SR_model.fake_H = self.SR_model.netG(self.SR_model.var_L)
            if DISPLAY_INDUCED_LR:
                self.induced_LR_image = self.SR_model.netG.module.DownscaleOP(self.SR_model.fake_H)


    def DrawRandChannel(self,min_val,max_val,uniform=False):
        return (max_val-min_val)*torch.rand([1,1]+([1,1] if uniform else self.canvas.Z_size))+min_val

    def Process_Random_Z(self):
        UNIFORM_RANDOM = False
        Z_mask = torch.from_numpy(self.canvas.Z_mask).type(self.cur_Z.dtype)
        if USE_SVD:
            self.canvas.lambda0 = Z_mask*self.DrawRandChannel(0,MAX_SVD_LAMBDA,uniform=UNIFORM_RANDOM).squeeze(0).squeeze(0)+(1-Z_mask)*self.canvas.lambda0
            self.canvas.lambda1 = Z_mask*self.DrawRandChannel(0,MAX_SVD_LAMBDA,uniform=UNIFORM_RANDOM).squeeze(0).squeeze(0)+(1-Z_mask)*self.canvas.lambda1
            self.canvas.theta = Z_mask*self.DrawRandChannel(0,np.pi,uniform=UNIFORM_RANDOM).squeeze(0).squeeze(0)+(1-Z_mask)*self.canvas.theta
            self.Recompose_cur_Z()
            self.canvas.Update_Z_Sliders()
        else:
            random_Z = (torch.rand([1,self.SR_model.num_latent_channels]+self.canvas.Z_size)-0.5)*2
            self.cur_Z = Z_mask*random_Z+(1-Z_mask)*self.cur_Z
        self.ReProcess()
    def Validate_Z_optimizer(self,objective):
        if self.canvas.Z_optimizer is not None:
            if self.canvas.Z_optimizer.objective!=objective:# or objective=='hist': # Resetting optimizer in the 'patchhist' case because I use automatic tempersture search there, so I want to search each time for the best temperature.
                self.canvas.Z_optimizer_Reset()

    def MasksStorage(self,store):
        if store:
            self.stored_Z_mask = 1*self.canvas.Z_mask
            self.stored_HR_selected_mask = 1*self.canvas.HR_selected_mask
            self.stored_mask_vertices = 1*self.canvas.LR_mask_vertices
            self.stored_cur_Z = 1*self.cur_Z
            self.stored_var_L = 1*self.var_L
        else:
            self.canvas.Z_mask = 1*self.stored_Z_mask
            self.canvas.HR_selected_mask = 1*self.stored_HR_selected_mask
            self.canvas.LR_mask_vertices = 1*self.stored_mask_vertices
            self.cur_Z = 1*self.stored_cur_Z
            self.var_L = 1*self.stored_var_L

    def SVD_ValuesStorage(self,store):
        if store:
            self.stored_lambda0 = 1*self.canvas.lambda0
            self.stored_lambda1 = 1*self.canvas.lambda1
            self.stored_theta = 1*self.canvas.theta
        else:
            self.canvas.lambda0 = 1*self.stored_lambda0
            self.canvas.lambda1 = 1*self.stored_lambda1
            self.canvas.theta = 1*self.stored_theta

    def Crop2BoundingRect(self,arrays,bounding_rect,HR=False):
        operating_on_list = isinstance(arrays,list)
        if not operating_on_list:
            arrays = [arrays]
        if HR:
            bounding_rect = self.canvas.DTE_opt['scale'] * bounding_rect
        bounding_rect = 1 * bounding_rect
        arrays_2_return = []
        for array in arrays:
            if isinstance(array,np.ndarray):
                arrays_2_return.append(array[bounding_rect[1]:bounding_rect[1]+bounding_rect[3],bounding_rect[0]:bounding_rect[0]+bounding_rect[2]])
            elif torch.is_tensor(array):
                if array.dim()==4:
                    arrays_2_return.append(array[:,:,bounding_rect[1]:bounding_rect[1] + bounding_rect[3],bounding_rect[0]:bounding_rect[0] + bounding_rect[2]])
                elif array.dim()==2:
                    arrays_2_return.append(array[bounding_rect[1]:bounding_rect[1] + bounding_rect[3],bounding_rect[0]:bounding_rect[0] + bounding_rect[2]])
                else:
                    raise Exception('Unsupported')
        return arrays_2_return if operating_on_list else arrays_2_return[0]

    def Set_Extreme_SVD_Values(self,min_not_max):
        self.SetZ(0 if min_not_max else 1,0,reset_optimizer=False) # I'm using 1 as maximal value and not MAX_LAMBDA_VAL because I want these images to correspond to Z=[-1,1] like in the model training. Different maximal Lambda values will be manifested in the Z optimization itself when cur_Z is normalized.
        self.SetZ(0 if min_not_max else 1, 1,reset_optimizer=False)

    def Optimize_Z(self,objective):
        ITERS_PER_ROUND = 5
        D_EXPECTED_LR_SIZE = 64
        MARGINS_AROUND_REGION_OF_INTEREST = 5
        self.Validate_Z_optimizer(objective)
        data = {'LR':self.var_L}
        if self.canvas.Z_optimizer is None:
            if not np.all(self.canvas.HR_selected_mask):#Cropping an image region to be optimized, to save on computations and allow adversarial loss
                self.optimizing_region = True
                self.bounding_rect = np.array(cv2.boundingRect(np.stack([list(p) for p in self.canvas.LR_mask_vertices],1).transpose()))
                if np.all(self.bounding_rect[2:]<=D_EXPECTED_LR_SIZE-2*MARGINS_AROUND_REGION_OF_INTEREST) or objective=='Adversarial':
                    #Use this D_EXPECTED_LR_SIZE LR_image cropped size when the region of interest is smaller, or when using D that can only work with this size (non mapGAN)
                    gaps = D_EXPECTED_LR_SIZE-self.bounding_rect[2:]
                    self.bounding_rect = np.concatenate([np.maximum(self.bounding_rect[:2]-gaps//2,0),np.array(2*[D_EXPECTED_LR_SIZE])])
                else:
                    self.bounding_rect = np.concatenate([np.maximum(self.bounding_rect[:2]-MARGINS_AROUND_REGION_OF_INTEREST//2,0),self.bounding_rect[2:]+MARGINS_AROUND_REGION_OF_INTEREST])
                self.bounding_rect[:2] = np.maximum([0,0],np.minimum(self.bounding_rect[:2]+self.bounding_rect[2:],self.canvas.LR_size[::-1])-self.bounding_rect[2:])
                self.bounding_rect[2:] = np.minimum(self.bounding_rect[:2]+self.bounding_rect[2:],self.canvas.LR_size[::-1])-self.bounding_rect[:2]
                self.MasksStorage(True)
                self.canvas.HR_selected_mask = self.Crop2BoundingRect(self.canvas.HR_selected_mask,self.bounding_rect,HR=True)
                self.canvas.Z_mask = self.Crop2BoundingRect(self.canvas.Z_mask,self.bounding_rect,HR=self.canvas.HR_Z)
                self.var_L = self.Crop2BoundingRect(self.var_L, self.bounding_rect)
                data['LR'] = self.var_L
                self.SR_model.cur_Z = self.Crop2BoundingRect(self.SR_model.cur_Z,self.bounding_rect,HR=self.canvas.HR_Z)#Because I'm saving initial Z when initializing optimizer
                self.cur_Z = self.Crop2BoundingRect(self.cur_Z,self.bounding_rect,HR=self.canvas.HR_Z)
            else:
                self.optimizing_region = False
                # self.bounding_rect = np.array([0,0]+self.canvas.LR_size[::-1])

            if 'hist' in objective:
                if self.desired_hist_image is None:
                    return
                data['HR'] = torch.from_numpy(np.ascontiguousarray(np.transpose(self.desired_hist_image, (2, 0, 1)))).float().to(self.SR_model.device).unsqueeze(0)
                data['Desired_Im_Mask'] = self.desired_hist_image_HR_mask
            elif 'desired_SVD' in objective:
                data['desired_Z'] = util.SVD_2_LatentZ(torch.stack([self.canvas.lambda0,self.canvas.lambda1,self.canvas.theta],0).unsqueeze(0),max_lambda=MAX_SVD_LAMBDA)
                self.SVD_ValuesStorage(True)
                if self.optimizing_region:
                    data['desired_Z'] = self.Crop2BoundingRect(data['desired_Z'],self.bounding_rect,HR=self.canvas.HR_Z)
                    self.canvas.lambda0,self.canvas.lambda1,self.canvas.theta = self.Crop2BoundingRect([self.canvas.lambda0,self.canvas.lambda1,self.canvas.theta],self.bounding_rect,HR=self.canvas.HR_Z)
                self.Set_Extreme_SVD_Values(min_not_max=True)
                data['reference_image_min'] = 1*self.SR_model.fake_H
                self.Set_Extreme_SVD_Values(min_not_max=False)
                data['reference_image_max'] = 1*self.SR_model.fake_H
                self.SVD_ValuesStorage(False)
            if self.canvas.Z_optimizer_logger is None:
                self.canvas.Z_optimizer_logger = Logger(self.canvas.DTE_opt)
            self.canvas.Z_optimizer = util.Z_optimizer(objective=objective,Z_size=self.canvas.Z_size,model=self.SR_model,Z_range=MAX_SVD_LAMBDA,data=data,
                initial_LR=self.canvas.Z_optimizer_initial_LR,logger=self.canvas.Z_optimizer_logger,max_iters=ITERS_PER_ROUND,image_mask=self.canvas.HR_selected_mask,Z_mask=self.canvas.Z_mask,
                                                       auto_set_hist_temperature=self.auto_set_hist_temperature)
            if self.optimizing_region:
                self.MasksStorage(False)
        # if self.optimizing_region:
        self.stored_Z = 1 * self.cur_Z # Storing previous Z for two reasons: To recover the big picture Z when optimizing_region, and to recover previous Z if loss did not decrease
        self.cur_Z = self.canvas.Z_optimizer.optimize()
        if self.canvas.Z_optimizer.loss_values[0] - self.canvas.Z_optimizer.loss_values[-1] < 0:
            self.cur_Z = 1 * self.stored_Z
            self.SR_model.cur_Z = self.cur_Z.type(self.var_L.type())
        else:
            if self.optimizing_region:
                temp_Z = 1 * self.cur_Z
                self.cur_Z = 1 * self.stored_Z
                cropping_rect = 1*self.bounding_rect
                if self.canvas.HR_Z:
                    cropping_rect = [self.canvas.DTE_opt['scale']*val for val in self.bounding_rect]
                self.cur_Z[:, :, cropping_rect[1]:cropping_rect[1] + cropping_rect[3],cropping_rect[0]:cropping_rect[0] + cropping_rect[2]] = temp_Z
            self.Compute_SR_Image()
            self.Update_Image_Display()
        if (self.canvas.Z_optimizer.loss_values[0]-self.canvas.Z_optimizer.loss_values[-1])/np.abs(self.canvas.Z_optimizer.loss_values[0])<1e-4: #If the loss did not decrease, I decrease the optimizer's learning rate
            self.canvas.Z_optimizer_initial_LR /= 5
            self.canvas.Z_optimizer = None
            print('Z optimizer loss did not decrease relative to beginning, decreasing learning rate to %.3e'%(self.canvas.Z_optimizer_initial_LR))
        else: # This means I'm happy with this optimizer (and its learning rate), so I can cancel the auto-hist-temperature setting, in case it was set to True.
            self.auto_set_hist_temperature = False
            self.auto_hist_temperature_mode_button.setChecked(False)

    def Clear_Z_Mask(self):
        self.canvas.Z_mask = np.ones(self.canvas.Z_size)
        self.canvas.HR_selected_mask = np.ones(self.canvas.HR_size)
        self.canvas.LR_mask_vertices = []
        self.canvas.Update_Z_Sliders()
        self.canvas.Z_optimizer_Reset()

    def Invert_Z_Mask(self):
        self.canvas.Z_mask = 1-self.canvas.Z_mask
        self.canvas.HR_selected_mask = 1-self.canvas.HR_selected_mask

    def Recompose_cur_Z(self):
        Z_mask = torch.from_numpy(self.canvas.Z_mask).type(self.cur_Z.dtype).to(self.cur_Z.device)
        new_Z = util.SVD_2_LatentZ(torch.stack([self.canvas.lambda0, self.canvas.lambda1, self.canvas.theta], 0).unsqueeze(0)).to(self.cur_Z.device)
        self.cur_Z = Z_mask * new_Z + (1 - Z_mask) * self.cur_Z
        # self.cur_Z[0, 0, ...] = (self.canvas.lambda1 * np.sin(self.canvas.theta) ** 2 + self.canvas.lambda0 * np.cos(
        #     self.canvas.theta) ** 2 - 0.5).to(self.cur_Z.device) * 2*Z_mask+(1-Z_mask)*self.cur_Z[0, 0, ...]  # Since lambda is assumed in [0,1], the resulting value here for I_x**2 has this same range, so I normalize to [-1,1]
        # self.cur_Z[0, 1, ...] = (self.canvas.lambda0 * np.sin(self.canvas.theta) ** 2 + self.canvas.lambda1 * np.cos(
        #     self.canvas.theta) ** 2 - 0.5).to(self.cur_Z.device) * 2*Z_mask+(1-Z_mask)*self.cur_Z[0, 1, ...]  # Since lambda is assumed in [0,1], the resulting value here for I_y**2 has this same range, so I normalize to [-1,1]
        # self.cur_Z[0, 2, ...] = 2 * ((self.canvas.lambda0 - self.canvas.lambda1) * np.sin(self.canvas.theta) * np.cos(
        #     self.canvas.theta)).to(self.cur_Z.device)*Z_mask+(1-Z_mask)*self.cur_Z[0, 2, ...]  # Theta is in [0,pi], so the resulting value here for I_xy is in [-0.5,0.5], so I normalize to [-1,1]
    def SetZ_And_Display(self,value,index):
        self.SetZ(value,index)
        self.Update_Image_Display()

    def SetZ(self,value,index,reset_optimizer=True,recompose_Z=True):
        if reset_optimizer:
            self.canvas.Z_optimizer_Reset()
        Z_mask = torch.from_numpy(self.canvas.Z_mask).type(self.cur_Z.dtype)
        if USE_SVD:
            if index==0:
                self.canvas.lambda0 = Z_mask*value+(1-Z_mask)*self.canvas.lambda0
            elif index == 1:
                self.canvas.lambda1 = Z_mask*value+(1-Z_mask)*self.canvas.lambda1
            elif index == 2:
                self.canvas.theta = Z_mask*value+(1-Z_mask)*self.canvas.theta
            if recompose_Z:
                self.Recompose_cur_Z()
            if VERBOSITY:
                self.latent_mins = torch.min(torch.cat([self.cur_Z,self.latent_mins],0),dim=0,keepdim=True)[0]
                self.latent_maxs = torch.max(torch.cat([self.cur_Z,self.latent_maxs],0),dim=0,keepdim=True)[0]
                print(self.canvas.lambda0,self.canvas.lambda1,self.canvas.theta)
                print('mins:',[z.item() for z in self.latent_mins.view([-1])])
                print('maxs:', [z.item() for z in self.latent_maxs.view([-1])])
        else:
            raise Exception('Should recode to support Z-mask')
            self.cur_Z[0,index] = value
        if recompose_Z:
            self.Compute_SR_Image()
    def Update_Image_Display(self):
        pixmap = QPixmap()
        SR_image = 255 * self.SR_model.fake_H.detach()[0].float().cpu().numpy().transpose(1, 2, 0).copy()
        pixmap.convertFromImage(qimage2ndarray.array2qimage(SR_image))
        self.canvas.setPixmap(pixmap)
        if DISPLAY_INDUCED_LR:
            self.Update_LR_Display()

    def Update_LR_Display(self):
        pixmap = QPixmap()
        pixmap.convertFromImage(qimage2ndarray.array2qimage(255 * self.induced_LR_image[0].data.cpu().numpy().transpose(1,2,0).copy()))
        self.LR_canvas.setPixmap(pixmap)

    def ReProcess(self):
        self.Compute_SR_Image()
        self.Update_Image_Display()

    def open_file(self):
        """
        Open image file for editing, scaling the smaller dimension and cropping the remainder.
        :return:
        """
        path, _ = QFileDialog.getOpenFileName(self,"Open GT HR file" if DISPLAY_GT_HR else "Open file", "", "PNG image files (*.png); JPEG image files (*jpg); All files (*.*)")

        if path:
            loaded_image = data_util.read_img(None, path)
            if loaded_image.shape[2] == 3:
                loaded_image = loaded_image[:, :, [2, 1, 0]]
            if DISPLAY_GT_HR:
                SR_scale = self.canvas.DTE_opt['scale']
                loaded_image = loaded_image[:loaded_image.shape[0]//SR_scale*SR_scale,:loaded_image.shape[1]//SR_scale*SR_scale,:] #Removing bottom right margins to make the image shape adequate to this SR factor
                pixmap = QPixmap()
                pixmap.convertFromImage(qimage2ndarray.array2qimage(255 * loaded_image))
                self.GT_canvas.setPixmap(pixmap)
                self.var_L = self.SR_model.netG.module.DownscaleOP(torch.from_numpy(np.ascontiguousarray(np.transpose(loaded_image, (2, 0, 1)))).float().to(self.SR_model.device).unsqueeze(0))
            else:
                self.var_L = torch.from_numpy(np.ascontiguousarray(np.transpose(loaded_image, (2, 0, 1)))).float().to(self.SR_model.device).unsqueeze(0)
            if DISPLAY_ESRGAN_RESULTS:
                ESRGAN_opt = option.parse('./options/test/GUI_esrgan.json', is_train=False,name='RRDB_ESRGAN_x4')
                ESRGAN_opt = option.dict_to_nonedict(ESRGAN_opt)
                ESRGAN_opt['network_G']['latent_input'] = 'None'
                ESRGAN_opt['network_G']['DTE_arch'] = 0
                ESRGAN_model = create_model(ESRGAN_opt)
                ESRGAN_model.netG.eval()
                ESRGAN_SR = ESRGAN_model.netG(self.var_L)
                pixmap = QPixmap()
                pixmap.convertFromImage(qimage2ndarray.array2qimage(255 * ESRGAN_SR[0].data.cpu().numpy().transpose(1,2,0).copy()))
                self.ESRGAN_canvas.setPixmap(pixmap)

            self.canvas.LR_size = list(self.var_L.size()[2:])
            self.canvas.Z_size = [val*self.canvas.DTE_opt['scale'] for val in self.canvas.LR_size] if self.canvas.HR_Z else self.canvas.LR_size
            self.canvas.Z_mask = np.ones(self.canvas.Z_size)
            self.cur_Z = torch.zeros(size=[1,self.SR_model.num_latent_channels]+self.canvas.Z_size)
            if USE_SVD:
                self.canvas.lambda0 = torch.tensor(0.5)#*np.ones(self.canvas.LR_size)
                self.canvas.lambda1 = torch.tensor(0.5)#*np.ones(self.canvas.LR_size)
                self.canvas.theta = torch.tensor(0)#*np.ones(self.canvas.LR_size)
                self.SetZ(0.5*MAX_SVD_LAMBDA, 0,recompose_Z=False)  # *np.ones(self.canvas.LR_size)
                self.SetZ(0.5*MAX_SVD_LAMBDA, 1,recompose_Z=False)  # *np.ones(self.canvas.LR_size)
                self.SetZ(0, 2)  # *np.ones(self.canvas.LR_size)
                if VERBOSITY:
                    self.latent_mins = 100 * torch.ones([1, 3, 1, 1])
                    self.latent_maxs = -100 * torch.ones([1, 3, 1, 1])

            self.image_name = path.split('/')[-1].split('.')[0]
            # if USE_SVD:
            #     self.canvas.lambda0 = torch.tensor(0.5)  # *np.ones(self.canvas.LR_size)
            #     self.canvas.lambda1 = torch.tensor(0.5)  # *np.ones(self.canvas.LR_size)
            #     self.canvas.theta = torch.tensor(0)  # *np.ones(self.canvas.LR_size)

            self.ReProcess()
            # self.reference_image_4_SVD_optimization = 1*self.SR_model.fake_H.detach()
            self.canvas.HR_size = list(self.SR_model.fake_H.size()[2:])
            self.canvas.setGeometry(QRect(0,0,self.canvas.HR_size[0],self.canvas.HR_size[1]))
            self.Clear_Z_Mask()

    def save_file(self):
        """
        Save active canvas to image file.
        :return:
        """
        path, _ = QFileDialog.getSaveFileName(self, "Save file", "", "PNG Image file (*.png)")

        if path:
            imageio.imsave(path,np.clip(255*self.SR_model.fake_H[0].data.cpu().numpy().transpose(1,2,0),0,255).astype(np.uint8))
            # pixmap = self.canvas.pixmap()
            # pixmap.save(path, "PNG" )

    def save_file_and_Z_map(self):
        """
        Save active canvas and cur_Z map to image file.
        :return:
        """
        # path = os.path.join('/media/ybahat/data/projects/SRGAN/GUI_outputs','%s_%d%s.png'%(self.image_name,self.saved_outputs_counter,'%s'))
        path = os.path.join('/'.join(self.canvas.DTE_opt['path']['results_root'].split('/')[:-2]),'GUI_outputs','%s_%d%s.png'%(self.image_name,self.saved_outputs_counter,'%s'))

        if path:
            imageio.imsave(path%(''),np.clip(255*self.SR_model.fake_H[0].data.cpu().numpy().transpose(1,2,0),0,255).astype(np.uint8))
            imageio.imsave(path%('_Z'),np.clip(255/2/MAX_SVD_LAMBDA*(MAX_SVD_LAMBDA+self.cur_Z[0].data.cpu().numpy().transpose(1,2,0)),0,255).astype(np.uint8))
            if DISPLAY_INDUCED_LR:
                imageio.imsave(path % ('_LR'), np.clip(255*self.induced_LR_image[0].data.cpu().numpy().transpose(1, 2, 0),0, 255).astype(np.uint8))
            self.saved_outputs_counter += 1

    def invert(self):
        img = QImage(self.canvas.pixmap())
        img.invertPixels()
        pixmap = QPixmap()
        pixmap.convertFromImage(img)
        self.canvas.setPixmap(pixmap)

    def flip_horizontal(self):
        pixmap = self.canvas.pixmap()
        self.canvas.setPixmap(pixmap.transformed(QTransform().scale(-1, 1)))

    def flip_vertical(self):
        pixmap = self.canvas.pixmap()
        self.canvas.setPixmap(pixmap.transformed(QTransform().scale(1, -1)))



if __name__ == '__main__':

    app = QApplication([])
    window = MainWindow()
    app.exec_()