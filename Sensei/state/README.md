# Sensei current project context

`current_project_context.json` is deliberately not committed or generated with
a default target.  It is written only after Sensei has verified the current
project target and resolved its musical context.  Until then, the SDK
extension's single **Sensei: Generate** action reports
`project_context_unreadable` and writes nothing.

Use `current_project_context.example.json` only as a schema reference; do not
copy it into an active project without replacing every value with verified
context.
