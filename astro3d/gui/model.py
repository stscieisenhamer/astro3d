"""Data Model"""

from __future__ import absolute_import, print_function

from os.path import basename

from attrdict import AttrDict

from numpy import concatenate

from ..external.qt.QtGui import (QStandardItem, QStandardItemModel)
from ..core.model3d import Model3D
from ..core.region_mask import RegionMask
from ..core.meshes import (get_triangles, reflect_mesh)
from ..util.logger import make_logger


__all__ = ['Model']


class LayerItem(QStandardItem):
    """Layers"""

    def __init__(self, *args, **kwargs):
        super(LayerItem, self).__init__(*args, **kwargs)
        self._value = None
        self.setCheckable(True)
        self.setCheckState(True)

    @property
    def value(self):
        """Value of the item"""
        return self._value

    @value.setter
    def value(self, value):
        self._value = value

    @classmethod
    def empty(cls):
        result = cls('')
        result.setCheckable(False)
        result.setEnabled(False)
        return result


class Model(QStandardItemModel):
    """Data model"""

    image = None

    def __init__(self, *args, **kwargs):
        logger = kwargs.pop('logger', None)
        if logger is None:
            logger = make_logger('astro3d Layer Manager')
        self.logger = logger

        super(Model, self).__init__(*args, **kwargs)

        # Setup the basic structure
        self.regions = LayerItem('Regions')
        self.textures = LayerItem('Textures')
        self.cluster_catalogs = LayerItem('Star Clusters')
        self.stars_catalogs = LayerItem('Stars')

        self.setHorizontalHeaderLabels(['Class', 'Type', 'Name'])
        root = self.invisibleRootItem()
        root.appendRow(self.regions)
        root.appendRow(self.textures)
        root.appendRow(self.cluster_catalogs)
        root.appendRow(self.stars_catalogs)

        self.stages = AttrDict({
            'intensity': True,
            'textures': False,
            'spiral_galaxy': False,
            'double_sided': False
        })

    def set_image(self, image):
        """Set the image"""
        self.image = image

    def read_regionpathlist(self, pathlist):
        """Read a list of mask files"""
        for path in pathlist:
            data = RegionMask.from_fits(path)
            region_type = LayerItem(data.mask_type)
            region = LayerItem()
            region.setText(basename(path))
            region.value = path
            self.regions.appendRow(
                [LayerItem.empty(),
                 region_type,
                 region]
            )

    def read_star_catalog(self, pathname):
        """Read in a star catalog"""
        self.star_catalog = pathname

    def read_cluster_catalog(self, pathname):
        """Read in a star cluster catalog"""
        self.cluster_catalog = pathname

    def process(self):
        """Create the 3D model."""
        self.logger.debug('Starting processing...')

        # Setup steps in the thread. Between each step,
        # check to see if stopped.
        m = Model3D(self.image)

        #for path in self.maskpathlist:
        #    m.read_mask(path)
        for row in range(self.regions.rowCount()):
            region = self.regions.child(row, 2)
            if region.checkState():
                m.read_mask(region.value)

        #if self.cluster_catalog is not None:
        #    m.read_star_clusters(self.cluster_catalog)

        #if self.star_catalog is not None:
        #    m.read_stars(self.star_catalog)

        m.has_textures = self.stages.textures
        m.has_intensity = self.stages.intensity
        m.spiral_galaxy = self.stages.spiral_galaxy
        m.double_sided = self.stages.double_sided

        m.make()

        triset = get_triangles(m.data)
        if m.double_sided:
            triset = concatenate((triset, reflect_mesh(triset)))
        return triset
