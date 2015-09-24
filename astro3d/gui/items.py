"""Model Items"""
from __future__ import absolute_import, print_function

from collections import defaultdict
import logging as log

from ..external.qt.QtGui import QStandardItem
from ..external.qt.QtCore import Qt


__all__ = ['LayerItem']


class InstanceDefaultDict(defaultdict):
    """A default dict with class instantion using the key as argument"""
    def __missing__(self, key):
        if self.default_factory is None:
            raise KeyError(key)
        else:
            self[key] = self.default_factory(key)
            return self[key]


class LayerItem(QStandardItem):
    """Layers"""

    def __init__(self, *args, **kwargs):
        super(LayerItem, self).__init__(*args, **kwargs)
        self._value = None
        self._currentrow = None

    def __iter__(self):
        self._currentrow = None
        return self

    def next(self):
        self._currentrow = self._currentrow + 1 \
                           if self._currentrow is not None \
                           else 0
        item = self.child(self._currentrow, 2)
        if item is None:
            raise StopIteration
        else:
            return item

    @property
    def value(self):
        """Value of the item"""
        return self._value

    @value.setter
    def value(self, value):
        self._value = value


class CheckableItem(LayerItem):
    """Items that are checkable"""
    def __init__(self, *args, **kwargs):
        super(CheckableItem, self).__init__(*args, **kwargs)
        self.setCheckable(True)
        self.setCheckState(Qt.Checked)


class TypeItem(CheckableItem):
    """Types of regions"""


class Regions(CheckableItem):
    """Regions container"""

    def __init__(self, *args, **kwargs):
        super(Regions, self).__init__(*args, **kwargs)
        self.setText('Regions')

        self.types = InstanceDefaultDict(TypeItem)

    def next(self):
        while True:
            item = super(Regions, self).next()
            if item.checkState():
                return item.value

    def add(self, region, id):
        """Add a new region"""
        type_item = self.types[region.mask_type]
        region_item = CheckableItem(id)
        region_item.value = region
        type_item.appendRow(region_item)
        if not type_item.index().isValid():
            self.appendRow(type_item)


class Textures(LayerItem):
    """Textures container"""
    def __init__(self, *args, **kwargs):
        super(Textures, self).__init__(*args, **kwargs)
        self.setText('Textures')


class Clusters(LayerItem):
    """Cluster container"""
    def __init__(self, *args, **kwargs):
        super(Clusters, self).__init__(*args, **kwargs)
        self.setText('Clusters')


class Stars(LayerItem):
    """Stars container"""

    def __init__(self, *args, **kwargs):
        super(Stars, self).__init__(*args, **kwargs)
        self.setText('Stars')
