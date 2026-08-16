# AGENTS.md

## Resources

There're four ways you can get useful information about this project. Ordered by their efficiency:

1. Read `app/README.md`, `assets/README.md` for a primary brief view.
2. Use the skills at `.agents/skills/` directory for specific knowledge.
3. Explore `docs/` for detailed business knowledge.
4. Read the existing code.

## Rules

1. *Don't* do these dangerous actions *without* the user's command:
    - *Don't* commit or delete untracked files or irrelevant files *without* the user's command.
    - *Don't* modify or drop current data directory or database *without* the user's command.
    - *Don't* push to remote or modify remote branches *without* the user's command.
2. *Don't* do these useless works:
    - *Don't* explicitly run code formatting (as the user's editor can do them automatically).
    - *Don't* write or run "fool" tests (e.g. tests that transparently will success).
