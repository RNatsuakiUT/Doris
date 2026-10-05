#!/usr/bin/env python3
"""
nisar_dump_data.py
Extract SLC data from a NISAR RSLC HDF5 and write raw complex_real4
(IEEE 754 float32 I/Q pairs, native byte order = little-endian on x86).

Usage:
  nisar_dump_data.py <NISAR_RSLC.h5> <output> [l0 lN p0 pN]
                     [--band LSAR|SSAR] [--freq A|B|LOW|HIGH] [--pol RH]

  l0, lN, p0, pN: 1-based inclusive line/pixel indices (Doris DBOW convention)

Band is auto-detected from /science if --band is omitted, the frequency
sub-band defaults to the first entry of listOfFrequencies (normally A, the
full-bandwidth swath), and the polarisation defaults to the first entry of
that sub-band's listOfPolarizations (S-band compact pol is RH/RV, not HH;
L-band frequencyB lists them in a different order from frequencyA).

Output: raw complex float32, n_lines x n_pixels x 8 bytes -> Doris complex_real4

Dependencies: h5py, numpy

Author: RN 2026
"""

import sys
import argparse

try:
    import h5py
    import numpy as np
except ImportError as e:
    sys.stderr.write('ERROR: Missing dependency: %s\n' % e)
    sys.stderr.write('Install with: pip install h5py numpy\n')
    sys.exit(1)

# Shared with nisar_dump_header2doris.py so both resolve --freq identically.
from nisar_common import dec, resolve_subband

# Rows written per read, to bound memory on very large frames.
CHUNK_LINES = 512


def main():
    ap = argparse.ArgumentParser(
        description='Dump NISAR RSLC SLC to raw complex_real4')
    ap.add_argument('input',  help='NISAR RSLC HDF5 file')
    ap.add_argument('output', help='Output raw binary file')
    ap.add_argument('l0', nargs='?', type=int, default=None,
                    help='First azimuth line, 1-based (Doris DBOW)')
    ap.add_argument('lN', nargs='?', type=int, default=None,
                    help='Last  azimuth line, 1-based (Doris DBOW)')
    ap.add_argument('p0', nargs='?', type=int, default=None,
                    help='First range pixel, 1-based (Doris DBOW)')
    ap.add_argument('pN', nargs='?', type=int, default=None,
                    help='Last  range pixel, 1-based (Doris DBOW)')
    ap.add_argument('--band', default=None, choices=['LSAR', 'SSAR'])
    ap.add_argument('--freq', default=None,
                    help='Frequency sub-band: a literal name (A, B) or a '
                         'relative selector LOW/HIGH resolved from the stored '
                         'centre frequency (default: the first sub-band)')
    ap.add_argument('--pol',  default=None)
    args = ap.parse_args()

    with h5py.File(args.input, 'r') as f:
        band = args.band
        if band is None:
            for cand in ('LSAR', 'SSAR'):
                if '/science/%s/RSLC' % cand in f:
                    band = cand
                    break
            if band is None:
                sys.stderr.write('ERROR: No /science/{LSAR,SSAR}/RSLC group found.\n')
                sys.exit(1)

        swath = '/science/%s/RSLC/swaths' % band
        fsub = resolve_subband(f, swath, args.freq)
        freq = '%s/frequency%s' % (swath, fsub)

        pol = args.pol
        if pol is None:
            try:
                pol = dec(f['%s/listOfPolarizations' % freq][()][0])
            except (KeyError, IndexError):
                pol = 'HH'

        slc_path = '%s/%s' % (freq, pol)
        if slc_path not in f:
            avail = list(f[freq].keys()) if freq in f else '(path not found)'
            sys.stderr.write('ERROR: Dataset %s not found.\n' % slc_path)
            sys.stderr.write('Available under %s: %s\n' % (freq, avail))
            sys.exit(1)

        ds = f[slc_path]
        n_lines, n_pixels = ds.shape

        # 1-based inclusive (Doris) -> 0-based half-open (Python)
        row0 = (args.l0 - 1) if args.l0 is not None else 0
        rowN = args.lN       if args.lN is not None else n_lines
        col0 = (args.p0 - 1) if args.p0 is not None else 0
        colN = args.pN       if args.pN is not None else n_pixels

        if row0 < 0 or col0 < 0 or rowN > n_lines or colN > n_pixels or \
           rowN <= row0 or colN <= col0:
            sys.stderr.write('ERROR: Requested window [%d:%d, %d:%d] is invalid '
                             'for dataset size [%d, %d]\n'
                             % (row0, rowN, col0, colN, n_lines, n_pixels))
            sys.exit(1)

        with open(args.output, 'wb') as fout:
            for r in range(row0, rowN, CHUNK_LINES):
                r2 = min(r + CHUNK_LINES, rowN)
                block = ds[r:r2, col0:colN]
                if block.dtype != np.complex64:
                    block = block.astype(np.complex64)
                block.tofile(fout)

    sys.stderr.write('nisar_dump_data: band=%s freq=%s pol=%s wrote %d lines x '
                     '%d pixels (complex_real4) to %s\n'
                     % (band, fsub, pol, rowN - row0, colN - col0, args.output))


if __name__ == '__main__':
    main()
