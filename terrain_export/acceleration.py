"""Exact height comparisons on CPU or an available OpenCL graphics device."""
import sys
import time
import numpy as np


def cpu_threshold(values, options):
    selected = np.isfinite(values)
    if options.mode in ('between', 'above'):
        selected &= values >= options.minimum if options.mode == 'between' else values > options.minimum
    if options.mode in ('between', 'below'):
        selected &= values <= options.maximum if options.mode == 'between' else values < options.maximum
    return selected


def ordered_float(value):
    bits = np.float64(0. if value == 0 else value).view(np.uint64).item()
    return np.uint64(bits ^ (0xffffffffffffffff if bits >> 63 else 0x8000000000000000))


KERNEL = r'''
__kernel void select_height(__global const ulong *bits, __global uchar *selected,
                            const ulong low, const ulong high, const int mode, const int count) {
    int i = get_global_id(0);
    if (i >= count) return;
    ulong value = bits[i];
    if ((value & 0x7ff0000000000000UL) == 0x7ff0000000000000UL) {
        selected[i] = 0; return;
    }
    if ((value & 0x7fffffffffffffffUL) == 0) value = 0;
    ulong ordered = value ^ ((value >> 63) ? 0xffffffffffffffffUL : 0x8000000000000000UL);
    selected[i] = (mode == 0) ? (ordered >= low && ordered <= high) :
                  ((mode == 1) ? (ordered > low) : (ordered < high));
}
'''


class OpenCLThreshold:
    def __init__(self, device=None):
        import pyopencl as cl
        self.cl = cl
        if device is None:
            devices = []
            for platform in cl.get_platforms():
                try: devices.extend(platform.get_devices(device_type=cl.device_type.GPU))
                except cl.Error: continue
            if not devices: raise RuntimeError('No OpenCL GPU device is available')
            device = max(devices, key=lambda d: d.max_compute_units*d.max_clock_frequency)
        if bool(device.endian_little) != (sys.byteorder == 'little'):
            raise RuntimeError('OpenCL device byte order is unsupported')
        self.device = device
        self.context = cl.Context([device])
        self.queue = cl.CommandQueue(self.context)
        self.kernel = cl.Program(self.context, KERNEL).build(options=['-cl-std=CL1.2']).select_height
        self.capacity = 0
        self.source = self.target = None

    def apply(self, values, options):
        cl = self.cl
        values = np.ascontiguousarray(values, dtype='float64')
        count = values.size
        if not count: return np.zeros(values.shape, dtype=bool)
        if count > self.capacity:
            self.source = cl.Buffer(self.context, cl.mem_flags.READ_ONLY, count*8)
            self.target = cl.Buffer(self.context, cl.mem_flags.WRITE_ONLY, count)
            self.capacity = count
        cl.enqueue_copy(self.queue, self.source, values, is_blocking=False)
        self.kernel(self.queue, (count,), None, self.source, self.target,
                    ordered_float(options.minimum), ordered_float(options.maximum),
                    np.int32({'between':0, 'above':1, 'below':2}[options.mode]), np.int32(count))
        output = np.empty(values.shape, dtype='uint8')
        cl.enqueue_copy(self.queue, output, self.target, is_blocking=True)
        return output.astype(bool)


class ThresholdAccelerator:
    def __init__(self, options, progress):
        self.options, self.progress = options, progress
        self.enabled = options.gpu and options.acceleration != 'cpu'
        self.tried = False
        self.engine = None
        self.backend = 'CPU'

    def apply(self, values):
        if not self.enabled or (self.options.acceleration == 'auto' and values.size < 262144):
            return cpu_threshold(values, self.options)
        if not self.tried:
            self.tried = True
            try:
                self.engine = OpenCLThreshold()
                # Validate exact thresholds once on the actual driver. Integer IEEE
                # ordering retains float64 precision without requiring GPU FP64 support.
                first = self.engine.apply(values, self.options)
                reference = cpu_threshold(values, self.options)
                if not np.array_equal(first, reference):
                    raise RuntimeError('GPU comparison validation failed')
                if self.options.acceleration == 'auto':
                    start = time.perf_counter(); self.engine.apply(values, self.options)
                    gpu_time = time.perf_counter()-start
                    start = time.perf_counter(); cpu_threshold(values, self.options)
                    cpu_time = time.perf_counter()-start
                    if gpu_time >= cpu_time:
                        self.enabled = False; self.engine = None
                        self.progress('Acceleration: CPU (faster for this raster tile)')
                        return reference
                self.backend = 'OpenCL GPU: '+self.engine.device.name.strip()
                self.progress('Acceleration: '+self.backend)
                return first
            except Exception as exc:
                self.engine = None; self.enabled = False
                self.progress(f'GPU unavailable ({type(exc).__name__}); using CPU.')
        if self.engine is not None:
            try: return self.engine.apply(values, self.options)
            except Exception as exc:
                self.progress(f'GPU failed ({type(exc).__name__}); using CPU.')
                self.enabled = False; self.engine = None; self.backend = 'CPU'
        return cpu_threshold(values, self.options)


def binding_report():
    import pyopencl as cl
    report = dict(binding_loaded=True, version=cl.VERSION_TEXT, gpu_devices=[])
    try:
        for platform in cl.get_platforms():
            try:
                report['gpu_devices'].extend(d.name.strip() for d in platform.get_devices(device_type=cl.device_type.GPU))
            except cl.Error: continue
    except cl.Error as exc:
        report['driver_query'] = type(exc).__name__
    report['gpu_available'] = bool(report['gpu_devices'])
    return report
