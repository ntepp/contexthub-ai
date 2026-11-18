"""Optical character recognition behind a narrow port (ADR-003).

``base`` defines the ``OcrEngine`` protocol, ``tesseract`` the default local adapter,
``registry`` picks the engine (with a test override), ``preprocess`` holds the
engine-independent image clean-up and ``reader`` turns one page image into text plus
a confidence measure.
"""
