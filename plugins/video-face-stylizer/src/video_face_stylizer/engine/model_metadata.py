"""Read the small subset of bundled TFLite metadata needed by the GPU backend.

Field numbers follow the public TFLite and MediaPipe metadata schemas. Model
weights are never changed and detector anchors come from the model itself.
"""
from __future__ import annotations

import struct
import numpy as np


class Table:
    def __init__(self, data, position=None):
        self.data = data
        self.position = struct.unpack_from('<I', data)[0] if position is None else position

    def field(self, index):
        vtable = self.position - struct.unpack_from('<i', self.data, self.position)[0]
        offset = 4 + index * 2
        if offset >= struct.unpack_from('<H', self.data, vtable)[0]:
            return None
        value = struct.unpack_from('<H', self.data, vtable + offset)[0]
        return self.position + value if value else None

    def scalar(self, index, fmt='I', default=0):
        field = self.field(index)
        return struct.unpack_from('<' + fmt, self.data, field)[0] if field else default

    def indirect(self, position):
        return position + struct.unpack_from('<I', self.data, position)[0]

    def table(self, index):
        return Table(self.data, self.indirect(self.field(index)))

    def vector(self, index, dtype='u1'):
        field = self.field(index)
        if field is None:
            return np.empty(0, dtype=dtype)
        start = self.indirect(field)
        length = struct.unpack_from('<I', self.data, start)[0]
        return np.frombuffer(self.data, dtype=dtype, count=length, offset=start + 4)

    def tables(self, index):
        field = self.field(index)
        if field is None:
            return []
        start = self.indirect(field)
        return [Table(self.data, self.indirect(start + 4 + i * 4))
                for i in range(struct.unpack_from('<I', self.data, start)[0])]

    def string(self, index):
        return self.vector(index).tobytes().decode('utf-8')


def metadata(data):
    model = Table(data)
    buffers = model.tables(4)
    for entry in model.tables(6):
        if entry.string(0) == 'TFLITE_METADATA':
            return Table(buffers[entry.scalar(1)].vector(0).tobytes()).tables(3)[0]
    raise ValueError('Bundled model is missing TFLite metadata.')


def normalization(data):
    tensor = metadata(data).tables(2)[0]
    for unit in tensor.tables(4):
        if unit.scalar(0, 'B') == 1:  # ProcessUnitOptions.NormalizationOptions
            options = unit.table(1)
            mean, std = options.vector(0, '<f4'), options.vector(1, '<f4')
            if len(mean) in {1, 3} and len(std) in {1, 3} and np.all(std > 0):
                return np.broadcast_to(mean, (3,)).copy(), np.broadcast_to(std, (3,)).copy()
    raise ValueError('Bundled model is missing input normalization.')


def detector_metadata(data):
    for entry in metadata(data).tables(9):
        if entry.string(0) == 'DETECTOR_METADATA':
            options = Table(entry.vector(1).tobytes())
            anchors = options.table(1).table(0).tables(0)
            anchors = np.array([[a.scalar(i, 'f') for i in range(4)] for a in anchors], np.float32)
            decoding = options.table(2)
            scales = np.array([decoding.scalar(i, 'f') for i in range(6, 10)], np.float32)
            return anchors, scales, bool(decoding.scalar(10, '?')), bool(decoding.scalar(11, '?'))
    raise ValueError('Bundled detector is missing raw-output decoding metadata.')
