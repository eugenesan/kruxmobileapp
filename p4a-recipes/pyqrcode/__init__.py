"""Build pyqrcode.

python-for-android bundles no recipe for pyqrcode, and pyqrcode publishes no
Android wheels for any Python version, so p4a can satisfy the `pyqrcode`
entry in buildozer.spec by neither route. The result is p4a's fatal

    pip3 install pyqrcode --only-binary=:all: --platform=android_24_aarch64 ...
    ERROR: Could not find a version that satisfies the requirement pyqrcode

pyqrcode is pure Python, so PythonRecipe -- unpack, then install with the host
interpreter's pip into the target site-packages -- is all that is needed. No
C extensions, no hostpython prerequisites beyond setuptools.

The folder name must stay `pyqrcode`: p4a resolves a recipe by matching the
requirement name against the recipe directory name.
"""
from pythonforandroid.recipe import PythonRecipe


class PyQRCodeRecipe(PythonRecipe):
    version = "1.2.1"
    url = (
        "https://files.pythonhosted.org/packages/37/61/"
        "f07226075c347897937d4086ef8e55f0a62ae535e28069884ac68d979316/"
        "PyQRCode-{version}.tar.gz"
    )
    name = "pyqrcode"
    site_packages_name = "pyqrcode"
    depends = ["setuptools"]


recipe = PyQRCodeRecipe()
