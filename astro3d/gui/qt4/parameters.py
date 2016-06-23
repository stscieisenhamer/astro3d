"""Parameter Editor"""

from ginga.misc.Bunch import Bunch
from ginga.gw import Widgets

from ...external.qt import (QtGui, QtCore)
from ...util.logger import make_logger
from .. import signaldb
from ..util import build_widgets

__all__ = ['Parameters']


STAGES = {
    'Intensity':     'intensity',
    'Textures':      'textures',
    'Spiral Galaxy': 'spiral_galaxy',
    'Double Sided':  'double_sided'
}


class Parameters(QtGui.QWidget):
    """Parameters Editor

    Paremeters
    ----------
    logger: logging.Logger
        The common logger.
    """

    def __init__(self, *args, **kwargs):
        self.logger = kwargs.pop(
            'logger',
            make_logger('astro3d Shape Editor')
        )
        self.model = kwargs.pop('model')
        self.parent = kwargs.pop('parent')
        super(Parameters, self).__init__(*args, **kwargs)

        self._build_gui()

    def create_gas_spiral_masks(self, *args, **kwargs):
        """Set Gas/Spiral arm esitmator parameters"""
        self.model.create_gas_spiral_masks(
            smooth_size=self.children.smooth_size.get_value(),
            gas_percentile=self.children.gas_percentile.get_value(),
            spiral_percentile=self.children.spiral_percentile.get_value()
        )

    def set_stage(self, widget, state):
        stage = STAGES[widget.get_widget().text()]
        self.model.params[stage] = state
        signaldb.ModelUpdate()

    def _build_gui(self):
        """Build out the GUI"""
        self.logger.debug('Called.')
        self.children = Bunch()
        spacer = Widgets.Label('')

        # Processing parameters
        params_widget, params_bunch = build_widgets(self.model.params)
        self.children.update(params_bunch)

        """
        w = params_bunch['autoprocess']
        w.set_state(signaldb.ModelUpdate.enabled)
        w.add_callback(
            'activated',
            lambda w, state: signaldb.ModelUpdate.set_enabled(state)
        )

        params_bunch.reprocess.add_callback(
            'activated',
            lambda w: self.parent.force_update()
        )
        """

        params_frame = Widgets.Frame('Processing')
        params_frame.set_widget(params_widget)

        # Gas/Spiral parameters
        captions = (
            ('Gas Percentile:', 'label', 'Gas Percentile', 'spinbutton'),
            ('Spiral Percentile:', 'label', 'Spiral Percentile', 'spinbutton'),
            ('Smooth Size:', 'label', 'Smooth Size', 'spinbutton'),
            ('Create masks', 'button'),
        )
        gasspiral_widget, gasspiral_bunch = Widgets.build_info(captions)
        self.children.update(gasspiral_bunch)

        gasspiral_bunch.gas_percentile.set_limits(0., 100.)
        gasspiral_bunch.gas_percentile.set_value(55)
        gasspiral_bunch.gas_percentile.set_tooltip(
            'The percentile of values above which'
            ' are assigned to the Gas mask'
        )

        gasspiral_bunch.spiral_percentile.set_limits(0., 100.)
        gasspiral_bunch.spiral_percentile.set_value(75)
        gasspiral_bunch.spiral_percentile.set_tooltip(
            'The percential of values above which are'
            ' assigned to the Spiral Arm mask'
        )

        gasspiral_bunch.smooth_size.set_limits(3, 100)
        gasspiral_bunch.smooth_size.set_value(11)
        gasspiral_bunch.smooth_size.set_tooltip(
            'Size of the smoothing window'
        )

        gasspiral_bunch.create_masks.add_callback(
            'activated',
            self.create_gas_spiral_masks
        )

        gasspiral_frame = Widgets.Frame('Gas/Spiral Arm parameters')
        gasspiral_frame.set_widget(gasspiral_widget)

        # Put it together
        layout = QtGui.QVBoxLayout()
        layout.setContentsMargins(QtCore.QMargins(20, 20, 20, 20))
        layout.setSpacing(1)
        layout.addWidget(params_frame.get_widget(), stretch=0)
        layout.addWidget(spacer.get_widget(), stretch=1)
        layout.addWidget(gasspiral_frame.get_widget(), stretch=0)
        layout.addWidget(spacer.get_widget(), stretch=2)
        self.setLayout(layout)

    def callbackme(self, *args, **kwargs):
        self.logger.debug('Callback: args="{}" kwargs="{}"'.format(args, kwargs))
        self.logger.debug('dir(w): "{}"'.format(dir(args[0])))
