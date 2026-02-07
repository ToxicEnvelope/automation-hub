from src.app import app
from uvicorn import run

if __name__ == '__main__':
    run(app, host='0.0.0.0', port=80, server_header=False, reload=False)
