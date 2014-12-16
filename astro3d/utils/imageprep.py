"""Functions to prepare image before conversion to 3D model."""
from __future__ import division, print_function

# STDLIB
import os
import warnings
from collections import defaultdict
from copy import deepcopy
from functools import partial

# Anaconda
import numpy as np
from astropy import log
from astropy.io import ascii, fits
from astropy.table import Table
from astropy.utils.exceptions import AstropyUserWarning
from PIL import Image
from scipy import ndimage

# THIRD-PARTY
import photutils
from photutils.utils import scale_linear

# LOCAL
from . import imageutils as iutils
from . import texture as _texture
from .meshcreator import to_mesh


class ModelFor3D(object):
    """Class to do the same thing as :func:`make_model` but
    in parts to support GUI preview.

    Parameters
    ----------
    input_image : array_like
        Monochrome image.

    Examples
    --------
    >>> model = ModelFor3D.from_fits('myimage.fits')
    >>> model.is_spiralgal = True
    >>> model.double = True
    >>> model.load_regions('*.npz')
    >>> model.load_peaks(model.clusters_key, 'myclusters.txt')
    >>> model.make()
    >>> preview_intensity = model.preview_intensity
    >>> preview_dots_mask = model.get_preview_mask(model.dots_key)
    >>> preview_clusters = model.get_final_clusters()
    >>> preview_stars = model.get_final_stars()
    >>> image_for_stl = model.out_image
    >>> model.save_stl('myprefix')
    >>> model.save_regions('myprefix')
    >>> model.save_peaks('myprefix')

    """
    _MIN_PIXELS = 8.1e5  # 900 x 900
    _MAX_PIXELS = 1.69e6  # 1300 x 1300

    def __init__(self, input_image):
        self.orig_img = input_image

        # This can be set from GUI
        self.region_masks = defaultdict(list)
        self.peaks = {
            self.clusters_key: Table(names=['xcen', 'ycen', 'flux']),
            self.stars_key: Table(names=['xcen', 'ycen', 'flux'])}
        self.height = 150.0
        self.base_thickness = 20
        self.clus_r_fac_add = 10
        self.clus_r_fac_mul = 5
        self.star_r_fac_add = 10
        self.star_r_fac_mul = 5
        self.double = False
        self._has_texture = True
        self._has_intensity = True
        self.is_spiralgal = False
        self._layer_order = [self.lines_key, self.dots_key, self.small_dots_key]

        # Results
        self._preview_intensity = None
        self._out_image = None
        self._texture_layer = None
        self._preview_masks = None
        self._final_peaks = {}

        # Auto resize image to 1k (replaces wizard's resize page)
        orig_h, orig_w = input_image.shape
        if (input_image.size < self._MIN_PIXELS or
                input_image.size > self._MAX_PIXELS):
            scale = orig_h / orig_w
            if orig_w <= orig_h:
                new_w = 1000
            else:
                new_w = int(1000 / scale)
            new_h = int(new_w * scale)
            image = iutils.resize_image(
                scale_linear(input_image, percent=99), new_h, width=new_w)
            log.info('Image resize from {0}x{1} to {2}x{3}'.format(
                orig_w, orig_h, new_w, new_h))
        else:
            image = input_image
            log.info('Image retains original dimension {0}x{1}'.format(
                orig_w, orig_h))

        # Image is now ready for the rest of processing when user
        # provides the rest of the info
        self._preproc_img = np.flipud(image)

    @classmethod
    def from_fits(cls, filename):
        """Create class instance from FITS file."""
        data = fits.getdata(filename)
        if data is None:
            raise ValueError('FITS file does not have image data')
        return cls(data)

    @classmethod
    def from_rgb(cls, filename):
        """Create class instance from RGB images like JPEG and TIFF."""
        data = np.array(
            Image.open(filename), dtype=np.float32)[::-1, :, :].sum(axis=2)
        return cls(data)

    def load_regions(self, search_string):
        """Load region files directly into ``self.region_masks``.

        .. note::

            This is for debugging. Do not use with GUI.

        """
        import glob

        for filename in glob.iglob(search_string):
            dat = np.load(filename)
            key = dat['name'].tostring()
            if key not in self.allowed_textures():
                warnings.warn('{0} not allowed, skipping {1}'.format(
                    key, filename), AstropyUserWarning)
                continue
            self.region_masks[key].append(dat['data'])
            log.info('{0} loaded from {1}'.format(key, filename))

    def save_regions(self, prefix):
        """Save uncropped region masks to files using
        :meth:`astro3d.astroObjects.Region.save`.

        Coordinates are transformed to match original image.
        One output file per region, each named
        ``<prefixpath>/<type>/<prefixname>_<n>_<description>.npz``.

        """
        prefixpath, prefixname = os.path.split(prefix)
        for key, reglist in self.region_masks.iteritems():
            rpath = os.path.join(prefixpath, '_'.join(['region', key]))
            if not os.path.exists(rpath):
                os.mkdir(rpath)
            for i, reg in enumerate(reglist, 1):
                rname = os.path.join(rpath, '_'.join(
                    map(str, [prefixname, i, reg.description])) + '.npz')
                reg.save(rname, self.orig_img.shape)

    def _store_peaks(self, key, tab):
        """Store peaks in attribute."""
        tab.keep_columns(['xcen', 'ycen', 'flux'])
        self.peaks[key] = tab

    def find_peaks(self, key, n):
        """Find point sources and store them in ``self.peaks[key]``.

        .. note:: Overwrites :meth:`load_peaks`.

        Parameters
        ----------
        key : {self.clusters_key, self.stars_key}
            Stars or star clusters.

        n : int
            Maximum number of sources allowed.

        """
        tab = find_peaks(np.flipud(self.model3d.orig_img))[:n]
        self._store_peaks(key, tab)

    def load_peaks(self, key, filename):
        """Load existing point sources and store them in ``self.peaks[key]``.

        .. note:: Overwrites :meth:`find_peaks`.

        Parameters
        ----------
        key : {self.clusters_key, self.stars_key}
            Stars or star clusters.

        filename : str
            ASCII table generated by ``photutils``.

        """
        tab = ascii.read(filename, data_start=1)
        self._store_peaks(key, tab)

    def save_peaks(self, prefix):
        """Save stars and star clusters to text files.

        Coordinates already match original image.
        One output file per table, each named ``<prefix>_<type>.txt``.

        """
        for key, tab in self.peaks.iteritems():
            if len(tab) < 1:
                continue
            tname = '{0}_{1}.txt'.format(prefix, key)
            tab.write(tname, format='ascii')
            log.info('{0} saved'.format(tname))

    @property
    def has_texture(self):
        """Apply textures."""
        return self._has_texture

    @has_texture.setter
    def has_texture(self, value):
        """Set to `True` or `False`."""
        if not isinstance(value, bool):
            raise ValueError('Must be a boolean')
        if not self.has_intensity and not value:
            raise ValueError('Model must have textures or intensity!')
        self._has_texture = value

    @property
    def has_intensity(self):
        """Generate intensity map."""
        return self._has_intensity

    @has_intensity.setter
    def has_intensity(self, value):
        """Set to `True` or `False`."""
        if not isinstance(value, bool):
            raise ValueError('Must be a boolean')
        if not self.has_texture and not value:
            raise ValueError('Model must have textures or intensity!')
        self._has_intensity = value

    @property
    def smooth_key(self):
        """Key identifying regions to smooth."""
        if self.is_spiralgal:
            key = 'remove_star'
        else:
            key = 'smooth'
        return key

    @property
    def small_dots_key(self):
        """Key identifying regions to mark with small dots."""
        if self.is_spiralgal:
            key = 'gas'
        else:
            key = 'dots_small'
        return key

    @property
    def dots_key(self):
        """Key identifying regions to mark with dots."""
        if self.is_spiralgal:
            key = 'spiral'
        else:
            key = 'dots'
        return key

    @property
    def lines_key(self):
        """Key identifying regions to mark with lines."""
        if self.is_spiralgal:
            key = 'disk'
        else:
            key = 'lines'
        return key

    @property
    def clusters_key(self):
        """Key identifying star clusters to be marked."""
        return 'clusters'

    @property
    def stars_key(self):
        """Key identifying stars to be marked."""
        return 'stars'

    @property
    def layer_order(self):
        """Layer ordering, listed by highest priority first."""
        return self._layer_order

    @layer_order.setter
    def layer_order(self, value):
        if self.is_spiralgal:
            raise ValueError('Layer order is fixed for spiral galaxy')
        if set(value) != set(self.layer_order):
            raise ValueError(
                'Layers can be reordered but cannot be added or removed.')
        self._layer_order = value

    def allowed_textures(self):
        """Return a list of allowed texture names."""
        return [self.dots_key, self.small_dots_key, self.lines_key,
                self.smooth_key]

    def texture_names(self):
        """Return existing region texture names, except for the one used
        for smoothing.

        .. note::

            This is targeted at textures with dots and lines,
            where lines belong in the foreground layer by default,
            hence listed first.

        """
        names = sorted(
            self.region_masks, key=lambda x: self.layer_order.index(x)
            if x in self.layer_order else 99, reverse=True)
        if self.smooth_key in names:
            names.remove(self.smooth_key)
        for key in names:
            if len(self.region_masks[key]) < 1:
                names.remove(key)
        return names

    @property
    def preview_intensity(self):
        """Monochrome intensity for GUI preview."""
        if self._preview_intensity is None:
            raise ValueError('Run make() first')
        return np.flipud(self._preview_intensity)

    @property
    def out_image(self):
        """Final result for STL generator."""
        if self._out_image is None:
            raise ValueError('Run make() first')
        return self._out_image

    def save_stl(self, fname, split_halves=True, _ascii=False):
        """Save 3D model to STL file(s)."""
        model = self.out_image

        # Remove any .stl suffix because it is added by to_mesh()
        if fname.lower().endswith('.stl'):
            fname = fname[:-4]

        # Depth is set to 1 here because it is accounted for in make()
        depth = 1
        if split_halves:
            model1, model2 = iutils.split_image(model, axis='horizontal')
            to_mesh(model1, fname + '_1', depth, self.double, _ascii)
            to_mesh(model2, fname + '_2', depth, self.double, _ascii)
        else:
            to_mesh(model, fname, depth, self.double, _ascii)

    def get_preview_mask(self, key):
        """Boolean mask for given texture key for GUI preview."""
        if self._preview_masks is None:
            raise ValueError('Run make() first')
        return self._preview_masks == key

    def get_final_clusters(self):
        """Star clusters for GUI preview (not in native coords)."""
        if self.clusters_key not in self._final_peaks:
            raise ValueError('Run make() first')
        return self._final_peaks[self.clusters_key]

    def get_final_stars(self):
        """Stars for GUI preview (not in native coords)."""
        if self.stars_key not in self._final_peaks:
            raise ValueError('Run make() first')
        return self._final_peaks[self.stars_key]

    def _process_masks(self):
        """Scale and combine masks."""
        scaled_masks = defaultdict(list)
        disk = None
        spiralarms = None

        for key, reglist in self.region_masks.iteritems():
            masklist = [reg.scaled_mask(self._preproc_img.shape)
                        for reg in reglist]

            if key != self.smooth_key:
                scaled_masks[key] = [combine_masks(masklist)]
            else:  # To be smoothed
                scaled_masks[key] = masklist

        if self.is_spiralgal:
            if len(scaled_masks[self.lines_key]) > 0:
                disk = scaled_masks[self.lines_key][0]
            if len(scaled_masks[self.dots_key]) > 0:
                spiralarms = scaled_masks[self.dots_key][0]

        return scaled_masks, disk, spiralarms

    def _crop_masks(self, scaled_masks, ix1, ix2, iy1, iy2):
        """Crop masks."""
        croppedmasks = defaultdict(list)
        disk = None
        spiralarms = None

        for key, mlist in scaled_masks.iteritems():
            if key == self.smooth_key:  # Smoothing already done
                continue
            for mask in mlist:
                croppedmasks[key].append(mask[iy1:iy2, ix1:ix2])

        if self.is_spiralgal:
            if len(croppedmasks[self.lines_key]) > 0:
                disk = croppedmasks[self.lines_key][0]
            if len(croppedmasks[self.dots_key]) > 0:
                spiralarms = croppedmasks[self.dots_key][0]

        return croppedmasks, disk, spiralarms

    def _process_peaks(self):
        """Scale peaks."""
        scaled_peaks = deepcopy(self.peaks)
        fac = self._preproc_img.shape[0] / self.orig_img.shape[0]

        for peaks in scaled_peaks.itervalues():  # clusters and stars
            peaks['xcen'] *= fac
            peaks['ycen'] *= fac

        return scaled_peaks

    def _crop_peaks(self, scaled_peaks, key, ix1, ix2, iy1, iy2):
        """Crop peaks."""
        if key in scaled_peaks:
            cropped_peak = deepcopy(scaled_peaks[key])
            cropped_peak = cropped_peak[(cropped_peak['xcen'] > ix1) &
                                        (cropped_peak['xcen'] < ix2 - 1) &
                                        (cropped_peak['ycen'] > iy1) &
                                        (cropped_peak['ycen'] < iy2 - 1)]
            cropped_peak['xcen'] -= ix1
            cropped_peak['ycen'] -= iy1
            log.info('{0} before and after cropping: {1} -> {2}'.format(
                key, len(scaled_peaks[key]), len(cropped_peak)))
        else:
            cropped_peak = []

        return cropped_peak

    def make(self):
        """Make the model."""

        # Don't want to change input for repeated calls
        image = deepcopy(self._preproc_img)

        # Scale and combine masks
        scaled_masks, disk, spiralarms = self._process_masks()
        scaled_peaks = self._process_peaks()

        log.info('Smoothing {0} region(s)'.format(
                len(scaled_masks[self.smooth_key])))
        image = remove_stars(image, scaled_masks[self.smooth_key])

        log.info('Filtering image (first pass)')
        image = ndimage.filters.median_filter(image, size=10)  # For 1k image
        image = np.ma.masked_equal(image, 0.0)
        image = iutils.normalize(image, True)

        if self.is_spiralgal:
            log.info('Scaling top')
            image = scale_top(image, mask=disk)
            image = iutils.normalize(image, True)

        # Only works for single-disk image.
        # Do this even for smooth intensity map to avoid sharp peak in model.

        cusp_mask = None
        cusp_texture_flat = None

        if disk is not None:
            log.info('Replacing cusp')
            cusp_rad = 20  # For 1k image
            cusp_texture = replace_cusp(
                image, mask=disk, radius=cusp_rad, height=40, percent=10)
            cusp_mask = cusp_texture > 0

            if not self.has_intensity:
                cusp_texture_flat = replace_cusp(
                    image, mask=disk, radius=cusp_rad, height=10, percent=None)

            image[cusp_mask] = cusp_texture[cusp_mask]

        log.info('Emphasizing regions')
        image = emphasize_regions(
            image, scaled_masks[self.small_dots_key] +
            scaled_masks[self.dots_key] + scaled_masks[self.lines_key])

        image, iy1, iy2, ix1, ix2 = iutils.crop_image(image, _max=1.0)
        log.info('Cropped image shape: {0}'.format(image.shape))

        # Also crop masks and lists
        croppedmasks, disk, spiralarms = self._crop_masks(
            scaled_masks, ix1, ix2, iy1, iy2)
        if cusp_mask is not None:
            cusp_mask = cusp_mask[iy1:iy2, ix1:ix2]
        if cusp_texture_flat is not None:
            cusp_texture_flat = cusp_texture_flat[iy1:iy2, ix1:ix2]

        clusters = self._crop_peaks(
            scaled_peaks, self.clusters_key, ix1, ix2, iy1, iy2)
        markstars = self._crop_peaks(
            scaled_peaks, self.stars_key, ix1, ix2, iy1, iy2)

        log.info(
            'Filtering image (second pass, height={0})'.format(self.height))
        image = ndimage.filters.median_filter(image, 10)  # For 1k image
        image = ndimage.filters.gaussian_filter(image, 3)  # Magic?
        image = np.ma.masked_equal(image, 0)
        image = iutils.normalize(image, True, self.height)

        # Generate monochrome intensity for GUI preview
        self._preview_intensity = deepcopy(image.data)

        # Generate list of peaks for GUI preview
        self._final_peaks = {
            self.clusters_key: clusters,
            self.stars_key: markstars}

        # To store non-overlapping key-coded texture info
        self._preview_masks = np.zeros(
            self._preview_intensity.shape, dtype='S10')

        # Texture layers
        if self.has_texture:

            # Dots and lines

            if self.is_spiralgal:
                log.info('Adding textures for spiral arms and disk')

                # At this point, unsuppressed regions that are not part of disk
                # means spiral arms
                self._galaxy_texture(image, lmask=disk, cmask=cusp_mask)

            else:
                self._texture_layer = np.zeros(image.shape)

                # Apply layers from bottom up
                for layer_key in self.layer_order[::-1]:
                    if layer_key == self.dots_key:
                        texture_func = DOTS
                    elif layer_key == self.small_dots_key:
                        texture_func = SMALL_DOTS
                    elif layer_key == self.lines_key:
                        texture_func = LINES
                    else:
                        warnings.warn('{0} is not a valid texture, skipping...'
                                      ''.format(layer_key), AstropyUserWarning)
                        continue

                    log.info('Adding {0}'.format(layer_key))
                    for mask in croppedmasks[layer_key]:
                        cur_texture = texture_func(image, mask)
                        self._texture_layer[mask] = cur_texture[mask]
                        self._preview_masks[mask] = layer_key

            image += self._texture_layer

            # Stars and star clusters

            clustexarr = None

            if self.has_intensity:
                h_percentile = 75
                s_height = 5
            else:
                h_percentile = None
                s_height = 10

            # Add star clusters

            n_clus_added = 0

            if len(clusters) > 0:
                maxclusflux = max(clusters['flux'])
                clusters.sort('flux')  # Lower flux added first

            for cluster in clusters:
                c1 = make_star_cluster(
                    image, cluster,  maxclusflux, height=s_height,
                    h_percentile=h_percentile, r_fac_add=self.clus_r_fac_add,
                    r_fac_mul=self.clus_r_fac_mul, n_craters=3)
                if not np.any(c1):
                    continue
                if clustexarr is None:
                    clustexarr = c1
                else:
                    clustexarr = add_clusters(clustexarr, c1)
                n_clus_added += 1

            log.info('Displaying {0} clusters'.format(n_clus_added))

            # Add individual stars

            n_star_added = 0

            if len(markstars) > 0:
                maxstarflux = max(markstars['flux'])
                markstars.sort('flux')  # Lower flux added first

            for mstar in markstars:
                s1 = make_star_cluster(
                    image, mstar, maxstarflux, height=s_height,
                    h_percentile=h_percentile, r_fac_add=self.star_r_fac_add,
                    r_fac_mul=self.star_r_fac_mul, n_craters=1)
                if not np.any(s1):
                    continue
                if clustexarr is None:
                    clustexarr = s1
                else:
                    clustexarr = add_clusters(clustexarr, s1)
                n_star_added += 1

            log.info('Displaying {0} stars'.format(n_star_added))

            # Both stars and star clusters share the same mask

            if clustexarr is not None:
                clustermask = clustexarr != 0
                if self.has_intensity:
                    image[clustermask] = clustexarr[clustermask]
                else:
                    self._texture_layer[clustermask] = clustexarr[clustermask]

            # For texture-only model, need to add cusp to texture layer
            if not self.has_intensity and cusp_mask is not None:
                self._texture_layer[cusp_mask] = cusp_texture_flat[cusp_mask]

        # endif has_texture

        # Renormalize again so that height is more predictable
        image = iutils.normalize(image, True, self.height)

        if isinstance(image, np.ma.core.MaskedArray):
            image = image.data

        log.info('Making base')
        if self.double:
            base_dist = 100  # Magic? Was 60. Widened for nibbler.
            base_height = self.base_thickness / 2  # Doubles in mesh creator
            base = make_base(image, dist=base_dist, height=base_height,
                             snapoff=True)
        else:
            base = make_base(image, height=self.base_thickness, snapoff=False)

        if self.has_intensity:
            self._out_image = image + base
        else:
            self._out_image = self._texture_layer + base

    def _galaxy_texture(self, galaxy, lmask=None, cmask=None,
                        sd_percentile=60.0, scale=1.0, fil_size=25,
                        fil_invscale=1.1):
        """Like :func:`galaxy_texture` but populates ``self._preview_masks``."""
        galmax = galaxy.max()
        maxfilt = ndimage.filters.maximum_filter(galaxy, fil_size)

        # Try to automatically find disk if not given
        if lmask is None:
            log.info('No mask given; Attempting auto-find disk and spiral arms')
            fac = galmax - scale * galaxy.std()
            #fac = galmax - fil_invscale * galaxy.std()
            lmask = galaxy > fac
            dmask = galaxy <= fac
        else:
            dmask = ~lmask

        # Mark spiral arms as dots
        x = maxfilt / fil_invscale - galaxy
        dotmask = (x <= 0) & dmask
        dots = DOTS(galaxy, dotmask)
        self._preview_masks[dotmask] = self.dots_key

        # Mark disk as lines, except for galactic center
        x = maxfilt + 5  # Magic?
        linemask = (x <= galmax) & lmask
        lines = LINES(galaxy, linemask)
        self._preview_masks[linemask] = self.lines_key

        if self.has_intensity:
            # No small dots at all
            #sdmask = None

            # Small dots mark everything
            sdmask = (~linemask) & (~dotmask) & dmask
        else:
            # Mark dust lanes at given percentile intensity contour
            sdmask = ((galaxy > np.percentile(galaxy, sd_percentile)) &
                      (~linemask) & (~dotmask) & dmask)

        if sdmask is not None:
            small_dots = SMALL_DOTS(galaxy, sdmask)
            self._preview_masks[sdmask] = self.small_dots_key
        else:
            small_dots = np.zeros_like(galaxy)

        filt = ndimage.filters.maximum_filter(lines, fil_size - 15)  # 10
        fmask = filt != 0
        dots[fmask] = 0
        small_dots[fmask] = 0
        self._preview_masks[fmask & (self._preview_masks != self.lines_key)] = ''

        # Exclude background and cusp
        bgmask = galaxy < 1
        dots[bgmask] = 0
        small_dots[bgmask] = 0
        self._preview_masks[bgmask & (self._preview_masks != self.lines_key)] = ''

        self._texture_layer = small_dots + dots + lines
        if cmask is not None:
            self._texture_layer[cmask] = 0


