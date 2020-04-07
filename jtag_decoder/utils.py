def bits_to_bytes(in_bits):
    """ Converts list of bits to list of bytes 

    >>> bits_to_bytes([])
    >>> bits_to_bytes([0])
    [0]
    >>> bits_to_bytes([1])
    [1]
    >>> bits_to_bytes([1, 1, 1, 1, 1, 1, 1, 1])
    [255]
    >>> bits_to_bytes([1, 1, 1, 1, 1, 1, 1, 1, 1])
    [255, 1]
    >>> bits_to_bytes([1, 1, 1, 1, 1, 1, 1, 1, 0])
    [255, 0]

    """
    itr = iter(in_bits)
    bit_offset = 0
    byte = 0
    while True:
        try:
            bit = next(itr)
            if bit:
                byte |= 1 << bit_offset

            bit_offset += 1
            if bit_offset == 8:
                yield byte
                bit_offset = 0
                byte = 0
        except StopIteration:
            if bit_offset > 0:
                yield byte
            break
