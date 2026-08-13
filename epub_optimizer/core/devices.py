"""Device presets for supported Amazon Kindle e-readers."""

from __future__ import annotations

from dataclasses import dataclass

#: The Kindle home screen / cover gallery renders covers at a strict
#: 3:4 (width:height) portrait ratio. Covers that are not 3:4 are
#: pillarboxed or letterboxed with a plain background — exactly the
#: artifact the "blurred sidebars" feature is designed to remove.
COVER_ASPECT: tuple[int, int] = (3, 4)


@dataclass(frozen=True)
class DevicePreset:
    """A target e-reader profile: screen limits plus cover styling knobs."""

    #: Stable identifier used to persist the selection in QSettings.
    key: str
    #: Human-readable label shown in the device dropdown.
    name: str
    #: Maximum screen resolution of the target device (pixels).
    max_width: int
    max_height: int
    #: Base Gaussian-blur radius (px) for the blurred cover background at
    #: the reference canvas height of 800 px. It is scaled linearly for
    #: taller canvases so the *visual* blur amount stays consistent across
    #: devices instead of becoming comparatively weaker on high-res ones.
    base_blur_radius: int = 18

    @property
    def cover_canvas(self) -> tuple[int, int]:
        """Return the largest *strict* 3:4 portrait canvas that fits the screen.

        For the Paperwhite the panel is 1072×1448 (≈ 3:4.05), which is not
        exactly 3:4, so we compute the biggest exact-3:4 canvas that fits
        inside those bounds (1072×1429). The e-reader fills the remaining
        sliver itself.
        """
        height_from_width = round(self.max_width * COVER_ASPECT[1] / COVER_ASPECT[0])
        if height_from_width <= self.max_height:
            return self.max_width, height_from_width
        width_from_height = round(self.max_height * COVER_ASPECT[0] / COVER_ASPECT[1])
        return width_from_height, self.max_height


#: The devices this application targets (Kindle 10th generation models).
DEVICE_PRESETS: tuple[DevicePreset, ...] = (
    DevicePreset(
        key="kindle_10th_basic",
        name="Kindle 10th Gen Basic (600×800)",
        max_width=600,
        max_height=800,
    ),
    DevicePreset(
        key="kindle_10th_paperwhite",
        name="Kindle 10th Gen Paperwhite (1072×1448)",
        max_width=1072,
        max_height=1448,
    ),
)

#: Device used when nothing has been chosen yet (the most common device).
DEFAULT_DEVICE_KEY: str = DEVICE_PRESETS[0].key


def get_device_preset(key: str) -> DevicePreset:
    """Return the preset identified by ``key`` (falling back to the default)."""
    for preset in DEVICE_PRESETS:
        if preset.key == key:
            return preset
    return DEVICE_PRESETS[0]
