"""Entrypoint: `python -m ebony_enriching` -> uvicorn."""

import logging

import uvicorn

from ebony_enriching.config import HOST, PORT


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    uvicorn.run("ebony_enriching.server:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
