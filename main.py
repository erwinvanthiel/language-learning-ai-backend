from fastapi import FastAPI


app = FastAPI(title="Language Learning AI API")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Hello, world!"}
