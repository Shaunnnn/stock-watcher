"""Shared pytest setup. Runs before any test module imports the app's own
modules, since build_index.py and rag_chat.py construct an OpenAI client
at import time and need OPENAI_API_KEY to exist (it never needs to be a
real key for these tests — nothing here makes a live API call)."""
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-for-tests")
os.environ.setdefault("EDGAR_USER_AGENT", "Test Suite test@example.com")