def make_model(image, region_masks=defaultdict(list), peaks={}, height=150.0,
               base_thickness=20, clus_r_fac_add=10, clus_r_fac_mul=5,
               star_r_fac_add=10, star_r_fac_mul=5,
               layer_order=['lines', 'dots', 'dots_small'], double=False,
               has_texture=True, has_intensity=True, is_spiralgal=False):
    """Apply a number of image transformations to enable
    the creation of a meaningful 3D model for an astronomical
    image from a Numpy array.

    Boundaries are set by :func:`emphasize_regions` and
    :func:`~astro3d.utils.imageutils.crop_image`.

    .. note:: Not used.

    Parameters
    ----------
    image : ndarray
        Image array to process.

    region_masks : dict
        A dictionary that maps each texture type to a list of
        corresponding boolean masks.

    peaks : dict
        A dictionary that maps each texture type to a `astropy.table.Table`.

    height : float
        The maximum height above the base.

    base_thickness : int
        Thickness of the base so model is stable when printed
        on its side.

    clus_r_fac_add, clus_r_fac_mul, star_r_fac_add, star_r_fac_mul : float
        Crater radius scaling factors for star clusters and stars,
        respectively. See :func:`make_star_cluster`.

    layer_order : list
        Order of texture layers (dots, lines) to apply.
        Top/foreground layer overwrites the bottom/background.
        This is only used if ``is_spiralgal=False`` and ``has_texture=True``.

    double : bool
        Double- or single-sided.

    has_texture : bool
        Apply textures.

    has_intensity : bool
        Generate intensity map.

    is_spiralgal : bool
        Special processing for a single spiral galaxy.

    Returns
    -------
    out_image : ndarray
        Prepared image ready for STL.

    """
    if not has_texture and not has_intensity:
        raise ValueError('Model must have textures or intensity!')

    smooth_key = 'smooth'
    small_dots_key = 'dots_small'
    dots_key = 'dots'
    lines_key = 'lines'
    disk = None
    spiralarms = None

    # Old logic specific to single spiral galaxy
    if is_spiralgal:
        smooth_key = 'remove_star'
        small_dots_key = None
        lines_key = 'disk'
        dots_key = 'spiral'

        if len(region_masks[lines_key]) > 0:
            disk = region_masks[lines_key][0]

        if len(region_masks[dots_key]) > 0:
            spiralarms = region_masks[dots_key][0]

    log.info('Input image shape: {0}'.format(image.shape))
    imsz = max(image.shape)  # GUI allows only approx. 1000

    log.info('Smoothing {0} region(s)'.format(len(region_masks[smooth_key])))
    image = remove_stars(image, region_masks[smooth_key])

    log.info('Filtering image (first pass)')
    fil_size = int(0.01 * imsz)  # imsz / 100
    image = ndimage.filters.median_filter(image, size=fil_size)
    image = np.ma.masked_equal(image, 0.0)
    image = iutils.normalize(image, True)

    if is_spiralgal:
        log.info('Scaling top')
        image = scale_top(image, mask=disk)
        image = iutils.normalize(image, True)

    # Only works for single-disk image.
    # Do this even for smooth intensity map to avoid sharp peak in model.
    cusp_mask = None
    cusp_texture_flat = None
    if disk is not None:
        log.info('Replacing cusp')
        cusp_rad = 0.02 * imsz  # 20
        cusp_texture = replace_cusp(
            image, mask=disk, radius=cusp_rad, height=40, percent=10)
        cusp_mask = cusp_texture > 0

        if not has_intensity:
            cusp_texture_flat = replace_cusp(
                image, mask=disk, radius=cusp_rad, height=10, percent=None)

        image[cusp_mask] = cusp_texture[cusp_mask]

    log.info('Emphasizing regions')
    image = emphasize_regions(
        image, region_masks[small_dots_key] + region_masks[dots_key] +
        region_masks[lines_key])

    log.info('Cropping image')
    image, iy1, iy2, ix1, ix2 = iutils.crop_image(image, _max=1.0)
    log.info('Current image shape: {0}'.format(image.shape))

    log.info('Cropping region masks')
    croppedmasks = defaultdict(list)
    for key, mlist in region_masks.iteritems():
        if key == smooth_key:  # Smoothing already done
            continue
        for mask in mlist:
            croppedmasks[key].append(mask[iy1:iy2, ix1:ix2])
    region_masks = croppedmasks
    if is_spiralgal:
        if len(region_masks[lines_key]) > 0:
            disk = region_masks[lines_key][0]
        if len(region_masks[dots_key]) > 0:
            spiralarms = region_masks[dots_key][0]
    if cusp_mask is not None:
        cusp_mask = cusp_mask[iy1:iy2, ix1:ix2]
    if cusp_texture_flat is not None:
        cusp_texture_flat = cusp_texture_flat[iy1:iy2, ix1:ix2]

    if 'clusters' in peaks:
        clusters = peaks['clusters']
        log.info('Clusters before cropping: {0}'.format(len(clusters)))
        clusters = clusters[(clusters['xcen'] > ix1) &
                            (clusters['xcen'] < ix2 - 1) &
                            (clusters['ycen'] > iy1) &
                            (clusters['ycen'] < iy2 - 1)]
        clusters['xcen'] -= ix1
        clusters['ycen'] -= iy1
        log.info('Clusters after cropping: {0}'.format(len(clusters)))
    else:
        clusters = []

    if 'stars' in peaks:
        markstars = peaks['stars']
        log.info('Stars before cropping: {0}'.format(len(markstars)))
        markstars = markstars[(markstars['xcen'] > ix1) &
                              (markstars['xcen'] < ix2 - 1) &
                              (markstars['ycen'] > iy1) &
                              (markstars['ycen'] < iy2 - 1)]
        markstars['xcen'] -= ix1
        markstars['ycen'] -= iy1
        log.info('Stars after cropping: {0}'.format(len(markstars)))
    else:
        markstars = []

    log.info('Filtering image (second pass, height={0})'.format(height))
    image = ndimage.filters.median_filter(image, fil_size)  # 10
    image = ndimage.filters.gaussian_filter(image, 3)  # Magic?
    image = np.ma.masked_equal(image, 0)
    image = iutils.normalize(image, True, height)

    # Texture layer that is added later overwrites previous layers if overlap
    if has_texture:

        # Dots and lines

        if is_spiralgal:
            log.info('Adding textures for spiral arms and disk')

            # At this point, unsuppressed regions that are not part of disk
            # means spiral arms
            texture_layer = galaxy_texture(
                image, lmask=disk, cmask=cusp_mask, has_intensity=has_intensity)

        else:
            texture_layer = np.zeros(image.shape)

            # Apply layers from bottom up
            for layer_key in layer_order[::-1]:
                if layer_key == dots_key:
                    texture_func = DOTS
                elif layer_key == small_dots_key:
                    texture_func = SMALL_DOTS
                elif layer_key == lines_key:
                    texture_func = LINES
                else:
                    warnings.warn('{0} is not a valid texture, skipping...'
                                  ''.format(layer_key), AstropyUserWarning)
                    continue

                log.info('Adding {0}'.format(layer_key))
                for mask in region_masks[layer_key]:
                    cur_texture = texture_func(image, mask)
                    texture_layer[mask] = cur_texture[mask]

        image += texture_layer

        # Stars and star clusters

        clustexarr = None

        if has_intensity:
            h_percentile = 75
            s_height = 5
        else:
            h_percentile = None
            s_height = 10

        # Add star clusters

        n_clus_added = 0

        if len(clusters) > 0:
            maxclusflux = max(clusters['flux'])

        for cluster in clusters:
            c1 = make_star_cluster(
                image, cluster,  maxclusflux, height=s_height,
                h_percentile=h_percentile, r_fac_add=clus_r_fac_add,
                r_fac_mul=clus_r_fac_mul, n_craters=3)
            if not np.any(c1):
                continue
            if clustexarr is None:
                clustexarr = c1
            else:
                clustexarr = add_clusters(clustexarr, c1)
            n_clus_added += 1

        log.info('Displaying {0} clusters'.format(n_clus_added))

        # Add individual stars

        n_star_added = 0

        if len(markstars) > 0:
            maxstarflux = max(markstars['flux'])

        for mstar in markstars:
            s1 = make_star_cluster(
                image, mstar, maxstarflux, height=s_height,
                h_percentile=h_percentile, r_fac_add=star_r_fac_add,
                r_fac_mul=star_r_fac_mul, n_craters=1)
            if not np.any(s1):
                continue
            if clustexarr is None:
                clustexarr = s1
            else:
                clustexarr = add_clusters(clustexarr, s1)
            n_star_added += 1

        log.info('Displaying {0} stars'.format(n_star_added))

        # Both stars and star clusters share the same mask

        if clustexarr is not None:
            clustermask = clustexarr != 0
            if has_intensity:
                image[clustermask] = clustexarr[clustermask]
            else:
                texture_layer[clustermask] = clustexarr[clustermask]

        # For texture-only model, need to add cusp to texture layer
        if not has_intensity and cusp_mask is not None:
            texture_layer[cusp_mask] = cusp_texture_flat[cusp_mask]

    # endif has_texture

    # Renormalize again so that height is more predictable
    image = iutils.normalize(image, True, height)

    if isinstance(image, np.ma.core.MaskedArray):
        image = image.data

    log.info('Making base')
    if double:
        base_dist = 100  # Magic? Was 60.
        base_height = base_thickness / 2  # Doubles in mesh creator
        base = make_base(image, dist=base_dist, height=base_height,
                         snapoff=True)
    else:
        base = make_base(image, height=base_thickness, snapoff=False)

    if has_intensity:
        out_image = image + base
    else:
        out_image = texture_layer + base

    return out_image


