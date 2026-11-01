# Demo recording for the README. Do not run this directly; use:  make demo
#
# make demo builds the demo repository (scripts/demo/prepare.sh) and then records this tape
# with VHS (https://github.com/charmbracelet/vhs, version 0.7 or newer for `Wait`).
# It talks to a real model, so a provider key must be set in your environment first.

Output docs/assets/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 780
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Set WindowBar Colorful

# Setup, not shown: enter the demo repository and put its git-ai-commit shim on PATH.
Hide
Type "cd /tmp/gitcommit-ai-demo/repo && export PATH=/tmp/gitcommit-ai-demo/bin:$PATH PS1='$ ' && clear"
Enter
Show

# A change is already staged.
Type "git status --short"
Enter
Sleep 1500ms
Type "git diff --cached --stat"
Enter
Sleep 2500ms

# Generate a message. --verbose prints the detected convention, the examples taken from
# history, and every critic round before the final message.
Type "git ai-commit --verbose"
Enter
Wait+Screen@120s /Commit with this message\?/
Sleep 6s

# Accept it, then show what was committed.
Type "y"
Enter
Sleep 2s
Type "git log -1 --format=%B"
Enter
Sleep 5s
