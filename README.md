# Foundry

One job: pull every public repo on a GitHub account into `~/GitHub`, build each app
with its own build script, and rebuild only what has changed since last time.

It replaces `Update Repos.command` — same clone/pull of everything, plus the build step.

## Run it

- **Windows:** double-click `run-windows.bat`
- **Mac:** double-click `run-mac.command` (first time: `chmod +x run-mac.command` and
  `xattr -d com.apple.quarantine run-mac.command` in Terminal)

Both find a Python 3.8+ and install one if there is none (winget on Windows, the Apple
Command Line Tools on Mac). Nothing else is needed — git is used if present, otherwise
Foundry downloads zipballs and tracks commits itself.

## What one run does

1. Lists the account's public, non-fork repos from the GitHub API.
2. For each repo under `~/GitHub/<name>`: `git pull --rebase --autostash` if it is a clone,
   `git clone` if it is missing, or a zipball refresh if git is not installed.
3. If the repo has `build-exe.bat` (Windows) or `build-app.command` (Mac) **and** the commit
   changed, or nothing is in `dist/`, or the last build failed — runs that script, with output
   to `~/GitHub/.foundry/logs/<name>.log`.
4. Puts a launcher for every built app in `~/GitHub/_Apps` (symlink on Mac, `.lnk` on Windows).
5. Prints a summary. Exit code = number of failures.

Repos with no build script (the site, plain scripts) are still synced, just not built.
If Foundry itself lives under `~/GitHub` it updates itself first and restarts.

## Options

```
python foundry.py --list           show repos and what would happen, change nothing
python foundry.py --force          rebuild everything
python foundry.py --only findex    one or more repos (repeatable)
python foundry.py --skip hayloft   leave a repo out this run (repeatable)
python foundry.py --no-build       sync only
python foundry.py --verbose        stream build output to the terminal too
```

Pass options to the launchers the same way: `run-windows.bat --force`.

`foundry.json` holds the standing settings: `owner`, `root`, `skip` (never cloned),
`no_build` (cloned but never built), `shortcuts`, `include_forks`, `include_archived`.
Set `GITHUB_TOKEN` in the environment if you ever hit the API's 60-requests-an-hour limit
(a normal run uses one or two).

## For build scripts

Foundry runs build scripts with stdin closed and `FOUNDRY=1` / `CI=1` in the environment,
so a `pause` returns immediately. A script can check `%FOUNDRY%` / `$FOUNDRY` to skip opening
the finished app. A build counts as successful only if the script exits 0 **and** something
appears in `dist/` (`*.app` on Mac, `*.exe` at the top of `dist/` or one folder down on
Windows).

## State

`~/GitHub/.foundry/state.json` — per repo: commit synced, commit built, result, artefacts.
Delete it to forget everything and rebuild on the next run.
