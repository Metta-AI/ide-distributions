---
name: working-with-the-ide
description: Use when choosing between chat, the ui tool and a notice.
---

# Working with the IDE and other clients

Three channels reach the user. Which one you use depends on whether someone is reading.

## In a chat Session

The user sent a message; answer in the conversation. The turn context (`[client context]`)
says which client it is, the mode and the command, what is on screen, the references and the
attachments. The session context says which panels the client can show and names a client
guide (`client-coplay`, `client-web-ide`): load it when the context names it. Mode and command
outrank your judgement on how to handle a request; only the user's explicit request outranks
them.

Each turn opens with the unread notices, the "changed since your last turn" note and the active
Coworld. Mention what matters to the user; do not recite the list.

The away summary: when the user comes back after an hour or more, the turn context carries
`[while you were away …]` with counts from the events log. Open with two or three sentences,
what finished, what was submitted, what needs a decision, with record ids, then answer the
message.

## In a wake

Nobody is reading. A wake writes under the shared `workspace` conversation that every client
sees, and `ui.notify` is the only channel for news. Never write a wake's result as chat text: it
would wait in a conversation nobody has open. Finish a wake with one notice, or none when
nothing changed.

## The `ui` tool

It writes UI state and never waits on a client.

- `ui.show {ref}` or `{view}`: bring a record (G-3, X-5, V-7) or a client view into view when
  the user would otherwise have to find it.
- `ui.panel_set {panel, kind, content}`: a named panel with a declared kind (`markdown`,
  `record`, `table`, `checklist`, `file`, `link`, `replay`, `html`). Never assume Markdown. A
  `file` panel names a file under the Coworld's `documents/` folder; a `replay` panel a replay
  or a moment in it; `html` only when the session context says the client can show it.
- `ui.notify {text, refs, urgency}`: plain language, informs and never asks, one or two
  sentences with ids. Every background result goes here: a reviewed Task, a submission, a
  repair, a switch. Urgency `high` only for something the user should act on today (a repair
  that failed twice, a submission a setting stopped).
- `ui.ask {question, options, refs}`: only for what the user alone can answer (a preference, a
  credential, a choice between Goals, a submission in mode `ask`). The first answer from any
  client wins and arrives as a UI action, into the running run or a new one. Decide everything
  else yourself; a question you could answer with a tool is not a `ui.ask`.

## The agent-context panel and its needs section

The watcher computes the `agent-context` panel: Coworld, Player, Goals, champion, Versions,
running Tasks, capabilities, what the client sent. You write only its "I need from you" items:
`ui.panel_set {panel: "agent-context", kind: "agent-context", content: {needs: [...]}}`, each
item one sentence saying what and why, with a record id when there is one. List only what the
user alone can supply (a User credential, a decision on a parked Goal, a repository grant).
Clear an item as soon as it arrives; a stale need is worse than none.

## Answering during setup

A fresh workspace, or a switch to a Coworld never set up, starts the setup wake. A user who asks
for something meanwhile gets an answer at once: say that setup of the Coworld is running and
that their request runs when it is ready, then pass it along with
`workspace.coworld_switch {coworld, cites, pending_request: {text, cites}}`. The setup wake ends
by carrying the request out (for a submission: a Version on `main`, upload, submit citing their
message) and notifying. Never make the user wait for setup, and never refuse because it is
running. Questions that need only platform reads (`platform.standings`, `platform.leagues`) are
answered now.

## Skill overrides

The code repository may override a core skill under `.agents/skills/<skill>/`. The briefing and
`workspace.doctor` flag an override whose upstream changed. Suggest the update to the user with
what changed; the user decides. Mixin skill changes go live on merge.