def remove_stars(input_image, starmasks):
    """Patches all bright/foreground stars marked as such by the user.

    Parameters
    ----------
    input_image : ndimage

    starmasks : list
        List of boolean masks of foreground stars that need to be patched.

    Returns
    -------
    image : ndimage

    """
    image = deepcopy(input_image)

    for mask in starmasks:
        ypoints, xpoints = np.where(mask)
        dist = max(ypoints.ptp(), xpoints.ptp())
        xx = [xpoints, xpoints, xpoints + dist, xpoints - dist]
        yy = [ypoints + dist, ypoints - dist, ypoints, ypoints]
        newmasks = []
        warn_msg = []

        for x, y in zip(xx, yy):
            try:
                pts = image[y, x]
            except IndexError as e:
                warn_msg.append('\t{0}'.format(e))
            else:
                newmasks.append(pts)

        if len(newmasks) == 0:
            warnings.warn('remove_stars() failed:\n{0}'.format(
                '\n'.join(warn_msg)), AstropyUserWarning)
            continue

        medians = [newmask.mean() for newmask in newmasks]
        index = np.argmax(medians)
        image[mask] = newmasks[index]

    return image


def scale_top(input_image, mask=None, percent=30, factor=10.0):
    """Linear scale of very high values of image.

    Parameters
    ----------
    input_image : ndarray
        Image array.

    mask : ndarray
        Mask of region with very high values. E.g., disk.

    percent : float
        Percentile between 0 and 100, inclusive.
        Only used if ``mask`` is given.

    factor : float
        Scaling factor.

    Returns
    -------
    image : ndarray
        Scaled image.

    """
    image = deepcopy(input_image)

    if mask is None:
        top = image.mean() + image.std()
    else:
        top = np.percentile(image[mask], percent)

    topmask = image > top
    image[topmask] = top + (image[topmask] - top) * factor / image.max()

    return image


