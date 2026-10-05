#!/usr/bin/env python3
"""
biomass_dump_header2doris.py
Extract ESA BIOMASS L1 SCS (SLC) metadata from the annotation XML and write Doris .res format.

Usage:
  biomass_dump_header2doris.py <annotation_xml> [--pol HH|HV|VH|VV] > scratchres_biomass

Verified against real BIOMASS L1 SCS data (BPS processor 4.4.3) and
BPS_L1_PFD_v1_6_1.pdf.

BIOMASS L1 product layout:
  <PRODUCT>.SAFE-like/
    annotation/{stem}_annot.xml            <- input to this script
    annotation/navigation/{stem}_orb.xml   <- orbit ADS, EO CFI SW (EOF) format
    annotation/navigation/{stem}_att.xml   <- attitude ADS
    measurement/{stem}_i_abs.tiff          <- SLC amplitude, 4-band COG
    measurement/{stem}_i_phase.tiff        <- SLC phase, 4-band COG
    measurement/{stem}_i.vrt               <- GDAL VRT (polar pixel func)

Doris .res parsing is POSITIONAL (see slcimage.cc): the number of tokens
between keyword and value is fixed.  Do not reformat the printed lines.

Units / conventions confirmed from the data:
  rangeTimeInterval is TWO-WAY  (c/2 * rti == rangePixelSpacing exactly)
    -> Range_sampling_rate (MHz) = 1e-6 / rangeTimeInterval
  PRF for Doris must be the OUTPUT GRID rate 1/azimuthTimeInterval,
    not the hardware prfList/prf/value (which includes cal/noise pulses).
    Cross-check: azimuthProcessingParameters/totalBandwidth == 1/azimuthTimeInterval.
  Doris f_DC(tau) = a0 + a1*tau + a2*tau^2 with tau = two-way slant range
    time offset from the FIRST pixel [s]  (slcimage.cc:1031).
    BIOMASS geometryDCPolynomial is centred on its own t0, so the polynomial
    is re-centred onto firstSampleSlantRangeTime here.

Author: RN 2026
"""

import sys
import os
import math
import datetime
import argparse

try:
    import xml.etree.ElementTree as ET
except ImportError:
    sys.stderr.write('ERROR: xml.etree.ElementTree not available.\n')
    sys.exit(1)

CODE_REVISION = '3.0'
SPEED_OF_LIGHT = 299792458.0  # m/s
BIOMASS_NOMINAL_FREQ_HZ = 435.0e6  # P-band
POL_BAND = {'HH': 1, 'HV': 2, 'VH': 3, 'VV': 4}


# ─── helpers ──────────────────────────────────────────────────────────────────

def strip_ns_tree(root):
    """Strip XML namespace prefixes from all tags in-place."""
    for el in root.iter():
        if '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    return root


def txt(root, xpath, default=None):
    el = root.find(xpath)
    if el is not None and el.text and el.text.strip():
        return el.text.strip()
    return default


def fnum(root, xpath, default=0.0):
    v = txt(root, xpath)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def parse_utc(s):
    """Parse UTC string; tolerates the EO CFI 'UTC=' prefix."""
    s = s.strip()
    for pfx in ('UTC=', 'TAI=', 'UT1='):
        if s.startswith(pfx):
            s = s[len(pfx):]
            break
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S.%fZ'):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError('Cannot parse UTC string: %r' % s)


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


def shift_polynomial(coeffs, delta, nout=3):
    """
    Re-centre a polynomial:  sum_k c_k (x - x0)^k  ->  sum_j b_j (x - x1)^j
    with delta = x1 - x0.  Binomial expansion of (u + delta)^k.
    """
    n = len(coeffs)
    out = [0.0] * nout
    for k in range(n):
        ck = coeffs[k]
        if ck == 0.0:
            continue
        for j in range(min(k, nout - 1) + 1):
            out[j] += ck * math.comb(k, j) * (delta ** (k - j))
    return out


# ─── product path derivation ──────────────────────────────────────────────────

