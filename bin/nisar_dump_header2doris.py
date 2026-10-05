#!/usr/bin/env python3
"""
nisar_dump_header2doris.py
Extract NISAR RSLC (HDF5/ISCE3) metadata and write Doris .res format to stdout.

Usage:
  nisar_dump_header2doris.py <NISAR_RSLC.h5> [--band LSAR|SSAR]
                            [--freq A|B|LOW|HIGH] [--pol RH] > scratchres_nisar

Verified against real NISAR S-band RSLC (productVersion 1.2.1, ISCE3).
The band is auto-detected from /science if --band is not given, and the
polarisation defaults to the first entry of listOfPolarizations (S-band
compact pol is RH/RV, not HH).

Layout actually used (differs from earlier spec-based guesses):
  /science/<BAND>/identification/...            mission, look/pass, orbit, frame
  /science/<BAND>/RSLC/swaths/zeroDopplerTime         <- NOT under frequencyA
  /science/<BAND>/RSLC/swaths/zeroDopplerTimeSpacing  <- output grid, gives PRF
  /science/<BAND>/RSLC/swaths/frequency<F>/<POL>        complex64 SLC
  /science/<BAND>/RSLC/swaths/frequency<F>/slantRange        one-way [m]
  /science/<BAND>/RSLC/swaths/frequency<F>/slantRangeSpacing one-way [m]
  /science/<BAND>/RSLC/swaths/frequency<F>/acquiredCenterFrequency
  /science/<BAND>/RSLC/swaths/frequency<F>/processedRangeBandwidth
  /science/<BAND>/RSLC/swaths/frequency<F>/processedAzimuthBandwidth
      <F> is A (full bandwidth) or B (narrow band); L-band dual-band
      products carry both and their polarisation order can differ.
  /science/<BAND>/RSLC/metadata/orbit/{time,position,velocity}
      epoch lives in the 'units' attribute of time; there is NO referenceEpoch
  /science/<BAND>/RSLC/metadata/processingInformation/parameters/frequency<F>/
      {dopplerCentroid,slantRange,zeroDopplerTime}

Conventions:
  PRF must be the OUTPUT GRID rate 1/zeroDopplerTimeSpacing, not
  nominalAcquisitionPRF (the hardware PRF, which differs).
  Range_sampling_rate = c / (2 * slantRangeSpacing).
  Doris f_DC(tau) = a0 + a1*tau + a2*tau^2 with tau the two-way slant range
  time measured from the FIRST pixel [s] (slcimage.cc:1031); the ISCE3
  dopplerCentroid grid is resampled onto that basis here.

Dependencies: h5py, numpy

Author: RN 2026
"""

import sys
import datetime
import argparse

try:
    import h5py
    import numpy as np
except ImportError as e:
    sys.stderr.write('ERROR: Missing dependency: %s\n' % e)
    sys.stderr.write('Install with: pip install h5py numpy\n')
    sys.exit(1)

# Shared with nisar_dump_data.py so both resolve --freq identically.
from nisar_common import dec, resolve_subband

CODE_REVISION = '2.0'
SPEED_OF_LIGHT = 299792458.0  # m/s


def parse_epoch(s):
    """Parse 'seconds since YYYY-MM-DDTHH:MM:SS[.ffffff]' or a bare timestamp."""
    s = dec(s).strip()
    if 'since' in s:
        s = s.split('since', 1)[-1].strip()
    if s.endswith('Z'):
        s = s[:-1]
    # ISCE3 writes 8 fractional digits; datetime accepts at most 6
    if '.' in s:
        head, frac = s.split('.', 1)
        frac = ''.join(c for c in frac if c.isdigit())[:6]
        s = head + '.' + frac if frac else head
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError('Cannot parse epoch string: %r' % s)


def to_doris_time(dt):
    """datetime -> 'DD-Mon-YYYY HH:MM:SS.ffffff' (slcimage.cc truncates to .fff)."""
    months = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    return '%02d-%s-%04d %02d:%02d:%02d.%06d' % (
        dt.day, months[dt.month], dt.year,
        dt.hour, dt.minute, dt.second, dt.microsecond)


def secs_of_day(dt):
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return (dt - midnight).total_seconds()