def replace_cusp(image, mask=None, radius=20, height=40, percent=10):
    """Replaces the center of the galaxy, which would be
    a sharp point, with a crater.

    Parameters
    ----------
    image : ndarray
        Image array.

    mask : ndarray
        Mask of the disk.

    radius : int
        Radius of the crater in pixels.

    height : int
        Height of the crater.

    percent : float or `None`
        Percentile between 0 and 100, inclusive, used to
        re-adjust height of marker.
        If `None` is given, then no readjustment is done.

    Returns
    -------
    cusp_texture : ndarray
        Crater values to be added.

    """
    cusp_texture = np.zeros(image.shape)

    if mask is None:
        y, x = np.where(image == image.max())
    else:
        a = np.ma.array(image.data, mask=~mask)
        y, x = np.where(a == a.max())

    if not np.isscalar(y):
        med = len(y) // 2
        y, x = y[med], x[med]

    log.info('\tCenter of galaxy at X={0} Y={1}'.format(x, y))

    ymin = max(y - radius, 0)
    ymax = min(y + radius, image.shape[0])
    xmin = max(x - radius, 0)
    xmax = min(x + radius, image.shape[1])

    if percent is None:
        top = 0.0
    else:
        top = np.percentile(image[ymin:ymax, xmin:xmax], percent)

    star = make_star(radius, height)
    smask = star != -1

    diam = 2 * radius + 1
    ymax = min(ymin + diam, image.shape[0])
    xmax = min(xmin + diam, image.shape[1])
    cusp_texture[ymin:ymax, xmin:xmax][smask] = top + star[smask]

    return cusp_texture


