"""Shape Editor"""

from functools import partial
from numpy import (zeros, uint8)

from ginga import colors
from ginga.misc.Bunch import Bunch
from ginga.RGBImage import RGBImage
from ginga.gw import Widgets

from ...core.region_mask import RegionMask
from ...external.qt import (QtGui, QtCore)
from ...util.logger import make_logger
from .. import signaldb


__all__ = ['ShapeEditor']


VALID_KINDS = [
    'freepolygon', 'paint',
    'circle', 'rectangle',
    'triangle', 'righttriangle',
    'square', 'ellipse', 'box'
]

INSTRUCTIONS = (
    'Draw a region with the cursor. '
    'For polygons/paths press \'v\' to create a vertex, '
    '\'z\' to remove last vertex.'
)


class ShapeEditor(QtGui.QWidget):
    """Shape Editor

    Paremeters
    ----------
    surface: `ginga.Canvas`
        The canvas to interact on.

    logger: logging.Logger
        The common logger.
    """

    def __init__(self, *args, **kwargs):
        self.logger = kwargs.pop(
            'logger',
            make_logger('astro3d Shape Editor')
        )
        self.surface = kwargs.pop('surface', None)
        canvas = kwargs.pop('canvas', None)

        super(ShapeEditor, self).__init__(*args, **kwargs)

        self._canvas = None
        self._mode = None
        self.drawkinds = []
        self.enabled = False
        self.canvas = canvas

        signaldb.NewRegion.connect(self.new_region)

    @property
    def canvas(self):
        """The canvas the draw object will appear"""
        return self._canvas

    @canvas.setter
    def canvas(self, canvas):
        if canvas is None or \
           self._canvas is canvas:
            return

        self._canvas = canvas

        # Setup parameters
        self.drawkinds = VALID_KINDS

        # Create paint mode
        canvas.add_draw_mode(
            'paint',
            down=self.paint_start,
            move=self.paint_stroke,
            up=self.paint_stop
        )

        # Setup common events.
        canvas.set_callback('draw-event', self.draw_cb)
        canvas.set_callback('edit-event', self.edit_cb)
        canvas.set_callback('edit-select', self.edit_select_cb)

        # Initial canvas state
        canvas.enable_edit(True)
        canvas.enable_draw(True)
        canvas.setSurface(self.surface)
        canvas.register_for_cursor_drawing(self.surface)
        self._build_gui()
        self.mode = None
        self.enabled = True

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, state):
        self._enabled = state
        try:
            self._canvas.ui_setActive(state)
        except AttributeError:
            pass

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, new_mode):
        self.logger.debug('Called new_mode="{}"'.format(new_mode))
        self._mode = new_mode
        for mode in self.mode_frames:
            for frame in self.mode_frames[mode]:
                frame.hide()
        try:
            for frame in self.mode_frames[new_mode]:
                frame.show()
        except KeyError:
            """Doesn't matter if there is nothing to show."""
            pass

        if new_mode is None:
            self.canvas.set_draw_mode('edit')
        elif new_mode == 'edit_select':
            self.canvas.set_draw_mode('edit')
        else:
            self.canvas.set_draw_mode(new_mode)

    def new_region(self, type_item):
        self.logger.debug('Called type_item="{}"'.format(type_item))
        self.type_item = type_item
        if self.canvas is None:
            raise RuntimeError('Internal error: no canvas to draw on.')
        self.set_drawparams_cb()

    def set_drawparams_cb(self, kind=None):
        params = {}
        if kind is None:
            kind = self.get_selected_kind()
        if kind == 'paint':
            self.mode = 'paint'
        else:
            self.mode = 'draw'
            try:
                params.update(self.type_item.draw_params)
            except AttributeError:
                pass
            self.canvas.set_drawtype(kind, **params)

    def draw_cb(self, canvas, tag):
        """Shape draw completion"""
        shape = canvas.get_object_by_tag(tag)
        region_mask = partial(
            self.surface.get_shape_mask,
            self.type_item.text(),
            shape
        )
        self.type_item.add_shape(shape=shape, mask=region_mask, id=tag)
        self.mode = None

    def edit_cb(self, *args, **kwargs):
        """Edit callback"""
        self.logger.debug('Called with args="{}" kwargs="{}".'.format(args, kwargs))
        signaldb.ModelUpdate()

    def edit_select_cb(self, *args, **kwargs):
        """Edit selected object callback"""
        self.logger.debug('Called with args="{}" kwargs="{}".'.format(args, kwargs))
        if self.canvas.num_selected() > 0:
            self.mode = 'edit_select'
        else:
            self.mode = None

    def edit_deselect_cb(self, *args, **kwargs):
        """Deselect"""
        self.logger.debug('Called with args="{}" kwargs="{}".'.format(args, kwargs))
        self.canvas.clear_selected()

    def rotate_object(self, w):
        delta = float(w.get_text())
        self.canvas.edit_rotate(delta, self.surface)
        signaldb.ModelUpdate()

    def scale_object(self, w):
        delta = float(w.get_text())
        self.canvas.edit_scale(delta, delta, self.surface)
        signaldb.ModelUpdate()

    def new_brush(self, copy_from=None):
        """Create a new brush shape"""
        brush_size = self.children.brush_size.get_value()
        brush = self.canvas.get_draw_class('squarebox')(
            x=0., y=0., radius=max(brush_size / 2, 0.5),
            **self.type_item.draw_params
        )
        if copy_from is not None:
            brush.x = copy_from.x
            brush.y = copy_from.y
            brush.radius = copy_from.radius
        self.canvas.add(brush)
        return brush

    def paint_start(self, canvas, event, data_x, data_y, surface):
        """Start a paint stroke"""
        self.logger.debug((
            'canvas="{}" '
            'event="{}" '
            'x="{}" y="{}"'
            'surface="{}"'
        ).format(
            canvas,
            event,
            data_x,
            data_y,
            surface
        ))

        self.brush = self.new_brush()
        self.mask = self.new_mask()
        self.brush_move(data_x, data_y)

    def paint_stroke(self, canvas, event, data_x, data_y, surface):
        """Perform a paint stroke"""
        previous = self.brush
        self.brush = self.new_brush(previous)
        self.brush_move(data_x, data_y)
        self.stroke(previous, self.brush)
        self.canvas.delete_object(previous)
        self.canvas.redraw(whence=0)

    def paint_stop(self, canvas, event, data_x, data_y, surface):
        """Finish paint stroke"""
        self.paint_stroke(canvas, event, data_x, data_y, surface)
        self.canvas.delete_object(self.brush)
        self.mode = None
        region_mask = RegionMask(
            mask=self.mask > 0,
            mask_type=self.type_item.text()
        )
        self.type_item.add_shape(None, region_mask, self.mask_id)

    def stroke(self, previous, current):
        """Stroke to current brush position"""
        poly_points = get_bpoly(previous, current)
        polygon = self.canvas.get_draw_class('polygon')(
            poly_points,
            **self.type_item.draw_params
        )
        self.canvas.add(polygon)
        view, contains = self.surface.get_image().get_shape_view(polygon)
        self.mask[view][contains] = self.type_item.draw_params['fillalpha'] * 255
        self.canvas.delete_object(polygon)

    def new_mask(self):
        color = self.type_item.draw_params['color']
        r, g, b = colors.lookup_color(color)
        height, width = self.surface.get_image().shape
        rgbarray = zeros((height, width, 4), dtype=uint8)
        mask_rgb = RGBImage(data_np=rgbarray)
        mask_image = self.canvas.get_draw_class('image')(0, 0, mask_rgb)
        rc = mask_rgb.get_slice('R')
        gc = mask_rgb.get_slice('G')
        bc = mask_rgb.get_slice('B')
        rc[:] = int(r * 255)
        gc[:] = int(g * 255)
        bc[:] = int(b * 255)
        alpha = mask_rgb.get_slice('A')
        alpha[:] = 0
        self.mask_id = self.canvas.add(mask_image)
        return alpha

    def get_selected_kind(self):
        kind = self.drawkinds[self.children.draw_type.get_index()]
        return kind

    def brush_move(self, x, y):
        self.brush.move_to(x, y)
        self.canvas.update_canvas(whence=3)

    def _build_gui(self):
        """Build out the GUI"""
        # Remove old layout
        self.logger.debug('Called.')
        if self.layout() is not None:
            QtGui.QWidget().setLayout(self.layout())
        self.children = Bunch()
        spacer = Widgets.Label('')

        # Instructions
        tw = Widgets.TextArea(wrap=True, editable=False)
        font = QtGui.QFont('sans serif', 12)
        tw.set_font(font)
        tw.set_text(INSTRUCTIONS)
        tw_frame = Widgets.Expander("Instructions")
        tw_frame.set_widget(tw)
        self.children['tw'] = tw
        self.children['tw_frame'] = tw_frame

        # Setup for the drawing types
        captions = (
            ("Draw type:", 'label', "Draw type", 'combobox'),
        )
        dtypes_widget, dtypes_bunch = Widgets.build_info(captions)
        self.children.update(dtypes_bunch)
        self.logger.debug('children="{}"'.format(self.children))

        combobox = dtypes_bunch.draw_type
        for name in self.drawkinds:
            combobox.append_text(name)
        index = self.drawkinds.index('freepolygon')
        combobox.add_callback(
            'activated',
            lambda w, idx: self.set_drawparams_cb()
        )
        combobox.set_index(index)

        draw_frame = Widgets.Frame("Drawing")
        draw_frame.set_widget(dtypes_widget)

        # Setup for painting
        captions = (
            ('Brush size:', 'label', 'Brush size', 'spinbutton'),
        )
        paint_widget, paint_bunch = Widgets.build_info(captions)
        self.children.update(paint_bunch)
        brush_size = paint_bunch.brush_size
        brush_size.set_limits(1, 100)
        brush_size.set_value(10)

        paint_frame = Widgets.Frame('Painting')
        paint_frame.set_widget(paint_widget)

        # Setup for editing
        captions = (("Rotate By:", 'label', 'Rotate By', 'entry'),
                    ("Scale By:", 'label', 'Scale By', 'entry')
        )
        edit_widget, edit_bunch = Widgets.build_info(captions)
        self.children.update(edit_bunch)
        edit_bunch.scale_by.add_callback('activated', self.scale_object)
        edit_bunch.scale_by.set_text('0.9')
        edit_bunch.scale_by.set_tooltip("Scale selected object in edit mode")
        edit_bunch.rotate_by.add_callback('activated', self.rotate_object)
        edit_bunch.rotate_by.set_text('90.0')
        edit_bunch.rotate_by.set_tooltip("Rotate selected object in edit mode")

        edit_frame = Widgets.Frame('Editing')
        edit_frame.set_widget(edit_widget)

        # Put it together
        layout = QtGui.QVBoxLayout()
        layout.setContentsMargins(QtCore.QMargins(20, 20, 20, 20))
        layout.setSpacing(1)
        layout.addWidget(tw_frame.get_widget(), stretch=0)
        layout.addWidget(draw_frame.get_widget(), stretch=0)
        layout.addWidget(paint_frame.get_widget(), stretch=0)
        layout.addWidget(edit_frame.get_widget(), stretch=0)
        layout.addWidget(spacer.get_widget(), stretch=1)
        self.setLayout(layout)

        # Setup mode frames
        self.mode_frames = {
            'draw': [draw_frame],
            'edit_select': [edit_frame],
            'paint': [
                draw_frame,
                paint_frame
            ]
        }


def get_bpoly(box1, box2):
    """Get the bounding polygon of two boxes"""
    left = box1
    right = box2
    if box2.x < box1.x:
        left = box2
        right = box1
    left_llx, left_lly, left_urx, left_ury = left.get_llur()
    right_llx, right_lly, right_urx, right_ury = right.get_llur()

    b = [(left_llx, left_lly)]
    if left.y <= right.y:
        b.extend([
            (left_urx, left_lly),
            (right_urx, right_lly),
            (right_urx, right_ury),
            (right_llx, right_ury),
            (left_llx, left_ury)
        ])
    else:
        b.extend([
            (right_llx, right_lly),
            (right_urx, right_lly),
            (right_urx, right_ury),
            (left_urx, left_ury),
            (left_llx, left_ury)
        ])
    return b


def corners(box):
    """Get the corners of a box

    Returns
    -------
    List of the corners starting at the
    lower left, going counter-clockwise
    """
    xll, yll, xur, yur = box.get_llur()
    corners = [
        (xll, yll),
        (xll, yur),
        (xur, yur),
        (xur, yll)
    ]
    return corners