def main():
    ap = argparse.ArgumentParser(
        description='Dump NISAR RSLC header to Doris .res format')
    ap.add_argument('input', help='NISAR RSLC HDF5 (.h5) file')
    ap.add_argument('--band', default=None, choices=['LSAR', 'SSAR'],
                    help='Radar band (default: auto-detect from /science)')
    ap.add_argument('--freq', default=None,
                    help='Frequency sub-band: a literal name (A, B) or a '
                         'relative selector LOW/HIGH resolved from the stored '
                         'centre frequency (default: the first sub-band)')
    ap.add_argument('--pol', default=None,
                    help='Polarisation (default: first of listOfPolarizations)')
    args = ap.parse_args()

    try:
        f = h5py.File(args.input, 'r')
    except Exception as e:
        sys.stderr.write('ERROR: Cannot open %s: %s\n' % (args.input, e))
        sys.exit(1)

    # ----- band -----
    band = args.band
    if band is None:
        for cand in ('LSAR', 'SSAR'):
            if '/science/%s/RSLC' % cand in f:
                band = cand
                break
        if band is None:
            sys.stderr.write('ERROR: No /science/{LSAR,SSAR}/RSLC group found.\n')
            sys.exit(1)

    base  = '/science/%s' % band
    rslc  = '%s/RSLC' % base
    swath = '%s/swaths' % rslc
    orbit = '%s/metadata/orbit' % rslc
    ident = '%s/identification' % base

    # ----- frequency sub-band (A/B, or LOW/HIGH by centre frequency) -----
    fsub  = resolve_subband(f, swath, args.freq)
    freq  = '%s/frequency%s' % (swath, fsub)
    parms = '%s/metadata/processingInformation/parameters/frequency%s' % (rslc, fsub)

    def val(path, default=None):
        try:
            return f[path][()]
        except KeyError:
            return default

    def sval(path, default='UNKNOWN'):
        v = val(path)
        return dec(v) if v is not None else default

    def fval(path, default=0.0):
        v = val(path)
        return float(v) if v is not None else default

    # ----- polarisation -----
    pol = args.pol
    pols = val('%s/listOfPolarizations' % freq)
    pol_list = [dec(p) for p in pols] if pols is not None else []
    if pol is None:
        pol = pol_list[0] if pol_list else 'HH'
    if pol_list and pol not in pol_list:
        sys.stderr.write('ERROR: polarisation %s not in %s\n' % (pol, pol_list))
        sys.exit(1)

    slc_path = '%s/%s' % (freq, pol)
    if slc_path not in f:
        sys.stderr.write('ERROR: Dataset %s not found.\n' % slc_path)
        sys.exit(1)
    n_lines, n_pixels = f[slc_path].shape

    # ----- azimuth timing (zeroDopplerTime sits on swaths, not frequencyA) -----
    zdt_ds = f['%s/zeroDopplerTime' % swath]
    epoch  = parse_epoch(zdt_ds.attrs.get('units', b'seconds since 2000-01-01T00:00:00'))
    t_first = float(zdt_ds[0])
    first_line_utc = to_doris_time(epoch + datetime.timedelta(seconds=t_first))

    dt_azi = fval('%s/zeroDopplerTimeSpacing' % swath, 0.0)
    if dt_azi > 0:
        prf = 1.0 / dt_azi
    else:
        prf = fval('%s/nominalAcquisitionPRF' % freq, 0.0)
        sys.stderr.write('WARNING: zeroDopplerTimeSpacing missing; using '
                         'nominalAcquisitionPRF %.6f Hz (hardware PRF, may not '
                         'match the image grid).\n' % prf)

    # ----- range timing -----
    slant_range = f['%s/slantRange' % freq]
    r_first = float(slant_range[0])                       # one-way [m]
    range_time_ms = 2.0 * r_first / SPEED_OF_LIGHT * 1000.0
    dr = fval('%s/slantRangeSpacing' % freq, 0.0)
    if dr <= 0 and slant_range.shape[0] > 1:
        dr = float(slant_range[1]) - float(slant_range[0])
    rsr_mhz = (SPEED_OF_LIGHT / (2.0 * dr)) / 1e6 if dr > 0 else 0.0

    # ----- frequency / bandwidths -----
    center_freq = fval('%s/acquiredCenterFrequency' % freq,
                       fval('%s/processedCenterFrequency' % freq, 0.0))
    if center_freq <= 0:
        sys.stderr.write('ERROR: no usable centre frequency.\n')
        sys.exit(1)
    wavelength   = SPEED_OF_LIGHT / center_freq
    range_bw_mhz = fval('%s/processedRangeBandwidth' % freq,
                        fval('%s/acquiredRangeBandwidth' % freq, 0.0)) / 1e6
    az_bw_hz     = fval('%s/processedAzimuthBandwidth' % freq, 0.0)

    # ----- Doppler centroid -> Doris range-time polynomial -----
    dc0 = dc1 = dc2 = 0.0
    dc_grid = val('%s/dopplerCentroid' % parms)
    dc_sr   = val('%s/slantRange' % parms)
    if dc_grid is not None and dc_sr is not None:
        dc_grid = np.asarray(dc_grid)
        dc_sr   = np.asarray(dc_sr, dtype=float)
        row = dc_grid[dc_grid.shape[0] // 2, :] if dc_grid.ndim == 2 else dc_grid
        tau = 2.0 * (dc_sr - r_first) / SPEED_OF_LIGHT   # two-way time from pixel 1
        if tau.size >= 3:
            c = np.polyfit(tau, np.asarray(row, dtype=float), 2)[::-1]
            dc0, dc1, dc2 = float(c[0]), float(c[1]), float(c[2])
            resid = np.abs(dc0 + dc1 * tau + dc2 * tau ** 2 - row).max()
            if resid > 1.0:
                sys.stderr.write('WARNING: Doppler centroid quadratic fit residual '
                                 '%.3f Hz (>1 Hz).\n' % resid)
    else:
        sys.stderr.write('WARNING: no dopplerCentroid annotation; using 0.\n')

    # ----- orbit (epoch from the units attribute; no referenceEpoch dataset) -----
    orb_t_ds = f['%s/time' % orbit]
    orb_epoch = parse_epoch(orb_t_ds.attrs.get('units',
                                               b'seconds since 2000-01-01T00:00:00'))
    orb_time = orb_t_ds[:]
    orb_pos  = f['%s/position' % orbit][:]
    orb_vel  = f['%s/velocity' % orbit][:]
    n_sv = len(orb_time)
    orb_sod = [secs_of_day(orb_epoch + datetime.timedelta(seconds=float(t)))
               for t in orb_time]

    # ----- identification -----
    mission    = sval('%s/missionId' % ident, 'NISAR')
    look_dir   = sval('%s/lookDirection' % ident, 'Right')
    pass_dir   = sval('%s/orbitPassDirection' % ident, 'UNKNOWN')
    track      = sval('%s/trackNumber' % ident, 'DUMMY')
    frame      = sval('%s/frameNumber' % ident, 'DUMMY')
    abs_orbit  = sval('%s/absoluteOrbitNumber' % ident, 'DUMMY')
    proc_date  = sval('%s/processingDateTime' % ident, 'DUMMY')
    prod_ver   = sval('%s/productVersion' % ident, 'UNKNOWN')
    soft_ver   = sval('%s/metadata/processingInformation/algorithms/softwareVersion'
                      % rslc, 'UNKNOWN')

    # ----- scene centre from the geolocation grid (epsg 4326 -> lon/lat) -----
    lat_c = lon_c = 0.0
    gg = '%s/metadata/geolocationGrid' % rslc
    try:
        epsg = int(f['%s/epsg' % gg][()])
        if epsg == 4326:
            cx = f['%s/coordinateX' % gg]
            cy = f['%s/coordinateY' % gg]
            i, j, k = cx.shape[0] // 2, cx.shape[1] // 2, cx.shape[2] // 2
            lon_c = float(cx[i, j, k])
            lat_c = float(cy[i, j, k])
    except (KeyError, IndexError, ValueError):
        pass

    f.close()

    # Product type specifier must let slcimage.cc tell the bands apart:
    # it matches "NISAR-S" before "NISAR" (strstr on the 3rd token).
    prod_tag = 'NISAR-S' if band == 'SSAR' else 'NISAR'

    # ===== Doris .res =========================================================
    # Token positions after each keyword are fixed -- see slcimage.cc.
    print()
    print('*******************************************************************')
    print('*_Start_readfiles:')
    print('*******************************************************************')
    print('Volume file:                                     %s' % args.input)
    print('Volume_ID:                                       %s_%s_RSLC' % (mission, band))
    print('Volume_identifier:                               NISAR L1 RSLC %s-band' % band[0])
    print('Volume_set_identifier:                           abs_orbit %s track %s frame %s'
          % (abs_orbit, track, frame))
    print('(Check)Number of records in ref. file:           %d' % n_lines)
    print('SAR_PROCESSOR:                                   ISCE3 %s' % soft_ver)
    print('SWATH:                                           frequency%s' % fsub)
    print('PASS:                                            %s' % pass_dir)
    print('IMAGING_MODE:                                    StripMap %s' % pol)
    print('RADAR_FREQUENCY (Hz):                            %.9e' % center_freq)
    print()
    print('Product type specifier:                          %s %s RSLC' % (prod_tag, band))
    print('Logical volume generating facility:              JPL')
    print('Logical volume creation date:                    %s' % proc_date)
    print('Location and date/time of product creation:      %s' % proc_date)
    print('Scene identification:                            orbit %s track %s frame %s'
          % (abs_orbit, track, frame))
    print('Scene location:                                  lat: %9.4f lon: %9.4f' % (lat_c, lon_c))
    print()
    print('Leader file:                                     %s' % args.input)
    print('Sensor platform mission identifer:               %s' % mission)
    print('Scene_centre_latitude:                           %.6f' % lat_c)
    print('Scene_centre_longitude:                          %.6f' % lon_c)
    print('Scene_centre_heading:                            0.0')
    print('Radar_wavelength (m):                            %.10f' % wavelength)
    print('First_pixel_azimuth_time (UTC):                  %s' % first_line_utc)
    print('Pulse_Repetition_Frequency (computed, Hz):       %.9f' % prf)
    print('Total_azimuth_band_width (Hz):                   %.6f' % az_bw_hz)
    print('Weighting_azimuth:                               UNKNOWN')
    print('Xtrack_f_DC_constant (Hz, early edge):           %.9f' % dc0)
    print('Xtrack_f_DC_linear (Hz/s, early edge):           %.9f' % dc1)
    print('Xtrack_f_DC_quadratic (Hz/s/s, early edge):      %.9f' % dc2)
    print('Range_time_to_first_pixel (2way) (ms):           %.15f' % range_time_ms)
    print('Range_sampling_rate (computed, MHz):             %.9f' % rsr_mhz)
    print('Total_range_band_width (MHz):                    %.6f' % range_bw_mhz)
    print('Weighting_range:                                 UNKNOWN')
    print()
    print('*******************************************************************')
    print('Datafile:                                        %s' % args.input)
    print('Dataformat:                                      HDF5_RSLC')
    print('Number_of_lines_original:                        %d' % n_lines)
    print('Number_of_pixels_original:                       %d' % n_pixels)
    print('*******************************************************************')
    print('* End_readfiles:_NORMAL')
    print('*******************************************************************')
    print()
    print()
    print('*******************************************************************')
    print('*_Start_leader_datapoints')
    print('*******************************************************************')
    print(' t(s)            X(m)            Y(m)            Z(m)')
    print('NUMBER_OF_DATAPOINTS:                            %d' % n_sv)
    print()
    for i in range(n_sv):
        # Exactly four columns, line MUST stay under 127 characters:
        # orbit::initialize does getline(dummyline, ONE27=127) then reads
        # t,x,y,z only.  A longer line leaves a tail in the stream that is
        # consumed as the next record's time and wrecks the orbit.
        print(' %.6f %.13f %.13f %.13f'
              % (orb_sod[i], orb_pos[i, 0], orb_pos[i, 1], orb_pos[i, 2]))
    print()
    print('*******************************************************************')
    print('* End_leader_datapoints:_NORMAL')
    print('*******************************************************************')


if __name__ == '__main__':
    main()
