from pathlib import Path
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VIDEO_SUFFIXES={'.mp4','.mov','.mkv','.avi','.webm','.m4v'}

class RegionKeyframe(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    seconds: float = Field(ge=0,description='Absolute time in the source video, before start_seconds trimming.')
    box: tuple[float,float,float,float] = Field(description='Normalized head bounds [left, top, right, bottom], each in [0,1].')

    @field_validator('box')
    @classmethod
    def valid_box(cls,value):
        x0,y0,x1,y1=value
        if not (0<=x0<x1<=1 and 0<=y0<y1<=1):raise ValueError('box must satisfy 0 <= left < right <= 1 and 0 <= top < bottom <= 1.')
        return value

class HeadRegion(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0,description='Exclusive absolute source time. The region never extends beyond this time.')
    keyframes: list[RegionKeyframe] = Field(min_length=1,max_length=300,description='Increasing source times. Head boxes interpolate between keys and hold at the interval edges.')

    @model_validator(mode='after')
    def valid_interval(self):
        if self.end_seconds<=self.start_seconds:raise ValueError('end_seconds must be after start_seconds.')
        times=[key.seconds for key in self.keyframes]
        if any(t<self.start_seconds or t>=self.end_seconds for t in times) or any(a>=b for a,b in zip(times,times[1:])):
            raise ValueError('Keyframe times must be increasing and inside the region interval.')
        return self

class ProcessingOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    coverage: Literal['head','face'] = Field(default='head',description='head: opaque white plaster covering hair and face, with independent segmentation fallback. face: legacy 3D face mesh, retaining hair; missed detections stay unchanged.')
    person_filter: bool = Field(default=True,description='Require independent person detection for automatic masks, and constrain them to person regions. Reduces false heads on background objects. Manual head_regions bypass this filter.')
    person_detection_confidence: float = Field(default=.35,ge=.1,le=.95,description='Person detection threshold. If an unusual close-up is missed, inspect the shot and use head_regions or explicitly disable person_filter.')
    detection_max_side: int = Field(default=1280,ge=256,le=2560,description='Working image longest side for face detection. Separate from output resolution. Tiled/local detection helps small faces that still fail full-frame detection.')
    min_face_detection_confidence: float = Field(default=.35,ge=.05,le=.95)
    min_face_presence_confidence: float = Field(default=.45,ge=.05,le=.95)
    min_tracking_confidence: float = Field(default=.5,ge=.05,le=.95)
    tiled_detection: bool = Field(default=True,description='On a missed full-frame face, search previous/segmented head crops and overlapping grid crops; map results back to source coordinates.')
    detection_tile_grid: int = Field(default=3,ge=2,le=4,description='Grid size per axis when tiled_detection is enabled: 2, 3 or 4. Finer grids help distant faces at additional processing cost.')
    tiled_segmentation: bool = Field(default=True,description='In head mode, when full-frame segmentation finds no head, retry the overlapping grid crops. Shares detection_tile_grid; improves distant heads at additional processing cost.')
    segmentation_confidence: float = Field(default=.4,ge=.1,le=.95)
    segmentation_requires_hair: bool = Field(default=True,description='Require hair support for segmentation-only regions to avoid painting hands/body skin. Detected face geometry and manual regions still work without hair. May miss bald/profile heads; inspect and use manual regions or disable explicitly.')
    segmentation_head_fraction: float = Field(default=.35,ge=.2,le=1,description='Accept segmentation-only head centers in this upper fraction of a detected person box; rejects hands and torso patches. Default suits upright/seated people. Use 1 for unusual poses, or manual head_regions. Face meshes are not limited by this fraction.')
    mask_padding: float = Field(default=.08,ge=0,le=.4,description='Expand detected head silhouettes by this fraction of the smaller head dimension.')
    temporal_hold_seconds: float = Field(default=.2,ge=0,le=1,description='Maximum short detection gap to bridge using validated optical flow. Never reused across detected cuts; zero disables.')
    head_regions: list[HeadRegion] = Field(default_factory=list,max_length=64,description='Optional timed head masks for difficult distant/profile/back-facing shots. Apply opaque plaster regardless of face detection. Source-relative times; do not interpolate across cuts.')

    @model_validator(mode='after')
    def regions_need_head_mode(self):
        if self.head_regions and self.coverage!='head':raise ValueError('head_regions requires coverage=head.')
        return self

class VideoRequest(ProcessingOptions):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    input_path: str = Field(description='Absolute local path of the user-selected source video. URLs are not supported.')
    output_directory: str = Field(description='Absolute local folder where the user wants the new video and job records saved.')
    start_seconds: float = Field(default=0,ge=0,description='Start time in the source video; default 0.')
    duration_seconds: float | None = Field(default=None,gt=0,description='Seconds to process; omit for the entire remaining video. Use 30 for a 30-second test.')
    max_height: int = Field(default=0,ge=0,le=4320,description='0 keeps source resolution; otherwise use an even height of at least 64. No upscaling.')
    output_filename: str | None = Field(default=None,max_length=180,description='Optional new .mp4 basename. Existing files are never overwritten.')

    @field_validator('input_path','output_directory')
    @classmethod
    def absolute_path(cls,value):
        if '\x00' in value or not Path(value).is_absolute():
            raise ValueError('An absolute local filesystem path is required.')
        return str(Path(value).resolve())

    @field_validator('input_path')
    @classmethod
    def video_file(cls,value):
        path=Path(value)
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            raise ValueError('Supported videos: mp4, mov, mkv, avi, webm, m4v.')
        if not path.is_file():raise ValueError('Input video does not exist.')
        return value

    @field_validator('max_height')
    @classmethod
    def valid_height(cls,value):
        if value and (value<64 or value%2):raise ValueError('max_height must be 0 or an even integer >=64.')
        return value

    @field_validator('output_filename')
    @classmethod
    def basename(cls,value):
        if value is None:return value
        if (Path(value).name!=value or re.search(r'[<>:"/\\|?*\x00-\x1f]',value)
                or value.endswith((' ','.')) or Path(value).suffix.lower()!='.mp4'):
            raise ValueError('output_filename must be a simple .mp4 filename, without a directory.')
        if Path(value).stem.upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
            raise ValueError('Reserved filename.')
        return value
