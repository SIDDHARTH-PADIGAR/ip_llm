from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr 

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None
    
class UserOut(BaseModel):
    id: int
    email: EmailStr
    name: Optional[str] = None
    created_at: Optional[datetime] = None
    
    class Config: 
        orm_mode = True
        from_attributes = True
        
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer" 