```json
{
  "required": [],
  "checklist": [
    {
      "key": "claims",
      "text": "claims recorded (C- ids)",
      "pattern": "^C-[1-9][0-9]*$",
      "min_items": 1,
      "none_ok": false
    },
    {
      "key": "experiment",
      "text": "the Experiment run (X- id)",
      "pattern": "^X-[1-9][0-9]*$"
    },
    {
      "key": "versions",
      "text": "the Versions tested (V- ids)",
      "pattern": "^V-[1-9][0-9]*$",
      "min_items": 1
    },
    {
      "key": "submission_candidate",
      "text": "whether a tested Version is a submission candidate (yes or no)",
      "pattern": "^(yes|no)$"
    },
    {
      "key": "submission_reason",
      "text": "why it is or is not a submission candidate",
      "pattern": "\\S"
    }
  ]
}
```

# Improvement Task

Instructions arrive in entry 7.
