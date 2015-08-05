from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import os
import warnings
import argparse
import numpy as np
from astropy import log
from astropy.table import Table
from astropy.utils.exceptions import AstropyUserWarning
from astro3d.textures import TextureMask


def npz_to_fits(filename):
    """
    Convert texture mask from a .npz to FITS file.

    Parameters
    ----------
    filename : str
        The input filename containing the texture mask stored in the
        binary numpy format ('.npz').  The input file will be renamed by
        appending the filename with '.original'.  The output filename is
        that same as the input filename, but with '.npz' replaced by
        '.fits'.
    """

    fn_orig = filename + '.original'
    fits_filename = filename.replace('.npz', '.fits')
    os.rename(filename, fn_orig)
    log.info('Renaming {0} to {1}'.format(filename, fn_orig))

    log.info('Reading {0}'.format(fn_orig))
    fo = np.load(fn_orig)
    data = fo['data']
    texture_type = fo['name'].tostring()
    texture_mask = TextureMask(data, texture_type)
    texture_mask.save(fits_filename)
    log.info('Saved {0}'.format(fits_filename))


def convert_starlike_table(filename):
    """
    Convert table columns in a star-like texture file.

    The 'xcen' column will be renamed 'x_center'.  The 'ycen' column
    will be renamed 'y_center'.

    Parameters
    ----------
    filename : str
        The input filename containing the star or star cluster positions
        and fluxes.  The input file will be renamed by appending the
        filename with '.original'.  The output filename is that same as
        the input filename, but will end with '_new.txt' instead of
        '.txt'.
    """

    try:
        log.info('Reading {0}'.format(filename))
        t = Table.read(filename, format='ascii', guess=False)
    except:
        warnings.warn('Skipping {0}: Cannot read Table.'.format(filename),
                      AstropyUserWarning)
        return

    if 'xcen' not in t.colnames:
        warnings.warn('Skipping {0}: Table does not contain a '
                      '"xcen" column.'.format(filename), AstropyUserWarning)
        return

    fn_orig = filename + '.original'
    os.rename(filename, fn_orig)
    log.info('Renaming {0} to {1}'.format(filename, fn_orig))

    t.rename_column('xcen', 'x_center')
    t.rename_column('ycen', 'y_center')
    t.write(filename, format='ascii')
    log.info('Saved {0}'.format(filename))


def main(args=None):
    parser = argparse.ArgumentParser(
        description='Convert older texture masks to new format.')
    parser.add_argument('filename', metavar='filename', nargs='+',
                        help='filename containing content')
    args = parser.parse_args()

    for filename in args.filename:
        if filename.endswith('.npz'):
            npz_to_fits(filename)
        elif filename.endswith('.txt'):
            convert_starlike_table(filename)
        else:
            warnings.warn('Skipping {0}: Invalid file.'.format(filename),
                          AstropyUserWarning)
            continue


if __name__ == '__main__':
    main()