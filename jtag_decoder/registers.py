from collections import deque


class ShiftRegister(object):
    def __init__(self, width):
        self.data = deque()
        self.width = width

        for _ in range(width):
            self.data.append(0)

    def shift(self, di):
        do = self.data.popleft()
        self.data.append(int(di))
        return do

    def load(self, data):
        for idx in range(self.width):
            self.data[idx] = 1 if (data & (1 << idx)) != 0 else 0

    def read(self):
        data = 0
        for idx, bit in enumerate(self.data):
            if bit:
                data |= (1 << idx)

        return data


class SinkRegister(object):
    """ Represents a DR that simply sinks the clocked in data forever.

    The CFG_IN register uses this type to forward the input bitstream directly
    to the configuration engine.
    """
    def __init__(self):
        self.data = []

    def shift(self, di):
        self.data.append(di)
        return 0

    def read(self):
        return self.data
