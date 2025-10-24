from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from api.epo_client import EPOClient

app = FastAPI(title="Patent History Analyzer")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Patent History Analyzer API"}

@app.get("/patent/{publication_number}")
async def get_patent_info(publication_number: str):
    try:
        client = EPOClient()
        data = client.get_patent_data(publication_number)
        return JSONResponse(content=data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))