def emphasize_regions(input_image, masks, threshold=20, niter=2):
    """Emphasize science data and suppress background.

    Parameters
    ----------
    input_image : ndarray

    masks : list
        List of masks that mark areas of interest.
        If no mask provided (empty list), entire
        image is used for calculations.

    threshold : float
        After regions are emphasized, values less than
        this are set to zero.

    niter : int
        Number of iterations.

    Returns
    -------
    image : ndarray

    """
    image = deepcopy(input_image)
    n_masks = len(masks)

    for i in range(niter):
        if n_masks < 1:
            _min = image.mean()
        else:
            _min = min([image[mask].mean() for mask in masks])
        _min -= image.std() * 0.5
        minmask = image < _min
        image[minmask] =  image[minmask] * (image[minmask] / _min)

    # Remove low bound
    boolarray = image < threshold
    log.debug('# background pix set to zero: {0}'.format(len(image[boolarray])))
    image[boolarray] = 0

    return image


def make_star(radius, height):
    """Creates a crater-like depression that can be used
    to represent a star.

    Similar to :func:`astro3d.utils.texture.make_star`.

    """
    a = np.arange(radius * 2 + 1)
    x, y = np.meshgrid(a, a)
    r = np.sqrt((x - radius) ** 2 + (y - radius) **2)
    star = height / radius ** 2 * r ** 2
    star[r > radius] = -1
    return star


