from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str


class RoomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    member_ids: list[int] = Field(default_factory=list)


class RoomResponse(BaseModel):
    id: int
    name: str
    member_ids: list[int]


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class MessageResponse(BaseModel):
    id: int
    room_id: int
    user_id: int
    body: str
    created_at: str


class ReadReceiptCreate(BaseModel):
    pass


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=8, max_length=128)
