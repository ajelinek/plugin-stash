# Changelog

## 0.1.2 -- 2026-11-01

- Bundle shared helpers and the startup hook as real files rather than
  checkout-relative symlinks. Git marketplace installs that copy only this
  plugin no longer lose `shadetree_ai_plugins_common` or `check_uv.py`.

## 0.1.1 -- 2026-10-21

- `imessage_send`: fix sends failing with "Can't get chat id ... (-1728)" when
  a 1:1 chat exists in `chat.db` but not among Messages.app's loaded chats
  (deleted in the app, or not synced to this Mac). The send now falls back to
  the chat's handle as a participant.
- The fallback follows the chat's own service (`RCS` -> RCS, then SMS;
  `SMS` -> SMS; `iMessage` -> iMessage, then SMS). Messages.app accepts an
  iMessage send to a non-iMessage number and only fails at delivery, so
  iMessage is never tried for an RCS/SMS chat.

## 0.1.0

Initial release.
