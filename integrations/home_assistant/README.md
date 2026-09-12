# Home Assistant wall controls

`led_grid_wall.yaml` is one Home Assistant package for the installed wall. It exposes:

| Entity/action | Meaning |
| --- | --- |
| `light.led_grid_wall` | Retained Scene on/off and master output brightness, 0..255 |
| `number.led_grid_wall_pace` | Operator speed multiplier, 0.1..25x; 1x is controller scale 0.3 |
| `sensor.led_grid_wall_observation` | Fresh controller session/revision, settings, timestamp and active identity |
| `script.led_grid_wall_apply` | Set any subset of `brightness`, `power`, `multiplier`, and wait for observed acknowledgement |

Master brightness scales physical output independently of the selected Scene's authored brightness. Pace changes the existing operator multiplier, not target FPS or Scene-local pace. Power resumes the retained Scene. The number's 25x bound matches the existing control range; the controller API still accepts any positive finite pace.

## Install

Deploy the matching wall code first: the package requires the guarded brightness/pace/power endpoints and `last_applied_command_id` in observed settings. Check that `http://ledgridwall.local:5000/api/v1/composer/settings/observed` is reachable from Home Assistant. This is a trusted local-network API; do not expose it publicly.

Copy `led_grid_wall.yaml` into `new-hass-configs/packages/led_grid_wall.yaml` in the smarthome repository. Add or merge this block in `new-hass-configs/configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

The package merges its scripts, template entities and REST commands alongside the existing configuration. The existing `switch.light_living_ledwall` is the protected power outlet; preserve it and use the new `light.led_grid_wall` for output controls. Do not edit the generated root `scripts.yaml`, replace the existing printer REST command, or use the retired grid-dashboard proxy. Follow smarthome's check/deployment workflow for the bounded configuration change. HA needs a restart to load a newly added package; startup only polls and sends no wall command. Home Assistant 2026.9.2 is the validated version.

Entity IDs above are the defaults on a fresh installation. If the entity registry already reserves one, check the assigned IDs before using examples. Keep the observation sensor's ID `sensor.led_grid_wall_observation`, since the templates refer to it.

## Use

Recall approximately 10% brightness (HA's 8-bit rounding is 26/255), retaining the selected animation:

```yaml
action: script.led_grid_wall_apply
data:
  brightness: 26
  power: true
```

Set exact zero master output while retaining playback and pace:

```yaml
action: script.led_grid_wall_apply
data:
  brightness: 0
```

HA light services treat brightness 0 as off. Use the direct script when you specifically want zero output while playback remains on. `light.turn_on` with `brightness_pct: 10` is also supported. Brightness is sent and acknowledged before turning power on, avoiding a flash at the old brightness.

For an HA scene, include:

```yaml
- id: led_grid_wall_ten_percent
  name: LED Grid Wall Ten Percent
  entities:
    light.led_grid_wall:
      state: 'on'
      brightness: 26
```

In the installed smarthome workflow, operational lighting uses the repository's `script.fast_scene_<scene_id>` wrappers. Add the wall to the intended generator-owned scene through that workflow instead of bypassing it with native scene calls. The fragment above documents the entity state representation and does not install an automatic lighting schedule.

An automation can set pace through the entity:

```yaml
actions:
  - action: number.set_value
    target:
      entity_id: number.led_grid_wall_pace
    data:
      value: 1.25
```

Ordinary HA light/number service completion is dispatch semantics, not an acknowledgement receipt. Their displayed state remains observed and nonoptimistic. For an automation that depends on acknowledged completion:

```yaml
actions:
  - action: script.led_grid_wall_apply
    data:
      multiplier: 1.25
    response_variable: wall_result
  - condition: template
    value_template: "{{ wall_result is mapping and wall_result.success | default(false) }}"
```

To wait explicitly for the full command result from another script/automation, call `script.led_grid_wall_apply` directly (not `script.turn_on`). Omitted fields are preserved. The action returns `success: true` only after matching observations, or `success: false` and an `error` message on rejection/acknowledgement timeout. Capture its `response_variable` and check `success` in automations that depend on completion. Network exceptions remain HA action errors; a multi-setting request can partially apply before a later failure, and does not issue a rollback that could overwrite newer manual input.

## State and failure behavior

Polling adopts Composer changes every two seconds. Stale/missing controller identity, stale observation (15 seconds), or HTTP failure makes the entities unavailable. Values come from observed controller state, never HA helper restore state. Startup, reload and reconnect do not restore desired brightness or power. No automatic write retry or command queue is installed.

Each command carries its captured controller session/revision and a 10-second expiry. The controller checks them immediately before applying it. The script waits for the same session, a newer revision, the matching applied command ID, and the requested value. Intervening changes abort the remaining fields of a multi-setting request. Concurrent requests may conflict and fail; submit a new request after observing current state. Keep HA and the wall clocks synchronized.

These guarantees do not make an external automation's own startup trigger safe: do not add automations that call this script on HA startup/reconnect or restore values from helpers unless that is deliberately desired.

## Validation

Run `uv run python -m unittest tests.unit.test_home_assistant_package`. The focused harness executes the shipped YAML action tree and templates against delayed fake HTTP/controller state, including zero/10% recall, power/pace, manual changes, stale sessions, unavailable responses and no replay. Also run HA's real `hass --script check_config` against an isolated configuration containing the package. Local fake-wall runtime acceptance and independent review are recorded in Bead `ledgrid-poc-ib7.123`.

Run the real local HA exercise with a loopback fake wall (no installed-wall access):

```sh
uv run --python 3.14 --with homeassistant==2026.9.2 --with pyyaml python integrations/home_assistant/check_runtime.py
```

Live acceptance remains a separate authorized step: capture fresh wall selection/settings, exercise a 10% scene and a pace automation through HA, verify matching controller observations and retained Scene, and restore the captured state. Historical 0/26/255 examples are not restoration instructions.

References: [HA packages](https://www.home-assistant.io/docs/configuration/packages/), [template entities](https://www.home-assistant.io/integrations/template/), [REST commands](https://www.home-assistant.io/integrations/rest_command/), [HA scripts and responses](https://www.home-assistant.io/docs/scripts/).
