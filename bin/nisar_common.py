#!/usr/bin/env python3
"""
nisar_common.py
Shared helpers for nisar_dump_header2doris.py and nisar_dump_data.py.

The frequency sub-band resolution lives here rather than in each script
because the header reader and the data reader are handed the same --freq
string by processor.cc / readdata.cc and MUST resolve it identically.  If
they ever disagreed, the .res would describe one sub-band while the crop
contained another -- a silent, hard-to-spot corruption.

Author: RN 2026
"""

import sys

try:
    import h5py
    import numpy as np
except ImportError as e:  # pragma: no cover - reported by the calling script
    sys.stderr.write('ERROR: Missing dependency: %s\n' % e)
    raise

# Relative selectors, resolved against the centre frequency actually stored
# for each sub-band rather than against the sub-band letter.
_LOW_WORDS = ('L', 'LO', 'LOW')
_HIGH_WORDS = ('H', 'HI', 'HIGH')


def dec(v):
    """bytes/np.bytes_ -> str, otherwise unchanged."""
    if isinstance(v, (bytes, np.bytes_)):
        return v.decode('utf-8')
    return v


def list_subbands(f, swath):
    """Sub-band letters present under <swath>, e.g. ['A', 'B']."""
    if swath not in f:
        return []
    out = []
    for k in f[swath]:
        if not k.startswith('frequency'):
            continue
        if isinstance(f[swath][k], h5py.Group):
            out.append(k[len('frequency'):])
    return sorted(out)


def centre_frequency(f, swath, sub):
    """Centre frequency [Hz] of one sub-band, or None if not annotated."""
    for name in ('acquiredCenterFrequency', 'processedCenterFrequency'):
        path = '%s/frequency%s/%s' % (swath, sub, name)
        try:
            v = float(f[path][()])
        except (KeyError, TypeError, ValueError):
            continue
        if v > 0:
            return v
    return None


def resolve_subband(f, swath, request):
    """
    Turn a --freq request into an actual sub-band letter.

    Accepted forms:
      'A', 'B', ...        a literal sub-band name ('frequencyA' also works)
      'L' / 'LO' / 'LOW'   the sub-band with the LOWEST centre frequency
      'H' / 'HI' / 'HIGH'  the sub-band with the HIGHEST centre frequency
      None                 the first sub-band present (normally A)

    The relative selectors are deliberately resolved from the stored centre
    frequency, not from the letter: in NISAR L-band products frequencyB
    (1293.5 MHz) sits ABOVE frequencyA (1239.0 MHz), so 'HIGH' is B there,
    but nothing guarantees that ordering for a future sensor.

    Returns the sub-band letter.  Exits with a message on failure.
    """
    avail = list_subbands(f, swath)
    if not avail:
        sys.stderr.write('ERROR: no frequency sub-bands found under %s\n' % swath)
        sys.exit(1)

    if request is None:
        return avail[0]

    req = str(request).strip().upper()
    if req.startswith('FREQUENCY'):
        req = req[len('FREQUENCY'):]

    # ___ literal sub-band name ___
    if req in avail:
        return req

    # ___ relative selector ___
    if req in _LOW_WORDS or req in _HIGH_WORDS:
        want_low = req in _LOW_WORDS
        freqs = [(centre_frequency(f, swath, s), s) for s in avail]
        missing = [s for fc, s in freqs if fc is None]
        if missing:
            sys.stderr.write('ERROR: cannot resolve --freq %s: no centre frequency '
                             'annotated for sub-band(s) %s\n' % (request, missing))
            sys.exit(1)
        if len(avail) == 1:
            sys.stderr.write('WARNING: --freq %s requested but this product has only '
                             'sub-band %s (%.3f MHz); using it.\n'
                             % (request, avail[0], freqs[0][0] / 1e6))
            return avail[0]
        freqs.sort()
        if freqs[0][0] == freqs[-1][0]:
            sys.stderr.write('ERROR: cannot resolve --freq %s: all sub-bands share the '
                             'same centre frequency (%.3f MHz); name one of %s '
                             'explicitly.\n' % (request, freqs[0][0] / 1e6, avail))
            sys.exit(1)
        fc, sub = freqs[0] if want_low else freqs[-1]
        ladder = ', '.join('%s=%.3f MHz' % (s, v / 1e6) for v, s in freqs)
        sys.stderr.write('nisar: --freq %s -> frequency%s (%.3f MHz)  [%s]\n'
                         % (request, sub, fc / 1e6, ladder))
        return sub

    sys.stderr.write('ERROR: frequency sub-band %s not found (available: %s; '
                     'or use LOW/HIGH to pick by centre frequency)\n'
                     % (request, avail))
    sys.exit(1)
