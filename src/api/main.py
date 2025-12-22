import os
from datetime import timedelta
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import models, schemas
from .db import engine, get_db, Base
from .auth import hash_password, verify_password, create_access_token
from .deps import get_current_user

# Create DB tables if they don't exist (for SQLite/local dev)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="IP LLM Auth API")

# Add CORS for Streamlit front-end (adjust origins in production)
origins = os.getenv("CORS_ORIGINS", "http://localhost:8501").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Signup endpoint
@app.post("/api/signup", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def signup(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    Create a new user. Password is hashed before storing.
    """
    # Check existing
    existing = db.query(models.User).filter(models.User.email == user_in.email.lower()).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    user = models.User(
        email=user_in.email.lower(),
        password_hash=hash_password(user_in.password),
        name=user_in.name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

# Login endpoint
@app.post("/api/login", response_model=schemas.Token)
def login(form_data: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    Login using email + password. Returns JWT access token.
    Note: this endpoint expects {email, password} in body.
    """
    user = db.query(models.User).filter(models.User.email == form_data.email.lower()).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    access_token = create_access_token(subject=user.id, expires_delta=timedelta(minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))))
    return {"access_token": access_token, "token_type": "bearer"}

# Get current user
@app.get("/api/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    """
    Return basic user info for the authenticated user.
    """
    return current_user