"""Pydantic models for the Map Service."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EventMeta(BaseModel):
    session_id: str
    user_id: str


class MapKind(str, Enum):
    WORLD = "world"
    REGION = "region"
    CITY = "city"
    DUNGEON = "dungeon"
    BATTLEMAP = "battlemap"


class LayerType(str, Enum):
    TERRAIN = "terrain"
    OBJECT = "object"
    ROOF = "roof"
    WALL = "wall"
    DOOR = "door"
    LIGHT = "light"
    MARKER = "marker"


class AggregateType(str, Enum):
    CHARACTER = "character"
    NPC = "npc"
    COMBAT = "combat"


class MapCreate(BaseModel):
    campaign_id: UUID
    name: str = Field(min_length=1, max_length=200)
    kind: MapKind = MapKind.DUNGEON
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    tile_size: int = Field(default=5, ge=1)
    description: str = ""
    background_asset_key: Optional[str] = None
    meta: Optional[EventMeta] = None


class MapUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    kind: Optional[MapKind] = None
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)
    tile_size: Optional[int] = Field(default=None, ge=1)
    description: Optional[str] = None
    background_asset_key: Optional[str] = None
    active: Optional[bool] = None
    meta: Optional[EventMeta] = None


class MapOut(BaseModel):
    map_id: UUID
    campaign_id: UUID
    name: str
    kind: MapKind
    width: int
    height: int
    tile_size: int
    description: str
    background_asset_key: Optional[str] = None
    active: bool
    created_at: datetime
    updated_at: datetime


class LayerCreate(BaseModel):
    type: LayerType
    name: str = Field(min_length=1, max_length=200)
    z_index: int = 0
    visible: bool = True
    features: dict[str, Any] = Field(default_factory=dict)
    meta: Optional[EventMeta] = None


class LayerUpdate(BaseModel):
    type: Optional[LayerType] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    z_index: Optional[int] = None
    visible: Optional[bool] = None
    features: Optional[dict[str, Any]] = None
    meta: Optional[EventMeta] = None


class LayerOut(BaseModel):
    layer_id: UUID
    map_id: UUID
    campaign_id: UUID
    type: LayerType
    name: str
    z_index: int
    visible: bool
    features: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class FogStatePut(BaseModel):
    campaign_id: UUID
    character_id: UUID
    explored_cells: list[str] = Field(default_factory=list)
    meta: Optional[EventMeta] = None


class FogStatePatch(BaseModel):
    campaign_id: UUID
    character_id: UUID
    add_cells: list[str] = Field(default_factory=list)
    meta: Optional[EventMeta] = None


class FogStateOut(BaseModel):
    map_id: UUID
    campaign_id: UUID
    character_id: UUID
    explored_cells: list[str]
    updated_at: datetime


class MapSelectionUpsert(BaseModel):
    campaign_id: UUID
    map_id: UUID
    character_id: Optional[UUID] = None
    meta: Optional[EventMeta] = None


class MapSelectionOut(BaseModel):
    campaign_id: UUID
    map_id: UUID
    character_id: Optional[UUID] = None
    scope: str
    updated_at: datetime


class TokenUpsert(BaseModel):
    campaign_id: UUID
    encounter_id: Optional[UUID] = None
    aggregate_id: UUID
    aggregate_type: AggregateType
    x: int
    y: int
    visible: bool = True
    meta: Optional[EventMeta] = None


class TokenUpdate(BaseModel):
    x: Optional[int] = None
    y: Optional[int] = None
    visible: Optional[bool] = None
    meta: Optional[EventMeta] = None


class TokenOut(BaseModel):
    token_id: UUID
    map_id: UUID
    campaign_id: UUID
    encounter_id: Optional[UUID] = None
    aggregate_id: UUID
    aggregate_type: AggregateType
    x: int
    y: int
    visible: bool
    created_at: datetime
    updated_at: datetime


class MapSnapshot(BaseModel):
    map: MapOut
    layers: list[LayerOut]
    fog_of_war: FogStateOut
    tokens: list[TokenOut]