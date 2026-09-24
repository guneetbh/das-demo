# Loaded as soon as anything imports `neutail`, before gateway.py or
# session_store.py read ANTHROPIC_API_KEY / REDIS_URL from os.environ —
# a .env file in the project root just works, no manual `export` needed.
# Optional: the whole point of this package is to run standalone with zero
# required dependencies beyond stdlib, so a missing python-dotenv degrades
# to "use real environment variables only" rather than an ImportError.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass
