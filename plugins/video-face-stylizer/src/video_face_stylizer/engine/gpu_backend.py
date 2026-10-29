"""Select a complete GPU backend from the actual OpenGL renderer."""
from __future__ import annotations


def renderer_backend(vendor, renderer):
    identity = (vendor + ' ' + renderer).lower()
    if any(word in identity for word in ('llvmpipe', 'softpipe', 'software', 'gdi generic', 'swrast')):
        raise RuntimeError('A hardware Intel or NVIDIA OpenGL renderer is required; software rendering is forbidden.')
    if 'nvidia' in vendor.lower():
        return 'nvidia'
    if 'intel' in vendor.lower():
        return 'intel'
    raise RuntimeError(f'Unsupported GPU renderer: {vendor} / {renderer}. Intel and NVIDIA are supported.')


def match_adapter(renderer, adapters):
    """Never silently use the iGPU/default adapter for NVIDIA inference."""
    matches = [item for item in adapters if item['vendor_id'] == 0x10DE
               and item['name'].lower() in renderer.lower() and not item.get('software', False)]
    if len(matches) != 1:
        raise RuntimeError('Cannot uniquely match the OpenGL NVIDIA GPU to a hardware inference/codec adapter.')
    return matches[0]


def nvidia_adapter(renderer):
    import ctypes as c
    import uuid
    from ctypes import wintypes as w

    class LUID(c.Structure):
        _fields_ = [('low', w.DWORD), ('high', w.LONG)]

    class Description(c.Structure):
        _fields_ = [('name', w.WCHAR * 128), ('vendor', w.UINT), ('device', w.UINT),
                    ('subsystem', w.UINT), ('revision', w.UINT), ('video_memory', c.c_size_t),
                    ('system_memory', c.c_size_t), ('shared_memory', c.c_size_t),
                    ('luid', LUID), ('flags', w.UINT)]

    def method(obj, slot, result, *args):
        table = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(result, c.c_void_p, *args)(table[slot])

    factory = c.c_void_p()
    iid = (c.c_ubyte * 16).from_buffer_copy(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    dxgi = c.WinDLL('dxgi.dll')
    create = dxgi.CreateDXGIFactory1
    create.argtypes, create.restype = [c.c_void_p, c.POINTER(c.c_void_p)], w.LONG
    if create(c.byref(iid), c.byref(factory)) < 0:
        raise RuntimeError('Cannot enumerate DirectX GPU adapters.')
    adapters = []
    try:
        for index in range(32):
            adapter = c.c_void_p()
            hr = method(factory, 12, w.LONG, w.UINT, c.POINTER(c.c_void_p))(factory, index, c.byref(adapter))
            if hr & 0xFFFFFFFF == 0x887A0002:
                break
            if hr < 0:
                raise RuntimeError('DirectX GPU adapter enumeration failed.')
            try:
                desc = Description()
                if method(adapter, 10, w.LONG, c.POINTER(Description))(adapter, c.byref(desc)) < 0:
                    raise RuntimeError('Cannot read DirectX GPU adapter identity.')
                adapters.append({'id': index, 'name': desc.name, 'vendor_id': desc.vendor,
                                 'software': bool(desc.flags & 2)})
            finally:
                method(adapter, 2, w.ULONG)(adapter)
    finally:
        method(factory, 2, w.ULONG)(factory)
    selected = match_adapter(renderer, adapters)
    cuda = c.WinDLL('nvcuda.dll')
    if cuda.cuInit(0) != 0:
        raise RuntimeError('NVIDIA driver could not initialize hardware video devices.')
    count = c.c_int()
    if cuda.cuDeviceGetCount(c.byref(count)) != 0:
        raise RuntimeError('Cannot enumerate NVIDIA video devices.')
    codecs = []
    for index in range(count.value):
        name = c.create_string_buffer(256)
        if cuda.cuDeviceGetName(name, len(name), index) != 0:
            raise RuntimeError('Cannot read NVIDIA video device identity.')
        codecs.append({'id': index, 'name': name.value.decode('utf-8'), 'vendor_id': 0x10DE})
    selected['cuda_id'] = match_adapter(renderer, codecs)['id']
    return selected
