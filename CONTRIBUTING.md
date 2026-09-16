# Contributing to WaveFlow

Thanks for wanting to help. WaveFlow is GPL-3.0, and pull requests are welcome.

## The short version

By sending a pull request you agree that:

1. You wrote the change, or you have the right to send it.
2. Your change is licensed to this project under **GPL-3.0**, like the rest of the code.
3. You also grant the project owner (MrAbookah) the right to release your change under other
   licence terms in future versions of WaveFlow. You keep your own copyright on what you wrote.

Point 3 is what lets the project change licence later, or offer a paid licence to a company,
without having to track down every past contributor. It does not take your work away from you.

## Before you open a pull request

- Run the tests. They are fast and need no GPU:

  ```bash
  python server/test_live_settle.py   # LIVE_SETTLE_OK
  python server/test_auth.py          # AUTH_OK
  python server/vocab.py              # VOCAB_OK
  python app/test_wizard.py           # WIZARD_OK
  python app/test_settings.py         # SETTINGS_OK
  python app/test_uninstall.py        # UNINSTALL_OK
  ```

- If you fix a bug, add the case that failed to the matching test file. Most tests in this repo
  were written that way: the real broken output first, then the fix.
- Keep changes small and say what you measured. "Feels faster" is not a result; "0.31 s -> 0.19 s
  on a 10 s clip, i7-7700K, 4 threads" is.
- Anything the user looks at: say what it should look like and attach a screenshot.

## Things that always need saying no to

- Sending audio, text or telemetry anywhere the user did not configure.
- A default that opens the server to the network without a token.
- Uninstall touching anything WaveFlow did not install.

## Untested setups

"This PC — Docker" and the VPS path are written but nobody has run them end to end. If you try
one, an issue saying what happened is as useful as a fix.
