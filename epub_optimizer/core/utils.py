"""Small shared helpers for the core package."""

from __future__ import annotations


def fmt_bytes(size: float) -> str:
    """Format a byte count as a human-readable string (B/KB/MB/GB)."""
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.2f} GB"
