#!/usr/bin/env python3
"""
biomass_dump_data.py
Extract SLC data from ESA BIOMASS L1a product and write as raw complex_real4.

Usage:
  biomass_dump_data.py <amp_tiff> <output> [l0 lN p0 pN] [--pol HH|HV|VH|VV]

  amp_tiff: BIOMASS amplitude COG GeoTIFF (*_i_abs.tiff), all 4 polarisations as bands.
            The co-located phase file (*_i_phase.tiff) is derived automatically.
  l0, lN, p0, pN: 1-based indices (Doris DBOW convention)
  --pol: polarisation channel (default HH); maps to TIFF band HH=1, HV=2, VH=3, VV=4

Output: raw complex float32 binary (complex_real4):
  amplitude * exp(1j * phase), written line by line as numpy complex64.

BIOMASS L1a MDS (BPS_L1_PFD_v1_6_1.pdf §4.3.2):
  *_i_abs.tiff   — 4-band COG GeoTIFF, float32, amplitude [linear]
  *_i_phase.tiff — 4-band COG GeoTIFF, float32, phase [radians]
  Band order: HH=1, HV=2, VH=3, VV=4

Dependencies: numpy, gdal (via osgeo) or rasterio

Author: RN 2026
"""

import sys
import os
import argparse

try:
    import numpy as np
except ImportError:
    sys.stderr.write('ERROR: numpy not available. Install with: pip install numpy\n')
    sys.exit(1)

POL_BAND = {'HH': 1, 'HV': 2, 'VH': 3, 'VV': 4}

# BIOMASS MDS COGs use lerc_zstd compression, which needs GDAL >= 3.4.
# rasterio ships its own (newer) GDAL, so try it first; a system osgeo.gdal
# may import fine yet still fail to open the file for lack of the codec.
try:
    import rasterio
    import rasterio.windows
except ImportError:
    rasterio = None

try:
    from osgeo import gdal
except ImportError:
    gdal = None


def _read_rasterio(filepath, band, row0, rowN, col0, colN):
    with rasterio.open(filepath) as src:
        if band > src.count:
            raise IOError('%s has only %d band(s); requested band %d'
                          % (filepath, src.count, band))
        win = rasterio.windows.Window(col0, row0, colN - col0, rowN - row0)
        return src.read(band, window=win).astype(np.float32)


def _read_gdal(filepath, band, row0, rowN, col0, colN):
    ds = gdal.Open(filepath, gdal.GA_ReadOnly)
    if ds is None:
        raise IOError('GDAL cannot open %s (missing lerc_zstd codec?)' % filepath)
    try:
        if band > ds.RasterCount:
            raise IOError('%s has only %d band(s); requested band %d'
                          % (filepath, ds.RasterCount, band))
        arr = ds.GetRasterBand(band).ReadAsArray(
            col0, row0, colN - col0, rowN - row0)
        if arr is None:
            raise IOError('GDAL failed to read window from %s' % filepath)
        return arr.astype(np.float32)
    finally:
        del ds


def read_tiff_band(filepath, band, row0, rowN, col0, colN):
    """Read one band/window of a BIOMASS COG as float32, trying each backend."""
    errors = []
    for name, fn in (('rasterio', _read_rasterio if rasterio else None),
                     ('gdal',     _read_gdal if gdal else None)):
        if fn is None:
            errors.append('%s: not installed' % name)
            continue
        try:
            return fn(filepath, band, row0, rowN, col0, colN)
        except Exception as e:
            errors.append('%s: %s' % (name, e))
    raise IOError('Could not read %s\n  %s' % (filepath, '\n  '.join(errors)))


def main():
    parser = argparse.ArgumentParser(
        description='Dump BIOMASS L1a SLC to raw complex_real4')
    parser.add_argument('input',  help='Amplitude COG GeoTIFF (*_i_abs.tiff)')
    parser.add_argument('output', help='Output raw binary file')
    parser.add_argument('l0',  nargs='?', type=int, default=None,
                        help='First azimuth line, 1-based (Doris DBOW)')
    parser.add_argument('lN',  nargs='?', type=int, default=None,
                        help='Last  azimuth line, 1-based (Doris DBOW)')
    parser.add_argument('p0',  nargs='?', type=int, default=None,
                        help='First range pixel, 1-based (Doris DBOW)')
    parser.add_argument('pN',  nargs='?', type=int, default=None,
                        help='Last  range pixel, 1-based (Doris DBOW)')
    parser.add_argument('--pol', default='HH', choices=['HH', 'HV', 'VH', 'VV'],
                        help='Polarisation channel (default: HH)')
    args = parser.parse_args()

    if any(v is None for v in [args.l0, args.lN, args.p0, args.pN]):
        sys.stderr.write('ERROR: All four DBOW indices (l0 lN p0 pN) must be specified.\n')
        sys.exit(1)

    row0 = args.l0 - 1   # 0-based
    rowN = args.lN        # exclusive (0-based end)
    col0 = args.p0 - 1
    colN = args.pN

    band = POL_BAND[args.pol]

    # Derive phase file path
    amp_path = args.input
    if amp_path.endswith('_i_abs.tiff'):
        phase_path = amp_path[:-len('_i_abs.tiff')] + '_i_phase.tiff'
    elif amp_path.endswith('_i_abs.tif'):
        phase_path = amp_path[:-len('_i_abs.tif')] + '_i_phase.tif'
    else:
        # Try common suffix substitutions
        for sfx in ('_abs.tiff', '_abs.tif', '_amp.tiff', '_amp.tif'):
            if amp_path.endswith(sfx):
                base = amp_path[:-len(sfx)]
                phase_path = base + sfx.replace('_abs', '_phase').replace('_amp', '_phase')
                break
        else:
            phase_path = amp_path.replace('_abs', '_phase').replace('_amp', '_phase')

    if not os.path.isfile(phase_path):
        sys.stderr.write('ERROR: Phase file not found: %s\n' % phase_path)
        sys.stderr.write('       Expected alongside amplitude file: %s\n' % amp_path)
        sys.exit(1)

    if rasterio is None and gdal is None:
        sys.stderr.write('ERROR: No GeoTIFF reader available. '
                         'Install rasterio (preferred) or GDAL >= 3.4 '
                         '(lerc_zstd codec required).\n')
        sys.exit(1)

    try:
        amp   = read_tiff_band(amp_path,   band, row0, rowN, col0, colN)
        phase = read_tiff_band(phase_path, band, row0, rowN, col0, colN)
    except Exception as e:
        sys.stderr.write('ERROR reading BIOMASS GeoTIFFs: %s\n' % e)
        sys.exit(1)

    # Reconstruct complex SLC:  z = amplitude * exp(i * phase)
    data = (amp * np.exp(1j * phase.astype(np.float32))).astype(np.complex64)

    with open(args.output, 'wb') as fout:
        for line in data:
            line.tofile(fout)

    n_out_lines  = rowN - row0
    n_out_pixels = colN - col0
    sys.stderr.write('biomass_dump_data: pol=%s band=%d wrote %d lines x %d pixels '
                     '(complex_real4) to %s\n'
                     % (args.pol, band, n_out_lines, n_out_pixels, args.output))


if __name__ == '__main__':
    main()
