# Bro

Turn a bloated agent reply, or any text, into the shortest factual version that keeps every fact: result first, what needs you, then bullets. No filler.

## Use

- `/bro`: rewrite the agent's previous reply.
- `/bro <text>`: rewrite the pasted text.
- `/bro <request>`: write something new in this style, for example `/bro PR description for this change`.

In Codex, type `$bro`. The skill runs only when called; agents do not load it on their own.

It lists every fact in the source (numbers, links, refs, decisions, asks, negations), rewrites, then checks the rewrite against that list. Code, commands, quotes and drafts meant to be sent stay verbatim.

## Private rules

Put house rules that should stay private (title formats, link formats, time zones, names) in `${XDG_CONFIG_HOME:-~/.config}/ostack/bro/NOTES.md`. The skill reads that file first when it exists.

## Credits

Ideas adapted, not copied, from:

- [blader/humanizer](https://github.com/blader/humanizer) (MIT): treat the text as material, and check the rewrite added and dropped no fact.
- [petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop) (MIT): keep the writer's voice.
- [hardikpandya/stop-slop](https://github.com/hardikpandya/stop-slop) (MIT): state the point instead of contrasting it.
- [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) (Apache-2.0): fragments over full sentences, code verbatim, never drop a negation, clarity beats compression.
- [ayghri/i-have-adhd](https://github.com/ayghri/i-have-adhd) (MIT): lead with what the reader acts on.
- [backnotprop/pstack](https://github.com/backnotprop/pstack) (MIT): its `bro` skill, a user-invoked plain restatement of the last message.
