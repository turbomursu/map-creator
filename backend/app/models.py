from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LAYERS = {
    "water", "waterway", "landcover", "landuse", "park", "mountain_peak",
    "transportation", "transportation_name", "building", "boundary", "place", "water_name",
}
NATURE = ["water", "waterway", "landcover", "landuse", "park"]
STANDARD = NATURE + ["transportation", "building", "boundary", "place"]


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="My region", min_length=1, max_length=100)
    geometry: dict
    minzoom: int = Field(default=0, ge=0, le=14)
    maxzoom: int = Field(default=14, ge=0, le=14)
    layers: list[str] = Field(default_factory=lambda: NATURE.copy(), min_length=1)
    format: Literal["pmtiles", "mbtiles"] = "pmtiles"
    memory_gb: int = Field(default=2, ge=1, le=256)
    sources: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def valid_configuration(self):
        if self.minzoom > self.maxzoom:
            raise ValueError("Minimum zoom must not exceed maximum zoom.")
        if set(self.layers) - LAYERS or len(set(self.layers)) != len(self.layers):
            raise ValueError("Select unique supported tile layers.")
        return self


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: JobSpec
    preflight_id: str
