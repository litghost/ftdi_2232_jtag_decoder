class Buffer(object):
    """ deque like object that doesn't discard data on pop.

    This is useful for rewinding failed decodes.

    """
    def __init__(self):
        self.buf = []
        self.insert_boundry = set()
        self.pop_index = 0
        self.frames = {}
        self.begin_to_frame = {}
        self.frame = None

    def __len__(self):
        assert self.pop_index >= 0 and self.pop_index <= len(self.buf)
        return len(self.buf) - self.pop_index

    def extend(self, iterable, frame=None):
        original_index = len(self.buf)
        self.buf.extend(iterable)
        self.insert_boundry.add(len(self.buf))
        if frame is not None:
            self.frames[frame] = (original_index, len(self.buf))
            self.begin_to_frame[original_index] = frame

    def popleft(self):
        assert self.pop_index >= 0 and self.pop_index <= len(self.buf)
        if self.pop_index < len(self.buf):
            ret = self.buf[self.pop_index]

            # Update self.frame
            if self.pop_index == 0:
                self.frame = self.begin_to_frame[self.pop_index]

                b, e = self.frames[self.frame]
                assert self.pop_index == b
            else:
                b, e = self.frames[self.frame]
                assert self.pop_index >= b
                if self.pop_index >= e:
                    self.frame = self.begin_to_frame[self.pop_index]

                    b, e = self.frames[self.frame]
                    assert self.pop_index == b

            self.pop_index += 1
            return ret
        else:
            raise IndexError()

    def at_boundry(self):
        return self.pop_index in self.insert_boundry

    def current_frame(self):
        return self.frame

    def frame_of(self, idx):
        for frame, (begin, end) in self.frames.items():
            if idx >= begin and idx < end:
                return frame

    def get_context(self, C=10):
        """ Yield bytes before and after the current byte for context.

        Useful for debugging decode failures.

        """
        first_idx = max(self.pop_index - C, 0)
        delta = self.pop_index - first_idx
        for idx, data in enumerate(self.buf[first_idx:self.pop_index+C]):
            yield -delta + idx, data

