from setuptools import setup

setup(
    name='doris-ut',
    version='6.0.0',
    packages=['install', 'doris_stack', 'doris_stack.functions', 'doris_stack.main_code', 'prepare_stack'],
    url='https://github.com/TUDelftGeodesy/Doris',
    license='LICENSE.txt',
    author='Gert Mulder',
    author_email='g.mulder-@tudelft.nl',
    description='doris InSAR processing software',
    python_requires='>=3.6',
    install_requires=['numpy', 'shapely', 'requests', 'fiona', 'gdal', 'osr', 'scipy', 'fastkml']
)
