"""Surface weather and the ATIS: moved to ``atc_core.atis`` in 0.4; the old names stay here."""

from localtc.atc_core.atis import (  # noqa: F401
    CROSSWIND_CAUTION_KT,
    LETTERS,
    MAX_TAILWIND_KT,
    MIN_UPDATE_S,
    AtisBoard,
    AtisInfo,
    Weather,
    WeatherTracker,
    components,
    magnetic_wind,
    remarks,
    wind_text,
)
