from typing import Literal

from pydantic import BaseModel


TokenAuthority = Literal["PLATFORM_ISSUED", "SELF_MANAGED"]
TokenVisibility = Literal["PUBLIC", "PRIVATE"]
TokenMode = Literal["PERSONAL", "TEAM", "ORGANIZATION"]
SubjectType = Literal["USER", "TEAM", "ORGANIZATION"]


class TokenContext(BaseModel):
    token_id: str
    authority: TokenAuthority
    visibility: TokenVisibility
    mode: TokenMode
    subject_type: SubjectType
    subject_id: str
    scopes: list[str]


class TokenVerifyRequest(BaseModel):
    token: str


class TokenVerifyResponse(BaseModel):
    valid: bool
    context: TokenContext | None = None