#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from connector import WebSocketDecoder

def read_frames(dec):
    frames = []
    while True:
        frame = dec.read_frame()
        if frame is None:
            break
        frames.append((frame.fin, frame.opcode, frame.payload))
    return frames

def read_messages(dec):
    messages = []
    while True:
        message = dec.read_message()
        if message is None:
            break
        messages.append((message.opcode, message.payload))
    return messages

class TestWebSocketDecoder(unittest.TestCase):
    def test_rfc(self):
        """Test samples from RFC 6455 section 5.7."""
        TESTS = [
            ("\x81\x05\x48\x65\x6c\x6c\x6f", False,
                [(True, 1, "Hello")],
                [(1, u"Hello")]),
            ("\x81\x85\x37\xfa\x21\x3d\x7f\x9f\x4d\x51\x58", True,
                [(True, 1, "Hello")],
                [(1, u"Hello")]),
            ("\x01\x03\x48\x65\x6c\x80\x02\x6c\x6f", False,
                [(False, 1, "Hel"), (True, 0, "lo")],
                [(1, u"Hello")]),
            ("\x89\x05\x48\x65\x6c\x6c\x6f", False,
                [(True, 9, "Hello")],
                [(9, u"Hello")]),
            ("\x8a\x85\x37\xfa\x21\x3d\x7f\x9f\x4d\x51\x58", True,
                [(True, 10, "Hello")],
                [(10, u"Hello")]),
            ("\x82\x7e\x01\x00" + "\x00" * 256, False,
                [(True, 2, "\x00" * 256)],
                [(2, "\x00" * 256)]),
            ("\x82\x7f\x00\x00\x00\x00\x00\x01\x00\x00" + "\x00" * 65536, False,
                [(True, 2, "\x00" * 65536)],
                [(2, "\x00" * 65536)]),
        ]
        for data, use_mask, expected_frames, expected_messages in TESTS:
            dec = WebSocketDecoder(use_mask = use_mask)
            dec.feed(data)
            actual_frames = read_frames(dec)
            self.assertEqual(actual_frames, expected_frames)

            dec = WebSocketDecoder(use_mask = use_mask)
            dec.feed(data)
            actual_messages = read_messages(dec)
            self.assertEqual(actual_messages, expected_messages)

            dec = WebSocketDecoder(use_mask = not use_mask)
            dec.feed(data)
            self.assertRaises(WebSocketDecoder.MaskingError, dec.read_frame)

    def test_empty_feed(self):
        """Test that the decoder can handle a zero-byte feed."""
        dec = WebSocketDecoder()
        self.assertIsNone(dec.read_frame())
        dec.feed("")
        self.assertIsNone(dec.read_frame())
        dec.feed("\x81\x05H")
        self.assertIsNone(dec.read_frame())
        dec.feed("ello")
        self.assertEqual(read_frames(dec), [(True, 1, u"Hello")])

    def test_empty_frame(self):
        """Test that a frame may contain a zero-byte payload."""
        dec = WebSocketDecoder()
        dec.feed("\x81\x00")
        self.assertEqual(read_frames(dec), [(True, 1, u"")])
        dec.feed("\x82\x00")
        self.assertEqual(read_frames(dec), [(True, 2, "")])

    def test_empty_message(self):
        """Test that a message may have a zero-byte payload."""
        dec = WebSocketDecoder()
        dec.feed("\x01\x00\x00\x00\x80\x00")
        self.assertEqual(read_messages(dec), [(1, u"")])
        dec.feed("\x02\x00\x00\x00\x80\x00")
        self.assertEqual(read_messages(dec), [(2, "")])

    def test_interleaved_control(self):
        """Test that control messages interleaved with fragmented messages are
        returned."""
        dec = WebSocketDecoder()
        dec.feed("\x89\x04PING\x01\x03Hel\x8a\x04PONG\x80\x02lo\x89\x04PING")
        self.assertEqual(read_messages(dec), [(9, "PING"), (10, "PONG"), (1, u"Hello"), (9, "PING")])

    def test_fragmented_control(self):
        """Test that illegal fragmented control messages cause an error."""
        dec = WebSocketDecoder()
        dec.feed("\x09\x04PING")
        self.assertRaises(ValueError, dec.read_message)

    def test_zero_opcode(self):
        """Test that it is an error for the first frame in a message to have an
        opcode of 0."""
        dec = WebSocketDecoder()
        dec.feed("\x80\x05Hello")
        self.assertRaises(ValueError, dec.read_message)
        dec = WebSocketDecoder()
        dec.feed("\x00\x05Hello")
        self.assertRaises(ValueError, dec.read_message)

    def test_nonzero_opcode(self):
        """Test that every frame after the first must have a zero opcode."""
        dec = WebSocketDecoder()
        dec.feed("\x01\x01H\x01\x02el\x80\x02lo")
        self.assertRaises(ValueError, dec.read_message)
        dec = WebSocketDecoder()
        dec.feed("\x01\x01H\x00\x02el\x01\x02lo")
        self.assertRaises(ValueError, dec.read_message)

    def test_utf8(self):
        """Test that text frames (opcode 1) are decoded from UTF-8."""
        text = u"Hello World or Καλημέρα κόσμε or こんにちは 世界 or \U0001f639"
        utf8_text = text.encode("utf-8")
        dec = WebSocketDecoder()
        dec.feed("\x81" + chr(len(utf8_text)) + utf8_text)
        self.assertEqual(read_messages(dec), [(1, text)])

    def test_wrong_utf8(self):
        """Test that failed UTF-8 decoding causes an error."""
        TESTS = [
            "\xc0\x41", # Non-shortest form.
            "\xc2", # Unfinished sequence.
        ]
        for test in TESTS:
            dec = WebSocketDecoder()
            dec.feed("\x81" + chr(len(test)) + test)
            self.assertRaises(ValueError, dec.read_message)

    def test_overly_large_payload(self):
        """Test that large payloads are rejected."""
        dec = WebSocketDecoder()
        dec.feed("\x82\x7f\x00\x00\x00\x00\x01\x00\x00\x00")
        self.assertRaises(ValueError, dec.read_frame)

if __name__ == "__main__":
    unittest.main()