def make_star_cluster(image, peak, max_intensity, r_fac_add=15, r_fac_mul=5,
                      height=5, h_percentile=75, fil_size=10, n_craters=3):
    """Mark star or star cluster for given position.

    Parameters
    ----------
    image : ndarray

    peak : `astropy.table.Table` row
        One star or star cluster entry.

    max_intensity : float
        Max intensity for all the stars or star clusters.

    r_fac_add, r_fac_mul : number
        Scaling factors to be added and multiplied to
        intensity ratio to determine marker radius.

    height : number
        Height of the marker for :func:`make_star`.

    h_percentile : float or `None`
        Percentile between 0 and 100, inclusive, used to
        re-adjust height of marker.
        If `None` is given, then no readjustment is done.

    fil_size : int
        Filter size for :func:`~scipy.ndimage.filters.maximum_filter`.

    n_craters : {1, 3}
        Star cluster is marked with ``3``. For single star, use ``1``.

    Returns
    -------
    array : ndarray

    """
    array = np.zeros(image.shape)

    x, y, intensity = peak['xcen'], peak['ycen'], peak['flux']
    radius = r_fac_add + r_fac_mul * intensity / float(max_intensity)
    #log.info('\tcluster radius = {0}'.format(radius, r_fac_add, r_fac_mul))
    star = make_star(radius, height)
    diam = 2 * radius
    r = star.shape[0]
    dr = r / 2
    star_mask = star != -1
    imx1 = max(int(x - diam), 0)
    imx2 = min(int(x + diam), image.shape[1])
    imy1 = max(int(y - diam), 0)
    imy2 = min(int(y + diam), image.shape[0])

    if n_craters == 1:
        centers = [(y, x)]
    else:  # 3
        dy = 0.5 * radius * np.sqrt(3)
        centers = [(y + dy, x), (y - dy, x + radius), (y - dy, x - radius)]

    if h_percentile is None:
        _max = 0.0
    else:
        try:
            _max = np.percentile(image[imy1:imy2, imx1:imx2], h_percentile)
        except ValueError as e:
            warnings.warn('Make star/cluster failed: {0}\n\timage[{1}:{2},'
                          '{3}:{4}]'.format(e, imy1, imy2, imx1, imx2),
                          AstropyUserWarning)
            return array

    for (cy, cx) in centers:
        xx1, xx2, yy1, yy2, sx1, sx2, sy1, sy2 = iutils.calc_insertion_pos(
            array, star, int(cx - dr), int(cy - dr))
        cur_smask = star_mask[sy1:sy2, sx1:sx2]
        cur_star = star[sy1:sy2, sx1:sx2]
        array[yy1:yy2, xx1:xx2][cur_smask] = _max + cur_star[cur_smask]

    if h_percentile is not None:
        filt = ndimage.filters.maximum_filter(array, fil_size)
        mask = (filt > 0) & (image > filt) & (array == 0)
        array[mask] = filt[mask]

    return array


def add_clusters(input_cluster1, cluster2):
    """Add two star clusters together.

    Parameters
    ----------
    input_cluster1, cluster2 : ndarray
        See :func:`make_star_cluster`.

    Returns
    -------
    cluster1 : ndarray

    """
    cluster1 = deepcopy(input_cluster1)
    mask = cluster2 != 0

    if cluster1[mask].min() < cluster2[mask].min():
        m = mask
    else:
        m = cluster1 == 0

    cluster1[m] = cluster2[m]
    return cluster1


def dots_from_mask(image, mask, hexgrid_spacing=7, dots_width=5,
                   dots_scale=3.2):
    """Apply dots texture to region marked by given mask.

    Parameters
    ----------
    image : ndarray
        Input image with background already suppressed.

    mask : ndarray
        Boolean mask of the region to be marked.

    hexgrid_spacing : int
        Spacing for :func:`~astro3d.utils.texture.hex_grid` to
        populate dots.

    dots_width : int
        Width of each dot.

    dots_scale : float
        Scaling for dot height.

    Returns
    -------
    dots : ndarray
        Output array with texture values.

    Examples
    --------
    Texture for NGC 602 dust region:

    >>> gastex = dots_from_mask(
    ...     image, gasmask, hexgrid_spacing=7, dots_width=7,
    ...     dots_scale=1.0)

    Texture for NGC 602 dust and gas combined region:

    >>> dustgastex = dots_from_mask(
    ...     image, dustgasmask, hexgrid_spacing=10, dots_width=7,
    ...     dots_scale=3.0)

    Alternate texture for NGC 602 gas region:

    >>> dusttex = dots_from_mask(
    ...     image, dustmask, hexgrid_spacing=20, dots_width=7,
    ...     dots_scale=3.0)

    """
    dots = _texture.dots('linear', image.shape, dots_width, dots_scale,
                         _texture.hex_grid(image.shape, hexgrid_spacing))
    dotmask = np.zeros_like(dots)
    dotmask[mask] = 1
    return dots * dotmask


def lines_from_mask(image, mask, lines_width=10, lines_spacing=20,
                    lines_scale=1.2, lines_orient=0):
    """Apply lines texture to region marked by given mask.

    Parameters
    ----------
    image : ndarray
        Input image with background already suppressed.

    mask : ndarray
        Boolean mask of the region to be marked.
        If not given, it is guessed from image values.

    lines_width, lines_spacing : int
        Width and spacing for each line.

    lines_scale : float
        Scaling for line height.

    lines_orient : float
        Orientation of the lines in degrees.

    Returns
    -------
    lines : ndarray
        Output array with texture values.

    Examples
    --------
    Texture for NGC 602 dust region:

    >>> dusttex = lines_from_mask(
    ...     image, dustmask, lines_width=15, lines_spacing=25,
    ...     lines_scale=0.7, lines_orient=0)

    """
    lines = _texture.lines('linear', image.shape, lines_width, lines_spacing,
                           lines_scale, lines_orient)
    linemask = np.zeros_like(lines)
    linemask[mask] = 1
    return lines * linemask


