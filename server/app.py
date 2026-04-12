# server/app.py — OpenEnv multi-mode deployment entry point
import uvicorn
from app.main import app

def main():
    uvicorn.run(app, host="0.0.0.0", port=7860)

if __name__ == "__main__":
    main()