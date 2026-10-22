from fastapi import FastAPI

app = FastAPI(title="PySwarm API")


@app.get("/")
async def root():
    return {"message": "PySwarm Backend is running!"}
