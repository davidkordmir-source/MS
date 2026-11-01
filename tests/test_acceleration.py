import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from terrain_export.acceleration import ThresholdAccelerator, ordered_float, cpu_threshold
from terrain_export.engine import Options


class AcceleratorTests(unittest.TestCase):
    def test_ordered_values_keep_float64_boundaries_and_signed_zero(self):
        values = [-1e300, -30., np.nextafter(0., -1.), -0., 0., np.nextafter(0., 1.), 30., 1e300]
        ordered = [int(ordered_float(value)) for value in values]
        self.assertEqual(ordered, sorted(ordered))
        self.assertEqual(ordered[3], ordered[4])
        self.assertLess(ordered_float(30.), ordered_float(np.nextafter(30., np.inf)))

    def test_missing_gpu_and_mid_processing_failure_fall_back_to_cpu(self):
        values = np.array([[np.nan, -1, 30, np.nextafter(30., np.inf), 100.]])
        options = Options('', '', '', mode='above', minimum=30, gpu=True, acceleration='gpu')
        messages = []
        with patch('terrain_export.acceleration.OpenCLThreshold', side_effect=RuntimeError('No driver')):
            accelerator = ThresholdAccelerator(options, messages.append)
            np.testing.assert_array_equal(accelerator.apply(values), [[False, False, False, True, True]])
        self.assertEqual(accelerator.backend, 'CPU')
        self.assertTrue(any('using CPU' in m for m in messages))
        fake = SimpleNamespace(device=SimpleNamespace(name='Test GPU'),
                               apply=lambda data, settings: cpu_threshold(data, settings))
        with patch('terrain_export.acceleration.OpenCLThreshold', return_value=fake):
            accelerator = ThresholdAccelerator(options, messages.append)
            accelerator.apply(values)
            self.assertIn('GPU', accelerator.backend)
            fake.apply = lambda *args: (_ for _ in ()).throw(RuntimeError('Device lost'))
            np.testing.assert_array_equal(accelerator.apply(values), [[False, False, False, True, True]])
            self.assertEqual(accelerator.backend, 'CPU')

    def test_auto_keeps_cpu_when_transfer_is_slower(self):
        options = Options('', '', '', mode='above', minimum=30, gpu=True, acceleration='auto')
        values = np.full((512, 512), 40.)
        fake = SimpleNamespace(device=SimpleNamespace(name='Test GPU'),
                               apply=lambda data, settings: cpu_threshold(data, settings))
        with patch('terrain_export.acceleration.OpenCLThreshold', return_value=fake), \
             patch('terrain_export.acceleration.time.perf_counter', side_effect=[0., .01, 1., 1.001]):
            accelerator = ThresholdAccelerator(options, lambda message: None)
            self.assertTrue(accelerator.apply(values).all())
            self.assertEqual(accelerator.backend, 'CPU')
            self.assertFalse(accelerator.enabled)
