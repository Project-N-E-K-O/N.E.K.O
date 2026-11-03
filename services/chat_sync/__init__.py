"""Private chat and character-profile synchronization services.

Importing this package must not open storage or start a scheduler. The application
starts synchronization explicitly after its storage barrier is released.
"""