def derive_paths(annot_path):
    """
    From <root>/annotation/{stem}_annot.xml derive the sibling ADS/MDS paths.
    Falls back to same-directory lookup if the layout differs.
    """
    annot_path = os.path.abspath(annot_path)
    annot_dir = os.path.dirname(annot_path)
    base = os.path.basename(annot_path)
    stem = base[:-len('_annot.xml')] if base.endswith('_annot.xml') \
        else os.path.splitext(base)[0]

    prod_root = os.path.dirname(annot_dir)  # .../annotation -> product root

    cands_orb = [
        os.path.join(annot_dir, 'navigation', stem + '_orb.xml'),
        os.path.join(prod_root, 'annotation', 'navigation', stem + '_orb.xml'),
        os.path.join(annot_dir, stem + '_orb.xml'),
    ]
    cands_abs = [
        os.path.join(prod_root, 'measurement', stem + '_i_abs.tiff'),
        os.path.join(annot_dir, stem + '_i_abs.tiff'),
    ]
    orb = next((p for p in cands_orb if os.path.isfile(p)), cands_orb[0])
    mds = next((p for p in cands_abs if os.path.isfile(p)), cands_abs[0])
    return orb, mds


# ─── orbit ADS (EO CFI SW / EOF format) ───────────────────────────────────────

