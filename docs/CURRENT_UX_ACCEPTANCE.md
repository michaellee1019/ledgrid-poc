# Current Composer behavior

The application serves one 33×138 plant wall. Keep the catalog, browser previews,
Widgets, Looks, phone/desktop editing, playlists, manual takeover, brightness,
power, pace, calibration and Home Assistant controls.

All wall animations render on the host. Receiver-native-only entries and World
Flags are removed. Existing saved references remain stored and report an
unsupported component when opened/applied; they are never silently substituted.
Canopy Cup, Pixel Quest, Maze Chase, Pinball, Tetris and Snake remain available.

The UI separates requested scene, controller playback, receiver connectivity,
and errors. Controller playback means frames are being generated; it does not
promise exact all-five physical display. Newer commands supersede older user
intent, and manual selection takes over from a playlist.

Failed changes can interrupt output. Retry or recover manually; automatic
rollback and restoration are not product promises. Personal saved data and
current brightness survive application replacement. Calibration is editable
files for this installed wall, not a profile library or distributed transaction.

Composer is online-only. Preserve browser rendering while connected and show a
clear disconnected state. Retire service-worker caches from prior installations.

Validate the affected phone/desktop journey, retained previews, saved Looks,
playlists/takeover, stop, reconnect, and unsupported saved references. Installed
acceptance also checks mapping, calibration, smooth playback, current brightness
and prepared scene switches within five seconds. See
[current validation](CURRENT_COMPOSER_VALIDATION.md).
