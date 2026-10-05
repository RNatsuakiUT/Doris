===========
Doris v5 Beta
===========

The new Doris version, Doris 5, is developed to process a stack of Sentinel-1 images, as well as all the already familiar
functionality of Doris 4.

This is a beta version. Therefore, you still may experience some problems. Please report them to us. But even better,
try to fix them! We are very happy to discuss with you how you can contribute to this project!

The new Doris version consists of 2 parts:
-       The doris_core directory cantaining the Doris core code, which is similar to the original Doris code and is
        written in C. This code is mainly used to create individual interferograms based on different steps.
-       The doris_stack directory containing scripts written in Python. These scripts automate the processing of a
        single master stack of Sentinel-1 images. The scripts manage the processing of the bursts of a sentinel 1 image,
        contain algorithms specific to processing sentinel 1 images and support parallelisation of the processing of the
        bursts. The functionality of these scripts can be further extended to support more sensors and modes.

Note that the python code is developed in python 2.7, so be sure you are not using python 3.

In addition, you will find a stack preparation script, to automatically download the burst you need for your Area of
Interest which you defined by a shape file, automatically download the SRTM DEM associated with this area, and setup
your processing structure.


Installation
===========

See the INSTALL file in the install directory. This file descripes the installation of the C libraries, python libraries
and some utility software.


Creating Sentinel-1 datastacks
=============================


Create a folder structure
-----------------------------

After installing the software you can create your first doris datastack. To do so you have to prepare the following:
- Create folders to download radar data and orbit files. In a further stage these files can be downloaded automatically,
    but it is also possible to do it yourself manually.
- Create a folder where you can store intermediate DEM results. Data will be downloaded automatically, so you only have
    to create the folder itself. Note that these automatic downloads are based on SRTM data and are therefore limited to
    60 degrees south and north of the equator.
