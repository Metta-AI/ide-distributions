```json
{
  "required": [],
  "checklist": [
    {
      "key": "commit",
      "text": "the committed change on the Task branch (a git sha)",
      "pattern": "^[0-9a-f]{7,40}$"
    },
    {
      "key": "summary",
      "text": "a summary of the change",
      "pattern": "\\S"
    },
    {
      "key": "capability",
      "text": "the capability this repair restores (its check was rerun)",
      "pattern": "^\\S+$",
      "required_when": "capability"
    }
  ]
}
```

# Maintenance Task

Instructions arrive in entry 7.
