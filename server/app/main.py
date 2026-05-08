from fastapi import FastAPI #type:ignore
from fastapi.middleware.cors import CORSMiddleware #cors middleware  #type:ignore
from app.routes.content_routes import router
app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers =["*"],
)
#routes connected to the main app
app.include_router(router)

@app.get("/") #python decorator 
def home():
    return {"message": "Backend is running"}