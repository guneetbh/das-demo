# Loaded as soon as anything imports `neutail`, before gateway.py or
# session_store.py read ANTHROPIC_API_KEY / REDIS_URL from os.environ —
# a .env file in the project root just works, no manual `export` needed.
# Optional: the whole point of this package is to run standalone with zero
# required dependencies beyond stdlib, so a missing python-dotenv degrades
# to "use real environment variables only" rather than an ImportError.
try:
    import os

    from dotenv import dotenv_values, load_dotenv

    load_dotenv()  # default override=False: never clobbers a real, already-set value

    # Real gap found running this demo from inside a Claude Code/Desktop
    # terminal: that environment pre-sets ANTHROPIC_API_KEY="" (empty, not
    # absent) itself, e.g. for its own unrelated internal use. load_dotenv()'s
    # override=False only checks *presence* in os.environ, not truthiness —
    # an empty string already counts as "set", so the real key from .env was
    # silently never applied. gateway.py then saw a falsy key and skipped
    # straight past the Anthropic fallback with no error, which is a much
    # more confusing failure than "key missing" (confirmed: OPENROUTER_API_KEY
    # loaded fine here since nothing pre-set *that* one — only ANTHROPIC_API_KEY
    # collided). Fix: only for keys still empty/absent after the call above,
    # apply .env's value directly — never touches a real non-empty value,
    # whether it came from a shell export or an ambient tool's own config.
    for key, value in dotenv_values().items():
        if value and not os.environ.get(key):
            os.environ[key] = value
except ImportError:
    pass