def galaxy_texture(galaxy, lmask=None, cmask=None, has_intensity=True,
                   sd_percentile=60.0, scale=1.0, fil_size=25,
                   fil_invscale=1.1):
    """Apply texture to the spiral arms and disk of galaxy.

    Lines to mark disk, and dots to mark spiral arms.
    Input array must be already pre-processed accordingly.

    .. note::

        Not used.

        ``scale`` works well for NGC 3344 (first test galaxy)
        but poorly for NGC 1566 (second test galaxy).

    Parameters
    ----------
    galaxy : ndarray
        Input array with background already suppressed.
        Unsuppressed regions that are not disk are assumed
        to be spiral arms.

    lmask, cmask : ndarray or `None`
        Boolean masks for disk and cusp.
        If not given, it is guessed from image values.

    has_intensity : bool
        If `False`, will also add small dots to mark dust lanes.

    sd_percentile : float
        Percentile for intensity cut-off to populate small dots texture.
        This is only used if `has_intensity=False`.

    scale : float
        Scaling for auto texture generation without mask.
        This is only used if ``lmask`` is not given.

    fil_size : int
        Filter size for :func:`~scipy.ndimage.filters.maximum_filter`.

    fil_invscale : float
        Filter is divided by this number.

    Returns
    -------
    textured_galaxy : ndarray
        Output array with texture values.

    """
    galmax = galaxy.max()
    maxfilt = ndimage.filters.maximum_filter(galaxy, fil_size)

    # Try to automatically find disk if not given
    if lmask is None:
        log.info('No mask given; Attempting auto-find disk and spiral arms')
        fac = galmax - scale * galaxy.std()
        #fac = galmax - fil_invscale * galaxy.std()
        lmask = galaxy > fac
        dmask = galaxy <= fac
    else:
        dmask = ~lmask

    # Mark spiral arms as dots
    x = maxfilt / fil_invscale - galaxy
    dotmask = (x <= 0) & dmask
    dots = DOTS(galaxy, dotmask)

    # Mark disk as lines, except for galactic center
    x = maxfilt + 5  # Magic?
    linemask = (x <= galmax) & lmask
    lines = LINES(galaxy, linemask)

    if has_intensity:
        # No small dots at all
        #sdmask = None

        # Small dots mark everything
        sdmask = (~linemask) & (~dotmask) & dmask
    else:
        # Mark dust lanes at given percentile intensity contour
        sdmask = ((galaxy > np.percentile(galaxy, sd_percentile)) &
                  (~linemask) & (~dotmask) & dmask)

    if sdmask is not None:
        small_dots = SMALL_DOTS(galaxy, sdmask)
    else:
        small_dots = np.zeros_like(galaxy)

    filt = ndimage.filters.maximum_filter(lines, fil_size - 15)  # 10
    fmask = filt != 0
    dots[fmask] = 0
    small_dots[fmask] = 0

    # Debug info
    #where = np.where(lines)
    #log.debug('line texture locations: {0}, '
    #          '{1}'.format(where[0].ptp(), where[1].ptp()))

    # Exclude background and cusp
    bgmask = galaxy < 1
    dots[bgmask] = 0
    small_dots[bgmask] = 0
    textured_galaxy = small_dots + dots + lines
    if cmask is not None:
        textured_galaxy[cmask] = 0

    return textured_galaxy


def make_base(image, dist=60, height=10, snapoff=True):
    """Used to create a stronger base for printing.
    Prevents model from shaking back and forth due to printer vibration.

    .. note::

        Raft can be added using Makerware during printing process.

    Parameters
    ----------
    image : ndarray

    dist : int
        Filter size for :func:`~scipy.ndimage.filters.maximum_filter`.
        Only used if ``snapoff=True``.

    height : int
        Height of the base.

    snapoff : bool
        If `True`, base is thin around object border so it
        can be snapped off. Set this to `False` for flat
        texture map or one sided prints.

    Returns
    -------
    max_filt : ndarray
        Array containing base values.

    """
    if snapoff:
        max_filt = ndimage.filters.maximum_filter(image, dist)
        max_filt[max_filt < 1] = -5  # Magic?
        max_filt[max_filt > 1] = 0
        max_filt[max_filt < 0] = height
    else:
        max_filt = np.zeros(image.shape) + height

    return max_filt


def combine_masks(masks):
    """Combine boolean masks into a single mask."""
    if len(masks) == 0:
        return masks

    return reduce(lambda m1, m2: m1 | m2, masks)


def find_peaks(image, remove=0, num=None, threshold=8, npix=10, minpeaks=35):
    """Identifies the brightest point sources in an image.

    Parameters
    ----------
    image : ndarray
        Image to find.

    remove : int
        Number of brightest point sources to remove.

    num : int
        Number of unrejected brightest point sources to return.

    threshold, npix : int
        Parameters for ``photutils.detect_sources()``.

    minpeaks : int
        This is the minimum number of peaks that has to be found,
        if possible.

    Returns
    -------
    peaks : list
        Point sources.

    """
    while threshold >= 4:
        segm_img = photutils.detect_sources(
            image, snr_threshold=threshold, npixels=npix, mask_val=0.0)
        isophot = photutils.segment_photometry(image, segm_img)
        if len(isophot['xcen']) >= minpeaks:
            break
        else:
            threshold -= 1

    isophot.sort('flux')
    isophot.reverse()

    if remove > 0:
        isophot.remove_rows(range(remove))

    if num is not None:
        peaks = isophot[:num]
    else:
        peaks = isophot

    return peaks


# Pre-defined textures (by Perry Greenfield for NGC 602)
# This is for XSIZE=1100 YSIZE=1344
#DOTS = partial(
#    dots_from_mask, hexgrid_spacing=10, dots_width=7, dots_scale=3.0)
#SMALL_DOTS = partial(
#    dots_from_mask, hexgrid_spacing=7, dots_width=7, dots_scale=1.0)
#LINES = partial(lines_from_mask, lines_width=15, lines_spacing=25,
#                lines_scale=0.7, lines_orient=0)

# Pre-defined textures (by Roshan Rao for NGC 3344 and NGC 1566)
# This is for roughly XSIZE=1000 YSIZE=1000
DOTS = partial(
    dots_from_mask, hexgrid_spacing=7, dots_width=5, dots_scale=3.2)
SMALL_DOTS = partial(
    dots_from_mask, hexgrid_spacing=4.5, dots_width=5, dots_scale=0.8)
LINES = partial(lines_from_mask, lines_width=10, lines_spacing=20,
                lines_scale=1.2, lines_orient=0)


######################################
# OLD FUNCTIONS - FOR REFERENCE ONLY #
######################################


def _scale_top_old(image):
    """Not used."""
    top = image.mean() + image.std()
    image[image > top] = top + (image[image > top] - top) * 10. / image.max()
    return image


def _replace_cusp_old(image):
    """Not used."""
    scale = 3
    jump = 1
    radius = None
    ratio = None
    while True:
        top = image.mean() + scale * image.std()
        to_replace = np.where(image > top)
        ymin, ymax = to_replace[0].min(), to_replace[0].max()
        xmin, xmax = to_replace[1].min(), to_replace[1].max()
        radius = max(xmax - xmin, ymax - ymin) / 2.
        log.info('radius = {0}'.format(radius))
        if ratio is None:
            ratio = image.shape[0] / radius
        if radius < 20:
            if jump > 0: jump *= -0.5
            scale += jump
        elif radius > 30:
            if jump < 0: jump *= -0.5
            scale += jump
        else:
            ratio = (image.shape[0] / float(radius)) / float(ratio)
            break
    star = make_star(radius, 40)
    image[ymin:ymin + 2*radius + 1, xmin:xmin + 2*radius + 1][star != -1] = top + star[star != -1]
    return image, ratio


