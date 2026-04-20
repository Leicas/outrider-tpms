"""Constants for the Garmin Outrider TPMS integration."""

from __future__ import annotations

DOMAIN = "outrider_tpms"

# The Outrider exposes a vendor service whose UUID differs by the last byte
# between the front and rear sensor (23 vs 24). We match on the shared prefix.
OUTRIDER_SERVICE_PREFIX = "efcd1400"
OUTRIDER_NOTIFY_CHAR_PREFIX = "efcd1401"

# Atmospheric pressure at sea level in PSI — subtracted from absolute reading
# to produce gauge pressure (what pumps and apps display).
ATM_PSI = 14.6959488

# PSI → kPa conversion factor.
PSI_TO_KPA = 6.89476

# Advertised local names — used for wheel position inference.
LOCAL_NAME_FRONT = "OutriderF"
LOCAL_NAME_REAR = "OutriderR"

# Config entry data keys.
CONF_LOCAL_NAME = "local_name"
