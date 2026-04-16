from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str


class StreamCreate(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    member_ids: list[int] = Field(default_factory=list)


class StreamResponse(BaseModel):
    id: int
    name: str
    member_ids: list[int]


class EventCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class EventResponse(BaseModel):
    id: int
    stream_id: int
    user_id: int
    body: str
    created_at: str


class ReadReceiptCreate(BaseModel):
    pass


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=8, max_length=128)