- Create a .shp file with your area of interest. You can use different software packages, but ArcGIS and QGIS (free) are
    the most convenient for this purpose. Or you could download from one of the websites that offer free shapefiles for
    administrative boundaries (for example: http://www.diva-gis.org/Data)
- Finally, create the folder where you want to process your datastack. Be aware that to process your data you will need
    at least 100 GB of free space on your disc.


Register for Sentinel and SRTM downloads
----------------------------------------

Additionally, you will need an account for downloading Sentinel-1 and SRTM data. You can use the following links to
create an account. (How to link them to your code is described in the INSTALL file)
- To register for Sentinel-1 data download use: https://scihub.copernicus.eu/dhus/#/self-registration
- To register for SRTM download use: https://urs.earthdata.nasa.gov/users/new/


Run the stack preparation script
----------------------------------------

Move to the prepare_stack directory:
cd prepare_stack
Run the python script:
python prepare_datastack_main.py

This code will ask you to define the different folders you created before. The script will ask you whether you want
to run your code in parallel. Generally, this is recommended as it speeds up your processing speed. Note that either the
number of cores and your RAM can be limiting (one process will use about 4GB of RAM). Because it is not possible to mix
different orbits in one datastack it will also ask you which orbit you want to use and whether it is ascending or
descending. Please check this beforehand on the ESA website (https://scihub.copernicus.eu)
Finally, the code will ask you the start date, end date and master date:
- start date    > What is the first image (in time) you want to process?
- end date      > What is the last image (in time) you want to process? (Tip: This date can be in the far future if you
                    just want to download all images till now)
- master data   > This image will be used as the master of your stack. Other images will be resampled
                    to the geometry of this master image.
After finishing this script, the new datastack is automatically created together with a DEM of the area. This can take
a while in case the download speeds are low or your area is large.


Editing the data stack settings (generally not needed)
----------------------------------------------------

You can enter the folder to find, the newly created DEM, your .shp file, configuration files (inputfiles) and the stack.
Further there is the doris_input.xml file where all configuration settings for your datastack are stored.
This file is created in the folder where you will process your datastack. So, if you want to change this configuration 
afterwards, you can make some changes there.


Processing
=========================================

In the main folder of your datastack you will have three bash files:
create_dem.sh           > To create a DEM for your area. This is already done if you used the automatic DEM generation
download_sentinel.sh    > This will run the a download of sentinel images for the specified track over your area of
                            interest. Only dates between your start and end date are considered. This script will also
                            download the needed precise or restituted orbit files.
You can call this scripts using bash <script_name>

After downloading your DEM, radar data and orbit files you can start your processing by the following command:
bash doris_stack.sh

or, if your server uses qsub (for parallel processing)

qsub doris_stack.sh

If you want to extend your datastack later on, you can run the scripts again for the same datastack. It will check which
files are new and only process them. This software is therefore perfectly fit for continues monitoring.
Be sure that you do not change your master image in between, as this will break your code.



Enjoy,

TUDELFT RADAR GROUP 2017
doris_users@tudelft.nl


===========================================================================
Fork additions - Doris-UT 6.0.0 (University of Tokyo)
===========================================================================

This fork is jointly made by Ryo Natsuaki and Claude Sonnet 4.1 for modernization of source codes in Doris
as well as adding recent SAR platforms. ALOS-2, -4 and StriX were applied manually. NISAR and BIOMASS are AI-driven.

This fork adds single-pair support for three new sensors to doris_core, on
top of the modernisation work in doris_core/modernized.  Everything below
concerns the C++ core (individual interferograms); the Sentinel-1 stack
scripts in doris_stack are unchanged.


New sensors
-----------------------------------------------------------

    M_IN_METHOD     sensor                     product format
    ------------    -----------------------    ---------------------------
    ALOS2           ALOS-2 L-band              CEOS
    ALOS4           ALOS-4 L-band              CEOS
    STRIX           STRIX X-band               CEOS
    NISAR           NISAR L-band RSLC          HDF5 (ISCE3)
    NISAR-L         same as NISAR
    NISAR-S         NISAR S-band RSLC          HDF5 (ISCE3)
    BIOMASS         ESA BIOMASS L1 SCS         COG GeoTIFF + XML annotation

Metadata and SLC extraction are done by python helpers in bin/, which Doris
calls through system():

    nisar_dump_header2doris.py     nisar_dump_data.py
    biomass_dump_header2doris.py   biomass_dump_data.py
    nisar_common.py                (shared --freq resolution, not called
                                    directly)

For NISAR the readfiles step uses M_IN_DAT (the RSLC .h5); there is no
separate leader file.  For BIOMASS, M_IN_LEA is the annotation XML and the
orbit file (annotation/navigation/*_orb.xml) and the amplitude/phase COG
pair are located relative to it automatically.


New input cards
-----------------------------------------------------------

M_IN_POL / S_IN_POL     polarisation channel
M_IN_FREQ / S_IN_FREQ   NISAR frequency sub-band

Both are optional.  One card per image feeds both the readfiles and the
crop step, so the channel cannot disagree between the two.  Omitting a card
reproduces the previous behaviour exactly, so existing input files are
unaffected.

M_IN_POL accepts whatever the product actually contains:

    BIOMASS         HH HV VH VV     (COG bands 1 2 3 4)
    NISAR L-band    HH HV           (dual pol, product dependent)
    NISAR S-band    RH RV           (compact pol - there is no HH)

M_IN_FREQ selects the sub-band of a dual-band NISAR product.  It takes
either a literal name or a relative selector:

    A, B                    the sub-band as named in the product
    LOW, L, HIGH, H         resolved from the centre frequency actually
                            stored for each sub-band

The relative form exists because the lettering does not follow frequency:
in NISAR L-band, frequencyB (1293.5 MHz) sits ABOVE frequencyA (1239.0 MHz).
The resolution is echoed to the log so you can see what was picked:

    nisar: --freq HIGH -> frequencyB (1293.500 MHz)  [A=1239.000 MHz, B=1293.500 MHz]

If the card is omitted, the first sub-band is used (normally A, the full
bandwidth swath).  Note that frequencyB may list its polarisations in a
different order from frequencyA, so when using frequencyB it is safer to
name the polarisation explicitly as well.


Prerequisites
-----------------------------------------------------------

1. Build in doris_core/modernized.  That is the only tree carrying the new
   sensor support; building doris_core gives you a binary that rejects
   M_IN_METHOD NISAR / BIOMASS.

       cd doris_core/modernized
       make CFLAGS="-O2 -std=c++17 -D__USE_FFTW_LIBRARY__ \
                    -D__X86PROCESSOR__ -I/usr/local/include"

   To check a binary actually has the support:

       strings doris | grep -c BIOMASS      # must be non-zero

2. Put bin/ on PATH.  processor.cc invokes the helpers by bare name:

       export PATH=/path/to/Doris-5.0.3Beta/bin:$PATH

3. Python dependencies:

       NISAR     h5py, numpy            pip install --user h5py
       BIOMASS   rasterio, numpy        pip install --user rasterio

   For BIOMASS, rasterio is preferred over a system GDAL: the measurement
   COGs use lerc_zstd compression, which GDAL older than 3.4 cannot open
   ("missing codec") even though the python module imports fine.


Example 1 - BIOMASS pair, readfiles and crop
-----------------------------------------------------------

    c
    SCREEN     INFO
    MEMORY     2000
    OVERWRITE  ON
    BATCH      ON
    PREVIEW    OFF
    c
    PROCESS    m_readfiles
    PROCESS    m_crop
    PROCESS    s_readfiles
    PROCESS    s_crop
    c
    LOGFILE    biomass_log.out
    M_RESFILE  biomass_master.res
    S_RESFILE  biomass_slave.res
    I_RESFILE  biomass_ifg.res
    c
    M_IN_METHOD  BIOMASS
    M_IN_POL     HH
    M_IN_LEA     <master>/annotation/<stem>_annot.xml
    M_IN_DAT     <master>/measurement/<stem>_i_abs.tiff
    c
    S_IN_METHOD  BIOMASS
    S_IN_POL     HH
    S_IN_LEA     <slave>/annotation/<stem>_annot.xml
    S_IN_DAT     <slave>/measurement/<stem>_i_abs.tiff
    c
    M_CROP_IN    <master>/measurement/<stem>_i_abs.tiff
    M_CROP_OUT   biomass_master.raw
    M_DBOW       8001 12000 1 1552
    c
    S_CROP_IN    <slave>/measurement/<stem>_i_abs.tiff
    S_CROP_OUT   biomass_slave.raw
    S_DBOW       7501 12500 1 1552
    c
    STOP

M_CROP_IN must be the amplitude COG (*_i_abs.tiff).  The reader derives the
matching phase COG (*_i_phase.tiff) and rebuilds the complex SLC as
amplitude * exp(i * phase), which is what the product's own VRT does.


Example 2 - NISAR S-band, compact pol
-----------------------------------------------------------

    M_IN_METHOD  NISAR-S
    M_IN_POL     RH
    M_IN_DAT     <master>.h5
    c
    S_IN_METHOD  NISAR-S
    S_IN_POL     RH
    S_IN_DAT     <slave>.h5
    c
    M_CROP_IN    <master>.h5
    M_CROP_OUT   nisar_master.raw
    M_DBOW       26001 30000 20001 24000
    c
    S_CROP_IN    <slave>.h5
    S_CROP_OUT   nisar_slave.raw
    S_DBOW       25501 30500 19501 24500
    c
    ORB_INTERP   SPLINE
    c
    STOP

The --band argument is added automatically from M_IN_METHOD, so NISAR-S
reads /science/SSAR and NISAR reads /science/LSAR.


Example 3 - NISAR L-band, narrow sub-band selected by frequency
-----------------------------------------------------------

    M_IN_METHOD  NISAR
    M_IN_POL     HH
    M_IN_FREQ    HIGH
    M_IN_DAT     <master>.h5
    c
    S_IN_METHOD  NISAR
    S_IN_POL     HH
    S_IN_FREQ    HIGH
    S_IN_DAT     <slave>.h5
    c
    ORB_INTERP   POLYFIT 5
    c
    STOP

M_IN_FREQ HIGH and M_IN_FREQ B give byte-identical results on this product;
HIGH is preferred because it keeps meaning "the upper band" on a sensor that
letters its sub-bands the other way round.


Choosing ORB_INTERP
-----------------------------------------------------------

Doris either fits one global polynomial over all state vectors (degree 5 by
default) or runs a natural cubic spline.  Which one is more accurate depends
on how long an orbit arc the product carries, so it is worth measuring per
product rather than assuming.  Measured against a Hermite reference over the
image time span:

    product     orbit arc           polyfit deg 5   natural spline
    ---------   -----------------   -------------   --------------
    NISAR S     64 pts / 630 s          0.393 m        0.011 m
    NISAR L     18 pts / 170 s          0.011 m        0.055 m
    BIOMASS    100 pts /  99 s          0.002 m         -

Long arcs break the polynomial; short arcs are hurt by the spline's natural
end condition.  polyfit() warns when its residual at the datapoints exceeds
0.02 m, which is a useful tripwire.


Choosing the crop windows
-----------------------------------------------------------

The slave crop must still cover the master footprint after the azimuth and
range offset between the two acquisitions, which is not always small: for a
NISAR L-band pair whose frames differed in length (39520 vs 41040 lines) the
offset was 1267 lines.  A plain symmetric margin left the top 20 percent of
the master without slave data and its coherence collapsed from 0.50 to 0.00.

Run coarseorb first, read the reported offset, then shift S_DBOW by it and
add a margin:

    grep Coarse_orbits_translation <ifg>.res


Checking that a new sensor is read correctly
-----------------------------------------------------------

The most informative single check is to compare the offset predicted from
the orbits with the offset measured from the data:

    grep -E "Coarse_orbits_translation|Coarse_correlation_translation" <ifg>.res

If PRF, range sampling rate, range time to the first pixel, wavelength,
azimuth time or the orbit were wrong, these two diverge badly.  Agreement
within a few lines means the metadata is self-consistent.  Observed: 3 lines
for BIOMASS, 1 line for NISAR L-band.

Note also that coherence alone does not tell you whether processing is
correct - read it together with the baseline.  A BIOMASS tomographic pair
with a 1792 m perpendicular baseline (height ambiguity -70 m) gives 0.27 and
is perfectly healthy, while a NISAR L-band pair at 35 m (-2209 m) gives 0.53.


Adding further sensors
-----------------------------------------------------------

doris_core/ADDING_SENSORS.md documents the procedure, the places
in the code that need editing, the failure modes that build cleanly but
corrupt numbers, and the tests that catch them.


Ryo Natsuaki, The University of Tokyo, 2026
natsuaki@eis.t.u-tokyo.ac.jp