def _prepFits(filename=None, array=None, height=150.0, spiralarms=None,
              disk=None, stars=None, clusters=None, rotation=0.0,
              filter_radius=2, replace_stars=True, texture=True, num=15,
              remove=0):
    """Prepares a fits file to be printed with a 3D printer.

    This is the original method used by Roshan to turn a numpy
    array to an STL file.

    .. note::

        Do not used. For reference only.

    Parameters
    ----------
    filename : str
        Image file to process.
        This is only used if ``array`` is not given.

    array : ndarray
        Image array to process.

    height : float
        The maximum height above the base.

    spiralarms, disk : Region
        Squish values below the average height of input regions.

    stars : list
        A list of very bright objects (usually stars) that need
        to be removed in order for proper scaling.

    clusters
        Add star clusters.

    rotation : float
        Number of degrees to rotate the image, usually to undo a
        previously rotated image.

    filter_radius : int
        The amount of smoothing to apply to the image.
        Keep between 2 and 5.

    replace_stars : bool
        Replaces high values with an artificial star that is
        better for texture.

    texture : bool
        Automatically applies a certain texture to galaxies.

    num : int
        Number of peaks to find.

    remove : list
        List of peaks to remove.

    Returns
    -------
    img : ndarray
        Prepared image ready for STL.

    """
    from astropy.io import fits

    # TODO: ratio is not a good indicator
    ratio = None

    img = array
    if not img:
        if not filename:
            raise ValueError("Must provide either filename or array")
        # Get file
        log.info("Getting file")
        img = fits.getdata(filename)
        img = np.flipud(img)
    h, w = img.shape

    if stars:
        log.info("Removing stars")
        if isinstance(stars, dict):
            stars = [stars]
        starmasks = [star.to_mask(img) for star in stars]
        for mask in starmasks:
            ypoints, xpoints = np.where(mask)
            dist = max(ypoints.ptp(), xpoints.ptp())
            newmasks = [img[ypoints+dist, xpoints], img[ypoints-dist, xpoints],
                        img[ypoints, xpoints+dist], img[ypoints, xpoints-dist]]
            medians = [newmask.mean() for newmask in newmasks]
            index = np.argmax(medians)
            img[mask] = newmasks[index]

    spiralarms = [arm.to_mask(img) for arm in spiralarms]
    disk = disk.to_mask(img)
    masks = spiralarms + [disk]

    if rotation:
        log.info("Rotating Image")

        if masks:
            masks = [ndimage.interpolation.rotate(
                    mask.astype(int), rotation).astype(bool) for mask in masks]
            spiralarms = masks[:-1]
            disk = masks[-1]

        img = ndimage.interpolation.rotate(img, rotation)

        log.info("Cropping image")

        if masks:
            img, masks = iutils.crop_image(img, 1.0, masks)[:2]
            spiralarms = masks[:-1]
            disk = masks[-1]
        else:
            img = iutils.crop_image(img, 1.0)[0]

    peaks = find_peaks(img, remove, num)

    # Filter values (often gets rid of stars), normalize
    log.info("Filtering image")
    img = ndimage.filters.median_filter(img, max(h, w) / 100)
    img = np.ma.masked_equal(img, 0.0)
    img = iutils.normalize(img, True)

    # Rescale very high values (cusp of galaxy, etc.)
    log.info("Scaling top")
    img = scale_top(img, disk, combine_masks(spiralarms))
    #img = scale_top_old(img)
    img = iutils.normalize(img, True)

    if replace_stars:
        log.info("Replacing stars")
        scale = 3
        jump = 1
        radius = None
        while True:
            top = img.mean() + scale * img.std()
            to_replace = np.where(img > top)
            ymin, ymax = to_replace[0].min(), to_replace[0].max()
            xmin, xmax = to_replace[1].min(), to_replace[1].max()
            radius = max(xmax-xmin, ymax-ymin) / 2.
            if ratio == None: ratio = h / radius
            log.info(radius)
            if radius < 20:
                if jump > 0: jump *= -0.5
                scale += jump
            elif radius > 30:
                if jump < 0: jump *= -0.5
                scale += jump
            else:
                ratio = (h / float(radius)) / float(ratio)
                break
        star = make_star(radius, 40)
        img[ymin:ymin+2*radius+1, xmin:xmin+2*radius+1][star != -1] = (
            top + star[star != -1])

    # Squish lower bound
    if spiralarms or disk:
        log.info("Squishing lower bound")
        img = emphasize_regions(img, masks)

    # Get rid of 'padding'
    log.info("Cropping image")
    if masks and clusters:
        img, masks, peaks = iutils.crop_image(img, 1.0, masks, peaks)
        spiralarms = masks[:-1]
        disk = masks[-1]
    elif masks:
        img, masks = iutils.crop_image(img, 1.0, masks)[:2]
        spiralarms = masks[:-1]
        disk = masks[-1]
    elif clusters:
        img, dummy, peaks = iutils.crop_image(img, 1.0, table=peaks)
    else:
        img = iutils.crop_image(img, 1.0)[0]

    # Filter, smooth, normalize again
    log.info("Filtering image")
    img = ndimage.filters.median_filter(img, 10) # Needs to be adjustable for image size
    img = ndimage.filters.gaussian_filter(img, filter_radius)
    img = np.ma.masked_equal(img, 0)
    img = iutils.normalize(img, True, height)

    clustermask = None
    if clusters:
        log.info("Adding clusters")
        clusters = reduce(
            add_clusters,
            [make_star_cluster(img, peak, peaks['flux'][0]) for peak in peaks])
        clustermask = clusters != 0
        img[clustermask] = clusters[clustermask]

    if texture:
        log.info("Adding texture")
        #texture = galaxy_texture(img, lmask=disk, dmask=combine_masks(spiralarms))
        texture = galaxy_texture(img, 1.1)
        #texture = a_texture(img, masks)
        if clusters is not None:
            texture[clustermask] = 0
        img = img + texture

    if isinstance(img, np.ma.core.MaskedArray):
        img = img.data

    return img


def _prepareImg(filename, height=30, filter_radius=None, crop=False,
               invert=False, compress=True):
    """An old method, used for testing img2stl.to_mesh on random images.

    .. note::

        Do not use. For reference only.

    """
    img = None
    if filename[-5:] == '.fits':
        f = fits.open(filename)
        for hdu in f:
            if isinstance(hdu.data, np.ndarray):
                img = hdu.data
                break
        f.close()
    else:
        img = iutils.img2array(filename)
    if crop != False:
        if np.isscalar(crop):
            img = iutils.crop_image(img, crop)[0]
        else:
            iutils.crop_image(img, 1.0)[0]

        if np.isscalar(crop):
            img = remove_background(img, crop)
        else:
            img = remove_background(img, 1.0)

    if compress and img.shape[0] > 500:
        img = iutils.resize_image(img, 500)
    if filter_radius:
        img = ndimage.filters.gaussian_filter(img, filter_radius)
    img = img - img.min()
    if invert:
        img = img.max() - img
    img = iutils.normalize(img, True, height)
    return np.fliplr(img)
