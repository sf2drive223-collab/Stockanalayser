"""Start the deployed API using the hosting provider's port."""
import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
