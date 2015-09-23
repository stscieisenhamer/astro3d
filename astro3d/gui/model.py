"""Data Model"""

from __future__ import absolute_import, print_function

from os.path import basename

from attrdict import AttrDict

from numpy import concatenate

from ..external.qt.QtGui import QStandardItemModel
from ..core.model3d import Model3D
from ..core.region_mask import RegionMask
from ..core.meshes import (make_triangles, reflect_triangles)
from ..util.logger import make_logger
from .items import (Regions, Textures, Clusters, Stars)


__all__ = ['Model']


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
        self.regions = Regions()
        self.textures = Textures()
        self.cluster_catalogs = Clusters()
        self.stars_catalogs = Stars()

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
            region = RegionMask.from_fits(path)
            id = basename(path)
            self.regions.add(region=region, id=id)

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

        for region in self.regions:
            m.add_mask(region)

        #if self.cluster_catalog is not None:
        #    m.read_star_clusters(self.cluster_catalog)

        #if self.star_catalog is not None:
        #    m.read_stars(self.star_catalog)

        m.has_textures = self.stages.textures
        m.has_intensity = self.stages.intensity
        m.spiral_galaxy = self.stages.spiral_galaxy
        m.double_sided = self.stages.double_sided

        m.make()

        triset = make_triangles(m.data)
        if m.double_sided:
            triset = concatenate((triset, reflect_triangles(triset)))
        return triset
