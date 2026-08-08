"""PCI ID repository plugin.

pci.ids (https://pci-ids.ucw.cz/) is a plain-text database of PCI vendor and
device IDs maintained by the community and shipped with every Linux distro.
New GPUs, NICs, and other silicon usually get a device ID registered here
before or around launch -- often before any marketing material exists. A new
line appearing is a genuinely high-signal, low-noise leak indicator, and the
format is stable and structured, which is exactly why this is one of the two
sources chosen for M1 over social/forum sources.

File format (top-level vendor/device section only -- M1 does not parse the
subsystem lines or the device-class section that follows it):

    # comment
    10de  NVIDIA Corporation
    \t2782  AD103 [GeForce RTX 4080]
    \t\t1462 5001  Motherboard model   <- subvendor/subdevice, skipped in M1

As with the RSS plugin, the network fetch is injected so this stays
unit-testable against a small fixture string instead of the real ~3MB file.
"""

from __future__ import annotations

import urllib.request
from typing import Callable, Iterable, Optional

from semi_intel.domain.enums import SourceType
from semi_intel.ingestion.base import RawItem, SourcePlugin

DEFAULT_PCI_IDS_URL = "https://pci-ids.ucw.cz/v2.2/pci.ids"

FetchFn = Callable[[str], str]


def _default_fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


class PciIdsSourcePlugin(SourcePlugin):
    source_type = SourceType.REGISTRY

    def __init__(
        self,
        name: str = "PCI ID Repository",
        url: str = DEFAULT_PCI_IDS_URL,
        default_trust_weight: float = 0.9,
        fetch_fn: Optional[FetchFn] = None,
    ):
        self.name = name
        self.url = url
        self.default_trust_weight = default_trust_weight
        self._fetch_fn = fetch_fn or _default_fetch

    def fetch(self) -> Iterable[RawItem]:
        text = self._fetch_fn(self.url)
        yield from self._parse(text)

    @staticmethod
    def _parse(text: str) -> Iterable[RawItem]:
        vendor_id: Optional[str] = None
        vendor_name: Optional[str] = None

        for line in text.splitlines():
            if not line.strip() or line.startswith("#"):
                continue

            if line.startswith("\t\t"):
                continue  # subvendor/subdevice entries -- out of scope for M1

            if line.startswith("\t"):
                if vendor_id is None:
                    continue  # malformed / mid-file resume, skip defensively
                parts = line.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                device_id, device_name = parts
                external_id = f"{vendor_id}:{device_id}"
                yield RawItem(
                    title=f"New PCI device: {vendor_name} {device_name}",
                    content=(
                        f"vendor={vendor_name} ({vendor_id}) "
                        f"device={device_name} ({device_id})"
                    ),
                    external_id=external_id,
                    url=None,
                    observed_at=None,
                    raw={"vendor_id": vendor_id, "vendor_name": vendor_name,
                         "device_id": device_id, "device_name": device_name},
                )
                continue

            # Top-level line: either a vendor entry or the start of the
            # device-class section ("C 03  Display controller" etc), which
            # marks the end of what M1 parses.
            if line.startswith("C ") and len(line) > 2 and line[2:4].strip():
                break

            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            candidate_id, candidate_name = parts
            if len(candidate_id) == 4 and all(c in "0123456789abcdef" for c in candidate_id.lower()):
                vendor_id, vendor_name = candidate_id, candidate_name