def read_eocfi_orbit(orb_xml_path):
    """
    Parse EO Mission CFI SW orbit XML.  Root is Earth_Observation_File
    (older files: Earth_Explorer_File) -- iter('OSV') is root-agnostic.

      <Data_Block><List_of_OSVs count="N">
        <OSV><UTC>UTC=...</UTC>
             <X unit="m">..</X> <Y/> <Z/> <VX unit="m/s">..</VX> <VY/> <VZ/>
        </OSV>
      ...
    Reference frame is EARTH_FIXED (ECEF), time reference UTC.
    """
    try:
        tree = ET.parse(orb_xml_path)
    except Exception as e:
        sys.stderr.write('WARNING: Cannot parse orbit file %s: %s\n' % (orb_xml_path, e))
        return []
    root = strip_ns_tree(tree.getroot())

    ref_frame = txt(root, './/Ref_Frame', '(unknown)')
    if ref_frame not in ('EARTH_FIXED', '(unknown)'):
        sys.stderr.write('WARNING: orbit Ref_Frame is %r, expected EARTH_FIXED (ECEF).\n'
                         % ref_frame)

    svs = []
    for osv in root.iter('OSV'):
        try:
            dt = parse_utc(osv.find('UTC').text)
            svs.append((secs_of_day(dt),
                        float(osv.find('X').text),
                        float(osv.find('Y').text),
                        float(osv.find('Z').text),
                        float(osv.find('VX').text),
                        float(osv.find('VY').text),
                        float(osv.find('VZ').text)))
        except (AttributeError, TypeError, ValueError) as e:
            sys.stderr.write('WARNING: skipping malformed OSV: %s\n' % e)
    return svs


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='Dump BIOMASS L1 SCS annotation XML to Doris .res format')
    ap.add_argument('input', help='BIOMASS annotation XML (*_annot.xml)')
    ap.add_argument('--pol', default='HH', choices=['HH', 'HV', 'VH', 'VV'],
                    help='Polarisation channel (default: HH)')
    args = ap.parse_args()

    try:
        root = strip_ns_tree(ET.parse(args.input).getroot())
    except Exception as e:
        sys.stderr.write('ERROR: Cannot parse %s: %s\n' % (args.input, e))
        sys.exit(1)

    orb_xml, mds_path = derive_paths(args.input)

    # ── acquisitionInformation ──
    mission     = txt(root, 'acquisitionInformation/mission', 'BIOMASS')
    swath       = txt(root, 'acquisitionInformation/swath', 'UNKNOWN')
    prod_type   = txt(root, 'acquisitionInformation/productType', 'SCS')
    pass_dir    = txt(root, 'acquisitionInformation/orbitPass', 'UNKNOWN')
    abs_orbit   = txt(root, 'acquisitionInformation/absoluteOrbitNumber', 'DUMMY')
    rel_orbit   = txt(root, 'acquisitionInformation/relativeOrbitNumber', 'DUMMY')
    frame       = txt(root, 'acquisitionInformation/frame', 'DUMMY')
    heading     = fnum(root, 'acquisitionInformation/platformHeading', 0.0)
    start_time  = txt(root, 'acquisitionInformation/startTime', '')

    # ── instrumentParameters ──
    radar_freq = fnum(root, 'instrumentParameters/radarCarrierFrequency',
                      BIOMASS_NOMINAL_FREQ_HZ)
    if radar_freq <= 0.0:
        radar_freq = BIOMASS_NOMINAL_FREQ_HZ
        sys.stderr.write('WARNING: bad radarCarrierFrequency, using nominal %.0f Hz\n'
                         % radar_freq)
    wavelength = SPEED_OF_LIGHT / radar_freq

    # ── sarImage geometry / timing ──
    r_first = fnum(root, 'sarImage/firstSampleSlantRangeTime', 0.0)  # two-way [s]
    rti     = fnum(root, 'sarImage/rangeTimeInterval', 0.0)          # two-way [s]
    ati     = fnum(root, 'sarImage/azimuthTimeInterval', 0.0)        # [s]
    n_lines  = int(fnum(root, 'sarImage/numberOfLines', 0))
    n_pixels = int(fnum(root, 'sarImage/numberOfSamples', 0))

    slant_range_time_ms = r_first * 1000.0
    rsr_mhz = (1.0 / rti) / 1e6 if rti > 0 else 0.0

    # PRF: output grid rate.  Hardware prfList/prf/value is NOT the grid rate.
    prf = 1.0 / ati if ati > 0 else 0.0
    if prf == 0.0:
        prf = fnum(root, 'instrumentParameters/prfList/prf/value', 0.0)
        if prf > 0:
            sys.stderr.write('WARNING: azimuthTimeInterval missing; using hardware '
                             'PRF %.6f Hz (may not match image grid).\n' % prf)

    # cross-check against processing bandwidth annotation
    az_total_bw = fnum(root, 'processingParameters/azimuthProcessingParameters/totalBandwidth', 0.0)
    if az_total_bw > 0 and prf > 0 and abs(az_total_bw - prf) / prf > 1e-4:
        sys.stderr.write('WARNING: 1/azimuthTimeInterval (%.6f Hz) disagrees with '
                         'azimuthProcessingParameters/totalBandwidth (%.6f Hz).\n'
                         % (prf, az_total_bw))

    first_line_str = txt(root, 'sarImage/firstLineAzimuthTime') or start_time
    if first_line_str:
        try:
            first_line_utc = to_doris_time(parse_utc(first_line_str))
        except ValueError:
            first_line_utc = 'UNKNOWN'
            sys.stderr.write('WARNING: cannot parse firstLineAzimuthTime %r\n' % first_line_str)
    else:
        first_line_utc = 'UNKNOWN'
        sys.stderr.write('WARNING: no firstLineAzimuthTime in annotation.\n')

    # ── processingParameters: bandwidths and weighting ──
    rbw_mhz = fnum(root, 'processingParameters/rangeProcessingParameters/processingBandwidth', 0.0) / 1e6
    abw_hz  = fnum(root, 'processingParameters/azimuthProcessingParameters/processingBandwidth', 0.0)
    w_range = txt(root, 'processingParameters/rangeProcessingParameters/windowType', 'UNKNOWN')
    w_azi   = txt(root, 'processingParameters/azimuthProcessingParameters/windowType', 'UNKNOWN')

    # ── scene centre from footprint (4 corner lat/lon pairs) ──
    lat_c, lon_c = 0.0, 0.0
    fp = txt(root, 'sarImage/footprint')
    if fp:
        v = [float(x) for x in fp.split()]
        if len(v) >= 8:
            lat_c = sum(v[0::2][:4]) / 4.0
            lon_c = sum(v[1::2][:4]) / 4.0

    # ── Doppler centroid: re-centre onto first pixel range time ──
    # Pick the dcEstimate closest to mid-azimuth of the image.
    dc_a = [0.0, 0.0, 0.0]
    estimates = root.findall('dopplerParameters/dcEstimateList/dcEstimate')
    if estimates:
        chosen, best = None, None
        if first_line_str and ati > 0 and n_lines > 0:
            try:
                mid = parse_utc(first_line_str) + \
                      datetime.timedelta(seconds=0.5 * ati * (n_lines - 1))
                for e in estimates:
                    at = txt(e, 'azimuthTime')
                    if not at:
                        continue
                    d = abs((parse_utc(at) - mid).total_seconds())
                    if best is None or d < best:
                        best, chosen = d, e
            except ValueError:
                pass
        if chosen is None:
            chosen = estimates[0]

        poly = None
        for tag in ('combinedDCPolynomial', 'geometryDCPolynomial'):
            s = txt(chosen, tag)
            if s:
                try:
                    poly = [float(x) for x in s.split()]
                    break
                except ValueError:
                    pass
        if poly:
            t0 = fnum(chosen, 't0', r_first)
            dc_a = shift_polynomial(poly, r_first - t0, nout=3)
        else:
            sys.stderr.write('WARNING: no usable DC polynomial; using 0.\n')

    # ── orbit ──
    svs = read_eocfi_orbit(orb_xml) if os.path.isfile(orb_xml) else []
    if not svs:
        sys.stderr.write('WARNING: no orbit state vectors (looked for %s).\n' % orb_xml)

    # ═══ Doris .res output ═════════════════════════════════════════════════════
    # NOTE: token positions after each keyword are fixed -- see slcimage.cc.
    print()
    print('*******************************************************************')
    print('*_Start_readfiles:')
    print('*******************************************************************')
    print('Volume file:                                     %s' % args.input)
    print('Volume_ID:                                       %s_%s_%s' % (mission, swath, prod_type))
    print('Volume_identifier:                               ESA BIOMASS L1 %s P-band' % prod_type)
    print('Volume_set_identifier:                           abs_orbit %s rel_orbit %s frame %s'
          % (abs_orbit, rel_orbit, frame))
    print('(Check)Number of records in ref. file:           %d' % n_lines)
    print('SAR_PROCESSOR:                                   BPS %s'
          % txt(root, 'processingParameters/processorVersion', 'UNKNOWN'))
    print('SWATH:                                           %s' % swath)
    print('PASS:                                            %s' % pass_dir)
    print('IMAGING_MODE:                                    StripMap %s' % args.pol)
    print('RADAR_FREQUENCY (Hz):                            %.9e' % radar_freq)
    print()
    print('Product type specifier:                          BIOMASS %s' % prod_type)
    print('Logical volume generating facility:              ESA')
    print('Logical volume creation date:                    %s'
          % txt(root, 'processingParameters/productGenerationTime', 'DUMMY'))
    print('Location and date/time of product creation:      DUMMY')
    print('Scene identification:                            orbit %s frame %s' % (abs_orbit, frame))
    print('Scene location:                                  lat: %9.4f lon: %9.4f' % (lat_c, lon_c))
    print()
    print('Leader file:                                     %s' % args.input)
    print('Sensor platform mission identifer:               %s' % mission)
    print('Scene_centre_latitude:                           %.6f' % lat_c)
    print('Scene_centre_longitude:                          %.6f' % lon_c)
    print('Scene_centre_heading:                            %.6f' % heading)
    print('Radar_wavelength (m):                            %.10f' % wavelength)
    print('First_pixel_azimuth_time (UTC):                  %s' % first_line_utc)
    print('Pulse_Repetition_Frequency (computed, Hz):       %.9f' % prf)
    print('Total_azimuth_band_width (Hz):                   %.6f' % abw_hz)
    print('Weighting_azimuth:                               %s' % w_azi.upper())
    print('Xtrack_f_DC_constant (Hz, early edge):           %.9f' % dc_a[0])
    print('Xtrack_f_DC_linear (Hz/s, early edge):           %.9f' % dc_a[1])
    print('Xtrack_f_DC_quadratic (Hz/s/s, early edge):      %.9f' % dc_a[2])
    print('Range_time_to_first_pixel (2way) (ms):           %.15f' % slant_range_time_ms)
    print('Range_sampling_rate (computed, MHz):             %.9f' % rsr_mhz)
    print('Total_range_band_width (MHz):                    %.6f' % rbw_mhz)
    print('Weighting_range:                                 %s' % w_range.upper())
    print()
    print('*******************************************************************')
    print('Datafile:                                        %s' % mds_path)
    print('Dataformat:                                      BIOMASS_SCS')
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
    print('NUMBER_OF_DATAPOINTS:                            %d' % len(svs))
    print()
    for t, x, y, z, vx, vy, vz in svs:
        # Exactly four columns, and the line MUST stay under 127 characters.
        # orbit::initialize (orbitbk.cc) does
        #     infile.getline(dummyline, ONE27, '\n');
        #     infile >> time >> data_x >> data_y >> data_z;
        # with ONE27 == 127 and reads only t,x,y,z (velocities are derived by
        # differentiating the fitted polynomial, never read from file).
        # Appending vx,vy,vz pushed the line to 138 chars, so getline left the
        # tail in the stream and it was consumed as the next record's t --
        # producing a garbage orbit, diverging lp2xyz and a singular polyfit
        # normal matrix ("choles: A not pos. def.").
        # t needs sub-second precision: OSVs sit on .xxx offsets and t is real8.
        print(' %.6f %.13f %.13f %.13f' % (t, x, y, z))
    print()
    print('*******************************************************************')
    print('* End_leader_datapoints:_NORMAL')
    print('*******************************************************************')


if __name__ == '__main__':
    main()
