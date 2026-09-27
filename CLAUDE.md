<coding_conventions>

- HABIT 1: [Security First] Never hardcode sensitive credentials or API keys; always use environment variables. Actively avoid reading, echoing, or printing raw secret values into the terminal or chat context to prevent them from leaking into the LLM context window. If necessary, write small execution scripts around .env files to keep secrets isolated.

- HABIT 2: [Agentic Initiative] When fixing errors, analyze the terminal output directly using CLI commands rather than asking the user to copy-paste logs.

- HABIT 3: [Plan & Reason First] Before executing complex tasks, fixes, or architectural changes, draft a step-by-step execution plan and lay out your logical reasoning. Think through edge cases explicitly, present the plan to the user, and wait for approval before writing code or executing commands.

- HABIT 4: [Library Integrity] When working with external libraries, never guess the syntax. You must first consult the official documentation or the local source code (via the venv or internet tools) to ensure the library, with the version we use in the code-base, is applied correctly semantically and syntactically.

- HABIT 5: [Documentation & Context Maintenance] Keep all docs, templates (.env.example), and AI context files (claude.md, memories) perfectly synced with code changes. Example configs must reflect actual required keys without containing real secrets. Resolve any contradictions between the code, docs, and AI context immediately, or discuss them with the user if necessary.

- HABIT 6: [Meta-Optimization] Continuously evaluate our interactions. If you notice repetitive manual tasks, recurring boilerplate generation, or identical shell commands being executed across multiple turns, STOP and proactively suggest an automation. Advise the user if creating a new Skill, Hook, or Plugin from the Marketplace, or using a dedicated CLI or MCP, or using a Subagent would optimize the workflow, strictly following the "Lightest First" philosophy.

- HABIT 7: [Standardized Quality] Always write code that adheres to established, language-specific style guides. Anticipate and prevent the warnings, errors, and hints that standard static analysis tools and linters (like Pylint or ESLint) would flag to ensure maximum maintainability.

- HABIT 8: [Secure by Design] Actively integrate fundamental cybersecurity principles into the application. Validate inputs, sanitize outputs, and defend against the OWASP Top 10 vulnerabilities (e.g., Injection, XSS) while applying the principle of least privilege.

- HABIT 9: [Verifiable Reliability] Maintain a dedicated tests/ directory at the project root. Write automated tests for significant new functionality and run the test suite to confirm everything works and no regressions are introduced before finalizing features.

- HABIT 10: [Code Discovery & Pattern-Driven Design] Never blindly generate new code. Actively scan the existing codebase first to discover and reuse existing components, utilities, or routing structures (DRY principle). When architectural decisions are required, always apply established, industry-standard design patterns rather than inventing proprietary or redundant logic structures.

</coding_conventions>