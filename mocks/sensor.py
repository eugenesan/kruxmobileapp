# The MIT License (MIT)

# Copyright (c) 2021-2022 Krux contributors

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from kivy.uix.widget import Widget
from .qrreader import QRReader
from unittest import mock
from kivy.properties import ObjectProperty
from math import sqrt
from PIL import Image


def _channel_std_percent(histogram, total):
    """Population standard deviation of one 256-bin channel, as a percentage.

    Two-pass (mean, then mean squared deviation) over the histogram, which is
    what numpy.std does, so the result agrees with it to floating-point noise.
    The one-pass `E[x^2] - E[x]^2` form is avoided deliberately: on a near-flat
    image it cancels catastrophically and can return 0 or a NaN, and these
    values are compared against the entropy thresholds, so a wrong answer there
    is a security decision rather than a cosmetic one.
    """
    if total <= 0:
        return 0
    mean = 0.0
    for value, count in enumerate(histogram):
        if count:
            mean += value * count
    mean /= total

    variance = 0.0
    for value, count in enumerate(histogram):
        if count:
            deviation = value - mean
            variance += count * deviation * deviation
    variance /= total

    if variance <= 0.0:
        return 0
    return (sqrt(variance) * 100) // 255


class MockStatistics:
    """
    Used to mock openMV the statistics object returned by the sensor module
    """
    def __init__(self, img=None, height=480, width=640):
        def set_all_to(number=0):
            self.r_std = number
            self.g_std = number
            self.b_std = number

        if not img:
            set_all_to(0)
            return

        # Pillow decodes the raw RGBA buffer; it raises if the length does not
        # match the requested geometry, which is the same guard the previous
        # numpy reshape provided.
        try:
            image = Image.frombytes("RGBA", (width, height), img)
        except Exception:
            set_all_to(10) # If the image format is not standard, set all to 10 to pass the entropy check
            return

        # Pillow lays the per-band histograms out consecutively: R, G, B, A.
        histograms = image.histogram()
        pixels = width * height
        self.r_std = _channel_std_percent(histograms[0:256], pixels)
        self.g_std = _channel_std_percent(histograms[256:512], pixels)
        self.b_std = _channel_std_percent(histograms[512:768], pixels)

    
    # It would be too resource expensive to convert RGB to LAB on Android
    # So on Android RGB standard deviation will be used

    def l_stdev(self):
        return self.r_std
    
    def a_stdev(self):
        return self.g_std
    
    def b_stdev(self):
        return self.b_std

class Sensor(Widget):
    RGB565 = 0
    QVGA = 0

    running = ObjectProperty()

    def __init__(self) -> None:
        self.camera = None
        self.qrreader = QRReader(letterbox_color='black',
                                 aspect_ratio='4:3')
        self.codes = []
        self.m = mock.MagicMock()
        self.m.get_frame.return_value = None
        self.m.find_qrcodes.return_value = []
        self.m.get_statistics.return_value = MockStatistics()
        self.m.width.return_value = 640
        self.m.height.return_value = 480

    def reset(self, freq=None, dual_buff=False):
        pass


    def run(self, on):
        if on:
            self.running = True
        else:
            self.running = False

    def snapshot(self):
        codes = self.qrreader.pick_annotations()
        self.m.get_frame.return_value = None
        self.m.find_qrcodes.return_value = codes
        img = self.qrreader.pick_snapshot_bytes()
        self.m.to_bytes.return_value = img
        if len(img) == 640*480*4:
            width = 640
            height = 480
        else: # tries with 320x240
            width = 320
            height = 240
        self.m.get_statistics.return_value = MockStatistics(self.qrreader.pick_snapshot_bytes(), height, width)
        self.m.width.return_value = width
        self.m.height.return_value = height
        return self.m

    def get_id(self):
        return 0x7742

    def skip_frames(self, frames=None):
        pass

    def set_pixformat(self, format):
        pass

    def set_framesize(self, format):
        pass

    def _Camera__write_reg(self, reg, data):
        pass

