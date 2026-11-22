"""alpaca_imagebytes — ImageBytes header, element types, [x][y] orientation, JSON fallback."""
import struct

import numpy as np
import pytest

from services.camera.alpaca.alpaca_imagebytes import (
    HEADER_SIZE,
    parse_header,
    parse_imagearray_json,
    parse_imagebytes,
)
from services.camera.alpaca.alpaca_protocol import (
    AlpacaInvalidOperationError,
    AlpacaNotConnectedError,
    AlpacaProtocolError,
    CameraDisconnectedError,
    ImageElementType,
)
from tests.alpaca_fake import wire


def _header(error=0, ctid=7, stid=9, start=44, img=2, tx=8, rank=2, d1=0, d2=0, d3=0):
    return struct.pack("<11i", 1, error, ctid, stid, start, img, tx, rank, d1, d2, d3)


def test_header_is_44_bytes_of_eleven_little_endian_int32():
    assert HEADER_SIZE == 44
    h = parse_header(_header(ctid=123, stid=456, d1=5, d2=3) + b"\0" * 30)
    assert (h.metadata_version, h.client_transaction_id, h.server_transaction_id) == (1, 123, 456)
    assert (h.data_start, h.image_element_type, h.transmission_element_type) == (44, 2, 8)
    assert (h.rank, h.dimension1, h.dimension2, h.dimension3) == (2, 5, 3, 0)


def test_hand_built_payload_is_x_major_with_y_fastest():
    # Alpaca a[x][y] for a 3-wide, 2-high image: the stream is a[0][0], a[0][1], a[1][0] …
    width, height = 3, 2
    image = np.array([[10, 11, 12],
                      [20, 21, 22]])           # image[y][x]
    stream = [image[y][x] for x in range(width) for y in range(height)]
    payload = _header(tx=ImageElementType.UINT16, d1=width, d2=height) + \
        struct.pack(f"<{len(stream)}H", *stream)
    out = parse_imagebytes(payload)
    assert out.shape == (height, width)
    np.testing.assert_array_equal(out, image)
    assert out.flags["C_CONTIGUOUS"] and out.flags["OWNDATA"] and out.flags["WRITEABLE"]


@pytest.mark.parametrize("code,dtype", [
    (ImageElementType.INT16, np.int16), (ImageElementType.INT32, np.int32),
    (ImageElementType.DOUBLE, np.float64), (ImageElementType.SINGLE, np.float32),
    (ImageElementType.UINT64, np.uint64), (ImageElementType.BYTE, np.uint8),
    (ImageElementType.INT64, np.int64), (ImageElementType.UINT16, np.uint16),
    (ImageElementType.UINT32, np.uint32),
])
def test_every_transmission_element_type(code, dtype):
    image = (np.arange(35).reshape(5, 7) % 100).astype(dtype)
    payload = wire.encode_imagebytes(image, transmission_type=int(code))
    out = parse_imagebytes(payload)
    assert out.dtype == np.dtype(dtype)
    np.testing.assert_array_equal(out, image)


def test_non_square_frame_keeps_orientation():
    image = np.arange(6 * 11, dtype=np.uint16).reshape(6, 11)
    out = parse_imagebytes(wire.encode_imagebytes(image))
    np.testing.assert_array_equal(out, image)
    assert out[0, 10] == 10 and out[5, 0] == 55


def test_rank3_planes_last():
    image = np.arange(4 * 5 * 3, dtype=np.uint16).reshape(4, 5, 3)
    payload = wire.encode_imagebytes(image)
    assert parse_header(payload).rank == 3
    assert (parse_header(payload).dimension1, parse_header(payload).dimension2,
            parse_header(payload).dimension3) == (5, 4, 3)
    np.testing.assert_array_equal(parse_imagebytes(payload), image)


def test_error_payload_raises_mapped_exception_with_message():
    payload = wire.encode_imagebytes_error(0x40B, "No image is available")
    with pytest.raises(AlpacaInvalidOperationError, match="No image is available"):
        parse_imagebytes(payload)
    with pytest.raises(AlpacaNotConnectedError):
        parse_imagebytes(wire.encode_imagebytes_error(0x407, "gone"))


def test_not_connected_is_a_disconnect_for_the_capture_loop():
    with pytest.raises(CameraDisconnectedError):
        parse_imagebytes(wire.encode_imagebytes_error(0x407, "gone"))


@pytest.mark.parametrize("payload", [
    b"",
    b"\x01\x00\x00\x00" * 5,
    _header(d1=4, d2=4) + b"\0" * 10,                     # truncated data
    _header(d1=4, d2=4, start=20) + b"\0" * 64,           # DataStart inside the header
    _header(d1=4, d2=4, rank=4) + b"\0" * 64,             # bad rank
    _header(d1=0, d2=4) + b"\0" * 64,                     # zero dimension
    _header(d1=2, d2=2, tx=42) + b"\0" * 64,              # unknown element type
    struct.pack("<11i", 2, 0, 0, 0, 44, 2, 8, 2, 1, 1, 0) + b"\0\0",  # version 2
])
def test_malformed_payload_is_a_protocol_error(payload):
    with pytest.raises(AlpacaProtocolError):
        parse_imagebytes(payload)


def test_json_fallback_equals_imagebytes():
    image = np.random.default_rng(1).integers(0, 4096, (7, 9)).astype(np.uint16)
    via_bytes = parse_imagebytes(wire.encode_imagebytes(image))
    body = wire.imagearray_json(image, 3, 4)
    assert body["Value"][2][5] == image[5][2]   # Value[x][y]
    via_json = parse_imagearray_json(body)
    np.testing.assert_array_equal(via_json, via_bytes)
    assert via_json.shape == (7, 9)


def test_json_rank3_and_case_insensitive_keys():
    image = np.arange(2 * 3 * 3).reshape(2, 3, 3)
    body = {k.lower(): v for k, v in wire.imagearray_json(image, 1, 1).items()}
    np.testing.assert_array_equal(parse_imagearray_json(body), image)


def test_json_error_and_garbage():
    with pytest.raises(AlpacaInvalidOperationError):
        parse_imagearray_json({"ErrorNumber": 0x40B, "ErrorMessage": "no image"})
    with pytest.raises(AlpacaProtocolError):
        parse_imagearray_json({"ErrorNumber": 0, "Value": [[1, 2], [3]], "Rank": 2})
    with pytest.raises(AlpacaProtocolError):
        parse_imagearray_json({"ErrorNumber": 0, "Value": [[1, 2]], "Rank": 3})
    with pytest.raises(AlpacaProtocolError):
        parse_imagearray_json([1, 2, 3])
